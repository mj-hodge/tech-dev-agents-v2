#!/usr/bin/env bash
# loki-query.sh — Query Loki logs for agent activity
# Usage: bash tools/loki-query.sh <agent> <query_filter> [hours_back]
#
# Examples:
#   bash tools/loki-query.sh dan "START|DONE|END" 2
#   bash tools/loki-query.sh derrick "claude-sdk|Claude Code" 1
#   bash tools/loki-query.sh all "429|rate|ERROR" 4
set -euo pipefail

source /mnt/c/Projects/tech-dev-agents/.env 2>/dev/null

AGENT="${1:-all}"
FILTER="${2:-START|DONE|END}"
HOURS="${3:-2}"
LIMIT="${4:-30}"

NOW=$(date -u +%s)
START=$((NOW - HOURS * 3600))

if [ "$AGENT" = "all" ]; then
  QUERY="{agent=~\"dan|derrick\"} |~ \"${FILTER}\""
else
  QUERY="{agent=\"${AGENT}\"} |~ \"${FILTER}\""
fi

curl -sf --max-time 15 \
  "https://grafana.gorillacommerce.ai/loki/api/v1/query_range" \
  -H "Authorization: Bearer ${OPS_LOKI_API_KEY}" \
  --data-urlencode "query=${QUERY}" \
  --data-urlencode "start=${START}" \
  --data-urlencode "end=${NOW}" \
  --data-urlencode "limit=${LIMIT}" \
  --data-urlencode "direction=backward"
