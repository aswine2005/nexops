"""Shared helpers for timeline events, audit records and agent run bookkeeping."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

from sqlalchemy.orm import Session

from app.models import AgentRun, AuditLog, Incident, IncidentEvent, utc_now


def add_event(
    session: Session,
    incident: Incident,
    title: str,
    detail: str = "",
    *,
    kind: str = "note",
    severity: str = "info",
    actor: str | None = None,
    at: datetime | None = None,
) -> IncidentEvent:
    event = IncidentEvent(
        incident_id=incident.id,
        event_at=at or utc_now(),
        kind=kind,
        title=title[:300],
        detail=detail,
        severity=severity,
        actor=actor,
    )
    session.add(event)
    return event


def audit(session: Session, actor: str, action: str, target_type: str, target_id: str, detail: dict | None = None) -> None:
    session.add(AuditLog(actor=actor, action=action, target_type=target_type, target_id=str(target_id), detail=detail or {}))


class AgentRunHandle:
    def __init__(self, run: AgentRun) -> None:
        self.run = run

    def complete(self, summary: str, output: dict | None = None, provider: str | None = None, model: str | None = None) -> None:
        self.run.status = "completed"
        self.run.summary = summary[:4000]
        self.run.output = output or {}
        self.run.provider = provider or self.run.provider
        self.run.model = model or self.run.model
        self.run.finished_at = datetime.now(timezone.utc)

    def fail(self, error: str) -> None:
        self.run.status = "failed"
        self.run.error = error[:4000]
        self.run.finished_at = datetime.now(timezone.utc)


@contextmanager
def agent_run(session: Session, agent: str, *, incident_id: int | None = None, deployment_id: int | None = None, provider: str | None = None) -> Iterator[AgentRunHandle]:
    run = AgentRun(agent=agent, status="running", incident_id=incident_id, deployment_id=deployment_id, provider=provider, started_at=utc_now())
    session.add(run)
    session.flush()
    handle = AgentRunHandle(run)
    try:
        yield handle
    except Exception as error:
        handle.fail(f"{error.__class__.__name__}: {error}")
        raise
    else:
        if run.status == "running":
            handle.complete(run.summary or "Completed")
