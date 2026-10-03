#!/usr/bin/env bash
# Runs on the EC2 host (via SSM): sync the bundle, render secrets, pull and start the NexOps platform.
# Usage: deploy-platform.sh <project> <aws-region> <ecr-registry> <bundle-bucket> <nexops-version>
set -euo pipefail
PROJECT="$1"; REGION="$2"; REGISTRY="$3"; BUCKET="$4"; VERSION="$5"
mkdir -p /opt/nexops
aws s3 sync --region "$REGION" --delete --exclude "aws/.env" --exclude "aws/versions.env" "s3://${BUCKET}/bundle/" /opt/nexops/
cd /opt/nexops/aws
chmod +x ./*.sh
./render-env.sh "$PROJECT" "$REGION" "$REGISTRY" "$VERSION"
touch versions.env
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REGISTRY"
COMPOSE=(docker compose -f platform.yml --env-file .env)
if grep -q '^COMPOSE_PROFILES=local-db' .env; then
  COMPOSE+=(--profile local-db)
fi
"${COMPOSE[@]}" pull --quiet
# The bundled database must be healthy before the migration container runs.
if grep -q '^COMPOSE_PROFILES=local-db' .env; then
  "${COMPOSE[@]}" up -d --wait nexops-db
fi
"${COMPOSE[@]}" up -d --remove-orphans
# A changed prometheus.yml (e.g. a scrape job for a new application) does not recreate the container; reload it.
"${COMPOSE[@]}" exec -T prometheus wget -qO- --post-data='' http://127.0.0.1:9090/-/reload || true

# No database backups in the cloud: hosted.py down/up copy the data to and from PostgreSQL on your Mac.
# Remove the nightly S3 backup timer that earlier versions installed.
if [ -f /etc/systemd/system/nexops-backup.timer ]; then
  systemctl disable --now nexops-backup.timer >/dev/null 2>&1 || true
  rm -f /etc/systemd/system/nexops-backup.timer /etc/systemd/system/nexops-backup.service
  systemctl daemon-reload
fi

healthy=false
for _ in $(seq 1 30); do
  if "${COMPOSE[@]}" exec -T nexops-web wget -qO- http://127.0.0.1:8080/health >/dev/null 2>&1; then healthy=true; break; fi
  sleep 4
done
if [ "$healthy" != true ]; then
  echo "NexOps did not become healthy" >&2
  "${COMPOSE[@]}" ps >&2
  exit 1
fi
DOMAIN=$(grep '^NEXOPS_DOMAIN=' .env | cut -d= -f2- | tr -d "'")
for _ in $(seq 1 30); do
  if curl -fsS --max-time 5 "https://${DOMAIN}/health" >/dev/null 2>&1; then echo "NexOps ${VERSION} is healthy at https://${DOMAIN}"; exit 0; fi
  sleep 4
done
echo "NexOps ${VERSION} is healthy, but https://${DOMAIN} is not serving yet (certificate issuance pending?)." >&2
"${COMPOSE[@]}" logs --tail 20 caddy >&2 || true
