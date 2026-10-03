"""Deterministic production-signal detection. Rules detect symptoms; they never claim a root cause."""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from statistics import median

SEVERITY_ORDER = {"info": 0, "warning": 1, "high": 2, "critical": 3}


@dataclass(frozen=True)
class Thresholds:
    error_rate_percent: float = 5.0
    critical_error_rate_percent: float = 10.0
    p99_latency_ms: float = 1000.0
    high_p99_latency_ms: float = 1500.0
    latency_regression_factor: float = 2.5
    cpu_percent: float = 85.0
    high_cpu_percent: float = 95.0
    memory_percent: float = 85.0
    high_memory_percent: float = 95.0
    restart_count: int = 3
    high_restart_count: int = 10
    db_error_count: int = 5
    high_db_error_count: int = 50
    business_success_rate_min: float = 95.0
    min_request_rate: float = 0.05

    @classmethod
    def from_overrides(cls, overrides: dict | None) -> "Thresholds":
        allowed = {item.name for item in fields(cls)}
        values = {key: value for key, value in (overrides or {}).items() if key in allowed and value is not None}
        return cls(**values)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Finding:
    code: str
    title: str
    detail: str
    severity: str
    observed_value: float
    threshold: float
    baseline_value: float | None = None

    def as_dict(self) -> dict:
        return asdict(self)


BASELINE_KEYS = ("request_rate", "error_rate_percent", "p50_latency_ms", "p95_latency_ms", "p99_latency_ms", "cpu_percent", "memory_percent", "db_errors", "business_success_rate")


def compute_baseline(samples: list[dict]) -> dict:
    """Median of each signal across healthy reference samples."""
    baseline: dict = {}
    for key in BASELINE_KEYS:
        values = [float(sample[key]) for sample in samples if sample.get(key) is not None]
        if values:
            baseline[key] = round(median(values), 3)
    baseline["samples"] = len(samples)
    return baseline


def _fmt(value: float | None, unit: str) -> str:
    if value is None:
        return "n/a"
    if unit == "ms":
        return f"{value / 1000:.2f}s" if value >= 1000 else f"{value:.0f}ms"
    if unit == "%":
        return f"{value:.2f}%" if value < 1 else f"{value:.1f}%"
    return f"{value:g}"


class DetectionEngine:
    def __init__(self, thresholds: Thresholds | None = None) -> None:
        self.thresholds = thresholds or Thresholds()

    def evaluate(self, sample: dict, baseline: dict | None = None) -> list[Finding]:
        rules = self.thresholds
        baseline = baseline or {}
        findings: list[Finding] = []

        def add(code: str, title: str, detail: str, severity: str, value: float, threshold: float, base: float | None = None) -> None:
            findings.append(Finding(code, title, detail, severity, round(float(value), 3), float(threshold), base))

        traffic = sample.get("request_rate")
        has_traffic = traffic is None or traffic >= rules.min_request_rate

        error_rate = sample.get("error_rate_percent")
        if error_rate is not None and has_traffic and error_rate >= rules.error_rate_percent:
            base = baseline.get("error_rate_percent")
            severity = "critical" if error_rate >= rules.critical_error_rate_percent else "high"
            change = f" (baseline {_fmt(base, '%')})" if base is not None else ""
            add("error_rate", "Error rate elevated", f"HTTP 5xx error rate is {_fmt(error_rate, '%')}{change}, above the {rules.error_rate_percent:g}% threshold.", severity, error_rate, rules.error_rate_percent, base)

        p99 = sample.get("p99_latency_ms")
        if p99 is not None and has_traffic:
            base = baseline.get("p99_latency_ms")
            regression = base is not None and base > 0 and p99 >= base * rules.latency_regression_factor and p99 - base >= 250
            if p99 >= rules.p99_latency_ms or regression:
                severity = "high" if p99 >= rules.high_p99_latency_ms else "warning"
                reason = f"above the {rules.p99_latency_ms:g}ms threshold" if p99 >= rules.p99_latency_ms else f"{p99 / base:.1f}x the baseline"
                change = f" (baseline {_fmt(base, 'ms')})" if base is not None else ""
                add("p99_latency", "P99 latency elevated", f"P99 latency is {_fmt(p99, 'ms')}{change}, {reason}.", severity, p99, rules.p99_latency_ms, base)

        cpu = sample.get("cpu_percent")
        if cpu is not None and cpu >= rules.cpu_percent:
            severity = "high" if cpu >= rules.high_cpu_percent else "warning"
            add("cpu_saturation", "CPU saturation", f"CPU usage is {cpu:.0f}% (threshold {rules.cpu_percent:g}%).", severity, cpu, rules.cpu_percent, baseline.get("cpu_percent"))

        memory = sample.get("memory_percent")
        if memory is not None and memory >= rules.memory_percent:
            severity = "high" if memory >= rules.high_memory_percent else "warning"
            add("memory_saturation", "Memory saturation", f"Memory usage is {memory:.0f}% (threshold {rules.memory_percent:g}%).", severity, memory, rules.memory_percent, baseline.get("memory_percent"))

        healthy, total = sample.get("healthy_replicas"), sample.get("total_replicas")
        if healthy is not None and total and healthy < total:
            severity = "critical" if healthy == 0 else "high"
            add("replica_availability", "Replica availability reduced", f"Only {healthy} of {total} replicas are healthy.", severity, healthy, total)

        crashlooping = sample.get("crashlooping")
        if crashlooping:
            add("crash_loop", "Replicas crash looping", f"{crashlooping} replica(s) are restarting repeatedly (CrashLoopBackOff/restart loop).", "high", crashlooping, 0)

        restarts = sample.get("restarts")
        if restarts is not None and restarts >= rules.restart_count:
            severity = "high" if restarts >= rules.high_restart_count else "warning"
            add("restarts", "Container restarts increased", f"Current replicas restarted {restarts} times (threshold {rules.restart_count}).", severity, restarts, rules.restart_count)

        db_errors = sample.get("db_errors")
        if db_errors is not None and db_errors >= rules.db_error_count:
            severity = "high" if db_errors >= rules.high_db_error_count else "warning"
            add("database_errors", "Database connection errors", f"{db_errors} database connection errors in the last window (threshold {rules.db_error_count}).", severity, db_errors, rules.db_error_count, baseline.get("db_errors"))

        failed_checks = sample.get("failed_health_checks")
        if failed_checks:
            add("health_check", "Health checks failing", f"{failed_checks} health check endpoint(s) are failing.", "high", failed_checks, 0)

        business = sample.get("business_success_rate")
        if business is not None and has_traffic and business < rules.business_success_rate_min:
            base = baseline.get("business_success_rate")
            add("business_transactions", "Business transactions failing", f"Transaction success rate is {business:.1f}% (minimum {rules.business_success_rate_min:g}%).", "high", business, rules.business_success_rate_min, base)

        return findings

    @staticmethod
    def is_incident_worthy(findings: list[Finding]) -> bool:
        """One high/critical finding, or two independent warnings, opens an incident."""
        if any(SEVERITY_ORDER[finding.severity] >= SEVERITY_ORDER["high"] for finding in findings):
            return True
        return len(findings) >= 2

    @staticmethod
    def incident_severity(findings: list[Finding]) -> str:
        levels = {finding.severity for finding in findings}
        if "critical" in levels:
            return "critical"
        if "high" in levels or len(findings) >= 3:
            return "high"
        return "medium" if findings else "low"

    @staticmethod
    def application_status(findings: list[Finding], has_data: bool) -> str:
        if not has_data:
            return "unknown"
        if not findings:
            return "healthy"
        if any(finding.severity in {"critical", "high"} for finding in findings):
            return "critical" if any(finding.severity == "critical" for finding in findings) else "degraded"
        return "degraded"
