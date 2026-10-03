"""Background monitor: collect telemetry → store → detect → verify recovery → update deployment verification."""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from sqlalchemy import delete, select, text
from sqlalchemy.engine import Connection

from app.config import Settings, get_settings
from app.database.session import get_engine, session_scope
from app.models import Application, MetricSample, utc_now
from app.services import tasks
from app.services.deployments import DeploymentService
from app.services.detection import DetectionService
from app.services.telemetry import Snapshot, TelemetryService
from app.services.workflow import IncidentWorkflow

logger = logging.getLogger(__name__)
_LEADER_LOCK_KEY = 72_746_101  # arbitrary constant shared by all NexOps workers


class LeaderLock:
    """PostgreSQL advisory lock so only one API/worker replica runs the monitor at a time."""

    def __init__(self) -> None:
        self._connection: Connection | None = None

    def acquire(self) -> bool:
        engine = get_engine()
        if engine.dialect.name != "postgresql":
            return True
        try:
            if self._connection is not None:
                self._connection.execute(text("SELECT 1"))
                return True
            connection = engine.connect()
            acquired = bool(connection.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": _LEADER_LOCK_KEY}).scalar())
            if acquired:
                connection.commit()
                self._connection = connection
                logger.info("Monitor leadership acquired")
                return True
            connection.close()
            return False
        except Exception:
            logger.exception("Leader lock check failed")
            self.release()
            return False

    def release(self) -> None:
        if self._connection is not None:
            try:
                self._connection.close()
            except Exception:
                pass
            self._connection = None


class Monitor:
    def __init__(self, telemetry: TelemetryService | None = None, settings: Settings | None = None, background: bool = True) -> None:
        self.settings = settings or get_settings()
        self.telemetry = telemetry or TelemetryService(settings=self.settings)
        self.background = background
        self._last_prune = 0.0

    def run_cycle(self) -> dict:
        with session_scope() as session:
            apps = session.scalars(select(Application).where(Application.monitoring_enabled.is_(True))).all()
            session.expunge_all()
        if not apps:
            return {"applications": 0, "incidents_created": []}

        with ThreadPoolExecutor(max_workers=min(8, len(apps)), thread_name_prefix="nexops-collect") as pool:
            snapshots = list(pool.map(self._collect, apps))

        created: list[int] = []
        for snapshot in snapshots:
            if snapshot is None:
                continue
            try:
                with session_scope() as session:
                    app = session.get(Application, snapshot.application_id)
                    if app is None:
                        continue
                    sample = snapshot.to_sample()
                    session.add(sample)
                    session.flush()
                    result = DetectionService(session, self.settings).process(app, sample)
                    IncidentWorkflow(session, settings=self.settings, telemetry=self.telemetry).verify_cycle(app.id, sample)
                    DeploymentService(session, settings=self.settings).update_verification(app, result.app_status)
                    if result.created and result.incident is not None:
                        created.append(result.incident.id)
                        logger.warning("Incident INC-%s opened for %s: %s", result.incident.id, app.id, result.incident.title)
            except Exception:
                logger.exception("Monitor processing failed for %s", snapshot.application_id)

        for incident_id in created:
            if self.settings.auto_investigate:
                if self.background:
                    tasks.submit(tasks.investigate_and_remediate, incident_id)
                else:
                    tasks.investigate_and_remediate(incident_id)
        self._prune()
        return {"applications": len(apps), "incidents_created": created}

    def _collect(self, app: Application) -> Snapshot | None:
        try:
            return self.telemetry.collect(app)
        except Exception:
            logger.exception("Telemetry collection failed for %s", app.id)
            return None

    def _prune(self) -> None:
        if time.time() - self._last_prune < 3600:
            return
        self._last_prune = time.time()
        cutoff = utc_now() - timedelta(days=self.settings.metric_retention_days)
        with session_scope() as session:
            session.execute(delete(MetricSample).where(MetricSample.observed_at < cutoff))


class MonitorThread(threading.Thread):
    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__(name="nexops-monitor", daemon=True)
        self.settings = settings or get_settings()
        self.stop_event = threading.Event()
        self.monitor = Monitor(settings=self.settings)
        self.leader = LeaderLock()

    def run(self) -> None:
        interval = max(5, self.settings.monitor_interval_seconds)
        logger.info("NexOps monitor started (interval %ss)", interval)
        while not self.stop_event.is_set():
            started = time.time()
            if self.leader.acquire():
                try:
                    self.monitor.run_cycle()
                except Exception:
                    logger.exception("Monitor cycle failed")
            self.stop_event.wait(max(1.0, interval - (time.time() - started)))
        self.leader.release()

    def stop(self) -> None:
        self.stop_event.set()
