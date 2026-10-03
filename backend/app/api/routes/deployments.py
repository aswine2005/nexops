from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import require_viewer
from app.core.ids import iso, parse_key
from app.database.session import get_db
from app.models import AgentRun, Deployment, User
from app.services.serializers import Serializer

router = APIRouter(prefix="/deployments", tags=["deployments"])


@router.get("")
def list_deployments(
    application: str | None = None,
    environment: str | None = None,
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    _: User = Depends(require_viewer),
) -> list[dict]:
    query = select(Deployment).order_by(Deployment.started_at.desc()).limit(limit)
    if application:
        query = query.where(Deployment.application_id == application)
    if environment:
        query = query.where(Deployment.environment == environment.lower())
    if status:
        query = query.where(Deployment.status == status)
    serializer = Serializer(db)
    return [serializer.deployment(item) for item in db.scalars(query)]


@router.get("/{deployment_id}")
def read_deployment(deployment_id: str, db: Session = Depends(get_db), _: User = Depends(require_viewer)) -> dict:
    key = parse_key(deployment_id)
    deployment = db.get(Deployment, key) if key else None
    if deployment is None:
        raise HTTPException(status_code=404, detail="Deployment not found.")
    data = Serializer(db).deployment(deployment, include_changes=True)
    risk_run = db.scalars(
        select(AgentRun).where(AgentRun.deployment_id == deployment.id, AgentRun.agent == "deployment_risk").order_by(AgentRun.id.desc()).limit(1)
    ).first()
    data["risk_assessment"] = {"summary": risk_run.summary, "output": risk_run.output} if risk_run else None
    canary_runs = db.scalars(
        select(AgentRun).where(AgentRun.deployment_id == deployment.id, AgentRun.agent == "canary_analysis").order_by(AgentRun.id).limit(50)
    ).all()
    data["canary_analyses"] = [{"summary": run.summary, "at": iso(run.started_at), **{key: run.output.get(key) for key in ("verdict", "reasons", "canary", "stable", "limits", "canary_weight", "canary_version", "window")}} for run in canary_runs]
    return data
