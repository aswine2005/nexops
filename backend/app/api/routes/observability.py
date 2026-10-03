import asyncio
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import require_viewer
from app.core.ids import deployment_key, incident_key, iso
from app.database.session import get_db
from app.detection.engine import Thresholds, compute_baseline
from app.integrations.types import IntegrationError
from app.models import Application, Deployment, Incident, MetricSample, User, as_utc
from app.services.serializers import sample_dict
from app.services.telemetry import TelemetryService, sample_to_signals

router = APIRouter(tags=["observability"])

RANGES = {"15m": timedelta(minutes=15), "1h": timedelta(hours=1), "6h": timedelta(hours=6), "24h": timedelta(hours=24), "7d": timedelta(days=7)}
FIELDS = (
    "request_rate", "error_rate_percent", "p50_latency_ms", "p95_latency_ms", "p99_latency_ms", "cpu_percent", "memory_percent",
    "healthy_replicas", "total_replicas", "restarts", "db_errors", "business_success_rate",
)


def _downsample(samples: list[MetricSample], start: datetime, end: datetime, max_points: int = 240) -> list[dict]:
    if not samples:
        return []
    if len(samples) <= max_points:
        return [{"t": iso(sample.observed_at), **{field: getattr(sample, field) for field in FIELDS}} for sample in samples]
    bucket = (end - start) / max_points
    buckets: dict[int, list[MetricSample]] = {}
    for sample in samples:
        index = int((as_utc(sample.observed_at) - start) / bucket)
        buckets.setdefault(index, []).append(sample)
    points = []
    for index in sorted(buckets):
        group = buckets[index]
        point = {"t": iso(group[-1].observed_at)}
        for field in FIELDS:
            values = [getattr(item, field) for item in group if getattr(item, field) is not None]
            if field in {"restarts", "total_replicas", "db_errors", "p99_latency_ms", "error_rate_percent"}:
                point[field] = max(values) if values else None
            elif field == "healthy_replicas":
                point[field] = min(values) if values else None
            else:
                point[field] = round(sum(values) / len(values), 3) if values else None
        points.append(point)
    return points


@router.get("/metrics")
def read_metrics(
    application: str,
    time_range: str = Query(default="1h", alias="range", pattern="^(15m|1h|6h|24h|7d)$"),
    db: Session = Depends(get_db),
    _: User = Depends(require_viewer),
) -> dict:
    app = db.get(Application, application)
    if app is None:
        raise HTTPException(status_code=404, detail="Application not found.")
    end = datetime.now(timezone.utc)
    span = RANGES[time_range]
    start = end - span
    samples = db.scalars(select(MetricSample).where(
        MetricSample.application_id == app.id, MetricSample.observed_at >= start,
    ).order_by(MetricSample.observed_at)).all()
    previous = db.scalars(select(MetricSample).where(
        MetricSample.application_id == app.id, MetricSample.observed_at >= start - span, MetricSample.observed_at < start,
    )).all()
    deployments = db.scalars(select(Deployment).where(
        Deployment.application_id == app.id, Deployment.finished_at >= start,
    ).order_by(Deployment.finished_at)).all()
    incidents = db.scalars(select(Incident).where(Incident.application_id == app.id, Incident.detected_at >= start - span)).all()
    return {
        "application_id": app.id,
        "application_name": app.name,
        "range": time_range,
        "start": iso(start),
        "end": iso(end),
        "points": _downsample(list(samples), start, end),
        "latest": sample_dict(samples[-1]) if samples else None,
        "previous": compute_baseline([sample_to_signals(sample) for sample in previous]),
        "thresholds": Thresholds.from_overrides(app.thresholds).as_dict(),
        "deployments": [
            {"id": deployment_key(item.id), "version": item.version, "kind": item.kind, "status": item.status, "t": iso(item.finished_at)}
            for item in deployments
        ],
        "incidents": [
            {"id": incident_key(item.id), "title": item.title, "detected_at": iso(item.detected_at), "resolved_at": iso(item.resolved_at), "status": item.status}
            for item in incidents
        ],
        "sample_count": len(samples),
    }


@router.get("/logs")
async def read_logs(
    application: str | None = None,
    environment: str | None = None,
    level: str | None = None,
    q: str | None = Query(default=None, max_length=300),
    time_range: str = Query(default="1h", alias="range", pattern="^(15m|1h|6h|24h|7d)$"),
    limit: int = Query(default=200, ge=1, le=1000),
    db: Session = Depends(get_db),
    _: User = Depends(require_viewer),
) -> dict:
    query = select(Application).order_by(Application.name)
    if application:
        query = query.where(Application.id == application)
    elif environment:
        query = query.where(Application.environment == environment.lower())
    apps = db.scalars(query.limit(20)).all()
    for app in apps:
        db.expunge(app)
    end = datetime.now(timezone.utc)
    start = end - RANGES[time_range]
    telemetry = TelemetryService()
    entries: list[dict] = []
    sources: dict[str, str] = {}
    errors: dict[str, str] = {}

    def fetch(app: Application) -> None:
        try:
            lines, source = telemetry.logs(app, start, end, limit=limit, level=level, query=q)
            sources[app.id] = source
            for line in lines:
                entries.append({**line.as_dict(), "application_id": app.id})
        except IntegrationError as error:
            errors[app.id] = str(error)[:300]
        except Exception as error:  # surface provider failures per application instead of failing the page
            errors[app.id] = f"{error.__class__.__name__}: {error}"[:300]

    await asyncio.gather(*(asyncio.to_thread(fetch, app) for app in apps))
    entries.sort(key=lambda entry: entry["timestamp"], reverse=True)
    return {"entries": entries[:limit], "sources": sources, "errors": errors, "start": iso(start), "end": iso(end)}
