"""Unit tests for detection rules, recovery checks, policy decisions and signature helpers."""
from app.core.security import hash_password, sign_payload, verify_password, verify_signature
from app.detection.engine import DetectionEngine, Thresholds, compute_baseline
from app.models import Application, Policy
from app.services.policy import PolicyEngine
from app.services.workflow import recovery_checks

HEALTHY = {"request_rate": 20, "error_rate_percent": 0.2, "p99_latency_ms": 280, "cpu_percent": 40, "memory_percent": 50, "healthy_replicas": 3, "total_replicas": 3, "restarts": 0, "db_errors": 0, "failed_health_checks": 0}


def test_healthy_sample_has_no_findings():
    assert DetectionEngine().evaluate(HEALTHY) == []


def test_degraded_sample_detects_symptoms_with_baseline():
    baseline = compute_baseline([HEALTHY, HEALTHY])
    findings = DetectionEngine().evaluate({**HEALTHY, "error_rate_percent": 8.7, "p99_latency_ms": 1900, "cpu_percent": 91, "healthy_replicas": 2, "db_errors": 148}, baseline)
    codes = {finding.code for finding in findings}
    assert codes == {"error_rate", "p99_latency", "cpu_saturation", "replica_availability", "database_errors"}
    error = next(finding for finding in findings if finding.code == "error_rate")
    assert error.baseline_value == 0.2 and "baseline 0.20%" in error.detail
    assert DetectionEngine.incident_severity(findings) == "high"


def test_latency_regression_relative_to_baseline():
    baseline = compute_baseline([{**HEALTHY, "p99_latency_ms": 120}])
    findings = DetectionEngine().evaluate({**HEALTHY, "p99_latency_ms": 650}, baseline)
    assert [finding.code for finding in findings] == ["p99_latency"]
    assert DetectionEngine.is_incident_worthy(findings) is False  # a single warning only degrades the app


def test_no_traffic_suppresses_rate_based_rules():
    assert DetectionEngine().evaluate({**HEALTHY, "request_rate": 0.0, "error_rate_percent": 50}) == []


def test_threshold_overrides():
    engine = DetectionEngine(Thresholds.from_overrides({"error_rate_percent": 1.0, "unknown": 5}))
    assert [finding.code for finding in engine.evaluate({**HEALTHY, "error_rate_percent": 1.5})] == ["error_rate"]


def test_recovery_checks_require_baseline_level_and_target_version():
    baseline = {"error_rate_percent": 0.2, "p99_latency_ms": 280}
    good = recovery_checks(Thresholds(), {**HEALTHY, "error_rate_percent": 0.3, "version": "v1.0.0", "crashlooping": 0}, baseline, "v1.0.0")
    assert all(check["passed"] for check in good)
    wrong_version = recovery_checks(Thresholds(), {**HEALTHY, "version": "v1.0.1"}, baseline, "v1.0.0")
    assert not all(check["passed"] for check in wrong_version)
    still_high = recovery_checks(Thresholds(), {**HEALTHY, "error_rate_percent": 3.0}, baseline, None)
    assert not next(check for check in still_high if check["name"] == "Error rate")["passed"]


def _policy_engine(policies: list[Policy]):
    from sqlalchemy.orm import Session

    from app.database.session import get_engine
    from app.models import Base

    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add_all(policies)
    session.flush()
    return PolicyEngine(session)


def _app(environment: str) -> Application:
    return Application(id="svc", name="Svc", environment=environment)


def test_policy_production_rollback_requires_approval_and_database_is_forbidden():
    policies = [
        Policy(id="p1", name="Prod rollback", action="rollback", environment="production", mode="approval_required", enabled=True, conditions={"require_previous_version": True}),
        Policy(id="p2", name="Staging rollback", action="rollback", environment="staging", mode="automatic", enabled=True, conditions={"min_confidence": 0.6}),
        Policy(id="p3", name="DB", action="database_modification", environment="production", mode="forbidden", enabled=True, conditions={}),
    ]
    engine = _policy_engine(policies)
    assert engine.evaluate("rollback", _app("production"), confidence=0.9).mode == "approval_required"
    assert engine.evaluate("rollback", _app("staging"), confidence=0.9).mode == "automatic"
    assert engine.evaluate("rollback", _app("development"), confidence=0.9).mode == "automatic"
    low = engine.evaluate("rollback", _app("staging"), confidence=0.4)
    assert low.mode == "approval_required" and "below" in low.reasons[-1]
    assert engine.evaluate("database_modification", _app("production")).mode == "forbidden"
    assert engine.evaluate("rollback", _app("production"), has_previous_version=False).mode == "forbidden"
    assert engine.evaluate("restart", _app("production")).mode == "approval_required"  # no policy → safe default


def test_password_hash_and_webhook_signature():
    encoded = hash_password("correct horse battery")
    assert verify_password("correct horse battery", encoded)
    assert not verify_password("wrong", encoded)
    signature = sign_payload("secret", b'{"a":1}')
    assert verify_signature("secret", b'{"a":1}', signature)
    assert not verify_signature("secret", b'{"a":2}', signature)
