#!/usr/bin/env python3
"""Local CI/CD for the sample Payment API: gate → build → test → scan → deploy → report to NexOps.

  python3 scripts/deploy_local.py v1.0.0                      # release the current source (docker compose)
  python3 scripts/deploy_local.py v1.1.0 --break-db-config    # release that points at the wrong database port (a real failure)
  python3 scripts/deploy_local.py v1.1.1 --message "fix: ..."  # any source edits under examples/payment-api are diffed and reported
  python3 scripts/deploy_local.py v1.4.0 --target kind        # Argo Rollouts canary on kind (scripts/k8s/cluster.py kind-up)
  python3 scripts/deploy_local.py v1.4.1 --target kind --break-schema   # canary fails NexOps analysis; Argo aborts it
  python3 scripts/deploy_local.py v1.4.0 --target eks         # build linux/amd64, push to ECR, canary on EKS

The NexOps webhook secret is read from deploy/.env (EKS: SSM). Changed files are diffed against the build NexOps reports as running.
"""
from __future__ import annotations

import argparse
import base64
import difflib
import getpass
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "examples" / "payment-api"
STATE = ROOT / ".nexops-local" / "payment-api"
COMPOSE = ["docker", "compose", "-f", str(ROOT / "deploy" / "docker-compose.yml"), "--env-file", str(ROOT / "deploy" / ".env")]
sys.path.insert(0, str(ROOT / "scripts"))
from nexops_notify import post  # noqa: E402


def load_env() -> dict[str, str]:
    values: dict[str, str] = {}
    env_file = ROOT / "deploy" / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def run(command: list[str], env: dict | None = None) -> bool:
    print(f"$ {' '.join(command)}", flush=True)
    return subprocess.run(command, env={**os.environ, **(env or {})}).returncode == 0


REGION = os.getenv("AWS_REGION", "ap-south-1")
KUBECTL = {"kind": ["kubectl", "--context", "kind-nexops"], "eks": ["kubectl", "--context", "nexops-eks"]}


def capture(command: list[str]) -> str:
    return subprocess.run(command, check=True, capture_output=True, text=True).stdout.strip()


def ssm(name: str) -> str:
    return capture(["aws", "ssm", "get-parameter", "--region", REGION, "--name", f"/nexops/{name}", "--with-decryption", "--query", "Parameter.Value", "--output", "text"])


def watch_canary(kubectl: list[str], image: str, timeout: int = 900) -> tuple[bool, str]:
    """Follow the Argo Rollout until the canary is promoted (True) or aborted (False); NexOps analysis decides."""
    deadline, last = time.time() + timeout, ""
    while time.time() < deadline:
        rollout = json.loads(capture([*kubectl, "-n", "payments", "get", "rollout", "payment-api", "-o", "json"]))
        status = rollout.get("status") or {}
        if str(status.get("observedGeneration")) != str(rollout["metadata"]["generation"]):
            time.sleep(1)  # the controller has not seen the new image yet; the old status would look "Healthy"
            continue
        steps = rollout["spec"]["strategy"]["canary"].get("steps", [])
        index = status.get("currentStepIndex") or 0
        weight = next((step["setWeight"] for step in reversed(steps[: index + 1]) if "setWeight" in step), 0) if index < len(steps) else 100
        line = f"  rollout {status.get('phase')}: step {index}/{len(steps)}, canary {weight}% {status.get('message') or ''}".rstrip()
        if line != last:
            print(line, flush=True)
            last = line
        if status.get("abort") or status.get("phase") == "Degraded":
            runs = json.loads(capture([*kubectl, "-n", "payments", "get", "analysisrun", "-o", "json"])).get("items", [])
            failed = [item for item in runs if (item.get("status") or {}).get("phase") in {"Failed", "Error"}]
            detail = ((failed[-1].get("status") or {}).get("message") if failed else None) or status.get("message") or "aborted"
            return False, f"Canary aborted by Argo Rollouts at {weight or 'the first'}% after NexOps analysis: {detail}. The stable version kept serving."
        if status.get("phase") == "Healthy" and status.get("stableRS") == status.get("currentPodHash") and image in json.dumps(rollout["spec"]["template"]):
            return True, f"Canary promoted to 100% after {sum(1 for step in steps if 'analysis' in step)} passing NexOps analyses."
        time.sleep(3)
    return False, f"Rollout did not finish within {timeout}s."


def tree(path: Path) -> dict[str, str]:
    files = {}
    for item in sorted(path.rglob("*")):
        if item.is_file() and "__pycache__" not in item.parts and item.suffix != ".pyc":
            files[str(item.relative_to(path))] = item.read_text(errors="replace")
    return files


def diff(previous: dict[str, str], current: dict[str, str]) -> list[dict]:
    changes = []
    for name in sorted(set(previous) | set(current)):
        before, after = previous.get(name), current.get(name)
        if before == after:
            continue
        lines = list(difflib.unified_diff((before or "").splitlines(), (after or "").splitlines(), f"a/{name}", f"b/{name}", lineterm=""))
        changes.append({
            "filename": f"examples/payment-api/{name}",
            "status": "added" if before is None else "removed" if after is None else "modified",
            "additions": sum(1 for line in lines if line.startswith("+") and not line.startswith("+++")),
            "deletions": sum(1 for line in lines if line.startswith("-") and not line.startswith("---")),
            "patch": "\n".join(lines)[:6000],
        })
    return changes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("version", help="Release version, e.g. v1.1.0")
    parser.add_argument("--break-db-config", action="store_true", help="Ship config/database.json with port 6543 (database becomes unreachable)")
    parser.add_argument("--break-schema", action="store_true", help="Ship config/database.json pointing at table payments_v2, which no migration creates (writes fail, readiness passes)")
    parser.add_argument("--message", help="Change description recorded as the commit message")
    parser.add_argument("--target", choices=["compose", "kind", "eks"], default="compose",
                        help="compose (default), or the payment-api Argo Rollout on the kind / EKS cluster from scripts/k8s/cluster.py")
    parser.add_argument("--nexops-url", default=os.getenv("NEXOPS_URL"))
    args = parser.parse_args()

    env = load_env()
    secret = os.getenv("NEXOPS_WEBHOOK_SECRET") or env.get("NEXOPS_WEBHOOK_SECRET")
    image = f"nexops/payment-api:{args.version}"
    if args.target == "eks":
        secret = ssm("NEXOPS_WEBHOOK_SECRET")
        account = json.loads(capture(["aws", "sts", "get-caller-identity", "--output", "json"]))["Account"]
        image = f"{account}.dkr.ecr.{REGION}.amazonaws.com/payment-api:{args.version}"
        public_url = capture([*KUBECTL["eks"], "-n", "nexops", "get", "secret", "nexops-secrets", "-o", "jsonpath={.data.NEXOPS_PUBLIC_URL}"])
        args.nexops_url = args.nexops_url or base64.b64decode(public_url).decode()  # https://<elastic-ip>.sslip.io
    args.nexops_url = args.nexops_url or {"compose": "http://localhost:8000", "kind": "http://localhost:8090"}[args.target]
    if not secret:
        raise SystemExit("NEXOPS_WEBHOOK_SECRET is missing (set it in deploy/.env).")
    webhook = f"{args.nexops_url.rstrip('/')}/api/webhooks/deployments"

    gate = post(f"{args.nexops_url.rstrip('/')}/api/webhooks/deploy-gate", secret, {"application": "payment-api", "kind": "release"})
    if not gate.get("allowed"):
        print(f"NexOps deploy gate is closed: {gate.get('reason')}")
        return 3

    context = Path(tempfile.mkdtemp(prefix="payment-api-build-"))
    shutil.copytree(SOURCE, context, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    message = args.message or f"Release payment-api {args.version}"
    if args.break_db_config:
        config_path = context / "config" / "database.json"
        config = json.loads(config_path.read_text())
        config["port"] = 6543
        config_path.write_text(json.dumps(config, indent=2) + "\n")
        message = args.message or "Tune database connection settings for the new pool"
    if args.break_schema:
        config_path = context / "config" / "database.json"
        config = json.loads(config_path.read_text())
        config["table"] = "payments_v2"
        config_path.write_text(json.dumps(config, indent=2) + "\n")
        message = args.message or "Write payments to the new partitioned payments_v2 table"
    current = tree(context)
    # Diff against the build that is running in production now (NexOps knows it, even after a rollback).
    running = gate.get("current_version")
    snapshot = STATE / "versions" / f"{running}.json"
    previous = json.loads(snapshot.read_text()) if running and snapshot.exists() else {}
    files = diff(previous, current) if previous else []
    started = datetime.now(timezone.utc)
    common = {
        "application": "payment-api", "version": args.version, "environment": "production", "source": "local-cd",
        "commit_sha": f"local-{int(time.time())}", "commit_message": message, "commit_timestamp": started.isoformat(),
        "author": os.getenv("GIT_AUTHOR_NAME") or getpass.getuser(), "ci_run_id": f"local-{args.version}-{int(time.time())}",
        "image": image,
    }

    post(webhook, secret, {**common, "event": "started", "pipeline": [{"name": "Build", "status": "in_progress"}]})
    stages: list[dict] = []

    def stage(name: str, ok: bool) -> bool:
        stages.append({"name": name, "status": "success" if ok else "failure"})
        return ok

    platform = ["--platform", "linux/amd64"] if args.target == "eks" else []  # EKS nodes are x86_64
    ok = stage("Build", run(["docker", "build", "-q", *platform, "-t", image, "--build-arg", f"APP_VERSION={args.version}", str(context)]))
    ok = ok and stage("Tests", run(["docker", "run", "--rm", "--entrypoint", "python", image, "-c", "import app.main; print('import ok')"]))
    ok = ok and stage("Security", run(["docker", "run", "--rm", "--entrypoint", "pip", image, "check"]))
    ci_status, summary = ("success" if ok else "failure"), None
    if args.target == "compose":
        ok = ok and stage("Docker", True)
        ok = ok and stage("Deploy", run([*COMPOSE, "up", "-d", "--no-deps", "--no-build", "payment-api"], env={"PAYMENT_API_VERSION": args.version}))
        ci_status = "success" if ok else "failure"
    elif ok:
        if args.target == "kind":
            ok = stage("Load image", run(["kind", "load", "docker-image", "--name", "nexops", image]))
        else:
            registry = image.split("/", 1)[0]
            password = capture(["aws", "ecr", "get-login-password", "--region", REGION])
            ok = stage("Push", subprocess.run(["docker", "login", "--username", "AWS", "--password-stdin", registry], input=password, text=True).returncode == 0
                       and run(["docker", "push", "-q", image]))
        patch = json.dumps([{"op": "replace", "path": "/spec/template/spec/containers/0/image", "value": image}])
        ok = ok and stage("Deploy", run([*KUBECTL[args.target], "-n", "payments", "patch", "rollout", "payment-api", "--type", "json", "-p", patch]))
        ci_status = "success" if ok else "failure"
        if ok:
            promoted, summary = watch_canary(KUBECTL[args.target], image)
            stage("Canary", promoted)
            ci_status = "success" if promoted else "aborted"
            ok = promoted
    shutil.rmtree(context, ignore_errors=True)

    result = post(webhook, secret, {**common, "event": "finished", "ci_status": ci_status, "summary": summary, "pipeline": stages, "files": files})
    print(f"\nNexOps recorded {result.get('id')}: {result.get('status')} · risk {result.get('risk_level')} ({result.get('risk_score')}) · previous {result.get('previous_version')}")
    print(f"Open {result.get('url')}")
    if ok:
        (STATE / "versions").mkdir(parents=True, exist_ok=True)
        (STATE / "versions" / f"{args.version}.json").write_text(json.dumps(current))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
