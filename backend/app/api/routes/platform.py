"""Infrastructure inventory, DORA metrics, and integration/settings status."""
import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import require_admin, require_viewer
from app.config import get_settings
from app.database.session import get_db
from app.integrations.registry import get_integrations
from app.models import Application, User
from app.services.dora import DoraService
from app.services.serializers import Serializer

router = APIRouter(tags=["platform"])


@router.get("/dora")
def read_dora(
    months: int = Query(default=6, ge=1, le=24),
    environment: str | None = Query(default="production"),
    application: str | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(require_viewer),
) -> dict:
    return DoraService(db).compute(months=months, environment=(environment or None) if environment != "all" else None, application_id=application)


@router.get("/integrations")
async def integrations_status(_: User = Depends(require_viewer)) -> list[dict]:
    return await asyncio.to_thread(get_integrations().status)


@router.post("/integrations/notifications/test")
async def test_notification(user: User = Depends(require_admin)) -> dict:
    url = get_settings().notify_webhook_url
    if not url:
        raise HTTPException(status_code=409, detail="NEXOPS_NOTIFY_WEBHOOK_URL is not configured.")
    from app.services.notifications import send_test  # noqa: PLC0415

    return await asyncio.to_thread(send_test, url)


@router.get("/settings")
def runtime_settings(_: User = Depends(require_viewer)) -> dict:
    settings = get_settings()
    return {
        "version": settings.version,
        "environment": settings.environment,
        "monitor_interval_seconds": settings.monitor_interval_seconds,
        "detection_consecutive_breaches": settings.detection_consecutive_breaches,
        "correlation_window_minutes": settings.correlation_window_minutes,
        "deployment_verification_minutes": settings.deployment_verification_minutes,
        "recovery_required_healthy_samples": settings.recovery_required_healthy_samples,
        "recovery_timeout_minutes": settings.recovery_timeout_minutes,
        "auto_investigate": settings.auto_investigate,
        "remediation_enabled": settings.remediation_enabled,
        "metric_retention_days": settings.metric_retention_days,
        "prometheus_rate_window": settings.prometheus_rate_window,
        "llm_provider": "groq" if settings.groq_api_key else "local-rules",
        "llm_model": settings.groq_model if settings.groq_api_key else None,
        "webhook_url": f"{settings.public_url.rstrip('/')}/api/webhooks/deployments",
        "jaeger_ui_url": settings.jaeger_ui_url,
        "notifications_enabled": bool(settings.notify_webhook_url),
    }


@router.get("/infrastructure")
async def infrastructure(environment: str | None = None, db: Session = Depends(get_db), _: User = Depends(require_viewer)) -> dict:
    integrations = get_integrations()
    serializer = Serializer(db)
    query = select(Application).order_by(Application.name)
    if environment:
        query = query.where(Application.environment == environment.lower())
    workloads = []
    for app in db.scalars(query):
        latest = serializer.latest_sample(app.id)
        runtime = (latest.runtime_state if latest else None) or {}
        workloads.append({
            "application_id": app.id,
            "application_name": app.name,
            "environment": app.environment,
            "runtime": app.runtime,
            "target": runtime.get("target") or app.docker_container or (f"{app.k8s_namespace}/{app.k8s_deployment}" if app.k8s_deployment else app.ec2_instance_id),
            "image": runtime.get("image"),
            "version": runtime.get("version") or app.current_version,
            "healthy_replicas": runtime.get("healthy_replicas"),
            "total_replicas": runtime.get("total_replicas"),
            "restarts": runtime.get("restarts"),
            "replicas": runtime.get("replicas", []),
            "events": runtime.get("events", []),
            "rollout": runtime.get("rollout"),
            "observed_at": serializer.application(app)["last_checked_at"],
        })

    async def section(name: str, fn) -> tuple[str, dict]:
        try:
            return name, {"available": True, "data": await asyncio.to_thread(fn)}
        except Exception as error:  # each provider reports its own failure
            return name, {"available": False, "error": str(error)[:300]}

    def require(client, name: str):
        if client is None:
            raise RuntimeError(integrations.failure(name) or f"{name} integration is unavailable")
        return client

    def docker_info() -> dict:
        info = require(integrations.docker, "docker").client.info()
        return {
            "server_version": info.get("ServerVersion"), "containers": info.get("Containers"), "running": info.get("ContainersRunning"),
            "cpus": info.get("NCPU"), "memory_gb": round((info.get("MemTotal") or 0) / 1e9, 1), "os": info.get("OperatingSystem"),
        }

    jobs = []
    if integrations.settings.aws_enabled:
        jobs.append(section("ec2", lambda: require(integrations.aws, "aws").ec2_instances()))
        jobs.append(section("ecr", lambda: require(integrations.aws, "aws").ecr_repositories()))
        jobs.append(section("cloudwatch_log_groups", lambda: require(integrations.aws, "aws").log_groups()))
    if integrations.settings.kubernetes_enabled:
        jobs.append(section("kubernetes", lambda: require(integrations.kubernetes, "kubernetes").cluster_summary()))
    if integrations.settings.docker_enabled:
        jobs.append(section("docker", docker_info))
    providers = dict(await asyncio.gather(*jobs)) if jobs else {}
    return {"workloads": workloads, "providers": providers, "aws_region": integrations.settings.aws_region if integrations.settings.aws_enabled else None}
