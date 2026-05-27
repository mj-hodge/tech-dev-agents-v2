#!/usr/bin/env bash
# cost_monitor.sh — Hourly cost check for agent VMs
# Deployed to /opt/agent/cost_monitor.sh
# Cron: */60 * * * * /opt/agent/cost_monitor.sh
#
# Checks:
# 1. Hermes main loop model is NOT opus (config drift detection)
# 2. Daily SDK session costs haven't exceeded threshold
# 3. Terminal guard is functional (not erroring)

set -euo pipefail

AGENT_NAME="${AGENT_NAME:-$(hostname | sed 's/vm-//' | sed 's/-agent-dev//')}"
DAILY_COST_LIMIT="${DAILY_COST_LIMIT:-10.00}"
LOG_TAG="[COST_MONITOR]"

log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) ${LOG_TAG} agent=${AGENT_NAME} $1"; }

# --- Check 1: Model config drift ---
CONFIG_MODEL=$(grep -A1 "^model:" /home/hermes/.hermes/config.yaml 2>/dev/null | grep "default:" | awk '{print $2}' || echo "unknown")
if echo "$CONFIG_MODEL" | grep -qi "opus"; then
    log "ALERT model_drift=true current_model=${CONFIG_MODEL} expected=sonnet — Hermes main loop is using Opus, burning ~15x more tokens"
fi

# --- Check 2: Daily SDK cost ---
TODAY=$(date -u +%Y-%m-%d)
DAILY_COST=$(journalctl -u hermes-gateway --since "${TODAY}" --no-pager 2>/dev/null \
    | grep -oP 'cost=\$\K[\d.]+' \
    | awk '{s+=$1} END {printf "%.2f", s}' 2>/dev/null || echo "0.00")

if [ "$(echo "${DAILY_COST} > ${DAILY_COST_LIMIT}" | bc -l 2>/dev/null || echo 0)" -eq 1 ]; then
    log "ALERT daily_cost_exceeded=true daily_cost=\$${DAILY_COST} limit=\$${DAILY_COST_LIMIT}"
else
    log "OK daily_cost=\$${DAILY_COST} limit=\$${DAILY_COST_LIMIT}"
fi

# --- Check 3: Terminal guard health ---
GUARD_OK=$(python3 -c "
import sys
sys.path.insert(0, '/opt/agent')
from terminal_guard import check_command
ok, _ = check_command('git status')
print('true' if ok else 'false')
" 2>/dev/null || echo "error")

if [ "$GUARD_OK" = "error" ]; then
    log "ALERT guard_broken=true — terminal_guard.py failed to import or execute"
elif [ "$GUARD_OK" = "false" ]; then
    log "ALERT guard_broken=true — terminal_guard.py denied 'git status' (should be allowed)"
else
    log "OK guard_healthy=true"
fi

# --- Check 4: Hermes model in use (from recent logs) ---
RECENT_MODEL=$(journalctl -u hermes-gateway --since "1 hour ago" --no-pager 2>/dev/null \
    | grep -oP 'model=\K[^ ]+' | tail -1 || echo "unknown")
if echo "$RECENT_MODEL" | grep -qi "opus"; then
    log "ALERT active_model_opus=true recent_model=${RECENT_MODEL} — Hermes is actively using Opus in API calls"
fi

# --- Check 5: Promtail log delivery (Loki query) ---
LOKI_URL="https://grafana.gorillacommerce.ai/loki/api/v1/query_range"
LOKI_RESPONSE=$(curl -s --max-time 10 -G "${LOKI_URL}" \
    --data-urlencode "query={agent=\"${AGENT_NAME}\"}" \
    --data-urlencode "start=$(date -u -d '10 min ago' +%s%N)" \
    --data-urlencode "end=$(date -u +%s%N)" \
    --data-urlencode "limit=1" 2>&1) || true
LOKI_CURL_EXIT=$?

if [ "${LOKI_CURL_EXIT}" -ne 0 ] || [ -z "${LOKI_RESPONSE}" ]; then
    log "ALERT loki_unreachable=true curl_exit=${LOKI_CURL_EXIT} — cannot reach Loki to verify Promtail delivery"
else
    RESULT_COUNT=$(echo "${LOKI_RESPONSE}" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    results = data.get('data', {}).get('result', [])
    print(len(results))
except Exception:
    print('-1')
" 2>/dev/null || echo "-1")

    if [ "${RESULT_COUNT}" = "-1" ]; then
        log "ALERT loki_unreachable=true — Loki returned unparseable response"
    elif [ "${RESULT_COUNT}" = "0" ]; then
        log "ALERT promtail_stale=true — no logs received in Loki for agent=${AGENT_NAME} in last 10 minutes"
    else
        LAST_TS=$(echo "${LOKI_RESPONSE}" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    vals = data['data']['result'][0]['values']
    ts_ns = int(vals[-1][0])
    from datetime import datetime, timezone
    dt = datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc)
    print(dt.strftime('%Y-%m-%dT%H:%M:%SZ'))
except Exception:
    print('unknown')
" 2>/dev/null || echo "unknown")
        log "OK promtail_healthy=true last_log_received=${LAST_TS}"
    fi
fi

# --- Check 6: Promtail service status ---
PROMTAIL_STATUS=$(systemctl is-active promtail 2>/dev/null || echo "unknown")
if [ "${PROMTAIL_STATUS}" != "active" ]; then
    log "ALERT promtail_down=true status=${PROMTAIL_STATUS} — restarting promtail"
    sudo systemctl restart promtail 2>/dev/null || true
else
    log "OK promtail_running=true"
fi
