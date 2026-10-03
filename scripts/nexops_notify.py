#!/usr/bin/env python3
"""Report deployments to NexOps with an HMAC-signed webhook (stdlib only, used by GitHub Actions and local deploys).

  nexops_notify.py deployment --event started|finished --application payment-api --version v1.4.3 [--ci-status success]
  nexops_notify.py gate --application payment-api [--kind release|rollback]     # exits 3 when deployments are frozen

Environment defaults: NEXOPS_URL, NEXOPS_WEBHOOK_SECRET, and the standard GITHUB_* variables inside Actions.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request


def git(*args: str) -> str | None:
    try:
        return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def changed_files(base: str | None, head: str, paths: list[str]) -> list[dict]:
    if not base or set(base) == {"0"}:
        base = git("rev-parse", f"{head}~1")
    if not base:
        return []
    numstat = git("diff", "--numstat", base, head, "--", *paths) or ""
    files = []
    for line in numstat.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        additions, deletions, filename = parts
        patch = git("diff", base, head, "--", filename) or ""
        files.append({
            "filename": filename,
            "additions": int(additions) if additions.isdigit() else 0,
            "deletions": int(deletions) if deletions.isdigit() else 0,
            "patch": patch[:6000] or None,
        })
    return files[:500]


def post(url: str, secret: str, payload: dict, attempts: int = 5) -> dict:
    """POST a signed payload; retries connection errors and 5xx (NexOps may be restarting during a deploy)."""
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/json", "X-NexOps-Signature": signature})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")[:500]
            if error.code < 500 or attempt == attempts:
                raise SystemExit(f"NexOps rejected the request ({error.code}): {detail}") from error
        except urllib.error.URLError as error:
            if attempt == attempts:
                raise SystemExit(f"NexOps is unreachable at {url}: {error.reason}") from error
        print(f"NexOps not ready (attempt {attempt}/{attempts}); retrying in {attempt * 5}s", file=sys.stderr)
        time.sleep(attempt * 5)
    raise SystemExit("unreachable")


def stages(raw: list[str]) -> list[dict]:
    result = []
    for item in raw:
        name, _, status = item.partition("=")
        result.append({"name": name.strip(), "status": (status or "success").strip().lower()})
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=os.getenv("NEXOPS_URL", "http://localhost:8000"))
    parser.add_argument("--secret", default=os.getenv("NEXOPS_WEBHOOK_SECRET"))
    sub = parser.add_subparsers(dest="command", required=True)

    deployment = sub.add_parser("deployment")
    deployment.add_argument("--event", choices=["started", "finished"], default="finished")
    deployment.add_argument("--application", required=True)
    deployment.add_argument("--version", required=True)
    deployment.add_argument("--environment")
    deployment.add_argument("--kind", choices=["release", "rollback", "hotfix"], default="release")
    deployment.add_argument("--ci-status", default="success")
    deployment.add_argument("--image")
    deployment.add_argument("--summary")
    deployment.add_argument("--stage", action="append", default=[], help="Pipeline stage as name=status (repeatable)")
    deployment.add_argument("--base-sha", default=os.getenv("NEXOPS_BASE_SHA"), help="Previous deployed commit for the diff")
    deployment.add_argument("--path", action="append", default=[], help="Limit changed files to these paths")
    deployment.add_argument("--files-json", help="JSON file with a pre-computed changed-file list")
    deployment.add_argument("--commit-sha", default=os.getenv("GITHUB_SHA"))
    deployment.add_argument("--commit-message")
    deployment.add_argument("--author", default=os.getenv("GITHUB_ACTOR"))

    gate = sub.add_parser("gate")
    gate.add_argument("--application", required=True)
    gate.add_argument("--kind", choices=["release", "rollback", "hotfix"], default="release")

    args = parser.parse_args()
    if not args.secret:
        raise SystemExit("Set NEXOPS_WEBHOOK_SECRET or pass --secret.")
    base_url = args.url.rstrip("/")

    if args.command == "gate":
        result = post(f"{base_url}/api/webhooks/deploy-gate", args.secret, {"application": args.application, "kind": args.kind})
        if not result.get("allowed"):
            print(f"Deployment blocked by NexOps: {result.get('reason')}", file=sys.stderr)
            return 3
        print(f"NexOps deploy gate open (current version {result.get('current_version')})")
        return 0

    head = args.commit_sha or git("rev-parse", "HEAD")
    payload: dict = {
        "event": args.event,
        "application": args.application,
        "version": args.version,
        "kind": args.kind,
        "ci_status": "in_progress" if args.event == "started" else args.ci_status,
        "commit_sha": head,
        "author": args.author or git("log", "-1", "--format=%an"),
        "branch": os.getenv("GITHUB_REF_NAME") or git("rev-parse", "--abbrev-ref", "HEAD"),
        "repository": os.getenv("GITHUB_REPOSITORY"),
        "pipeline": stages(args.stage),
    }
    if args.environment:
        payload["environment"] = args.environment
    if args.path:
        payload["paths"] = args.path
    if args.image:
        payload["image"] = args.image
    if args.summary:
        payload["summary"] = args.summary
    if os.getenv("GITHUB_RUN_ID"):
        payload["ci_run_id"] = f"{os.getenv('GITHUB_RUN_ID')}-{os.getenv('GITHUB_RUN_ATTEMPT', '1')}"
        payload["ci_run_url"] = f"{os.getenv('GITHUB_SERVER_URL', 'https://github.com')}/{os.getenv('GITHUB_REPOSITORY')}/actions/runs/{os.getenv('GITHUB_RUN_ID')}"
    if head:
        payload["commit_message"] = args.commit_message or git("log", "-1", "--format=%s", head)
        committed = git("log", "-1", "--format=%cI", head)
        if committed:
            payload["commit_timestamp"] = committed
    elif args.commit_message:
        payload["commit_message"] = args.commit_message
    if args.event == "finished" and args.kind != "rollback":
        if args.files_json:
            with open(args.files_json, encoding="utf-8") as handle:
                payload["files"] = json.load(handle)
        elif head:
            payload["files"] = changed_files(args.base_sha, head, args.path)
    payload = {key: value for key, value in payload.items() if value not in (None, "", [])} | {"pipeline": payload["pipeline"]}
    result = post(f"{base_url}/api/webhooks/deployments", args.secret, payload)
    print(f"NexOps recorded {result.get('id')} · status {result.get('status')} · risk {result.get('risk_level')} ({result.get('risk_score')}) · {result.get('url')}")
    if os.getenv("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as handle:
            handle.write(f"deployment_id={result.get('id')}\nprevious_version={result.get('previous_version') or ''}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
