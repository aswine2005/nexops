"""First-run bootstrap: default policies and the initial administrator from environment variables."""
from __future__ import annotations

import logging

from sqlalchemy import func, select

from app.config import get_settings
from app.core.security import hash_password
from app.database.session import session_scope
from app.models import User
from app.services.policy import ensure_default_policies

logger = logging.getLogger(__name__)


def bootstrap() -> None:
    settings = get_settings()
    with session_scope() as session:
        created = ensure_default_policies(session)
        if created:
            logger.info("Created %s default remediation policies", created)
        if session.scalar(select(func.count(User.id))) == 0:
            if settings.admin_email and settings.admin_password:
                session.add(User(
                    email=settings.admin_email.lower(),
                    display_name=settings.admin_name or "NexOps Admin",
                    password_hash=hash_password(settings.admin_password),
                    role="admin",
                ))
                logger.info("Created initial administrator %s", settings.admin_email)
            else:
                logger.warning("No users exist. Set NEXOPS_ADMIN_EMAIL and NEXOPS_ADMIN_PASSWORD, or run: python -m app.cli create-user")
