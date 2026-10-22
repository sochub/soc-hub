#!/usr/bin/env bash
# Build the static website demo: fixtures from a throwaway stack, then the demo frontend.
#
#   scripts/build-demo.sh [OUT_DIR]     # default: frontend/dist-demo
#
# Spins up a disposable Postgres/Redis (compose project "sochub-demo"), seeds a fictional tenant,
# records API fixtures, and removes everything again. Your dev database is never touched.
set -euo pipefail
cd "$(dirname "$0")/.."
OUT="${1:-frontend/dist-demo}"
WORK="$(mktemp -d)"
TAG="demo-build"
cleanup() {
  docker compose -p sochub-demo -f "$WORK/docker-compose.prod.yml" --env-file "$WORK/.env" down -v --remove-orphans >/dev/null 2>&1 || true
  docker image rm -f "docker.io/sochub/sochub-backend:$TAG" >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT

rnd() { python3 -c "import secrets; print(secrets.token_urlsafe(32))"; }
cp docker-compose.prod.yml "$WORK/"
cat > "$WORK/.env" <<ENV
SOCHUB_TAG=$TAG
POSTGRES_USER=demo
POSTGRES_PASSWORD=$(rnd)
POSTGRES_DB=demo
REDIS_PASSWORD=$(rnd)
SECRET_KEY=$(rnd)$(rnd)
BACKEND_CORS_ORIGINS=http://localhost
PUBLIC_BASE_URL=http://localhost
STORAGE_BACKEND=local
ENV

echo "==> building backend image"
docker build -q -t "docker.io/sochub/sochub-backend:$TAG" backend >/dev/null
echo "==> recording fixtures (throwaway database)"
docker compose -p sochub-demo -f "$WORK/docker-compose.prod.yml" --env-file "$WORK/.env" \
  run --rm -T backend python -m app.scripts.demo_fixtures > "$WORK/fixtures.json"
echo "==> building demo frontend into $OUT"
mkdir -p "$OUT" && OUT="$(cd "$OUT" && pwd)"
(cd frontend && npm run build:demo -- --outDir "$OUT" --emptyOutDir)
cp "$WORK/fixtures.json" "$OUT/demo-fixtures.json"
echo "==> done: $(du -sh "$OUT" | cut -f1) in $OUT"
