"""Request bodies validated by the API. Responses are produced by app.services.serializers."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

Environment = Literal["production", "staging", "development"]
Runtime = Literal["docker", "kubernetes", "ec2", "external"]
RollbackStrategy = Literal["docker", "kubernetes", "github_actions", "none"]
Role = Literal["admin", "operator", "viewer"]
PolicyMode = Literal["automatic", "approval_required", "forbidden"]
PolicyAction = Literal["rollback", "restart", "scale_up", "pause_deployments", "disable_feature", "database_modification"]


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=400)


class UserCreate(BaseModel):
    email: str = Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+$")
    display_name: str = Field(min_length=1, max_length=160)
    role: Role = "operator"
    password: str = Field(min_length=10, max_length=400)


class UserUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=160)
    role: Role | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=10, max_length=400)


class HealthCheckInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    url: HttpUrl


class ApplicationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]{0,98}[a-z0-9]$")
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    owner: str | None = Field(default=None, max_length=160)
    environment: Environment = "production"
    repository: str | None = Field(default=None, max_length=300, pattern=r"^[\w.-]+/[\w.-]+$")
    runtime: Runtime = "docker"
    monitoring_enabled: bool = True
    current_version: str | None = Field(default=None, max_length=120)
    prometheus_selector: str | None = Field(default=None, max_length=500)
    metric_queries: dict[str, str] = Field(default_factory=dict)
    loki_selector: str | None = Field(default=None, max_length=500)
    cloudwatch_log_group: str | None = Field(default=None, max_length=500)
    tracing_service: str | None = Field(default=None, max_length=200)
    health_checks: list[HealthCheckInput] = Field(default_factory=list, max_length=10)
    docker_container: str | None = Field(default=None, max_length=200)
    k8s_namespace: str | None = Field(default=None, max_length=200)
    k8s_deployment: str | None = Field(default=None, max_length=200)
    k8s_container: str | None = Field(default=None, max_length=200)
    ec2_instance_id: str | None = Field(default=None, max_length=64)
    image_repository: str | None = Field(default=None, max_length=500)
    rollback_strategy: RollbackStrategy = "none"
    github_workflow: str | None = Field(default=None, max_length=300)
    github_ref: str | None = Field(default=None, max_length=200)
    thresholds: dict[str, float] = Field(default_factory=dict)
    dependencies: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("prometheus_selector")
    @classmethod
    def _selector_without_braces(cls, value: str | None) -> str | None:
        if value:
            value = value.strip()
            if value.startswith("{") and value.endswith("}"):
                value = value[1:-1].strip()
        return value or None

    @field_validator("loki_selector")
    @classmethod
    def _loki_selector_braces(cls, value: str | None) -> str | None:
        if value:
            value = value.strip()
            if not value.startswith("{"):
                value = "{" + value + "}"
        return value or None

    @field_validator("metric_queries")
    @classmethod
    def _known_metric_keys(cls, value: dict[str, str]) -> dict[str, str]:
        allowed = {"request_rate", "error_rate_percent", "p50_latency_ms", "p95_latency_ms", "p99_latency_ms", "cpu_percent", "memory_percent", "business_success_rate"}
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"Unknown metric keys: {', '.join(sorted(unknown))}")
        return {key: query for key, query in value.items() if query.strip()}


class PipelineStage(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    status: str = Field(min_length=1, max_length=40)


class ChangedFile(BaseModel):
    filename: str = Field(min_length=1, max_length=600)
    status: str = "modified"
    additions: int = 0
    deletions: int = 0
    patch: str | None = Field(default=None, max_length=20000)


class DeploymentWebhook(BaseModel):
    event: Literal["started", "finished"] = "finished"
    application: str = Field(min_length=1, max_length=100)
    environment: Environment | None = None
    version: str = Field(min_length=1, max_length=120)
    previous_version: str | None = Field(default=None, max_length=120)
    kind: Literal["release", "rollback", "hotfix"] = "release"
    source: str = Field(default="github_actions", max_length=40)
    commit_sha: str | None = Field(default=None, max_length=80)
    commit_message: str | None = Field(default=None, max_length=5000)
    commit_timestamp: datetime | None = None
    branch: str | None = Field(default=None, max_length=200)
    author: str | None = Field(default=None, max_length=200)
    repository: str | None = Field(default=None, max_length=300)
    # "aborted": a progressive rollout (Argo Rollouts canary) was stopped before promotion; stable kept serving.
    ci_status: Literal["success", "failure", "cancelled", "in_progress", "skipped", "aborted"] = "success"
    ci_run_id: str | None = Field(default=None, max_length=120)
    ci_run_url: str | None = Field(default=None, max_length=600)
    image: str | None = Field(default=None, max_length=600)
    summary: str | None = Field(default=None, max_length=5000)
    pipeline: list[PipelineStage] = Field(default_factory=list, max_length=30)
    files: list[ChangedFile] | None = Field(default=None, max_length=1000)
    # Monorepos: only changes under these path prefixes belong to this application.
    paths: list[str] | None = Field(default=None, max_length=20)


class DeployGateRequest(BaseModel):
    application: str = Field(min_length=1, max_length=100)
    kind: Literal["release", "rollback", "hotfix"] = "release"


class PolicyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]{0,118}[a-z0-9]$")
    name: str = Field(min_length=1, max_length=240)
    description: str = Field(default="", max_length=2000)
    action: PolicyAction
    environment: Literal["production", "staging", "development", "*"] = "production"
    application_id: str | None = None
    mode: PolicyMode
    enabled: bool = True
    conditions: dict = Field(default_factory=dict)

    @field_validator("conditions")
    @classmethod
    def _known_conditions(cls, value: dict) -> dict:
        allowed = {"min_confidence", "require_previous_version", "max_executions_per_hour", "max_replicas"}
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"Unknown policy conditions: {', '.join(sorted(unknown))}")
        return value


class RemediationRequest(BaseModel):
    action: Literal["rollback", "restart", "scale_up", "pause_deployments", "disable_feature"]
    reason: str | None = Field(default=None, max_length=1000)
    flag: str | None = Field(default=None, max_length=120)


class FlagInput(BaseModel):
    key: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,118}[a-z0-9]$")
    description: str | None = Field(default=None, max_length=1000)
    enabled: bool = False
    rollout_percent: int = Field(default=100, ge=0, le=100)


class FlagUpdate(BaseModel):
    enabled: bool | None = None
    rollout_percent: int | None = Field(default=None, ge=0, le=100)
    description: str | None = Field(default=None, max_length=1000)
    reason: str | None = Field(default=None, max_length=500)


class ReasonRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


class FreezeRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)
