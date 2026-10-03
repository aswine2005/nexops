"""Feature flags: NexOps stores them, applications poll them, and remediation can switch them off."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.ids import iso
from app.models import FeatureFlag, FeatureFlagChange, utc_now
from app.services.common import audit


def set_flag(session: Session, flag: FeatureFlag, *, enabled: bool | None = None, rollout_percent: int | None = None, actor: str, reason: str | None = None) -> FeatureFlagChange | None:
    """Apply a change and record it in the flag's history (no-op if nothing changes)."""
    new_enabled = flag.enabled if enabled is None else enabled
    new_percent = flag.rollout_percent if rollout_percent is None else max(0, min(100, rollout_percent))
    if new_enabled == flag.enabled and new_percent == flag.rollout_percent:
        return None
    flag.enabled = new_enabled
    flag.rollout_percent = new_percent
    flag.updated_by = actor
    flag.updated_at = utc_now()
    change = FeatureFlagChange(flag_id=flag.id, application_id=flag.application_id, enabled=new_enabled, rollout_percent=new_percent, actor=actor, reason=reason, changed_at=utc_now())
    session.add(change)
    audit(session, actor, "flag.enable" if new_enabled else "flag.disable", "feature_flag", f"{flag.application_id}/{flag.key}", {"rollout_percent": new_percent, "reason": reason})
    return change


def find(session: Session, application_id: str, key: str) -> FeatureFlag | None:
    return session.scalars(select(FeatureFlag).where(FeatureFlag.application_id == application_id, FeatureFlag.key == key)).first()


def recent_changes(session: Session, application_id: str, since: datetime, until: datetime | None = None) -> list[FeatureFlagChange]:
    query = select(FeatureFlagChange).where(FeatureFlagChange.application_id == application_id, FeatureFlagChange.changed_at >= since)
    if until is not None:
        query = query.where(FeatureFlagChange.changed_at <= until)
    return list(session.scalars(query.order_by(FeatureFlagChange.changed_at.desc())))


def evaluate(session: Session, application_id: str) -> dict:
    flags = session.scalars(select(FeatureFlag).where(FeatureFlag.application_id == application_id)).all()
    return {flag.key: {"enabled": flag.enabled, "rollout_percent": flag.rollout_percent} for flag in flags}


def serialize(flag: FeatureFlag, history: int = 5) -> dict:
    return {
        "id": flag.id,
        "application_id": flag.application_id,
        "key": flag.key,
        "description": flag.description,
        "enabled": flag.enabled,
        "rollout_percent": flag.rollout_percent,
        "updated_by": flag.updated_by,
        "updated_at": iso(flag.updated_at),
        "created_at": iso(flag.created_at),
        "history": [
            {"enabled": change.enabled, "rollout_percent": change.rollout_percent, "actor": change.actor, "reason": change.reason, "changed_at": iso(change.changed_at)}
            for change in flag.changes[:history]
        ],
    }
