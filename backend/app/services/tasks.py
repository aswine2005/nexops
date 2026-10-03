"""Single-threaded background task runner so workflow actions (investigations, remediations) run one at a time."""
from __future__ import annotations

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable

from app.database.session import session_scope
from app.services.workflow import IncidentWorkflow

logger = logging.getLogger(__name__)
_executor: ThreadPoolExecutor | None = None
_lock = threading.Lock()
SYNCHRONOUS = False  # tests run workflow tasks inline


def _pool() -> ThreadPoolExecutor:
    global _executor
    with _lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="nexops-workflow")
        return _executor


def submit(fn: Callable[..., Any], *args: Any) -> Future:
    def run() -> Any:
        try:
            return fn(*args)
        except Exception:
            logger.exception("Background workflow task %s failed", getattr(fn, "__name__", fn))
            raise

    if SYNCHRONOUS:
        future: Future = Future()
        try:
            future.set_result(run())
        except Exception as error:
            future.set_exception(error)
        return future
    return _pool().submit(run)


def investigate_and_remediate(incident_id: int, actor: str = "investigation-agent") -> None:
    """Automatic path after detection: investigate, then execute an action the policy allows automatically."""
    with session_scope() as session:
        IncidentWorkflow(session).investigate(incident_id, actor)
    execute_approved(incident_id, actor="policy-engine")


def execute_approved(incident_id: int, actor: str) -> None:
    from sqlalchemy import select  # noqa: PLC0415

    from app.models import RemediationAction  # noqa: PLC0415

    with session_scope() as session:
        remediation_id = session.scalars(select(RemediationAction.id).where(
            RemediationAction.incident_id == incident_id, RemediationAction.status == "approved",
        ).order_by(RemediationAction.id.desc()).limit(1)).first()
    if remediation_id is None:
        return
    with session_scope() as session:
        IncidentWorkflow(session).execute(remediation_id, actor)


def shutdown() -> None:
    global _executor
    with _lock:
        if _executor is not None:
            _executor.shutdown(wait=False, cancel_futures=True)
            _executor = None
