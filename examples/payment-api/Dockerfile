FROM python:3.12-slim
ARG APP_VERSION=dev
# Traces only: Jaeger accepts no OTLP metrics/logs, and FastAPI's built-in telemetry would otherwise export them.
ENV APP_VERSION=${APP_VERSION} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OTEL_METRICS_EXPORTER=none \
    OTEL_LOGS_EXPORTER=none
LABEL org.opencontainers.image.title="payment-api" \
      org.opencontainers.image.version=${APP_VERSION} \
      nexops.application="payment-api"
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --no-create-home app
COPY app ./app
COPY config ./config
USER app
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=2).status == 200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
