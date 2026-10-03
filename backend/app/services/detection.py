"""Runs detection rules over stored telemetry, correlates deployments, and opens incidents."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.detection.engine import DetectionEngine, Finding, Thresholds, compute_baseline
from app.models import Application, Deployment, Incident, MetricSample, as_utc
from app.services import flags as flag_service
from app.services import notifications
from app.services.common import add_event
from app.services.telemetry import sample_to_signals

ACTIVE_STATUSES = ("open", "investigating", "awaiting_approval", "mitigating", "verifying")
ROLLOUT_EXPECTED = {"replica_availability", "health_check", "crash_loop", "restarts"}


@dataclass
class DetectionResult:
    findings: list[Finding]
    app_status: str
    incident: Incident | None = None
    created: bool = False


class DetectionService:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()

    def engine_for(self, app: Application) -> DetectionEngine:
        return DetectionEngine(Thresholds.from_overrides(app.thresholds))

    def active_incident(self, application_id: str) -> Incident | None:
        return self.session.scalars(
            select(Incident).where(Incident.application_id == application_id, Incident.status.in_(ACTIVE_STATUSES)).order_by(Incident.id.desc()).limit(1)
        ).first()

    def recent_samples(self, application_id: str, limit: int) -> list[MetricSample]:
        rows = self.session.scalars(
            select(MetricSample).where(MetricSample.application_id == application_id).order_by(MetricSample.observed_at.desc()).limit(limit)
        ).all()
        return list(reversed(rows))

    def baseline(self, application_id: str, reference: datetime, minutes: int = 30) -> dict:
        start = reference - timedelta(minutes=minutes)
        samples = self.session.scalars(
            select(MetricSample).where(
                MetricSample.application_id == application_id,
                MetricSample.observed_at >= start,
                MetricSample.observed_at < reference,
            ).order_by(MetricSample.observed_at)
        ).all()
        return compute_baseline([sample_to_signals(sample) for sample in samples])

    def correlated_deployment(self, application_id: str, first_breach: datetime) -> Deployment | None:
        """Most recent release that started before the first breaching sample.

        CI often reports "finished" a few seconds after the new version is already serving traffic, so the match uses the
        deployment start time (with a small tolerance) instead of the finish time.
        """
        window_start = first_breach - timedelta(minutes=self.settings.correlation_window_minutes)
        tolerance = timedelta(seconds=60)
        candidates = self.session.scalars(
            select(Deployment).where(
                Deployment.application_id == application_id,
                Deployment.ci_status.in_(("success", "in_progress")),
                Deployment.started_at <= first_breach + tolerance,
                Deployment.started_at >= window_start,
            ).order_by(Deployment.started_at.desc()).limit(1)
        ).all()
        return candidates[0] if candidates else None

    def in_rollout(self, application_id: str, at: datetime) -> bool:
        """True shortly after a deployment started/finished, while replicas are expected to restart."""
        grace = timedelta(seconds=self.settings.rollout_grace_seconds)
        recent = self.session.scalars(
            select(Deployment).where(Deployment.application_id == application_id, Deployment.started_at >= at - timedelta(hours=2))
            .order_by(Deployment.started_at.desc()).limit(1)
        ).first()
        if recent is None:
            return False
        last_change = as_utc(recent.finished_at) or as_utc(recent.started_at)
        return recent.status == "in_progress" or (last_change is not None and at - last_change <= grace)

    @staticmethod
    def actionable(findings: list[Finding], rollout: bool) -> list[Finding]:
        """During a rollout, replica churn alone is expected; user-facing signals still count."""
        if not rollout:
            return findings
        return [finding for finding in findings if finding.code not in ROLLOUT_EXPECTED]

    def process(self, app: Application, sample: MetricSample) -> DetectionResult:
        engine = self.engine_for(app)
        signals = sample_to_signals(sample)
        has_data = any(signals.get(key) is not None for key in ("request_rate", "error_rate_percent", "healthy_replicas", "failed_health_checks", "cpu_percent"))
        latest_deployment = self.session.scalars(
            select(Deployment).where(Deployment.application_id == app.id, Deployment.finished_at.is_not(None), Deployment.ci_status == "success")
            .order_by(Deployment.finished_at.desc()).limit(1)
        ).first()
        observed_at = as_utc(sample.observed_at)
        recent_release = latest_deployment is not None and as_utc(latest_deployment.finished_at) >= observed_at - timedelta(minutes=self.settings.correlation_window_minutes)
        reference = as_utc(latest_deployment.started_at) if recent_release else observed_at - timedelta(minutes=5)
        baseline = self.baseline(app.id, reference)
        findings = engine.evaluate(signals, baseline)
        app_status = engine.application_status(findings, has_data)
        app.status = app_status
        app.status_reason = "; ".join(finding.title for finding in findings) or ("No telemetry sources returned data" if not has_data else None)
        app.last_checked_at = sample.observed_at
        result = DetectionResult(findings=findings, app_status=app_status)

        active = self.active_incident(app.id)
        if active is not None:
            self._update_active(active, findings, signals)
            result.incident = active
            return result
        rollout = self.in_rollout(app.id, observed_at)
        if not findings or not engine.is_incident_worthy(self.actionable(findings, rollout)):
            return result
        canary = (sample.runtime_state or {}).get("rollout") or {}
        if rollout and canary.get("updating") and not canary.get("aborted"):
            # An Argo Rollouts canary is live and judged by NexOps canary analysis, which makes Argo abort a bad one;
            # opening an incident as well would race that abort. Breaches after promotion are handled as usual.
            app.status_reason = f"{app.status_reason} (canary {canary.get('canary_version')} at {canary.get('canary_weight')}% under analysis)"
            return result

        # Require consecutive breaching samples to avoid paging on a single noisy scrape.
        needed = max(1, self.settings.detection_consecutive_breaches)
        window = self.recent_samples(app.id, needed)
        if len(window) < needed:
            return result
        breaches = [self.actionable(engine.evaluate(sample_to_signals(item), baseline), self.in_rollout(app.id, as_utc(item.observed_at))) for item in window]
        if not all(engine.is_incident_worthy(items) for items in breaches):
            return result

        first_breach = as_utc(window[0].observed_at)
        result.incident = self._open_incident(app, sample, findings, signals, first_breach, engine)
        result.created = True
        return result

    def _open_incident(self, app: Application, sample: MetricSample, findings: list[Finding], signals: dict, first_breach: datetime, engine: DetectionEngine) -> Incident:
        deployment = self.correlated_deployment(app.id, first_breach)
        reference = as_utc(deployment.started_at) if deployment else first_breach
        baseline = self.baseline(app.id, reference)
        # Re-evaluate against the pre-release baseline so finding details quote the right comparison.
        findings = engine.evaluate(signals, baseline) or findings
        severity = engine.incident_severity(findings)
        lead = max(findings, key=lambda finding: ("info", "warning", "high", "critical").index(finding.severity))
        if deployment:
            title = f"{app.name} degraded after {deployment.version}"
            summary = (
                f"{app.name} shows {', '.join(finding.title.lower() for finding in findings)} "
                f"{_minutes_between(as_utc(deployment.finished_at), first_breach)} after deployment {deployment.version} "
                f"(previous {deployment.previous_version or 'unknown'}). CI/CD passed, production telemetry did not."
            )
        else:
            title = f"{app.name}: {lead.title.lower()}"
            summary = f"{app.name} shows {', '.join(finding.title.lower() for finding in findings)}. No deployment in the last {self.settings.correlation_window_minutes} minutes correlates with the change."
        # Feature flag changes are production changes too.
        release_time = as_utc(deployment.finished_at or deployment.started_at) if deployment else None
        flag_change = next((change for change in flag_service.recent_changes(self.session, app.id, first_breach - timedelta(minutes=self.settings.correlation_window_minutes), first_breach + timedelta(seconds=60))
                            if change.enabled and (release_time is None or as_utc(change.changed_at) >= release_time)), None)
        if flag_change is not None:
            # The flag is the newest production change: the release keeps its status and DORA does not count it.
            title = f"{app.name} degraded after enabling {flag_change.flag.key}"
            summary = (
                f"{app.name} shows {', '.join(finding.title.lower() for finding in findings)} "
                f"{_minutes_between(as_utc(flag_change.changed_at), first_breach)} after feature flag {flag_change.flag.key} was enabled "
                f"({flag_change.rollout_percent}%) by {flag_change.actor}."
                + (f" The running release {deployment.version} was deployed earlier and is unchanged." if deployment else "")
            )
        observed = {key: value for key, value in signals.items() if value is not None}
        incident = Incident(
            application_id=app.id,
            deployment_id=deployment.id if deployment else None,
            title=title,
            severity=severity,
            status="open",
            summary=summary,
            signals=[finding.as_dict() for finding in findings],
            baseline=baseline,
            observed=observed,
            peak=dict(observed),
            deployment_correlated=deployment is not None and flag_change is None,
            detected_at=as_utc(sample.observed_at),
        )
        self.session.add(incident)
        self.session.flush()

        if deployment:
            add_event(self.session, incident, f"Deployment {deployment.version} started", f"Commit {(deployment.commit_sha or 'unknown')[:8]} by {deployment.author or 'unknown'}", kind="deployment", at=as_utc(deployment.started_at))
            add_event(self.session, incident, f"Deployment {deployment.version} completed", f"CI/CD status {deployment.ci_status}; previous version {deployment.previous_version or 'unknown'}", kind="deployment", severity="healthy", at=as_utc(deployment.finished_at))
            if flag_change is None:
                deployment.status = "degraded"
                deployment.status_reason = f"Production degraded after release; see INC-{incident.id}."
        if flag_change is not None:
            add_event(self.session, incident, f"Feature flag {flag_change.flag.key} enabled", f"{flag_change.rollout_percent}% rollout by {flag_change.actor}. {flag_change.reason or ''}".strip(), kind="flag", at=as_utc(flag_change.changed_at))
        for finding in findings:
            add_event(self.session, incident, finding.title, finding.detail, kind="signal", severity="critical" if finding.severity == "critical" else "warning", at=first_breach)
        add_event(
            self.session, incident, f"Incident INC-{incident.id} created",
            f"Severity {severity}. {'Correlated with ' + deployment.version if deployment else 'No correlated deployment'}.",
            kind="detected", severity="critical", actor="detection-engine", at=as_utc(sample.observed_at),
        )
        self.session.flush()
        notifications.queue(self.session, "incident.opened", f"INC-{incident.id} · {severity.upper()} · {title}", summary, f"/incidents/INC-{incident.id}")
        return incident

    def _update_active(self, incident: Incident, findings: list[Finding], signals: dict) -> None:
        known = {signal["code"] for signal in incident.signals}
        new = [finding for finding in findings if finding.code not in known]
        if new and incident.status != "verifying":
            incident.signals = [*incident.signals, *[finding.as_dict() for finding in new]]
            for finding in new:
                add_event(self.session, incident, finding.title, finding.detail, kind="signal", severity="warning")
        observed = {key: value for key, value in signals.items() if value is not None}
        incident.observed = observed
        peak = dict(incident.peak or {})
        for key, value in observed.items():
            if not isinstance(value, (int, float)):
                continue
            current = peak.get(key)
            worse_is_lower = key in {"healthy_replicas", "business_success_rate"}
            if current is None or (value < current if worse_is_lower else value > current):
                peak[key] = value
        incident.peak = peak


def _minutes_between(start: datetime | None, end: datetime) -> str:
    if start is None:
        return "shortly"
    minutes = max(0, int((end - start).total_seconds() // 60))
    return "within a minute" if minutes < 1 else f"{minutes} min"
