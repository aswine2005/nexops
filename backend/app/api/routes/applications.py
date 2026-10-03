import asyncio

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import require_admin, require_operator, require_viewer
from app.core.ids import slugify
from app.database.session import get_db
from app.models import Application, Deployment, Incident, User
from app.schemas.api import ApplicationInput, FreezeRequest
from app.services.common import audit
from app.services.serializers import Serializer, sample_dict
from app.services.telemetry import TelemetryService

router = APIRouter(prefix="/applications", tags=["applications"])


def _get(db: Session, application_id: str) -> Application:
    app = db.get(Application, application_id)
    if app is None:
        raise HTTPException(status_code=404, detail="Application not found.")
    return app


def _apply(app: Application, request: ApplicationInput) -> None:
    data = request.model_dump(exclude={"id"})
    if data.get("current_version") is None:
        data.pop("current_version")  # runtime state comes from deployments; a config edit must not reset it
    data["health_checks"] = [{"name": check.name, "url": str(check.url)} for check in request.health_checks]
    for key, value in data.items():
        setattr(app, key, value)


@router.get("")
def list_applications(environment: str | None = None, db: Session = Depends(get_db), _: User = Depends(require_viewer)) -> list[dict]:
    query = select(Application).order_by(Application.name)
    if environment:
        query = query.where(Application.environment == environment.lower())
    serializer = Serializer(db)
    return [serializer.application(app) for app in db.scalars(query)]


@router.post("", status_code=201)
def create_application(request: ApplicationInput, db: Session = Depends(get_db), user: User = Depends(require_operator)) -> dict:
    application_id = request.id or slugify(request.name)
    if db.get(Application, application_id):
        raise HTTPException(status_code=409, detail=f"Application '{application_id}' already exists.")
    app = Application(id=application_id)
    _apply(app, request)
    db.add(app)
    audit(db, user.email, "application.create", "application", application_id)
    db.commit()
    return Serializer(db).application(app, detail=True)


@router.get("/{application_id}")
def read_application(application_id: str, db: Session = Depends(get_db), _: User = Depends(require_viewer)) -> dict:
    app = _get(db, application_id)
    serializer = Serializer(db)
    data = serializer.application(app, detail=True)
    data["deployments"] = [serializer.deployment(item) for item in db.scalars(
        select(Deployment).where(Deployment.application_id == app.id).order_by(Deployment.started_at.desc()).limit(15)
    )]
    data["incidents"] = [serializer.incident(item, include_timeline=False) for item in db.scalars(
        select(Incident).where(Incident.application_id == app.id).order_by(Incident.detected_at.desc()).limit(15)
    )]
    return data


@router.put("/{application_id}")
def update_application(application_id: str, request: ApplicationInput, db: Session = Depends(get_db), user: User = Depends(require_operator)) -> dict:
    app = _get(db, application_id)
    _apply(app, request)
    audit(db, user.email, "application.update", "application", application_id)
    db.commit()
    return Serializer(db).application(app, detail=True)


@router.delete("/{application_id}", status_code=204)
def delete_application(application_id: str, db: Session = Depends(get_db), user: User = Depends(require_admin)) -> None:
    app = _get(db, application_id)
    db.delete(app)
    audit(db, user.email, "application.delete", "application", application_id)
    db.commit()


@router.post("/{application_id}/check")
async def check_application(application_id: str, db: Session = Depends(get_db), _: User = Depends(require_operator)) -> dict:
    """Run a live telemetry collection (without storing it) to validate the application's configuration."""
    app = _get(db, application_id)
    db.expunge(app)
    snapshot = await asyncio.to_thread(TelemetryService().collect, app)
    sample = snapshot.to_sample()
    return {
        "sample": sample_dict(sample),
        "runtime": snapshot.runtime.as_dict() if snapshot.runtime else None,
        "health_checks": snapshot.health_checks,
        "sources": sorted(set(snapshot.sources)),
        "errors": snapshot.errors,
    }


@router.post("/{application_id}/freeze")
def freeze_deployments(application_id: str, request: FreezeRequest, db: Session = Depends(get_db), user: User = Depends(require_operator)) -> dict:
    app = _get(db, application_id)
    app.deploy_frozen = True
    app.deploy_freeze_reason = request.reason or f"Paused by {user.display_name}"
    audit(db, user.email, "application.freeze", "application", application_id, {"reason": app.deploy_freeze_reason})
    db.commit()
    return Serializer(db).application(app)


@router.post("/{application_id}/unfreeze")
def unfreeze_deployments(application_id: str, db: Session = Depends(get_db), user: User = Depends(require_operator)) -> dict:
    app = _get(db, application_id)
    app.deploy_frozen = False
    app.deploy_freeze_reason = None
    audit(db, user.email, "application.unfreeze", "application", application_id)
    db.commit()
    return Serializer(db).application(app)
