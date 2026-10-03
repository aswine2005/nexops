"""Jaeger query API client (works with Jaeger v1 and v2): error and slow traces as investigation evidence."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx

from app.integrations.types import IntegrationError


def _tags(items: list[dict] | None) -> dict:
    return {item.get("key"): item.get("value") for item in (items or [])}


class JaegerClient:
    def __init__(self, base_url: str, timeout: float = 8.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(timeout=httpx.Timeout(timeout, connect=3.0))

    def close(self) -> None:
        self._client.close()

    def traces(self, service: str, start: datetime, end: datetime, limit: int = 20, tags: dict | None = None, min_duration: str | None = None) -> list[dict]:
        params: dict = {
            "service": service,
            "start": int(start.timestamp() * 1_000_000),
            "end": int(end.timestamp() * 1_000_000),
            "limit": limit,
        }
        if tags:
            params["tags"] = json.dumps(tags)
        if min_duration:
            params["minDuration"] = min_duration
        try:
            response = self._client.get(f"{self.base_url}/api/traces", params=params)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise IntegrationError(f"Jaeger query failed: {error}") from error
        return [self.summarize(trace) for trace in payload.get("data") or []]

    def ready(self) -> bool:
        try:
            return self._client.get(f"{self.base_url}/api/services").status_code == 200
        except httpx.HTTPError:
            return False

    @staticmethod
    def summarize(trace: dict) -> dict:
        """Reduce a Jaeger trace to its root operation, duration, failing spans and the slowest operations."""
        spans = trace.get("spans") or []
        ids = {span.get("spanID") for span in spans}
        roots = [span for span in spans if not any(ref.get("spanID") in ids for ref in span.get("references") or [])]
        root = min(roots or spans, key=lambda span: span.get("startTime", 0)) if spans else {}
        errors = []
        by_operation: dict[str, dict] = {}
        for span in spans:
            tags = _tags(span.get("tags"))
            failed = tags.get("error") in (True, "true") or str(tags.get("otel.status_code", "")).upper() == "ERROR"
            operation = span.get("operationName", "?")
            stats = by_operation.setdefault(operation, {"operation": operation, "count": 0, "total_ms": 0.0})
            stats["count"] += 1
            stats["total_ms"] += span.get("duration", 0) / 1000
            if failed:
                message = tags.get("otel.status_description") or tags.get("error.message")
                for log in span.get("logs") or []:
                    fields = _tags(log.get("fields"))
                    message = message or fields.get("exception.message") or fields.get("message")
                generic = not message or operation.endswith((" http send", " http receive"))
                errors.append({"operation": operation, "message": str(message or "error")[:300], "flag": tags.get("feature_flag.key"), "_rank": (generic, span is root)})
        # Most informative failure first: a real error message beats ASGI transport spans and the root span.
        errors = [{"operation": item["operation"], "message": item["message"], **({"flag": item["flag"]} if item["flag"] else {})} for item in sorted(errors, key=lambda item: item["_rank"])]
        root_tags = _tags(root.get("tags"))
        start = root.get("startTime")
        return {
            "trace_id": trace.get("traceID"),
            "root": root.get("operationName"),
            "duration_ms": round(root.get("duration", 0) / 1000, 1),
            "status_code": root_tags.get("http.status_code") or root_tags.get("http.response.status_code"),
            "error": bool(errors),
            "errors": errors,
            "span_count": len(spans),
            "slowest": sorted(by_operation.values(), key=lambda item: item["total_ms"], reverse=True)[:3],
            "start": datetime.fromtimestamp(start / 1_000_000, tz=timezone.utc).isoformat() if start else None,
        }
