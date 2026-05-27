#!/bin/bash
# Auto-restart hermes-gateway if no activity for 5 minutes
# Runs via cron every 2 minutes

LOG="/tmp/hermes-combined.log"
LAST_MODIFIED=$(stat -c %Y "$LOG" 2>/dev/null || echo 0)
NOW=$(date +%s)
STALE_SECONDS=300  # 5 minutes

DIFF=$((NOW - LAST_MODIFIED))

if [ "$DIFF" -gt "$STALE_SECONDS" ]; then
    echo "[watchdog] $(date) — No activity for ${DIFF}s. Restarting hermes-gateway." | systemd-cat -t hermes-watchdog -p warning
    killall -9 claude-real 2>/dev/null
    rm -f /home/hermes/.hermes/sessions/*.json /home/hermes/.hermes/sessions/*.jsonl
    systemctl restart hermes-gateway
    # Reset the log so we don't immediately trigger again
    truncate -s 0 "$LOG"
    systemctl restart hermes-log-forwarder
fi
