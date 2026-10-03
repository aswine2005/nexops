import hmac

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import require_admin, require_operator, require_viewer
from app.config import get_settings
from app.database.session import get_db
from app.models import Application, FeatureFlag, User
from app.schemas.api import FlagInput, FlagUpdate
from app.services import flags as flag_service
from app.services.common import audit

router = APIRouter(tags=["feature flags"])


@router.get("/flags/evaluate")
def evaluate_flags(application: str, request: Request, db: Session = Depends(get_db)) -> dict:
    """Polled by applications. Protected by NEXOPS_FLAGS_TOKEN when it is set."""
    token = get_settings().flags_token
    if token:
        header = request.headers.get("Authorization", "")
        if not header.lower().startswith("bearer ") or not hmac.compare_digest(header[7:].strip(), token):
            raise HTTPException(status_code=401, detail="Invalid flags token.")
    if db.get(Application, application) is None:
        raise HTTPException(status_code=404, detail="Application not found.")
    return {"application": application, "flags": flag_service.evaluate(db, application), "ttl_seconds": 10}


@router.get("/flags")
def list_flags(application: str | None = None, db: Session = Depends(get_db), _: User = Depends(require_viewer)) -> list[dict]:
    query = select(FeatureFlag).order_by(FeatureFlag.application_id, FeatureFlag.key)
    if application:
        query = query.where(FeatureFlag.application_id == application)
    return [flag_service.serialize(flag) for flag in db.scalars(query)]


@router.post("/applications/{application_id}/flags", status_code=201)
def create_flag(application_id: str, request: FlagInput, db: Session = Depends(get_db), user: User = Depends(require_operator)) -> dict:
    if db.get(Application, application_id) is None:
        raise HTTPException(status_code=404, detail="Application not found.")
    if flag_service.find(db, application_id, request.key):
        raise HTTPException(status_code=409, detail=f"Flag '{request.key}' already exists for {application_id}.")
    flag = FeatureFlag(application_id=application_id, key=request.key, description=request.description, enabled=False, rollout_percent=request.rollout_percent, updated_by=user.display_name)
    db.add(flag)
    db.flush()
    audit(db, user.email, "flag.create", "feature_flag", f"{application_id}/{request.key}")
    if request.enabled:
        flag_service.set_flag(db, flag, enabled=True, actor=user.display_name, reason="Created enabled")
    db.commit()
    db.refresh(flag)
    return flag_service.serialize(flag)


@router.patch("/flags/{flag_id}")
def update_flag(flag_id: int, request: FlagUpdate, db: Session = Depends(get_db), user: User = Depends(require_operator)) -> dict:
    flag = db.get(FeatureFlag, flag_id)
    if flag is None:
        raise HTTPException(status_code=404, detail="Flag not found.")
    if request.description is not None:
        flag.description = request.description
    flag_service.set_flag(db, flag, enabled=request.enabled, rollout_percent=request.rollout_percent, actor=user.display_name, reason=request.reason)
    db.commit()
    db.refresh(flag)
    return flag_service.serialize(flag)


@router.delete("/flags/{flag_id}", status_code=204)
def delete_flag(flag_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)) -> None:
    flag = db.get(FeatureFlag, flag_id)
    if flag is None:
        raise HTTPException(status_code=404, detail="Flag not found.")
    audit(db, user.email, "flag.delete", "feature_flag", f"{flag.application_id}/{flag.key}")
    db.delete(flag)
    db.commit()
