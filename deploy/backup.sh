#!/usr/bin/env bash
# Nightly database backup, keeps 14 days. Cron (crontab -e):
#   30 3 * * * /home/ubuntu/nitsdate/deploy/backup.sh >> /home/ubuntu/nitsdate-backups/backup.log 2>&1
set -euo pipefail
umask 077   # backups hold every member's data: owner-only
DIR="$HOME/nitsdate-backups"
mkdir -p "$DIR"
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
STAMP=$(date +%Y%m%d-%H%M)
MYSQL_PWD="$DB_PASSWORD" mysqldump -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" --single-transaction --routines --triggers \
    "$DB_NAME" | gzip > "$DIR/nitsdate-$STAMP.sql.gz"
find "$DIR" -name 'nitsdate-*.sql.gz' -mtime +14 -delete
echo "$(date -Is) backup ok: $DIR/nitsdate-$STAMP.sql.gz"
