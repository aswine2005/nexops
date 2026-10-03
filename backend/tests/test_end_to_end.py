"""Deploy → monitor → detect → investigate → approve → rollback → verify → postmortem → DORA, through the public API."""
from datetime import datetime, timedelta, timezone

from app.services.monitor import Monitor
from tests.conftest import PAYMENT_API, signed


def run_cycles(count: int) -> list[dict]:
    monitor = Monitor(background=False)
    return [monitor.run_cycle() for _ in range(count)]


def deploy(client, version: str, files: list[dict] | None = None, minutes_ago: int = 30) -> dict:
    payload = {
        "event": "finished", "application": "payment-api", "environment": "production", "version": version,
        "commit_sha": f"{version.replace('.', '')}abcdef", "commit_message": f"Release {version}", "author": "aswin",
        "commit_timestamp": (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat(),
        "ci_status": "success", "ci_run_id": f"run-{version}", "image": f"nexops/payment-api:{version}",
        "pipeline": [{"name": "Build", "status": "success"}, {"name": "Tests", "status": "success"}],
        "files": files or [],
    }
    body, headers = signed(payload)
    response = client.post("/api/webhooks/deployments", content=body, headers=headers)
    assert response.status_code == 202, response.text
    return response.json()


def test_full_reliability_loop(client, admin_headers, scenario):
    assert client.post("/api/applications", json=PAYMENT_API, headers=admin_headers).status_code == 201

    deploy(client, "v1.0.0")
    run_cycles(3)
    app = client.get("/api/applications/payment-api", headers=admin_headers).json()
    assert app["status"] == "healthy"
    assert app["current_version"] == "v1.0.0"
    assert app["latest"]["error_rate_percent"] == 0.2

    bad = deploy(client, "v1.0.1", files=[
        {"filename": "app/config/database.py", "additions": 4, "deletions": 1, "patch": "-DB_PORT = 5432\n+DB_PORT = 6543"},
        {"filename": "app/payments.py", "additions": 20, "deletions": 3},
    ])
    assert bad["previous_version"] == "v1.0.0"
    assert bad["risk_level"] in {"medium", "high"}

    scenario.healthy = False
    scenario.version = "v1.0.1"
    first = run_cycles(1)[0]
    assert first["incidents_created"] == []  # one breaching sample is not enough
    second = run_cycles(1)[0]
    assert len(second["incidents_created"]) == 1
    incident_id = f"INC-{second['incidents_created'][0]}"

    workflow = client.get(f"/api/incidents/{incident_id}/workflow", headers=admin_headers).json()
    incident = workflow["incident"]
    assert incident["deployment_correlated"] is True
    assert incident["deployment"]["version"] == "v1.0.1"
    assert incident["status"] == "awaiting_approval"
    codes = {signal["code"] for signal in incident["signals"]}
    assert {"error_rate", "p99_latency", "database_errors"} <= codes
    investigation = workflow["investigation"]
    assert investigation["provider"] == "local-rules"
    assert "database" in investigation["root_cause"].lower()
    assert investigation["recommended_action"] == "rollback"
    assert investigation["cited_evidence_ids"]
    remediation = workflow["remediations"][0]
    assert remediation["status"] == "proposed"
    assert remediation["target_version"] == "v1.0.0"
    assert remediation["target_image"] == "nexops/payment-api:v1.0.0"
    assert remediation["policy_id"] == "production-rollback-approval"

    deployment = client.get(f"/api/deployments/{bad['id']}", headers=admin_headers).json()
    assert deployment["status"] == "degraded"
    assert deployment["ci_status"] == "success"

    approved = client.post(f"/api/remediations/{remediation['id']}/approve", headers=admin_headers)
    assert approved.status_code == 200, approved.text
    assert scenario.redeploys == ["nexops/payment-api:v1.0.0"]
    workflow = approved.json()
    assert workflow["incident"]["status"] == "verifying"
    assert workflow["remediations"][0]["status"] == "succeeded"

    run_cycles(2)
    workflow = client.get(f"/api/incidents/{incident_id}/workflow", headers=admin_headers).json()
    assert workflow["incident"]["status"] == "resolved", workflow["verification"]
    assert workflow["verification"]["status"] == "passed"
    assert all(check["passed"] for check in workflow["verification"]["checks"])
    assert workflow["postmortem"]["provider"] == "template"
    assert "v1.0.1" in workflow["postmortem"]["summary"]
    titles = [event["title"] for event in workflow["incident"]["timeline"]]
    assert "Recovery verified" in titles and any("approved" in title for title in titles)

    deployments = {item["version"]: item for item in client.get("/api/deployments", headers=admin_headers).json() if item["kind"] != "rollback"}
    assert deployments["v1.0.1"]["status"] == "rolled_back"
    rollback = [item for item in client.get("/api/deployments", headers=admin_headers).json() if item["kind"] == "rollback"][0]
    assert rollback["status"] == "healthy" and rollback["version"] == "v1.0.0"

    dora = client.get("/api/dora", headers=admin_headers).json()
    summary = dora["summary"]
    assert summary["deployments"] == 2
    assert summary["change_failure_rate_percent"] == 50.0
    assert summary["recovery_time_minutes"] is not None
    assert summary["rework_rate_percent"] > 0
    assert summary["lead_time_minutes"] >= 29

    agents = {agent["id"]: agent for agent in client.get("/api/agents", headers=admin_headers).json()}
    for key in ("deployment_risk", "investigation", "root_cause", "remediation", "recovery_verification", "postmortem"):
        assert agents[key]["runs_total"] >= 1, key

    # Incident memory feeds the next investigation of a similar failure.
    deploy(client, "v1.0.2", files=[{"filename": "app/config/database.py", "additions": 1, "deletions": 1}], minutes_ago=5)
    scenario.healthy = False
    scenario.version = "v1.0.2"
    created = [item for result in run_cycles(2) for item in result["incidents_created"]]
    assert created
    workflow = client.get(f"/api/incidents/INC-{created[0]}/workflow", headers=admin_headers).json()
    assert workflow["similar"] and workflow["similar"][0]["id"] == incident_id
    assert any(item["kind"] == "HISTORY" for item in workflow["investigation"]["evidence"])


def test_breach_before_ci_finished_correlates_with_the_rolling_release(client, admin_headers, scenario):
    """The new version serves traffic before CI posts "finished": the incident must still blame that release."""
    assert client.post("/api/applications", json=PAYMENT_API, headers=admin_headers).status_code == 201
    deploy(client, "v1.0.0")
    run_cycles(2)
    body, headers = signed({"event": "started", "application": "payment-api", "version": "v1.1.0", "ci_run_id": "run-v1.1.0"})
    started = client.post("/api/webhooks/deployments", content=body, headers=headers).json()
    scenario.healthy = False
    scenario.version = "v1.1.0"
    created = [item for result in run_cycles(2) for item in result["incidents_created"]]
    assert created
    incident = client.get(f"/api/incidents/INC-{created[0]}", headers=admin_headers).json()
    assert incident["deployment"]["id"] == started["id"]
    body, headers = signed({"event": "finished", "application": "payment-api", "version": "v1.1.0", "ci_run_id": "run-v1.1.0", "ci_status": "success", "image": "nexops/payment-api:v1.1.0"})
    finished = client.post("/api/webhooks/deployments", content=body, headers=headers).json()
    assert finished["id"] == started["id"]
    assert finished["status"] == "degraded"


def test_replica_churn_during_rollout_does_not_open_an_incident(client, admin_headers, scenario):
    assert client.post("/api/applications", json=PAYMENT_API, headers=admin_headers).status_code == 201
    deploy(client, "v1.0.0")
    run_cycles(2)
    deploy(client, "v1.0.1")
    from tests.conftest import FakeDocker

    original = FakeDocker.state

    def churn(self, app, with_stats=True):
        state = original(self, app, with_stats)
        state.replicas[0].ready = False
        state.replicas[0].reason = "Starting"
        return state

    FakeDocker.state = churn
    try:
        results = run_cycles(3)
    finally:
        FakeDocker.state = original
    assert all(not result["incidents_created"] for result in results)
