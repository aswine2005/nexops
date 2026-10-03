"""Canary analysis for progressive delivery: Argo Rollouts asks NexOps whether the canary pods are healthy.

The canary and stable pods are told apart by the rollouts_pod_template_hash label Prometheus copies from the pods.
Each answer is recorded as a canary_analysis agent run on the release, and a failing canary sends an alert.
"""
from __future__ import annotations

import re
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.ids import deployment_key
from app.detection.engine import Thresholds
from app.integrations.prometheus import DEFAULT_QUERIES, render_query
from app.integrations.registry import Integrations
from app.integrations.types import IntegrationError
from app.models import Application, Deployment, utc_now
from app.services import notifications
from app.services.common import agent_run

HASH = re.compile(r"^[a-z0-9]{1,24}$")
WINDOW = re.compile(r"^[1-9][0-9]{0,2}[smh]$")
METRICS = ("request_rate", "error_rate_percent", "p99_latency_ms", "business_success_rate")
MIN_REQUESTS = 20  # fewer canary requests than this in the window is not enough evidence either way


def _seconds(window: str) -> int:
    return int(window[:-1]) * {"s": 1, "m": 60, "h": 3600}[window[-1]]


def _measure(integrations: Integrations, app: Application, pod_hash: str, window: str, errors: list[str]) -> dict:
    selector = f'{app.prometheus_selector},rollouts_pod_template_hash="{pod_hash}"'
    values: dict = {}
    for key in METRICS:
        template = (app.metric_queries or {}).get(key) or DEFAULT_QUERIES[key]
        try:
            value = integrations.prometheus.query(render_query(template, selector, window))
        except IntegrationError as error:
            errors.append(str(error)[:200])
            value = None
        values[key] = round(value, 3) if value is not None else None
    return values


def _release(session: Session, app: Application, version: str | None) -> Deployment | None:
    """The release being rolled out: the newest one for this version, else the newest still in progress."""
    recent = select(Deployment).where(Deployment.application_id == app.id, Deployment.started_at >= utc_now() - timedelta(hours=6)).order_by(Deployment.started_at.desc())
    if version:
        match = session.scalars(recent.where(Deployment.version == version).limit(1)).first()
        if match:
            return match
    return session.scalars(recent.where(Deployment.status.in_(("in_progress", "verifying"))).limit(1)).first()


def verdict(canary: dict, stable: dict, thresholds: Thresholds, window: str) -> tuple[str, list[str], dict]:
    requests = (canary.get("request_rate") or 0) * _seconds(window)
    stable_error = stable.get("error_rate_percent") or 0.0
    stable_p99 = stable.get("p99_latency_ms")
    limits = {
        # Stricter than incident detection: a canary must not be measurably worse than stable.
        "error_rate_percent": round(min(thresholds.error_rate_percent, max(1.0, stable_error + 1.0)), 2),
        "p99_latency_ms": round(min(thresholds.p99_latency_ms, max(stable_p99 * thresholds.latency_regression_factor, stable_p99 + 100)) if stable_p99 else thresholds.p99_latency_ms, 1),
        "business_success_rate": thresholds.business_success_rate_min,
        "min_requests": MIN_REQUESTS,
    }
    if requests < MIN_REQUESTS:
        return "inconclusive", [f"Only {requests:.0f} canary requests in {window}; need {MIN_REQUESTS}."], limits
    reasons = []
    if (canary.get("error_rate_percent") or 0) > limits["error_rate_percent"]:
        reasons.append(f"Error rate {canary['error_rate_percent']:.2f}% > {limits['error_rate_percent']}% (stable {stable_error:.2f}%)")
    if canary.get("p99_latency_ms") is not None and canary["p99_latency_ms"] > limits["p99_latency_ms"]:
        reasons.append(f"P99 latency {canary['p99_latency_ms']:.0f} ms > {limits['p99_latency_ms']:.0f} ms (stable {stable_p99 or 0:.0f} ms)")
    if canary.get("business_success_rate") is not None and canary["business_success_rate"] < limits["business_success_rate"]:
        reasons.append(f"Business success {canary['business_success_rate']:.1f}% < {limits['business_success_rate']}%")
    return ("fail" if reasons else "pass"), reasons, limits


def analyze(session: Session, integrations: Integrations, app: Application, canary_hash: str, stable_hash: str | None, window: str = "1m") -> dict:
    if not HASH.match(canary_hash) or (stable_hash and not HASH.match(stable_hash)):
        raise ValueError("Pod template hashes must be lowercase alphanumeric.")
    if not WINDOW.match(window):
        raise ValueError("window must look like 30s, 1m or 5m.")
    result: dict = {"application": app.id, "window": window, "canary_hash": canary_hash, "stable_hash": stable_hash}
    if integrations.prometheus is None or not app.prometheus_selector:
        return {**result, "verdict": "inconclusive", "reasons": ["Prometheus is not configured for this application."]}

    rollout = None
    if app.runtime == "kubernetes" and integrations.kubernetes:
        try:
            rollout = integrations.kubernetes.state(app).rollout
        except IntegrationError:
            rollout = None
    canary_version = (rollout or {}).get("canary_version")
    release = _release(session, app, canary_version)
    errors: list[str] = []
    canary = _measure(integrations, app, canary_hash, window, errors)
    stable = _measure(integrations, app, stable_hash, window, errors) if stable_hash and stable_hash != canary_hash else {}
    outcome, reasons, limits = verdict(canary, stable, Thresholds.from_overrides(app.thresholds), window)
    if errors and outcome == "pass" and canary.get("error_rate_percent") is None:
        outcome, reasons = "inconclusive", errors[:2]
    weight = (rollout or {}).get("canary_weight")
    result.update({
        "verdict": outcome, "reasons": reasons, "canary": canary, "stable": stable, "limits": limits,
        "canary_version": canary_version, "stable_version": (rollout or {}).get("stable_version"), "canary_weight": weight,
        "deployment_id": deployment_key(release.id) if release else None, "measured_at": utc_now().isoformat(),
    })

    label = f"{canary_version or canary_hash}" + (f" at {weight}%" if weight is not None else "")
    summary = f"Canary {label}: {outcome}" + (f" — {'; '.join(reasons)}" if reasons else f" (error {canary.get('error_rate_percent') or 0:.2f}%, p99 {canary.get('p99_latency_ms') or 0:.0f} ms)")
    with agent_run(session, "canary_analysis", deployment_id=release.id if release else None, provider="prometheus") as run:
        run.complete(summary, result)  # a failing canary is a successful analysis; the verdict is in the output
    if outcome == "fail":
        notifications.queue(
            session, "canary.failed", f"Canary {label} failed on {app.name}",
            f"{'; '.join(reasons)}. Argo Rollouts aborts the rollout; stable keeps serving.",
            f"/deployments/{deployment_key(release.id)}" if release else f"/applications/{app.id}",
        )
    return result
