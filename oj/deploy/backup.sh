#!/usr/bin/env bash
# Nightly backup. Add to root's crontab:
#   15 3 * * * /srv/oj/app/deploy/backup.sh >> /var/log/oj-backup.log 2>&1
#
# Two things need backing up and they fail differently: the database holds
# submissions and accounts, the problem directory holds test data you may
# have spent a weekend writing. Losing either is bad; losing the test data is
# worse, because it cannot be reconstructed from anything.

set -euo pipefail

DEST="${OJ_BACKUP_DIR:-/srv/oj/backups}"
KEEP_DAYS="${OJ_BACKUP_KEEP:-30}"
STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$DEST"

# shellcheck disable=SC1091
set -a; . /etc/oj/oj.env; set +a

if [ "${OJ_DB:-sqlite}" = "postgres" ]; then
  PGPASSWORD="$OJ_DB_PASSWORD" pg_dump \
    -h "$OJ_DB_HOST" -U "$OJ_DB_USER" -d "$OJ_DB_NAME" \
    --format=custom --file="$DEST/db-$STAMP.dump"
else
  cp "${OJ_DB_PATH:-/srv/oj/app/web/dev.sqlite3}" "$DEST/db-$STAMP.sqlite3"
fi

tar -czf "$DEST/problems-$STAMP.tar.gz" -C "$(dirname "$OJ_PROBLEM_ROOT")" \
    "$(basename "$OJ_PROBLEM_ROOT")"

# Statement PDFs live outside the problem directory because they are uploaded
# through the admin rather than shipped in packages.
if [ -d "${OJ_MEDIA_ROOT:-/srv/oj/media}" ]; then
  tar -czf "$DEST/media-$STAMP.tar.gz" \
      -C "$(dirname "${OJ_MEDIA_ROOT:-/srv/oj/media}")" \
      "$(basename "${OJ_MEDIA_ROOT:-/srv/oj/media}")"
fi

find "$DEST" -type f -mtime "+$KEEP_DAYS" -delete

echo "$(date -Is) backup ok: $DEST/db-$STAMP.* and problems-$STAMP.tar.gz"

# A backup you have never restored is a hypothesis, not a backup. Once a term:
#   pg_restore --list /srv/oj/backups/db-XXXX.dump | head
#   tar -tzf /srv/oj/backups/problems-XXXX.tar.gz | head
