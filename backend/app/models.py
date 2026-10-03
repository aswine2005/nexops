"""SQLAlchemy persistence models. PostgreSQL is the system of record for every NexOps entity."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None) -> datetime | None:
    """Normalise timestamps (SQLite returns naive values) to timezone-aware UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class Base(DeclarativeBase):
    """Base metadata for all NexOps persistence models."""


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(400), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="viewer")  # admin | operator | viewer
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    owner: Mapped[str | None] = mapped_column(String(160))
    environment: Mapped[str] = mapped_column(String(40), nullable=False, default="production")
    repository: Mapped[str | None] = mapped_column(String(300))
    runtime: Mapped[str] = mapped_column(String(40), nullable=False, default="docker")  # docker | kubernetes | ec2 | external
    current_version: Mapped[str | None] = mapped_column(String(120))
    previous_version: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="unknown")
    status_reason: Mapped[str | None] = mapped_column(Text)
    monitoring_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Telemetry sources
    prometheus_selector: Mapped[str | None] = mapped_column(String(500))
    metric_queries: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    loki_selector: Mapped[str | None] = mapped_column(String(500))
    cloudwatch_log_group: Mapped[str | None] = mapped_column(String(500))
    tracing_service: Mapped[str | None] = mapped_column(String(200))
    health_checks: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)

    # Runtime targets
    docker_container: Mapped[str | None] = mapped_column(String(200))
    k8s_namespace: Mapped[str | None] = mapped_column(String(200))
    k8s_deployment: Mapped[str | None] = mapped_column(String(200))
    k8s_container: Mapped[str | None] = mapped_column(String(200))
    ec2_instance_id: Mapped[str | None] = mapped_column(String(64))
    image_repository: Mapped[str | None] = mapped_column(String(500))

    # Remediation
    rollback_strategy: Mapped[str] = mapped_column(String(40), nullable=False, default="none")  # docker | kubernetes | github_actions | none
    github_workflow: Mapped[str | None] = mapped_column(String(300))
    github_ref: Mapped[str | None] = mapped_column(String(200))
    deploy_frozen: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    deploy_freeze_reason: Mapped[str | None] = mapped_column(Text)

    thresholds: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    dependencies: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)

    deployments: Mapped[list[Deployment]] = relationship(back_populates="application", cascade="all, delete-orphan", passive_deletes=True)
    incidents: Mapped[list[Incident]] = relationship(back_populates="application", cascade="all, delete-orphan", passive_deletes=True)


class Deployment(Base):
    __tablename__ = "deployments"
    __table_args__ = (Index("ix_deployments_app_finished", "application_id", "finished_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True)
    environment: Mapped[str] = mapped_column(String(40), nullable=False)
    version: Mapped[str] = mapped_column(String(120), nullable=False)
    previous_version: Mapped[str | None] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="release")  # release | rollback | hotfix
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="github_actions")
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="in_progress")
    status_reason: Mapped[str | None] = mapped_column(Text)
    ci_status: Mapped[str] = mapped_column(String(40), nullable=False, default="in_progress")
    ci_run_id: Mapped[str | None] = mapped_column(String(120), index=True)
    ci_run_url: Mapped[str | None] = mapped_column(String(600))
    commit_sha: Mapped[str | None] = mapped_column(String(80))
    commit_message: Mapped[str | None] = mapped_column(Text)
    commit_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    branch: Mapped[str | None] = mapped_column(String(200))
    author: Mapped[str | None] = mapped_column(String(200))
    repository: Mapped[str | None] = mapped_column(String(300))
    image: Mapped[str | None] = mapped_column(String(600))
    pipeline: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    risk_score: Mapped[int | None] = mapped_column(Integer)
    risk_level: Mapped[str | None] = mapped_column(String(20))
    risk_factors: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    files_changed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    additions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deletions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    summary: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)

    application: Mapped[Application] = relationship(back_populates="deployments")
    changes: Mapped[list[DeploymentChange]] = relationship(back_populates="deployment", cascade="all, delete-orphan", order_by="DeploymentChange.id")


class DeploymentChange(Base):
    __tablename__ = "deployment_changes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    deployment_id: Mapped[int] = mapped_column(ForeignKey("deployments.id", ondelete="CASCADE"), nullable=False, index=True)
    file_path: Mapped[str] = mapped_column(String(600), nullable=False)
    change_type: Mapped[str] = mapped_column(String(40), nullable=False, default="modified")
    additions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deletions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    risk_tag: Mapped[str | None] = mapped_column(String(120))
    patch: Mapped[str | None] = mapped_column(Text)

    deployment: Mapped[Deployment] = relationship(back_populates="changes")


class MetricSample(Base):
    """One telemetry sample per application per monitor cycle, collected from the configured providers."""

    __tablename__ = "metric_samples"
    __table_args__ = (Index("ix_metric_samples_app_observed", "application_id", "observed_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    request_rate: Mapped[float | None] = mapped_column(Float)
    error_rate_percent: Mapped[float | None] = mapped_column(Float)
    p50_latency_ms: Mapped[float | None] = mapped_column(Float)
    p95_latency_ms: Mapped[float | None] = mapped_column(Float)
    p99_latency_ms: Mapped[float | None] = mapped_column(Float)
    cpu_percent: Mapped[float | None] = mapped_column(Float)
    memory_percent: Mapped[float | None] = mapped_column(Float)
    healthy_replicas: Mapped[int | None] = mapped_column(Integer)
    total_replicas: Mapped[int | None] = mapped_column(Integer)
    restarts: Mapped[int | None] = mapped_column(Integer)
    db_errors: Mapped[int | None] = mapped_column(Integer)
    failed_health_checks: Mapped[int | None] = mapped_column(Integer)
    health_checks_total: Mapped[int | None] = mapped_column(Integer)
    business_success_rate: Mapped[float | None] = mapped_column(Float)
    runtime_state: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    sources: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    errors: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True)
    deployment_id: Mapped[int | None] = mapped_column(ForeignKey("deployments.id", ondelete="SET NULL"), index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="open")
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    signals: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    baseline: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    observed: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    peak: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    deployment_correlated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    root_cause: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    recommended_action: Mapped[str | None] = mapped_column(Text)
    resolution_summary: Mapped[str | None] = mapped_column(Text)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mitigated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)

    application: Mapped[Application] = relationship(back_populates="incidents")
    deployment: Mapped[Deployment | None] = relationship()
    events: Mapped[list[IncidentEvent]] = relationship(back_populates="incident", cascade="all, delete-orphan", order_by="IncidentEvent.event_at")


class IncidentEvent(Base):
    __tablename__ = "incident_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False, index=True)
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False, default="note")
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="info")
    actor: Mapped[str | None] = mapped_column(String(200))

    incident: Mapped[Incident] = relationship(back_populates="events")


class Investigation(Base):
    __tablename__ = "investigations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    provider: Mapped[str | None] = mapped_column(String(60))
    model: Mapped[str | None] = mapped_column(String(160))
    root_cause: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    reasoning_summary: Mapped[str | None] = mapped_column(Text)
    recommended_action: Mapped[str | None] = mapped_column(String(60))
    recommended_flag: Mapped[str | None] = mapped_column(String(120))
    recommendation_detail: Mapped[str | None] = mapped_column(Text)
    alternative_hypotheses: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    next_steps: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    evidence: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    cited_evidence_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    incident_id: Mapped[int | None] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    deployment_id: Mapped[int | None] = mapped_column(ForeignKey("deployments.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str | None] = mapped_column(String(60))
    model: Mapped[str | None] = mapped_column(String(160))
    summary: Mapped[str | None] = mapped_column(Text)
    output: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Policy(Base):
    __tablename__ = "policies"

    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    action: Mapped[str] = mapped_column(String(60), nullable=False)  # rollback | restart | scale | pause_deployments | database_modification
    environment: Mapped[str] = mapped_column(String(40), nullable=False, default="*")
    application_id: Mapped[str | None] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"))
    mode: Mapped[str] = mapped_column(String(40), nullable=False)  # automatic | approval_required | forbidden
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    conditions: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class RemediationAction(Base):
    __tablename__ = "remediation_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    target_version: Mapped[str | None] = mapped_column(String(120))
    target_image: Mapped[str | None] = mapped_column(String(600))
    target_flag: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="proposed")
    mode: Mapped[str] = mapped_column(String(40), nullable=False, default="approval_required")
    policy_id: Mapped[str | None] = mapped_column(ForeignKey("policies.id", ondelete="SET NULL"))
    policy_reasons: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    reason: Mapped[str | None] = mapped_column(Text)
    proposed_by: Mapped[str] = mapped_column(String(200), nullable=False, default="remediation-agent")
    approved_by_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_by_name: Mapped[str | None] = mapped_column(String(200))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_reason: Mapped[str | None] = mapped_column(Text)
    executor: Mapped[str | None] = mapped_column(String(40))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class RecoveryVerification(Base):
    __tablename__ = "recovery_verifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False, index=True)
    remediation_id: Mapped[int | None] = mapped_column(ForeignKey("remediation_actions.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")  # running | passed | failed
    healthy_samples: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    required_samples: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    last_sample_id: Mapped[int | None] = mapped_column(Integer)
    target_version: Mapped[str | None] = mapped_column(String(120))
    checks: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    baseline: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    detail: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    not_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Postmortem(Base):
    __tablename__ = "postmortems"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False, unique=True)
    provider: Mapped[str] = mapped_column(String(60), nullable=False, default="template")
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    impact: Mapped[str] = mapped_column(Text, nullable=False)
    root_cause: Mapped[str] = mapped_column(Text, nullable=False)
    detection: Mapped[str] = mapped_column(Text, nullable=False)
    resolution: Mapped[str] = mapped_column(Text, nullable=False)
    recovery: Mapped[str] = mapped_column(Text, nullable=False)
    preventive_actions: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    lessons: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class IncidentMemory(Base):
    """Knowledge base of resolved incidents, used as context for future investigations."""

    __tablename__ = "incident_memory"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    incident_id: Mapped[int | None] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), unique=True)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    failed_version: Mapped[str | None] = mapped_column(String(120))
    root_cause: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    recovery_successful: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    signals: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    keywords: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class FeatureFlag(Base):
    """Runtime switch for a code path, evaluated by the application; "disable feature" remediation turns it off."""

    __tablename__ = "feature_flags"
    __table_args__ = (UniqueConstraint("application_id", "key", name="uq_feature_flags_app_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    rollout_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    updated_by: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)

    changes: Mapped[list[FeatureFlagChange]] = relationship(back_populates="flag", cascade="all, delete-orphan", order_by="FeatureFlagChange.changed_at.desc()")


class FeatureFlagChange(Base):
    """History of flag changes; recent changes are correlated with incidents like deployments are."""

    __tablename__ = "feature_flag_changes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    flag_id: Mapped[int] = mapped_column(ForeignKey("feature_flags.id", ondelete="CASCADE"), nullable=False, index=True)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    rollout_percent: Mapped[int] = mapped_column(Integer, nullable=False)
    actor: Mapped[str] = mapped_column(String(200), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, index=True)

    flag: Mapped[FeatureFlag] = relationship(back_populates="changes")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor: Mapped[str] = mapped_column(String(200), nullable=False)
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    target_type: Mapped[str] = mapped_column(String(60), nullable=False)
    target_id: Mapped[str] = mapped_column(String(200), nullable=False)
    detail: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, index=True)
