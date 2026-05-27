#!/usr/bin/env bash
# fix-morris-fleet-check.sh — One-shot remediation for STORY-323
#
# Run on Morris VM as root:
#   sudo bash /path/to/fix-morris-fleet-check.sh
#
# What it does:
#   1. Removes stale lock file if present
#   2. Kills any hung fleet-check processes
#   3. Deploys hardened fleet-check script to /opt/agent/
#   4. Installs system cron in /etc/cron.d/ (survives crontab overwrites)
#   5. Removes fleet-check line from hermes user crontab (if present) to avoid dupes
#   6. Runs the script once to verify it works
#   7. Reports results

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCK=/var/run/morris-fleet-check.lock
LOG=/var/log/morris-fleet-check.log

echo "=== STORY-323 fleet-check fix — $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

# 1. Kill any hung fleet-check processes
echo "[1/6] Checking for hung processes..."
HUNG_PIDS=$(pgrep -f "morris-fleet-check\.sh" 2>/dev/null || true)
if [ -n "$HUNG_PIDS" ]; then
    echo "  Killing hung processes: $HUNG_PIDS"
    kill $HUNG_PIDS 2>/dev/null || true
    sleep 2
    kill -9 $HUNG_PIDS 2>/dev/null || true
else
    echo "  No hung processes found"
fi

# 2. Remove stale lock
echo "[2/6] Removing stale lock file..."
if [ -f "$LOCK" ]; then
    LOCK_AGE=$(( ( $(date +%s) - $(stat -c %Y "$LOCK") ) / 60 ))
    echo "  Lock file exists (age: ${LOCK_AGE}m) — removing"
    rm -f "$LOCK"
else
    echo "  No lock file present"
fi

# 3. Deploy hardened script
echo "[3/6] Deploying hardened fleet-check script..."
cp "$SCRIPT_DIR/morris-fleet-check.sh" /opt/agent/morris-fleet-check.sh
chmod 755 /opt/agent/morris-fleet-check.sh
chown root:root /opt/agent/morris-fleet-check.sh
echo "  Deployed to /opt/agent/morris-fleet-check.sh"

# 4. Install system cron
echo "[4/6] Installing system cron..."
cp "$SCRIPT_DIR/morris-fleet-check.cron" /etc/cron.d/morris-fleet-check
chmod 644 /etc/cron.d/morris-fleet-check
chown root:root /etc/cron.d/morris-fleet-check
echo "  Installed /etc/cron.d/morris-fleet-check"

# 5. Remove from hermes user crontab (avoid duplicates)
echo "[5/6] Cleaning hermes user crontab..."
if sudo -u hermes crontab -l 2>/dev/null | grep -q "morris-fleet-check"; then
    sudo -u hermes crontab -l 2>/dev/null | grep -v "morris-fleet-check" | sudo -u hermes crontab -
    echo "  Removed fleet-check line from hermes crontab"
else
    echo "  No fleet-check line in hermes crontab (already clean)"
fi

# 6. Run the script once to verify
echo "[6/6] Running fleet-check manually..."
/opt/agent/morris-fleet-check.sh
RC=$?

echo ""
echo "=== Verification ==="
echo "Script exit code: $RC"

# Check log for recent successful run
if tail -5 "$LOG" 2>/dev/null | grep -q "fleet-check DONE"; then
    echo "✓ Log shows successful completion"
    tail -3 "$LOG"
else
    echo "✗ Log does not show completion — check $LOG"
    tail -10 "$LOG" 2>/dev/null
fi

# Check system cron is installed
if [ -f /etc/cron.d/morris-fleet-check ] && grep -q "morris-fleet-check" /etc/cron.d/morris-fleet-check; then
    echo "✓ System cron installed"
else
    echo "✗ System cron NOT installed"
fi

# Check no stale lock
if [ ! -f "$LOCK" ] || flock -n "$LOCK" true 2>/dev/null; then
    echo "✓ Lock is clean"
else
    echo "✗ Lock is still held — investigate"
fi

echo ""
echo "Done. Next automatic run should appear in log within 15 minutes."
