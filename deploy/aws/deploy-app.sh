#!/usr/bin/env bash
# Runs on the EC2 host (via SSM): deploy one monitored application version from ECR.
# Usage: deploy-app.sh <project> <aws-region> <ecr-registry> <service> <version>
set -euo pipefail
PROJECT="$1"; REGION="$2"; REGISTRY="$3"; SERVICE="$4"; VERSION="$5"
cd /opt/nexops/aws
# Re-render so new SSM parameters (e.g. NEXOPS_FLAGS_TOKEN) reach the app; keep the platform's version pin.
CURRENT=$( (grep '^NEXOPS_VERSION=' .env 2>/dev/null || echo NEXOPS_VERSION=latest) | cut -d= -f2- | tr -d "'")
./render-env.sh "$PROJECT" "$REGION" "$REGISTRY" "${CURRENT:-latest}"
VAR="$(echo "$SERVICE" | tr 'a-z-' 'A-Z_')_VERSION"
touch versions.env
grep -v "^${VAR}=" versions.env > versions.env.tmp || true
echo "${VAR}=${VERSION}" >> versions.env.tmp
mv versions.env.tmp versions.env
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REGISTRY"
COMPOSE=(docker compose -f apps.yml --env-file .env --env-file versions.env --profile demo-traffic)
# Dependencies first (no-op when already running), then only the released service.
"${COMPOSE[@]}" up -d --wait payments-db
"${COMPOSE[@]}" pull --quiet "$SERVICE"
"${COMPOSE[@]}" up -d --no-deps "$SERVICE"
echo "${SERVICE} ${VERSION} deployed"
