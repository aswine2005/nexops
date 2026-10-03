import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.router import api_router
from app.bootstrap import bootstrap
from app.config import get_settings
from app.database.session import get_db
from app.services import tasks
from app.services.monitor import MonitorThread

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("nexops")
settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    bootstrap()
    monitor = None
    if settings.run_worker_in_api:
        monitor = MonitorThread(settings)
        monitor.start()
    yield
    if monitor:
        monitor.stop()
    tasks.shutdown()


app = FastAPI(
    title=settings.app_name,
    version=settings.version,
    description=(
        "NexOps connects deployments with production telemetry, detects post-deployment failures, investigates likely root "
        "causes from evidence, runs policy-controlled remediation, verifies recovery, and learns from incidents."
    ),
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-NexOps-Signature"],
)
app.include_router(api_router)


@app.get("/health", tags=["health"])
def health() -> dict:
    """Liveness: the process is serving requests."""
    return {"status": "ok", "service": settings.app_name, "version": settings.version}


@app.get("/ready", tags=["health"])
def ready(db: Session = Depends(get_db)) -> dict:
    """Readiness: PostgreSQL is reachable."""
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database is unavailable.") from error
    return {"status": "ready", "database": "connected", "monitor": "embedded" if settings.run_worker_in_api else "external"}
