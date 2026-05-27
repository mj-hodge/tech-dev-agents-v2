#!/usr/bin/env bash
# health-ping.sh — pure-shell liveness probe for Morris cron.
# Replaces: `claude -p ping --max-turns 1` (was invoking Opus, ~$0 but wrong model billing path)
# No LLM involved. Exits 0 if ops-console responds 2xx, 1 otherwise.

set -u
LOG=/var/log/morris-health-ping.log
TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)

OPS_URL="${OPS_CONSOLE_URL:-https://tech-dev-agents.gorillacommerce.ai}"
OPS_KEY="$(sudo grep -oP '(?<=OPS_CONSOLE_API_KEY=).+' /opt/agent/.env | head -1)"

HTTP_CODE=$(curl -sf -o /dev/null -w "%{http_code}" \
    -H "X-API-Key: $OPS_KEY" \
    "$OPS_URL/healthz" 2>/dev/null || echo "000")

if [[ "$HTTP_CODE" =~ ^2 ]]; then
    echo "$TS health-ping OK ($HTTP_CODE)" >> "$LOG"
    exit 0
else
    echo "$TS health-ping FAIL ($HTTP_CODE)" >> "$LOG"
    exit 1
fi
