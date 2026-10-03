"""Runtime configuration read from the process environment (and an optional backend/.env file)."""
from functools import lru_cache
import os

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value and value.strip() else default


def _float(name: str, default: float) -> float:
    value = os.getenv(name)
    return float(value) if value and value.strip() else default


def _str(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


class Settings:
    """All NexOps settings. Every integration is optional and disabled until its variables are set."""

    app_name = "NexOps API"
    version = "1.0.0"

    def __init__(self) -> None:
        # Core
        self.environment = _str("NEXOPS_ENV", "development")
        self.database_url = _str("DATABASE_URL")
        self.cors_origins = [
            origin.strip()
            for origin in (_str("NEXOPS_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173") or "").split(",")
            if origin.strip()
        ]
        self.secret_key = _str("NEXOPS_SECRET_KEY")
        self.token_ttl_hours = _int("NEXOPS_TOKEN_TTL_HOURS", 12)
        self.admin_email = _str("NEXOPS_ADMIN_EMAIL")
        self.admin_password = _str("NEXOPS_ADMIN_PASSWORD")
        self.admin_name = _str("NEXOPS_ADMIN_NAME", "NexOps Admin")
        self.webhook_secret = _str("NEXOPS_WEBHOOK_SECRET")
        self.public_url = _str("NEXOPS_PUBLIC_URL", "http://localhost:5173")

        # Background monitor (detection, verification)
        self.run_worker_in_api = _bool("NEXOPS_RUN_WORKER_IN_API", True)
        self.monitor_interval_seconds = _int("NEXOPS_MONITOR_INTERVAL_SECONDS", 30)
        self.detection_consecutive_breaches = _int("NEXOPS_DETECTION_CONSECUTIVE_BREACHES", 2)
        self.correlation_window_minutes = _int("NEXOPS_CORRELATION_WINDOW_MINUTES", 60)
        # Replica restarts/health-check blips right after a deployment are expected and do not open incidents alone.
        self.rollout_grace_seconds = _int("NEXOPS_ROLLOUT_GRACE_SECONDS", 45)
        self.deployment_verification_minutes = _int("NEXOPS_DEPLOYMENT_VERIFICATION_MINUTES", 10)
        self.recovery_required_healthy_samples = _int("NEXOPS_RECOVERY_HEALTHY_SAMPLES", 3)
        self.recovery_timeout_minutes = _int("NEXOPS_RECOVERY_TIMEOUT_MINUTES", 15)
        # Wait this long after a remediation before counting samples, so pre-rollback rate windows age out.
        self.recovery_grace_seconds = _int("NEXOPS_RECOVERY_GRACE_SECONDS", max(30, self.monitor_interval_seconds))
        self.auto_investigate = _bool("NEXOPS_AUTO_INVESTIGATE", True)
        self.remediation_enabled = _bool("NEXOPS_REMEDIATION_ENABLED", True)
        self.metric_retention_days = _int("NEXOPS_METRIC_RETENTION_DAYS", 30)

        # Prometheus
        self.prometheus_url = _str("PROMETHEUS_URL")
        self.prometheus_bearer_token = _str("PROMETHEUS_BEARER_TOKEN")
        self.prometheus_rate_window = _str("PROMETHEUS_RATE_WINDOW", "2m")

        # Loki
        self.loki_url = _str("LOKI_URL")
        self.loki_tenant_id = _str("LOKI_TENANT_ID")
        self.loki_bearer_token = _str("LOKI_BEARER_TOKEN")

        # AWS (CloudWatch, EC2, ECR) - credentials come from the standard AWS chain
        self.aws_enabled = _bool("AWS_INTEGRATION_ENABLED", False)
        self.aws_region = _str("AWS_REGION", _str("AWS_DEFAULT_REGION", "us-east-1"))

        # Kubernetes
        self.kubernetes_enabled = _bool("KUBERNETES_ENABLED", False)
        self.kubernetes_in_cluster = _bool("KUBERNETES_IN_CLUSTER", False)
        self.kubeconfig = _str("KUBECONFIG")
        self.kubernetes_context = _str("KUBERNETES_CONTEXT")

        # Docker Engine (local / EC2 hosts)
        self.docker_enabled = _bool("DOCKER_ENABLED", False)
        self.docker_host = _str("DOCKER_HOST")

        # Jaeger (distributed traces); JAEGER_UI_URL is the browser-facing address used for trace links
        self.jaeger_url = _str("JAEGER_URL")
        self.jaeger_ui_url = (_str("JAEGER_UI_URL", self.jaeger_url or "") or "").rstrip("/") or None

        # Feature flags: bearer token applications use to read their flags (unset = open read access)
        self.flags_token = _str("NEXOPS_FLAGS_TOKEN")
        # Canary analysis: bearer token Argo Rollouts sends to /api/analysis/canary (unset = open)
        self.analysis_token = _str("NEXOPS_ANALYSIS_TOKEN")

        # Incident notifications (Slack/Discord-compatible incoming webhook)
        self.notify_webhook_url = _str("NEXOPS_NOTIFY_WEBHOOK_URL")
        self.notify_events = {
            item.strip() for item in (_str("NEXOPS_NOTIFY_EVENTS", "incident.opened,approval.required,flag.disabled,canary.failed,remediation.failed,recovery.failed,incident.resolved") or "").split(",")
            if item.strip()
        }

        # GitHub
        self.github_token = _str("GITHUB_TOKEN")
        self.github_api_url = (_str("GITHUB_API_URL", "https://api.github.com") or "").rstrip("/")

        # Groq (OpenAI-compatible LLM API) for AI investigation and postmortems
        self.groq_api_key = _str("GROQ_API_KEY")
        self.groq_model = _str("GROQ_MODEL", "openai/gpt-oss-120b")
        self.groq_base_url = (_str("GROQ_BASE_URL", "https://api.groq.com/openai/v1") or "").rstrip("/")
        self.groq_timeout_seconds = _float("GROQ_TIMEOUT_SECONDS", 60.0)

    @property
    def is_production(self) -> bool:
        return (self.environment or "").lower() == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
