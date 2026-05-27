#!/usr/bin/env bash
# run-converge.sh — cron wrapper for converge.py on agent VMs.
#
# Strips Foundry/subscription billing env vars (same pattern as run_cron.sh)
# before invoking converge.py, so the converge process is not billed to
# the Foundry agent subscription.
#
# Deployed to: /opt/agent/run-converge.sh
# Crontab (added by /schedule-cron skill during Phase 8 deployment):
#   */30 * * * * root /opt/agent/run-converge.sh
#
# Logs go to /var/log/converge/<hostname>.log.
# logrotate picks these up via the existing agent logrotate config.
#
# STORY-644: declarative VM-state convergence pilot

set -euo pipefail

AGENT_LOG_DIR="/var/log/converge"
LOG_FILE="${AGENT_LOG_DIR}/$(hostname -s).log"

# Create log directory if missing (first run)
mkdir -p "$AGENT_LOG_DIR"

# Strip Foundry billing env vars (do NOT let converge.py be charged to
# the per-agent Foundry subscription — it runs as a system cron job).
unset ANTHROPIC_BEDROCK_BASE_URL
unset CLAUDE_CODE_USE_BEDROCK
unset AWS_REGION
unset AWS_DEFAULT_REGION
unset FOUNDRY_AGENT_ID
unset CLAUDE_CODE_FOUNDRY_ENDPOINT

# Source the agent env for OPS_CONSOLE_URL etc. (needed if converge ever
# needs to call back to the ops console — not required in the pilot, but
# harmless to source).
if [[ -f /opt/agent/.env ]]; then
    # shellcheck disable=SC1091
    set +u  # .env may reference unset vars
    source /opt/agent/.env
    set -u
fi

exec /usr/bin/python3 /opt/agent/converge.py \
    --config /opt/agent/canonical-state.yaml \
    --registry /opt/agent/agent-registry.json \
    >> "$LOG_FILE" 2>&1
