"""Model → API dictionaries. Keeps response shapes in one place for the frontend."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.ids import deployment_key, incident_key, iso
from app.models import (
    Application, Deployment, Incident, Investigation, MetricSample, Policy, Postmortem, RecoveryVerification, RemediationAction, User, as_utc,
)
from app.services.detection import ACTIVE_STATUSES


def sample_dict(sample: MetricSample | None) -> dict | None:
    if sample is None:
        return None
    runtime = sample.runtime_state or {}
    return {
        "observed_at": iso(sample.observed_at),
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
        "health_checks_total": sample.health_checks_total,
        "version": runtime.get("version"),
        "sources": sample.sources,
        "errors": sample.errors,
    }


def user_dict(user: User) -> dict:
    return {
        "id": str(user.id), "email": user.email, "display_name": user.display_name, "role": user.role,
        "is_active": user.is_active, "created_at": iso(user.created_at), "last_login_at": iso(user.last_login_at),
    }


def application_config(app: Application) -> dict:
    return {
        "id": app.id, "name": app.name, "description": app.description, "owner": app.owner, "environment": app.environment,
        "repository": app.repository, "runtime": app.runtime, "monitoring_enabled": app.monitoring_enabled,
        "prometheus_selector": app.prometheus_selector, "metric_queries": app.metric_queries or {},
        "loki_selector": app.loki_selector, "cloudwatch_log_group": app.cloudwatch_log_group, "tracing_service": app.tracing_service,
        "health_checks": app.health_checks or [], "docker_container": app.docker_container,
        "k8s_namespace": app.k8s_namespace, "k8s_deployment": app.k8s_deployment, "k8s_container": app.k8s_container,
        "ec2_instance_id": app.ec2_instance_id, "image_repository": app.image_repository,
        "rollback_strategy": app.rollback_strategy, "github_workflow": app.github_workflow, "github_ref": app.github_ref,
        "thresholds": app.thresholds or {}, "dependencies": app.dependencies or [],
    }


class Serializer:
    def __init__(self, session: Session) -> None:
        self.session = session

    def latest_sample(self, application_id: str) -> MetricSample | None:
        return self.session.scalars(
            select(MetricSample).where(MetricSample.application_id == application_id).order_by(MetricSample.observed_at.desc()).limit(1)
        ).first()

    def spark(self, application_id: str, field: str = "error_rate_percent", points: int = 20) -> list[float]:
        rows = self.session.scalars(
            select(MetricSample).where(MetricSample.application_id == application_id).order_by(MetricSample.observed_at.desc()).limit(points)
        ).all()
        return [round(getattr(row, field) or 0, 3) for row in reversed(rows)]

    def active_incident(self, application_id: str) -> Incident | None:
        return self.session.scalars(
            select(Incident).where(Incident.application_id == application_id, Incident.status.in_(ACTIVE_STATUSES)).order_by(Incident.id.desc()).limit(1)
        ).first()

    def last_deployment(self, application_id: str) -> Deployment | None:
        return self.session.scalars(
            select(Deployment).where(Deployment.application_id == application_id).order_by(Deployment.started_at.desc()).limit(1)
        ).first()

    def application(self, app: Application, detail: bool = False) -> dict:
        latest = self.latest_sample(app.id)
        active = self.active_incident(app.id)
        last = self.last_deployment(app.id)
        data = {
            **application_config(app),
            "current_version": app.current_version,
            "previous_version": app.previous_version,
            "status": app.status,
            "status_reason": app.status_reason,
            "deploy_frozen": app.deploy_frozen,
            "deploy_freeze_reason": app.deploy_freeze_reason,
            "last_checked_at": iso(app.last_checked_at),
            "latest": sample_dict(latest),
            "sparkline": self.spark(app.id),
            "last_deployment": self.deployment_brief(last) if last else None,
            "active_incident": self.incident_brief(active) if active else None,
            "created_at": iso(app.created_at),
        }
        if detail:
            runtime = (latest.runtime_state if latest else None) or {}
            data["runtime_state"] = {key: value for key, value in runtime.items() if key != "health_checks"} or None
            data["health_check_results"] = runtime.get("health_checks", [])
        return data

    @staticmethod
    def deployment_brief(deployment: Deployment) -> dict:
        return {
            "id": deployment_key(deployment.id), "version": deployment.version, "status": deployment.status,
            "kind": deployment.kind, "finished_at": iso(deployment.finished_at), "started_at": iso(deployment.started_at),
        }

    @staticmethod
    def incident_brief(incident: Incident) -> dict:
        return {"id": incident_key(incident.id), "title": incident.title, "severity": incident.severity, "status": incident.status, "detected_at": iso(incident.detected_at)}

    def deployment(self, deployment: Deployment, include_changes: bool = False) -> dict:
        incident = self.session.scalars(select(Incident).where(Incident.deployment_id == deployment.id).order_by(Incident.id.desc()).limit(1)).first()
        lead_time = None
        if deployment.commit_timestamp and deployment.finished_at:
            lead_time = round((as_utc(deployment.finished_at) - as_utc(deployment.commit_timestamp)).total_seconds() / 60, 1)
        data = {
            "id": deployment_key(deployment.id),
            "number": deployment.id,
            "application_id": deployment.application_id,
            "application_name": deployment.application.name if deployment.application else deployment.application_id,
            "environment": deployment.environment,
            "version": deployment.version,
            "previous_version": deployment.previous_version,
            "kind": deployment.kind,
            "source": deployment.source,
            "status": deployment.status,
            "status_reason": deployment.status_reason,
            "ci_status": deployment.ci_status,
            "ci_run_id": deployment.ci_run_id,
            "ci_run_url": deployment.ci_run_url,
            "commit_sha": deployment.commit_sha,
            "commit_message": deployment.commit_message,
            "branch": deployment.branch,
            "author": deployment.author,
            "repository": deployment.repository,
            "image": deployment.image,
            "pipeline": deployment.pipeline or [],
            "risk_score": deployment.risk_score,
            "risk_level": deployment.risk_level,
            "risk_factors": deployment.risk_factors or [],
            "files_changed": deployment.files_changed,
            "additions": deployment.additions,
            "deletions": deployment.deletions,
            "summary": deployment.summary,
            "started_at": iso(deployment.started_at),
            "finished_at": iso(deployment.finished_at),
            "verified_at": iso(deployment.verified_at),
            "lead_time_minutes": lead_time,
            "incident": self.incident_brief(incident) if incident else None,
        }
        if include_changes:
            data["changes"] = [
                {"file": change.file_path, "change_type": change.change_type, "additions": change.additions, "deletions": change.deletions, "risk_tag": change.risk_tag, "patch": change.patch}
                for change in deployment.changes
            ]
        return data

    def incident(self, incident: Incident, include_timeline: bool = True) -> dict:
        deployment = incident.deployment
        end = as_utc(incident.resolved_at)
        duration = None
        if end:
            duration = int((end - as_utc(incident.detected_at)).total_seconds())
        data = {
            "id": incident_key(incident.id),
            "number": incident.id,
            "application_id": incident.application_id,
            "application_name": incident.application.name if incident.application else incident.application_id,
            "environment": incident.application.environment if incident.application else None,
            "deployment": {
                "id": deployment_key(deployment.id), "version": deployment.version, "previous_version": deployment.previous_version,
                "commit_sha": deployment.commit_sha, "author": deployment.author, "status": deployment.status, "finished_at": iso(deployment.finished_at),
            } if deployment else None,
            "title": incident.title,
            "severity": incident.severity,
            "status": incident.status,
            "summary": incident.summary,
            "signals": incident.signals or [],
            "baseline": incident.baseline or {},
            "observed": incident.observed or {},
            "peak": incident.peak or {},
            "deployment_correlated": incident.deployment_correlated,
            "root_cause": incident.root_cause,
            "confidence": incident.confidence,
            "recommended_action": incident.recommended_action,
            "resolution_summary": incident.resolution_summary,
            "detected_at": iso(incident.detected_at),
            "acknowledged_at": iso(incident.acknowledged_at),
            "mitigated_at": iso(incident.mitigated_at),
            "resolved_at": iso(incident.resolved_at),
            "duration_seconds": duration,
        }
        if include_timeline:
            data["timeline"] = [
                {"time": iso(event.event_at), "kind": event.kind, "title": event.title, "detail": event.detail, "severity": event.severity, "actor": event.actor}
                for event in sorted(incident.events, key=lambda event: (as_utc(event.event_at), event.id))
            ]
        return data

    @staticmethod
    def investigation(investigation: Investigation | None) -> dict | None:
        if investigation is None:
            return None
        return {
            "id": investigation.id, "status": investigation.status, "provider": investigation.provider, "model": investigation.model,
            "root_cause": investigation.root_cause, "confidence": investigation.confidence, "reasoning_summary": investigation.reasoning_summary,
            "recommended_action": investigation.recommended_action, "recommended_flag": investigation.recommended_flag, "recommendation_detail": investigation.recommendation_detail,
            "alternative_hypotheses": investigation.alternative_hypotheses or [], "next_steps": investigation.next_steps or [],
            "evidence": investigation.evidence or [], "cited_evidence_ids": investigation.cited_evidence_ids or [],
            "error": investigation.error, "started_at": iso(investigation.started_at), "completed_at": iso(investigation.completed_at),
        }

    @staticmethod
    def remediation(remediation: RemediationAction) -> dict:
        return {
            "id": remediation.id, "action": remediation.action, "target_version": remediation.target_version, "target_image": remediation.target_image, "target_flag": remediation.target_flag,
            "status": remediation.status, "mode": remediation.mode, "policy_id": remediation.policy_id, "policy_reasons": remediation.policy_reasons or [],
            "reason": remediation.reason, "proposed_by": remediation.proposed_by, "approved_by": remediation.approved_by_name,
            "approved_at": iso(remediation.approved_at), "rejected_reason": remediation.rejected_reason, "executor": remediation.executor,
            "started_at": iso(remediation.started_at), "completed_at": iso(remediation.completed_at), "outcome": remediation.outcome or {},
            "error": remediation.error, "created_at": iso(remediation.created_at),
        }

    @staticmethod
    def verification(verification: RecoveryVerification | None) -> dict | None:
        if verification is None:
            return None
        return {
            "id": verification.id, "status": verification.status, "healthy_samples": verification.healthy_samples,
            "required_samples": verification.required_samples, "checks": verification.checks or [], "baseline": verification.baseline or {},
            "target_version": verification.target_version, "detail": verification.detail, "started_at": iso(verification.started_at),
            "not_before": iso(verification.not_before), "deadline_at": iso(verification.deadline_at), "finished_at": iso(verification.finished_at),
        }

    @staticmethod
    def postmortem(postmortem: Postmortem | None) -> dict | None:
        if postmortem is None:
            return None
        return {
            "provider": postmortem.provider, "summary": postmortem.summary, "impact": postmortem.impact, "root_cause": postmortem.root_cause,
            "detection": postmortem.detection, "resolution": postmortem.resolution, "recovery": postmortem.recovery,
            "preventive_actions": postmortem.preventive_actions or [], "lessons": postmortem.lessons or [], "metrics": postmortem.metrics or {},
            "generated_at": iso(postmortem.generated_at),
        }

    @staticmethod
    def policy(policy: Policy) -> dict:
        return {
            "id": policy.id, "name": policy.name, "description": policy.description, "action": policy.action, "environment": policy.environment,
            "application_id": policy.application_id, "mode": policy.mode, "enabled": policy.enabled, "conditions": policy.conditions or {},
            "created_at": iso(policy.created_at), "updated_at": iso(policy.updated_at),
        }
