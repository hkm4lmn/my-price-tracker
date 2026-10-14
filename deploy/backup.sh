#!/usr/bin/env bash
# Nightly database backup, kept for 7 days. Run from cron (see deploy/README.md).
set -euo pipefail

cd "$(dirname "$0")/.."
dir=/var/backups/price-tracker
mkdir -p "$dir"
target="$dir/$(date +%F).sql.gz"

# Write to a temporary file first so a failed dump never replaces a good one.
docker compose -f deploy/docker-compose.prod.yml --env-file .env exec -T db \
  pg_dump -U tracker tracker | gzip > "$target.tmp"
mv "$target.tmp" "$target"

find "$dir" -name '*.sql.gz' -mtime +7 -delete
