#!/usr/bin/env bash
# Daily backup of the database and uploaded photos. Keeps 30 days.
#   deploy/backup.sh                 # run by cron, see DEPLOY.md
#   BACKUP_DIR=/tmp/x deploy/backup.sh
set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/oxi}"
KEEP_DAYS="${KEEP_DAYS:-30}"
STAMP="$(date +%Y-%m-%d_%H%M)"
COMPOSE=(docker compose -f "$DEPLOY_DIR/docker-compose.prod.yml" --project-directory "$DEPLOY_DIR")

mkdir -p "$BACKUP_DIR"

# Database: custom-format dump, restorable with pg_restore.
"${COMPOSE[@]}" exec -T db pg_dump -U oxi -d oxi -Fc > "$BACKUP_DIR/db_$STAMP.dump.part"
mv "$BACKUP_DIR/db_$STAMP.dump.part" "$BACKUP_DIR/db_$STAMP.dump"

# Uploaded photos (Wagtail originals; renditions are regenerated on demand).
"${COMPOSE[@]}" exec -T web tar -C /app/media -czf - original_images documents 2>/dev/null \
  > "$BACKUP_DIR/media_$STAMP.tar.gz" || true

find "$BACKUP_DIR" -name 'db_*.dump' -mtime +"$KEEP_DAYS" -delete
find "$BACKUP_DIR" -name 'media_*.tar.gz' -mtime +"$KEEP_DAYS" -delete

echo "$(date -Is) backup ok: db_$STAMP.dump ($(du -h "$BACKUP_DIR/db_$STAMP.dump" | cut -f1))"
