# NexOps setup guide, phase by phase

Each phase lists **what is built**, **what to fill in**, **what resources you need**, and **how to run it**.
Phases 1–7 run entirely on your laptop with Docker. Phases 8–9 put the same system on AWS.

Variable reference: [ENVIRONMENT.md](ENVIRONMENT.md).

---

## Phase 1 — Real-data platform core

**Completed**
- All mock data, the in-memory demo repository, the seed script and the browser-side "scenario" state machine were removed. PostgreSQL is the system of record (SQLite is used only by the test suite).
- Schema (Alembic `0001_nexops_schema`): users, applications (with telemetry/runtime/rollback configuration), deployments + file changes, telemetry samples, incidents + timeline events, investigations, agent runs, policies, remediation actions, recovery verifications, postmortems, incident memory, audit log.
- Authentication: email/password login (scrypt hashes), JWT sessions, roles `viewer` < `operator` < `admin`. Approvals are recorded against the signed-in user. Audit log for approvals, policy, user and application changes.
- Application catalog: register/edit services in the UI (**Applications → Register application**) with their Prometheus selector, Loki selector or CloudWatch log group, health-check URLs, runtime (Docker / Kubernetes / EC2) and rollback strategy.
- Frontend rebuilt on the API: every page loads live data and polls; environment selector, notifications (active incidents), ⌘K search, and the previously empty pages (AI Agents, Infrastructure, Policies, Settings) are implemented.

**Fill in** — `DATABASE_URL`, `NEXOPS_SECRET_KEY`, `NEXOPS_WEBHOOK_SECRET`, `NEXOPS_ADMIN_EMAIL`, `NEXOPS_ADMIN_PASSWORD`.

**Resources** — PostgreSQL 15+ (the compose stack provides one).

**How** — see Phase 2 (one command starts everything). To run the API alone against an existing database:

```bash
cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
cp .env.example .env   # edit values
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --reload --port 8000
```

Frontend dev server (proxies `/api` to port 8000): `npm install && npm run dev` → http://localhost:5173.

---

## Phase 2 — Local stack with real telemetry

**Completed**
- `deploy/docker-compose.yml`: NexOps API, monitor worker, web (nginx), PostgreSQL, **Prometheus**, **Loki**, **Grafana Alloy** (ships every container's logs to Loki), **Grafana**, plus a real monitored service.
- `examples/payment-api`: an instrumented FastAPI service backed by its own PostgreSQL. It exposes `http_requests_total`, `http_request_duration_seconds`, `business_transactions_total`, JSON logs, `/health` and `/ready`, and exits when its database stays unreachable (so the runtime restarts it, like a failing liveness probe).
- `examples/traffic-generator`: steady customer traffic so the metrics have real requests behind them.
- Monitor loop (`python -m app.worker`): every cycle it queries Prometheus (golden signals), the Docker Engine (replicas, restarts, CPU/memory), HTTP probes, and Loki + runtime logs (database errors); it stores one sample per service. A PostgreSQL advisory lock makes multiple workers safe.

**Fill in** — copy `deploy/.env.example` to `deploy/.env` and set `NEXOPS_SECRET_KEY`, `NEXOPS_WEBHOOK_SECRET`, `NEXOPS_ADMIN_EMAIL`, `NEXOPS_ADMIN_PASSWORD` (optional: `GROQ_API_KEY`, `GITHUB_TOKEN`).

**Resources** — Docker Desktop (≈4 GB RAM free). Ports on 127.0.0.1: 8080 UI, 8000 API, 5433 NexOps DB, 8081 Payment API, 9090 Prometheus, 3100 Loki, 3000 Grafana.

**How**

```bash
cp deploy/.env.example deploy/.env    # then fill the four required values
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build
python3 scripts/register_local_app.py # registers payment-api with its real telemetry sources
```

Open http://localhost:8080 and sign in with the admin from `deploy/.env`. **Settings → Integrations** shows a live connectivity check for every provider.

Stop: `docker compose -f deploy/docker-compose.yml --env-file deploy/.env down` (add `-v` to delete all local data).

---

## Phase 3 — Deployment tracking from CI/CD

**Completed**
- `POST /api/webhooks/deployments` (HMAC-SHA256 `X-NexOps-Signature`): CI reports `started` and `finished` events with version, commit, author, branch, CI status, pipeline stages, image and changed files. NexOps records the previous version, lead time, and keeps "CI/CD passed" separate from "production verified".
- `scripts/nexops_notify.py`: zero-dependency notifier used by GitHub Actions (computes the git diff between the previous and current commit, signs the payload).
- **Deployment Risk Agent**: scores each release 0–100 from change size, sensitive paths (database/migrations, configuration, auth, payment paths, infrastructure, dependencies), CI status and the service's recent incident history.
- Deploy gate `POST /api/webhooks/deploy-gate`: CI asks before releasing; NexOps blocks releases (not rollbacks) while deployments are paused.
- Optional GitHub enrichment (`GITHUB_TOKEN`): file-level diffs and patches pulled from the GitHub API.
- `scripts/deploy_local.py`: a local CI/CD pipeline (gate → build → test → dependency check → deploy → report) for the sample service.

**Fill in** — `NEXOPS_WEBHOOK_SECRET` (same value in CI), optional `GITHUB_TOKEN`.

**Resources** — a GitHub repository (Phase 8) or the local script.

**How** — report a healthy release locally: `python3 scripts/deploy_local.py v1.0.0`. In GitHub, `ci/github-actions/deploy-payment-api.yml` does this on every push.

---

## Phase 4 — Detection, deployment correlation, incidents

**Completed**
- Deterministic rules on every sample: error rate, P99 latency (absolute and relative to the pre-release baseline), CPU, memory, replica availability, crash loops, restarts, database errors, failing health checks, business transaction success. Rules detect symptoms; they never claim a cause.
- Baseline: median of the samples in the 30 minutes before the release started.
- An incident opens after N consecutive breaching samples and is correlated with the release that started before the first breach (it tolerates CI reporting "finished" after the new version is already serving). Replica churn during a rollout's grace period does not page.
- The incident stores signals, baseline, observed and peak values, and a timeline (deployment started/completed, each signal, incident created). The release is marked **Degraded** even though CI passed.
- Per-service threshold overrides in the application form.

**Fill in** — optional tuning: `NEXOPS_MONITOR_INTERVAL_SECONDS`, `NEXOPS_DETECTION_CONSECUTIVE_BREACHES`, `NEXOPS_CORRELATION_WINDOW_MINUTES`, `NEXOPS_ROLLOUT_GRACE_SECONDS`.

**How — try a real bad release locally**

```bash
python3 scripts/deploy_local.py v1.0.0            # healthy baseline; wait ~2 minutes
python3 scripts/deploy_local.py v1.1.0 --break-db-config
```

`--break-db-config` ships `config/database.json` with the wrong port. CI passes; within ~30 seconds the service returns 503s, latency climbs, readiness fails, and "Connection refused" floods the logs. NexOps opens an incident correlated with v1.1.0.

---

## Phase 5 — AI investigation (Groq) and incident memory

**Completed**
- **Investigation Agent** collects an evidence package with stable IDs: the release and its riskiest file changes (with patches), each signal vs baseline, the recent sample series, grouped error/warning log signatures with counts, unhealthy replicas and Kubernetes warning events, failing health checks, and similar past incidents.
- **Root Cause Agent**: Groq (`openai/gpt-oss-120b` with strict JSON-schema output by default) returns the likely root cause, confidence, reasoning, alternative hypotheses, next steps and a recommended action, citing only supplied evidence IDs. The output is validated; unknown citations are dropped and unsupported confidence is capped. Without a key, or if Groq fails, deterministic evidence rules produce the same structure (marked `local-rules`).
- **Incident memory**: every resolved incident is stored with its signals, root cause and action; similar incidents become evidence for future investigations.
- Investigations run automatically on new incidents (`NEXOPS_AUTO_INVESTIGATE`) and on demand from the incident page.

**Fill in** — `GROQ_API_KEY` (free: https://console.groq.com/keys), optional `GROQ_MODEL`.

**Resources** — Groq account (free tier). No data leaves your network unless this key is set.

**How** — add the key to `deploy/.env`, then `docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d nexops-api nexops-worker`. The sidebar card switches to "LLM ENABLED"; the incident page shows `Groq · <model>`.

---

## Phase 6 — Policy-gated remediation, recovery verification, postmortem

**Completed**
- **Policy engine** (Policies page, admin-editable): each action (rollback, restart, scale up, pause deployments, database modification) is `automatic`, `approval_required` or `forbidden` per environment/application, with conditions (minimum confidence, required previous version, max automatic runs per hour, max replicas). Defaults: production rollback → approval; non-production rollback/restart → automatic; production database modification → forbidden; no matching policy → approval.
- **Remediation Agent** proposes the action with a concrete target (previous version and the exact image it last ran).
- Executors: **Docker** (recreates the container with the previous image, preserving runtime env/ports/networks/labels, and restores the old container if the new one fails), **Kubernetes** (sets the previous image on the Deployment, restart, scale), **GitHub Actions** (dispatches the deploy workflow with `rollback=true`), **pause deployments** (closes the deploy gate).
- **Recovery Verifier**: a rollback is not success. NexOps requires N consecutive samples that pass error rate and latency (near the pre-incident baseline), replica availability, crash loops, health endpoints, database errors, business transactions and the **running version**. Only then is the incident resolved; on timeout it returns to investigating.
- **Postmortem Agent** (Groq or template): impact (peak vs baseline, estimated failed requests), detection time after deploy, time to mitigate, recovery evidence, and preventive actions.

**Fill in** — optional `NEXOPS_REMEDIATION_ENABLED`, `NEXOPS_RECOVERY_HEALTHY_SAMPLES`, `NEXOPS_RECOVERY_TIMEOUT_MINUTES`. For the GitHub strategy: `GITHUB_TOKEN` with *Actions: write*.

**How** — on the incident page, review the evidence and **Approve rollback** (operator or admin). Watch the recovery checks flip to green and the postmortem appear.

---

## Phase 7 — DORA metrics from real records

**Completed** — computed on request from deployments and incidents (nothing is stored or seeded):
- Deployment frequency: successful production releases (rollbacks excluded).
- Lead time for changes: median commit → production time.
- Change failure rate: releases that degraded production or were rolled back.
- Failed deployment recovery time: median failed release → verified recovery.
- Deployment rework rate: rollbacks, hotfixes and releases shipped during an open incident.
- 30-day summary with DORA performance bands, plus monthly history.

**Fill in** — nothing. Lead time needs commit timestamps, which `nexops_notify.py` sends automatically.

---

## Phase 8 — AWS (EC2 + ECR + CloudWatch) with Terraform and GitHub Actions

**Completed**
- `infra/terraform/aws`: VPC (2 public + 2 private subnets, no NAT), EC2 host (Amazon Linux 2023, Docker, Compose, CloudWatch agent, IMDSv2, encrypted gp3, Elastic IP, **SSM only, no SSH**), security group (port 80 from your CIDRs), ECR repositories with scan-on-push and lifecycle rules, CloudWatch log groups, S3 deploy-bundle bucket, SSM Parameter Store secrets (generated), optional RDS PostgreSQL, IAM instance role (ECR pull, logs, NexOps read access to CloudWatch/EC2/ECR), and a **GitHub OIDC role** (no AWS keys in GitHub).
- `deploy/aws`: `platform.yml` (NexOps + observability) and `apps.yml` (monitored services) run as separate compose projects on a shared network, so a platform redeploy never undoes a NexOps rollback. Container logs go to CloudWatch Logs (and to Loki through Docker dual logging).
- `ci/github-actions/ci.yml`: backend tests, migrations against PostgreSQL (upgrade/check/downgrade), frontend build, container builds, pip-audit, terraform fmt/validate, kustomize render.
- `ci/github-actions/deploy-nexops.yml`: build → push to ECR → upload bundle to S3 → SSM rollout → health check.
- `ci/github-actions/deploy-payment-api.yml`: report start → deploy gate → tests → dependency scan → build/push → deploy (EC2 via SSM or EKS via kubectl) → report every stage to NexOps. `workflow_dispatch` with `rollback=true` is the NexOps GitHub-strategy rollback target.

**Resources and approximate monthly cost (us-east-1)**

| Resource | Notes | ≈ Cost |
|---|---|---|
| EC2 `t3.small` (2 GiB + 1 GiB swap) + 30 GB gp3 + Elastic IP | NexOps, Prometheus, Loki, Grafana, sample app | ~$22 (covered by Free plan credits) |
| ECR | a few hundred MB of images | <$1 |
| CloudWatch Logs + agent metrics | 14-day retention | ~$1–5 |
| S3 + SSM Parameter Store (standard) | tiny | ~$0 |
| RDS `db.t4g.micro` (optional) | `enable_rds = true` | ~$15 |

**Fill in**
- `infra/terraform/aws/terraform.tfvars` (copy the `.example`): `github_repository`, `admin_email`, `allowed_cidrs`, `webhook_cidrs`, optional `groq_api_key`, `github_token`, `enable_rds`.
  Newer GitHub repositories issue **immutable OIDC subjects** (`repo:owner@<id>/name@<id>:…`). Check with `GET /repos/OWNER/REPO/actions/oidc/customization/sub`; if `use_immutable_subject` is true, also set `github_owner_id` and `github_repository_id`, or the deploy role cannot be assumed.
- AWS Free plan accounts (created after 15 July 2025) can only launch Free Tier eligible types: keep `instance_type = "t3.small"` (the stack uses ~700 MiB).
- GitHub variables and secret: see the table in [ENVIRONMENT.md](ENVIRONMENT.md#github-repository-settings-for-the-workflows).

**How**

```bash
# 0. Tools: Terraform >= 1.6, AWS CLI v2 with admin credentials (aws configure / SSO), a GitHub repo.
cd infra/terraform/aws
cp terraform.tfvars.example terraform.tfvars        # edit
terraform init && terraform plan && terraform apply
terraform output                                    # values for GitHub variables
$(terraform output -raw webhook_secret_command)     # value for the GitHub secret NEXOPS_WEBHOOK_SECRET
$(terraform output -raw admin_password_command)     # NexOps admin password
```

1. Push this project to GitHub (`git init && git add . && git commit -m "NexOps" && git remote add origin … && git push -u origin main`).
2. In the repository: create the `production` environment, add the variables and the `NEXOPS_WEBHOOK_SECRET` secret.
3. Run **Actions → Deploy NexOps** (or push to `main`). Open `terraform output nexops_url` and sign in.
4. Run **Actions → Deploy payment-api** (deploys `v1.0.0` from `examples/payment-api/VERSION`).
5. In NexOps register `payment-api` with:
   Prometheus selector `job="payment-api"`, Loki selector `{container="payment-api"}`, CloudWatch log group `/nexops/payment-api`,
   health checks `http://payment-api:8000/health` and `/ready`, runtime **Docker**, container `payment-api`, EC2 instance id from Terraform,
   image repository `<ecr_registry>/payment-api`, rollback strategy **GitHub Actions** (workflow `deploy-payment-api.yml`, ref `main`; needs `GITHUB_TOKEN`) or **Docker**.
6. Break it for real: change `"port": 5432` to `6543` in `examples/payment-api/config/database.json`, bump `VERSION` to `1.1.0`, push. CI passes; NexOps detects, investigates, and asks for rollback approval.

Grafana/Prometheus are not public. Tunnel: `$(terraform output -raw grafana_tunnel_command)` → http://localhost:3000.

Destroy: `terraform destroy` (RDS has deletion protection; disable it first if enabled).

---

## Phase 9 — Kubernetes, Argo Rollouts canaries, kind and on-demand EKS

**Completed**
- The whole platform runs on Kubernetes from `deploy/k8s` (bases `nexops`, `observability`, `payments`; overlays `kind` and `eks`). Prometheus and Jaeger run in the cluster; NexOps reads pod logs through the Kubernetes API.
- payment-api is an **Argo Rollout**: 25% canary → NexOps analysis → 50% → NexOps analysis → 100%. NexOps is the analysis provider (`GET /api/analysis/canary`, AnalysisTemplate `nexops-canary`): it compares canary and stable pods by `rollouts_pod_template_hash` in Prometheus and answers pass / fail / inconclusive. A failing canary is aborted by Argo; NexOps records the verdicts on the release, marks it **Canary aborted** and sends a Slack alert.
- NexOps reads Rollouts (phase, step, canary weight, stable/canary pods), and its rollback/restart/scale executors patch Rollouts (`rollbackWindow` makes rollbacks skip the canary steps). While a canary is live NexOps defers to the analysis; after an abort it measures only the stable pods.
- `scripts/k8s/cluster.py` creates and removes a local **kind** cluster or an on-demand **EKS** cluster (Terraform: public subnets without NAT, 2 × c7i-flex.large, EBS CSI, metrics-server, AWS Load Balancer Controller and Argo Rollouts through Helm, a Network Load Balancer on Elastic IPs with Caddy for HTTPS on `<ip>.sslip.io`). `deploy_local.py --target kind|eks` and the GitHub workflow (`DEPLOY_TARGET=eks`) release through the canary.

**Resources and cost** — kind: free (Docker Desktop ~6 GB). EKS: about $0.30/hour while it exists, nothing after `eks-down`. The `nexops` IAM user needs `eks:*` (`infra/iam/nexops-eks-policy.json`).

**How** — see [KUBERNETES.md](KUBERNETES.md):

```bash
python3 scripts/k8s/cluster.py kind-up                                  # local cluster, http://localhost:8090
python3 scripts/deploy_local.py v1.4.0 --target kind                     # canary promoted
python3 scripts/deploy_local.py v1.4.1 --target kind --break-schema      # canary aborted by NexOps analysis
python3 scripts/k8s/cluster.py kind-down
AWS_PROFILE=nexops python3 scripts/k8s/cluster.py eks-up                 # on-demand EKS + HTTPS load balancer (billed hourly)
AWS_PROFILE=nexops python3 scripts/k8s/cluster.py eks-down
```

---

## Phase 10 — Tracing, notifications, HTTPS, backups, remote state

**Completed**
- **Distributed tracing.** The Payment API is instrumented with OpenTelemetry (FastAPI, psycopg, and a `db.connect` span that records connection failures) and exports OTLP to **Jaeger v2**. Log lines carry the trace id. The Investigation Agent adds failing traces (grouped by where they fail) and the slowest trace as `TRACE` evidence, with "Open trace in Jaeger" links.
- **Notifications.** Incident opened, approval required, remediation failed, recovery not verified, and resolved, posted to a Slack- or Discord-compatible webhook only after the change commits.
- **HTTPS on AWS.** Caddy terminates TLS with an automatically issued and renewed certificate for `<elastic-ip>.sslip.io` (or your own domain via SSM `NEXOPS_DOMAIN`). Port 80 redirects to HTTPS.
- **Backups.** No copies in AWS: `python3 scripts/aws/hosted.py down` (or `save`) copies the online database over an SSM tunnel into the local Docker PostgreSQL `nexops-cloud-data`, and `up` restores it. (Earlier versions ran a nightly `pg_dump` to S3; deploys now remove that timer.)
- **Remote Terraform state.** State lives in a versioned, encrypted S3 bucket with native S3 locking (`infra/terraform/aws/backend.hcl`).

**Fill in** — optional `NEXOPS_NOTIFY_WEBHOOK_URL`; optional SSM `NEXOPS_DOMAIN`. Register `tracing_service` on each application (application form → Telemetry).

**How**
- Local: `docker compose … up -d` now includes Jaeger at http://localhost:16686.
- AWS: Jaeger UI through an SSM tunnel (see [RUN_ONLINE.md](RUN_ONLINE.md)); data: `hosted.py save` / `down` / `up`.
- Terraform: `terraform init -backend-config=backend.hcl` (Terraform ≥ 1.10).

---

## Phase 11 — Feature flags and the "disable feature" remediation

**Completed**
- NexOps manages runtime feature flags (*Delivery → Feature Flags*). Applications poll `GET /api/flags/evaluate?application=<id>` (Bearer `NEXOPS_FLAGS_TOKEN`); the sample service guards a fraud-scoring call with `fraud-check`.
- Turning a flag on is recorded as a production change. An incident that follows it is titled after the flag, gets `FLAG` and flagged-trace evidence, and is **not** counted against the earlier release.
- `disable_feature` is a remediation like rollback: the default policy runs it automatically (confidence ≥ 0.6, at most 5 per hour), then recovery is verified as usual. Slack gets `flag.disabled`.

**Fill in** — `NEXOPS_FLAGS_TOKEN` (local `deploy/.env`; on AWS Terraform generates it into SSM).

**How** — Feature Flags page → turn `fraud-check` on → watch the incident open, the flag switch off by itself, and the incident resolve.

---

## Security notes

- Mounting the Docker socket gives NexOps control of that host's containers. It is required for Docker rollbacks; on servers prefer the EC2 setup (Docker group via `group_add`, no root) or Kubernetes RBAC.
- Webhooks are HMAC-signed; the UI and API require login; approvals require the operator role; policies and users require admin.
- LLM prompts treat all evidence as untrusted data, and the LLM can only *recommend*. Every action passes the policy engine, and production actions need a human.
- Keep `deploy/.env`, `backend/.env` and `terraform.tfvars` out of git (already in `.gitignore`).
