#!/usr/bin/env bash
# Deploy an image built by CI and roll back automatically if it doesn't come up healthy.
#
#   deploy/deploy.sh sha-1a2b3c4        # run by GitHub Actions, or by hand
#   deploy/deploy.sh sha-0f9e8d7        # manual rollback to any earlier tag
#
# Steps: pull the image -> back up the database -> start it -> wait for /health/
# (the web container's healthcheck). On failure the previous tag is started again.
# The current tag is kept as IMAGE_TAG in deploy/.env, so plain
# `docker compose -f docker-compose.prod.yml ...` commands use the deployed version.
set -euo pipefail

NEW_TAG="${1:?usage: deploy.sh <image-tag>, e.g. sha-1a2b3c4}"
DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$DEPLOY_DIR/.env"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-180}"
COMPOSE=(docker compose -f "$DEPLOY_DIR/docker-compose.prod.yml" --project-directory "$DEPLOY_DIR")

log() { echo "[$(date -Is)] $*"; }

current_tag() {
  grep -E '^IMAGE_TAG=' "$ENV_FILE" 2>/dev/null | tail -n1 | cut -d= -f2- || true
}

set_tag() {
  if grep -qE '^IMAGE_TAG=' "$ENV_FILE"; then
    sed -i "s|^IMAGE_TAG=.*|IMAGE_TAG=$1|" "$ENV_FILE"
  else
    printf '\nIMAGE_TAG=%s\n' "$1" >> "$ENV_FILE"
  fi
}

start() {
  set_tag "$1"
  "${COMPOSE[@]}" up -d --no-build --remove-orphans
}

wait_healthy() {
  local deadline=$((SECONDS + HEALTH_TIMEOUT)) cid status
  while (( SECONDS < deadline )); do
    cid="$("${COMPOSE[@]}" ps -q web)"
    status="$(docker inspect -f '{{.State.Health.Status}}' "$cid" 2>/dev/null || echo missing)"
    case "$status" in
      healthy) return 0 ;;
      unhealthy) return 1 ;;
    esac
    sleep 5
  done
  return 1
}

[[ -f "$ENV_FILE" ]] || { echo "Missing $ENV_FILE (copy .env.example)" >&2; exit 1; }
PREV_TAG="$(current_tag)"
log "deploying $NEW_TAG (currently: ${PREV_TAG:-none})"

IMAGE_TAG="$NEW_TAG" "${COMPOSE[@]}" pull --quiet web worker

if [[ -n "$("${COMPOSE[@]}" ps -q db)" ]]; then
  log "backing up the database before migrations"
  "$DEPLOY_DIR/backup.sh" || log "WARNING: backup failed, continuing"
fi

start "$NEW_TAG"
if wait_healthy; then
  log "OK: $NEW_TAG is healthy"
  docker image prune -f >/dev/null
  exit 0
fi

log "FAILED: $NEW_TAG did not become healthy within ${HEALTH_TIMEOUT}s"
"${COMPOSE[@]}" logs --tail 50 web || true

if [[ -n "$PREV_TAG" && "$PREV_TAG" != "$NEW_TAG" ]]; then
  log "rolling back to $PREV_TAG"
  start "$PREV_TAG"
  if wait_healthy; then
    log "rolled back to $PREV_TAG"
  else
    log "ERROR: rollback to $PREV_TAG is not healthy either, check the logs"
  fi
  log "note: database migrations are not reverted; the pre-deploy dump is in /var/backups/oxi"
else
  log "no previous tag to roll back to"
fi
exit 1
