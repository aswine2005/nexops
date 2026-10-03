from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, require_admin
from app.core.ids import iso
from app.core.security import create_access_token, hash_password, verify_password
from app.database.session import get_db
from app.models import AuditLog, User, utc_now
from app.schemas.api import LoginRequest, UserCreate, UserUpdate
from app.services.common import audit
from app.services.serializers import user_dict

router = APIRouter(tags=["auth"])


@router.post("/auth/login")
def login(request: LoginRequest, db: Session = Depends(get_db)) -> dict:
    user = db.scalars(select(User).where(User.email == request.email.strip().lower())).first()
    if user is None or not user.is_active or not verify_password(request.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password.")
    token, expires = create_access_token(str(user.id), user.role)
    user.last_login_at = utc_now()
    db.commit()
    return {"token": token, "expires_at": iso(expires), "user": user_dict(user)}


@router.get("/auth/me")
def me(user: User = Depends(get_current_user)) -> dict:
    return user_dict(user)


@router.get("/users")
def list_users(db: Session = Depends(get_db), _: User = Depends(require_admin)) -> list[dict]:
    return [user_dict(user) for user in db.scalars(select(User).order_by(User.created_at))]


@router.post("/users", status_code=201)
def create_user(request: UserCreate, db: Session = Depends(get_db), admin: User = Depends(require_admin)) -> dict:
    email = request.email.strip().lower()
    if db.scalars(select(User).where(User.email == email)).first():
        raise HTTPException(status_code=409, detail="A user with this email already exists.")
    user = User(email=email, display_name=request.display_name, role=request.role, password_hash=hash_password(request.password))
    db.add(user)
    audit(db, admin.email, "user.create", "user", email, {"role": request.role})
    db.commit()
    return user_dict(user)


@router.patch("/users/{user_id}")
def update_user(user_id: UUID, request: UserUpdate, db: Session = Depends(get_db), admin: User = Depends(require_admin)) -> dict:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    if user.id == admin.id and (request.role not in (None, "admin") or request.is_active is False):
        raise HTTPException(status_code=400, detail="You cannot remove your own admin access.")
    if request.display_name is not None:
        user.display_name = request.display_name
    if request.role is not None:
        user.role = request.role
    if request.is_active is not None:
        user.is_active = request.is_active
    if request.password:
        user.password_hash = hash_password(request.password)
    audit(db, admin.email, "user.update", "user", user.email, request.model_dump(exclude={"password"}, exclude_none=True))
    db.commit()
    return user_dict(user)


@router.get("/audit")
def audit_log(limit: int = 100, db: Session = Depends(get_db), _: User = Depends(require_admin)) -> list[dict]:
    rows = db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(min(limit, 500)))
    return [
        {"id": row.id, "actor": row.actor, "action": row.action, "target_type": row.target_type, "target_id": row.target_id, "detail": row.detail, "created_at": iso(row.created_at)}
        for row in rows
    ]
