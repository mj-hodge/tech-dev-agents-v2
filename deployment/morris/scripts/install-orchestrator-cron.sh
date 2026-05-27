#!/usr/bin/env bash
set -euo pipefail
# ============================================================================
# install-orchestrator-cron.sh — Idempotent cron installer for Morris VM
# STORY-724: Morris Queue Orchestrator
#
# Usage: bash install-orchestrator-cron.sh
# Installs two cron entries under the CRON_MARKER guard (idempotent).
# ============================================================================

# --- Install systemd-tmpfiles entry for orchestrator lock ---
# /var/run is tmpfs and is wiped on every boot. Without this, every cron tick
# after a reboot crashes with PermissionError on /var/run/morris-orchestrator.lock.
# Idempotent: only writes when src/dst differ. Runs before cron install so the
# lock is in place when the first scheduled run fires.
TMPFILES_SRC="$(dirname "$(readlink -f "$0")")/../morris-orchestrator.tmpfiles.conf"
TMPFILES_DST="/etc/tmpfiles.d/morris-orchestrator.conf"
if [ -f "$TMPFILES_SRC" ]; then
    if ! sudo cmp -s "$TMPFILES_SRC" "$TMPFILES_DST" 2>/dev/null; then
        sudo install -m 0644 "$TMPFILES_SRC" "$TMPFILES_DST"
        sudo systemd-tmpfiles --create "$TMPFILES_DST" 2>/dev/null || true
        echo "Installed tmpfiles.d entry → $TMPFILES_DST"
    fi
else
    echo "WARN: tmpfiles source not found at $TMPFILES_SRC — skipping lock-file bootstrap" >&2
fi

# Helper: install one cron block if its marker isn't already in the crontab.
# Each section is independent — never `exit 0` mid-script (2026-04-26 bug:
# the first matched marker exited the script before later sections could run,
# so the morris-state-commit entry never installed even though the file
# claimed to be idempotent).
install_cron_block() {
    local marker="$1"
    local label="$2"
    local block="$3"
    local current
    current=$(crontab -l 2>/dev/null || echo "")
    if echo "$current" | grep -q "$marker"; then
        echo "$label cron already installed."
        return 0
    fi
    ( echo "$current"; printf '\n%s\n' "$block" ) | crontab -
    echo "$label cron installed."
}

install_cron_block "# morris-orchestrator-724" "Orchestrator" "# morris-orchestrator-724
*/10 * * * * /opt/morris/venv/bin/python /opt/morris/orchestrator_loop.py --config /opt/morris/orchestrator_config.yaml >> /var/log/morris/orchestrator.log 2>&1
30 13 * * 1-5 /opt/morris/venv/bin/python /opt/morris/orchestrator_loop.py --briefing-only --config /opt/morris/orchestrator_config.yaml >> /var/log/morris/orchestrator.log 2>&1"

install_cron_block "# morris-contact-tracking" "Contact summary" "# morris-contact-tracking
0 8 * * 1-5 /opt/morris/venv/bin/python /opt/morris/daily_contact_summary.py --config /opt/morris/orchestrator_config.yaml >> /var/log/morris/orchestrator.log 2>&1"

install_cron_block "# morris-foundry-pace-585" "Foundry pace" "# morris-foundry-pace-585
5 * * * * /opt/morris/venv/bin/python /opt/morris/foundry_pace_check.py >> /var/log/morris/foundry-pace.log 2>&1"

install_cron_block "# morris-state-commit" "State-commit" "# morris-state-commit — daily commit + push of state/morris/*.md to main
0 4 * * * /opt/morris/state-commit.sh >> /var/log/morris/state-commit.log 2>&1"

install_cron_block "# morris-operating-modes-734" "Overnight mode" "# morris-operating-modes-734 — STORY-734: overnight scheduling (05:00 UTC = 00:00 ET → light, 12:00 UTC = 07:00 ET → full)
0 5 * * *  /opt/morris/venv/bin/python /opt/morris/set_mode.py light --reason overnight_schedule >> /var/log/morris/orchestrator.log 2>&1
0 12 * * * /opt/morris/venv/bin/python /opt/morris/set_mode.py full  --reason overnight_end      >> /var/log/morris/orchestrator.log 2>&1"
