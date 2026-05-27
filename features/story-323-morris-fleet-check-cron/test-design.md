# Test Design — STORY-323: Morris Fleet-Check Cron Fix

**Date:** 2026-04-16
**Scope:** Small
**Phase:** 7 (Test Design)

---

## Test Strategy

This is an infrastructure/ops fix targeting a shell script on a remote VM. Tests are verification procedures, not unit tests — the artifact is a shell script, not application code.

## Test Cases

### T1: Stale lock detection works

**Given:** `/var/run/morris-fleet-check.lock` exists, is >10 min old, and no `morris-fleet-check.sh` process is running
**When:** `morris-fleet-check.sh` is invoked
**Then:** The script removes the stale lock, acquires a fresh lock, and runs to completion
**Verify:**
```bash
# Create a stale lock (simulate)
sudo touch -t 202604160000 /var/run/morris-fleet-check.lock
sudo /opt/agent/morris-fleet-check.sh
tail -5 /var/log/morris-fleet-check.log | grep -q "fleet-check START"
echo "T1: PASS=$?"
```

### T2: Master timeout prevents indefinite lock hold

**Given:** The script is running and one of its subprocesses hangs
**When:** 300 seconds (master timeout) elapses
**Then:** The script is killed, the lock file descriptor is closed, the lock is released
**Verify:** Confirmed by code review — `timeout 300` wraps the main function, ensuring process termination releases flock.

### T3: Cron line is present in system cron

**Given:** `/etc/cron.d/morris-fleet-check` is deployed
**When:** `cat /etc/cron.d/morris-fleet-check`
**Then:** Contains `*/15 * * * * hermes /opt/agent/morris-fleet-check.sh`
**Verify:**
```bash
grep -q '*/15.*morris-fleet-check' /etc/cron.d/morris-fleet-check
echo "T3: PASS=$?"
```

### T4: Script runs successfully end-to-end

**Given:** Lock is clean, cron is installed, network is available
**When:** `sudo /opt/agent/morris-fleet-check.sh` is run manually
**Then:** Log shows `fleet-check START` followed by `fleet-check DONE (rc=0)` within 5 minutes
**Verify:**
```bash
sudo /opt/agent/morris-fleet-check.sh
tail -5 /var/log/morris-fleet-check.log | grep -q "fleet-check DONE (rc=0)"
echo "T4: PASS=$?"
```

### T5: Cron fires automatically within 15 minutes

**Given:** T3 and T4 pass
**When:** Wait 15 minutes
**Then:** A new `fleet-check START` entry appears in `/var/log/morris-fleet-check.log` with a timestamp within the last 15 minutes
**Verify:**
```bash
# Check most recent entry is within last 20 minutes
LAST=$(tail -20 /var/log/morris-fleet-check.log | grep "fleet-check START" | tail -1 | grep -oP '^\S+')
echo "Last run: $LAST — verify it's recent"
```

### T6: Fix script is idempotent

**Given:** `fix-morris-fleet-check.sh` has already been run once successfully
**When:** It is run a second time
**Then:** No errors, no duplicate cron entries, script completes cleanly
**Verify:**
```bash
sudo bash fix-morris-fleet-check.sh
sudo bash fix-morris-fleet-check.sh  # second run
sudo -u hermes crontab -l | grep -c fleet-check  # should be 0 or 1 (user crontab, not system)
cat /etc/cron.d/morris-fleet-check | wc -l  # should be consistent
echo "T6: PASS (no errors, no duplicates)"
```

## Automated Verification Script

The fix script (`fix-morris-fleet-check.sh`) includes a built-in verification section that runs T1, T3, and T4 automatically and reports results. T5 requires waiting 15 minutes and must be verified manually after deployment.
