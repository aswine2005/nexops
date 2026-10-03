"""Incident memory: stores resolved incidents and retrieves similar ones as investigation context."""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Incident, IncidentMemory

_STOPWORDS = {
    "the", "and", "for", "with", "after", "that", "this", "from", "into", "most", "likely", "release", "service",
    "began", "right", "while", "which", "there", "their", "were", "have", "been", "because", "version", "production",
    "incident", "errors", "error", "rollout", "changed", "started", "connection", "connections",
}


def keywords(text: str | None) -> list[str]:
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9_\-]{3,}", (text or "").lower())
    seen: list[str] = []
    for word in words:
        if word not in _STOPWORDS and word not in seen:
            seen.append(word)
    return seen[:25]


def remember(session: Session, incident: Incident, action: str, recovery_successful: bool) -> IncidentMemory:
    existing = session.scalars(select(IncidentMemory).where(IncidentMemory.incident_id == incident.id)).first()
    memory = existing or IncidentMemory(incident_id=incident.id, application_id=incident.application_id)
    memory.title = incident.title
    memory.failed_version = incident.deployment.version if incident.deployment else None
    memory.root_cause = incident.root_cause or "Root cause was not determined."
    memory.action = action
    memory.recovery_successful = recovery_successful
    memory.signals = sorted({signal["code"] for signal in incident.signals})
    memory.keywords = keywords(f"{incident.root_cause or ''} {incident.title}")
    if existing is None:
        session.add(memory)
    return memory


def similar(session: Session, incident: Incident, limit: int = 5) -> list[tuple[IncidentMemory, float]]:
    codes = {signal["code"] for signal in incident.signals}
    words = set(keywords(f"{incident.root_cause or ''} {incident.title} {incident.summary}"))
    scored: list[tuple[IncidentMemory, float]] = []
    for memory in session.scalars(select(IncidentMemory).where(IncidentMemory.incident_id != incident.id).order_by(IncidentMemory.created_at.desc()).limit(500)):
        score = 0.0
        if memory.application_id == incident.application_id:
            score += 2.0
        memory_codes = set(memory.signals or [])
        if codes and memory_codes:
            score += 3.0 * len(codes & memory_codes) / len(codes | memory_codes)
        memory_words = set(memory.keywords or [])
        if words and memory_words:
            score += min(2.0, 0.5 * len(words & memory_words))
        if score >= 2.5:
            scored.append((memory, round(score, 2)))
    scored.sort(key=lambda item: item[1], reverse=True)
    return scored[:limit]
