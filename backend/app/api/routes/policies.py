from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import require_admin, require_viewer
from app.core.ids import slugify
from app.database.session import get_db
from app.models import Application, Policy, User
from app.schemas.api import PolicyInput
from app.services.common import audit
from app.services.serializers import Serializer

router = APIRouter(prefix="/policies", tags=["policies"])


def _validate(db: Session, request: PolicyInput) -> None:
    if request.application_id and db.get(Application, request.application_id) is None:
        raise HTTPException(status_code=422, detail=f"Application '{request.application_id}' does not exist.")
    if request.action == "database_modification" and request.mode != "forbidden":
        raise HTTPException(status_code=422, detail="NexOps has no database executor; database modification policies must be 'forbidden'.")


@router.get("")
def list_policies(db: Session = Depends(get_db), _: User = Depends(require_viewer)) -> list[dict]:
    return [Serializer.policy(policy) for policy in db.scalars(select(Policy).order_by(Policy.action, Policy.environment, Policy.name))]


@router.post("", status_code=201)
def create_policy(request: PolicyInput, db: Session = Depends(get_db), user: User = Depends(require_admin)) -> dict:
    _validate(db, request)
    policy_id = request.id or slugify(request.name)
    if db.get(Policy, policy_id):
        raise HTTPException(status_code=409, detail=f"Policy '{policy_id}' already exists.")
    policy = Policy(id=policy_id, **request.model_dump(exclude={"id"}))
    db.add(policy)
    audit(db, user.email, "policy.create", "policy", policy_id, request.model_dump(exclude={"id"}))
    db.commit()
    return Serializer.policy(policy)


@router.put("/{policy_id}")
def update_policy(policy_id: str, request: PolicyInput, db: Session = Depends(get_db), user: User = Depends(require_admin)) -> dict:
    policy = db.get(Policy, policy_id)
    if policy is None:
        raise HTTPException(status_code=404, detail="Policy not found.")
    _validate(db, request)
    for key, value in request.model_dump(exclude={"id"}).items():
        setattr(policy, key, value)
    audit(db, user.email, "policy.update", "policy", policy_id, request.model_dump(exclude={"id"}))
    db.commit()
    return Serializer.policy(policy)


@router.delete("/{policy_id}", status_code=204)
def delete_policy(policy_id: str, db: Session = Depends(get_db), user: User = Depends(require_admin)) -> None:
    policy = db.get(Policy, policy_id)
    if policy is None:
        raise HTTPException(status_code=404, detail="Policy not found.")
    db.delete(policy)
    audit(db, user.email, "policy.delete", "policy", policy_id)
    db.commit()
