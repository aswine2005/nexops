"""Human-readable identifiers (DEP-12, INC-7) mapped to integer primary keys."""
from __future__ import annotations

import re
from datetime import datetime, timezone

_KEY = re.compile(r"^(?:[A-Za-z]+-)?(\d+)$")


def deployment_key(deployment_id: int) -> str:
    return f"DEP-{deployment_id}"


def incident_key(incident_id: int) -> str:
    return f"INC-{incident_id}"


def parse_key(value: str | int) -> int | None:
    if isinstance(value, int):
        return value
    match = _KEY.match(value.strip())
    return int(match.group(1)) if match else None


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug[:100] or "application"
