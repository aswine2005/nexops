"""Trace evidence from Jaeger and webhook notifications across the incident lifecycle."""
import time
from datetime import datetime, timezone

from app.config import get_settings
from app.integrations.jaeger import JaegerClient
from app.integrations.registry import get_integrations
from app.services import notifications
from tests.conftest import PAYMENT_API
from tests.test_end_to_end import deploy, run_cycles

NOW_US = int(datetime.now(timezone.utc).timestamp() * 1_000_000)


def jaeger_trace(trace_id: str, failing: bool) -> dict:
    root = {"traceID": trace_id, "spanID": "a", "operationName": "POST /payments", "references": [], "startTime": NOW_US, "duration": 1_950_000,
            "tags": [{"key": "http.status_code", "value": 503}] + ([{"key": "otel.status_code", "value": "ERROR"}] if failing else [])}
    connect = {"traceID": trace_id, "spanID": "b", "operationName": "db.connect", "references": [{"refType": "CHILD_OF", "spanID": "a"}],
               "startTime": NOW_US + 10, "duration": 600_000,
               "tags": [{"key": "error", "value": True}, {"key": "otel.status_description", "value": 'connection to server at "10.0.0.5", port 6543 failed: Connection refused'}]}
    return {"traceID": trace_id, "spans": [root, connect] if failing else [root], "processes": {}}


class FakeJaeger:
    def traces(self, service, start, end, limit=20, tags=None, min_duration=None):
        return [JaegerClient.summarize(jaeger_trace(f"t{index}", failing=True)) for index in range(3)]

    def ready(self):
        return True


def test_summarize_finds_failing_span_and_slowest_operations():
    summary = JaegerClient.summarize(jaeger_trace("abc", failing=True))
    assert summary["root"] == "POST /payments" and summary["error"] is True
    assert summary["errors"][0]["operation"] == "db.connect" and "6543" in summary["errors"][0]["message"]
    assert summary["slowest"][0]["operation"] == "POST /payments" and summary["duration_ms"] == 1950.0


def test_trace_evidence_and_notifications(client, admin_headers, scenario, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "jaeger_url", "http://jaeger.test")
    monkeypatch.setattr(settings, "notify_webhook_url", "https://hooks.example.test/T000/B000/xyz")
    get_integrations()._cache["jaeger"] = FakeJaeger()
    delivered: list[dict] = []
    monkeypatch.setattr(notifications, "_deliver", lambda url, items: delivered.extend(items))

    assert client.post("/api/applications", json={**PAYMENT_API, "tracing_service": "payment-api"}, headers=admin_headers).status_code == 201
    deploy(client, "v1.0.0")
    run_cycles(2)
    deploy(client, "v1.0.1", files=[{"filename": "app/config/database.py", "additions": 1, "deletions": 1}], minutes_ago=5)
    scenario.healthy, scenario.version = False, "v1.0.1"
    created = [item for result in run_cycles(2) for item in result["incidents_created"]]
    assert created
    workflow = client.get(f"/api/incidents/INC-{created[0]}/workflow", headers=admin_headers).json()
    traces = [item for item in workflow["investigation"]["evidence"] if item["kind"] == "TRACE"]
    assert traces and traces[0]["title"].startswith("3× POST /payments failed in db.connect")
    assert traces[0]["data"]["trace_id"] == "t0"

    deadline = time.time() + 3
    while time.time() < deadline and {item["kind"] for item in delivered} < {"incident.opened", "approval.required"}:
        time.sleep(0.05)
    kinds = [item["kind"] for item in delivered]
    assert "incident.opened" in kinds and "approval.required" in kinds
    opened = next(item for item in delivered if item["kind"] == "incident.opened")
    assert opened["link"].endswith(f"/incidents/INC-{created[0]}")
    assert notifications.render(opened)["text"].startswith("🚨 *INC-")


def test_notifications_are_dropped_on_rollback(monkeypatch):
    from sqlalchemy.orm import Session

    from app.database.session import get_engine

    monkeypatch.setattr(get_settings(), "notify_webhook_url", "https://hooks.example.test/x")
    delivered: list[dict] = []
    monkeypatch.setattr(notifications, "_deliver", lambda url, items: delivered.extend(items))
    with Session(get_engine()) as session:
        notifications.queue(session, "incident.opened", "t", "d")
        session.rollback()
        session.commit()
    time.sleep(0.1)
    assert delivered == []


def test_summarize_prefers_informative_failures():
    root_only = {"traceID": "r", "spans": [{"spanID": "a", "operationName": "db.connect", "references": [], "startTime": NOW_US, "duration": 12_000,
                                              "tags": [{"key": "error", "value": True}, {"key": "otel.status_description", "value": "Connection refused"}]}]}
    assert JaegerClient.summarize(root_only)["errors"] == [{"operation": "db.connect", "message": "Connection refused"}]
    trace = jaeger_trace("x", failing=True)
    trace["spans"].insert(1, {"spanID": "s", "operationName": "POST /payments http send", "references": [{"spanID": "a"}], "startTime": NOW_US, "duration": 1,
                              "tags": [{"key": "otel.status_code", "value": "ERROR"}]})
    assert JaegerClient.summarize(trace)["errors"][0]["operation"] == "db.connect"


class FlaggedJaeger:
    """Failing traces whose failing span belongs to the fraud-check feature."""

    def traces(self, service, start, end, limit=20, tags=None, min_duration=None):
        trace = {"traceID": "f1", "processes": {}, "spans": [
            {"spanID": "a", "operationName": "POST /payments", "references": [], "startTime": NOW_US, "duration": 1_600_000,
             "tags": [{"key": "otel.status_code", "value": "ERROR"}]},
            {"spanID": "b", "operationName": "fraud.score", "references": [{"spanID": "a"}], "startTime": NOW_US + 5, "duration": 1_500_000,
             "tags": [{"key": "error", "value": True}, {"key": "feature_flag.key", "value": "fraud-check"},
                      {"key": "otel.status_description", "value": "ConnectTimeout: timed out connecting to fraud scoring"}]},
        ]}
        return [JaegerClient.summarize(trace)] * 4

    def ready(self):
        return True


def test_enabled_flag_failure_is_disabled_automatically_and_verified(client, admin_headers, scenario, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "jaeger_url", "http://jaeger.test")
    monkeypatch.setattr(settings, "notify_webhook_url", "https://hooks.example.test/x")
    get_integrations()._cache["jaeger"] = FlaggedJaeger()
    delivered: list[dict] = []
    monkeypatch.setattr(notifications, "_deliver", lambda url, items: delivered.extend(items))

    assert client.post("/api/applications", json={**PAYMENT_API, "tracing_service": "payment-api"}, headers=admin_headers).status_code == 201
    deploy(client, "v1.0.0", minutes_ago=40)
    run_cycles(2)
    flag = client.post("/api/applications/payment-api/flags", json={"key": "fraud-check", "description": "Score payments for fraud"}, headers=admin_headers).json()
    assert flag["enabled"] is False
    assert client.get("/api/flags/evaluate?application=payment-api").json()["flags"] == {"fraud-check": {"enabled": False, "rollout_percent": 100}}
    client.patch(f"/api/flags/{flag['id']}", json={"enabled": True, "reason": "Launch fraud scoring"}, headers=admin_headers)

    scenario.healthy = False  # the flagged code path fails; no new deployment
    created = [item for result in run_cycles(2) for item in result["incidents_created"]]
    assert created
    incident_id = f"INC-{created[0]}"
    workflow = client.get(f"/api/incidents/{incident_id}/workflow", headers=admin_headers).json()
    assert "fraud-check" in workflow["incident"]["title"]
    assert workflow["incident"]["deployment_correlated"] is False  # the flag, not the earlier release, changed
    assert client.get("/api/deployments/DEP-1", headers=admin_headers).json()["status"] != "degraded"
    assert any(event["title"] == "Feature flag fraud-check enabled" for event in workflow["incident"]["timeline"])
    investigation = workflow["investigation"]
    assert investigation["recommended_action"] == "disable_feature" and investigation["recommended_flag"] == "fraud-check"
    assert any(item["kind"] == "FLAG" for item in investigation["evidence"])
    remediation = workflow["remediations"][0]
    assert remediation["action"] == "disable_feature" and remediation["mode"] == "automatic"
    assert remediation["status"] == "succeeded" and remediation["target_flag"] == "fraud-check"
    assert client.get("/api/flags/evaluate?application=payment-api").json()["flags"]["fraud-check"]["enabled"] is False
    assert workflow["incident"]["status"] == "verifying"

    scenario.healthy = True  # the application picked up the disabled flag
    run_cycles(2)
    workflow = client.get(f"/api/incidents/{incident_id}/workflow", headers=admin_headers).json()
    assert workflow["incident"]["status"] == "resolved"
    assert workflow["incident"]["resolution_summary"].startswith("Disable feature fraud-check")
    history = client.get("/api/flags?application=payment-api", headers=admin_headers).json()[0]["history"]
    assert history[0]["enabled"] is False and "NexOps remediation" in history[0]["reason"]

    deadline = time.time() + 3
    while time.time() < deadline and "incident.resolved" not in {item["kind"] for item in delivered}:
        time.sleep(0.05)
    assert {"incident.opened", "flag.disabled", "incident.resolved"} <= {item["kind"] for item in delivered}


def test_flags_token_protects_evaluation(client, admin_headers, monkeypatch):
    monkeypatch.setattr(get_settings(), "flags_token", "flag-secret")
    client.post("/api/applications", json=PAYMENT_API, headers=admin_headers)
    assert client.get("/api/flags/evaluate?application=payment-api").status_code == 401
    assert client.get("/api/flags/evaluate?application=payment-api", headers={"Authorization": "Bearer flag-secret"}).status_code == 200
