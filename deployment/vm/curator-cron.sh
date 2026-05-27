#!/usr/bin/env bash
# curator-cron.sh — Weekly Cole the Curator trigger (STORY-340)
#
# Cron: 0 13 * * 0 (Sunday 13:00 UTC / 9 AM ET)
# Triggers Cole via the ops-console dispatch queue.
# If the knowledgebase has no changes since the last run, posts
# "Nothing to curate this week" to Mark's Teams DM and exits.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KB_REPO="/home/hermes/dev/hpi-gorillacommerce/tech-gc-knowledgebase"
STATE_DIR="/home/hermes/state/morris"
LAST_RUN_FILE="${STATE_DIR}/last-curator-run.txt"
OPS_CONSOLE_URL="${OPS_CONSOLE_URL:-http://localhost:8080}"
API_KEY="${OPS_API_KEY:-}"

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] curator-cron: $*"; }

# --- Preflight ---
if [ ! -d "${KB_REPO}" ]; then
    log "ERROR: knowledgebase repo not found at ${KB_REPO}"
    exit 1
fi

if [ -z "${API_KEY}" ]; then
    log "ERROR: OPS_API_KEY not set"
    exit 1
fi

# --- Check for changes since last run ---
last_sha=""
if [ -f "${LAST_RUN_FILE}" ]; then
    last_sha=$(cat "${LAST_RUN_FILE}" 2>/dev/null || true)
fi

current_sha=$(git -C "${KB_REPO}" rev-parse HEAD 2>/dev/null || echo "unknown")

# Also check for uncommitted scratch changes
scratch_changes=$(git -C "${KB_REPO}" status --porcelain scratch/ sources/ 2>/dev/null | wc -l || echo "0")

if [ "${current_sha}" = "${last_sha}" ] && [ "${scratch_changes}" -eq 0 ]; then
    log "No changes since last run (${current_sha}). Skipping."

    # Post skip notice to Teams via ops-console
    curl -sf -X POST "${OPS_CONSOLE_URL}/api/agents/mark/message" \
        -H "X-API-Key: ${API_KEY}" \
        -H "Content-Type: application/json" \
        -d '{"content": "Cole (Morris-as-curator): Nothing to curate this week. No new scratch files or source changes since last run."}' \
        >/dev/null 2>&1 || log "WARN: Failed to send skip notice to Teams"

    exit 0
fi

# --- Trigger curation via dispatch ---
log "Changes detected (last=${last_sha:-none}, current=${current_sha}, scratch_changes=${scratch_changes}). Triggering curation."

response=$(curl -sf -X POST "${OPS_CONSOLE_URL}/api/morris/curate" \
    -H "X-API-Key: ${API_KEY}" \
    -H "Content-Type: application/json" \
    2>&1) || {
    log "ERROR: Failed to trigger curation: ${response}"
    exit 1
}

log "Curation triggered: ${response}"

# --- Update last-run state ---
mkdir -p "${STATE_DIR}"
echo "${current_sha}" > "${LAST_RUN_FILE}"

log "Done. State saved."
