#!/usr/bin/env bash
# weekly-patch.sh — apply OS security patches + update Claude Code globally.
# Deployed to /opt/agent/weekly-patch.sh on each agent VM.
# Cron: 0 6 * * 0  /opt/agent/weekly-patch.sh   (Sunday 06:00 UTC)
#
# Why a self-cron rather than central orchestration:
# - No central Azure auth required (no shared secret to leak)
# - Each VM owns its own patch cadence
# - Failure on one VM doesn't block patching others
#
# Authoritative reference: deployment/vm/NEW-AGENT-PROCESS.md (Phase 6 crons)

set -u
LOG=/var/log/weekly-patch.log
TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)

log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*" | tee -a "$LOG" >&2; }

log "=== weekly-patch START ==="

# 1. Capture before-state for diff visibility
BEFORE_KERNEL=$(uname -r)
BEFORE_CLAUDE=$(sudo -u hermes claude --version 2>&1 | head -1)
log "before: kernel=$BEFORE_KERNEL  claude=$BEFORE_CLAUDE"

# 2. OS security upgrades (no full dist-upgrade — keep this conservative)
log "apt-get update + upgrade (security only)"
{
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get upgrade -y -qq \
    -o Dpkg::Options::="--force-confdef" \
    -o Dpkg::Options::="--force-confold"
  apt-get autoremove -y -qq
} >> "$LOG" 2>&1
APT_RC=$?
log "apt exit=$APT_RC"

# 3. Claude Code update via npm (global install — root needed)
log "npm i -g @anthropic-ai/claude-code"
npm i -g @anthropic-ai/claude-code --silent >> "$LOG" 2>&1
NPM_RC=$?
log "npm exit=$NPM_RC"

# 4. Verify auth still works AFTER update (the hermes user runs claude)
AUTH_STATUS=$(sudo -u hermes claude auth status 2>&1 | grep -E '"loggedIn"' | head -1)
log "auth: $AUTH_STATUS"

# 5. Verify the keepalive command works (returns 'pong' if token is valid)
PING=$(timeout 30 sudo -u hermes claude -p ping --max-turns 1 < /dev/null 2>&1 | tail -1)
log "ping: $PING"

# 6. Capture after-state
AFTER_KERNEL=$(uname -r)
AFTER_CLAUDE=$(sudo -u hermes claude --version 2>&1 | head -1)
log "after:  kernel=$AFTER_KERNEL  claude=$AFTER_CLAUDE"

# 7. If kernel changed, schedule a reboot for next maintenance window — DO NOT
# auto-reboot here. An unscheduled reboot mid-SDK-session would lose work.
if [ "$BEFORE_KERNEL" != "$AFTER_KERNEL" ]; then
  log "ALERT: kernel changed ($BEFORE_KERNEL -> $AFTER_KERNEL). Reboot required at next maintenance window."
fi

# 8. Exit non-zero if anything important failed
if [ "$APT_RC" -ne 0 ] || [ "$NPM_RC" -ne 0 ] || [[ "$PING" != *pong* ]]; then
  log "=== weekly-patch FAILED (apt=$APT_RC npm=$NPM_RC ping=$PING) ==="
  exit 1
fi

log "=== weekly-patch OK ==="
exit 0
