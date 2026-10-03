"""Grafana Loki HTTP API client (LogQL)."""
from __future__ import annotations

from datetime import datetime, timezone

import httpx

from app.integrations.types import IntegrationError, LogLine, parse_log_line


class LokiClient:
    def __init__(self, base_url: str, tenant_id: str | None = None, bearer_token: str | None = None, timeout: float = 8.0) -> None:
        self.base_url = base_url.rstrip("/")
        headers: dict[str, str] = {}
        if tenant_id:
            headers["X-Scope-OrgID"] = tenant_id
        if bearer_token:
            headers["Authorization"] = f"Bearer {bearer_token}"
        self._client = httpx.Client(timeout=httpx.Timeout(timeout, connect=3.0), headers=headers)

    def close(self) -> None:
        self._client.close()

    def _get(self, path: str, params: dict) -> dict:
        try:
            response = self._client.get(f"{self.base_url}{path}", params=params)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise IntegrationError(f"Loki request failed: {error}") from error
        if payload.get("status") != "success":
            raise IntegrationError("Loki returned an unsuccessful response")
        return payload["data"]

    def query_range(self, logql: str, start: datetime, end: datetime, limit: int = 200, service: str = "") -> list[LogLine]:
        data = self._get("/loki/api/v1/query_range", {
            "query": logql,
            "start": int(start.timestamp() * 1e9),
            "end": int(end.timestamp() * 1e9),
            "limit": limit,
            "direction": "backward",
        })
        lines: list[LogLine] = []
        for stream in data.get("result", []):
            labels = stream.get("stream", {})
            source = labels.get("container") or labels.get("pod") or labels.get("instance") or labels.get("job") or "loki"
            for timestamp_ns, raw in stream.get("values", []):
                timestamp = datetime.fromtimestamp(int(timestamp_ns) / 1e9, tz=timezone.utc)
                lines.append(parse_log_line(raw, timestamp, source, service or labels.get("application", source)))
        lines.sort(key=lambda line: line.timestamp, reverse=True)
        return lines[:limit]

    def count(self, logql_metric: str, at: datetime | None = None) -> float | None:
        params: dict = {"query": logql_metric}
        if at:
            params["time"] = int(at.timestamp() * 1e9)
        data = self._get("/loki/api/v1/query", params)
        result = data.get("result", [])
        if not result:
            return 0.0
        return sum(float(item["value"][1]) for item in result)

    def ready(self) -> bool:
        # The query API is what NexOps depends on; /ready can stay 503 in single-binary mode while queries work.
        try:
            return self._client.get(f"{self.base_url}/loki/api/v1/labels").status_code == 200
        except httpx.HTTPError:
            return False
