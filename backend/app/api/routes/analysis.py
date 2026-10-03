import hmac

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database.session import get_db
from app.integrations.registry import get_integrations
from app.models import Application
from app.services import canary

router = APIRouter(prefix="/analysis", tags=["progressive delivery"])


@router.get("/canary")
def canary_analysis(application: str, canary_hash: str, request: Request, stable_hash: str | None = None, window: str = "1m", db: Session = Depends(get_db)) -> dict:
    """Argo Rollouts web metric. Protected by NEXOPS_ANALYSIS_TOKEN when it is set.

    successCondition: result.verdict == "pass"; failureCondition: result.verdict == "fail".
    """
    token = get_settings().analysis_token
    if token:
        header = request.headers.get("Authorization", "")
        if not header.lower().startswith("bearer ") or not hmac.compare_digest(header[7:].strip(), token):
            raise HTTPException(status_code=401, detail="Invalid analysis token.")
    app = db.get(Application, application)
    if app is None:
        raise HTTPException(status_code=404, detail="Application not found.")
    try:
        result = canary.analyze(db, get_integrations(), app, canary_hash, stable_hash or None, window)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    db.commit()
    return result
