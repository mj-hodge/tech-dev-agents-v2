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
