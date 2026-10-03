"""Authentication and authorisation dependencies."""
from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.database.session import get_db
from app.models import User

ROLE_LEVEL = {"viewer": 0, "operator": 1, "admin": 2}


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    header = request.headers.get("Authorization", "")
    token = header[7:].strip() if header.lower().startswith("bearer ") else None
    claims = decode_access_token(token) if token else None
    if not claims:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in to continue.", headers={"WWW-Authenticate": "Bearer"})
    try:
        user = db.get(User, UUID(str(claims.get("sub"))))
    except ValueError:
        user = None
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Your session is no longer valid.")
    return user


def require_role(role: str) -> Callable[[User], User]:
    def dependency(user: User = Depends(get_current_user)) -> User:
        if ROLE_LEVEL.get(user.role, -1) < ROLE_LEVEL[role]:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"This action requires the {role} role.")
        return user

    return dependency


require_viewer = require_role("viewer")
require_operator = require_role("operator")
require_admin = require_role("admin")
