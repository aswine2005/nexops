# Run NexOps locally (on your Mac)

Everything here runs on your Mac and costs nothing. There are two ways; you only need the first.

| | What runs | Address | When to use |
|---|---|---|---|
| **A. Docker Compose** (main) | NexOps + Prometheus, Loki, Grafana, Jaeger + the sample payment-api | http://localhost:8080 | everyday use and testing |
| **B. kind** (optional) | the same on a local Kubernetes cluster, payment-api as an Argo Rollouts canary | http://localhost:8090 | learning Kubernetes and canary releases |

Every command below is run from the project folder. Open Terminal and go there first:

```bash
cd nexops
```

---

## 0. What you need (once)

| Need | How |
|---|---|
| Docker Desktop | installed and **running** (whale icon in the menu bar). *Settings → Resources → Memory*: 4 GB for A, 6 GB if you also run B |
| Python 3.10+ | already on macOS (`python3 --version`) |
| For B only | `brew install kind argoproj/tap/kubectl-argo-rollouts` (kubectl comes with Docker Desktop) |

## Your local login

| | |
|---|---|
| Email | `admin@nexops.local` (the `NEXOPS_ADMIN_EMAIL` line in `deploy/.env`) |
| Password | the `NEXOPS_ADMIN_PASSWORD` line in `deploy/.env`. Show it: `grep NEXOPS_ADMIN_PASSWORD deploy/.env` |

The admin account is created the **first time** NexOps starts with an empty database. Changing the password in
`deploy/.env` later does **not** change it; reset it with
`docker exec -it nexops-nexops-api-1 python -m app.cli create-user --email admin@nexops.local --role admin`
(it asks for the new password). The kind cluster (B) uses the same email and password.

---

## A. Docker Compose

### Step 1: configuration (first time only, already done on your Mac)

```bash
cp deploy/.env.example deploy/.env
```

**What happens:** creates `deploy/.env`, the only file with your local secrets (git ignores it). Open it and fill in:

| Variable | What to put |
|---|---|
| `NEXOPS_ADMIN_EMAIL`, `NEXOPS_ADMIN_PASSWORD` | your login (password at least 10 characters) |
| `NEXOPS_SECRET_KEY` | random text, e.g. output of `python3 -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `NEXOPS_WEBHOOK_SECRET` | random text; CI signs deployment reports with it |
| `NEXOPS_FLAGS_TOKEN` | random text; apps use it to read feature flags |
| `GROQ_API_KEY` | optional, free at https://console.groq.com/keys; enables the AI root-cause analysis |
| `GITHUB_TOKEN` | optional; lets NexOps read commit diffs and run rollback workflows |
| `NEXOPS_NOTIFY_WEBHOOK_URL` | optional; Slack incoming-webhook URL for alerts |

### Step 2: start the stack

```bash
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build
```

**What happens** (3–5 minutes the first time, seconds afterwards):

1. Builds 4 images from this folder: `nexops/api:local`, `nexops/web:local`, `nexops/payment-api:v1.0.0`, `nexops/traffic-generator:local`, and downloads the rest (PostgreSQL, Prometheus, Loki, Alloy, Grafana, Jaeger).
2. Creates the Docker network `nexops_default` and 4 data volumes.
3. Starts the NexOps database, then `nexops-migrate` creates/updates the tables and exits, then the API (which creates your admin user and the default policies the first time), the worker (the monitor loop) and the web UI.
4. Starts the observability tools and the sample payment-api with its database and a traffic generator that sends ~8 fake customer requests per second.

**Resources created on your Mac:**

| Container | Role | Port |
|---|---|---|
| `nexops-nexops-web-1` | NexOps website (nginx) | http://localhost:8080 |
| `nexops-nexops-api-1` | NexOps API | http://localhost:8000 (`/docs` = API reference) |
| `nexops-nexops-worker-1` | monitor: collects telemetry every 15 s, detects, investigates, remediates, verifies | — |
| `nexops-nexops-db-1` | PostgreSQL for NexOps | localhost:5433 |
| `nexops-nexops-migrate-1` | runs database migrations, then stops (normal) | — |
| `payment-api` | the sample "website" NexOps watches | http://localhost:8081 |
| `nexops-payments-db-1` | its PostgreSQL | — |
| `nexops-traffic-generator-1` | fake customers | — |
| `nexops-prometheus-1` | metrics | http://localhost:9090 |
| `nexops-loki-1` + `nexops-alloy-1` | logs (Alloy ships every container's logs to Loki) | localhost:3100 |
| `nexops-grafana-1` | dashboards | http://localhost:3000 |
| `nexops-jaeger-1` | traces | http://localhost:16686 |

Volumes: `nexops_nexops_db`, `nexops_payments_db`, `nexops_prometheus_data`, `nexops_loki_data`. Network: `nexops_default`.

### Step 3: register the sample website (first time only)

```bash
python3 scripts/register_local_app.py
```

**What happens:** signs in with the admin from `deploy/.env`, creates the application **payment-api** in NexOps
(where its metrics, logs, traces, health checks and container are, and how to roll it back), creates the feature flag
`fraud-check` (off), runs one telemetry check and prints which sources answered.

### Step 4: first release (first time only)

```bash
python3 scripts/deploy_local.py v1.0.0
```

**What happens:** acts like a CI/CD pipeline. It asks NexOps' deploy gate for permission, builds and tests the image,
replaces the `payment-api` container, and reports *started* and *finished* to NexOps. NexOps records release DEP-1,
scores its risk, and watches production for 10 minutes before marking it **Verified healthy**.

### Step 5: open it

http://localhost:8080, sign in (see *Your local login*). What to do next: [USER_MANUAL.md](USER_MANUAL.md) and [TESTING.md](TESTING.md).

### Every day after that

| I want to… | Command | What happens |
|---|---|---|
| start | open Docker Desktop | containers come back by themselves (`restart: unless-stopped`) |
| start after I stopped it myself | `docker compose -f deploy/docker-compose.yml --env-file deploy/.env start` | starts the existing containers |
| stop, keep everything | `docker compose -f deploy/docker-compose.yml --env-file deploy/.env stop` | containers stop; data stays |
| see what runs | `docker compose -f deploy/docker-compose.yml --env-file deploy/.env ps` | lists the containers |
| apply a change in `deploy/.env` | `docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d nexops-api nexops-worker` | recreates API and worker with the new values |
| apply a change in NexOps code | `docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build nexops-api nexops-worker nexops-web` | rebuilds and restarts only NexOps |
| release payment-api | `python3 scripts/deploy_local.py v1.5.0` | a new release, reported like CI |

Do not re-run Step 2 with `--build` for everything once you have made releases: it rebuilds the payment-api image
under its first tag. Use the targeted command above.

---

## B. kind (Kubernetes, optional)

### Start (first time ~6 minutes)

```bash
python3 scripts/k8s/cluster.py kind-up
```

**What happens:**

1. Creates a one-node Kubernetes cluster in a Docker container `nexops-control-plane`, publishes ports 8090 (NexOps), 16687 (Jaeger) and 9091 (Prometheus), and adds the kubectl context `kind-nexops`.
2. Installs Argo Rollouts v1.10.0 (namespace `argo-rollouts`) and metrics-server.
3. Builds the NexOps, payment-api and traffic-generator images and loads them into the cluster.
4. Writes the Kubernetes Secrets from `deploy/.env` (nothing secret is written to disk).
5. Applies `deploy/k8s/overlays/kind`, which creates:
   - `nexops` namespace: API, worker, web, PostgreSQL (StatefulSet, 2 GB volume);
   - `observability` namespace: Prometheus, Jaeger;
   - `payments` namespace: the **payment-api Rollout** (4 pods) with the `nexops-canary` AnalysisTemplate, its PostgreSQL, the traffic generator.
6. Waits for everything, then registers payment-api in this NexOps as an Argo Rollout, with the `fraud-check` flag.

Then open http://localhost:8090 (same login as A). Watch the Rollout with
`kubectl argo rollouts --context kind-nexops -n payments get rollout payment-api --watch`.

### Every day

| I want to… | Command |
|---|---|
| pause (keeps everything) | `python3 scripts/k8s/cluster.py kind-stop` |
| resume (also after restarting Docker Desktop) | `python3 scripts/k8s/cluster.py kind-up --skip-build` |
| healthy canary release | `python3 scripts/deploy_local.py v1.5.0 --target kind` |
| broken canary release | `python3 scripts/deploy_local.py v1.5.1 --target kind --break-schema` |
| what is running | `python3 scripts/k8s/cluster.py status` |

---

## The `nexops-cloud-data` container (your saved online data)

Docker Desktop also shows a group **nexops-cloud-data** with one PostgreSQL container. It is not part of the local
NexOps: it holds the **online** NexOps data while AWS is switched off (`python3 scripts/aws/hosted.py down` fills it,
`up` copies it back; see [RUN_ONLINE.md](RUN_ONLINE.md)). Start/stop it with the Docker Desktop buttons as you like;
do not delete it or its volume `nexops_cloud_data` unless you want to lose the online data.

## Turn it off and delete it, in order

| Step | Command | What it removes | Data kept? |
|---|---|---|---|
| 1. Stop only | `docker compose -f deploy/docker-compose.yml --env-file deploy/.env stop` and `python3 scripts/k8s/cluster.py kind-stop` | nothing; just stops | yes |
| 2. Delete the kind cluster | `python3 scripts/k8s/cluster.py kind-down` | the `nexops-control-plane` container, everything inside it, the `kind-nexops` context | no |
| 3. Delete the Compose stack | `docker compose -f deploy/docker-compose.yml --env-file deploy/.env down -v` | all 13 containers, the network and the 4 volumes (local incident and deployment history) | no |
| 4. Delete the images | `docker image rm $(docker image ls --filter=reference='nexops/*' -q)` | the images built from this project | — |
| 5. Free build cache (optional) | `docker builder prune -f` | Docker's build cache (all projects) | — |

Leaving it running costs nothing but memory. Without step 3's `-v` (`down` only), the data volumes stay and a later `up -d` brings the old history back.

## What is left that only you can remove

| Item | Remove it with |
|---|---|
| Downloaded base images (postgres, prometheus, grafana/loki, grafana/alloy, grafana/grafana, jaegertracing/jaeger, kindest/node) | Docker Desktop → *Images* → delete, or `docker image prune -a` (removes **all** unused images, also other projects') |
| The old prototype container and its volume | `docker rm -f backend-postgres-1` then `docker volume rm backend_nexops_postgres_data` |
| Tools installed for kind | `brew uninstall kind kubectl-argo-rollouts` |
| Secrets file | `deploy/.env` (delete it if you give the folder to someone) |
| Local release snapshots | the `.nexops-local/` folder |
| Developer folders | `backend/.venv`, `node_modules` |
| Docker Desktop itself | drag it to the Trash if you no longer need it |
