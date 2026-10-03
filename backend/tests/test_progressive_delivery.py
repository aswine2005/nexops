"""Argo Rollouts support: canary analysis verdicts, the analysis endpoint, and Rollout reads/patches."""
import time

from kubernetes.client.exceptions import ApiException

from app.config import get_settings
from app.detection.engine import Thresholds
from app.integrations.kubernetes_runtime import KubernetesRuntime, Workload
from app.integrations.registry import get_integrations
from app.integrations.types import Replica
from app.services import canary, notifications
from tests.conftest import HEALTHY, PAYMENT_API
from tests.test_end_to_end import deploy

STABLE = {"request_rate": 6.0, "error_rate_percent": 0.0, "p99_latency_ms": 48.0, "business_success_rate": 100.0}


def test_canary_verdicts():
    thresholds = Thresholds()
    good = {"request_rate": 2.0, "error_rate_percent": 0.4, "p99_latency_ms": 60.0, "business_success_rate": 100.0}
    assert canary.verdict(good, STABLE, thresholds, "1m")[0] == "pass"

    bad = {"request_rate": 2.0, "error_rate_percent": 54.0, "p99_latency_ms": 70.0, "business_success_rate": 0.0}
    outcome, reasons, limits = canary.verdict(bad, STABLE, thresholds, "1m")
    assert outcome == "fail" and limits["error_rate_percent"] == 1.0
    assert reasons[0].startswith("Error rate 54.00% > 1.0%") and any("Business success" in reason for reason in reasons)

    slow = {**good, "p99_latency_ms": 900.0}
    assert canary.verdict(slow, STABLE, thresholds, "1m")[0] == "fail"  # 900 ms > max(48 × 2.5, 148) ms

    quiet = {**bad, "request_rate": 0.1}
    assert canary.verdict(quiet, STABLE, thresholds, "1m")[0] == "inconclusive"  # 6 requests is not evidence


class HashAwarePrometheus:
    """Canary pods (hash c4n4ry) fail; everything else is healthy."""

    def query(self, promql: str, at=None):
        values = {"request_rate": 3.0, "error_rate_percent": 40.0, "p99_latency_ms": 55.0, "business_success_rate": 10.0} if 'rollouts_pod_template_hash="c4n4ry"' in promql else {**HEALTHY, **STABLE}
        if "http_request_duration_seconds_bucket" in promql:
            return values["p99_latency_ms"]
        if "business_transactions_total" in promql:
            return values["business_success_rate"]
        if 'status=~"5.."' in promql:
            return values["error_rate_percent"]
        return values["request_rate"]

    def ready(self):
        return True


def test_analysis_endpoint_records_runs_and_alerts(client, admin_headers, monkeypatch):
    monkeypatch.setattr(get_settings(), "analysis_token", "argo-secret")
    monkeypatch.setattr(get_settings(), "notify_webhook_url", "https://hooks.example.test/x")
    delivered: list[dict] = []
    monkeypatch.setattr(notifications, "_deliver", lambda url, items: delivered.extend(items))
    client.post("/api/applications", json=PAYMENT_API, headers=admin_headers)
    deploy(client, "v1.0.0")
    deploy(client, "v1.1.0", minutes_ago=1)
    get_integrations()._cache["prometheus"] = HashAwarePrometheus()

    url = "/api/analysis/canary?application=payment-api&canary_hash=c4n4ry&stable_hash=st4ble"
    assert client.get(url).status_code == 401
    auth = {"Authorization": "Bearer argo-secret"}
    assert client.get("/api/analysis/canary?application=payment-api&canary_hash=bad%22hash", headers=auth).status_code == 422

    failing = client.get(url, headers=auth).json()
    assert failing["verdict"] == "fail" and failing["stable"]["error_rate_percent"] == 0.0
    assert failing["deployment_id"] == "DEP-2"  # the newest release in progress
    passing = client.get(url.replace("c4n4ry", "g00d"), headers=auth).json()
    assert passing["verdict"] == "pass"

    detail = client.get("/api/deployments/DEP-2", headers=admin_headers).json()
    assert [item["verdict"] for item in detail["canary_analyses"]] == ["fail", "pass"]
    agents = {agent["id"]: agent for agent in client.get("/api/agents", headers=admin_headers).json()}
    assert agents["canary_analysis"]["runs_total"] == 2

    deadline = time.time() + 3
    while time.time() < deadline and not delivered:
        time.sleep(0.05)
    assert [item["kind"] for item in delivered] == ["canary.failed"]
    assert delivered[0]["link"].endswith("/deployments/DEP-2")


def rollout(phase="Progressing", step=1, abort=False):
    return {
        "metadata": {"name": "payment-api", "namespace": "payments"},
        "spec": {
            "replicas": 4,
            "selector": {"matchLabels": {"app": "payment-api"}},
            "template": {"spec": {"containers": [{"name": "payment-api", "image": "nexops/payment-api:v1.4.0", "resources": {"limits": {"cpu": "500m"}}}]}},
            "strategy": {"canary": {"steps": [{"setWeight": 25}, {"pause": {"duration": "30s"}}, {"analysis": {}}, {"setWeight": 50}, {"analysis": {}}]}},
        },
        "status": {"phase": phase, "currentStepIndex": step, "stableRS": "st4ble", "currentPodHash": "c4n4ry", "abort": abort},
    }


def test_rollout_progress_reports_stable_and_canary():
    workload = Workload("Rollout", "payments", "payment-api", 4, {"app": "payment-api"}, [{"name": "payment-api", "image": "nexops/payment-api:v1.4.0", "limits": {}}], rollout(step=3))
    replicas = [Replica(name=f"s{index}", status="Running", ready=True, image="nexops/payment-api:v1.3.0", track="stable") for index in range(3)]
    replicas.append(Replica(name="c0", status="Running", ready=True, image="nexops/payment-api:v1.4.0", track="canary"))
    progress = KubernetesRuntime._rollout_progress(workload, replicas)
    assert progress["canary_weight"] == 50 and progress["updating"] is True
    assert progress["stable_version"] == "v1.3.0" and progress["canary_version"] == "v1.4.0"

    aborted = KubernetesRuntime._rollout_progress(Workload(**{**workload.__dict__, "rollout": rollout("Degraded", 0, abort=True)}), replicas[:3])
    assert aborted["aborted"] is True and aborted["canary_weight"] == 0 and aborted["stable_version"] == "v1.3.0"


class FakeApps:
    def read_namespaced_deployment(self, name, namespace):
        raise ApiException(status=404, reason="Not Found")


class FakeCustom:
    def __init__(self):
        self.patches = []

    def get_namespaced_custom_object(self, group, version, namespace, plural, name):
        assert (group, plural) == ("argoproj.io", "rollouts")
        return rollout()

    def patch_namespaced_custom_object(self, group, version, namespace, plural, name, body, _content_type=None):
        self.patches.append((body, _content_type))


def test_rollback_and_restart_patch_the_rollout():
    import kubernetes.client

    runtime = KubernetesRuntime.__new__(KubernetesRuntime)
    runtime.apps, runtime.custom, runtime._exceptions, runtime._kinds = FakeApps(), FakeCustom(), kubernetes.client.exceptions, {}

    class App:
        id, k8s_namespace, k8s_deployment, k8s_container = "payment-api", "payments", "payment-api", None

    result = runtime.set_image(App, "nexops/payment-api:v1.3.0")
    assert result["rollout"] == "payments/payment-api" and runtime._kinds["payments/payment-api"] == "Rollout"
    body, content_type = runtime.custom.patches[0]
    assert content_type == "application/json-patch+json"
    assert body == [{"op": "replace", "path": "/spec/template/spec/containers/0/image", "value": "nexops/payment-api:v1.3.0"}]

    runtime.restart(App)
    runtime.scale(App, 5)
    assert runtime.custom.patches[1][1] == "application/merge-patch+json" and "restartAt" in runtime.custom.patches[1][0]["spec"]
    assert runtime.custom.patches[2][0] == {"spec": {"replicas": 5}}


def test_aborted_canary_is_measured_on_stable_pods_only(monkeypatch):
    from app.config import get_settings
    from app.integrations.types import RuntimeState
    from app.services.telemetry import TelemetryService

    queries: list[str] = []

    class RecordingPrometheus(HashAwarePrometheus):
        def query(self, promql, at=None):
            queries.append(promql)
            return super().query(promql, at)

    class AbortedRollout:
        def state(self, app):
            replica = Replica(name="payment-api-st4ble-a", status="Running", ready=True, image="nexops/payment-api:v1.3.0", track="stable")
            return RuntimeState(runtime="kubernetes", target="payments/payment-api (Argo Rollout)", replicas=[replica], desired_replicas=1, version="v1.3.0",
                                rollout={"aborted": True, "updating": True, "stable_hash": "st4ble", "canary_hash": "c4n4ry"})

        def logs(self, app, since, limit=200):
            return []

    class Integrations:
        prometheus, kubernetes, docker, aws, loki = RecordingPrometheus(), AbortedRollout(), None, None, None

    class App:
        id, name, runtime, prometheus_selector, metric_queries, health_checks = "payment-api", "Payment API", "kubernetes", 'app="payment-api"', {}, []
        loki_selector = ec2_instance_id = cloudwatch_log_group = None

    snapshot = TelemetryService(Integrations(), get_settings()).collect(App)
    assert queries and all('rollouts_pod_template_hash="st4ble"' in query for query in queries)
    assert snapshot.metrics["error_rate_percent"] == 0.0
