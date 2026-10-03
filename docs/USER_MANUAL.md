# NexOps user manual: what each page does and how to use it

You have started NexOps ([RUN_LOCAL.md](RUN_LOCAL.md) or [RUN_ONLINE.md](RUN_ONLINE.md)) and signed in. This is what
you see and what to do with it. To try every feature with a real failure, follow [TESTING.md](TESTING.md).

## The idea in one paragraph

CI/CD tells you a release *passed its tests*. NexOps tells you whether *production is actually healthy* after the
release, and helps you get it back when it is not. It records every deployment, watches real telemetry (metrics, logs,
traces, health checks, containers/pods), opens an incident when something breaks, lets AI agents collect evidence and
name the likely cause, runs or proposes a fix under your policies, verifies that production really recovered, writes
the postmortem, and turns all of that into DORA metrics.

```
Deploy → Monitor → Detect → Investigate → Remediate (policy + approval) → Verify recovery → Learn → DORA
```

---

## Screen layout

| Area | What it is |
|---|---|
| **Left sidebar** | the pages, grouped as Workspace, Observability, Delivery, Governance. The red number next to *Incidents* is how many are open. The *NexOps AI* card at the bottom says whether Groq (LLM) analysis is on or only the deterministic rules are used |
| **Environment selector** (top) | Production / Staging / Development / All environments. Filters every page to that environment |
| **Search** (top, or ⌘K) | jump to any application, incident or deployment by name or ID (e.g. `INC-4`, `DEP-12`) |
| **Bell** | open incidents; click one to go to it |
| **Your name** (top right) | your role and **Sign out** |

Roles: **Viewer** can look; **Operator** can also approve fixes, run investigations, change flags and pause
deployments; **Admin** can also manage policies, applications and users.

---

## Workspace

### Dashboard

Production at a glance for the selected environment.

| Part | Shows |
|---|---|
| Six tiles | error rate, P99 latency, request rate, CPU, memory, active incidents, each with the change versus the previous hour |
| Active incident banner | the most important open incident, with a link to it (or "No active incidents") |
| Service health | every application with status, error rate, latency and traffic |
| Error rate chart | the last hours, with deployment markers, so you see whether a release changed it |
| Latest deployment / recent deployments | the newest releases and whether they were verified |
| Replicas | containers or pods of the focused application, ready or not |
| Latest logs | the newest log lines across services |

**Use it:** open it first. Green everywhere = nothing to do. A red tile or banner → click through to the incident.

### Applications

The service catalog: every application NexOps watches. Search, filter by health, sort by name or error rate.

**Application page** (click a service):

| Part | Shows / does |
|---|---|
| Current version, live tiles | the running version and current request rate, error rate, P50/P95/P99 latency, CPU, business success |
| **Test telemetry** | asks every configured source right now and shows which answered (Prometheus, Loki, Docker/Kubernetes, health checks) and any errors. Use it after registering or changing an app |
| **Pause deployments / Resume deployments** | freezes releases: the CI deploy gate answers "no" (rollbacks are still allowed). Use it during an incident or a release freeze |
| **Edit configuration** | the form below |
| Health checks | each URL, response time and status |
| Service information | owner, repository, runtime, Prometheus selector, log source, traces, rollback strategy, data sources |
| Dependencies | e.g. its database, and whether errors mention it |
| Replicas | each container/pod: version, restarts, CPU, status. On Kubernetes an **Argo Rollout** strip shows the canary: phase, step, weight, stable and canary versions |
| Deployment history, Incidents | this service's releases and incidents |

**New application / Edit configuration** has five sections:

| Section | Fill in |
|---|---|
| Service | ID, name, description, owner, environment, GitHub repository |
| Runtime | Docker (container name), Kubernetes (namespace, deployment/Rollout), EC2 (instance ID) or external |
| Telemetry | Prometheus selector (e.g. `job="shop"`), optional custom PromQL per metric, Loki selector or CloudWatch log group, tracing service name, health check URLs |
| Remediation | image repository, rollback strategy (Docker, Kubernetes, GitHub Actions or none), GitHub workflow and branch |
| Detection thresholds | override error rate %, P99 ms, CPU %, memory %, restarts, DB errors, business success % (defaults: 5% errors, 1000 ms) |

How to connect your own app is explained step by step in [TESTING.md](TESTING.md#4-connect-your-own-website).

### Deployments

Every release reported by CI/CD (GitHub Actions, `deploy_local.py`), every rollback, every hotfix. Filter by kind
(releases, rollbacks, hotfixes) and outcome; search by version or commit.

| Status | Means |
|---|---|
| Deploying | CI said *started* |
| Verifying | CI finished; NexOps is watching production for 10 minutes |
| Verified healthy | production stayed within thresholds |
| Degraded | an incident was caused by this release |
| Rolled back | NexOps (or you) replaced it with an earlier version |
| Canary aborted | Argo Rollouts stopped it at 25/50% because NexOps' canary check failed; most users never saw it |
| CI failed | the pipeline failed before production |
| Superseded | a newer release came before verification finished |

**Deployment page:** pipeline stages and verification result; **Canary analysis** table (Kubernetes: each NexOps
verdict with canary vs stable error rate, P99 and business success); **Change summary** (files changed, click a file to
see its diff); **Risk assessment** (score 0–100 and the reasons: database/config change, size, recent failures,
weekend, CI status); the correlated incident if there is one.

### Incidents

All incidents; filter by status (active, investigating, awaiting approval, verifying recovery, resolved) and severity.

**Incident page**, from top to bottom:

| Part | What it tells you / what you do |
|---|---|
| Header | title (e.g. "Payment API degraded after v1.1.0" or "…after enabling fraud-check"), severity, status, duration |
| Customer impact / How it was detected | which signals broke (error rate, latency, business success, restarts, health checks), baseline vs observed vs peak |
| Investigation agents | the agents that ran and how long they took. **Run investigation / Re-run investigation** starts it again |
| Root cause analysis | the likely root cause, confidence %, recommended action, alternative hypotheses, and the **evidence**: DEPLOYMENT, CHANGE (diffs), METRIC, LOG, TRACE (with *Open trace in Jaeger*), FLAG, RUNTIME, HEALTH, HISTORY |
| Remediation | the proposed fix and the policy decision. **Approve** runs it, **Reject** declines it. You can also propose another action yourself: Rollback, Restart, Scale up, Pause deployments, Disable feature flag |
| Recovery verification | after a fix: each check (error rate, P99, replicas, crash loops, health, DB errors, business success, running version) must pass several samples in a row. **Verify recovery** checks now |
| Postmortem | written when it resolves (blameless): summary, impact, root cause, how it was detected, how it was resolved, recovery, preventive actions, lessons |
| Incident timeline | every event with time: release, flag change, breach, incident opened, investigation, approval, fix, verification, resolved |
| Similar historical incidents | earlier incidents with the same signature, which the AI also uses |

**Use it:** when an incident opens (Slack tells you), open it, read the root cause and evidence, and if the
remediation waits for approval, check it and press **Approve**. Then watch it go to *Verifying recovery* and
*Resolved*.

### AI Agents

The seven agents and what they did: Deployment Risk, Canary Analysis, Investigation, Root Cause (Groq LLM or rules),
Remediation, Recovery Verifier, Postmortem. Each card shows inputs, outputs, number of runs, success rate and the last
run; below is every run with its summary. Click an agent to filter the runs.

---

## Observability

### Metrics

Pick an application and a range (15 minutes to 7 days). Four golden signals (traffic, errors, P99 latency, CPU) plus
P95 latency, memory and database connection errors, as charts with deployment markers. Use it to see when a problem
started and whether a release or fix changed it.

### Logs

The log explorer. Filter by service, level (ERROR, ERROR + WARN, …) and time range; search message text or a trace ID.
Click a trace ID to see every line of that request, or open the trace in Jaeger. **Copy** copies the visible lines.
Logs come from Loki (Docker/EC2), the Kubernetes API, or CloudWatch.

### Infrastructure

What the services run on: workloads (containers/pods, versions, replicas, restarts, Argo Rollout status), Kubernetes
nodes and events (when connected), and on AWS the EC2 instances, ECR repositories and CloudWatch log groups.

---

## Delivery

### Feature Flags

Runtime switches your application reads from NexOps. Create a flag (off at first), turn it on/off, change its rollout
percentage; each change asks for a reason and is kept in the history. Turning a flag on counts as a production change:
if an incident follows, NexOps names the flag as the likely cause and, by default policy, **turns it off by itself**.

### DORA Metrics

The four DORA metrics from real records, each rated Elite/High/Medium/Low: deployment frequency, lead time for
changes (commit to production), change failure rate (releases that caused an incident or rollback), failed deployment
recovery time; plus deployment rework rate and a monthly table. Click a metric for its chart.

---

## Governance

### Policies

Rules that decide what NexOps may do on its own:

| Mode | Meaning |
|---|---|
| Automatic | runs without asking |
| Human approval required | waits for an operator's **Approve** |
| Not permitted | never runs |

Each policy has an action, an environment (and optionally one application) and conditions: minimum AI confidence,
maximum runs per hour, maximum replicas, "previous version must exist". Defaults: in production, rollbacks, restarts
and scale-ups need approval; disabling a feature flag is automatic; database changes are forbidden. Outside production
(staging and development) rollbacks and restarts are automatic.

### Settings

| Part | Use |
|---|---|
| Integrations | each source (Prometheus, Loki, Jaeger, Docker, Kubernetes, AWS, GitHub, Groq, notifications) with connected / not configured and the reason |
| Send test alert | posts a test message to your Slack channel |
| Monitoring & remediation | the active settings: monitor interval, breaches needed to open an incident, correlation window, recovery samples |
| CI/CD deployment webhook | the endpoint, signature header, deploy gate and a ready-to-copy CI snippet |
| Users | add people, change roles, deactivate (admins only) |
| Audit log | who did what: logins, approvals, flag changes, policy edits |

---

## A normal day with NexOps

1. You push a release. It appears under **Deployments** as *Verifying*, with a risk score.
2. Ten minutes later it says *Verified healthy*. Nothing else to do.
3. If production breaks, Slack alerts you and an **incident** opens within about a minute.
4. Open it: the root cause and evidence are already there. Approve the fix if a policy asks you to.
5. NexOps verifies recovery, resolves the incident, writes the postmortem, and **DORA Metrics** update.
