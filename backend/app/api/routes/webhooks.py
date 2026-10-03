"""CI/CD endpoints authenticated with an HMAC-SHA256 signature (X-NexOps-Signature: sha256=<hex>)."""
import json

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.ids import deployment_key
from app.core.security import verify_signature
from app.database.session import get_db
from app.models import Application
from app.schemas.api import DeployGateRequest, DeploymentWebhook
from app.services.deployments import DeploymentService
from app.services.workflow import IncidentWorkflow

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


async def _verified_body(request: Request) -> bytes:
    secret = get_settings().webhook_secret
    if not secret:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="NEXOPS_WEBHOOK_SECRET is not configured.")
    body = await request.body()
    if not verify_signature(secret, body, request.headers.get("X-NexOps-Signature")):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook signature.")
    return body


def _parse(model, body: bytes):
    try:
        return model.model_validate(json.loads(body or b"{}"))
    except (ValueError, ValidationError) as error:
        raise HTTPException(status_code=422, detail=str(error)[:1000]) from error


@router.post("/deployments", status_code=202)
async def deployment_event(request: Request, db: Session = Depends(get_db)) -> dict:
    payload: DeploymentWebhook = _parse(DeploymentWebhook, await _verified_body(request))
    try:
        deployment, created = DeploymentService(db).ingest(payload.model_dump())
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    if payload.event == "finished":
        IncidentWorkflow(db).on_deployment_recorded(deployment)
    db.commit()
    return {
        "id": deployment_key(deployment.id),
        "created": created,
        "status": deployment.status,
        "risk_level": deployment.risk_level,
        "risk_score": deployment.risk_score,
        "previous_version": deployment.previous_version,
        "url": f"{get_settings().public_url.rstrip('/')}/deployments/{deployment_key(deployment.id)}",
    }


@router.post("/deploy-gate")
async def deploy_gate(request: Request, db: Session = Depends(get_db)) -> dict:
    """CI asks before deploying. Rollbacks are always allowed; releases are blocked while deployments are frozen."""
    payload: DeployGateRequest = _parse(DeployGateRequest, await _verified_body(request))
    app = db.get(Application, payload.application)
    if app is None:
        raise HTTPException(status_code=404, detail=f"Application '{payload.application}' is not registered in NexOps.")
    if app.deploy_frozen and payload.kind != "rollback":
        return {"allowed": False, "reason": app.deploy_freeze_reason or "Deployments are paused in NexOps."}
    return {"allowed": True, "reason": None, "current_version": app.current_version}
