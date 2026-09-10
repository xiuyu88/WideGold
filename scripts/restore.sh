#!/usr/bin/env sh
set -eu

FILE="${1:-}"
if [ -z "$FILE" ] || [ ! -f "$FILE" ]; then
  echo "usage: scripts/restore.sh backups/widegold_YYYYMMDD_HHMMSS.sql.gz" >&2
  exit 2
fi
USER_NAME="${POSTGRES_USER:-widegold}"
DB_NAME="${POSTGRES_DB:-widegold}"

echo "[WideGold] WARNING: restoring $FILE into $DB_NAME"
echo "[WideGold] existing business tables may be overwritten by SQL statements in the dump."
gzip -dc "$FILE" | docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U "$USER_NAME" -d "$DB_NAME"
echo "[WideGold] restore complete"
