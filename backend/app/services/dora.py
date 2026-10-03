"""DORA metrics computed from recorded deployments and incidents (no stored aggregates)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Deployment, Incident, as_utc


def _month_start(value: datetime) -> datetime:
    return value.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _add_months(value: datetime, months: int) -> datetime:
    month = value.month - 1 + months
    return value.replace(year=value.year + month // 12, month=month % 12 + 1)


def performance_level(metric: str, value: float | None) -> str | None:
    """Classify against the published DORA performance bands (elite/high/medium/low)."""
    if value is None:
        return None
    if metric == "deployment_frequency_per_day":
        return "elite" if value >= 1 else "high" if value >= 1 / 7 else "medium" if value >= 1 / 30 else "low"
    if metric == "lead_time_hours":
        return "elite" if value < 24 else "high" if value < 24 * 7 else "medium" if value < 24 * 30 else "low"
    if metric == "change_failure_rate":
        return "elite" if value <= 5 else "high" if value <= 10 else "medium" if value <= 15 else "low"
    if metric == "recovery_hours":
        return "elite" if value < 1 else "high" if value < 24 else "medium" if value < 24 * 7 else "low"
    return None


class DoraService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def compute(self, months: int = 6, environment: str | None = "production", application_id: str | None = None) -> dict:
        now = datetime.now(timezone.utc)
        start = _add_months(_month_start(now), -(months - 1))
        query = select(Deployment).where(Deployment.finished_at.is_not(None), Deployment.finished_at >= start)
        incident_query = select(Incident).where(Incident.detected_at >= start - timedelta(days=1))
        if environment:
            query = query.where(Deployment.environment == environment)
        if application_id:
            query = query.where(Deployment.application_id == application_id)
            incident_query = incident_query.where(Incident.application_id == application_id)
        deployments = self.session.scalars(query.order_by(Deployment.finished_at)).all()
        incidents = self.session.scalars(incident_query).all()
        failed_by_deployment = {incident.deployment_id: incident for incident in incidents if incident.deployment_correlated and incident.deployment_id}

        periods = []
        for index in range(months):
            period_start = _add_months(start, index)
            period_end = _add_months(period_start, 1)
            in_period = [item for item in deployments if period_start <= as_utc(item.finished_at) < period_end]
            successful = [item for item in in_period if item.ci_status == "success"]
            releases = [item for item in successful if item.kind in {"release", "hotfix"}]
            rework = [item for item in successful if self._is_rework(item, incidents)]
            failed = [item for item in releases if item.id in failed_by_deployment or item.status in {"degraded", "rolled_back"}]
            lead_times = [
                (as_utc(item.finished_at) - as_utc(item.commit_timestamp)).total_seconds() / 60
                for item in releases if item.commit_timestamp and item.finished_at and as_utc(item.finished_at) >= as_utc(item.commit_timestamp)
            ]
            recovery_times = []
            for item in failed:
                incident = failed_by_deployment.get(item.id)
                if incident and incident.resolved_at:
                    recovery_times.append((as_utc(incident.resolved_at) - as_utc(item.finished_at)).total_seconds() / 60)
            days = max(1, min((period_end - period_start).days, (now - period_start).days + 1))
            periods.append({
                "month": period_start.strftime("%b"),
                "period_start": period_start.date().isoformat(),
                "deployments": len(releases),
                "deployments_per_day": round(len(releases) / days, 3),
                "lead_time_minutes": round(median(lead_times), 1) if lead_times else None,
                "change_failure_rate_percent": round(len(failed) / len(releases) * 100, 1) if releases else None,
                "recovery_time_minutes": round(median(recovery_times), 1) if recovery_times else None,
                "rework_rate_percent": round(len(rework) / len(successful) * 100, 1) if successful else None,
                "failed_deployments": len(failed),
                "rework_deployments": len(rework),
                "total_deployments": len(successful),
            })

        window_start = now - timedelta(days=30)
        recent = [item for item in deployments if as_utc(item.finished_at) >= window_start and item.ci_status == "success"]
        recent_releases = [item for item in recent if item.kind in {"release", "hotfix"}]
        recent_failed = [item for item in recent_releases if item.id in failed_by_deployment or item.status in {"degraded", "rolled_back"}]
        recent_lead = [
            (as_utc(item.finished_at) - as_utc(item.commit_timestamp)).total_seconds() / 60
            for item in recent_releases if item.commit_timestamp and as_utc(item.finished_at) >= as_utc(item.commit_timestamp)
        ]
        recent_recovery = [
            (as_utc(failed_by_deployment[item.id].resolved_at) - as_utc(item.finished_at)).total_seconds() / 60
            for item in recent_failed if item.id in failed_by_deployment and failed_by_deployment[item.id].resolved_at
        ]
        recent_rework = [item for item in recent if self._is_rework(item, incidents)]
        summary = {
            "window_days": 30,
            "deployments": len(recent_releases),
            "deployments_per_day": round(len(recent_releases) / 30, 3),
            "lead_time_minutes": round(median(recent_lead), 1) if recent_lead else None,
            "change_failure_rate_percent": round(len(recent_failed) / len(recent_releases) * 100, 1) if recent_releases else None,
            "recovery_time_minutes": round(median(recent_recovery), 1) if recent_recovery else None,
            "rework_rate_percent": round(len(recent_rework) / len(recent) * 100, 1) if recent else None,
        }
        summary["levels"] = {
            "deployment_frequency": performance_level("deployment_frequency_per_day", summary["deployments_per_day"] if recent_releases else None),
            "lead_time": performance_level("lead_time_hours", summary["lead_time_minutes"] / 60 if summary["lead_time_minutes"] is not None else None),
            "change_failure_rate": performance_level("change_failure_rate", summary["change_failure_rate_percent"]),
            "recovery_time": performance_level("recovery_hours", summary["recovery_time_minutes"] / 60 if summary["recovery_time_minutes"] is not None else None),
        }
        return {"periods": periods, "summary": summary, "environment": environment, "application_id": application_id}

    @staticmethod
    def _is_rework(deployment: Deployment, incidents: list[Incident]) -> bool:
        """Unplanned deployments made because of a production incident: rollbacks, hotfixes, or releases shipped while one was open."""
        if deployment.kind in {"rollback", "hotfix"}:
            return True
        finished = as_utc(deployment.finished_at)
        for incident in incidents:
            if incident.application_id != deployment.application_id or incident.deployment_id == deployment.id:
                continue
            detected = as_utc(incident.detected_at)
            resolved = as_utc(incident.resolved_at) or datetime.now(timezone.utc)
            if detected <= finished <= resolved + timedelta(hours=1):
                return True
        return False
