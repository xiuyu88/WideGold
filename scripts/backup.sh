#!/usr/bin/env sh
set -eu

STAMP="$(date +%Y%m%d_%H%M%S)"
OUT="${1:-backups/widegold_${STAMP}.sql.gz}"
mkdir -p "$(dirname "$OUT")"
USER_NAME="${POSTGRES_USER:-widegold}"
DB_NAME="${POSTGRES_DB:-widegold}"

echo "[WideGold] backing up ${DB_NAME} -> ${OUT}"
docker compose exec -T postgres pg_dump -U "$USER_NAME" -d "$DB_NAME" --no-owner --no-privileges | gzip > "$OUT"
echo "[WideGold] backup complete: $OUT"
