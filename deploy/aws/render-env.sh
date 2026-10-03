#!/usr/bin/env bash
# Render .env for the EC2 compose projects from SSM Parameter Store (/<project>/<NAME>) and instance metadata.
# Usage: render-env.sh <project> <aws-region> <ecr-registry> <nexops-version>
set -euo pipefail
PROJECT="$1"; REGION="$2"; REGISTRY="$3"; VERSION="$4"

TOKEN=$(curl -sS -X PUT "http://169.254.169.254/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 60")
PUBLIC_IP=$(curl -sS -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/public-ipv4 || true)

{
  echo "PROJECT=${PROJECT}"
  echo "AWS_REGION=${REGION}"
  echo "ECR_REGISTRY=${REGISTRY}"
  echo "NEXOPS_VERSION=${VERSION}"
  echo "DOCKER_GID=$(stat -c %g /var/run/docker.sock)"
  aws ssm get-parameters-by-path --region "$REGION" --path "/${PROJECT}/" --with-decryption --recursive \
    --query "Parameters[].[Name,Value]" --output text |
    while IFS=$'\t' read -r name value; do
      key="${name##*/}"
      # Values are single-line secrets; quote them so compose interpolation keeps special characters.
      printf "%s='%s'\n" "$key" "${value//\'/}"
    done
} > .env.tmp

# HTTPS hostname: SSM /<project>/NEXOPS_DOMAIN if set, otherwise a wildcard-DNS name for the Elastic IP.
if ! grep -q '^NEXOPS_DOMAIN=' .env.tmp; then
  echo "NEXOPS_DOMAIN=${PUBLIC_IP//./-}.sslip.io" >> .env.tmp
fi
if ! grep -q '^NEXOPS_PUBLIC_URL=' .env.tmp; then
  DOMAIN=$(grep '^NEXOPS_DOMAIN=' .env.tmp | cut -d= -f2- | tr -d "'")
  echo "NEXOPS_PUBLIC_URL=https://${DOMAIN}" >> .env.tmp
fi
if ! grep -q '^DATABASE_URL=' .env.tmp; then
  # No external database configured: use the bundled PostgreSQL container.
  echo "DATABASE_URL=postgresql+psycopg://nexops:$(grep '^NEXOPS_DB_PASSWORD=' .env.tmp | cut -d= -f2- | tr -d "'")@nexops-db:5432/nexops" >> .env.tmp
  echo "COMPOSE_PROFILES=local-db" >> .env.tmp
fi
mv .env.tmp .env
chmod 600 .env
