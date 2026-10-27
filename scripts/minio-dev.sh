#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
DATA_DIR="$ROOT/data/minio-dev"
ENV_FILE="$ROOT/data/minio-dev.env"

command -v docker >/dev/null 2>&1 || { echo "Install and start Docker Desktop first." >&2; exit 1; }
docker info >/dev/null
mkdir -p "$DATA_DIR"
if [ ! -f "$ENV_FILE" ]; then
  ACCESS_KEY=$(openssl rand -hex 12)
  SECRET_KEY=$(openssl rand -hex 32)
  umask 077
  printf 'MINIO_ENDPOINT=127.0.0.1:9000\nMINIO_ACCESS_KEY=%s\nMINIO_SECRET_KEY=%s\nMINIO_BUCKET=software-portal-dev\nMINIO_SECURE=false\n' "$ACCESS_KEY" "$SECRET_KEY" > "$ENV_FILE"
fi
chmod 600 "$ENV_FILE"

docker compose --project-name duwuku-storage --env-file "$ENV_FILE" -f "$ROOT/compose.minio.yaml" up -d

ATTEMPTS=0
until curl --silent --fail --max-time 2 http://127.0.0.1:9000/minio/health/ready >/dev/null; do
  ATTEMPTS=$((ATTEMPTS + 1))
  if [ "$ATTEMPTS" -ge 30 ]; then
    echo "MinIO did not become ready. Check docker compose logs." >&2
    exit 1
  fi
  sleep 2
done
printf 'MinIO ready: http://127.0.0.1:9000\nConsole: http://127.0.0.1:9001\nBackend environment: %s\n' "$ENV_FILE"
