"""Deployment ingestion from CI/CD, change enrichment, risk scoring, and post-release verification status."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.integrations.registry import Integrations, get_integrations
from app.integrations.types import IntegrationError
from app.models import Application, Deployment, DeploymentChange, Incident, as_utc, utc_now
from app.services.common import agent_run

logger = logging.getLogger(__name__)

# (category label, regex on the file path, risk points)
SENSITIVE_PATHS: list[tuple[str, re.Pattern[str], int]] = [
    ("Database / migration", re.compile(r"(migration|schema|\.sql$|(^|/)db(/|\.|_)|database|repository|orm|pool)", re.I), 20),
    ("Configuration", re.compile(r"(config|settings|\.env|\.ya?ml$|\.toml$|\.ini$|\.properties$|values\.)", re.I), 15),
    ("Authentication / secrets", re.compile(r"(auth|security|secret|token|session|permission|iam)", re.I), 12),
    ("Business-critical path", re.compile(r"(payment|billing|checkout|order|invoice|ledger)", re.I), 10),
    ("Infrastructure / delivery", re.compile(r"(dockerfile|docker-compose|compose\.ya?ml|k8s|kubernetes|helm|terraform|\.tf$|\.github/workflows)", re.I), 10),
    ("Dependencies", re.compile(r"(package-lock\.json|yarn\.lock|pnpm-lock|requirements.*\.txt|poetry\.lock|go\.sum|pom\.xml|build\.gradle)", re.I), 6),
]


def classify_file(path: str) -> str | None:
    for label, pattern, _points in SENSITIVE_PATHS:
        if pattern.search(path):
            return label
    return None


class DeploymentService:
    def __init__(self, session: Session, integrations: Integrations | None = None, settings: Settings | None = None) -> None:
        self.session = session
        self.integrations = integrations or get_integrations()
        self.settings = settings or get_settings()

    # ------------------------------------------------------------------ ingestion
    def ingest(self, payload: dict) -> tuple[Deployment, bool]:
        """Record a CI/CD deployment event. Returns (deployment, created)."""
        app = self.session.get(Application, payload["application"])
        if app is None:
            raise LookupError(f"Application '{payload['application']}' is not registered in NexOps.")
        version = payload["version"].strip()
        event = payload.get("event", "finished")
        deployment = self._find_existing(app.id, version, payload.get("ci_run_id"))
        created = deployment is None
        if deployment is None:
            deployment = Deployment(
                application_id=app.id,
                environment=(payload.get("environment") or app.environment).lower(),
                version=version,
                started_at=payload.get("started_at") or utc_now(),
                previous_version=payload.get("previous_version") or (app.current_version if app.current_version != version else app.previous_version),
            )
            self.session.add(deployment)

        for field in ("kind", "source", "commit_sha", "commit_message", "branch", "author", "repository", "ci_run_id", "ci_run_url", "image", "summary"):
            if payload.get(field):
                setattr(deployment, field, payload[field])
        if payload.get("commit_timestamp"):
            deployment.commit_timestamp = payload["commit_timestamp"]
        if payload.get("pipeline"):
            deployment.pipeline = [dict(stage) for stage in payload["pipeline"]]
        deployment.repository = deployment.repository or app.repository
        if deployment.kind == "release" and _looks_like_hotfix(deployment.commit_message):
            deployment.kind = "hotfix"

        if event == "started":
            deployment.status = "in_progress"
            deployment.ci_status = "in_progress"
            self.session.flush()
            return deployment, created

        ci_status = payload.get("ci_status", "success")
        deployment.ci_status = ci_status
        deployment.finished_at = payload.get("finished_at") or utc_now()
        if ci_status == "success":
            open_incident = self.session.scalars(select(Incident).where(Incident.deployment_id == deployment.id, Incident.status != "resolved").limit(1)).first() if deployment.id else None
            if open_incident is not None:
                deployment.status = "degraded"
                deployment.status_reason = f"Production degraded during rollout; see INC-{open_incident.id}."
            else:
                deployment.status = "verifying"
                deployment.status_reason = "CI/CD passed. NexOps is verifying production telemetry."
            if app.current_version != version:
                app.previous_version = deployment.previous_version or app.current_version
                app.current_version = version
            for other in self.session.scalars(select(Deployment).where(
                Deployment.application_id == app.id, Deployment.status == "verifying", Deployment.id != deployment.id,
            )):
                other.status = "superseded"
                other.status_reason = f"Superseded by {version} before verification completed."
        elif ci_status == "aborted":
            deployment.status = "aborted"
            deployment.status_reason = payload.get("summary") or "Canary aborted before promotion; the stable version kept serving."
            if app.current_version == version and deployment.previous_version:
                app.current_version = deployment.previous_version  # a corrected report: this release never became current
        else:
            deployment.status = "failed"
            deployment.status_reason = f"CI/CD reported {ci_status}; the release did not reach production."

        self.session.flush()
        if deployment.kind != "rollback":  # a rollback redeploys an earlier image, not the current commit's diff
            self._attach_changes(deployment, payload.get("files"), payload.get("paths"))
        self.score_risk(deployment, app)
        self.session.flush()
        return deployment, created

    def _find_existing(self, application_id: str, version: str, ci_run_id: str | None) -> Deployment | None:
        query = select(Deployment).where(Deployment.application_id == application_id, Deployment.version == version)
        if ci_run_id:
            query = query.where(Deployment.ci_run_id == ci_run_id)
        else:
            query = query.where(Deployment.status == "in_progress")
        return self.session.scalars(query.order_by(Deployment.id.desc()).limit(1)).first()

    def _attach_changes(self, deployment: Deployment, files: list[dict] | None, paths: list[str] | None = None) -> None:
        if deployment.changes:
            return
        changes = list(files or [])
        github = self.integrations.github
        if github and deployment.repository and deployment.commit_sha:
            try:
                previous = self.session.scalars(select(Deployment).where(
                    Deployment.application_id == deployment.application_id,
                    Deployment.id != deployment.id,
                    Deployment.ci_status == "success",
                    Deployment.commit_sha.is_not(None),
                ).order_by(Deployment.finished_at.desc()).limit(1)).first()
                if previous and previous.commit_sha and previous.commit_sha != deployment.commit_sha:
                    detail = github.compare(deployment.repository, previous.commit_sha, deployment.commit_sha)
                    github_files = detail["files"]
                else:
                    commit = github.get_commit(deployment.repository, deployment.commit_sha)
                    github_files = commit["files"]
                    deployment.commit_message = deployment.commit_message or commit.get("message")
                    if commit.get("date") and not deployment.commit_timestamp:
                        deployment.commit_timestamp = datetime.fromisoformat(commit["date"].replace("Z", "+00:00"))
                if github_files:
                    changes = github_files
            except IntegrationError as error:
                logger.warning("GitHub enrichment failed for deployment %s: %s", deployment.id, error)
        prefixes = [prefix.strip().strip("/") + "/" for prefix in (paths or []) if prefix.strip()]
        for item in changes[:300]:
            path = item.get("filename") or item.get("file") or item.get("path")
            if not path or (prefixes and not any(path.startswith(prefix) for prefix in prefixes)):
                continue
            deployment.changes.append(DeploymentChange(
                file_path=path[:600],
                change_type=item.get("status", "modified"),
                additions=int(item.get("additions") or 0),
                deletions=int(item.get("deletions") or 0),
                risk_tag=classify_file(path),
                patch=(item.get("patch") or None),
            ))
        deployment.files_changed = len(deployment.changes)
        deployment.additions = sum(change.additions for change in deployment.changes)
        deployment.deletions = sum(change.deletions for change in deployment.changes)

    # ------------------------------------------------------------------ risk agent
    def score_risk(self, deployment: Deployment, app: Application) -> None:
        with agent_run(self.session, "deployment_risk", deployment_id=deployment.id, provider="rules") as run:
            if deployment.kind == "rollback":
                deployment.risk_score, deployment.risk_level = 5, "low"
                deployment.risk_factors = [f"Rollback to {deployment.version}, a previously deployed image"]
                run.complete(f"{app.name} {deployment.version}: rollback, low risk (5/100)", {"score": 5, "level": "low", "factors": deployment.risk_factors}, provider="rules")
                return
            score = 10
            factors: list[str] = []
            lines = deployment.additions + deployment.deletions
            if lines > 500:
                score += 20
                factors.append(f"Large change: {lines} lines")
            elif lines > 200:
                score += 12
                factors.append(f"Medium-sized change: {lines} lines")
            elif lines > 50:
                score += 6
            if deployment.files_changed > 20:
                score += 10
                factors.append(f"{deployment.files_changed} files changed")
            elif deployment.files_changed > 8:
                score += 5
            categories = {change.risk_tag for change in deployment.changes if change.risk_tag}
            for label, _pattern, points in SENSITIVE_PATHS:
                if label in categories:
                    score += points
                    touched = [change.file_path for change in deployment.changes if change.risk_tag == label][:3]
                    factors.append(f"{label} change: {', '.join(touched)}")
            since = utc_now() - timedelta(days=30)
            failed = self.session.scalar(select(func.count(Incident.id)).where(
                Incident.application_id == app.id, Incident.deployment_correlated.is_(True), Incident.detected_at >= since,
            )) or 0
            if failed:
                score += min(24, failed * 8)
                factors.append(f"{failed} deployment-related incident(s) for {app.name} in the last 30 days")
            if deployment.ci_status not in {"success", "in_progress"}:
                score += 30
                factors.append(f"CI/CD status {deployment.ci_status}")
            if deployment.kind == "hotfix":
                score += 5
                factors.append("Hotfix release")
            if deployment.environment == "production" and deployment.finished_at and as_utc(deployment.finished_at).weekday() >= 5:
                score += 5
                factors.append("Weekend production release")
            if not deployment.changes:
                factors.append("No file-level change data was supplied; risk is estimated from history only")
            score = max(0, min(100, score))
            level = "high" if score >= 60 else "medium" if score >= 30 else "low"
            deployment.risk_score = score
            deployment.risk_level = level
            deployment.risk_factors = factors
            run.complete(
                f"{app.name} {deployment.version}: {level} risk ({score}/100)",
                {"score": score, "level": level, "factors": factors, "categories": sorted(categories)},
                provider="rules",
            )

    # ------------------------------------------------------------------ verification lifecycle
    def update_verification(self, app: Application, app_status: str, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        window = timedelta(minutes=self.settings.deployment_verification_minutes)
        verifying = self.session.scalars(select(Deployment).where(Deployment.application_id == app.id, Deployment.status == "verifying")).all()
        for deployment in verifying:
            # Only incidents caused by this release (not e.g. a feature flag switched on later) degrade it.
            open_incident = self.session.scalars(select(Incident).where(
                Incident.deployment_id == deployment.id, Incident.deployment_correlated.is_(True), Incident.status != "resolved",
            ).limit(1)).first()
            if open_incident:
                deployment.status = "degraded"
                deployment.status_reason = f"Production degraded after release; see INC-{open_incident.id}."
                continue
            finished = as_utc(deployment.finished_at) or as_utc(deployment.started_at)
            if finished and finished + window <= now:
                if app_status == "healthy":
                    deployment.status = "healthy"
                    deployment.verified_at = now
                    deployment.status_reason = f"Production telemetry stayed within thresholds for {self.settings.deployment_verification_minutes} minutes after release."
                elif app_status == "unknown":
                    deployment.status = "unverified"
                    deployment.status_reason = "No production telemetry was available to verify this release."


def _looks_like_hotfix(message: str | None) -> bool:
    return bool(message and re.match(r"^\s*(\[?hotfix\]?|hotfix(\(.+\))?:)", message, re.I))
