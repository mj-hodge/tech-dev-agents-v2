# STORY-768 — Daily Disk-Cleanup Cron on Agent VMs (Prevent Slow Disk-Fill Outages)

## Overview
| Field | Value |
|-------|-------|
| Mode | feature_update |
| Scope | small |
| Feature Name | Agent VM disk cleanup cron |
| Phase Path | 1 → 7 → 8 → Done |
| Repo | tech-dev-agents |
| Frontend | false |
| Related | STORY-303 (monthly kernel reboot window — orthogonal preventive maintenance), STORY-767 (fleet-vigilance disk usage check — observability for this) |

## Problem Statement

Agent VMs (`vm-dan-agent-dev`, `vm-derrick-agent-dev`, `vm-daisy-dev`, `vm-morris-agent-dev`) are running out of disk space over multi-day uptimes, causing `sshd` to crash and the VM to become unreachable from outside despite Azure showing PowerState=running.

**Concrete evidence captured 2026-04-30 09:30 UTC:**

| VM | Uptime | Disk used (29GB total) | Pattern |
|----|--------|------------------------|---------|
| derrick | 3d 15h | **21G / 73%** | Climbing — likely failure within 24h if uninterrupted |
| morris | 3d 14h | 15G / 51% | Mid-range |
| daisy (post-reboot) | 12h | 8.8G / 32% | Fresh, climbing |
| dan | (down — symptoms consistent with disk-full causing sshd to refuse connections) |

The VMs that crashed today (devon yesterday, daisy yesterday, dan today) all hit "connection refused" or "banner exchange timeout" — both consistent with `/var` or `/tmp` being full enough that `sshd` cannot allocate the per-connection scratch space.

**Sources of disk growth:**
- `~/.cache/uv/` and `~/.cache/pip/` — pytest + dependency installs accumulate
- `~/.cache/pytest_cache/` — pytest cache files per story
- `~/dev/hpi-gorillacommerce/<repo>/` — cloned story branches that never get pruned after merge
- `/var/log/journal/` — systemd journal (averaging ~250-1200 MB per VM today)
- `~/state/<agent>/failed-stories/*.txt` — failure flags accumulate
- Stale `/tmp/*` files from interrupted SDK runs
- `~/.claude/sessions/` (claude SDK session JSON dumps)

This story adds a **daily cron** on each agent VM that prunes safe-to-delete artifacts. NOT a full reboot; preserves running pollers and uptime.

## Target User / Use Case

**User:** every agent VM (dan, derrick, daisy, devon, morris) and Mark.
**Today:** disks fill silently; agents crash every 2-4 days.
**After this story:** a 03:00 UTC daily cron prunes the known-safe artifacts. Disk usage stays below 70% indefinitely. Crashes from disk-fill stop.

## Success Criteria

1. **SC-1 — New cleanup script `deployment/vm/scripts/agent-disk-cleanup.sh`** that prunes the 6 sources listed in the problem statement, with safe defaults (only removes things older than N days, only removes files NOT currently in use).
2. **SC-2 — Cron entry per agent VM**: `0 3 * * * /opt/agent/agent-disk-cleanup.sh >> /var/log/agent-cleanup.log 2>&1`. Runs as the agent user (e.g., hermes), not root.
3. **SC-3 — `journalctl --vacuum-time=7d`** is one of the cleanup steps. Sized to 7d so we still have history for incident response.
4. **SC-4 — Cloned repo branches:** prune any local branch in `~/dev/hpi-gorillacommerce/<repo>/` whose corresponding remote PR is in `MERGED` or `CLOSED` state. Does NOT touch active branches or main.
5. **SC-5 — Failure flags:** `~/state/<agent>/failed-stories/*.txt` older than 30 days deleted (gives Morris's classifier 30 days of trailing data).
6. **SC-6 — pytest + uv caches:** prune `~/.cache/pytest_cache/` and `~/.cache/uv/` entries older than 14 days. **Do NOT touch claude session caches** (those need long-term retention for resume).
7. **SC-7 — Dry-run flag.** Script supports `--dry-run` that prints what would be deleted without acting. Tests use this.
8. **SC-8 — Disk-watermark guard.** If disk usage is < 60%, cleanup is a no-op (avoids unnecessary I/O on healthy days). > 60% triggers full cleanup.
9. **SC-9 — Logging.** Cleanup logs total bytes freed per source to `/var/log/agent-cleanup.log` with rotation (logrotate config that keeps 14 days).
10. **SC-10 — Deploy via push-code.sh.** Script lives in repo, deploys with the rest of the agent code. Cron entry installed via `deployment/vm/install-disk-cleanup-cron.sh` invoked by push-code.sh.

## Verification Plan

| SC | Command | Expected |
|----|---------|----------|
| SC-1 | `bash deployment/vm/scripts/agent-disk-cleanup.sh --dry-run` (in test fixture VM-like dir) | Prints planned deletions, exits 0 |
| SC-3 | After cleanup, `sudo journalctl --disk-usage` < pre-cleanup size | Documented in PR body |
| SC-4 | Test fixture: create branch `story-001-foo` + simulate PR merge state via mock; run script; assert branch deleted | PASSED |
| SC-7 | `bash agent-disk-cleanup.sh --dry-run` does NOT delete anything (tested by file-stat mtime unchanged) | PASSED |
| SC-8 | When disk usage < 60% (mocked via `df` mock or actual disk fixture), script exits early with `[CLEANUP] disk at X% — no-op` | PASSED |
| SC-10 | `push-code.sh --wait 2 dan` deploys script + cron; SSH dan and verify cron entry | PASSED — manual proof in PR body |

## Test Criteria

- **Tests use a tmp_path fixture** mimicking ~/dev/hpi-gorillacommerce/ + ~/.cache/ structures.
- **Dry-run is the default** in tests; only one explicit "non-dry-run" test verifies actual deletion in tmp_path.
- **SSH/git commands mocked** for branch-prune logic.
- **No subprocess calls to actual journalctl** — tests target the pure-Python helper that wraps it; integration verified via manual step in Validation.

## Validation

| Step | Command | Pass criterion |
|------|---------|----------------|
| 1 | `pytest tests/deployment/test_agent_disk_cleanup.py -v` | All ≥ 6 tests GREEN |
| 2 | `pytest tests/ -x --ignore=tests/e2e -q` | Zero regressions |
| 3 | After deploy: SSH derrick (the 73%-disk VM), run `sudo /opt/agent/agent-disk-cleanup.sh`. Disk usage drops to < 65%. Verify via `df -h /` before/after. | Documented in PR body with both readings |
| 4 | `crontab -u hermes -l` on each agent shows the new entry | Documented |
| 5 | After 24h of running on the fleet, all VMs at < 70% disk | Manual follow-up after merge — 1-week observation logged in `state/morris/post-768-disk-watch.md` |

## Acceptance Criteria

- [ ] AC-1: `deployment/vm/scripts/agent-disk-cleanup.sh` exists. Bash script. Idempotent.
- [ ] AC-2: `deployment/vm/install-disk-cleanup-cron.sh` installs the cron entry per VM (invoked by push-code.sh).
- [ ] AC-3: Cleanup script supports `--dry-run` and `--force` (force runs cleanup regardless of watermark).
- [ ] AC-4: `journalctl --vacuum-time=7d` is included.
- [ ] AC-5: Branch-prune logic uses `gh pr view <branch>` to confirm MERGED/CLOSED before deleting; never deletes main, never deletes a branch with uncommitted changes.
- [ ] AC-6: pytest + uv caches pruned via `find ~/.cache/{pytest_cache,uv} -type f -atime +14 -delete`.
- [ ] AC-7: failed-story flags older than 30 days deleted.
- [ ] AC-8: 60% watermark guard implemented — `df -h /` checked, cleanup early-exits if below.
- [ ] AC-9: Log output to `/var/log/agent-cleanup.log` with bytes-freed-per-source tally.
- [ ] AC-10: logrotate config: 14-day retention.
- [ ] AC-11: Logging — when cleanup runs, log `[CLEANUP] freed <N> bytes from <source>` for each source, plus total. Visible in Loki.
- [ ] AC-12: Error/logging AC — if any cleanup step fails (e.g., permission denied, missing dir), log the error and CONTINUE with the next step. Do NOT abort the cron.

## Constraints
| Constraint | Value |
|------------|-------|
| Budget | small — bash script + cron entry + tests |
| Timeline | URGENT — derrick at 73% will crash within 24h without intervention |
| Tech | bash + standard Linux tools (find, du, journalctl, gh CLI) |

## Performance Requirements
- Cleanup script run time: < 60 seconds even on a full-disk VM.
- Daily cron impact: negligible — runs at 03:00 UTC during fleet idle window.

## Security Constraints
- [ ] Script runs as `hermes` user (or current agent user), NOT root. journalctl --vacuum needs sudo — script invokes via specific sudoers rule, not full sudo.
- [ ] Cleanup paths are HARDCODED (not configurable via env var) to prevent accidental `rm -rf /` if a config file is malformed.
- [ ] All `find ... -delete` calls are anchored to user-owned paths under `~/`.
- [ ] No exec of arbitrary user input.

## Operational Lifecycle
- **Configuration:** none — hardcoded paths, fixed thresholds.
- **How operators tune:** edit script + redeploy; no env vars.
- **Monitoring:** `/var/log/agent-cleanup.log` shows daily run; alert if log shows < 1MB freed for 7 consecutive days (might indicate stuck cleanup).

## Boundaries

| Always Do | Ask First | Never Do |
|-----------|-----------|----------|
| Use `--dry-run` semantics in tests | Whether to add a Slack/Teams DM on freed bytes (probably overkill — log file is enough) | Delete with `rm -rf` — always use `find -delete` with explicit -atime/-mtime |
| Hardcode paths under `~/` | Whether to extend to other paths (e.g., `/tmp/sdk-*`) | Delete claude session caches (need for resume) |
| 60% watermark for early-exit | Whether to use 70% instead | Use 50% — too aggressive, cleans up still-needed items |
| Skip if disk fine, log skip reason | Whether to also clean up `/tmp` (system tmp; probably yes if files older than 7d AND owned by hermes) | Touch `/var/log/journal` directly — use journalctl --vacuum-time |
| Run as hermes user with limited sudo | Whether to add a separate /etc/sudoers.d entry for journalctl | Run as root |

## Files to Modify

- `deployment/vm/scripts/agent-disk-cleanup.sh` — **new**, the cleanup script.
- `deployment/vm/scripts/install-disk-cleanup-cron.sh` — **new**, installs cron entry.
- `deployment/vm/push-code.sh` — invoke the install script during deploy (one-time install + idempotent).
- `tests/deployment/test_agent_disk_cleanup.py` — **new**, 6+ tests with tmp_path.
- `features/story-768-agent-disk-cleanup-cron/test-design.md` — Phase 7 deliverable.
- `.project`, `backlog.md` — tracking.

## Files to NOT Modify

- Any file under `~/.claude/sessions/` semantics — DO NOT prune.
- `agent-registry.json` — read-only.
- The dispatch DB / API — orthogonal.
- `/var/log/journal/` directly — use `journalctl --vacuum-time`.

## Done Looks Like

```
$ pytest tests/deployment/test_agent_disk_cleanup.py -v
test_dry_run_does_not_delete PASSED
test_below_watermark_skips_cleanup PASSED
test_above_watermark_runs_cleanup PASSED
test_branch_prune_only_for_merged_or_closed PASSED
test_failed_story_flag_30_day_threshold PASSED
test_pytest_cache_14_day_threshold PASSED
test_logs_bytes_freed_per_source PASSED
========== 7 passed in 0.41s ==========

# After deploy on derrick:
$ ssh derrick 'df -h / | tail -1'
/dev/root        29G   21G  7.8G  73% /
$ ssh derrick 'sudo /opt/agent/agent-disk-cleanup.sh'
[CLEANUP] disk at 73% — running full cleanup
[CLEANUP] freed 2147483648 bytes from journalctl
[CLEANUP] freed 1073741824 bytes from pytest_cache
[CLEANUP] freed 4294967296 bytes from merged-PR branches
[CLEANUP] freed 268435456 bytes from failed-story flags
[CLEANUP] total: 7783775744 bytes
$ ssh derrick 'df -h / | tail -1'
/dev/root        29G   14G   15G  48% /
```

## Escalation Contract

1. **Cleanup script can't determine if a branch's PR is MERGED/CLOSED** (gh CLI auth missing on a VM, network failure) — skip branch prune, log the failure, continue. Don't delete branches we can't classify.
2. **Disk still > 80% after full cleanup** — log CRIT and DM Mark via Teams. The cleanup isn't enough; manual intervention or disk resize needed.
3. **journalctl --vacuum fails** (permission, missing) — log + continue. Don't abort.
4. **Cron entry already exists** (from a prior install) — replace it idempotently. Don't error.
5. **Test wants to run actual disk cleanup against the host** — STOP, that's a test smell. Use tmp_path fixtures only.

**Default if no story-specific rule fires:** if more than 1 turn is spent guessing, write QUESTION.md and pause.

## Codebase Context

| Aspect | Details |
|--------|---------|
| Affected files | new cleanup script + new install script + push-code.sh extension |
| Current behavior | Disks fill silently over multi-day uptime; sshd crashes at near-100%; VM becomes unreachable. |
| Desired change | Daily 03:00 UTC cron prunes known-safe sources. 60% watermark guard prevents unnecessary I/O. |
| Test coverage | 7+ pure-Python/bash tests via tmp_path fixtures. |
| Architecture constraints | Bash + standard Linux tools. No new deps. Idempotent. |

## Out of Scope

- Resizing the OS disks themselves (separate ops task — Mark approved doing this in parallel as "B" of A+B plan).
- Migrating ~/dev to a separate data disk (further hardening, separate story).
- Real-time disk-pressure alerting (covered by STORY-767 fleet-vigilance Check 12-ish).
- Cleaning up sources we don't know about yet — start with the 6 documented; future stories add more.

## Notes for Implementer

- 2026-04-30 incident is the canonical evidence. Snapshot data above documents the per-VM disk usage at the time.
- Mark approved this story to ship in parallel with disk resize (A+B). The resize buys headroom; this script keeps it.
- STORY-303 (monthly kernel reboot window) is the longer-cycle preventive measure. This story is the daily-cycle one.
- Use `find -atime` (access time) not `-mtime` for caches (some tools update mtime even on read).
- AC-12's continue-on-failure is critical — partial cleanup is better than none.
