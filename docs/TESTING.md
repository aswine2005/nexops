# Testing NexOps: break a release on purpose and watch NexOps find and fix it

## 1. The concept

**The problem.** A release can pass every CI/CD test and still break production: a wrong config value, a missing
database migration, a slow dependency, a feature switched on too early. The pipeline is green, customers get errors,
and someone has to notice, find the cause, decide what to do, fix it, and confirm it is really fixed.

**What NexOps does about it.** It sits next to your CI/CD and your production telemetry and runs that whole loop:

| Stage | What NexOps does | Where you see it |
|---|---|---|
| Deploy | CI reports each release (version, commit, changed files, pipeline). NexOps scores the risk and can block deploys (deploy gate) | Deployments |
| Monitor | every 15 s (local) / 30 s (EC2) it collects metrics (Prometheus), logs (Loki/Kubernetes/CloudWatch), traces (Jaeger), container or pod state, health checks | Dashboard, Metrics, Logs |
| Detect | compares with the pre-release baseline; two bad samples in a row open an incident, linked to the release or flag change that caused it | Incidents, Slack |
| Investigate | AI agents collect evidence (diff, metrics, error logs, failing traces, flag changes, similar past incidents) and Groq names the root cause with a confidence | Incident → Root cause analysis |
| Remediate | proposes a fix (rollback, restart, scale up, pause deploys, disable a flag); policies decide automatic vs your approval | Incident → Remediation, Policies |
| Verify | the incident is resolved only after several healthy samples (error rate, latency, replicas, running version…) | Incident → Recovery verification |
| Learn | blameless postmortem, incident memory for next time, DORA metrics | Incident → Postmortem, DORA |

On Kubernetes it goes one step earlier: Argo Rollouts sends a release to 25% of pods first and asks NexOps whether
those pods are healthy; a bad release is stopped before most users see it.

**The website under test.** `payment-api` is a small payment service (Python/FastAPI) with its own PostgreSQL. A
traffic generator plays customers (~8 requests per second: create payments, look them up). It exposes the metrics
NexOps expects, writes JSON logs, sends traces, has `/health` and `/ready`, and reads the `fraud-check` feature flag
from NexOps. It is the stand-in for *your* website; section 4 shows how to connect a real one the same way.

**The bugs you can inject** (all are real failures, nothing is simulated inside NexOps):

| Bug | How | What breaks |
|---|---|---|
| Wrong database port | `--break-db-config`: `config/database.json` port 5432 → 6543 | every payment fails (connection refused), readiness fails, the container restarts |
| Missing migration | `--break-schema`: writes go to table `payments_v2`, which no migration creates | payments fail, but `/ready` still passes, so only real traffic shows it |
| Feature switched on too early | turn on the `fraud-check` flag | every payment waits 1.5 s for a fraud service that never answers, then fails |

---

## 2. Before you start

| Check | How |
|---|---|
| NexOps runs | local: `docker compose -f deploy/docker-compose.yml --env-file deploy/.env ps` ([RUN_LOCAL.md](RUN_LOCAL.md)); online: `python3 scripts/aws/hosted.py status` ([RUN_ONLINE.md](RUN_ONLINE.md)) |
| payment-api is registered and **Healthy** | NexOps → Applications → Payment API |
| Groq is on (better root causes) | sidebar card says *LLM ENABLED* (otherwise the rules engine still works) |
| Slack works | Settings → **Send test alert** → a message arrives |
| You know the current version | Applications → Payment API → *Current version* (call it `vX`; the commands below use v1.5.x, any new number works) |

All commands are run from the project folder: `cd nexops`.

---

## 3. Test scenarios

### Test 1: a healthy release (5 minutes + 10 minutes watching)

```bash
python3 scripts/deploy_local.py v1.5.0 --message "Add receipt numbers"
```

| When | What you should see |
|---|---|
| immediately | the script prints the pipeline stages and `NexOps recorded DEP-n: verifying · risk low/medium` |
| Deployments | DEP-n *Verifying*, with risk score, changed files (click to see diffs) |
| after 10 min | *Verified healthy* |
| AI Agents | a new Deployment Risk Agent run |

### Test 2: a broken release → incident → AI root cause → you approve the rollback (about 5 minutes)

```bash
python3 scripts/deploy_local.py v1.5.1 --break-db-config
```

The script's CI passes: the change only edits a config value. Now watch NexOps:

| Time | What happens | Where |
|---|---|---|
| 0:00 | release DEP-n recorded, *Verifying* | Deployments |
| ~0:15 | payments fail with "connection refused"; error rate jumps, business success drops | Dashboard tiles turn red, Logs (filter ERROR) |
| ~0:30–1:00 | **incident opens**: "Payment API degraded after v1.5.1", Slack 🚨 alert | Incidents, bell, Slack |
| ~1:00 | investigation done: evidence = the `config/database.json` diff (5432 → 6543), "Connection refused" logs, failing `db.connect` traces; root cause with confidence ~90% | Incident → Root cause analysis |
| ~1:00 | remediation **Rollback to v1.5.0**, *Awaiting approval* (production policy), Slack 🛂 "approval required" | Incident → Remediation |
| you | press **Approve** | |
| +0:10 | NexOps recreates the container with the v1.5.0 image; status *Verifying recovery* | Incident, Applications → Replicas |
| +1–2 min | all recovery checks pass → **Resolved**, Slack ✅, postmortem written | Incident → Recovery verification, Postmortem |
| after | DEP-n *Rolled back*; a rollback deployment appears; DORA change failure rate and recovery time update | Deployments, DORA |

Then ship the fix properly (a "hotfix:" message is recorded as a hotfix):

```bash
python3 scripts/deploy_local.py v1.5.2 --message "hotfix: restore database port"
```

To try **Reject** instead: reject the proposal, then propose another action yourself under *Remediation*
(e.g. Restart). A restart cannot fix a wrong config, so the recovery verification keeps showing which checks fail;
approve a rollback afterwards to finish.

### Test 3: a feature flag breaks production → NexOps switches it off by itself (about 3 minutes)

1. NexOps → **Feature Flags** → `fraud-check` → click **Off** to turn it **On** (reason: "Launch fraud scoring").
2. Watch:

| Time | What happens |
|---|---|
| ~0:05 | payment-api picks up the flag; payments now call a fraud service that never answers (1.5 s timeout) and fail |
| ~0:30–1:00 | incident "Payment API degraded after **enabling fraud-check**"; the release is *not* blamed |
| ~1:00 | root cause: the flag; evidence includes a FLAG item and traces failing in `fraud.score` |
| ~1:00 | remediation **Disable feature fraud-check** runs **automatically** (policy), Slack 🚩 |
| ~2–3 min | recovery verified → resolved; the flag history shows "off by NexOps remediation" |

Works the same online (EC2 and EKS) from the website; no commands needed.

### Test 4: canary releases on Kubernetes (kind) (about 3 minutes each)

Needs the kind cluster ([RUN_LOCAL.md](RUN_LOCAL.md) part B). In a second terminal watch the Rollout:
`kubectl argo rollouts --context kind-nexops -n payments get rollout payment-api --watch`

```bash
python3 scripts/deploy_local.py v1.6.0 --target kind
```

Expected: 25% → NexOps check (3 measurements, *pass*) → 50% → check → 100%. NexOps (http://localhost:8090) → the
deployment shows a **Canary analysis** table of passes.

```bash
python3 scripts/deploy_local.py v1.6.1 --target kind --break-schema
```

Expected: at 25% NexOps measures the canary pods (error rate ~70–85% vs 0% on stable) → verdict *fail* → Argo
**aborts**, the canary pods disappear, stable keeps serving. NexOps marks the release **Canary aborted**, sends 🐤 to
Slack, and does **not** open an incident (only 25% of traffic was hit for ~30 seconds).

### Test 5: the online version through GitHub (EC2, about 15 minutes)

This is the real-world path: a bad commit, a green pipeline, a broken production. You only need the GitHub website.

1. Make sure it is up: `python3 scripts/aws/hosted.py up` (if it is down; prints the address).
2. On github.com open your repository and press the **`.`** key: the repository opens in the web editor (github.dev).
   Change both files there, so they go out as **one** commit (one release, and NexOps sees both changes in its diff):
   - `examples/payment-api/config/database.json`: `"port": 5432` → `"port": 6543`
   - `examples/payment-api/VERSION`: the next version, e.g. `1.5.0`
3. *Source Control* (left bar) → message "Tune database connection settings" → **Commit & Push** to `main`.
   (Do not make two separate commits: the first would redeploy the current version number with the bug, and the
   rollback target would then contain the bug too.)
4. **Actions → Deploy payment-api** runs: tests ✅, scan ✅, build ✅, deploy ✅. The pipeline is green.
5. In the hosted NexOps (the address from `hosted.py login`) within 1–2 minutes: incident, Slack alert, root cause citing the config diff, **Rollback** awaiting approval.
6. Press **Approve**. NexOps starts the *Deploy payment-api* workflow with `rollback=true` and the previous version (watch it in GitHub Actions), then verifies recovery and resolves the incident.
7. Fix the code the way a team would: in github.dev set `database.json` back to `5432` and bump `VERSION` again (e.g. `1.5.1`), one commit with the message `hotfix: restore database port`. The pipeline deploys it; NexOps records a hotfix and verifies it.

Without step 7 the next release would bring the bug back, because `main` still contains it.

### Test 6: other features worth a quick check

| Feature | How | Expected |
|---|---|---|
| Deploy freeze | Applications → Payment API → **Pause deployments**, then `python3 scripts/deploy_local.py v1.7.0` | the script stops: "NexOps deploy gate is closed". **Resume deployments** to undo |
| Slack | Settings → Send test alert | 🔔 message |
| Search | ⌘K → type `INC` | jumps to incidents |
| Policies | Policies → production rollback → set **Automatic**, repeat Test 2 | the rollback runs without approval (set it back afterwards) |
| Users and roles | Settings → Users → add an *operator* and a *viewer*, sign in as each | the viewer cannot approve |
| Logs ↔ traces | Logs → filter ERROR → click a trace ID → *Open trace in Jaeger* | the failing span with its error |

---

## 4. Connect your own website

Your website or API needs to give NexOps the same five things payment-api gives it. Only health checks and deployment
reports are required; every extra source makes detection and the AI root cause better.

| NexOps needs | Why | Your app provides |
|---|---|---|
| Health URL(s) | up/down every cycle | an HTTP endpoint returning 2xx when healthy (`/health`, `/ready`) |
| Deployment reports | links problems to releases, DORA | two calls from your CI per deploy (step 4) |
| Metrics | error rate, latency, traffic, business success | a Prometheus `/metrics` endpoint |
| Logs | error evidence | logs to stdout, ideally JSON with a `level` field |
| Traces (optional) | where a request failed | OpenTelemetry → Jaeger |

### Step 1: instrument the app

- **Metrics**, with any Prometheus client library. NexOps understands these names automatically:
  `http_requests_total{status="200"}` (counter per request), `http_request_duration_seconds` (histogram) and,
  optionally, `business_transactions_total{result="success|failure"}` for your key action (sign-up, checkout).
  Other names work too: put your own PromQL in the application's *Metric queries* using `$selector` and `$window`.
- **Logs**: one line per event on stdout; `"level": "ERROR"` lines count as errors.
- **Traces** (optional): OpenTelemetry with `OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4318` and a service name
  (Python example: `examples/payment-api/app/tracing.py`).
- **Feature flags** (optional): poll `GET <nexops>/api/flags/evaluate?application=<your-app-id>` with
  `Authorization: Bearer <NEXOPS_FLAGS_TOKEN>` every few seconds and keep the last answer if NexOps is unreachable
  (60-line Python client: `examples/payment-api/app/flags.py`).

### Step 2: run it where NexOps can reach it

| Your app runs… | Do this |
|---|---|
| locally in Docker | join the NexOps network: in your compose file `networks: { default: { name: nexops_default, external: true } }`; add the label `nexops.application: <id>`; copy the `payment-api` job in `deploy/prometheus/prometheus.yml` for your app and reload Prometheus with `curl -X POST localhost:9090/-/reload`. Logs are collected automatically |
| on the EC2 server | add a service to `deploy/aws/apps.yml` (copy payment-api), add its ECR repository to `repositories` in `infra/terraform/aws/main.tf` and run `python3 scripts/aws/hosted.py apply`, add the Prometheus job, copy `ci/github-actions/deploy-payment-api.yml` for your app. Pushing deploys it; *Deploy NexOps* reloads Prometheus |
| on Kubernetes | copy `deploy/k8s/payments` (Rollout, Service, AnalysisTemplate with your app ID, RoleBinding `nexops-remediator`); give the pods `prometheus.io/scrape: "true"`, `prometheus.io/port`, `prometheus.io/path` annotations |
| elsewhere (Vercel, Render, a VPS) | register it with runtime **external**: NexOps probes its health URLs and records deployments; for metrics, NexOps' Prometheus must be able to scrape it; rollback through a GitHub Actions workflow |

### Step 3: register it

NexOps → **Applications → New application** (fields explained in [USER_MANUAL.md](USER_MANUAL.md#applications)):
ID (e.g. `shop-web`), repository, runtime and container/namespace, Prometheus selector (`job="shop-web"`), Loki
selector (`{container="shop-web"}`), tracing service, health check URLs, image repository, rollback strategy. Save,
open the application, press **Test telemetry**: every source should answer.

### Step 4: report deployments from your CI

Copy `scripts/nexops_notify.py` into your repository (no dependencies). In GitHub Actions:

```yaml
env:
  NEXOPS_URL: ${{ vars.NEXOPS_URL }}                       # e.g. https://<ip>.sslip.io (hosted.py up keeps it current)
  NEXOPS_WEBHOOK_SECRET: ${{ secrets.NEXOPS_WEBHOOK_SECRET }}
steps:
  - run: python3 scripts/nexops_notify.py gate --application shop-web --kind release
  - run: python3 scripts/nexops_notify.py deployment --event started --application shop-web --version v2.3.0
  # ... build, test, deploy ...
  - if: always()
    run: python3 scripts/nexops_notify.py deployment --event finished --application shop-web --version v2.3.0 --ci-status "${{ job.status }}" --image <registry>/shop-web:v2.3.0
```

The secret is `NEXOPS_WEBHOOK_SECRET` from `deploy/.env` (local) or
`aws ssm get-parameter --profile nexops --region ap-south-1 --name /nexops/NEXOPS_WEBHOOK_SECRET --with-decryption --query Parameter.Value --output text` (online).
Any other CI works too: POST the JSON to `/api/webhooks/deployments` with header `X-NexOps-Signature: sha256=<HMAC-SHA256 of the body>` (Settings → *CI/CD deployment webhook* shows a snippet).

### Step 5: choose how NexOps may fix it

| Rollback strategy | NexOps does | Needs |
|---|---|---|
| Docker | recreates your container with the previous image tag | container name, image repository |
| Kubernetes | sets the previous image on the Deployment or Rollout | namespace, deployment, RoleBinding |
| GitHub Actions | runs your workflow with inputs `version`, `environment`, `reason`, `rollback=true` | repository, workflow file, `GITHUB_TOKEN` with Actions: write |

Then decide in **Policies** what may run automatically.

### Step 6: test it with your own bug

Release a version of your app with a deliberate bug (an endpoint that returns 500, a wrong environment variable, a
slow query) through your normal pipeline. You should see Test 2 again with your app: incident within a minute or two,
root cause pointing at your diff, the rollback proposal, verified recovery. Then release the fix.

---

## 5. If something does not happen

| Symptom | Check |
|---|---|
| No incident after a bad release | is there traffic? (Metrics → Traffic). Without requests there is no error rate. Also check the thresholds on the application |
| Root cause says "rules" instead of Groq | `GROQ_API_KEY` missing or Groq unreachable: Settings → Integrations |
| No **Approve** button | your role is viewer; ask an admin (Settings → Users) |
| Rollback failed | the previous image must still exist (local Docker image / ECR tag); see the remediation error text |
| Recovery not verified | the fix did not help: the verification table shows which check still fails |
| No Slack messages | Settings → Integrations → notifications, and **Send test alert** |
| Online: nothing happens after a push | the server must be running (`python3 scripts/aws/hosted.py status`); GitHub → Actions shows the run |
