# NexOps environment reference

The API (`uvicorn app.main:app`) and the monitor worker (`python -m app.worker`) read the same variables.
Every integration is **off until its variables are set**; NexOps never fabricates data for a missing source.

Where to put them:

| How you run NexOps | File / store |
|---|---|
| Local docker compose | `deploy/.env` (copy `deploy/.env.example`) |
| API directly on your machine | `backend/.env` (copy `backend/.env.example`) |
| AWS EC2 | SSM Parameter Store `/<project>/<NAME>` (Terraform creates the secrets; `render-env.sh` builds `.env`). Optional extras: `NEXOPS_DOMAIN` (your own HTTPS hostname), `NEXOPS_NOTIFY_WEBHOOK_URL`. |
| Kubernetes / EKS | ConfigMap `nexops-config` + Secret `nexops-secrets` (`deploy/k8s/nexops/config.yaml`) |

## Required

| Variable | Example | Purpose |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://nexops:pw@host:5432/nexops` | PostgreSQL, the system of record. Run `alembic upgrade head` before starting. |
| `NEXOPS_SECRET_KEY` | 48+ random characters | Signs login sessions (JWT). Required when `NEXOPS_ENV=production`. |
| `NEXOPS_WEBHOOK_SECRET` | 32+ random characters | HMAC key for CI/CD webhooks. The same value is the GitHub secret `NEXOPS_WEBHOOK_SECRET`. |
| `NEXOPS_ADMIN_EMAIL` / `NEXOPS_ADMIN_PASSWORD` | `you@example.com` | First administrator, created on first start only. Alternatively: `python -m app.cli create-user`. |

Generate secrets with `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`.

## Core behaviour

| Variable | Default | Purpose |
|---|---|---|
| `NEXOPS_ENV` | `development` | `production` enforces a configured secret key. |
| `NEXOPS_PUBLIC_URL` | `http://localhost:5173` | Used in links returned to CI (deployment URLs). |
| `NEXOPS_CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Allowed browser origins when the UI is on another host. |
| `NEXOPS_ADMIN_NAME` | `NexOps Admin` | Display name of the bootstrap admin. |
| `NEXOPS_TOKEN_TTL_HOURS` | `12` | Session length. |
| `NEXOPS_RUN_WORKER_IN_API` | `true` | Run the monitor inside the API process. Set `false` when running `python -m app.worker` separately. |
| `NEXOPS_MONITOR_INTERVAL_SECONDS` | `30` | Telemetry collection + detection cycle. |
| `NEXOPS_DETECTION_CONSECUTIVE_BREACHES` | `2` | Breaching samples in a row before an incident opens. |
| `NEXOPS_CORRELATION_WINDOW_MINUTES` | `60` | A release that started within this window before the breach is correlated. |
| `NEXOPS_ROLLOUT_GRACE_SECONDS` | `45` | After a deployment, replica/health-check churn alone does not open an incident. |
| `NEXOPS_DEPLOYMENT_VERIFICATION_MINUTES` | `10` | A release stays "verifying" this long; healthy telemetry marks it "verified healthy". |
| `NEXOPS_RECOVERY_HEALTHY_SAMPLES` | `3` | Consecutive healthy samples required to resolve an incident. |
| `NEXOPS_RECOVERY_TIMEOUT_MINUTES` | `15` | Verification fails (incident back to investigating) after this. |
| `NEXOPS_RECOVERY_GRACE_SECONDS` | `max(30, interval)` | Samples taken this soon after a remediation are ignored. |
| `NEXOPS_AUTO_INVESTIGATE` | `true` | Run the investigation agents as soon as an incident opens. |
| `NEXOPS_REMEDIATION_ENABLED` | `true` | Global kill switch for executing approved actions. |
| `NEXOPS_METRIC_RETENTION_DAYS` | `30` | Stored telemetry samples older than this are pruned. |

## Telemetry providers

| Variable | Default | Purpose |
|---|---|---|
| `PROMETHEUS_URL` | — | Prometheus HTTP API, e.g. `http://prometheus:9090`. Golden signals via PromQL. |
| `PROMETHEUS_BEARER_TOKEN` | — | For managed Prometheus (Grafana Cloud, AMP behind a proxy). |
| `PROMETHEUS_RATE_WINDOW` | `2m` | `rate()` window in queries; `1m` reacts faster on 5s scrapes. |
| `LOKI_URL` | — | Loki HTTP API, e.g. `http://loki:3100`. |
| `LOKI_TENANT_ID` / `LOKI_BEARER_TOKEN` | — | Multi-tenant / hosted Loki. |
| `DOCKER_ENABLED` | `false` | Read container health/logs and execute Docker rollbacks via the Docker socket. |
| `DOCKER_HOST` | local socket | Remote engine, e.g. `tcp://10.0.0.5:2376`. |
| `KUBERNETES_ENABLED` | `false` | Pod health, events, logs, and Kubernetes rollbacks. |
| `KUBERNETES_IN_CLUSTER` | `false` | `true` when NexOps runs inside the cluster (uses its ServiceAccount). |
| `KUBECONFIG` / `KUBERNETES_CONTEXT` | — | Out-of-cluster access. |
| `AWS_INTEGRATION_ENABLED` | `false` | CloudWatch metrics/logs, EC2 and ECR inventory (standard AWS credential chain / instance role / IRSA). |
| `AWS_REGION` | `us-east-1` | Region for AWS calls. |
| `JAEGER_URL` | — | Jaeger query API, e.g. `http://jaeger:16686`. Failing and slow traces become investigation evidence. |
| `JAEGER_UI_URL` | `JAEGER_URL` | Browser address for "Open trace" links (on AWS: `http://localhost:16686` through the SSM tunnel). |

## Notifications

| Variable | Default | Purpose |
|---|---|---|
| `NEXOPS_NOTIFY_WEBHOOK_URL` | — | Slack or Discord incoming-webhook URL. Messages are sent only after the change is committed. |
| `NEXOPS_NOTIFY_EVENTS` | all | Comma list of `incident.opened`, `approval.required`, `flag.disabled`, `canary.failed`, `remediation.failed`, `recovery.failed`, `incident.resolved`. |

On AWS put it in SSM: `aws ssm put-parameter --name /nexops/NEXOPS_NOTIFY_WEBHOOK_URL --type SecureString --value <url>` and re-run **Deploy NexOps**.

## Feature flags and progressive delivery

| Variable | Default | Purpose |
|---|---|---|
| `NEXOPS_FLAGS_TOKEN` | — | Bearer token applications send to `/api/flags/evaluate`. Unset = open read access (fine locally). On AWS Terraform generates it into SSM. |
| `NEXOPS_ANALYSIS_TOKEN` | — | Bearer token Argo Rollouts sends to `/api/analysis/canary` (the `nexops-tokens` Secret in the `payments` namespace). `cluster.py` generates it. |
| `PROMETHEUS_RATE_WINDOW` | `2m` | Rate window for metric queries (`1m` on Kubernetes, so canaries are judged quickly). |

Applications: `NEXOPS_FLAGS_URL` (e.g. `http://nexops-api:8000/api/flags/evaluate?application=payment-api`), `NEXOPS_FLAGS_TOKEN`, optional `NEXOPS_FLAGS_POLL_SECONDS` (5).

## AI and source control

| Variable | Default | Purpose |
|---|---|---|
| `GROQ_API_KEY` | — | Free key from https://console.groq.com/keys. Enables LLM root-cause analysis and postmortems. Without it NexOps uses deterministic evidence rules. |
| `GROQ_MODEL` | `openai/gpt-oss-120b` | Any Groq chat model. `openai/gpt-oss-120b`/`20b` use strict JSON-schema output; others use JSON mode. |
| `GROQ_BASE_URL` | `https://api.groq.com/openai/v1` | OpenAI-compatible endpoint. |
| `GROQ_TIMEOUT_SECONDS` | `60` | LLM request timeout. |
| `GITHUB_TOKEN` | — | Fine-grained PAT: *Contents: read* (commit diffs) and *Actions: read & write* (dispatch rollback workflows). |
| `GITHUB_API_URL` | `https://api.github.com` | GitHub Enterprise Server API URL. |

## GitHub repository settings (for the workflows)

| Kind | Name | Value |
|---|---|---|
| Variable | `AWS_REGION` | e.g. `us-east-1` |
| Variable | `AWS_ROLE_ARN` | `terraform output github_role_arn` |
| Variable | `DEPLOY_BUCKET` | `terraform output bundle_bucket` |
| Variable | `EC2_INSTANCE_ID` | `terraform output instance_id` |
| Variable | `NEXOPS_URL` | `terraform output nexops_url` (HTTPS) |
| Variable | `DEPLOY_TARGET` | `ec2` (default) or `eks` |
| Variable | `EKS_CLUSTER_NAME` | EKS only: `terraform -chdir=infra/terraform/eks output cluster_name` |
| Variable | `DEMO_TRAFFIC` | `true` to also deploy the traffic generator |
| Variable | `NEXOPS_PROJECT` | Optional, defaults to `nexops` (must match Terraform `project`) |
| Secret | `NEXOPS_WEBHOOK_SECRET` | `terraform output -raw webhook_secret_command` → run it |
| Environment | `production` | Create it (Settings → Environments); add required reviewers if you want manual deploy approval. |
