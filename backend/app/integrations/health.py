"""HTTP health-check probes for application endpoints."""
from __future__ import annotations

import time

import httpx


def probe(url: str, name: str | None = None, timeout: float = 4.0) -> dict:
    started = time.perf_counter()
    try:
        response = httpx.get(url, timeout=timeout, follow_redirects=True)
        latency = round((time.perf_counter() - started) * 1000)
        passing = 200 <= response.status_code < 400
        return {
            "name": name or url,
            "url": url,
            "status": "passing" if passing else "failing",
            "http_status": response.status_code,
            "latency_ms": latency,
            "error": None if passing else response.text[:300],
        }
    except httpx.HTTPError as error:
        return {
            "name": name or url,
            "url": url,
            "status": "failing",
            "http_status": None,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "error": f"{error.__class__.__name__}: {error}"[:300],
        }


def probe_all(checks: list[dict]) -> list[dict]:
    return [probe(check["url"], check.get("name")) for check in checks if check.get("url")]
