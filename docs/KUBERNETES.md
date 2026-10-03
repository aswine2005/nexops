# NexOps on Kubernetes: Argo Rollouts canaries, kind and on-demand EKS

The Docker Compose and EC2 setups run payment-api as a single container. This guide runs the whole platform on Kubernetes,
with payment-api released through **Argo Rollouts canaries** and **NexOps as the analysis provider**:

```
 deploy_local.py / GitHub Actions
        │  patch Rollout image
        ▼
 ┌──────────── payments ────────────┐     ┌────────── nexops ──────────┐    ┌──── observability ────┐
 │ Rollout payment-api (4 replicas) │◄────│ api  worker  web  postgres │───►│ Prometheus  Jaeger    │
 │  25% ─ analysis ─ 50% ─ analysis │ ──► │ /api/analysis/canary       │    │ (pod hash labels)     │
 │ payments-db  traffic-generator   │     │ /api/flags/evaluate        │    └───────────────────────┘
 └──────────────────────────────────┘     └────────────────────────────┘
          ▲  Argo Rollouts controller (argo-rollouts namespace) runs the steps and the AnalysisRuns
```

1. A release patches the Rollout image. Argo starts **one canary pod (25%)** next to three stable pods.
2. At each `analysis` step Argo calls `GET /api/analysis/canary` on NexOps (AnalysisTemplate `nexops-canary`, bearer
   `NEXOPS_ANALYSIS_TOKEN`). NexOps queries Prometheus **per pod hash** (`rollouts_pod_template_hash`) and compares the
   canary with stable: error rate, P99 latency, business success. It answers `pass`, `fail` or `inconclusive`.
3. `fail` → Argo **aborts**: the canary is scaled away, stable never stopped serving. NexOps records each verdict on the
   release (*Deployments → Canary analysis*, *AI Agents → Canary Analysis Agent*) and sends a 🐤 Slack alert.
4. `pass` twice → promoted to 100%. Problems that appear later go through the normal incident loop, and a NexOps
   rollback on a Rollout skips the canary steps (`rollbackWindow: 3 revisions`).
5. While a canary is live, NexOps leaves the judgement to the analysis (no duplicate incident for the same failure).

NexOps reads the Rollout, its pods (stable/canary), events and pod logs through the Kubernetes API. Feature flags work
the same as on Docker (`fraud-check` → *disable feature* remediation).

| Path | What |
|---|---|
| `deploy/k8s/nexops` | NexOps API (runs migrations in an init container), worker, web, PostgreSQL, RBAC (reads Rollouts, patches them for remediation) |
| `deploy/k8s/observability` | Prometheus (pod discovery, keeps the Argo pod hash) and Jaeger v2 |
| `deploy/k8s/payments` | payment-api `Rollout`, `AnalysisTemplate nexops-canary`, payments-db, traffic generator |
| `deploy/k8s/overlays/kind` | local images, NodePorts for the UI/Jaeger/Prometheus, kind cluster config |
| `deploy/k8s/overlays/eks` | ECR images, HTTPS edge (NLB with Elastic IPs + Caddy), gp3 StorageClass, IRSA, RBAC for the GitHub deploy role |
| `infra/terraform/eks` | VPC (public subnets, no NAT), EKS, 2 nodes, EBS CSI, metrics-server, AWS Load Balancer Controller, Argo Rollouts, 2 Elastic IPs |
| `scripts/k8s/cluster.py` | `kind-up`, `kind-down`, `eks-up`, `eks-down`, `status` |

---

## 1. Local: kind (free)

**Needs:** Docker Desktop with about 6 GB of memory, and:

```bash
brew install kind kubectl argoproj/tap/kubectl-argo-rollouts
```

**Start** (creates the cluster, installs Argo Rollouts + metrics-server, builds and loads the images, writes the
Secrets from `deploy/.env`, applies `overlays/kind`, registers payment-api and the `fraud-check` flag):

```bash
python3 scripts/k8s/cluster.py kind-up
```

| | |
|---|---|
| NexOps | http://localhost:8090 (same admin login as `deploy/.env`) |
| Jaeger | http://localhost:16687 |
| Prometheus | http://localhost:9091 |
| Rollout | `kubectl argo rollouts --context kind-nexops -n payments get rollout payment-api --watch` |
| Argo dashboard | `kubectl argo rollouts --context kind-nexops dashboard -p 3101` → http://localhost:3101 |

It can run next to the Docker Compose stack (different ports). Both send Slack alerts if the webhook is set.

**Demo**

```bash
# Healthy release: 25% → NexOps pass → 50% → NexOps pass → 100%
python3 scripts/deploy_local.py v1.4.0 --target kind

# Bad release: writes go to a table no migration created; readiness still passes, so only real traffic shows it.
# NexOps fails the canary at 25%, Argo aborts, stable keeps serving. NexOps records the release as "Canary aborted".
python3 scripts/deploy_local.py v1.4.1 --target kind --break-schema

# Feature flag incident (same as Docker): turn fraud-check on in NexOps → incident → NexOps turns it off by policy.
```

**Stop:** `python3 scripts/k8s/cluster.py kind-down` (deletes the cluster and everything in it).

---

## 2. AWS: on-demand EKS (billed while it exists)

Create it for a demo, delete it afterwards. The EC2 stack (`infra/terraform/aws`) is independent and untouched.

**Creates:** VPC with 2 public subnets (no NAT gateway), EKS control plane, managed node group of 2 × `c7i-flex.large`
(free-plan eligible), add-ons (VPC CNI, CoreDNS, kube-proxy, Pod Identity, metrics-server, EBS CSI), IRSA roles (EBS
CSI, Load Balancer Controller, NexOps read-only), Helm releases (AWS Load Balancer Controller, Argo Rollouts), 2 Elastic
IPs, and from the manifests an internet-facing **Network Load Balancer** on those IPs plus three small gp3 volumes.

**HTTPS without a domain:** the NLB keeps the Elastic IPs, so the UI is `https://<elastic-ip>.sslip.io` (sslip.io
resolves any `a-b-c-d.sslip.io` name to that IP). Caddy in the cluster gets a Let's Encrypt certificate for it, the
same way the EC2 host does. An ALB cannot do this: it has no fixed IPs, and its HTTPS listener needs an ACM certificate,
which needs a domain you own. The load balancer is open while the certificate is issued (Let's Encrypt must reach it),
then `eks-up` limits it to your IP with `loadBalancerSourceRanges`.

**Cost (ap-south-1, approximate):** EKS $0.10/h + 2 nodes + NLB + public IPv4 ≈ **$0.30/hour** (about $2.40 for an
8-hour day). Nothing is charged after `eks-down`. Check *Billing → Bills* after the first run.

**One-time permission:** the `nexops` IAM user has EC2/IAM/S3/SSM/ECR/Logs access but no EKS access. Attach
[`infra/iam/nexops-eks-policy.json`](../infra/iam/nexops-eks-policy.json) (`eks:*`) in the console
(*IAM → Users → nexops → Add permissions → Create inline policy → JSON*), or from an admin shell:

```bash
aws iam put-user-policy --user-name nexops --policy-name nexops-eks --policy-document file://infra/iam/nexops-eks-policy.json
```

Also apply the aws stack once so the GitHub deploy role may describe the cluster (adds `eks:DescribeCluster`):
`cd infra/terraform/aws && terraform apply`.

**Start** (≈ 20 minutes; uses ECR images `nexops-api:latest`, `nexops-web:latest`, `payment-api:<VERSION>` that the
GitHub workflows already pushed):

```bash
AWS_PROFILE=nexops python3 scripts/k8s/cluster.py eks-up
```

It prints the HTTPS URL (`https://<elastic-ip>.sslip.io`, reachable from your current IP; run it again with
`ALLOWED_CIDR=0.0.0.0/0` to demo from another network). Secrets come from SSM `/nexops/*`.

**Release on EKS**

- From your Mac: `AWS_PROFILE=nexops python3 scripts/deploy_local.py v1.4.0 --target eks` (builds linux/amd64, pushes to ECR).
- From GitHub Actions: set repository variable `DEPLOY_TARGET=eks` (and `EKS_CLUSTER_NAME=nexops-eks`). Pushes to
  `examples/payment-api/**` then patch the Rollout, follow the canary, and report to the in-cluster NexOps through
  `kubectl port-forward` (no public webhook). **Set it back to `ec2` before `eks-down`.**

**Stop:**

```bash
AWS_PROFILE=nexops python3 scripts/k8s/cluster.py eks-down
```

It deletes the load balancer Service first (so the controller removes the NLB and its security groups), then the
volumes, then runs `terraform destroy` (which also releases the Elastic IPs). Verify nothing is left:

```bash
AWS_PROFILE=nexops aws eks list-clusters --region ap-south-1
AWS_PROFILE=nexops aws elbv2 describe-load-balancers --region ap-south-1 --query 'LoadBalancers[].LoadBalancerName'
AWS_PROFILE=nexops aws ec2 describe-volumes --region ap-south-1 --filters Name=status,Values=available --query 'Volumes[].VolumeId'
```

---

## Troubleshooting

| Symptom | Check |
|---|---|
| Rollout stuck at `Paused` with an inconclusive analysis | Too little traffic on the canary: `kubectl -n payments logs deploy/traffic-generator`; resume with `kubectl argo rollouts promote payment-api -n payments` or abort with `... abort` |
| Analysis `Error` / 401 | `nexops-tokens` (payments) and `NEXOPS_ANALYSIS_TOKEN` (nexops-secrets) must match: re-run `kind-up` / `eks-up` |
| NexOps shows no canary/stable split | Prometheus target labels: http://localhost:9091/targets (kind) should show `rollouts_pod_template_hash` |
| EKS: URL not reachable | `kubectl -n kube-system logs deploy/aws-load-balancer-controller`; `kubectl -n nexops logs statefulset/caddy` (certificate) |
| EKS: PostgreSQL Pending | `kubectl get sc` should show `gp3 (default)`; `kubectl -n kube-system get pods -l app=ebs-csi-controller` |
| `eks-down` stuck on the VPC | A load balancer/ENI was left behind: delete `k8s-nexops-*` load balancers and security groups in the console, run `eks-down` again |
