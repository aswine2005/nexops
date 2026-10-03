"""Lazily-constructed provider clients, configured purely from environment settings."""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

from app.config import Settings, get_settings
from app.integrations.aws import AwsClient
from app.integrations.docker_runtime import DockerRuntime
from app.integrations.github import GitHubClient
from app.integrations.jaeger import JaegerClient
from app.integrations.kubernetes_runtime import KubernetesRuntime
from app.integrations.loki import LokiClient
from app.integrations.prometheus import PrometheusClient
from app.integrations.types import IntegrationError
from app.integrations.llm import GroqClient

logger = logging.getLogger(__name__)
_RETRY_SECONDS = 60


class Integrations:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._lock = threading.Lock()
        self._cache: dict[str, Any] = {}
        self._failures: dict[str, tuple[float, str]] = {}

    def _get(self, name: str, factory: Callable[[], Any]) -> Any:
        with self._lock:
            if name in self._cache:
                return self._cache[name]
            failure = self._failures.get(name)
            if failure and time.time() - failure[0] < _RETRY_SECONDS:
                return None
            try:
                client = factory()
            except IntegrationError as error:
                logger.warning("%s integration unavailable: %s", name, error)
                self._failures[name] = (time.time(), str(error))
                return None
            self._cache[name] = client
            self._failures.pop(name, None)
            return client

    def failure(self, name: str) -> str | None:
        failure = self._failures.get(name)
        return failure[1] if failure else None

    @property
    def prometheus(self) -> PrometheusClient | None:
        if not self.settings.prometheus_url:
            return None
        return self._get("prometheus", lambda: PrometheusClient(self.settings.prometheus_url, self.settings.prometheus_bearer_token))

    @property
    def loki(self) -> LokiClient | None:
        if not self.settings.loki_url:
            return None
        return self._get("loki", lambda: LokiClient(self.settings.loki_url, self.settings.loki_tenant_id, self.settings.loki_bearer_token))

    @property
    def jaeger(self) -> JaegerClient | None:
        if not self.settings.jaeger_url:
            return None
        return self._get("jaeger", lambda: JaegerClient(self.settings.jaeger_url))

    @property
    def docker(self) -> DockerRuntime | None:
        if not self.settings.docker_enabled:
            return None
        return self._get("docker", lambda: DockerRuntime(self.settings.docker_host))

    @property
    def kubernetes(self) -> KubernetesRuntime | None:
        if not self.settings.kubernetes_enabled:
            return None
        return self._get("kubernetes", lambda: KubernetesRuntime(self.settings.kubernetes_in_cluster, self.settings.kubeconfig, self.settings.kubernetes_context))

    @property
    def aws(self) -> AwsClient | None:
        if not self.settings.aws_enabled:
            return None
        return self._get("aws", lambda: AwsClient(self.settings.aws_region or "us-east-1"))

    @property
    def github(self) -> GitHubClient | None:
        if not self.settings.github_token:
            return None
        return self._get("github", lambda: GitHubClient(self.settings.github_token, self.settings.github_api_url))

    @property
    def llm(self) -> GroqClient | None:
        if not self.settings.groq_api_key:
            return None
        return self._get("groq", lambda: GroqClient(self.settings.groq_api_key, self.settings.groq_model or "openai/gpt-oss-120b", self.settings.groq_base_url, self.settings.groq_timeout_seconds))

    def status(self) -> list[dict]:
        """Connectivity report used by the Settings page."""
        settings = self.settings
        report: list[dict] = []

        def add(key: str, name: str, configured: bool, check: Callable[[], tuple[bool, str]], hint: str) -> None:
            if not configured:
                report.append({"key": key, "name": name, "configured": False, "healthy": False, "detail": hint})
                return
            try:
                healthy, detail = check()
            except Exception as error:  # report every failure mode instead of failing the page
                healthy, detail = False, str(error)[:300]
            report.append({"key": key, "name": name, "configured": True, "healthy": healthy, "detail": detail})

        def check_prometheus() -> tuple[bool, str]:
            client = self.prometheus
            if client is None:
                return False, self.failure("prometheus") or "Client unavailable"
            return (True, settings.prometheus_url or "") if client.ready() else (False, f"{settings.prometheus_url} is not ready")

        def check_loki() -> tuple[bool, str]:
            client = self.loki
            if client is None:
                return False, self.failure("loki") or "Client unavailable"
            return (True, settings.loki_url or "") if client.ready() else (False, f"{settings.loki_url} is not ready")

        def check_jaeger() -> tuple[bool, str]:
            client = self.jaeger
            if client is None:
                return False, self.failure("jaeger") or "Client unavailable"
            return (True, settings.jaeger_url or "") if client.ready() else (False, f"{settings.jaeger_url} is not answering")

        def check_notifications() -> tuple[bool, str]:
            host = (settings.notify_webhook_url or "").split("/")[2] if "://" in (settings.notify_webhook_url or "") else "webhook"
            return True, f"Posting {', '.join(sorted(settings.notify_events))} to {host}"

        def check_docker() -> tuple[bool, str]:
            client = self.docker
            if client is None:
                return False, self.failure("docker") or "Docker Engine unavailable"
            version = client.client.version()
            return True, f"Docker Engine {version.get('Version')} ({version.get('Os')}/{version.get('Arch')})"

        def check_kubernetes() -> tuple[bool, str]:
            client = self.kubernetes
            if client is None:
                return False, self.failure("kubernetes") or "Kubernetes unavailable"
            nodes = client.cluster_summary()["nodes"]
            return True, f"{len(nodes)} nodes reachable"

        def check_aws() -> tuple[bool, str]:
            client = self.aws
            if client is None:
                return False, self.failure("aws") or "AWS client unavailable"
            identity = client.identity()
            return True, f"Account {identity.get('Account')} · {settings.aws_region}"

        def check_github() -> tuple[bool, str]:
            client = self.github
            if client is None:
                return False, self.failure("github") or "GitHub client unavailable"
            core = client.rate_limit()
            return True, f"API rate limit remaining {core.get('remaining')}/{core.get('limit')}"

        def check_groq() -> tuple[bool, str]:
            client = self.llm
            if client is None:
                return False, "Groq client unavailable"
            return (True, f"Model {settings.groq_model}") if client.ping() else (False, "Groq API key was rejected or the API is unreachable")

        add("prometheus", "Prometheus", bool(settings.prometheus_url), check_prometheus, "Set PROMETHEUS_URL to collect golden-signal metrics.")
        add("loki", "Grafana Loki", bool(settings.loki_url), check_loki, "Set LOKI_URL to search application logs.")
        add("jaeger", "Jaeger tracing", bool(settings.jaeger_url), check_jaeger, "Set JAEGER_URL to use distributed traces (OpenTelemetry) as investigation evidence.")
        add("docker", "Docker Engine", settings.docker_enabled, check_docker, "Set DOCKER_ENABLED=true and mount the Docker socket for container runtimes.")
        add("kubernetes", "Kubernetes", settings.kubernetes_enabled, check_kubernetes, "Set KUBERNETES_ENABLED=true (and KUBERNETES_IN_CLUSTER on EKS) for pod health and rollbacks.")
        add("aws", "AWS (CloudWatch, EC2, ECR)", settings.aws_enabled, check_aws, "Set AWS_INTEGRATION_ENABLED=true with an IAM role or AWS credentials.")
        add("github", "GitHub", bool(settings.github_token), check_github, "Set GITHUB_TOKEN to enrich deployments with diffs and dispatch rollback workflows.")
        add("groq", "Groq LLM", bool(settings.groq_api_key), check_groq, "Set GROQ_API_KEY (free at console.groq.com) for AI investigations; deterministic evidence rules are used otherwise.")
        add("notifications", "Incident notifications", bool(settings.notify_webhook_url), check_notifications, "Set NEXOPS_NOTIFY_WEBHOOK_URL (Slack or Discord incoming webhook) to be alerted about incidents and approvals.")
        report.append({
            "key": "webhooks", "name": "CI/CD deployment webhooks", "configured": bool(settings.webhook_secret),
            "healthy": bool(settings.webhook_secret),
            "detail": "HMAC-signed webhooks accepted at /api/webhooks/deployments" if settings.webhook_secret else "Set NEXOPS_WEBHOOK_SECRET so GitHub Actions can report deployments.",
        })
        return report


_integrations: Integrations | None = None
_integrations_lock = threading.Lock()


def get_integrations() -> Integrations:
    global _integrations
    with _integrations_lock:
        if _integrations is None:
            _integrations = Integrations(get_settings())
        return _integrations


def set_integrations(integrations: Integrations | None) -> None:
    """Override the registry (tests inject fakes)."""
    global _integrations
    with _integrations_lock:
        _integrations = integrations
