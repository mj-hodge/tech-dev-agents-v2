# Seed — STORY-323: Morris Fleet-Check Cron Not Firing

**Date:** 2026-04-16
**Scope:** Small
**Phase Path:** 1 → 7 → 8 → Done

---

## Problem

Morris's fleet-check cron (`/opt/agent/morris-fleet-check.sh`, scheduled `*/15 * * * *`) last ran 13 hours ago. It should fire every 15 minutes. The fleet has been unmonitored for ~13h — no health checks, no CRIT/WARN alerts to Mark.

## Root Cause Analysis

Two likely causes (both documented in `deployment/vm/FLEET-MAINTENANCE.md`):

### 1. Stale lock file (most likely)

The script uses `flock -n 200` on `/var/run/morris-fleet-check.lock` (line 28-29). If a previous run hung — e.g., an SSH probe to an unreachable agent or the Claude SDK call exceeding its timeout — the process holds the lock indefinitely. All subsequent cron ticks see the lock held and exit with "skip — previous still running".

**Why the existing timeout isn't sufficient:** Only the `claude -p` call (line 127) is wrapped in `timeout 180`. The SSH probes (lines 64-66), curl to ops-console (line 42), and Python data assembly (lines 103-116) have no outer timeout. A hung SSH connection (despite `ConnectTimeout=8`, TCP half-open states can persist longer) or a stalled curl would hold the lock forever.

### 2. Cron line lost during config reset

`FLEET-MAINTENANCE.md` documents that "Morris was missing the bottom 3 [crontab entries] until 2026-04-15". A config reset or crontab overwrite during the weekly patch (Sunday 06:00 UTC — ~37h ago) could have dropped the fleet-check line. The fleet-check cron (`*/15 * * * *`) is NOT in the standard 6-entry crontab listed in FLEET-MAINTENANCE.md — it's a Morris-only addition that may not survive `crontab -` style overwrites.

## Fix Plan

1. **Immediate:** Remove the stale lock file on Morris VM, verify/restore cron line
2. **Permanent:** Wrap the entire script body in a master `timeout` (e.g., 300s = 5min, well under the 15min cron interval) so no run can hold the lock past the next tick
3. **Belt-and-suspenders:** Add stale-lock detection at script start — if the lock file's mtime is >10 minutes old and no `morris-fleet-check.sh` process is alive, forcibly remove it before attempting flock
4. **Cron resilience:** Add the fleet-check cron line to `/etc/cron.d/morris-fleet-check` (system cron) instead of relying solely on hermes user crontab, which is vulnerable to overwrite

## Files to Change

| File | Change |
|------|--------|
| `deployment/vm/morris-fleet-check.sh` | Add master timeout wrapper + stale-lock detection |
| `deployment/vm/morris-fleet-check.cron` | New system cron file for `/etc/cron.d/` deployment |
| `deployment/vm/FLEET-MAINTENANCE.md` | Document the fleet-check cron entry + recovery procedure |
| `deployment/vm/fix-morris-fleet-check.sh` | One-shot remediation script to run on Morris VM |

## Verification

1. Run `fix-morris-fleet-check.sh` on Morris VM — removes stale lock, restores cron, runs the script once
2. Check `/var/log/morris-fleet-check.log` for successful completion
3. Wait 15 min, confirm second automatic run appears in log

## Risk

Low. The fleet-check script is read-only (collects data, writes state file, optionally sends Teams DM). No data mutation risk.
