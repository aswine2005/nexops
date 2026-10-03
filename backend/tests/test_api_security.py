from tests.conftest import PAYMENT_API, signed


def test_endpoints_require_login(client):
    for path in ("/api/dashboard", "/api/applications", "/api/incidents", "/api/deployments", "/api/policies", "/api/dora"):
        assert client.get(path).status_code == 401, path


def test_login_rejects_bad_password(client):
    response = client.post("/api/auth/login", json={"email": "admin@nexops.test", "password": "wrong"})
    assert response.status_code == 401


def test_viewer_cannot_change_configuration(client, admin_headers):
    created = client.post("/api/users", json={"email": "viewer@nexops.test", "display_name": "Viewer", "role": "viewer", "password": "viewer-password-1"}, headers=admin_headers)
    assert created.status_code == 201
    token = client.post("/api/auth/login", json={"email": "viewer@nexops.test", "password": "viewer-password-1"}).json()["token"]
    viewer = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/applications", headers=viewer).status_code == 200
    assert client.post("/api/applications", json=PAYMENT_API, headers=viewer).status_code == 403
    assert client.post("/api/policies", json={"name": "x", "action": "restart", "mode": "automatic"}, headers=viewer).status_code == 403


def test_webhook_requires_valid_signature(client, admin_headers):
    client.post("/api/applications", json=PAYMENT_API, headers=admin_headers)
    body, headers = signed({"application": "payment-api", "version": "v2.0.0"})
    headers["X-NexOps-Signature"] = "sha256=" + "0" * 64
    assert client.post("/api/webhooks/deployments", content=body, headers=headers).status_code == 401


def test_webhook_rejects_unknown_application(client):
    body, headers = signed({"application": "unknown-app", "version": "v1.0.0"})
    assert client.post("/api/webhooks/deployments", content=body, headers=headers).status_code == 404


def test_deploy_gate_blocks_frozen_releases_but_allows_rollbacks(client, admin_headers):
    client.post("/api/applications", json=PAYMENT_API, headers=admin_headers)
    assert client.post("/api/applications/payment-api/freeze", json={"reason": "INC-1 open"}, headers=admin_headers).status_code == 200
    body, headers = signed({"application": "payment-api", "kind": "release"})
    assert client.post("/api/webhooks/deploy-gate", content=body, headers=headers).json() == {"allowed": False, "reason": "INC-1 open"}
    body, headers = signed({"application": "payment-api", "kind": "rollback"})
    assert client.post("/api/webhooks/deploy-gate", content=body, headers=headers).json()["allowed"] is True


def test_started_then_finished_events_update_one_deployment(client, admin_headers):
    client.post("/api/applications", json=PAYMENT_API, headers=admin_headers)
    base = {"application": "payment-api", "version": "v3.0.0", "ci_run_id": "42"}
    body, headers = signed({**base, "event": "started"})
    started = client.post("/api/webhooks/deployments", content=body, headers=headers).json()
    assert started["status"] == "in_progress"
    body, headers = signed({**base, "event": "finished", "ci_status": "failure"})
    finished = client.post("/api/webhooks/deployments", content=body, headers=headers).json()
    assert finished["id"] == started["id"] and finished["created"] is False
    assert finished["status"] == "failed"
    app = client.get("/api/applications/payment-api", headers=admin_headers).json()
    assert app["current_version"] is None  # failed CI never reaches production


def test_monorepo_paths_limit_changed_files(client, admin_headers):
    client.post("/api/applications", json=PAYMENT_API, headers=admin_headers)
    body, headers = signed({
        "application": "payment-api", "version": "v4.0.0", "paths": ["examples/payment-api"],
        "files": [
            {"filename": "examples/payment-api/config/database.json", "additions": 1, "deletions": 1},
            {"filename": "backend/app/main.py", "additions": 400, "deletions": 10},
            {"filename": "infra/terraform/aws/iam.tf", "additions": 50, "deletions": 0},
        ],
    })
    created = client.post("/api/webhooks/deployments", content=body, headers=headers).json()
    deployment = client.get(f"/api/deployments/{created['id']}", headers=admin_headers).json()
    assert [change["file"] for change in deployment["changes"]] == ["examples/payment-api/config/database.json"]
    assert deployment["risk_level"] != "high"


def test_rollback_deployments_are_low_risk_and_skip_diffs(client, admin_headers):
    client.post("/api/applications", json=PAYMENT_API, headers=admin_headers)
    body, headers = signed({"application": "payment-api", "version": "v1.0.0", "kind": "rollback",
                            "files": [{"filename": "app/config/database.py", "additions": 200, "deletions": 50}]})
    created = client.post("/api/webhooks/deployments", content=body, headers=headers).json()
    deployment = client.get(f"/api/deployments/{created['id']}", headers=admin_headers).json()
    assert deployment["risk_level"] == "low" and deployment["changes"] == []


def test_editing_configuration_keeps_the_running_version(client, admin_headers):
    client.post("/api/applications", json=PAYMENT_API, headers=admin_headers)
    body, headers = signed({"application": "payment-api", "version": "v2.0.0"})
    client.post("/api/webhooks/deployments", content=body, headers=headers)
    updated = client.put("/api/applications/payment-api", json={**PAYMENT_API, "owner": "New owner"}, headers=admin_headers).json()
    assert updated["current_version"] == "v2.0.0" and updated["owner"] == "New owner"
