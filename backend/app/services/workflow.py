"""Incident workflow: investigate → propose (policy) → approve → remediate → verify recovery → resolve → learn."""
from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core.ids import incident_key
from app.detection.engine import Thresholds
from app.integrations.registry import Integrations, get_integrations
from app.integrations.types import IntegrationError
from app.models import (
    Deployment, Incident, Investigation, MetricSample, RecoveryVerification, RemediationAction, User, as_utc, utc_now,
)
from app.services import flags as flag_service
from app.services import notifications
from app.services.common import add_event, agent_run, audit
from app.services.detection import ACTIVE_STATUSES
from app.services.investigation import InvestigationService
from app.services.memory import remember
from app.services.policy import PolicyEngine
from app.services.postmortem import PostmortemService
from app.services.remediation import ExecutionResult, RemediationExecutor, capability_gap, rollback_target
from app.services.telemetry import TelemetryService, sample_to_signals

logger = logging.getLogger(__name__)
ACTION_LABELS = {"rollback": "Rollback", "restart": "Restart", "scale_up": "Scale up", "pause_deployments": "Pause deployments", "disable_feature": "Disable feature"}


def action_label(action: str, target_version: str | None = None, target_flag: str | None = None) -> str:
    label = ACTION_LABELS.get(action, action)
    if target_flag:
        return f"{label} {target_flag}"
    return f"{label} to {target_version}" if target_version else label


class WorkflowError(RuntimeError):
    """A requested transition is not valid for the incident's current state."""


def recovery_checks(thresholds: Thresholds, signals: dict, baseline: dict, target_version: str | None) -> list[dict]:
    checks: list[dict] = []

    def add(name: str, passed: bool, observed: str, expected: str) -> None:
        checks.append({"name": name, "passed": bool(passed), "observed": observed, "expected": expected})

    error = signals.get("error_rate_percent")
    if error is not None:
        base = baseline.get("error_rate_percent")
        limit = thresholds.error_rate_percent if base is None else min(thresholds.error_rate_percent, max(base * 2.0, base + 1.0))
        add("Error rate", error <= limit, f"{error:.2f}%", f"≤ {limit:.2f}%")
    p99 = signals.get("p99_latency_ms")
    if p99 is not None:
        base = baseline.get("p99_latency_ms")
        limit = thresholds.p99_latency_ms if base is None else min(thresholds.p99_latency_ms, max(base * 1.5, base + 200))
        add("P99 latency", p99 <= limit, f"{p99:.0f} ms", f"≤ {limit:.0f} ms")
    healthy, total = signals.get("healthy_replicas"), signals.get("total_replicas")
    if total:
        add("Replica availability", healthy == total, f"{healthy}/{total}", "all replicas ready")
    if signals.get("crashlooping") is not None:
        add("Crash loops", not signals.get("crashlooping"), str(signals.get("crashlooping")), "0")
    if signals.get("failed_health_checks") is not None:
        add("Health endpoints", signals["failed_health_checks"] == 0, f"{signals['failed_health_checks']} failing", "all passing")
    if signals.get("db_errors") is not None:
        add("Database errors", signals["db_errors"] < thresholds.db_error_count, str(signals["db_errors"]), f"< {thresholds.db_error_count}")
    if signals.get("business_success_rate") is not None:
        add("Business transactions", signals["business_success_rate"] >= thresholds.business_success_rate_min, f"{signals['business_success_rate']:.1f}%", f"≥ {thresholds.business_success_rate_min:g}%")
    if target_version and signals.get("version"):
        add("Running version", signals["version"] == target_version, signals["version"], target_version)
    if not checks:
        add("Telemetry available", False, "no data", "at least one signal")
    return checks


class IncidentWorkflow:
    def __init__(
        self,
        session: Session,
        integrations: Integrations | None = None,
        settings: Settings | None = None,
        executor: RemediationExecutor | None = None,
        telemetry: TelemetryService | None = None,
    ) -> None:
        self.session = session
        self.integrations = integrations or get_integrations()
        self.settings = settings or get_settings()
        self.executor = executor or RemediationExecutor(self.integrations)
        self.telemetry = telemetry or TelemetryService(self.integrations, self.settings)

    # ------------------------------------------------------------------ helpers
    def _incident(self, incident_id: int, lock: bool = True) -> Incident:
        query = select(Incident).where(Incident.id == incident_id)
        if lock:
            query = query.with_for_update()
        incident = self.session.scalars(query).first()
        if incident is None:
            raise LookupError(f"Incident {incident_key(incident_id)} was not found.")
        return incident

    def _remediation(self, remediation_id: int) -> RemediationAction:
        remediation = self.session.scalars(select(RemediationAction).where(RemediationAction.id == remediation_id).with_for_update()).first()
        if remediation is None:
            raise LookupError(f"Remediation {remediation_id} was not found.")
        return remediation

    # ------------------------------------------------------------------ investigation + proposal
    def investigate(self, incident_id: int, actor: str) -> Incident:
        # No row lock: evidence collection and the LLM call can take a while and the monitor keeps updating telemetry.
        incident = self._incident(incident_id, lock=False)
        if incident.status == "resolved":
            raise WorkflowError("Resolved incidents cannot be re-investigated.")
        investigation = InvestigationService(self.session, self.telemetry, self.integrations, self.settings).run(incident, actor)
        self.propose_from_investigation(incident, investigation)
        return incident

    def propose_from_investigation(self, incident: Incident, investigation: Investigation) -> RemediationAction | None:
        action = investigation.recommended_action or "investigate_further"
        if action == "investigate_further":
            add_event(self.session, incident, "No automated remediation recommended", investigation.recommendation_detail or "", kind="remediation", actor="remediation-agent")
            return None
        return self.propose(incident, action, reason=investigation.recommendation_detail, confidence=investigation.confidence, actor="remediation-agent", target_flag=investigation.recommended_flag)

    def propose(self, incident: Incident, action: str, *, reason: str | None, confidence: float | None, actor: str, target_flag: str | None = None) -> RemediationAction:
        if incident.status == "resolved":
            raise WorkflowError("The incident is already resolved.")
        app = incident.application
        with agent_run(self.session, "remediation", incident_id=incident.id, provider="policy-engine") as run:
            for previous in self.session.scalars(select(RemediationAction).where(
                RemediationAction.incident_id == incident.id, RemediationAction.status.in_(("proposed", "approved")),
            )):
                previous.status = "superseded"
            target_version = target_image = None
            if action == "rollback":
                target_version, target_image = rollback_target(self.session, app, incident.deployment)
            decision = PolicyEngine(self.session).evaluate(action, app, incident, confidence, has_previous_version=bool(target_version) or action != "rollback")
            gap = capability_gap(app, action)
            if action == "disable_feature":
                flag = flag_service.find(self.session, app.id, target_flag or "")
                gap = None if flag and flag.enabled else f"Feature flag '{target_flag}' is not an enabled flag of {app.name}."
            remediation = RemediationAction(
                incident_id=incident.id, action=action, target_version=target_version, target_image=target_image, target_flag=target_flag if action == "disable_feature" else None,
                mode=decision.mode, policy_id=decision.policy.id if decision.policy else None, policy_reasons=decision.reasons,
                reason=reason, proposed_by=actor,
                outcome={"max_replicas": (decision.policy.conditions or {}).get("max_replicas")} if decision.policy else {},
            )
            label = action_label(action, target_version, remediation.target_flag)
            if not decision.allowed or gap:
                remediation.status = "blocked"
                remediation.error = gap or "; ".join(decision.reasons)
                add_event(self.session, incident, f"{label} blocked", remediation.error, kind="policy", severity="warning", actor="policy-engine")
                if incident.status == "open":
                    incident.status = "investigating"
            elif decision.mode == "approval_required":
                remediation.status = "proposed"
                incident.status = "awaiting_approval"
                add_event(self.session, incident, f"Approval required: {label}", "; ".join(decision.reasons), kind="policy", severity="warning", actor="policy-engine")
                notifications.queue(
                    self.session, "approval.required", f"Approval required: {label} ({app.name})",
                    f"{incident_key(incident.id)}: {incident.title}. Likely cause: {incident.root_cause or 'under investigation'}",
                    f"/incidents/{incident_key(incident.id)}",
                )
            else:
                remediation.status = "approved"
                remediation.approved_by_name = f"policy:{decision.policy.id}" if decision.policy else "policy"
                remediation.approved_at = utc_now()
                incident.status = "mitigating"
                add_event(self.session, incident, f"Policy allows automatic {label.lower()}", "; ".join(decision.reasons), kind="policy", actor="policy-engine")
            self.session.add(remediation)
            self.session.flush()
            run.complete(f"{label}: {remediation.status} ({decision.mode})", {"remediation_id": remediation.id, "mode": decision.mode, "reasons": decision.reasons})
        return remediation

    # ------------------------------------------------------------------ approval
    def approve(self, remediation_id: int, user: User) -> RemediationAction:
        remediation = self._remediation(remediation_id)
        incident = self._incident(remediation.incident_id)
        if remediation.status != "proposed":
            raise WorkflowError(f"Only proposed actions can be approved (current status: {remediation.status}).")
        decision = PolicyEngine(self.session).evaluate(remediation.action, incident.application, incident, incident.confidence, has_previous_version=bool(remediation.target_version) or remediation.action != "rollback")
        if not decision.allowed:
            remediation.status = "blocked"
            remediation.error = "; ".join(decision.reasons)
            raise WorkflowError(f"Policy no longer allows this action: {remediation.error}")
        remediation.status = "approved"
        remediation.approved_by_id = user.id
        remediation.approved_by_name = user.display_name
        remediation.approved_at = utc_now()
        incident.status = "mitigating"
        label = action_label(remediation.action, remediation.target_version, remediation.target_flag)
        add_event(self.session, incident, f"{label} approved", f"Approved by {user.display_name} ({user.email}).", kind="approval", actor=user.display_name)
        audit(self.session, user.email, "remediation.approve", "incident", incident_key(incident.id), {"remediation_id": remediation.id, "action": remediation.action})
        return remediation

    def reject(self, remediation_id: int, user: User, reason: str) -> RemediationAction:
        remediation = self._remediation(remediation_id)
        incident = self._incident(remediation.incident_id)
        if remediation.status not in {"proposed", "approved"}:
            raise WorkflowError(f"Only pending actions can be rejected (current status: {remediation.status}).")
        remediation.status = "rejected"
        remediation.rejected_reason = reason
        incident.status = "investigating"
        add_event(self.session, incident, f"{action_label(remediation.action, None, remediation.target_flag)} rejected", f"{user.display_name}: {reason}", kind="approval", severity="warning", actor=user.display_name)
        audit(self.session, user.email, "remediation.reject", "incident", incident_key(incident.id), {"remediation_id": remediation.id, "reason": reason})
        return remediation

    # ------------------------------------------------------------------ execution
    def execute(self, remediation_id: int, actor: str) -> RemediationAction:
        remediation = self._remediation(remediation_id)
        incident = self._incident(remediation.incident_id)
        app = incident.application
        if remediation.status != "approved":
            raise WorkflowError(f"Only approved actions can be executed (current status: {remediation.status}).")
        if not self.settings.remediation_enabled:
            remediation.status = "failed"
            remediation.error = "Remediation is disabled (NEXOPS_REMEDIATION_ENABLED=false)."
            add_event(self.session, incident, "Remediation not executed", remediation.error, kind="remediation", severity="warning", actor=actor)
            return remediation
        remediation.status = "executing"
        remediation.started_at = utc_now()
        label = action_label(remediation.action, remediation.target_version, remediation.target_flag)
        executor_name = "feature flags" if remediation.action == "disable_feature" else app.rollback_strategy if remediation.action == "rollback" else app.runtime
        add_event(self.session, incident, f"{label} started", f"Executor: {executor_name}.", kind="remediation", actor=actor)
        self.session.commit()  # make "executing" visible while the executor runs

        remediation = self._remediation(remediation_id)
        incident = self._incident(remediation.incident_id)
        app = incident.application
        try:
            result = self._disable_flag(app, remediation, actor) if remediation.action == "disable_feature" else self.executor.execute(app, remediation)
        except (IntegrationError, Exception) as error:  # executors wrap SDK errors, but never leave the action hanging
            logger.exception("Remediation %s failed", remediation.id)
            remediation.status = "failed"
            remediation.error = f"{error}"[:2000]
            remediation.completed_at = utc_now()
            incident.status = "investigating"
            add_event(self.session, incident, f"{label} failed", remediation.error, kind="remediation", severity="critical", actor=actor)
            notifications.queue(self.session, "remediation.failed", f"{label} failed ({app.name})", remediation.error, f"/incidents/{incident_key(incident.id)}")
            return remediation

        remediation.executor = result.executor
        remediation.outcome = {**(remediation.outcome or {}), **result.detail}
        if result.status == "dispatched":
            remediation.status = "dispatched"
            add_event(self.session, incident, f"{label} dispatched", f"GitHub Actions workflow {app.github_workflow} triggered on {app.github_ref or 'main'}.", kind="remediation", actor=actor)
        else:
            remediation.status = "succeeded"
            remediation.completed_at = utc_now()
            add_event(self.session, incident, f"{label} executed", f"Completed via {result.executor}.", kind="remediation", severity="healthy", actor=actor)
            if remediation.action == "rollback":
                self._record_rollback_deployment(incident, remediation)
        self.start_verification(incident, remediation, actor)
        return remediation

    def _disable_flag(self, app, remediation: RemediationAction, actor: str) -> ExecutionResult:
        flag = flag_service.find(self.session, app.id, remediation.target_flag or "")
        if flag is None:
            raise IntegrationError(f"Feature flag '{remediation.target_flag}' no longer exists.")
        flag_service.set_flag(self.session, flag, enabled=False, actor=remediation.approved_by_name or actor, reason=f"NexOps remediation for {incident_key(remediation.incident_id)}")
        notifications.queue(
            self.session, "flag.disabled", f"Feature {flag.key} disabled on {app.name}",
            f"NexOps switched off {flag.key} for {incident_key(remediation.incident_id)} ({remediation.mode.replace('_', ' ')}). Verifying recovery now.",
            f"/incidents/{incident_key(remediation.incident_id)}",
        )
        return ExecutionResult("completed", "feature_flags", {"flag": flag.key, "enabled": False})

    def _record_rollback_deployment(self, incident: Incident, remediation: RemediationAction) -> Deployment:
        app = incident.application
        failed = incident.deployment
        now = utc_now()
        rollback = Deployment(
            application_id=app.id, environment=app.environment, version=remediation.target_version or "unknown",
            previous_version=failed.version if failed else app.current_version, kind="rollback", source="nexops",
            status="verifying", status_reason=f"Rollback executed by NexOps for {incident_key(incident.id)}; verifying recovery.",
            ci_status="success", image=remediation.target_image, started_at=remediation.started_at or now, finished_at=now,
            summary=f"Rollback from {failed.version if failed else app.current_version} to {remediation.target_version} for {incident_key(incident.id)}.",
            pipeline=[{"name": "Approval", "status": "success"}, {"name": "Rollback", "status": "success"}, {"name": "Verify", "status": "in_progress"}],
            author=remediation.approved_by_name, repository=app.repository,
        )
        self.session.add(rollback)
        if failed:
            failed.status = "rolled_back"
            failed.status_reason = f"Rolled back to {remediation.target_version} for {incident_key(incident.id)}."
        app.previous_version = failed.version if failed else app.current_version
        app.current_version = remediation.target_version
        remediation.outcome = {**(remediation.outcome or {}), "rollback_deployment": "pending"}
        self.session.flush()
        remediation.outcome = {**remediation.outcome, "rollback_deployment": f"DEP-{rollback.id}"}
        return rollback

    def on_deployment_recorded(self, deployment: Deployment) -> None:
        """Link CI-reported rollback deployments to a dispatched remediation."""
        if deployment.ci_status != "success" or deployment.finished_at is None:
            return
        remediation = self.session.scalars(select(RemediationAction).join(Incident, Incident.id == RemediationAction.incident_id).where(
            Incident.application_id == deployment.application_id, RemediationAction.status == "dispatched",
            RemediationAction.target_version == deployment.version,
        ).order_by(RemediationAction.id.desc()).limit(1)).first()
        if remediation is None:
            return
        incident = self._incident(remediation.incident_id)
        remediation.status = "succeeded"
        remediation.completed_at = utc_now()
        remediation.outcome = {**(remediation.outcome or {}), "rollback_deployment": f"DEP-{deployment.id}", "ci_run_url": deployment.ci_run_url}
        deployment.kind = "rollback"
        if incident.deployment and incident.deployment.id != deployment.id:
            incident.deployment.status = "rolled_back"
            incident.deployment.status_reason = f"Rolled back to {deployment.version} for {incident_key(incident.id)}."
        add_event(self.session, incident, f"Rollback deployment {deployment.version} completed", f"Reported by CI run {deployment.ci_run_url or deployment.ci_run_id or ''}.", kind="remediation", severity="healthy", actor="github-actions")

    # ------------------------------------------------------------------ verification
    def start_verification(self, incident: Incident, remediation: RemediationAction | None, actor: str) -> RecoveryVerification:
        for running in self.session.scalars(select(RecoveryVerification).where(RecoveryVerification.incident_id == incident.id, RecoveryVerification.status == "running")):
            running.status = "superseded"
            running.finished_at = utc_now()
        now = utc_now()
        grace = timedelta(seconds=max(0, self.settings.recovery_grace_seconds))
        verification = RecoveryVerification(
            incident_id=incident.id,
            remediation_id=remediation.id if remediation else None,
            status="running",
            required_samples=max(1, self.settings.recovery_required_healthy_samples),
            baseline=incident.baseline or {},
            target_version=remediation.target_version if remediation and remediation.action == "rollback" else None,
            not_before=now + grace,
            deadline_at=now + timedelta(minutes=self.settings.recovery_timeout_minutes),
            started_at=now,
        )
        self.session.add(verification)
        incident.status = "verifying"
        incident.mitigated_at = incident.mitigated_at or now
        add_event(
            self.session, incident, "Recovery verification started",
            f"NexOps requires {verification.required_samples} consecutive healthy samples within {self.settings.recovery_timeout_minutes} minutes.",
            kind="verification", actor=actor,
        )
        self.session.flush()
        return verification

    def request_verification(self, incident_id: int, user: User) -> RecoveryVerification:
        incident = self._incident(incident_id)
        if incident.status == "resolved":
            raise WorkflowError("The incident is already resolved.")
        audit(self.session, user.email, "incident.verify", "incident", incident_key(incident.id))
        return self.start_verification(incident, None, user.display_name)

    def verify_cycle(self, application_id: str, sample: MetricSample) -> RecoveryVerification | None:
        incident = self.session.scalars(select(Incident).where(
            Incident.application_id == application_id, Incident.status == "verifying",
        ).order_by(Incident.id.desc()).limit(1).with_for_update()).first()
        if incident is None:
            return None
        verification = self.session.scalars(select(RecoveryVerification).where(
            RecoveryVerification.incident_id == incident.id, RecoveryVerification.status == "running",
        ).order_by(RecoveryVerification.id.desc()).limit(1)).first()
        if verification is None:
            return None
        if verification.not_before and as_utc(sample.observed_at) < as_utc(verification.not_before):
            return verification
        if verification.last_sample_id == sample.id:
            return verification
        thresholds = Thresholds.from_overrides(incident.application.thresholds)
        checks = recovery_checks(thresholds, sample_to_signals(sample), verification.baseline or {}, verification.target_version)
        verification.checks = checks
        verification.last_sample_id = sample.id
        if all(check["passed"] for check in checks):
            verification.healthy_samples += 1
        else:
            verification.healthy_samples = 0
        if verification.healthy_samples >= verification.required_samples:
            self._resolve(incident, verification)
        elif verification.deadline_at and as_utc(sample.observed_at) > as_utc(verification.deadline_at):
            verification.status = "failed"
            verification.finished_at = utc_now()
            failing = [check["name"] for check in checks if not check["passed"]]
            verification.detail = f"Recovery not verified before the deadline. Failing checks: {', '.join(failing) or 'unstable'}."
            incident.status = "investigating"
            add_event(self.session, incident, "Recovery NOT verified", verification.detail, kind="verification", severity="critical", actor="recovery-verifier")
            notifications.queue(self.session, "recovery.failed", f"Recovery not verified: {incident.title}", verification.detail, f"/incidents/{incident_key(incident.id)}")
            with agent_run(self.session, "recovery_verification", incident_id=incident.id, provider="rules") as run:
                run.complete(verification.detail, {"checks": checks})
        return verification

    def _resolve(self, incident: Incident, verification: RecoveryVerification) -> None:
        now = utc_now()
        verification.status = "passed"
        verification.finished_at = now
        summary = ", ".join(f"{check['name']} {check['observed']}" for check in verification.checks)
        verification.detail = f"{verification.healthy_samples} consecutive healthy samples: {summary}."
        incident.status = "resolved"
        incident.resolved_at = now
        remediation = self.session.get(RemediationAction, verification.remediation_id) if verification.remediation_id else None
        action_text = action_label(remediation.action, remediation.target_version, remediation.target_flag) if remediation else "Recovered without a NexOps remediation"
        incident.resolution_summary = f"{action_text}; recovery verified."
        with agent_run(self.session, "recovery_verification", incident_id=incident.id, provider="rules") as run:
            run.complete(verification.detail, {"checks": verification.checks, "samples": verification.healthy_samples})
        add_event(self.session, incident, "Recovery verified", verification.detail, kind="verification", severity="healthy", actor="recovery-verifier")
        add_event(self.session, incident, f"{incident_key(incident.id)} resolved", incident.resolution_summary, kind="resolved", severity="healthy", actor="nexops")
        minutes = round((now - as_utc(incident.detected_at)).total_seconds() / 60, 1)
        notifications.queue(
            self.session, "incident.resolved", f"Resolved: {incident.title}",
            f"{incident.resolution_summary} Duration {minutes} min. {verification.detail}", f"/incidents/{incident_key(incident.id)}",
        )
        for deployment in self.session.scalars(select(Deployment).where(
            Deployment.application_id == incident.application_id, Deployment.kind == "rollback", Deployment.status == "verifying",
        )):
            deployment.status = "healthy"
            deployment.verified_at = now
            deployment.status_reason = f"Recovery verified for {incident_key(incident.id)}."
            deployment.pipeline = [{**stage, "status": "success"} if stage.get("name") == "Verify" else stage for stage in (deployment.pipeline or [])]
        if incident.deployment and incident.deployment.status == "degraded" and remediation is None:
            incident.deployment.status = "healthy"
            incident.deployment.status_reason = "Production recovered and was verified without a rollback."
        remember(self.session, incident, action_text, recovery_successful=True)
        self.session.flush()
        try:
            PostmortemService(self.session, self.integrations).generate(incident)
            add_event(self.session, incident, "Postmortem generated", "Stored with the incident and added to incident memory.", kind="postmortem", actor="postmortem-agent")
        except Exception:  # a postmortem failure must never reopen a verified incident
            logger.exception("Postmortem generation failed for INC-%s", incident.id)

    def dismiss(self, incident_id: int, user: User, reason: str) -> Incident:
        incident = self._incident(incident_id)
        if incident.status == "resolved":
            raise WorkflowError("The incident is already resolved.")
        incident.status = "resolved"
        incident.resolved_at = utc_now()
        incident.resolution_summary = f"Closed without recovery verification by {user.display_name}: {reason}"
        for remediation in self.session.scalars(select(RemediationAction).where(RemediationAction.incident_id == incident.id, RemediationAction.status.in_(("proposed", "approved")))):
            remediation.status = "superseded"
        for verification in self.session.scalars(select(RecoveryVerification).where(RecoveryVerification.incident_id == incident.id, RecoveryVerification.status == "running")):
            verification.status = "superseded"
            verification.finished_at = utc_now()
        add_event(self.session, incident, "Incident closed manually", incident.resolution_summary, kind="resolved", severity="warning", actor=user.display_name)
        audit(self.session, user.email, "incident.dismiss", "incident", incident_key(incident.id), {"reason": reason})
        return incident


def active_incident_ids(session: Session, application_id: str) -> list[int]:
    return list(session.scalars(select(Incident.id).where(Incident.application_id == application_id, Incident.status.in_(ACTIVE_STATUSES))))
