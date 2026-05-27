#!/usr/bin/env bash
# daily_cost_alert.sh — Daily Foundry spend gate (AC-7)
# Cron: 0 18 * * * /opt/ops-console/scripts/daily_cost_alert.sh
set -euo pipefail

THRESHOLD="${FOUNDRY_DAILY_THRESHOLD:-20.00}"
LOG_TAG="[COST_ALERT]"
TODAY=$(date -u +%Y-%m-%d)

log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) ${LOG_TAG} $1"; }

# Query today's total from foundry_cost_daily (populated by refresh_foundry_cost.py every 2h)
TOTAL=$(psql "${DATABASE_URL}" -t -A -v today="${TODAY}" -c \
  "SELECT COALESCE(opus_usd + sonnet_usd + haiku_usd + other_usd, 0)
   FROM foundry_cost_daily
   WHERE usage_date = :'today';" 2>/dev/null || echo "0.00")

if [ -z "$TOTAL" ] || [ "$TOTAL" = "" ]; then
    TOTAL="0.00"
fi

EXCEEDED=$(echo "${TOTAL} > ${THRESHOLD}" | bc -l 2>/dev/null || echo 0)

if [ "$EXCEEDED" -eq 1 ]; then
    log "ALERT foundry_daily_exceeded=true date=${TODAY} total=\$${TOTAL} threshold=\$${THRESHOLD} — PAGES MARK"
else
    log "OK foundry_daily_cost=\$${TOTAL} threshold=\$${THRESHOLD} date=${TODAY}"
fi
