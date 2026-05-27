#!/usr/bin/env bash
# install-disk-cleanup-cron.sh — Install the daily disk cleanup cron entry.
#
# Installs a cron entry for agent-disk-cleanup.sh to run at 03:00 UTC daily.
# Idempotent: removes any existing agent-disk-cleanup entry before adding.
#
# Usage:
#   install-disk-cleanup-cron.sh
#
# The cron entry runs as the current user (hermes), not root.
# Logs to /var/log/agent-cleanup.log.
#
# SC-2: 0 3 * * * /opt/agent/agent-disk-cleanup.sh >> /var/log/agent-cleanup.log 2>&1

set -euo pipefail

SCRIPT_PATH="/opt/agent/agent-disk-cleanup.sh"
CRON_ENTRY="0 3 * * * ${SCRIPT_PATH} >> /var/log/agent-cleanup.log 2>&1"
CRON_MARKER="agent-disk-cleanup"

echo "[INSTALL] Installing disk cleanup cron entry for user $(whoami)"

# Copy the script to /opt/agent/ (idempotent)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE_SCRIPT="${SCRIPT_DIR}/agent-disk-cleanup.sh"

if [[ -f "$SOURCE_SCRIPT" ]]; then
    sudo mkdir -p /opt/agent
    sudo cp "$SOURCE_SCRIPT" "$SCRIPT_PATH"
    sudo chmod +x "$SCRIPT_PATH"
    echo "[INSTALL] Copied agent-disk-cleanup.sh to ${SCRIPT_PATH}"
else
    echo "[INSTALL] WARNING: source script not found at ${SOURCE_SCRIPT}"
fi

# Ensure log file exists and is writable
sudo touch /var/log/agent-cleanup.log
sudo chown "$(whoami)":"$(whoami)" /var/log/agent-cleanup.log

# Remove old cron entry (idempotent — Escalation #4)
# Filter out any existing agent-disk-cleanup lines, then add the new one
EXISTING_CRON=$(crontab -l 2>/dev/null | grep -v "$CRON_MARKER" || true)

# Add the new entry
echo "${EXISTING_CRON}
${CRON_ENTRY}" | crontab -

echo "[INSTALL] Cron entry installed:"
echo "  ${CRON_ENTRY}"

# Install logrotate config (AC-10: 14-day retention)
LOGROTATE_CONF="/etc/logrotate.d/agent-cleanup"
sudo tee "$LOGROTATE_CONF" > /dev/null <<'LOGROTATE'
/var/log/agent-cleanup.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    create 0644 hermes hermes
}
LOGROTATE

echo "[INSTALL] Logrotate config installed at ${LOGROTATE_CONF} (14-day retention)"
echo "[INSTALL] Done."
