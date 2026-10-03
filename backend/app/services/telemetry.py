"""Collects a telemetry snapshot for an application from every configured provider."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.config import Settings, get_settings
from app.integrations.health import probe_all
from app.integrations.prometheus import DEFAULT_QUERIES, render_query
from app.integrations.registry import Integrations, get_integrations
from app.integrations.types import DB_ERROR_PATTERN, IntegrationError, LogLine, RuntimeState
from app.models import Application, MetricSample

logger = logging.getLogger(__name__)

METRIC_FIELDS = (
    "request_rate", "error_rate_percent", "p50_latency_ms", "p95_latency_ms", "p99_latency_ms",
    "cpu_percent", "memory_percent", "business_success_rate",
)
LOKI_DB_ERRORS = 'sum(count_over_time($selector |~ "(?i)(econnrefused|connection refused|could not connect|database connection|too many connections|operationalerror)" [$window]))'


@dataclass
class Snapshot:
    application_id: str
    observed_at: datetime
    metrics: dict[str, float | None] = field(default_factory=dict)
    healthy_replicas: int | None = None
    total_replicas: int | None = None
    restarts: int | None = None
    crashlooping: int | None = None
    db_errors: int | None = None
    health_checks: list[dict] = field(default_factory=list)
    runtime: RuntimeState | None = None
    sources: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def failed_health_checks(self) -> int | None:
        if not self.health_checks:
            return None
        return sum(1 for check in self.health_checks if check["status"] != "passing")

    def to_sample(self) -> MetricSample:
        runtime_state = self.runtime.as_dict() if self.runtime else {}
        runtime_state["health_checks"] = self.health_checks
        runtime_state["crashlooping"] = self.crashlooping
        return MetricSample(
            application_id=self.application_id,
            observed_at=self.observed_at,
            request_rate=_round(self.metrics.get("request_rate"), 3),
            error_rate_percent=_round(self.metrics.get("error_rate_percent"), 3),
            p50_latency_ms=_round(self.metrics.get("p50_latency_ms"), 1),
            p95_latency_ms=_round(self.metrics.get("p95_latency_ms"), 1),
            p99_latency_ms=_round(self.metrics.get("p99_latency_ms"), 1),
            cpu_percent=_round(self.metrics.get("cpu_percent"), 2),
            memory_percent=_round(self.metrics.get("memory_percent"), 2),
            business_success_rate=_round(self.metrics.get("business_success_rate"), 3),
            healthy_replicas=self.healthy_replicas,
            total_replicas=self.total_replicas,
            restarts=self.restarts,
            db_errors=self.db_errors,
            failed_health_checks=self.failed_health_checks,
            health_checks_total=len(self.health_checks) or None,
            runtime_state=runtime_state,
            sources=sorted(set(self.sources)),
            errors=self.errors,
        )


def _round(value: float | None, digits: int) -> float | None:
    return None if value is None else round(float(value), digits)


def sample_to_signals(sample: MetricSample) -> dict:
    """Flatten a stored sample into the dictionary the detection engine evaluates."""
    runtime = sample.runtime_state or {}
    return {
        "request_rate": sample.request_rate,
        "error_rate_percent": sample.error_rate_percent,
        "p50_latency_ms": sample.p50_latency_ms,
        "p95_latency_ms": sample.p95_latency_ms,
        "p99_latency_ms": sample.p99_latency_ms,
        "cpu_percent": sample.cpu_percent,
        "memory_percent": sample.memory_percent,
        "business_success_rate": sample.business_success_rate,
        "healthy_replicas": sample.healthy_replicas,
        "total_replicas": sample.total_replicas,
        "restarts": sample.restarts,
        "crashlooping": runtime.get("crashlooping"),
        "db_errors": sample.db_errors,
        "failed_health_checks": sample.failed_health_checks,
        "version": runtime.get("version"),
    }


class TelemetryService:
    def __init__(self, integrations: Integrations | None = None, settings: Settings | None = None) -> None:
        self.integrations = integrations or get_integrations()
        self.settings = settings or get_settings()

    # ------------------------------------------------------------------ snapshot
    def collect(self, app: Application) -> Snapshot:
        snapshot = Snapshot(application_id=app.id, observed_at=datetime.now(timezone.utc))
        # Runtime first: an aborted Argo canary narrows the Prometheus selector to the stable pods.
        self._collect_runtime(app, snapshot)
        saturation = {key: snapshot.metrics[key] for key in ("cpu_percent", "memory_percent") if snapshot.metrics.get(key) is not None}
        self._collect_prometheus(app, snapshot)
        snapshot.metrics.update(saturation)  # runtime saturation is preferred over process-level Prometheus CPU
        self._collect_health(app, snapshot)
        self._collect_db_errors(app, snapshot)
        return snapshot

    def _collect_prometheus(self, app: Application, snapshot: Snapshot) -> None:
        client = self.integrations.prometheus
        if client is None or not app.prometheus_selector:
            return
        queries = {**DEFAULT_QUERIES, **{key: value for key, value in (app.metric_queries or {}).items() if value}}
        window = self.settings.prometheus_rate_window or "2m"
        selector = app.prometheus_selector
        rollout = snapshot.runtime.rollout if snapshot.runtime else None
        if rollout and rollout.get("aborted") and rollout.get("stable_hash"):
            # The aborted canary is gone, but its errors stay in the rate window for a while; judge what serves now.
            selector = f'{selector},rollouts_pod_template_hash="{rollout["stable_hash"]}"'
        answered = False
        for key in METRIC_FIELDS:
            template = queries.get(key)
            if not template:
                continue
            try:
                snapshot.metrics[key] = client.query(render_query(template, selector, window))
                answered = True
            except IntegrationError as error:
                snapshot.errors["prometheus"] = str(error)[:300]
                break
        if answered:
            snapshot.sources.append("prometheus")

    def _collect_runtime(self, app: Application, snapshot: Snapshot) -> None:
        state: RuntimeState | None = None
        try:
            if app.runtime == "docker" and self.integrations.docker:
                state = self.integrations.docker.state(app)
                snapshot.sources.append("docker")
            elif app.runtime == "kubernetes" and self.integrations.kubernetes:
                state = self.integrations.kubernetes.state(app)
                snapshot.sources.append("kubernetes")
        except IntegrationError as error:
            snapshot.errors[app.runtime] = str(error)[:300]
        except Exception as error:  # runtime SDKs raise many exception types
            logger.exception("Runtime state collection failed for %s", app.id)
            snapshot.errors[app.runtime] = f"{error.__class__.__name__}: {error}"[:300]
        if state is not None and state.replicas:
            snapshot.runtime = state
            snapshot.healthy_replicas = state.healthy_replicas
            snapshot.total_replicas = state.total_replicas
            snapshot.restarts = state.restarts
            snapshot.crashlooping = state.crashlooping
            # Runtime saturation is preferred over process-level Prometheus CPU.
            if state.cpu_percent is not None:
                snapshot.metrics["cpu_percent"] = state.cpu_percent
            if state.memory_percent is not None:
                snapshot.metrics["memory_percent"] = state.memory_percent
        elif state is not None:
            snapshot.runtime = state
            snapshot.healthy_replicas = 0
            snapshot.total_replicas = state.desired_replicas or 0
        if app.ec2_instance_id and self.integrations.aws and snapshot.metrics.get("cpu_percent") is None:
            try:
                snapshot.metrics["cpu_percent"] = self.integrations.aws.ec2_cpu(app.ec2_instance_id)
                snapshot.sources.append("cloudwatch")
            except IntegrationError as error:
                snapshot.errors["cloudwatch"] = str(error)[:300]

    def _collect_health(self, app: Application, snapshot: Snapshot) -> None:
        if app.health_checks:
            snapshot.health_checks = probe_all(app.health_checks)
            snapshot.sources.append("http-probe")

    def _collect_db_errors(self, app: Application, snapshot: Snapshot) -> None:
        """Count database connection errors in the rate window from Loki and/or runtime logs.

        Log shippers lag behind new containers by a few seconds, so when both sources exist the higher count wins.
        """
        window = self.settings.prometheus_rate_window or "2m"
        counts: list[int] = []
        loki = self.integrations.loki
        if loki and app.loki_selector:
            try:
                counts.append(int(loki.count(render_query(LOKI_DB_ERRORS, app.loki_selector, window)) or 0))
                snapshot.sources.append("loki")
            except IntegrationError as error:
                snapshot.errors["loki"] = str(error)[:300]
        since = snapshot.observed_at - _window_delta(window)
        try:
            lines = self._runtime_logs(app, since, snapshot.observed_at, limit=1000)
            if lines is not None:
                counts.append(sum(1 for line in lines if DB_ERROR_PATTERN.search(line.message)))
        except IntegrationError as error:
            snapshot.errors["logs"] = str(error)[:300]
        if counts:
            snapshot.db_errors = max(counts)

    # ------------------------------------------------------------------ logs
    def _runtime_logs(self, app: Application, start: datetime, end: datetime, limit: int) -> list[LogLine] | None:
        if app.cloudwatch_log_group and self.integrations.aws:
            return self.integrations.aws.logs(app.cloudwatch_log_group, start, end, app.name, limit=limit)
        if app.runtime == "docker" and self.integrations.docker:
            return self.integrations.docker.logs(app, start, end, limit=limit)
        if app.runtime == "kubernetes" and self.integrations.kubernetes:
            return self.integrations.kubernetes.logs(app, start, limit=limit)
        return None

    def logs(self, app: Application, start: datetime, end: datetime, limit: int = 200, level: str | None = None, query: str | None = None) -> tuple[list[LogLine], str]:
        """Return log lines (newest first) and the provider that served them."""
        loki = self.integrations.loki
        lines: list[LogLine] = []
        source = "none"
        if loki and app.loki_selector:
            logql = app.loki_selector
            if query:
                escaped = query.replace("\\", "\\\\").replace('"', '\\"')
                logql += f' |= "{escaped}"'
            lines = loki.query_range(logql, start, end, limit=limit * (3 if level else 1), service=app.name)
            source = "loki"
        if not lines:
            fetched = self._runtime_logs(app, start, end, limit=max(limit * 3, 500))
            if fetched is None:
                return [], source
            lines = fetched
            source = "cloudwatch" if app.cloudwatch_log_group and self.integrations.aws else app.runtime
            if query:
                needle = query.casefold()
                lines = [line for line in lines if needle in f"{line.message} {line.source} {line.trace_id or ''}".casefold()]
        if level:
            wanted = {item.strip().upper() for item in level.split(",") if item.strip()}
            lines = [line for line in lines if line.level in wanted]
        return lines[:limit], source

    def runtime_state(self, app: Application) -> RuntimeState | None:
        if app.runtime == "docker" and self.integrations.docker:
            return self.integrations.docker.state(app, with_stats=False)
        if app.runtime == "kubernetes" and self.integrations.kubernetes:
            return self.integrations.kubernetes.state(app)
        return None


def _window_delta(window: str) -> timedelta:
    unit = window[-1]
    try:
        value = int(window[:-1])
    except ValueError:
        return timedelta(minutes=2)
    return {"s": timedelta(seconds=value), "m": timedelta(minutes=value), "h": timedelta(hours=value)}.get(unit, timedelta(minutes=2))
