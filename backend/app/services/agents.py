"""AI agent catalogue with live statistics from recorded agent runs."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.ids import iso
from app.models import AgentRun, utc_now

AGENTS = [
    {
        "id": "deployment_risk", "name": "Deployment Risk Agent", "stage": "Deploy",
        "description": "Scores every release from changed files, change size, CI status and the service's recent failure history.",
        "trigger": "Every deployment webhook from CI/CD", "uses_llm": False,
        "inputs": ["Changed files and diff size", "CI/CD status", "Incident history (30 days)"],
        "outputs": ["Risk score 0–100", "Risk level", "Risk factors"],
    },
    {
        "id": "canary_analysis", "name": "Canary Analysis Agent", "stage": "Deploy",
        "description": "Answers Argo Rollouts during a canary: compares the canary pods' error rate, P99 latency and business success with the stable pods in Prometheus. A failing verdict makes Argo abort the rollout before most users see the release.",
        "trigger": "Each analysis step of an Argo Rollouts canary", "uses_llm": False,
        "inputs": ["Prometheus metrics split by rollouts_pod_template_hash", "Application thresholds"],
        "outputs": ["pass / fail / inconclusive", "Reasons", "Canary vs stable metrics"],
    },
    {
        "id": "investigation", "name": "Investigation Agent", "stage": "Investigate",
        "description": "Collects the evidence package: deployment diff, metrics against the pre-release baseline, error logs, replica state, health checks and similar past incidents.",
        "trigger": "Incident creation (automatic) or the Run investigation button", "uses_llm": False,
        "inputs": ["Prometheus / CloudWatch metrics", "Loki / Docker / Kubernetes / CloudWatch logs", "Runtime state", "Incident memory"],
        "outputs": ["Evidence items with stable IDs"],
    },
    {
        "id": "root_cause", "name": "Root Cause Agent", "stage": "Investigate",
        "description": "Correlates the evidence into the most likely root cause with a confidence score, citations and alternative hypotheses. Uses Groq when configured, deterministic evidence rules otherwise.",
        "trigger": "After evidence collection", "uses_llm": True,
        "inputs": ["Evidence package"], "outputs": ["Likely root cause", "Confidence", "Recommended action", "Cited evidence"],
    },
    {
        "id": "remediation", "name": "Remediation Agent", "stage": "Remediate",
        "description": "Turns the recommendation into a concrete action (rollback target, restart, scale) and evaluates it against policies: automatic, approval required, or forbidden.",
        "trigger": "After root-cause analysis", "uses_llm": False,
        "inputs": ["Recommendation", "Policies", "Deployment history"], "outputs": ["Proposed action", "Policy decision"],
    },
    {
        "id": "recovery_verification", "name": "Recovery Verifier", "stage": "Verify",
        "description": "Checks error rate, latency, replicas, health endpoints and running version against the pre-incident baseline over consecutive samples before resolving.",
        "trigger": "After a remediation executes", "uses_llm": False,
        "inputs": ["Post-remediation telemetry", "Pre-incident baseline"], "outputs": ["Recovery checks", "Resolved / not verified"],
    },
    {
        "id": "postmortem", "name": "Postmortem Agent", "stage": "Learn",
        "description": "Writes a blameless postmortem from recorded facts (timeline, impact, detection and recovery times) and stores the incident in memory for future investigations.",
        "trigger": "Incident resolved", "uses_llm": True,
        "inputs": ["Incident timeline", "Telemetry", "Remediation"], "outputs": ["Postmortem", "Incident memory"],
    },
]


def agent_catalogue(session: Session) -> list[dict]:
    settings = get_settings()
    since = utc_now() - timedelta(hours=24)
    result = []
    for agent in AGENTS:
        total = session.scalar(select(func.count(AgentRun.id)).where(AgentRun.agent == agent["id"])) or 0
        recent = session.scalar(select(func.count(AgentRun.id)).where(AgentRun.agent == agent["id"], AgentRun.started_at >= since)) or 0
        failed = session.scalar(select(func.count(AgentRun.id)).where(AgentRun.agent == agent["id"], AgentRun.status == "failed")) or 0
        running = session.scalar(select(func.count(AgentRun.id)).where(AgentRun.agent == agent["id"], AgentRun.status == "running")) or 0
        last = session.scalars(select(AgentRun).where(AgentRun.agent == agent["id"]).order_by(AgentRun.started_at.desc()).limit(1)).first()
        provider = ("groq" if settings.groq_api_key else "local-rules") if agent["uses_llm"] else "rules"
        result.append({
            **agent,
            "provider": provider,
            "model": settings.groq_model if agent["uses_llm"] and settings.groq_api_key else None,
            "status": "running" if running else ("idle" if total else "waiting"),
            "runs_total": total,
            "runs_24h": recent,
            "success_rate": round((total - failed) / total * 100, 1) if total else None,
            "last_run": serialize_run(last) if last else None,
        })
    return result


def serialize_run(run: AgentRun) -> dict:
    duration = None
    if run.finished_at and run.started_at:
        duration = round((run.finished_at - run.started_at).total_seconds(), 2)
    return {
        "id": run.id,
        "agent": run.agent,
        "status": run.status,
        "incident_id": f"INC-{run.incident_id}" if run.incident_id else None,
        "deployment_id": f"DEP-{run.deployment_id}" if run.deployment_id else None,
        "provider": run.provider,
        "model": run.model,
        "summary": run.summary,
        "error": run.error,
        "output": run.output,
        "started_at": iso(run.started_at),
        "finished_at": iso(run.finished_at),
        "duration_seconds": duration,
    }
