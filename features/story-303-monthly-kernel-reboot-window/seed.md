# Seed: STORY-303 — Monthly Kernel Reboot Maintenance Window

**Story:** STORY-303
**Date:** 2026-04-15
**Scope:** Small
**Phase Path:** 1 → 7 → 8 → Done
**Branch:** `story-303/monthly-reboot-window`

---

## Problem Statement

The weekly patch cron (`/opt/agent/weekly-patch.sh`, shipped in `9edc603`) installs OS security updates including kernel patches. When a kernel patch lands, the running kernel stays in memory until reboot — the new kernel only takes effect after a planned restart. Today the script **detects** kernel changes (logs an alert) but explicitly does NOT reboot, because an unscheduled reboot mid-SDK-session would lose work.

Result: kernels drift further behind every week. Eventually we accumulate critical CVEs the running kernel is still vulnerable to.

We need a **planned monthly reboot window** that:
1. Is announced in advance so Mark + agents can plan around it
2. Stops new dispatch claims before reboot (drain instead of yank)
3. Waits for in-flight SDK sessions to finish (with a hard cap)
4. Reboots one VM at a time so the fleet is never fully down
5. Verifies each VM comes back healthy before moving to the next

## Goals

1. **Zero unscheduled reboots.** Reboots only happen during a published window.
2. **Zero lost SDK work.** Drain → wait → reboot, never yank.
3. **Fleet always has ≥1 dev agent online.** Reboot Dan, then Derrick, then Morris (manager last) — sequenced, not parallel.
4. **Visibility.** Mark gets a Teams notification before, during, and after each reboot.

## Non-goals

- Don't auto-reboot on every kernel patch (defeats the purpose of "planned")
- Don't try to live-migrate / VM hot-patch — Azure can do livepatching for an extra cost; not in scope here
- Don't reboot the ops-console VM in the same window — it's the orchestrator and needs its own window

## Acceptance Criteria

| ID | Criterion | Measurable |
|---|---|---|
| AC-1 | A monthly cron on each agent VM checks `/var/run/reboot-required` (set by apt when kernel changes) | `cat /etc/cron.d/monthly-reboot` shows the entry |
| AC-2 | When reboot is required, the script waits up to 30 min for any running `[c]laude_sdk_tool.py` process to exit | Logged as `WAITING for SDK to drain (pid=NNN)` then `DRAINED` |
| AC-3 | Before reboot, the script sets a "draining" flag in `/var/run/agent-draining` so dispatch_poller stops claiming | `dispatch_poller.py` checks for this flag and skips poll if present |
| AC-4 | Reboots are sequenced across the fleet via a shared lock — only one agent reboots at a time | A POST to `/api/maintenance/reboot-lock` returns 200 (acquired) or 409 (held by another agent). Lock auto-releases after 15 min. |
| AC-5 | Mark receives a Teams DM before each reboot ("Dan rebooting in 10 min for kernel update") and after ("Dan back online, claude=2.1.109, kernel=6.17.0-1011-azure") | Teams notifications sent via the existing send_message MCP path |
| AC-6 | If reboot fails or VM doesn't come back within 5 min, an alert fires to Teams | Failure path tested |
| AC-7 | Planned schedule: first Sunday of each month at 07:00 UTC (1h after weekly-patch finishes) | Cron entry `0 7 1-7 * 0` |

## Technical Plan

### 1. New script: `deployment/vm/monthly-reboot.sh` (deployed to `/opt/agent/`)

```bash
#!/usr/bin/env bash
set -u
LOG=/var/log/monthly-reboot.log

# Skip if no reboot needed
if [ ! -f /var/run/reboot-required ]; then
    echo "$(date -u) no reboot needed, exiting" >> "$LOG"
    exit 0
fi

# Acquire fleet-wide lock from ops-console (sequenced reboots)
AGENT_NAME=$(hostname | sed 's/vm-//' | sed 's/-agent-dev//')
LOCK=$(curl -s -X POST -H "X-API-Key: $(grep ^OPS_CONSOLE_API_KEY /opt/agent/.env | cut -d= -f2)" \
    -d "{\"agent\":\"$AGENT_NAME\"}" \
    https://tech-dev-agents.gorillacommerce.ai/api/maintenance/reboot-lock)
if echo "$LOCK" | grep -q '"acquired":false'; then
    echo "$(date -u) lock held by another agent — abort, will retry next month" >> "$LOG"
    exit 0
fi

# Set draining flag (poller checks this)
sudo touch /var/run/agent-draining

# Notify Mark
curl ... send_message ... "Rebooting $AGENT_NAME in 10 min for kernel update"

# Wait for SDK drain (max 30 min)
for i in $(seq 1 60); do
    if ! pgrep -f claude_sdk_tool.py > /dev/null; then break; fi
    sleep 30
done

# Reboot
sudo systemctl reboot
```

### 2. Poller change (`dispatch_poller.py`)

Add a check at the top of the poll loop:

```python
if os.path.exists("/var/run/agent-draining"):
    print("[DISPATCH] DRAINING for maintenance — skipping poll")
    return
```

### 3. Ops-console: `/api/maintenance/reboot-lock` endpoint

- POST acquires the lock if not held; returns `{"acquired": true, "expires_at": ...}` or `{"acquired": false, "held_by": "...", "expires_at": ...}`
- Lock auto-expires after 15 min (TTL — even if a VM crashes mid-reboot, the lock releases)
- DELETE releases the lock (called after the VM comes back healthy)
- Stored in postgres in a single-row table `maintenance_lock(holder, expires_at)`

### 4. Post-reboot verification (`/etc/systemd/system/post-reboot-check.service`)

- Triggers on boot
- Waits for hermes-gateway + dispatch-poller + claude auth
- Posts "Dan back online" Teams message
- Releases the maintenance lock
- Removes `/var/run/agent-draining`

### 5. Tests

- `tests/deployment/test_monthly_reboot.py`: drain wait logic, lock acquisition, no-reboot-needed exit path
- `tests/ops_console/test_routes_maintenance.py`: lock acquire/release/expire

## Rollout

1. Deploy `monthly-reboot.sh` to all agent VMs (idempotent — just script + cron, no behavior until first Sunday of the month)
2. Deploy poller change with the `/var/run/agent-draining` check
3. Deploy ops-console with `/api/maintenance/reboot-lock` endpoint
4. Test with a forced kernel-required state on Morris (lowest-risk VM): `sudo touch /var/run/reboot-required && sudo /opt/agent/monthly-reboot.sh`
5. Watch the sequence end-to-end; verify Teams notifications, drain wait, reboot, post-reboot health check
6. Then let the natural monthly cadence take over

## Constraints

- DO NOT reboot the ops-console VM through this — it owns the lock; rebooting it would leak the lock until TTL
- Manager VM (Morris) reboots LAST in the sequence — losing him for 2-3 min mid-cycle is less harmful than losing a dev agent
- The lock TTL of 15 min is calibrated for typical Azure VM reboot times (2-4 min). Tune if reboots take longer.

## Out of Scope

- Live kernel patching (kpatch / Azure Hot Patching) — separate cost+complexity tradeoff
- ops-console VM reboots — separate story (it owns the lock, needs its own window)
- Postgres reboots — handled by Azure auto-patching of the managed instance (not on the VM)

## Success Criteria

- Kernel version on each VM matches the latest available within 30 days
- Zero SDK sessions interrupted by reboots in the past 90 days
- Mark gets a clean Teams update each month: "Fleet patched, kernel 6.17.0-1012-azure"

## Related runbook

When this story ships, the "Monthly kernel reboot" section in [`deployment/vm/FLEET-MAINTENANCE.md`](../../deployment/vm/FLEET-MAINTENANCE.md#monthly-kernel-reboot-spcd-not-yet-shipped--story-303) needs to be updated from "spec'd, not yet shipped" to live operational behavior.

## Version

0.1.0
