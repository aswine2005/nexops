"""Payment API: a small, instrumented service that NexOps monitors.

It exposes golden-signal metrics (http_requests_total, http_request_duration_seconds, business_transactions_total),
writes JSON logs to stdout, and depends on a real PostgreSQL database configured in config/database.json.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import psycopg
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from opentelemetry.trace import Status, StatusCode
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from pydantic import BaseModel, Field

from app import flags, tracing

VERSION = os.getenv("APP_VERSION", "dev")
SERVICE = os.getenv("SERVICE_NAME", "payment-api")
CONFIG = json.loads((Path(__file__).resolve().parent.parent / "config" / "database.json").read_text())
DB = {
    "host": os.getenv("DB_HOST", CONFIG["host"]),
    "port": int(os.getenv("DB_PORT", CONFIG["port"])),
    "dbname": os.getenv("DB_NAME", CONFIG["database"]),
    "user": os.getenv("DB_USER", CONFIG["user"]),
    "password": os.getenv("DB_PASSWORD", ""),
    "connect_timeout": int(CONFIG.get("connect_timeout_seconds", 2)),
}
FRAUD = json.loads((Path(__file__).resolve().parent.parent / "config" / "fraud.json").read_text())
# Table the release reads/writes; init_schema() (the "migration") only ever creates "payments".
TABLE = CONFIG.get("table", "payments")
RETRIES = int(CONFIG.get("retries", 3))
RETRY_BACKOFF = float(CONFIG.get("retry_backoff_seconds", 0.25))
EXIT_AFTER_DB_FAILURE_SECONDS = int(os.getenv("EXIT_AFTER_DB_FAILURE_SECONDS", "45"))


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "service": SERVICE,
            "version": VERSION,
            "msg": record.getMessage(),
        }
        for key in ("trace_id", "route", "status", "duration_ms", "error"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if "trace_id" not in payload and (trace_id := tracing.current_trace_id()):
            payload["trace_id"] = trace_id
        return json.dumps(payload)


handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(JsonFormatter())
log = logging.getLogger(SERVICE)
log.handlers = [handler]
log.setLevel(logging.INFO)
log.propagate = False

REQUESTS = Counter("http_requests_total", "HTTP requests", ["method", "route", "status"])
LATENCY = Histogram("http_request_duration_seconds", "HTTP request latency", ["method", "route"], buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 1.5, 2, 3, 5))
TRANSACTIONS = Counter("business_transactions_total", "Payment transactions", ["result"])
DB_ERRORS = Counter("db_errors_total", "Database errors")
BUILD_INFO = Gauge("app_build_info", "Build information", ["version"])
BUILD_INFO.labels(version=VERSION).set(1)

_db_state = {"failing_since": None}
_db_lock = threading.Lock()


def _record_db(ok: bool) -> None:
    with _db_lock:
        if ok:
            _db_state["failing_since"] = None
        elif _db_state["failing_since"] is None:
            _db_state["failing_since"] = time.time()


def connect() -> psycopg.Connection:
    """Connect with retries; database failures are logged like a production client would."""
    last_error: Exception | None = None
    for attempt in range(1, RETRIES + 1):
        with tracing.tracer.start_as_current_span("db.connect", attributes={
            "db.system": "postgresql", "db.name": DB["dbname"], "server.address": DB["host"], "server.port": DB["port"], "db.connect.attempt": attempt,
        }) as span:
            try:
                connection = psycopg.connect(**DB)
                _record_db(True)
                return connection
            except psycopg.OperationalError as error:
                last_error = error
                message = str(error).strip().splitlines()[0]
                span.record_exception(error)
                span.set_status(Status(StatusCode.ERROR, message[:200]))
                DB_ERRORS.inc()
                log.warning(f"Database connection attempt {attempt}/{RETRIES} failed: {message}")
        time.sleep(RETRY_BACKOFF * attempt)
    _record_db(False)
    raise last_error  # type: ignore[misc]


def init_schema() -> None:
    for _ in range(30):
        try:
            with connect() as connection:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS payments (id uuid PRIMARY KEY, amount_cents integer NOT NULL, currency text NOT NULL, created_at timestamptz NOT NULL DEFAULT now())"
                )
            return
        except psycopg.OperationalError:
            time.sleep(1)


def watchdog() -> None:
    """Exit when the database stays unreachable, so the orchestrator restarts the process (like a failing liveness probe)."""
    while True:
        time.sleep(5)
        with _db_lock:
            since = _db_state["failing_since"]
        if since and time.time() - since > EXIT_AFTER_DB_FAILURE_SECONDS:
            log.critical(f"Database unreachable for {int(time.time() - since)}s at {DB['host']}:{DB['port']}; exiting so the runtime restarts the service")
            os._exit(1)


@asynccontextmanager
async def lifespan(_: FastAPI):
    log.info(f"Starting Payment API {VERSION} · database {DB['host']}:{DB['port']}/{DB['dbname']}")
    if flags.start(log):
        log.info("Feature flags are read from NexOps")
    threading.Thread(target=init_schema, daemon=True).start()
    threading.Thread(target=watchdog, daemon=True).start()
    yield


app = FastAPI(title="Payment API", version=VERSION, lifespan=lifespan)


@app.middleware("http")
async def instrument(request: Request, call_next):
    started = time.perf_counter()
    trace_id = tracing.current_trace_id() or request.headers.get("x-trace-id") or uuid.uuid4().hex[:16]
    request.state.trace_id = trace_id
    response: Response | None = None
    try:
        response = await call_next(request)
        response.headers["X-Trace-Id"] = trace_id  # lets a client or support look the request up in Jaeger
        return response
    finally:
        route = request.scope.get("route")
        path = getattr(route, "path", "unmatched")
        if path not in {"/metrics"}:
            status = response.status_code if response is not None else 500
            duration = time.perf_counter() - started
            REQUESTS.labels(request.method, path, str(status)).inc()
            LATENCY.labels(request.method, path).observe(duration)


class PaymentRequest(BaseModel):
    amount_cents: int = Field(gt=0, le=10_000_000)
    currency: str = Field(default="USD", min_length=3, max_length=3)


def fraud_score(payment_id: uuid.UUID, payment: PaymentRequest) -> float:
    """Feature "fraud-check": score the payment with the fraud service before capturing it."""
    endpoint = FRAUD["endpoint"]
    with tracing.tracer.start_as_current_span("fraud.score", attributes={
        "feature_flag.key": "fraud-check", "feature_flag.provider_name": "nexops", "url.full": endpoint,
    }) as span:
        body = json.dumps({"payment_id": str(payment_id), "amount_cents": payment.amount_cents, "currency": payment.currency}).encode()
        try:
            request = urllib.request.Request(endpoint, data=body, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=float(FRAUD.get("timeout_seconds", 1.5))) as response:
                return float(json.loads(response.read()).get("score", 0))
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            reason = getattr(error, "reason", error)
            message = f"timed out calling fraud scoring at {endpoint}" if "timed out" in str(reason) else f"fraud scoring at {endpoint} failed: {reason}"
            span.record_exception(error)
            span.set_status(Status(StatusCode.ERROR, message))
            raise RuntimeError(message) from error


@app.post("/payments", status_code=201)
def create_payment(payment: PaymentRequest, request: Request):
    payment_id = uuid.uuid4()
    if flags.enabled("fraud-check", str(payment_id)):
        try:
            fraud_score(payment_id, payment)
        except RuntimeError as error:
            TRANSACTIONS.labels("failure").inc()
            log.error(f"Fraud check failed: {error} (feature fraud-check)", extra={"trace_id": request.state.trace_id, "route": "/payments", "error": "FraudCheckUnavailable"})
            return JSONResponse(status_code=502, content={"error": "fraud_check_unavailable", "trace_id": request.state.trace_id})
    try:
        with connect() as connection:
            connection.execute(f"INSERT INTO {TABLE} (id, amount_cents, currency) VALUES (%s, %s, %s)", (payment_id, payment.amount_cents, payment.currency.upper()))
    except psycopg.Error as error:
        TRANSACTIONS.labels("failure").inc()
        message = str(error).strip().splitlines()[0]
        what = "connection" if isinstance(error, psycopg.OperationalError) else "write"
        log.error(f"Database {what} failed: {message}", extra={"trace_id": request.state.trace_id, "route": "/payments", "error": "ECONNREFUSED" if "refused" in message.lower() else error.__class__.__name__})
        return JSONResponse(status_code=503, content={"error": "payment_unavailable", "trace_id": request.state.trace_id})
    TRANSACTIONS.labels("success").inc()
    return {"id": str(payment_id), "status": "captured", "version": VERSION}


@app.get("/payments/{payment_id}")
def get_payment(payment_id: str, request: Request):
    try:
        key = uuid.UUID(payment_id)
    except ValueError:
        return JSONResponse(status_code=404, content={"error": "not_found"})
    try:
        with connect() as connection:
            row = connection.execute(f"SELECT id, amount_cents, currency, created_at FROM {TABLE} WHERE id = %s", (key,)).fetchone()
    except psycopg.Error as error:
        log.error(f"Database query failed: {str(error).strip().splitlines()[0]}", extra={"trace_id": request.state.trace_id, "route": "/payments/{payment_id}"})
        return JSONResponse(status_code=503, content={"error": "payment_lookup_unavailable"})
    if row is None:
        return JSONResponse(status_code=404, content={"error": "not_found"})
    return {"id": str(row[0]), "amount_cents": row[1], "currency": row[2], "created_at": row[3].isoformat()}


@app.get("/health")
def health():
    return {"status": "ok", "service": SERVICE, "version": VERSION}


@app.get("/ready")
def ready():
    try:
        with tracing.untraced(), psycopg.connect(**DB) as connection:
            connection.execute("SELECT 1")
        _record_db(True)
        return {"status": "ready", "version": VERSION}
    except psycopg.OperationalError as error:
        _record_db(False)
        log.warning(f"Readiness check failed: database ping failed ({str(error).strip().splitlines()[0]})")
        return JSONResponse(status_code=503, content={"status": "not_ready", "reason": "database_unreachable"})


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# Registered last so the tracing middleware wraps the metrics middleware and every request has a trace id.
if tracing.setup(app, SERVICE, VERSION):
    log.info("OpenTelemetry tracing enabled")
