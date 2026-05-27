# Fleet Maintenance Runbook

How OS patches, Claude Code updates, and kernel reboots land on the agent VMs (Dan, Derrick, Morris). Authoritative reference for what's automated, what isn't, and how to intervene.

---

## TL;DR

| Cadence | What | Where | Auto |
|---|---|---|---|
| Every 6h | `claude -p ping` keepalive (refreshes OAuth token) | hermes user crontab | yes |
| Every 30min | Graph token refresh | hermes user crontab | yes |
| Every hour | Cost monitor (per-agent) | hermes user crontab | yes |
| Every hour | Cost anomaly check | hermes user crontab | yes |
| Every 10min | SDK health check | hermes user crontab | yes |
| Daily 06:00 UTC | SDLC framework pull | hermes user crontab | yes |
| Sunday 06:00 UTC | OS security patches + Claude Code update | `/etc/cron.d/weekly-patch` (root) | yes |
| First Sunday 07:00 UTC | Kernel reboot if needed (after STORY-303 ships) | `/etc/cron.d/monthly-reboot` (root) | planned |

**Today's posture (2026-04-15):** weekly patching is live on all 3 agent VMs. Monthly reboot is spec'd in STORY-303, dispatched but not yet shipped — until then, kernel patches accumulate without taking effect; reboot manually when needed.

---

## Weekly patching (live since 2026-04-15)

### What runs

`/opt/agent/weekly-patch.sh` — repo source: `deployment/vm/weekly-patch.sh`.

Steps in order:
1. `apt-get update` + `apt-get upgrade -y` (security patches; holds local config with `--force-confold`)
2. `apt-get autoremove -y`
3. `npm i -g @anthropic-ai/claude-code` (preserves `~/.claude` auth)
4. Verifies `claude auth status` returns `loggedIn: true`
5. Verifies `claude -p ping` returns `pong` (proves OAuth token still works)
6. Logs kernel-change alert if reboot needed (does NOT auto-reboot — would lose mid-flight SDK work)
7. Exits non-zero if anything important failed

Logs to: `/var/log/weekly-patch.log` on each VM.

### Schedule

`/etc/cron.d/weekly-patch`:
```
0 6 * * 0 root /opt/agent/weekly-patch.sh
```
Sunday 06:00 UTC. Chosen as low-traffic — Mark's typical work windows are M–F.

### Why each VM patches itself (instead of central orchestration)

- No central Azure auth on the ops-console (no shared secret to leak)
- Failure on one VM doesn't block patching others
- Each VM owns its own patch cadence — easier to debug locally
- Aligns with how Morris's deploy log evolved: each VM's setup is self-contained

### Manual patch run

```bash
ssh -p 443 azureagent@<vm-ip>
sudo /opt/agent/weekly-patch.sh
sudo tail -20 /var/log/weekly-patch.log
```

Or fleet-wide from the admin machine:

```bash
for ip in 20.228.224.243 20.121.210.186 20.246.36.143; do   # dan / derrick / morris
  echo "=== $ip ==="
  ssh -p 443 azureagent@$ip "sudo /opt/agent/weekly-patch.sh && sudo tail -10 /var/log/weekly-patch.log"
done
```

### When patching fails

The script exits non-zero on three conditions:
1. `apt-get upgrade` failed (network / repo signature / disk full)
2. `npm i -g @anthropic-ai/claude-code` failed (npm registry down / disk)
3. `claude -p ping` did not return "pong" (auth broken — re-login required)

For (3) specifically: see "Claude auth recovery" below.

### Claude auth recovery

If `claude -p ping` stops returning "pong" (auth expired or revoked):

```bash
ssh -p 443 azureagent@<vm-ip>
sudo -u hermes -i claude auth login --email tech-agent-<name>@gorillacommerce.co
# follow the device-code flow on Mark's machine
```

The OAuth token persists across container/VM restarts in `/home/hermes/.claude/.credentials.json`. The keepalive cron (every 6h) is what prevents it from going stale — that's why **Morris's setup missing this cron caused his auth to die silently within 24h**.

---

## Monthly kernel reboot (spec'd, not yet shipped — STORY-303)

When weekly-patch lands a kernel update, the new kernel only takes effect after reboot. We need a planned monthly window.

### Design (per `features/story-303-monthly-kernel-reboot-window/seed.md`)

- Cron: `0 7 1-7 * 0` (first Sunday of each month, 07:00 UTC — 1h after weekly-patch finishes)
- Per-VM script `/opt/agent/monthly-reboot.sh`:
  - Checks `/var/run/reboot-required` (set by apt on kernel change). Skip if absent.
  - Acquires fleet-wide lock via `POST /api/maintenance/reboot-lock` (15min TTL — auto-released so a crashed reboot doesn't leak)
  - Sets `/var/run/agent-draining` flag — dispatch_poller checks this and stops claiming
  - Waits up to 30 min for any running `claude_sdk_tool.py` to finish
  - Sends Teams DM to Mark: "Dan rebooting in 10 min for kernel update"
  - `systemctl reboot`
- On boot, post-reboot service:
  - Waits for hermes-gateway + dispatch-poller + claude auth
  - Sends Teams DM: "Dan back online, kernel=6.17.0-1011-azure"
  - Releases the maintenance lock
  - Removes `/var/run/agent-draining`

### Sequence

Dan → Derrick → Morris (last). Manager reboots last because losing him 2-3 min mid-cycle is less harmful than losing a dev agent. Lock ensures only one VM reboots at a time.

### Until STORY-303 ships — manual kernel reboot

When weekly-patch logs `ALERT: kernel changed`:

1. Pick a quiet window (no agents mid-work)
2. Drain manually:
   ```bash
   # Stop dispatch poller so no new claims
   ssh -p 443 azureagent@<vm-ip> "sudo systemctl stop dispatch-poller"
   # Wait for any claude_sdk_tool.py to finish (or kill if past expectation)
   ssh -p 443 azureagent@<vm-ip> "ps aux | grep '[c]laude_sdk_tool.py' || echo idle"
   ```
3. Reboot:
   ```bash
   ssh -p 443 azureagent@<vm-ip> "sudo systemctl reboot"
   ```
4. Wait ~3 min, then verify:
   ```bash
   ssh -p 443 azureagent@<vm-ip> "uname -r; sudo systemctl is-active hermes-gateway dispatch-poller; sudo -u hermes claude -p ping --max-turns 1"
   ```
5. Repeat for next VM (do NOT do all three at once — fleet would be fully offline)

### Detecting reboot-required across the fleet

```bash
for ip in 20.228.224.243 20.121.210.186 20.246.36.143; do
  echo -n "$ip: "
  ssh -p 443 azureagent@$ip "test -f /var/run/reboot-required && echo REBOOT_NEEDED || echo current"
done
```

Add this to a Morris weekly skill if you want him to surface it in his ops report.

---

## Cron summary per VM

Run this to verify any VM has the full set:

```bash
ssh -p 443 azureagent@<vm-ip> '
echo "--- hermes user ---"; sudo -u hermes crontab -l
echo "--- system ---";      sudo ls /etc/cron.d/
'
```

Expected hermes crontab (6 entries — Morris was missing the bottom 3 until 2026-04-15):
```
*/30 * * * * sudo /opt/agent/refresh_graph_token.sh >> /tmp/hermes-combined.log 2>&1
0 * * * *    AGENT_NAME=<name> /opt/agent/cost_monitor.sh >> /tmp/hermes-combined.log 2>&1
0 */6 * * *  claude -p ping --max-turns 1 > /dev/null 2>&1
0 6 * * *    /opt/agent/sdlc-pull-cron.sh
0 * * * *    /opt/agent/cost_anomaly_check.sh
*/10 * * * * /opt/agent/sdk_health_check.sh
```

Expected `/etc/cron.d/`:
- `weekly-patch` — `0 6 * * 0 root /opt/agent/weekly-patch.sh`
- `morris-fleet-check` — (Morris only) `*/15 * * * * hermes /opt/agent/morris-fleet-check.sh`
- `monthly-reboot` — (after STORY-303 ships) `0 7 1-7 * 0 root /opt/agent/monthly-reboot.sh`

> **STORY-323 lesson:** The fleet-check cron was originally in hermes's user crontab. It got lost during a config reset. Moved to `/etc/cron.d/` so it survives `crontab -` overwrites. If the fleet-check stops firing, check: (1) `/etc/cron.d/morris-fleet-check` exists, (2) `/var/run/morris-fleet-check.lock` isn't stale, (3) run `sudo /opt/agent/morris-fleet-check.sh` manually and check `/var/log/morris-fleet-check.log`.

---

## When something goes wrong — first checks

| Symptom | Likely cause | Where to look |
|---|---|---|
| Agent stops responding to Teams | Claude auth expired | `sudo -u hermes claude auth status` → if `loggedIn: false`, missing keepalive cron |
| Agent shows $0 in cost dashboard | Missing per-agent cost_monitor cron | `sudo -u hermes crontab -l \| grep cost_monitor` |
| Weekly-patch ran but kernel didn't update | Kernel patch needs reboot | `cat /var/run/reboot-required && uname -r` — manual reboot |
| Weekly-patch didn't run at all | System cron not installed | `sudo cat /etc/cron.d/weekly-patch` — should exist |
| Patches install but Claude Code stays old | npm install failed | `sudo tail -50 /var/log/weekly-patch.log` — check `npm exit=` line |
| Dispatch poller claims but never works | SDK quota exhausted (Claude Code Team plan) | logs show `You've hit your limit · resets <time>` — wait for reset |
| Fleet-check stops firing (Morris) | Stale lock or cron lost in config reset | `ls -la /var/run/morris-fleet-check.lock; cat /etc/cron.d/morris-fleet-check` — run `fix-morris-fleet-check.sh` |

---

## File ownership

| File | Owned by | Purpose |
|---|---|---|
| `/opt/agent/weekly-patch.sh` | root:root 755 | Weekly OS + Claude Code patcher |
| `/opt/agent/monthly-reboot.sh` (after STORY-303) | root:root 755 | Monthly reboot orchestrator |
| `/etc/cron.d/weekly-patch` | root:root 644 | Weekly cron entry |
| `/etc/cron.d/monthly-reboot` (after STORY-303) | root:root 644 | Monthly cron entry |
| `/var/log/weekly-patch.log` | root:root 644 | Weekly run log (rotated by logrotate default) |
| `/opt/agent/morris-fleet-check.sh` (Morris only) | root:root 755 | Fleet health monitor (runs every 15min) |
| `/etc/cron.d/morris-fleet-check` (Morris only) | root:root 644 | Fleet-check cron entry |
| `/var/run/morris-fleet-check.lock` (Morris only) | hermes 644 | Fleet-check flock file (auto-created) |
| `/var/log/morris-fleet-check.log` (Morris only) | hermes 644 | Fleet-check run log |
| `/home/hermes/.claude/.credentials.json` | hermes:hermes 600 | Claude OAuth token (do NOT touch directly — use `claude auth login`) |

---

## Updating this runbook

When something changes about how patching/reboots work — update this doc in the same commit. Don't let the doc drift from the live system. The whole reason Morris's keepalive cron got missed is that the runbook checkbox for it was buried in a wall of other checkboxes; this is the more discoverable home for "how the fleet stays patched."

---

## STORY-920: claude_sdk_tool.py Rollback

New `claude_sdk_tool.py` uses direct `anthropic` Python SDK (not `claude_agent_sdk` subprocess wrapper).
If the new code causes `[ERROR]` spikes or silent failures on Dan during canary, roll back before fleet-wide push.

### Quick rollback (single VM)

```bash
ssh -p 443 azureagent@<vm-ip> "
  grep -q USE_LEGACY_CLAUDE_SDK_TOOL /etc/systemd/system/dispatch-poller.service \
    || sudo bash -c 'echo Environment=USE_LEGACY_CLAUDE_SDK_TOOL=1 >> /etc/systemd/system/dispatch-poller.service'
  sudo systemctl daemon-reload
  sudo systemctl restart dispatch-poller
"
```

### Verify rollback active

```bash
ssh -p 443 azureagent@<vm-ip> "systemctl cat dispatch-poller | grep USE_LEGACY"
# Should output: Environment=USE_LEGACY_CLAUDE_SDK_TOOL=1
```

### Fleet-wide rollback

```bash
ROLLBACK=1 ./deployment/vm/push-code.sh all
```

### Verify new SDK is active (no rollback)

```bash
# Check [USAGE_V2] lines in Loki (or locally on VM):
ssh -p 443 azureagent@<vm-ip> "grep USAGE_V2 /tmp/claude-sdlc-logs/session-*.log | tail -5"
```

### Legacy file location

`/opt/agent/claude_sdk_tool_legacy.py` — retained for ≥30 days (until follow-up cleanup story).
Do NOT delete before Mark approves the follow-up cleanup story.
