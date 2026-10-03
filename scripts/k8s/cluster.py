#!/usr/bin/env python3
"""NexOps on Kubernetes: the whole platform plus the payment-api Argo Rollout, on kind (local) or EKS (on demand).

  python3 scripts/k8s/cluster.py kind-up     # free, local: kind + Argo Rollouts + NexOps + Prometheus + Jaeger
  python3 scripts/k8s/cluster.py kind-stop     # pause it (keeps everything); kind-up --skip-build resumes it
  python3 scripts/k8s/cluster.py kind-down
  python3 scripts/k8s/cluster.py eks-up      # AWS, billed while it exists (about $0.30/hour): EKS + NLB/HTTPS + Argo Rollouts
  python3 scripts/k8s/cluster.py eks-down    # removes the load balancer and volumes first, then terraform destroy
  python3 scripts/k8s/cluster.py status

Secrets come from deploy/.env (kind) or SSM Parameter Store /nexops/* (EKS) and are written straight into Kubernetes
Secrets; nothing secret is written to disk. Releases: scripts/deploy_local.py <version> --target kind|eks.
"""
from __future__ import annotations

import argparse
import base64
import http.client
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
K8S = ROOT / "deploy" / "k8s"
ENV_FILE = ROOT / "deploy" / ".env"
EKS_DIR = ROOT / "infra" / "terraform" / "eks"
AWS_DIR = ROOT / "infra" / "terraform" / "aws"
KIND_CLUSTER, KIND_CONTEXT, EKS_CONTEXT = "nexops", "kind-nexops", "nexops-eks"
ARGO_ROLLOUTS = "v1.10.0"
METRICS_SERVER = "v0.9.0"
TERRAFORM_IMAGE = "hashicorp/terraform:1.16.5"
PROJECT = "nexops"


def log(message: str) -> None:
    print(f"\n==> {message}", flush=True)


def run(command: list[str], *, check: bool = True, capture: bool = False, stdin: str | None = None, quiet: bool = False, env: dict | None = None) -> str:
    if not quiet:
        print("$ " + " ".join(command), flush=True)
    result = subprocess.run(command, input=stdin, text=True, capture_output=capture or quiet, env={**os.environ, **env} if env else None)
    if check and result.returncode != 0:
        if capture or quiet:
            sys.stderr.write(result.stderr or result.stdout or "")
        raise SystemExit(f"Command failed ({result.returncode}): {' '.join(command[:4])} ...")
    return (result.stdout or "").strip() if (capture or quiet) else ""


def need(*tools: str) -> None:
    missing = [tool for tool in tools if not shutil.which(tool)]
    if missing:
        raise SystemExit(f"Missing tools: {', '.join(missing)} (macOS: brew install {' '.join(missing)})")


def env_file() -> dict[str, str]:
    values: dict[str, str] = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip().strip("'\"")
    return values


def app_version() -> str:
    return "v" + (ROOT / "examples" / "payment-api" / "VERSION").read_text().strip().lstrip("v")


# ----------------------------------------------------------------------------------------------- kubernetes helpers
def kubectl(context: str, *args: str, **kwargs) -> str:
    return run(["kubectl", "--context", context, *args], **kwargs)


def read_secret(context: str, namespace: str, name: str) -> dict[str, str]:
    raw = kubectl(context, "-n", namespace, "get", "secret", name, "-o", "json", check=False, quiet=True)
    if not raw:
        return {}
    data = json.loads(raw).get("data") or {}
    return {key: base64.b64decode(value).decode() for key, value in data.items()}


def apply_secret(context: str, namespace: str, name: str, data: dict[str, str]) -> None:
    """kubectl apply from stdin, so secret values never appear in argv or on disk."""
    manifest = {
        "apiVersion": "v1", "kind": "Secret", "type": "Opaque",
        "metadata": {"name": name, "namespace": namespace},
        "data": {key: base64.b64encode(value.encode()).decode() for key, value in data.items()},
    }
    kubectl(context, "apply", "-f", "-", stdin=json.dumps(manifest), quiet=True)
    print(f"secret {namespace}/{name} applied ({len(data)} keys)")


def ensure_namespaces(context: str) -> None:
    for namespace in ("nexops", "payments", "observability"):
        manifest = {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": namespace}}
        kubectl(context, "apply", "-f", "-", stdin=json.dumps(manifest), quiet=True)


def write_secrets(context: str, values: dict[str, str], public_url: str) -> None:
    """nexops-secrets (NexOps), payments-db and nexops-tokens (payment-api flags + Argo analysis)."""
    ensure_namespaces(context)
    current = read_secret(context, "nexops", "nexops-secrets")
    db_password = current.get("NEXOPS_DB_PASSWORD") or values.get("NEXOPS_DB_PASSWORD") or secrets.token_hex(16)
    data = {
        "NEXOPS_DB_PASSWORD": db_password,
        "DATABASE_URL": f"postgresql+psycopg://nexops:{db_password}@nexops-db:5432/nexops",
        "NEXOPS_SECRET_KEY": values.get("NEXOPS_SECRET_KEY") or current.get("NEXOPS_SECRET_KEY") or secrets.token_urlsafe(48),
        "NEXOPS_WEBHOOK_SECRET": values.get("NEXOPS_WEBHOOK_SECRET") or current.get("NEXOPS_WEBHOOK_SECRET") or secrets.token_hex(32),
        "NEXOPS_ADMIN_EMAIL": values["NEXOPS_ADMIN_EMAIL"],
        "NEXOPS_ADMIN_PASSWORD": values["NEXOPS_ADMIN_PASSWORD"],
        "NEXOPS_PUBLIC_URL": public_url,
        "NEXOPS_FLAGS_TOKEN": values.get("NEXOPS_FLAGS_TOKEN") or current.get("NEXOPS_FLAGS_TOKEN") or secrets.token_urlsafe(30),
        "NEXOPS_ANALYSIS_TOKEN": current.get("NEXOPS_ANALYSIS_TOKEN") or secrets.token_urlsafe(30),
        "NEXOPS_NOTIFY_WEBHOOK_URL": values.get("NEXOPS_NOTIFY_WEBHOOK_URL", ""),
        "GROQ_API_KEY": values.get("GROQ_API_KEY", ""),
        "GITHUB_TOKEN": values.get("GITHUB_TOKEN", ""),
    }
    apply_secret(context, "nexops", "nexops-secrets", data)
    payments = read_secret(context, "payments", "payments-db")
    apply_secret(context, "payments", "payments-db", {"password": payments.get("password") or secrets.token_hex(16)})
    apply_secret(context, "payments", "nexops-tokens", {"flags-token": data["NEXOPS_FLAGS_TOKEN"], "analysis-token": data["NEXOPS_ANALYSIS_TOKEN"]})


def current_payment_image(context: str) -> str | None:
    image = kubectl(context, "-n", "payments", "get", "rollout", "payment-api", "-o", "jsonpath={.spec.template.spec.containers[0].image}", check=False, quiet=True)
    return image or None


def apply_overlay(context: str, overlay: str, replacements: dict[str, str]) -> None:
    rendered = kubectl(context, "kustomize", str(K8S / "overlays" / overlay), quiet=True)
    for placeholder, value in replacements.items():
        rendered = rendered.replace(placeholder, value)
    kubectl(context, "apply", "--server-side", "--force-conflicts", "-f", "-", stdin=rendered, quiet=True)
    print(f"applied deploy/k8s/overlays/{overlay}")


def wait_platform(context: str, timeout: int = 600) -> None:
    log("Waiting for NexOps, Prometheus, Jaeger and payment-api")
    for namespace, kind, name in (("nexops", "statefulset", "nexops-db"), ("nexops", "deployment", "nexops-api"), ("nexops", "deployment", "nexops-worker"),
                                  ("nexops", "deployment", "nexops-web"), ("observability", "deployment", "prometheus"), ("observability", "deployment", "jaeger"),
                                  ("payments", "statefulset", "payments-db")):
        kubectl(context, "-n", namespace, "rollout", "status", f"{kind}/{name}", f"--timeout={timeout}s")
    wait_rollout(context, timeout)


def wait_rollout(context: str, timeout: int = 600) -> str:
    deadline = time.time() + timeout
    phase = ""
    while time.time() < deadline:
        raw = kubectl(context, "-n", "payments", "get", "rollout", "payment-api", "-o", "json", check=False, quiet=True)
        status = (json.loads(raw).get("status") or {}) if raw else {}
        phase = status.get("phase") or ""
        if phase in {"Healthy", "Degraded"}:
            print(f"rollout payments/payment-api: {phase} {status.get('message') or ''}".rstrip())
            return phase
        time.sleep(5)
    raise SystemExit(f"payment-api rollout did not settle in {timeout}s (phase {phase or 'unknown'}).")


def register(url: str, values: dict[str, str], image_repository: str) -> None:
    log(f"Registering payment-api in NexOps at {url}")
    deadline = time.time() + 180
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/ready", timeout=5) as response:
                if response.status == 200:
                    break
        except OSError:
            pass
        time.sleep(3)
    # Credentials go through the environment, not argv.
    run([sys.executable, str(ROOT / "scripts" / "register_local_app.py"), "--url", url, "--runtime", "kubernetes", "--image-repository", image_repository],
        quiet=True, env={"NEXOPS_ADMIN_EMAIL": values["NEXOPS_ADMIN_EMAIL"], "NEXOPS_ADMIN_PASSWORD": values["NEXOPS_ADMIN_PASSWORD"]})
    print("payment-api registered (runtime kubernetes, Argo Rollout payments/payment-api) with feature flag fraud-check")


# ----------------------------------------------------------------------------------------------------------- kind
def kind_up(args: argparse.Namespace) -> None:
    need("kind", "kubectl", "docker")
    context = KIND_CONTEXT
    if KIND_CLUSTER not in run(["kind", "get", "clusters"], capture=True).split():
        log("Creating kind cluster 'nexops'")
        run(["kind", "create", "cluster", "--config", str(K8S / "overlays" / "kind" / "cluster.yaml"), "--wait", "120s"])
    else:
        # The node container does not restart with Docker Desktop (kind-stop or a reboot leaves it stopped).
        run(["docker", "start", f"{KIND_CLUSTER}-control-plane"], quiet=True)
        deadline = time.time() + 180
        while time.time() < deadline and kubectl(context, "get", "--raw", "/readyz", check=False, quiet=True) != "ok":
            time.sleep(3)

    log(f"Installing Argo Rollouts {ARGO_ROLLOUTS} and metrics-server {METRICS_SERVER}")
    kubectl(context, "create", "namespace", "argo-rollouts", check=False, quiet=True)
    kubectl(context, "apply", "-n", "argo-rollouts", "--server-side", "-f", f"https://github.com/argoproj/argo-rollouts/releases/download/{ARGO_ROLLOUTS}/install.yaml", quiet=True)
    kubectl(context, "apply", "-f", f"https://github.com/kubernetes-sigs/metrics-server/releases/download/{METRICS_SERVER}/components.yaml", quiet=True)
    # kind's kubelets use self-signed serving certificates.
    kubectl(context, "-n", "kube-system", "patch", "deployment", "metrics-server", "--type", "json",
            "-p", '[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]', check=False, quiet=True)
    kubectl(context, "-n", "argo-rollouts", "rollout", "status", "deployment/argo-rollouts", "--timeout=180s")

    version = args.version or (current_payment_image(context) or "").rpartition(":")[2] or app_version()
    log(f"Building images (payment-api {version}) and loading them into kind")
    images = {
        "nexops/api:local": ["docker", "build", "-q", "-t", "nexops/api:local", str(ROOT / "backend")],
        "nexops/web:local": ["docker", "build", "-q", "-f", str(ROOT / "Dockerfile.web"), "-t", "nexops/web:local", str(ROOT)],
        f"nexops/payment-api:{version}": ["docker", "build", "-q", "--build-arg", f"APP_VERSION={version}", "-t", f"nexops/payment-api:{version}", str(ROOT / "examples" / "payment-api")],
        "nexops/traffic-generator:local": ["docker", "build", "-q", "-t", "nexops/traffic-generator:local", str(ROOT / "examples" / "traffic-generator")],
    }
    for image, build in images.items():
        if args.skip_build and run(["docker", "image", "inspect", image], check=False, quiet=True):
            continue
        run(build, quiet=True)
        print(f"built {image}")
    run(["kind", "load", "docker-image", "--name", KIND_CLUSTER, *images])

    values = env_file()
    write_secrets(context, values, "http://localhost:8090")
    apply_overlay(context, "kind", {"PAYMENT_API_TAG": version})
    kubectl(context, "-n", "nexops", "rollout", "restart", "deployment/nexops-api", "deployment/nexops-worker", "deployment/nexops-web", quiet=True)
    wait_platform(context)
    register("http://localhost:8090", values, "nexops/payment-api")
    print(f"""
NexOps on kind is up.
  NexOps UI      http://localhost:8090   (sign in with NEXOPS_ADMIN_EMAIL / NEXOPS_ADMIN_PASSWORD from deploy/.env)
  Jaeger UI      http://localhost:16687
  Prometheus     http://localhost:9091
  Argo Rollouts  kubectl argo rollouts --context {context} -n payments get rollout payment-api --watch
  Release        python3 scripts/deploy_local.py v1.4.0 --target kind                 (healthy canary, promoted)
  Bad release    python3 scripts/deploy_local.py v1.4.1 --target kind --break-schema  (NexOps fails the canary, Argo aborts)
  Tear down      python3 scripts/k8s/cluster.py kind-down""")


def kind_stop(_: argparse.Namespace) -> None:
    run(["docker", "stop", f"{KIND_CLUSTER}-control-plane"])
    print("kind cluster stopped (data kept). Resume: python3 scripts/k8s/cluster.py kind-up --skip-build")


def kind_down(_: argparse.Namespace) -> None:
    need("kind")
    run(["kind", "delete", "cluster", "--name", KIND_CLUSTER])


# ------------------------------------------------------------------------------------------------------------ EKS
def aws(*args: str, check: bool = True) -> str:
    return run(["aws", *args, "--region", region(), "--output", "json"], check=check, quiet=True)


def region() -> str:
    return os.getenv("AWS_REGION") or "ap-south-1"


def terraform(*args: str, capture: bool = False, directory: Path = EKS_DIR, env: dict[str, str] | None = None) -> str:
    """Local terraform if installed, otherwise the official image (AWS credentials from ~/.aws, profile AWS_PROFILE).

    `env` values (e.g. TF_VAR_* secrets) reach the container by name only, so they never appear in a command line."""
    if shutil.which("terraform"):
        return run(["terraform", f"-chdir={directory}", *args], capture=capture, env=env)
    profile = os.getenv("AWS_PROFILE", PROJECT)
    passthrough = [flag for key in (env or {}) for flag in ("-e", key)]
    command = ["docker", "run", "--rm", "-v", f"{directory}:/w", "-w", "/w", "-v", f"{Path.home() / '.aws'}:/root/.aws:ro",
               "-e", f"AWS_PROFILE={profile}", "-e", "TF_IN_AUTOMATION=1", *passthrough, TERRAFORM_IMAGE, *args]
    return run(command, capture=capture, env=env)


def terraform_init() -> None:
    backend = {}
    for line in (AWS_DIR / "backend.hcl").read_text().splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            backend[key.strip()] = value.strip().strip('"')
    terraform("init", "-input=false", "-reconfigure", f"-backend-config=bucket={backend['bucket']}", f"-backend-config=key={PROJECT}/eks.tfstate",
              f"-backend-config=region={backend['region']}", "-backend-config=encrypt=true", "-backend-config=use_lockfile=true")


def eks_variables() -> list[str]:
    github_role = json.loads(aws("iam", "get-role", "--role-name", f"{PROJECT}-github-deploy", check=False) or "{}").get("Role", {}).get("Arn", "")
    variables = ["-var", f"region={region()}", "-var", f"github_deploy_role_arn={github_role}"]
    versions = json.loads(aws("eks", "describe-cluster-versions", "--default-only", check=False) or "{}").get("clusterVersions") or []
    if versions:
        variables += ["-var", f"kubernetes_version={versions[0]['clusterVersion']}"]
    return variables


def ssm_values() -> dict[str, str]:
    raw = aws("ssm", "get-parameters-by-path", "--path", f"/{PROJECT}/", "--with-decryption", "--recursive")
    return {item["Name"].rsplit("/", 1)[-1]: item["Value"] for item in json.loads(raw)["Parameters"]}


def my_cidr() -> str:
    if os.getenv("ALLOWED_CIDR"):
        return os.environ["ALLOWED_CIDR"]
    with urllib.request.urlopen("https://checkip.amazonaws.com", timeout=10) as response:
        return response.read().decode().strip() + "/32"


def eks_up(args: argparse.Namespace) -> None:
    need("kubectl", "aws")
    identity = json.loads(aws("sts", "get-caller-identity"))
    account = identity["Account"]
    log(f"Creating the EKS stack in {region()} as {identity['Arn']} (billed hourly until eks-down)")
    terraform_init()
    variables = eks_variables()
    # Phase 1: network, cluster, nodes and add-ons. Phase 2: Helm releases with a fresh cluster token.
    terraform("apply", "-input=false", "-auto-approve", "-target=module.vpc", "-target=module.eks", "-target=module.ebs_csi_irsa", *variables)
    terraform("apply", "-input=false", "-auto-approve", *variables)
    outputs = {key: value["value"] for key, value in json.loads(terraform("output", "-json", capture=True)).items()}
    run(["aws", "eks", "update-kubeconfig", "--region", region(), "--name", outputs["cluster_name"], "--alias", EKS_CONTEXT])

    context = EKS_CONTEXT
    registry = f"{account}.dkr.ecr.{region()}.amazonaws.com"
    version = args.version or (current_payment_image(context) or "").rpartition(":")[2] or ecr_version(app_version())
    values = ssm_values()
    url = outputs["nexops_url"]
    log(f"Deploying NexOps (ECR nexops-api:{args.nexops_tag}) and payment-api {version} behind {url}")
    write_secrets(context, values, url)
    apply_overlay(context, "eks", {
        "ECR_REGISTRY": registry, "NEXOPS_TAG": args.nexops_tag, "PAYMENT_API_TAG": version, "NEXOPS_IRSA_ROLE_ARN": outputs["nexops_service_account_role_arn"],
        "AWS_REGION_NAME": region(), "EDGE_SUBNETS": outputs["edge_subnet_ids"], "EDGE_EIP_ALLOCATIONS": outputs["edge_eip_allocation_ids"],
        "NEXOPS_DOMAIN_NAME": url.removeprefix("https://"),
    })
    kubectl(context, "-n", "nexops", "rollout", "restart", "deployment/nexops-api", "deployment/nexops-worker", quiet=True)  # pick up new secrets
    wait_platform(context, timeout=900)

    log("Waiting for the Network Load Balancer and the Let's Encrypt certificate")
    deadline = time.time() + 900
    ready, routed_since, restarted = False, None, False
    host = url.removeprefix("https://")
    while time.time() < deadline and not ready:
        try:
            with urllib.request.urlopen(f"{url}/ready", timeout=10) as response:  # verifies the certificate
                ready = response.status == 200
        except OSError:
            # Caddy asks for the certificate as soon as it starts, usually before the new load balancer routes to it;
            # after a failed attempt it falls back to a slower CA. Once plain HTTP reaches Caddy, restart it once.
            routed_since = routed_since or (time.time() if http_reaches_caddy(host) else None)
            if routed_since and not restarted and time.time() - routed_since > 60:
                print("Load balancer is routing; restarting Caddy so it requests the certificate again")
                kubectl(context, "-n", "nexops", "delete", "pod", "caddy-0", "--wait=true", quiet=True)
                restarted = True
            time.sleep(15)
    if not ready:
        raise SystemExit(f"{url} is not reachable yet. Check: kubectl --context {context} -n kube-system logs deploy/aws-load-balancer-controller "
                         f"and kubectl --context {context} -n nexops logs statefulset/caddy")
    cidr = my_cidr()
    if cidr != "0.0.0.0/0":
        # The certificate is issued; from now on only your IP may connect (GitHub Actions uses a port-forward).
        kubectl(context, "-n", "nexops", "patch", "service", "nexops-edge", "--type", "merge", "-p", json.dumps({"spec": {"loadBalancerSourceRanges": [cidr]}}), quiet=True)
    register(url, values, f"{registry}/payment-api")
    print(f"""
NexOps on EKS is up (billed while it exists: tear down with `python3 scripts/k8s/cluster.py eks-down`).
  NexOps UI      {url}   (from {cidr}; sign in with the admin user from SSM /{PROJECT}/NEXOPS_ADMIN_*)
  Argo Rollouts  kubectl argo rollouts --context {context} -n payments get rollout payment-api --watch
  Jaeger UI      kubectl --context {context} -n observability port-forward svc/jaeger 16686  ->  http://localhost:16686
  Release        python3 scripts/deploy_local.py v1.5.0 --target eks   (or GitHub variable DEPLOY_TARGET=eks)
  Open to all    ALLOWED_CIDR=0.0.0.0/0 python3 scripts/k8s/cluster.py eks-up   (e.g. to demo from another network)""")


def http_reaches_caddy(host: str) -> bool:
    try:
        connection = http.client.HTTPConnection(host, 80, timeout=10)
        connection.request("GET", "/")
        return connection.getresponse().status == 308  # Caddy's redirect to HTTPS
    except OSError:
        return False


def ecr_version(version: str) -> str:
    """The VERSION file may be ahead of what CI has pushed; fall back to the newest released tag in ECR."""
    images = json.loads(aws("ecr", "describe-images", "--repository-name", "payment-api", check=False) or '{"imageDetails": []}')["imageDetails"]
    pushed = {tag: item["imagePushedAt"] for item in images for tag in item.get("imageTags") or [] if tag.startswith("v")}
    if version in pushed or not pushed:
        return version
    latest = max(pushed, key=pushed.get)
    print(f"payment-api {version} is not in ECR yet; starting with {latest}")
    return latest


def eks_down(_: argparse.Namespace) -> None:
    need("kubectl", "aws")
    context = EKS_CONTEXT
    cluster = f"{PROJECT}-eks"
    exists = aws("eks", "describe-cluster", "--name", cluster, check=False)
    if exists:
        run(["aws", "eks", "update-kubeconfig", "--region", region(), "--name", cluster, "--alias", context], check=False)
        log("Deleting the load balancer Service/Ingress (the controller removes the NLB), then the volumes (EBS)")
        kubectl(context, "-n", "nexops", "delete", "service", "nexops-edge", "--wait=true", "--timeout=300s", check=False)
        kubectl(context, "delete", "ingress", "--all", "--all-namespaces", "--wait=true", "--timeout=300s", check=False)
        deadline = time.time() + 300
        while time.time() < deadline:
            balancers = json.loads(aws("elbv2", "describe-load-balancers", check=False) or '{"LoadBalancers": []}')["LoadBalancers"]
            arns = [item["LoadBalancerArn"] for item in balancers if item["LoadBalancerName"].startswith("k8s-nexops")]
            if not arns:
                break
            time.sleep(10)
        for namespace in ("nexops", "payments"):
            kubectl(context, "-n", namespace, "delete", "statefulset", "--all", "--wait=true", check=False)
            kubectl(context, "-n", namespace, "delete", "pvc", "--all", "--wait=true", "--timeout=300s", check=False)
        deadline = time.time() + 300
        while time.time() < deadline and kubectl(context, "get", "pv", "-o", "name", check=False, quiet=True):
            time.sleep(10)
    log("terraform destroy (EKS, nodes, VPC, IAM roles)")
    terraform_init()
    terraform("destroy", "-input=false", "-auto-approve", "-var", f"region={region()}")
    run(["kubectl", "config", "delete-context", context], check=False)
    print("EKS stack removed. The always-on EC2 stack (infra/terraform/aws) is untouched.")


def status(_: argparse.Namespace) -> None:
    if shutil.which("kind") and KIND_CLUSTER in run(["kind", "get", "clusters"], capture=True, check=False).split():
        print("kind cluster 'nexops': running")
        kubectl(KIND_CONTEXT, "-n", "payments", "get", "rollout,pods", check=False)
    else:
        print("kind cluster 'nexops': not running")
    if shutil.which("aws"):
        exists = aws("eks", "describe-cluster", "--name", f"{PROJECT}-eks", check=False)
        print(f"EKS cluster {PROJECT}-eks: {json.loads(exists)['cluster']['status'] if exists else 'not created (no charges)'}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    up = commands.add_parser("kind-up", help="create/update the local kind cluster")
    up.add_argument("--version", help="payment-api version to run (default: what is running, else examples/payment-api/VERSION)")
    up.add_argument("--skip-build", action="store_true", help="reuse local images that already exist")
    up.set_defaults(handler=kind_up)
    commands.add_parser("kind-stop", help="stop the kind cluster, keeping it").set_defaults(handler=kind_stop)
    commands.add_parser("kind-down", help="delete the kind cluster").set_defaults(handler=kind_down)
    eks = commands.add_parser("eks-up", help="create/update the EKS cluster (billed hourly)")
    eks.add_argument("--version", help="payment-api image tag in ECR (default: what is running, else VERSION)")
    eks.add_argument("--nexops-tag", default="latest", help="nexops-api/nexops-web image tag in ECR")
    eks.set_defaults(handler=eks_up)
    commands.add_parser("eks-down", help="destroy the EKS cluster").set_defaults(handler=eks_down)
    commands.add_parser("status", help="show what is running").set_defaults(handler=status)
    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
