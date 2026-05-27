# STORY-512: push-code.sh safety — don't restart pollers mid-SDK

> Phase 1 | Scope: **Small** | Created: 2026-04-21
> Advance: auto (automated dispatch path — 1 → 7 → 8+PR → Done)

---

## Problem Statement

`deployment/vm/push-code.sh` unconditionally restarts the `dispatch-poller` systemd unit after copying new files. When an agent is mid-SDK session, that restart sends SIGTERM to the Python process which kills the running `claude_sdk_tool.py` subprocess, losing in-progress work.

Today this happened twice: my 17:44Z timeout-fix deploy killed Daisy mid-STORY-507, and the 23:50Z poller-fix deploy killed her mid-STORY-508. STORY-507's SIGTERM handler (AC-4) saves partial state, and its resume mode (AC-1/2) recovers on re-claim — so no work was actually lost either time. But each interruption still:
- Burns ~5 minutes on re-claim + re-exploration
- Triggers Morris's kill-detection (before today's HANDS-OFF rules it would have auto-cancelled)
- Inflates apparent dispatch failure rate in metrics

Easy fix: check if any `claude_sdk_tool.py` process is running on the agent VM before calling `systemctl restart dispatch-poller`. If yes, either wait for it to finish, or skip the restart and warn the operator. Deploy becomes opt-in for force-restart.

---

## Target User

- **Deploy operators** (Mark, Morris via automation, anyone running `push-code.sh`)
- **Active agents** — Daisy/Devon/Dan/Derrick whose work shouldn't be interrupted
- **Fleet metrics** — cleaner failure rate stats when interruptions drop

---

## Acceptance Criteria

1. **AC-1** — Before calling `systemctl restart dispatch-poller` on an agent VM, `push-code.sh` must check for a running SDK via `pgrep -f claude_sdk_tool.py` (or equivalent) on that VM. Exit code 0 = SDK running.

2. **AC-2** — Default behavior when an SDK is running: **skip the restart**, emit a warning `[<agent>] SDK active (PID <N>, story <X>) — files copied, restart deferred`, continue with the next agent. Files are still updated (so the next natural restart picks up the new code).

3. **AC-3** — New flag `--force` (or `FORCE_RESTART=1` env) overrides AC-2: restarts even when SDK is running. Useful for emergency hotfixes. Documented in the script header and `README.md`.

4. **AC-4** — New flag `--wait` (or `WAIT_FOR_IDLE=N` env, default N=30 min) waits up to N minutes for the SDK to finish before restarting, polling every 60s. Emits progress (`[<agent>] waiting for SDK to finish (story <X>, NNs elapsed)`). If timeout, skip restart with warning.

5. **AC-5** — Exit status reflects the new behavior:
   - `0` — all target VMs deployed + restarted cleanly
   - `0` with warnings — some VMs deferred restart due to active SDK
   - `1` — actual failures (scp error, systemctl stop error with `--force`)
   - `2` — `--wait` timeout on one or more VMs

6. **AC-6** — README/usage section updated with the three modes: default-safe, `--force`, `--wait [minutes]`.

7. **AC-7** — Integration test (bash-testable): mock agent VM with a fake `claude_sdk_tool.py` process running. Verify default skips restart, `--force` restarts anyway, `--wait 0.1` times out cleanly.

---

## Scope Classification

**Small** — changes confined to one bash script + README update + one test. No DB changes, no ops-console changes, no frontend.

Phase path: **1 → 7 → 8+PR → Done**.

---

## Technical Notes

### The check
Inside `deploy_one()` before the restart block:
```bash
local sdk_running
sdk_running=$(ssh $SSH_OPTS "azureagent@$ip" "pgrep -f claude_sdk_tool.py | head -1" 2>/dev/null)
if [ -n "$sdk_running" ] && [ "${FORCE_RESTART:-0}" != "1" ]; then
    # Read the story it's working on for the log line
    local story
    story=$(ssh $SSH_OPTS "azureagent@$ip" "ps -p $sdk_running -o args= 2>/dev/null | grep -oE 'STORY-[0-9]+' | head -1" 2>/dev/null)
    echo "[$name] SDK active (PID $sdk_running, story ${story:-unknown}) — files copied, restart deferred"
    echo "[$name] Use --force to override; next poller tick picks up the new code naturally"
    echo "[$name] DONE (files only, no restart)"
    return 0
fi
```

### Wait mode implementation
```bash
if [ -n "${WAIT_FOR_IDLE:-}" ]; then
    local deadline=$(( $(date +%s) + WAIT_FOR_IDLE * 60 ))
    while [ -n "$sdk_running" ] && [ $(date +%s) -lt $deadline ]; do
        sleep 60
        local elapsed=$(( WAIT_FOR_IDLE * 60 - (deadline - $(date +%s)) ))
        echo "[$name] waiting for SDK (story ${story:-unknown}, ${elapsed}s elapsed)"
        sdk_running=$(ssh $SSH_OPTS "azureagent@$ip" "pgrep -f claude_sdk_tool.py | head -1" 2>/dev/null)
    done
    # After loop: either SDK finished or deadline hit
fi
```

### Why SIGTERM handler (STORY-507 AC-4) isn't a full substitute

STORY-507 AC-4 commits and pushes partial work on SIGTERM. That's the safety net — without it, a restart loses everything. But the resume still has real cost:
- Phase has to re-exec (5+ min of re-reading files)
- SDK tokens burned on re-exploration
- Visible to Mark as a "story got killed and restarted" event even when no harm done

AC-4 handles the *failure* case. This story handles the *avoidance* case.

### Edge cases to test

- **SDK running on one VM but not another** — verify selective restart (defer one, restart the other)
- **SDK process dies between the check and the restart** — benign race; restart happens, no harm
- **`--wait` interacts with `--force`** — `--force` wins; warn and restart immediately
- **Rate-limited agent** (poller disabled) — `pgrep claude_sdk_tool.py` returns empty, normal "NOT restarting" path still works

---

## Out of Scope

- Cancelling the running SDK cleanly from `push-code.sh` (that would need a new `/api/dispatch/pause` client call)
- Per-phase deploy granularity (only deploy to agents in certain phases) — overcomplicated for the current fleet size
- Blue-green deploys / canary (different problem entirely)

---

## Success Measure

After this ships, the number of "SDK killed by deploy" events in the Loki logs drops to zero for default invocations. Mark can deploy confidently during active fleet work without needing to check manually first.
