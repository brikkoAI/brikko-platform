#!/usr/bin/env bash
# /opt/voltari/infra/scripts/setup-s3-lifecycle.sh
#
# Idempotent: configure / re-configure object-lifecycle on the Voltari
# backup bucket. Re-running is a no-op when the policy is already current.
#
# Schedule (matches infra/scripts/backup-pg.sh paths):
#   daily/   →  expire after  7 days   (rolling daily snapshots)
#   weekly/  →  expire after 28 days   (4 weekly snapshots)
#   monthly/ →  expire after 365 days  (12 monthly snapshots)
#
# When to run:
#   - Once after first provisioning the bucket.
#   - After rotating the S3 access key (sanity-check: aws s3 ls $BUCKET).
#   - If you change retention rules in this script.
#
# Required environment (or pass via .env):
#   S3_ENDPOINT       e.g. https://s3.ru-1.storage.selcloud.ru
#   BACKUP_BUCKET     e.g. voltari-backups-pg
#   AWS_ACCESS_KEY_ID
#   AWS_SECRET_ACCESS_KEY
#
# Cost: lifecycle rules сами по себе бесплатны у Selectel и YC OS. Реальная
# экономия — ~150-300 ₽/мес после полугода работы.
#
# Source-of-truth: ./infra/RUNBOOK.md → "Backups & retention".

set -euo pipefail

# Load .env from /opt/voltari/.env when present (production), or local override.
if [ -f "/opt/voltari/.env" ]; then
  # shellcheck disable=SC1091
  set -a; . /opt/voltari/.env; set +a
elif [ -f "$(dirname "$0")/../.env" ]; then
  # shellcheck disable=SC1091
  set -a; . "$(dirname "$0")/../.env"; set +a
fi

: "${S3_ENDPOINT:?S3_ENDPOINT is not set (e.g. https://s3.ru-1.storage.selcloud.ru)}"
: "${BACKUP_BUCKET:?BACKUP_BUCKET is not set (e.g. voltari-backups-pg)}"
: "${AWS_ACCESS_KEY_ID:?AWS_ACCESS_KEY_ID is not set}"
: "${AWS_SECRET_ACCESS_KEY:?AWS_SECRET_ACCESS_KEY is not set}"

if ! command -v aws >/dev/null 2>&1; then
  echo "ERROR: awscli is not installed. Install with: apt-get install -y awscli" >&2
  exit 2
fi

LIFECYCLE_JSON=$(cat <<'EOF'
{
  "Rules": [
    {
      "ID": "voltari-daily-7d",
      "Status": "Enabled",
      "Filter": {"Prefix": "daily/"},
      "Expiration": {"Days": 7}
    },
    {
      "ID": "voltari-weekly-28d",
      "Status": "Enabled",
      "Filter": {"Prefix": "weekly/"},
      "Expiration": {"Days": 28}
    },
    {
      "ID": "voltari-monthly-365d",
      "Status": "Enabled",
      "Filter": {"Prefix": "monthly/"},
      "Expiration": {"Days": 365}
    },
    {
      "ID": "voltari-incomplete-multipart-cleanup",
      "Status": "Enabled",
      "Filter": {"Prefix": ""},
      "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 1}
    }
  ]
}
EOF
)

echo "[setup-s3-lifecycle] target bucket: ${BACKUP_BUCKET}"
echo "[setup-s3-lifecycle] endpoint:      ${S3_ENDPOINT}"

# Sanity-check: bucket reachable. If this fails — credentials/endpoint wrong.
if ! aws --endpoint-url "$S3_ENDPOINT" s3api head-bucket --bucket "$BACKUP_BUCKET" 2>/dev/null; then
  echo "ERROR: cannot reach bucket ${BACKUP_BUCKET} via ${S3_ENDPOINT}" >&2
  echo "Check: AWS_ACCESS_KEY_ID/SECRET, S3_ENDPOINT, bucket name." >&2
  exit 3
fi

# Apply (idempotent: put-bucket-lifecycle-configuration overwrites).
echo "$LIFECYCLE_JSON" | aws \
  --endpoint-url "$S3_ENDPOINT" \
  s3api put-bucket-lifecycle-configuration \
    --bucket "$BACKUP_BUCKET" \
    --lifecycle-configuration file:///dev/stdin

echo "[setup-s3-lifecycle] OK — policy applied."
echo
echo "Verify:"
echo "  aws --endpoint-url $S3_ENDPOINT s3api get-bucket-lifecycle-configuration --bucket $BACKUP_BUCKET"
