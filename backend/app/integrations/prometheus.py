"""Prometheus HTTP API client and the PromQL used for golden signals."""
from __future__ import annotations

import math
from datetime import datetime, timezone

import httpx

from app.integrations.types import IntegrationError

# `$selector` is the application's label selector (e.g. job="payment-api"), `$window` the rate window.
# The defaults follow the common http_requests_total / http_request_duration_seconds conventions and can be
# overridden per application through Application.metric_queries.
DEFAULT_QUERIES: dict[str, str] = {
    "request_rate": "sum(rate(http_requests_total{$selector}[$window]))",
    "error_rate_percent": (
        '100 * (sum(rate(http_requests_total{$selector,status=~"5.."}[$window])) or vector(0))'
        " / clamp_min(sum(rate(http_requests_total{$selector}[$window])), 0.000001)"
    ),
    "p50_latency_ms": "1000 * histogram_quantile(0.50, sum by (le) (rate(http_request_duration_seconds_bucket{$selector}[$window])))",
    "p95_latency_ms": "1000 * histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket{$selector}[$window])))",
    "p99_latency_ms": "1000 * histogram_quantile(0.99, sum by (le) (rate(http_request_duration_seconds_bucket{$selector}[$window])))",
    "cpu_percent": "100 * sum(rate(process_cpu_seconds_total{$selector}[$window]))",
    "business_success_rate": (
        '100 * (sum(rate(business_transactions_total{$selector,result="success"}[$window])) or vector(0))'
        " / clamp_min(sum(rate(business_transactions_total{$selector}[$window])), 0.000001)"
    ),
}


def render_query(template: str, selector: str, window: str) -> str:
    return template.replace("$selector", selector).replace("$window", window)


class PrometheusClient:
    def __init__(self, base_url: str, bearer_token: str | None = None, timeout: float = 6.0) -> None:
        self.base_url = base_url.rstrip("/")
        headers = {"Authorization": f"Bearer {bearer_token}"} if bearer_token else {}
        self._client = httpx.Client(timeout=httpx.Timeout(timeout, connect=3.0), headers=headers)

    def close(self) -> None:
        self._client.close()

    def _get(self, path: str, params: dict) -> dict:
        try:
            response = self._client.get(f"{self.base_url}{path}", params=params)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise IntegrationError(f"Prometheus request failed: {error}") from error
        if payload.get("status") != "success":
            raise IntegrationError(f"Prometheus error: {payload.get('error', 'unknown')}")
        return payload["data"]

    def query(self, promql: str, at: datetime | None = None) -> float | None:
        params: dict = {"query": promql}
        if at:
            params["time"] = at.timestamp()
        data = self._get("/api/v1/query", params)
        result = data.get("result", [])
        if data.get("resultType") == "scalar":
            value = float(result[1])
            return None if math.isnan(value) or math.isinf(value) else value
        if not result:
            return None
        value = float(result[0]["value"][1])
        return None if math.isnan(value) or math.isinf(value) else value

    def query_range(self, promql: str, start: datetime, end: datetime, step_seconds: int) -> list[tuple[datetime, float]]:
        data = self._get("/api/v1/query_range", {"query": promql, "start": start.timestamp(), "end": end.timestamp(), "step": step_seconds})
        result = data.get("result", [])
        if not result:
            return []
        points = []
        for timestamp, value in result[0]["values"]:
            number = float(value)
            if not (math.isnan(number) or math.isinf(number)):
                points.append((datetime.fromtimestamp(float(timestamp), tz=timezone.utc), number))
        return points

    def ready(self) -> bool:
        try:
            response = self._client.get(f"{self.base_url}/-/ready")
            return response.status_code == 200
        except httpx.HTTPError:
            return False
