"""Provider-neutral data shapes returned by integration adapters."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

_LEVEL = re.compile(r"\b(FATAL|CRITICAL|ERROR|ERR|WARN(?:ING)?|INFO|DEBUG|TRACE)\b", re.IGNORECASE)
_LEVEL_MAP = {"fatal": "ERROR", "critical": "ERROR", "error": "ERROR", "err": "ERROR", "warn": "WARN", "warning": "WARN", "info": "INFO", "debug": "DEBUG", "trace": "DEBUG"}

DB_ERROR_PATTERN = re.compile(
    r"econnrefused|connection refused|could not connect|database connection|connection to server|"
    r"connection timed out|too many connections|operationalerror|database .*unavailable|db (?:ping|connection) failed",
    re.IGNORECASE,
)


@dataclass
class LogLine:
    timestamp: datetime
    level: str
    message: str
    source: str
    service: str
    trace_id: str | None = None
    raw: str = ""

    def as_dict(self) -> dict:
        data = asdict(self)
        data["timestamp"] = self.timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        data.pop("raw", None)
        return data


def parse_log_line(raw: str, timestamp: datetime, source: str, service: str) -> LogLine:
    """Parse JSON or plain-text log lines into a LogLine with a normalised level."""
    text = raw.strip()
    level = None
    message = text
    trace_id = None
    if text.startswith("{"):
        try:
            payload = json.loads(text)
            if isinstance(payload, dict):
                raw_level = payload.get("level") or payload.get("levelname") or payload.get("severity") or payload.get("lvl")
                if isinstance(raw_level, str):
                    level = _LEVEL_MAP.get(raw_level.lower(), raw_level.upper())
                message = str(payload.get("msg") or payload.get("message") or payload.get("event") or text)
                error = payload.get("error") or payload.get("exc_info") or payload.get("exception")
                if error:
                    message = f"{message} · {error}"
                trace_id = payload.get("trace_id") or payload.get("traceId") or payload.get("request_id")
        except json.JSONDecodeError:
            pass
    if level is None:
        match = _LEVEL.search(text)
        level = _LEVEL_MAP.get(match.group(1).lower(), "INFO") if match else "INFO"
    return LogLine(timestamp=timestamp, level=level, message=message[:4000], source=source, service=service, trace_id=str(trace_id) if trace_id else None, raw=text[:4000])


@dataclass
class Replica:
    name: str
    status: str
    ready: bool
    restarts: int = 0
    image: str | None = None
    version: str | None = None
    node: str | None = None
    started_at: str | None = None
    reason: str | None = None
    exit_code: int | None = None
    cpu_percent: float | None = None
    memory_percent: float | None = None
    track: str | None = None  # Argo Rollouts: "stable" | "canary"


@dataclass
class RuntimeState:
    runtime: str
    target: str
    replicas: list[Replica] = field(default_factory=list)
    desired_replicas: int | None = None
    events: list[dict] = field(default_factory=list)
    image: str | None = None
    version: str | None = None
    rollout: dict | None = None  # Argo Rollouts progress (phase, step, canary weight/version)

    @property
    def healthy_replicas(self) -> int:
        return sum(1 for replica in self.replicas if replica.ready)

    @property
    def total_replicas(self) -> int:
        return max(self.desired_replicas or 0, len(self.replicas))

    @property
    def restarts(self) -> int:
        return sum(replica.restarts for replica in self.replicas)

    @property
    def crashlooping(self) -> int:
        return sum(1 for replica in self.replicas if (replica.reason or "").lower() in {"crashloopbackoff", "restarting", "error", "oomkilled"})

    def _avg(self, attribute: str) -> float | None:
        values = [getattr(replica, attribute) for replica in self.replicas if getattr(replica, attribute) is not None]
        return round(sum(values) / len(values), 2) if values else None

    @property
    def cpu_percent(self) -> float | None:
        return self._avg("cpu_percent")

    @property
    def memory_percent(self) -> float | None:
        return self._avg("memory_percent")

    def as_dict(self) -> dict:
        return {
            "runtime": self.runtime,
            "target": self.target,
            "image": self.image,
            "version": self.version,
            "desired_replicas": self.desired_replicas,
            "healthy_replicas": self.healthy_replicas,
            "total_replicas": self.total_replicas,
            "restarts": self.restarts,
            "crashlooping": self.crashlooping,
            "replicas": [asdict(replica) for replica in self.replicas],
            "events": self.events[:30],
            **({"rollout": self.rollout} if self.rollout else {}),
        }


class IntegrationError(RuntimeError):
    """Raised when a configured provider is unreachable or returns an unusable response."""
