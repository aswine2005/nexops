from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import require_viewer
from app.database.session import get_db
from app.models import AgentRun, User
from app.services.agents import agent_catalogue, serialize_run

router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("")
def list_agents(db: Session = Depends(get_db), _: User = Depends(require_viewer)) -> list[dict]:
    return agent_catalogue(db)


@router.get("/runs")
def list_runs(agent: str | None = None, limit: int = Query(default=50, ge=1, le=300), db: Session = Depends(get_db), _: User = Depends(require_viewer)) -> list[dict]:
    query = select(AgentRun).order_by(AgentRun.started_at.desc()).limit(limit)
    if agent:
        query = query.where(AgentRun.agent == agent)
    return [serialize_run(run) for run in db.scalars(query)]
