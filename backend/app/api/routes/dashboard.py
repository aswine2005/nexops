import asyncio
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.dependencies import require_viewer
from app.core.ids import deployment_key, iso
from app.database.session import get_db
from app.detection.engine import compute_baseline
from app.models import Application, Deployment, Incident, MetricSample, User, as_utc
from app.services.detection import ACTIVE_STATUSES
from app.services.serializers import Serializer
from app.services.telemetry import TelemetryService, sample_to_signals

router = APIRouter(tags=["dashboard"])


def _aggregate(samples: list[MetricSample]) -> dict:
    """Combine the latest sample of each application into fleet-level golden signals."""
    if not samples:
        return {}
    traffic = [sample.request_rate for sample in samples if sample.request_rate is not None]
    weighted = [(sample.error_rate_percent, sample.request_rate or 0) for sample in samples if sample.error_rate_percent is not None]
    total_weight = sum(weight for _, weight in weighted)
    error = (sum(value * weight for value, weight in weighted) / total_weight) if total_weight else (max((value for value, _ in weighted), default=None))
    p99 = [sample.p99_latency_ms for sample in samples if sample.p99_latency_ms is not None]
    cpu = [sample.cpu_percent for sample in samples if sample.cpu_percent is not None]
    memory = [sample.memory_percent for sample in samples if sample.memory_percent is not None]
    healthy = [sample.healthy_replicas for sample in samples if sample.healthy_replicas is not None]
    total = [sample.total_replicas for sample in samples if sample.total_replicas is not None]
    return {
        "request_rate": round(sum(traffic), 3) if traffic else None,
        "error_rate_percent": round(error, 3) if error is not None else None,
        "p99_latency_ms": max(p99) if p99 else None,
        "cpu_percent": round(sum(cpu) / len(cpu), 2) if cpu else None,
        "memory_percent": round(sum(memory) / len(memory), 2) if memory else None,
        "healthy_replicas": sum(healthy) if healthy else None,
        "total_replicas": sum(total) if total else None,
    }


@router.get("/dashboard")
async def read_dashboard(environment: str | None = None, db: Session = Depends(get_db), _: User = Depends(require_viewer)) -> dict:
    serializer = Serializer(db)
    query = select(Application).order_by(Application.name)
    if environment:
        query = query.where(Application.environment == environment.lower())
    apps = db.scalars(query).all()
    app_ids = [app.id for app in apps]
    now = datetime.now(timezone.utc)

    latest_samples = [sample for sample in (serializer.latest_sample(app_id) for app_id in app_ids) if sample is not None]
    hour_ago = db.scalars(select(MetricSample).where(
        MetricSample.application_id.in_(app_ids), MetricSample.observed_at >= now - timedelta(hours=2), MetricSample.observed_at < now - timedelta(hours=1),
    )).all() if app_ids else []

    incidents = db.scalars(select(Incident).where(Incident.application_id.in_(app_ids), Incident.status.in_(ACTIVE_STATUSES)).order_by(Incident.detected_at.desc())).all() if app_ids else []
    deployments = db.scalars(select(Deployment).where(Deployment.application_id.in_(app_ids)).order_by(Deployment.started_at.desc()).limit(6)).all() if app_ids else []
    deployments_today = db.scalar(select(func.count(Deployment.id)).where(
        Deployment.application_id.in_(app_ids), Deployment.finished_at >= now.replace(hour=0, minute=0, second=0, microsecond=0),
    )) if app_ids else 0

    # Focus: the application with an active incident, otherwise the most recently deployed one.
    focus_id = incidents[0].application_id if incidents else (deployments[0].application_id if deployments else (app_ids[0] if app_ids else None))
    series: list[dict] = []
    markers: list[dict] = []
    if focus_id:
        rows = db.scalars(select(MetricSample).where(MetricSample.application_id == focus_id, MetricSample.observed_at >= now - timedelta(minutes=45)).order_by(MetricSample.observed_at)).all()
        series = [{"t": iso(row.observed_at), "error_rate_percent": row.error_rate_percent, "p99_latency_ms": row.p99_latency_ms, "request_rate": row.request_rate} for row in rows]
        markers = [
            {"id": deployment_key(item.id), "version": item.version, "kind": item.kind, "t": iso(item.finished_at)}
            for item in db.scalars(select(Deployment).where(Deployment.application_id == focus_id, Deployment.finished_at >= now - timedelta(minutes=45)))
        ]
    focus_app = next((app for app in apps if app.id == focus_id), None)
    focus_logs: list[dict] = []
    log_source = None
    if focus_app is not None:
        db.expunge(focus_app)
        try:
            lines, log_source = await asyncio.to_thread(TelemetryService().logs, focus_app, now - timedelta(minutes=30), now, 6, None, None)
            focus_logs = [line.as_dict() for line in lines]
        except Exception as error:  # logs are optional on the dashboard
            log_source = f"unavailable: {str(error)[:120]}"

    statuses = [app.status for app in apps]
    overall = "unknown"
    if apps:
        overall = "critical" if "critical" in statuses else "degraded" if "degraded" in statuses or incidents else "healthy" if all(status == "healthy" for status in statuses) else "unknown"
    focus_sample = next((sample for sample in latest_samples if sample.application_id == focus_id), None)
    return {
        "environment": environment,
        "generated_at": iso(now),
        "status": overall,
        "summary": {
            "applications": len(apps),
            "healthy": statuses.count("healthy"),
            "degraded": statuses.count("degraded") + statuses.count("critical"),
            "unknown": statuses.count("unknown"),
            "active_incidents": len(incidents),
            "deployments_today": deployments_today or 0,
        },
        "golden_signals": _aggregate(latest_samples),
        "previous_hour": compute_baseline([sample_to_signals(sample) for sample in hour_ago]),
        "applications": [serializer.application(app) for app in db.scalars(query)],
        "recent_deployments": [serializer.deployment(item) for item in deployments],
        "active_incidents": [serializer.incident(item, include_timeline=False) for item in incidents],
        "focus": {
            "application_id": focus_id,
            "application_name": focus_app.name if focus_app else None,
            "series": series,
            "deployment_markers": markers,
            "runtime": {key: value for key, value in (focus_sample.runtime_state or {}).items() if key != "health_checks"} if focus_sample else None,
            "logs": focus_logs,
            "log_source": log_source,
            "baseline_error_rate": (incidents[0].baseline or {}).get("error_rate_percent") if incidents else None,
        },
        "last_sample_at": iso(max((as_utc(sample.observed_at) for sample in latest_samples), default=None)),
    }
