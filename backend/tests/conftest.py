"""Test configuration: SQLite in-memory database and fake telemetry providers driven by a scenario."""
import json
import os
from dataclasses import dataclass, field

os.environ.update({
    "DATABASE_URL": "sqlite:///:memory:",
    "NEXOPS_ENV": "test",
    "NEXOPS_SECRET_KEY": "test-secret-key-for-signing-tokens-0123456789",
    "NEXOPS_ADMIN_EMAIL": "admin@nexops.test",
    "NEXOPS_ADMIN_PASSWORD": "admin-password-123",
    "NEXOPS_WEBHOOK_SECRET": "webhook-secret",
    "NEXOPS_RUN_WORKER_IN_API": "false",
    "NEXOPS_DETECTION_CONSECUTIVE_BREACHES": "2",
    "NEXOPS_RECOVERY_HEALTHY_SAMPLES": "2",
    "NEXOPS_RECOVERY_GRACE_SECONDS": "0",
    "PROMETHEUS_URL": "http://prometheus.test",
    "DOCKER_ENABLED": "true",
})

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.core.security import sign_payload  # noqa: E402
from app.database.session import get_engine  # noqa: E402
from app.integrations.registry import Integrations, set_integrations  # noqa: E402
from app.integrations.types import LogLine, Replica, RuntimeState  # noqa: E402
from app.models import Base  # noqa: E402
from app.services import tasks  # noqa: E402

tasks.SYNCHRONOUS = True

HEALTHY = {"request_rate": 20.0, "error_rate_percent": 0.2, "p50_latency_ms": 40.0, "p95_latency_ms": 120.0, "p99_latency_ms": 280.0, "cpu_percent": 12.0, "business_success_rate": 99.8}
DEGRADED = {"request_rate": 19.0, "error_rate_percent": 8.7, "p50_latency_ms": 900.0, "p95_latency_ms": 1600.0, "p99_latency_ms": 1900.0, "cpu_percent": 91.0, "business_success_rate": 85.0}


@dataclass
class Scenario:
    healthy: bool = True
    version: str = "v1.0.0"
    redeploys: list[str] = field(default_factory=list)


class FakePrometheus:
    def __init__(self, scenario: Scenario) -> None:
        self.scenario = scenario

    def query(self, promql: str, at=None) -> float | None:
        values = HEALTHY if self.scenario.healthy else DEGRADED
        if "http_request_duration_seconds_bucket" in promql:
            return values["p99_latency_ms"] if "0.99" in promql else values["p95_latency_ms"] if "0.95" in promql else values["p50_latency_ms"]
        if "business_transactions_total" in promql:
            return values["business_success_rate"]
        if "process_cpu_seconds_total" in promql:
            return values["cpu_percent"]
        if 'status=~"5.."' in promql:
            return values["error_rate_percent"]
        if "http_requests_total" in promql:
            return values["request_rate"]
        return None

    def ready(self) -> bool:
        return True


class FakeDocker:
    def __init__(self, scenario: Scenario) -> None:
        self.scenario = scenario

    def state(self, app, with_stats: bool = True) -> RuntimeState:
        healthy = self.scenario.healthy
        image = f"nexops/payment-api:{self.scenario.version}"
        replica = Replica(
            name="payment-api", status="running (healthy)" if healthy else "restarting", ready=healthy,
            restarts=0 if healthy else 6, image=image, version=self.scenario.version,
            reason=None if healthy else "Restarting", memory_percent=40.0,
        )
        return RuntimeState(runtime="docker", target="payment-api", replicas=[replica], desired_replicas=1, image=image, version=self.scenario.version)

    def logs(self, app, since, until=None, limit=200) -> list[LogLine]:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        if self.scenario.healthy:
            return [LogLine(now, "INFO", "payment created", "payment-api", app.name)]
        return [
            LogLine(now, "ERROR", "Database connection failed: connection refused (ECONNREFUSED 10.0.2.25:6543)", "payment-api", app.name, "tr1")
            for _ in range(30)
        ] + [LogLine(now, "WARN", "Readiness check failed: database ping failed", "payment-api", app.name, "tr2")]

    def redeploy(self, app, image: str) -> dict:
        self.scenario.redeploys.append(image)
        self.scenario.version = image.rsplit(":", 1)[-1]
        self.scenario.healthy = True
        return {"image": image, "containers": ["payment-api"]}

    def restart(self, app) -> dict:
        return {"containers": ["payment-api"]}


class FakeIntegrations(Integrations):
    def __init__(self, scenario: Scenario) -> None:
        super().__init__(get_settings())
        self._cache["prometheus"] = FakePrometheus(scenario)
        self._cache["docker"] = FakeDocker(scenario)


@pytest.fixture()
def scenario() -> Scenario:
    return Scenario()


@pytest.fixture()
def client(scenario: Scenario):
    set_integrations(FakeIntegrations(scenario))
    engine = get_engine()
    Base.metadata.create_all(engine)
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
    Base.metadata.drop_all(engine)
    set_integrations(None)


@pytest.fixture()
def admin_headers(client: TestClient) -> dict:
    response = client.post("/api/auth/login", json={"email": "admin@nexops.test", "password": "admin-password-123"})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def signed(payload: dict) -> tuple[bytes, dict]:
    body = json.dumps(payload).encode()
    return body, {"X-NexOps-Signature": sign_payload("webhook-secret", body), "Content-Type": "application/json"}


PAYMENT_API = {
    "id": "payment-api",
    "name": "Payment API",
    "environment": "production",
    "owner": "Payments Platform",
    "repository": "acme/payment-api",
    "runtime": "docker",
    "prometheus_selector": 'job="payment-api"',
    "docker_container": "payment-api",
    "image_repository": "nexops/payment-api",
    "rollback_strategy": "docker",
    "dependencies": ["PostgreSQL"],
}
