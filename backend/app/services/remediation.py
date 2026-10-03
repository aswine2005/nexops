"""Remediation executors: perform approved actions against Docker, Kubernetes, or GitHub Actions."""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.ids import incident_key
from app.integrations.registry import Integrations, get_integrations
from app.integrations.types import IntegrationError
from app.models import Application, Deployment, RemediationAction


@dataclass
class ExecutionResult:
    status: str  # completed | dispatched
    executor: str
    detail: dict = field(default_factory=dict)


def rollback_target(session: Session, app: Application, failed: Deployment | None) -> tuple[str | None, str | None]:
    """Version and image to roll back to: the failed release's previous version and the image it last ran with."""
    version = failed.previous_version if failed else app.previous_version
    if not version:
        return None, None
    known = session.scalars(select(Deployment).where(
        Deployment.application_id == app.id, Deployment.version == version, Deployment.image.is_not(None), Deployment.ci_status == "success",
    ).order_by(Deployment.id.desc()).limit(1)).first()
    image = known.image if known else (f"{app.image_repository}:{version}" if app.image_repository else None)
    return version, image


def capability_gap(app: Application, action: str) -> str | None:
    """Explain why an action cannot run for this application, or None when it can."""
    if action == "rollback":
        if app.rollback_strategy == "none":
            return f"{app.name} has no rollback strategy configured."
        if app.rollback_strategy == "github_actions" and not (app.repository and app.github_workflow):
            return "GitHub Actions rollback needs the repository and workflow file configured."
    if action == "restart" and app.runtime not in {"docker", "kubernetes"}:
        return f"Restart is supported for Docker and Kubernetes runtimes, not '{app.runtime}'."
    if action == "scale_up" and app.runtime != "kubernetes":
        return "Scaling is supported for Kubernetes deployments only."
    if action == "database_modification":
        return "NexOps never modifies databases."
    return None


class RemediationExecutor:
    def __init__(self, integrations: Integrations | None = None) -> None:
        self.integrations = integrations or get_integrations()

    def execute(self, app: Application, remediation: RemediationAction) -> ExecutionResult:
        action = remediation.action
        if action == "rollback":
            return self._rollback(app, remediation)
        if action == "restart":
            if app.runtime == "docker":
                return ExecutionResult("completed", "docker", self._docker().restart(app))
            if app.runtime == "kubernetes":
                return ExecutionResult("completed", "kubernetes", self._kubernetes().restart(app))
        if action == "scale_up" and app.runtime == "kubernetes":
            client = self._kubernetes()
            state = client.state(app)
            maximum = int((remediation.outcome or {}).get("max_replicas") or 10)
            replicas = min(maximum, (state.desired_replicas or len(state.replicas) or 1) + 1)
            return ExecutionResult("completed", "kubernetes", client.scale(app, replicas))
        if action == "pause_deployments":
            app.deploy_frozen = True
            app.deploy_freeze_reason = f"Deployments paused by NexOps for {incident_key(remediation.incident_id)}."
            return ExecutionResult("completed", "nexops", {"deploy_frozen": True})
        raise IntegrationError(capability_gap(app, action) or f"No executor supports '{action}' for {app.runtime}.")

    def _rollback(self, app: Application, remediation: RemediationAction) -> ExecutionResult:
        if not remediation.target_version:
            raise IntegrationError("No rollback target version was recorded.")
        strategy = app.rollback_strategy
        if strategy == "docker":
            if not remediation.target_image:
                raise IntegrationError(f"No image is known for {remediation.target_version}; set the application's image repository.")
            return ExecutionResult("completed", "docker", self._docker().redeploy(app, remediation.target_image))
        if strategy == "kubernetes":
            if not remediation.target_image:
                raise IntegrationError(f"No image is known for {remediation.target_version}; set the application's image repository.")
            return ExecutionResult("completed", "kubernetes", self._kubernetes().set_image(app, remediation.target_image))
        if strategy == "github_actions":
            github = self.integrations.github
            if github is None:
                raise IntegrationError("GITHUB_TOKEN is not configured, so the rollback workflow cannot be dispatched.")
            inputs = {
                "version": remediation.target_version,
                "environment": app.environment,
                "reason": f"NexOps {incident_key(remediation.incident_id)} rollback",
                "rollback": "true",
            }
            github.dispatch_workflow(app.repository or "", app.github_workflow or "", app.github_ref or "main", inputs)
            return ExecutionResult("dispatched", "github_actions", {"repository": app.repository, "workflow": app.github_workflow, "ref": app.github_ref or "main", "inputs": inputs})
        raise IntegrationError(capability_gap(app, "rollback") or "Rollback strategy is not configured.")

    def _docker(self):
        client = self.integrations.docker
        if client is None:
            raise IntegrationError(self.integrations.failure("docker") or "Docker integration is disabled (DOCKER_ENABLED=false).")
        return client

    def _kubernetes(self):
        client = self.integrations.kubernetes
        if client is None:
            raise IntegrationError(self.integrations.failure("kubernetes") or "Kubernetes integration is disabled (KUBERNETES_ENABLED=false).")
        return client
