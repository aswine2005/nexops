#!/usr/bin/env python3
"""Register the local Payment API in NexOps with its real telemetry sources (Prometheus, Loki, Docker, health checks).

  python3 scripts/register_local_app.py --email admin@example.com --password '...'
  python3 scripts/register_local_app.py --url http://localhost:8090 --runtime kubernetes   # kind / EKS (Argo Rollout)

Uses the admin credentials from deploy/.env when flags are omitted.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

APPLICATION = {
    "id": "payment-api",
    "name": "Payment API",
    "description": "Processes payment authorisation and capture. Local sample service instrumented with Prometheus metrics and JSON logs.",
    "owner": "Payments Platform",
    "environment": "production",
    "runtime": "docker",
    "prometheus_selector": 'job="payment-api"',
    "loki_selector": '{container="payment-api"}',
    "tracing_service": "payment-api",
    "health_checks": [
        {"name": "liveness", "url": "http://payment-api:8000/health"},
        {"name": "readiness", "url": "http://payment-api:8000/ready"},
    ],
    "docker_container": "payment-api",
    "image_repository": "nexops/payment-api",
    "rollback_strategy": "docker",
    "dependencies": ["PostgreSQL (payments-db)"],
}


# On Kubernetes (deploy/k8s): an Argo Rollout in "payments", pod logs via the Kubernetes API, Prometheus pod labels.
KUBERNETES = {
    "description": "Processes payment authorisation and capture. Runs as an Argo Rollout (canary with NexOps analysis) on Kubernetes.",
    "runtime": "kubernetes",
    "prometheus_selector": 'namespace="payments",app="payment-api"',
    "loki_selector": None,
    "health_checks": [
        {"name": "liveness", "url": "http://payment-api.payments.svc:8000/health"},
        {"name": "readiness", "url": "http://payment-api.payments.svc:8000/ready"},
    ],
    "docker_container": None,
    "k8s_namespace": "payments",
    "k8s_deployment": "payment-api",
    "k8s_container": "payment-api",
    "rollback_strategy": "kubernetes",
    "dependencies": ["PostgreSQL (payments-db StatefulSet)"],
}

# On the EC2 host (deploy/aws): same Docker telemetry, plus CloudWatch logs and rollback through GitHub Actions.
EC2_HOST = {
    "description": "Payment authorisation and capture service running on the EC2 Docker host.",
    "cloudwatch_log_group": "/nexops/payment-api",
    "rollback_strategy": "github_actions",
    "github_workflow": "deploy-payment-api.yml",
    "github_ref": "main",
}

FRAUD_FLAG = {"key": "fraud-check", "description": "Score payments with the external fraud service (payment-api >= 1.3.0)", "rollout_percent": 100}


def request(method: str, url: str, body: dict | None = None, token: str | None = None) -> tuple[int, dict]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


def main() -> int:
    env: dict[str, str] = {}
    env_file = ROOT / "deploy" / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.getenv("NEXOPS_URL", "http://localhost:8000"))
    parser.add_argument("--email", default=os.getenv("NEXOPS_ADMIN_EMAIL") or env.get("NEXOPS_ADMIN_EMAIL"))
    parser.add_argument("--password", default=os.getenv("NEXOPS_ADMIN_PASSWORD") or env.get("NEXOPS_ADMIN_PASSWORD"))
    parser.add_argument("--runtime", choices=["docker", "kubernetes", "ec2-host"], default="docker",
                        help="ec2-host: the hosted EC2 setup (Docker + CloudWatch, rollback via GitHub Actions)")
    parser.add_argument("--image-repository", help="Image repository NexOps rolls back to (default nexops/payment-api)")
    parser.add_argument("--repository", help="GitHub repository owner/name (needed for GitHub Actions rollback)")
    parser.add_argument("--ec2-instance-id", help="EC2 instance the service runs on")
    args = parser.parse_args()
    application = {**APPLICATION, **(KUBERNETES if args.runtime == "kubernetes" else EC2_HOST if args.runtime == "ec2-host" else {})}
    application["runtime"] = "docker" if args.runtime == "ec2-host" else application["runtime"]
    for key, value in (("repository", args.repository), ("ec2_instance_id", args.ec2_instance_id)):
        if value:
            application[key] = value
    if args.image_repository:
        application["image_repository"] = args.image_repository
    base = args.url.rstrip("/")
    status, login = request("POST", f"{base}/api/auth/login", {"email": args.email, "password": args.password})
    if status != 200:
        raise SystemExit(f"Login failed ({status}): {login.get('detail')}")
    token = login["token"]
    status, existing = request("GET", f"{base}/api/applications/payment-api", token=token)
    if status == 200:
        status, result = request("PUT", f"{base}/api/applications/payment-api", application, token)
        print(f"Updated payment-api ({status})")
    else:
        status, result = request("POST", f"{base}/api/applications", application, token)
        print(f"Registered payment-api ({status})")
    if status >= 300:
        print(json.dumps(result, indent=2))
        return 1
    status, flags = request("GET", f"{base}/api/flags?application=payment-api", token=token)
    if status == 200 and not any(flag["key"] == "fraud-check" for flag in flags):
        status, _ = request("POST", f"{base}/api/applications/payment-api/flags", FRAUD_FLAG, token)
        print(f"Created feature flag fraud-check, off ({status})")
    status, check = request("POST", f"{base}/api/applications/payment-api/check", token=token)
    print(json.dumps({"sources": check.get("sources"), "errors": check.get("errors"), "sample": check.get("sample")}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
