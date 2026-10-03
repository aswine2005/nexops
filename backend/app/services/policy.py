"""Policy engine: decides whether a remediation may run automatically, needs human approval, or is forbidden."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Application, Incident, Policy, RemediationAction, utc_now

MODES = ("automatic", "approval_required", "forbidden")
POLICY_ACTIONS = ("rollback", "restart", "scale_up", "pause_deployments", "disable_feature", "database_modification")

# Safe defaults from the NexOps operating model. They are configuration, editable on the Policies page.
DEFAULT_POLICIES = [
    {
        "id": "production-rollback-approval",
        "name": "Production rollback requires approval",
        "description": "Rollbacks in production are proposed by the Remediation Agent and executed only after an operator approves.",
        "action": "rollback", "environment": "production", "mode": "approval_required",
        "conditions": {"require_previous_version": True, "min_confidence": 0.5},
    },
    {
        "id": "nonprod-rollback-automatic",
        "name": "Non-production rollback is automatic",
        "description": "Staging and development rollbacks run automatically when the investigation is confident.",
        "action": "rollback", "environment": "staging", "mode": "automatic",
        "conditions": {"require_previous_version": True, "min_confidence": 0.6, "max_executions_per_hour": 3},
    },
    {
        "id": "production-restart-approval",
        "name": "Production restart requires approval",
        "description": "Restarting production workloads needs an operator approval.",
        "action": "restart", "environment": "production", "mode": "approval_required", "conditions": {},
    },
    {
        "id": "nonprod-restart-automatic",
        "name": "Restart non-production services automatically",
        "description": "Non-production services may be restarted without approval.",
        "action": "restart", "environment": "development", "mode": "automatic", "conditions": {"max_executions_per_hour": 5},
    },
    {
        "id": "production-scale-approval",
        "name": "Production scaling requires approval",
        "description": "Adding replicas in production needs operator approval.",
        "action": "scale_up", "environment": "production", "mode": "approval_required", "conditions": {"max_replicas": 10},
    },
    {
        "id": "production-disable-feature-automatic",
        "name": "Disable a failing feature automatically",
        "description": "Turning off a recently enabled feature flag is instant and reversible, so NexOps may do it without approval when the investigation is confident.",
        "action": "disable_feature", "environment": "*", "mode": "automatic",
        "conditions": {"min_confidence": 0.6, "max_executions_per_hour": 5},
    },
    {
        "id": "production-database-forbidden",
        "name": "Production database modification is not permitted",
        "description": "NexOps never modifies production databases. Database changes stay with the owning team.",
        "action": "database_modification", "environment": "production", "mode": "forbidden", "conditions": {},
    },
]


@dataclass
class PolicyDecision:
    mode: str
    policy: Policy | None
    reasons: list[str] = field(default_factory=list)

    @property
    def allowed(self) -> bool:
        return self.mode != "forbidden"


def ensure_default_policies(session: Session) -> int:
    """Insert default policies the first time NexOps starts. Existing policies are never overwritten."""
    if session.scalar(select(func.count(Policy.id))):
        return 0
    for item in DEFAULT_POLICIES:
        session.add(Policy(enabled=True, **item))
    return len(DEFAULT_POLICIES)


class PolicyEngine:
    def __init__(self, session: Session) -> None:
        self.session = session

    def matching(self, action: str, app: Application) -> list[Policy]:
        environment = (app.environment or "production").lower()
        policies = self.session.scalars(select(Policy).where(Policy.enabled.is_(True), Policy.action == action)).all()

        def applies(policy: Policy) -> bool:
            env_ok = policy.environment in {"*", environment}
            # A "staging" policy also governs development, since both are non-production.
            if not env_ok and policy.environment == "staging" and environment == "development":
                env_ok = True
            if not env_ok and policy.environment == "development" and environment == "staging":
                env_ok = True
            return env_ok and (policy.application_id in {None, app.id})

        def specificity(policy: Policy) -> int:
            return (2 if policy.application_id else 0) + (1 if policy.environment == environment else 0)

        matched = [policy for policy in policies if applies(policy)]
        matched.sort(key=specificity, reverse=True)
        return matched

    def evaluate(self, action: str, app: Application, incident: Incident | None = None, confidence: float | None = None, has_previous_version: bool = True) -> PolicyDecision:
        matched = self.matching(action, app)
        if not matched:
            return PolicyDecision("approval_required", None, [f"No policy covers '{action}' in {app.environment}; defaulting to human approval."])
        top = matched[0]
        same_level = [policy for policy in matched if (policy.application_id, policy.environment) == (top.application_id, top.environment)]
        forbidden = next((policy for policy in same_level if policy.mode == "forbidden"), None)
        if forbidden:
            return PolicyDecision("forbidden", forbidden, [f"Policy '{forbidden.name}' forbids {action} in {app.environment}."])

        policy = top
        conditions = policy.conditions or {}
        reasons = [f"Policy '{policy.name}' applies ({policy.mode.replace('_', ' ')})."]
        mode = policy.mode
        if conditions.get("require_previous_version") and not has_previous_version:
            return PolicyDecision("forbidden", policy, reasons + ["No previous version is recorded, so a rollback target cannot be determined."])
        if mode == "automatic":
            minimum = conditions.get("min_confidence")
            if minimum is not None and (confidence is None or confidence < float(minimum)):
                mode = "approval_required"
                reasons.append(f"Investigation confidence {round((confidence or 0) * 100)}% is below the automatic threshold of {round(float(minimum) * 100)}%; approval required.")
            limit = conditions.get("max_executions_per_hour")
            if limit is not None:
                recent = self.session.scalar(select(func.count(RemediationAction.id)).join(Incident, Incident.id == RemediationAction.incident_id).where(
                    Incident.application_id == app.id,
                    RemediationAction.action == action,
                    RemediationAction.mode == "automatic",
                    RemediationAction.started_at >= utc_now() - timedelta(hours=1),
                )) or 0
                if recent >= int(limit):
                    mode = "approval_required"
                    reasons.append(f"{recent} automatic {action}(s) already ran in the last hour (limit {limit}); approval required.")
        return PolicyDecision(mode, policy, reasons)
