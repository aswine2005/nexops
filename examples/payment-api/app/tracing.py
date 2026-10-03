"""OpenTelemetry tracing. Enabled when OTEL_EXPORTER_OTLP_ENDPOINT is set; otherwise the API uses a no-op tracer."""
from __future__ import annotations

import logging
import os

from opentelemetry import trace

tracer = trace.get_tracer("payment-api")


def setup(app, service: str, version: str) -> bool:
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        return False
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter  # noqa: PLC0415
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor  # noqa: PLC0415
    from opentelemetry.sdk.resources import Resource  # noqa: PLC0415
    from opentelemetry.sdk.trace import TracerProvider  # noqa: PLC0415
    from opentelemetry.sdk.trace.export import BatchSpanProcessor  # noqa: PLC0415

    provider = TracerProvider(resource=Resource.create({
        "service.name": service,
        "service.version": version,
        "deployment.environment": os.getenv("DEPLOY_ENVIRONMENT", "production"),
    }))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    # Probes and scrapes run constantly and carry no user traffic.
    FastAPIInstrumentor.instrument_app(app, excluded_urls="metrics,health,ready")
    try:
        from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor  # noqa: PLC0415

        PsycopgInstrumentor().instrument()
    except Exception:  # query spans are optional; connection spans are recorded manually
        logging.getLogger(service).warning("psycopg instrumentation unavailable")
    return True


def untraced():
    """Context manager that suppresses spans (health probes run constantly and would flood the trace store)."""
    try:
        from opentelemetry.instrumentation.utils import suppress_instrumentation  # noqa: PLC0415

        return suppress_instrumentation()
    except ImportError:
        from contextlib import nullcontext  # noqa: PLC0415

        return nullcontext()


def current_trace_id() -> str | None:
    context = trace.get_current_span().get_span_context()
    return format(context.trace_id, "032x") if context.is_valid else None
