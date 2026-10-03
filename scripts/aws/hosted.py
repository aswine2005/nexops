#!/usr/bin/env python3
"""Hosted NexOps on AWS (the EC2 stack from infra/terraform/aws).

Everyday use (nothing in AWS between uses, so no AWS cost; your data waits in Docker on your Mac):
  python3 scripts/aws/hosted.py up                # create everything in AWS, deploy, restore your data (~20 min)
  python3 scripts/aws/hosted.py down              # copy the data to your Mac, then delete EVERYTHING in AWS (~10 min)
  python3 scripts/aws/hosted.py status            # what exists in AWS, what is saved locally

Other commands:
  python3 scripts/aws/hosted.py login             # print the address, admin email and password
  python3 scripts/aws/hosted.py save              # copy the online data to your Mac now (deletes nothing)
  python3 scripts/aws/hosted.py stop | start      # pause the server without deleting it (~$6/month while stopped)
  python3 scripts/aws/hosted.py apply             # apply infra/terraform/aws changes (shows the plan, asks first)
  python3 scripts/aws/hosted.py outputs           # the values GitHub needs (up sets them by itself)
  python3 scripts/aws/hosted.py register          # (re-)register payment-api in the hosted NexOps
  python3 scripts/aws/hosted.py down --preview    # list what down would delete; deletes nothing
  python3 scripts/aws/hosted.py destroy           # delete everything in AWS WITHOUT saving the data

The online data is kept in the Docker container nexops-cloud-data (deploy/cloud-data, PostgreSQL on localhost:5434).
Uses the AWS CLI profile in AWS_PROFILE (default "nexops") and region AWS_REGION (default ap-south-1).
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "k8s"))
import cluster  # noqa: E402  (shared run/aws/terraform helpers)

os.environ.setdefault("AWS_PROFILE", cluster.PROJECT)
AWS_DIR = cluster.AWS_DIR
PROJECT = cluster.PROJECT
ROOT = cluster.ROOT
TOOLS = ROOT / ".nexops-local" / "tools"
PLUGIN_URL = "https://s3.amazonaws.com/session-manager-downloads/plugin/latest/{arch}/sessionmanager-bundle.zip"
CLOUD_DATA = ROOT / "deploy" / "cloud-data" / "docker-compose.yml"
CLOUD_DATA_CONTAINER = "nexops-cloud-data"
TUNNEL_PORT = 15432
PLATFORM = "cd /opt/nexops/aws && docker compose -f platform.yml --env-file .env"
# Kept in deploy/.env so a re-created stack has the same login and GitHub webhook secret.
SAVED_SECRETS = {"NEXOPS_CLOUD_ADMIN_PASSWORD": "NEXOPS_ADMIN_PASSWORD", "NEXOPS_CLOUD_WEBHOOK_SECRET": "NEXOPS_WEBHOOK_SECRET"}
COUNTS = ("SELECT (SELECT count(*) FROM applications) || ' applications, ' || (SELECT count(*) FROM deployments) || ' deployments, ' || "
          "(SELECT count(*) FROM incidents) || ' incidents, ' || (SELECT count(*) FROM feature_flags) || ' flags, ' || "
          "(SELECT count(*) FROM users) || ' users, ' || (SELECT count(*) FROM metric_samples) || ' metric samples'")


# ----------------------------------------------------------------------------------------------------------- basics
def find_instance() -> dict | None:
    raw = cluster.aws("ec2", "describe-instances", "--filters", f"Name=tag:Project,Values={PROJECT}",
                      "Name=instance-state-name,Values=pending,running,stopping,stopped")
    found = [item for reservation in json.loads(raw)["Reservations"] for item in reservation["Instances"]
             if any(tag["Key"] == "Name" and tag["Value"].endswith("host") for tag in item.get("Tags", []))]
    return found[0] if found else None


def instance() -> dict:
    item = find_instance()
    if item is None:
        raise SystemExit("Nothing is running in AWS. Create it with: python3 scripts/aws/hosted.py up")
    return item


def url_for(item: dict) -> str | None:
    address = item.get("PublicIpAddress") or ""
    return f"https://{address.replace('.', '-')}.sslip.io" if address else None


def ssm(name: str) -> str:
    return json.loads(cluster.aws("ssm", "get-parameter", "--name", f"/{PROJECT}/{name}", "--with-decryption"))["Parameter"]["Value"]


def get_json(url: str) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return json.loads(response.read())
    except (OSError, ValueError):
        return None


def tfvar(name: str) -> str:
    for line in (AWS_DIR / "terraform.tfvars").read_text().splitlines():
        if line.split("=")[0].strip() == name:
            return line.partition("=")[2].strip().strip('"')
    return ""


def tf_env() -> dict[str, str]:
    """Secrets Terraform needs from deploy/.env (passed by name, never on a command line)."""
    values = cluster.env_file()
    return {
        "TF_VAR_groq_api_key": values.get("GROQ_API_KEY", ""), "TF_VAR_github_token": values.get("GITHUB_TOKEN", ""),
        "TF_VAR_admin_password": values.get("NEXOPS_CLOUD_ADMIN_PASSWORD", ""), "TF_VAR_webhook_secret": values.get("NEXOPS_CLOUD_WEBHOOK_SECRET", ""),
    }


def set_env_values(updates: dict[str, str]) -> None:
    """Write KEY=value lines into deploy/.env (replacing existing ones), without printing the values."""
    lines = cluster.ENV_FILE.read_text().splitlines() if cluster.ENV_FILE.exists() else []
    remaining = dict(updates)
    for index, line in enumerate(lines):
        key = line.partition("=")[0].strip()
        if key in remaining:
            lines[index] = f"{key}={remaining.pop(key)}"
    if remaining:
        lines += ["", "# Online (AWS) login and webhook secret, kept so hosted.py up re-creates them unchanged"]
        lines += [f"{key}={value}" for key, value in remaining.items()]
    cluster.ENV_FILE.write_text("\n".join(lines) + "\n")


def state_bucket() -> str:
    for line in (AWS_DIR / "backend.hcl").read_text().splitlines():
        if line.strip().startswith("bucket"):
            return line.partition("=")[2].strip().strip('"')
    raise SystemExit("bucket not found in infra/terraform/aws/backend.hcl")


def bucket_exists(bucket: str) -> bool:
    raw = cluster.aws("s3api", "list-buckets", "--query", f"Buckets[?Name=='{bucket}'].Name", check=False)
    return bool(raw and json.loads(raw))


def init_aws_stack() -> None:
    cluster.terraform("init", "-input=false", "-reconfigure", "-backend-config=backend.hcl", directory=AWS_DIR)


def eks_exists() -> bool:
    return bool(cluster.aws("eks", "describe-cluster", "--name", f"{PROJECT}-eks", check=False))


# --------------------------------------------------------------------------------------------- remote + tunnel
def remote(instance_id: str, *commands: str, timeout: int = 900) -> str:
    """Run shell commands on the EC2 host through SSM and return their output."""
    command_id = json.loads(cluster.aws("ssm", "send-command", "--instance-ids", instance_id, "--document-name", "AWS-RunShellScript",
                                        "--parameters", json.dumps({"commands": ["set -e", *commands]}), "--query", "Command.CommandId"))
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(3)
        raw = cluster.aws("ssm", "get-command-invocation", "--command-id", command_id, "--instance-id", instance_id, check=False)
        result = json.loads(raw) if raw else {}
        if result.get("Status") == "Success":
            return result["StandardOutputContent"].strip()
        if result.get("Status") in {"Failed", "Cancelled", "TimedOut"}:
            raise SystemExit(f"Command on the server failed:\n{(result.get('StandardErrorContent') or result.get('StandardOutputContent') or '')[-1500:]}")
    raise SystemExit("Command on the server did not finish in time.")


def session_plugin_env() -> dict[str, str]:
    """The AWS Session Manager plugin (for the database tunnel). Downloaded once into .nexops-local/tools if missing."""
    if shutil.which("session-manager-plugin"):
        return {}
    binary = TOOLS / "sessionmanager-bundle" / "bin" / "session-manager-plugin"
    if not binary.exists():
        if platform.system() != "Darwin":
            raise SystemExit("Install the AWS Session Manager plugin first: "
                             "https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html")
        arch = "mac_arm64" if platform.machine() == "arm64" else "mac"
        print("Downloading the AWS Session Manager plugin (official AWS build) into .nexops-local/tools ...")
        TOOLS.mkdir(parents=True, exist_ok=True)
        archive = TOOLS / "sessionmanager-bundle.zip"
        urllib.request.urlretrieve(PLUGIN_URL.format(arch=arch), archive)
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(TOOLS)
        archive.unlink()
        binary.chmod(0o755)
    return {"PATH": f"{binary.parent}{os.pathsep}{os.environ.get('PATH', '')}"}


@contextmanager
def database_tunnel(instance_id: str):
    """Encrypted tunnel (SSM port forwarding) from localhost:15432 to the NexOps PostgreSQL container on the server."""
    address = remote(instance_id, "docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' nexops-nexops-db-1")
    if port_in_use(TUNNEL_PORT):
        raise SystemExit(f"Port {TUNNEL_PORT} is already in use (an old tunnel?). Close it with: pkill -f session-manager-plugin")
    # Own process group: the aws CLI starts session-manager-plugin as a child, and both must be closed afterwards.
    process = subprocess.Popen(
        ["aws", "ssm", "start-session", "--region", cluster.region(), "--target", instance_id,
         "--document-name", "AWS-StartPortForwardingSessionToRemoteHost",
         "--parameters", json.dumps({"host": [address], "portNumber": ["5432"], "localPortNumber": [str(TUNNEL_PORT)]})],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env={**os.environ, **session_plugin_env()},
        start_new_session=True,
    )
    try:
        deadline = time.time() + 60
        while True:
            if process.poll() is not None:
                raise SystemExit("The database tunnel failed:\n" + (process.stdout.read() or b"").decode()[-800:])
            try:
                socket.create_connection(("127.0.0.1", TUNNEL_PORT), timeout=1).close()
                break
            except OSError:
                if time.time() > deadline:
                    raise SystemExit("The database tunnel did not open within 60 seconds.") from None
                time.sleep(1)
        yield f"-h host.docker.internal -p {TUNNEL_PORT} -U nexops -d nexops"
    finally:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(process.pid, sig)
            except ProcessLookupError:
                break
            deadline = time.time() + 10
            while time.time() < deadline and port_in_use(TUNNEL_PORT):
                time.sleep(0.5)
            if not port_in_use(TUNNEL_PORT):
                break
        process.wait(timeout=10)


def port_in_use(port: int) -> bool:
    try:
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
        return True
    except OSError:
        return False


# ------------------------------------------------------------------------------------------ local PostgreSQL copy
def cloud_data(script: str, password: str | None = None) -> str:
    """Run a shell script inside the local nexops-cloud-data container (PGPASSWORD passed by name, not in argv)."""
    command = ["docker", "exec", *(["-e", "PGPASSWORD"] if password else []), CLOUD_DATA_CONTAINER, "sh", "-c", script]
    return cluster.run(command, quiet=True, env={"PGPASSWORD": password} if password else None)


def cloud_data_up() -> None:
    cluster.run(["docker", "compose", "-f", str(CLOUD_DATA), "up", "-d", "--wait"], quiet=True)


def saved_summary() -> str | None:
    """What the local copy holds, or None if nothing was saved yet."""
    if not cluster.run(["docker", "ps", "-a", "--filter", f"name=^{CLOUD_DATA_CONTAINER}$", "--format", "{{.Names}}"], quiet=True, check=False):
        return None
    cloud_data_up()
    if cloud_data("psql -U nexops -d nexops -At -c \"SELECT to_regclass('public.alembic_version') IS NOT NULL\"") != "t":
        return None
    return cloud_data(f'psql -U nexops -d nexops -At -c "{COUNTS}"')


def wait_for_database(instance_id: str) -> None:
    remote(instance_id, "for i in $(seq 1 60); do docker exec nexops-nexops-db-1 pg_isready -U nexops -d nexops >/dev/null 2>&1 && exit 0; sleep 5; done; exit 1")


def pull_database(item: dict) -> str:
    """Copy the online NexOps database into the local nexops-cloud-data PostgreSQL (replacing the previous copy)."""
    cloud_data_up()
    if item["State"]["Name"] != "running":
        print("The server is stopped; starting it to read its database...")
        cluster.aws("ec2", "start-instances", "--instance-ids", item["InstanceId"])
        cluster.aws("ec2", "wait", "instance-running", "--instance-ids", item["InstanceId"])
        wait_ssm_online(item["InstanceId"])
    wait_for_database(item["InstanceId"])
    password = ssm("NEXOPS_DB_PASSWORD")
    with database_tunnel(item["InstanceId"]) as online:
        cloud_data(f"pg_dump {online} --format=custom --file=/tmp/online.dump", password)
        online_counts = cloud_data(f'psql {online} -At -c "{COUNTS}"', password)
    # Only now (the dump is complete) replace the previous local copy.
    cloud_data("psql -U nexops -d nexops -q -c 'DROP SCHEMA public CASCADE; CREATE SCHEMA public;' "
               "&& pg_restore -U nexops -d nexops --no-owner --exit-on-error /tmp/online.dump && rm -f /tmp/online.dump")
    local_counts = cloud_data(f'psql -U nexops -d nexops -At -c "{COUNTS}"')
    if local_counts != online_counts:
        raise SystemExit(f"The local copy does not match the server (online: {online_counts}; local: {local_counts}). Nothing was deleted.")
    set_env_values({local: ssm(name) for local, name in SAVED_SECRETS.items()})
    return local_counts


def push_database(item: dict) -> str:
    """Restore the local copy into the (new) online database, then migrate and restart NexOps."""
    instance_id = item["InstanceId"]
    wait_for_database(instance_id)
    cloud_data("pg_dump -U nexops -d nexops --format=custom --file=/tmp/local.dump")
    remote(instance_id, f"{PLATFORM} stop nexops-api nexops-worker")
    try:
        with database_tunnel(instance_id) as online:
            cloud_data(f"pg_restore {online} --clean --if-exists --no-owner --single-transaction --exit-on-error /tmp/local.dump; "
                       "status=$?; rm -f /tmp/local.dump; exit $status", ssm("NEXOPS_DB_PASSWORD"))
    finally:
        # Newer code may have newer migrations; the admin password follows SSM (the same one, saved in deploy/.env).
        remote(instance_id, f"{PLATFORM} run --rm nexops-migrate", f"{PLATFORM} up -d nexops-api nexops-worker",
               "sleep 5", "docker exec nexops-nexops-api-1 sh -c 'python -m app.cli create-user --email \"$NEXOPS_ADMIN_EMAIL\" --role admin --password \"$NEXOPS_ADMIN_PASSWORD\"'")
    return cloud_data(f'psql -U nexops -d nexops -At -c "{COUNTS}"')


# ------------------------------------------------------------------------------------------------------- GitHub
def github(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    token = cluster.env_file().get("GITHUB_TOKEN")
    if not token:
        raise SystemExit("GITHUB_TOKEN is missing in deploy/.env (needed to update the GitHub variables and run the deploy workflows).")
    request = urllib.request.Request(
        f"https://api.github.com/repos/{tfvar('github_repository')}{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


def set_github_variables(values: dict[str, str]) -> None:
    for name, value in values.items():
        status_code, _ = github("PATCH", f"/actions/variables/{name}", {"name": name, "value": value})
        if status_code == 404:
            status_code, _ = github("POST", "/actions/variables", {"name": name, "value": value})
        if status_code >= 300:
            raise SystemExit(f"Could not set the GitHub variable {name} (HTTP {status_code}); the token needs Variables: read and write.")
    print(f"GitHub variables updated: {', '.join(values)}")


def run_workflow(workflow: str, inputs: dict | None = None, timeout: int = 1800) -> None:
    started = datetime.now(timezone.utc) - timedelta(seconds=10)
    status_code, body = github("POST", f"/actions/workflows/{workflow}/dispatches", {"ref": "main", "inputs": inputs or {}})
    if status_code >= 300:
        raise SystemExit(f"Could not start {workflow} (HTTP {status_code}): {body.get('message')}")
    print(f"Running GitHub Actions {workflow} ...", flush=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(15)
        _, runs = github("GET", f"/actions/workflows/{workflow}/runs?event=workflow_dispatch&per_page=5")
        run = next((item for item in runs.get("workflow_runs", [])
                    if datetime.fromisoformat(item["created_at"].replace("Z", "+00:00")) >= started), None)
        if run and run["status"] == "completed":
            if run["conclusion"] != "success":
                raise SystemExit(f"{workflow} finished with {run['conclusion']}: {run['html_url']}")
            print(f"{workflow}: success")
            return
    raise SystemExit(f"{workflow} did not finish in {timeout // 60} minutes.")


# ------------------------------------------------------------------------------------------------------ helpers
def wait_ssm_online(instance_id: str, timeout: int = 600) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        raw = cluster.aws("ssm", "describe-instance-information", "--filters", f"Key=InstanceIds,Values={instance_id}",
                          "--query", "InstanceInformationList[0].PingStatus", check=False)
        if raw and json.loads(raw) == "Online":
            return
        time.sleep(10)
    raise SystemExit("The server did not come online in SSM.")


def wait_https(url: str, timeout: int = 600) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if get_json(f"{url}/ready"):
            return True
        time.sleep(10)
    return False


def terraform_outputs() -> dict:
    return {key: item["value"] for key, item in json.loads(cluster.terraform("output", "-json", capture=True, directory=AWS_DIR)).items()}


def empty_and_delete_bucket(bucket: str) -> None:
    """The state bucket is versioned: every version and delete marker must go before the bucket can be removed."""
    while True:
        raw = cluster.aws("s3api", "list-object-versions", "--bucket", bucket, "--max-items", "1000", check=False)
        listing = json.loads(raw) if raw else {}
        objects = [{"Key": item["Key"], "VersionId": item["VersionId"]} for item in (listing.get("Versions") or []) + (listing.get("DeleteMarkers") or [])]
        if not objects:
            break
        cluster.aws("s3api", "delete-objects", "--bucket", bucket, "--delete", json.dumps({"Objects": objects, "Quiet": True}))
    cluster.aws("s3api", "delete-bucket", "--bucket", bucket, check=False)
    print(f"Deleted state bucket {bucket}")


def leftover_parameters() -> list[str]:
    """SSM parameters under /nexops/ (Terraform's own are gone after destroy; ones added by hand, like the Slack URL, are not)."""
    raw = cluster.aws("ssm", "get-parameters-by-path", "--path", f"/{PROJECT}/", "--recursive", "--query", "Parameters[].Name", check=False)
    return json.loads(raw) if raw else []


def leftovers() -> list[str]:
    """Anything billable or NexOps-owned still in AWS."""
    found = []
    if find_instance():
        found.append("EC2 instance")
    addresses = json.loads(cluster.aws("ec2", "describe-addresses", "--filters", f"Name=tag:Project,Values={PROJECT}", "--query", "Addresses[].PublicIp") or "[]")
    found += [f"Elastic IP {address}" for address in addresses]
    buckets = json.loads(cluster.aws("s3api", "list-buckets", "--query", "Buckets[].Name") or "[]")
    found += [f"S3 bucket {bucket}" for bucket in buckets if bucket.startswith(f"{PROJECT}-")]
    repositories = json.loads(cluster.aws("ecr", "describe-repositories", "--query", "repositories[].repositoryName", check=False) or "[]")
    found += [f"ECR repository {name}" for name in repositories if name in {f"{PROJECT}-api", f"{PROJECT}-web", "payment-api", "traffic-generator"}]
    if eks_exists():
        found.append(f"EKS cluster {PROJECT}-eks")
    found += [f"SSM parameter {name}" for name in leftover_parameters()]
    return found


# ----------------------------------------------------------------------------------------------------- commands
def status(_: argparse.Namespace) -> None:
    item = find_instance()
    if item is None:
        print("AWS: nothing running (no costs).")
    else:
        url = url_for(item)
        print(f"AWS: EC2 {item['InstanceId']} ({item['InstanceType']}) is {item['State']['Name']}")
        if url and item["State"]["Name"] == "running":
            print(f"     NexOps {url}  ({'ready' if get_json(f'{url}/ready') else 'not answering yet'})")
    print(f"EKS {PROJECT}-eks: {'exists (billed hourly)' if eks_exists() else 'not created (no charges)'}")
    summary = saved_summary()
    print(f"Saved online data on this Mac (Docker {CLOUD_DATA_CONTAINER}): {summary or 'nothing saved yet'}")


def up(args: argparse.Namespace) -> None:
    """Create (or complete) the AWS stack, deploy NexOps and payment-api, restore the saved data."""
    cluster.need("aws", "docker")
    bucket = state_bucket()
    if not bucket_exists(bucket):
        print(f"Creating the Terraform state bucket {bucket}")
        cluster.aws("s3api", "create-bucket", "--bucket", bucket, "--create-bucket-configuration", f"LocationConstraint={cluster.region()}")
        cluster.aws("s3api", "put-bucket-versioning", "--bucket", bucket, "--versioning-configuration", "Status=Enabled")
    existed = find_instance() is not None
    init_aws_stack()
    env = tf_env()
    plan = AWS_DIR / "tfplan"
    try:
        cluster.terraform("plan", "-input=false", "-out=tfplan", directory=AWS_DIR, env=env)
        changes = json.loads(cluster.terraform("show", "-json", "tfplan", directory=AWS_DIR, capture=True)).get("resource_changes") or []
        deleting = [change["address"] for change in changes if "delete" in change["change"]["actions"]]
        if deleting:
            raise SystemExit(f"up only creates; this plan would also delete or replace {', '.join(deleting)}. Review it with: python3 scripts/aws/hosted.py apply")
        if any(change["change"]["actions"] != ["no-op"] for change in changes):
            log("Creating the AWS resources (~5 min)")
            cluster.terraform("apply", "-input=false", "tfplan", directory=AWS_DIR, env=env)
    finally:
        plan.unlink(missing_ok=True)

    notify = cluster.env_file().get("NEXOPS_NOTIFY_WEBHOOK_URL")
    if notify:  # Slack: kept in deploy/.env, put back into SSM (it is not managed by Terraform)
        cluster.aws("ssm", "put-parameter", "--name", f"/{PROJECT}/NEXOPS_NOTIFY_WEBHOOK_URL", "--type", "SecureString", "--overwrite", "--value", notify)
    outputs = terraform_outputs()
    item = instance()
    if item["State"]["Name"] != "running":
        cluster.aws("ec2", "start-instances", "--instance-ids", item["InstanceId"])
        cluster.aws("ec2", "wait", "instance-running", "--instance-ids", item["InstanceId"])
        item = instance()
    log("Waiting for the server to finish installing Docker")
    wait_ssm_online(item["InstanceId"])
    remote(item["InstanceId"], "for i in $(seq 1 90); do docker compose version >/dev/null 2>&1 && exit 0; sleep 5; done; exit 1")

    set_github_variables({
        "AWS_REGION": cluster.region(), "AWS_ROLE_ARN": outputs["github_role_arn"], "DEPLOY_BUCKET": outputs["bundle_bucket"],
        "EC2_INSTANCE_ID": outputs["instance_id"], "NEXOPS_URL": outputs["nexops_url"], "DEPLOY_TARGET": "ec2", "DEMO_TRAFFIC": "true",
        "HOSTED_UP": "true",
    })
    log("Deploying NexOps")
    run_workflow("deploy-nexops.yml")
    url = outputs["nexops_url"]
    wait_https(url)
    if not existed or args.restore:
        summary = saved_summary()
        if summary:
            log("Restoring your data from the Docker container nexops-cloud-data")
            print(f"Restored: {push_database(item)}")
        else:
            print("No saved data on this Mac yet; starting with an empty NexOps.")
    log("Deploying payment-api")
    run_workflow("deploy-payment-api.yml", {"reason": "hosted.py up"})
    register(args)
    print(f"""
NexOps is online: {url}
Sign in:          python3 scripts/aws/hosted.py login
When you are done: python3 scripts/aws/hosted.py down   (saves your data on this Mac, deletes everything in AWS)""")


def down(args: argparse.Namespace) -> None:
    """Save the online data into Docker on this Mac, then delete everything NexOps created in AWS."""
    cluster.need("aws", "docker")
    init_aws_stack()
    if args.preview:
        cluster.terraform("plan", "-destroy", "-input=false", "-lock=false", directory=AWS_DIR)
        print(f"\nAlso: EKS {PROJECT}-eks {'(exists) would be removed first' if eks_exists() else 'does not exist'}; the state bucket "
              f"{state_bucket()} and every SSM parameter under /{PROJECT}/ would be deleted. Before that, the online database would be "
              f"copied into the Docker container {CLOUD_DATA_CONTAINER}.")
        return
    item = find_instance()
    if item and not args.no_save:
        log(f"Saving the online data into the Docker container {CLOUD_DATA_CONTAINER} (PostgreSQL on localhost:5434)")
        print(f"Saved: {pull_database(item)}")
    elif args.no_save:
        print("Not saving the online data (--no-save).")
    if not args.yes:
        prompt = ("Delete EVERYTHING NexOps created in AWS now? Your data is saved on this Mac. [y/N] " if item and not args.no_save
                  else f"Delete EVERYTHING NexOps created in AWS now, WITHOUT saving the data? Type 'delete {PROJECT}': ")
        try:
            answer = input(prompt).strip().lower()
        except EOFError:
            answer = ""
        if answer not in ({"y", "yes"} if item and not args.no_save else {f"delete {PROJECT}"}):
            raise SystemExit("Cancelled; nothing in AWS was deleted.")
    if eks_exists():
        cluster.eks_down(args)
    log("Deleting the AWS resources (~10 min)")
    cluster.terraform("destroy", "-input=false", "-auto-approve", directory=AWS_DIR, env=tf_env())
    for name in leftover_parameters():  # added outside Terraform (the Slack URL)
        cluster.aws("ssm", "delete-parameter", "--name", name, check=False)
    empty_and_delete_bucket(state_bucket())
    try:  # pushes while AWS is down skip the deploy workflows instead of failing
        set_github_variables({"HOSTED_UP": "false"})
    except SystemExit as error:
        print(f"Note: {error}")
    remaining = leftovers()
    if remaining:
        print("Still in AWS (check the console): " + "; ".join(remaining))
    else:
        print("\nNothing NexOps created is left in AWS, so there are no AWS costs."
              + (f" Your online data waits in Docker ({CLOUD_DATA_CONTAINER}); bring it all back with: python3 scripts/aws/hosted.py up"
                 if item and not args.no_save else ""))


def destroy(args: argparse.Namespace) -> None:
    """Delete everything in AWS without saving the data (for when you abandon the online setup)."""
    args.no_save = True
    down(args)
    print("""Left for you (outside AWS resources): the IAM user 'nexops' (IAM console -> Users -> nexops -> Delete) and the
GitHub repository variables and secret (Settings -> Secrets and variables -> Actions).""")


def save(_: argparse.Namespace) -> None:
    print(f"Saved into Docker {CLOUD_DATA_CONTAINER}: {pull_database(instance())}")


def log(message: str) -> None:
    print(f"\n==> {message}", flush=True)


def start(_: argparse.Namespace) -> None:
    item = instance()
    cluster.aws("ec2", "start-instances", "--instance-ids", item["InstanceId"])
    print(f"Starting {item['InstanceId']}...")
    cluster.aws("ec2", "wait", "instance-running", "--instance-ids", item["InstanceId"])
    url = url_for(instance())
    print(f"NexOps is up: {url}" if wait_https(url) else f"Instance is running but {url} is not answering yet; try again in a minute.")
    print("Sign-in details: python3 scripts/aws/hosted.py login")


def stop(args: argparse.Namespace) -> None:
    item = instance()
    if args.dry_run:
        cluster.run(["aws", "ec2", "stop-instances", "--region", cluster.region(), "--instance-ids", item["InstanceId"], "--dry-run"], check=False)
        return
    cluster.aws("ec2", "stop-instances", "--instance-ids", item["InstanceId"])
    print(f"Stopping {item['InstanceId']}...")
    cluster.aws("ec2", "wait", "instance-stopped", "--instance-ids", item["InstanceId"])
    print("Stopped (about $6/month while stopped). Start: python3 scripts/aws/hosted.py start. For no cost at all: hosted.py down")


def login(_: argparse.Namespace) -> None:
    url = url_for(instance())
    print(f"URL:      {url}\nEmail:    {ssm('NEXOPS_ADMIN_EMAIL')}\nPassword: {ssm('NEXOPS_ADMIN_PASSWORD')}")


def apply(_: argparse.Namespace) -> None:
    """terraform apply for the EC2 stack with the secrets from deploy/.env (without them Terraform would remove the
    Groq/GitHub SSM parameters or generate a new login)."""
    env = tf_env()
    init_aws_stack()
    plan = AWS_DIR / "tfplan"
    try:
        cluster.terraform("plan", "-input=false", "-out=tfplan", directory=AWS_DIR, env=env)
        changes = json.loads(cluster.terraform("show", "-json", "tfplan", directory=AWS_DIR, capture=True)).get("resource_changes") or []
        if all(change["change"]["actions"] == ["no-op"] for change in changes):
            print("Nothing to apply.")
            return
        try:
            answer = input("Apply this plan? [y/N] ")
        except EOFError:
            answer = ""
        if answer.strip().lower() != "y":
            raise SystemExit("Not applied.")
        cluster.terraform("apply", "-input=false", "tfplan", directory=AWS_DIR, env=env)
    finally:
        plan.unlink(missing_ok=True)


def outputs(args: argparse.Namespace) -> None:
    """GitHub repository variables and secret for the workflows, from the Terraform outputs."""
    init_aws_stack()
    values = terraform_outputs()
    variables = {
        "AWS_REGION": cluster.region(), "AWS_ROLE_ARN": values["github_role_arn"], "DEPLOY_BUCKET": values["bundle_bucket"],
        "EC2_INSTANCE_ID": values["instance_id"], "NEXOPS_URL": values["nexops_url"], "DEPLOY_TARGET": "ec2", "DEMO_TRAFFIC": "true",
    }
    print(f"\nGitHub -> {tfvar('github_repository') or 'your repository'} -> Settings -> Secrets and variables -> Actions\n\nVariables:")
    for key, value in variables.items():
        print(f"  {key:<16} {value}")
    secret = ssm("NEXOPS_WEBHOOK_SECRET") if args.show_secret else "(run again with --show-secret to print it)"
    print(f"\nSecret:\n  NEXOPS_WEBHOOK_SECRET {secret}\n\nEnvironment: production (Settings -> Environments)")


def register(_: argparse.Namespace) -> None:
    """Register payment-api in the hosted NexOps with the EC2 settings (Docker, CloudWatch, GitHub Actions rollback)."""
    item = instance()
    url = url_for(item)
    account = json.loads(cluster.aws("sts", "get-caller-identity"))["Account"]
    cluster.run([sys.executable, str(ROOT / "scripts" / "register_local_app.py"), "--url", url, "--runtime", "ec2-host",
                 "--image-repository", f"{account}.dkr.ecr.{cluster.region()}.amazonaws.com/payment-api",
                 "--repository", tfvar("github_repository"), "--ec2-instance-id", item["InstanceId"]],
                env={"NEXOPS_ADMIN_EMAIL": ssm("NEXOPS_ADMIN_EMAIL"), "NEXOPS_ADMIN_PASSWORD": ssm("NEXOPS_ADMIN_PASSWORD")}, quiet=True)
    print("payment-api registered in the hosted NexOps")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    up_parser = commands.add_parser("up", help="create everything in AWS and restore the saved data")
    up_parser.add_argument("--restore", action="store_true", help="also restore when the server already existed (overwrites online data)")
    up_parser.set_defaults(handler=up)
    down_parser = commands.add_parser("down", help="save the data on this Mac, delete everything in AWS")
    down_parser.add_argument("--preview", action="store_true", help="show what would be deleted; delete nothing")
    down_parser.add_argument("--no-save", action="store_true", help="do not copy the online data first")
    down_parser.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    down_parser.set_defaults(handler=down)
    commands.add_parser("status").set_defaults(handler=status)
    commands.add_parser("save").set_defaults(handler=save)
    commands.add_parser("login").set_defaults(handler=login)
    commands.add_parser("start").set_defaults(handler=start)
    stop_parser = commands.add_parser("stop")
    stop_parser.add_argument("--dry-run", action="store_true", help="only check that the stop would be allowed")
    stop_parser.set_defaults(handler=stop)
    commands.add_parser("apply").set_defaults(handler=apply)
    outputs_parser = commands.add_parser("outputs")
    outputs_parser.add_argument("--show-secret", action="store_true", help="also print NEXOPS_WEBHOOK_SECRET")
    outputs_parser.set_defaults(handler=outputs)
    commands.add_parser("register").set_defaults(handler=register)
    destroy_parser = commands.add_parser("destroy", help="delete everything in AWS without saving the data")
    destroy_parser.add_argument("--preview", action="store_true")
    destroy_parser.add_argument("--yes", action="store_true")
    destroy_parser.set_defaults(handler=destroy, no_save=True)
    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
