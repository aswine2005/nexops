from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import require_admin, require_operator, require_viewer
from app.core.ids import incident_key, parse_key
from app.database.session import get_db
from app.models import Incident, Investigation, Postmortem, RecoveryVerification, RemediationAction, User
from app.schemas.api import ReasonRequest, RemediationRequest
from app.services import tasks
from app.services.detection import ACTIVE_STATUSES
from app.services.memory import similar
from app.services.serializers import Serializer
from app.services.workflow import IncidentWorkflow, WorkflowError

router = APIRouter(tags=["incidents"])


def _incident(db: Session, incident_id: str) -> Incident:
    key = parse_key(incident_id)
    incident = db.get(Incident, key) if key else None
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found.")
    return incident


def workflow_view(db: Session, incident: Incident) -> dict:
    serializer = Serializer(db)
    investigation = db.scalars(select(Investigation).where(Investigation.incident_id == incident.id).order_by(Investigation.id.desc()).limit(1)).first()
    remediations = db.scalars(select(RemediationAction).where(RemediationAction.incident_id == incident.id).order_by(RemediationAction.id.desc())).all()
    verification = db.scalars(select(RecoveryVerification).where(RecoveryVerification.incident_id == incident.id).order_by(RecoveryVerification.id.desc()).limit(1)).first()
    postmortem = db.scalars(select(Postmortem).where(Postmortem.incident_id == incident.id)).first()
    return {
        "incident": serializer.incident(incident),
        "investigation": serializer.investigation(investigation),
        "remediations": [serializer.remediation(item) for item in remediations],
        "verification": serializer.verification(verification),
        "postmortem": serializer.postmortem(postmortem),
        "similar": [
            {"id": incident_key(memory.incident_id) if memory.incident_id else None, "title": memory.title, "root_cause": memory.root_cause,
             "action": memory.action, "failed_version": memory.failed_version, "score": score, "recovery_successful": memory.recovery_successful}
            for memory, score in similar(db, incident)
        ],
    }


def _workflow_call(db: Session, fn, *args):
    try:
        result = fn(*args)
        db.commit()
        return result
    except LookupError as error:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except WorkflowError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/incidents")
def list_incidents(
    status: str = Query(default="all", pattern="^(all|active|resolved)$"),
    application: str | None = None,
    environment: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    _: User = Depends(require_viewer),
) -> list[dict]:
    query = select(Incident).order_by(Incident.detected_at.desc()).limit(limit)
    if status == "active":
        query = query.where(Incident.status.in_(ACTIVE_STATUSES))
    elif status == "resolved":
        query = query.where(Incident.status == "resolved")
    if application:
        query = query.where(Incident.application_id == application)
    serializer = Serializer(db)
    rows = [serializer.incident(item, include_timeline=False) for item in db.scalars(query)]
    if environment:
        rows = [row for row in rows if row["environment"] == environment.lower()]
    return rows


@router.get("/incidents/{incident_id}")
def read_incident(incident_id: str, db: Session = Depends(get_db), _: User = Depends(require_viewer)) -> dict:
    return Serializer(db).incident(_incident(db, incident_id))


@router.get("/incidents/{incident_id}/workflow")
def read_workflow(incident_id: str, db: Session = Depends(get_db), _: User = Depends(require_viewer)) -> dict:
    return workflow_view(db, _incident(db, incident_id))


@router.post("/incidents/{incident_id}/investigate")
def investigate(incident_id: str, db: Session = Depends(get_db), user: User = Depends(require_operator)) -> dict:
    incident = _incident(db, incident_id)
    _workflow_call(db, IncidentWorkflow(db).investigate, incident.id, user.display_name)
    tasks.submit(tasks.execute_approved, incident.id, "policy-engine")
    db.expire_all()
    return workflow_view(db, incident)


@router.post("/incidents/{incident_id}/remediations")
def propose_remediation(incident_id: str, request: RemediationRequest, db: Session = Depends(get_db), user: User = Depends(require_operator)) -> dict:
    incident = _incident(db, incident_id)
    workflow = IncidentWorkflow(db)
    _workflow_call(db, lambda: workflow.propose(incident, request.action, reason=request.reason or f"Requested by {user.display_name}", confidence=incident.confidence, actor=user.display_name, target_flag=request.flag))
    tasks.submit(tasks.execute_approved, incident.id, "policy-engine")
    db.expire_all()
    return workflow_view(db, incident)


@router.post("/remediations/{remediation_id}/approve")
def approve(remediation_id: int, db: Session = Depends(get_db), user: User = Depends(require_operator)) -> dict:
    remediation = _workflow_call(db, IncidentWorkflow(db).approve, remediation_id, user)
    tasks.submit(tasks.execute_approved, remediation.incident_id, user.display_name)
    db.expire_all()
    incident = db.get(Incident, remediation.incident_id)
    return workflow_view(db, incident)


@router.post("/remediations/{remediation_id}/reject")
def reject(remediation_id: int, request: ReasonRequest, db: Session = Depends(get_db), user: User = Depends(require_operator)) -> dict:
    remediation = _workflow_call(db, IncidentWorkflow(db).reject, remediation_id, user, request.reason)
    return workflow_view(db, db.get(Incident, remediation.incident_id))


@router.post("/incidents/{incident_id}/verify")
def verify(incident_id: str, db: Session = Depends(get_db), user: User = Depends(require_operator)) -> dict:
    incident = _incident(db, incident_id)
    _workflow_call(db, IncidentWorkflow(db).request_verification, incident.id, user)
    return workflow_view(db, incident)


@router.post("/incidents/{incident_id}/dismiss")
def dismiss(incident_id: str, request: ReasonRequest, db: Session = Depends(get_db), user: User = Depends(require_admin)) -> dict:
    incident = _incident(db, incident_id)
    _workflow_call(db, IncidentWorkflow(db).dismiss, incident.id, user, request.reason)
    return workflow_view(db, incident)
