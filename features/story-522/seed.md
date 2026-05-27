# STORY-522: Investigate and Fix Recurring SIGTERM (rc=-15) on SDK Phase Sessions

## Problem Statement

SDK phase sessions on the dispatch fleet are being killed by external SIGTERM (signal 15) at 60-120 seconds into execution. The raw exit code is **rc=-15** (not rc=143), which proves the signal is not caught by the phase runner's `_graceful_shutdown` handler — the SDK child process receives SIGTERM directly and dies without saving partial work.

### Evidence (2026-04-22)

| Agent | Story | Phase | rc | Duration |
|-------|-------|-------|----|----------|
| Devon | STORY-517 | 7 | -15 | 56s |
| Devon | STORY-517 | 7 (retry) | -15 | 72s, 74s |
| Devon | STORY-495 | 4 | -15 | 109s |
| Daisy | STORY-515 | 6 (retry 1) | -15 | 81s |
| Daisy | STORY-521 | 7 | -15 | 171s |

After a `--force` restart of the poller (push-code.sh --force), the pattern stopped reproducing.

### Why rc=-15 and not rc=143

- **rc=143** (128+15) = process caught SIGTERM via handler, committed partial work, exited cleanly
- **rc=-15** = process killed by raw signal 15 with no handler; `subprocess.run()` reports negative return code

This distinction is critical: the `_graceful_shutdown` handler in `sdlc_phase_runner.py` (line 167) would exit 143 and save partial work. The SDK child process is dying WITHOUT that handler ever running.

---

## Root Cause Analysis

### Primary Cause: SIGTERM handler never registers (daemon thread limitation)

**File:** `deployment/hermes/sdlc_phase_runner.py` lines 879-892

The `install_shutdown_handler()` function calls `signal.signal(signal.SIGTERM, _graceful_shutdown)`. However, Python requires signal handlers to be set from the **main thread only**. The call chain is:

```
run_dispatch_poller.py (main thread)
  -> poll_loop() (main thread)
    -> poll_once() (main thread)
      -> start_story() (main thread)
        -> threading.Thread(target=_run_and_complete, daemon=True).start()  [line 878]
          -> run_sdlc_phases()  [daemon thread]
            -> install_shutdown_handler()  [daemon thread - FAILS SILENTLY]
              -> signal.signal(signal.SIGTERM, ...)
                -> ValueError: signal only works in main thread
                -> except ValueError: pass  [SILENTLY SWALLOWED at line 890-892]
```

**Impact:** The SIGTERM handler is **never registered** in production. The `_graceful_shutdown` function exists but never fires. When SIGTERM arrives:
1. The main thread receives default Python SIGTERM handling (raises SystemExit)
2. The SDK child process in the daemon thread receives SIGTERM from systemd's cgroup kill
3. SDK child exits with signal 15 -> parent's `subprocess.run()` sees rc=-15
4. No partial work is committed, no pause API is called

### Secondary Cause: systemd cgroup kills all children on service restart

**File:** `deployment/vm/systemd/dispatch-poller.service`

The systemd unit uses default `KillMode=control-group` (the default when not specified). When `systemctl restart dispatch-poller` fires:

1. systemd sends SIGTERM to **all processes** in the service's cgroup
2. This includes the main `run_dispatch_poller.py` AND any child processes (`claude_sdk_tool.py`, `claude` CLI, etc.)
3. Every running SDK subprocess receives SIGTERM directly from systemd — not from the parent

### Trigger: What causes the restart?

Multiple pathways can trigger `systemctl restart dispatch-poller`:

1. **`push-code.sh` deploys** (line 181): `ssh ... "sudo systemctl restart dispatch-poller"` — documented in push-code.sh post-mortem (lines 48-53) as having killed Daisy mid-story on 2026-04-21
2. **`push-code.sh` nohup fallback** (line 190): `pkill -9 -f run_dispatch_poller` — even more aggressive (SIGKILL)
3. **systemd `Restart=on-failure`** (RestartSec=30): If the main process crashes for any reason, systemd restarts it automatically
4. **`hermes-watchdog.sh`** (cron every 2 min): Kills `claude-real` with SIGKILL if hermes-gateway is stale for 5 min — doesn't restart dispatch-poller directly, but `killall -9 claude-real` could kill the Claude Code process underneath the SDK

### Why the pattern stopped after --force restart

The `push-code.sh --force` deployed updated code AND restarted the service. After the new code was running, no further restarts were triggered (the old code may have had a crash bug that caused repeated `Restart=on-failure` cycles). Each restart would kill the SDK child at whatever point it had reached (60-120s).

---

## Files to Inspect

### Signal paths (where SIGTERM originates or propagates)

| File | Lines | What to check |
|------|-------|---------------|
| `deployment/hermes/sdlc_phase_runner.py` | 167-223, 879-892 | `_graceful_shutdown` handler; `install_shutdown_handler` fails silently in daemon thread |
| `deployment/hermes/sdlc_phase_runner.py` | 552-731 | `_run_phase_sdk` — subprocess.run() that reports rc=-15 |
| `deployment/hermes/sdlc_phase_runner.py` | 453-530 | `_save_partial_work` — only runs if handler fires (it doesn't) |
| `deployment/hermes/dispatch_poller.py` | 543-878 | `_run_and_complete` — daemon thread that calls run_sdlc_phases |
| `deployment/vm/systemd/dispatch-poller.service` | all | Missing `KillMode=process`; default cgroup kill hits children |
| `deployment/vm/push-code.sh` | 124-196 | SDK safety check + restart logic; nohup fallback sends SIGKILL |
| `deployment/vm/hermes-watchdog.sh` | all | `killall -9 claude-real` could kill SDK's claude process |

### Process supervision

| File | Lines | What to check |
|------|-------|---------------|
| `deployment/hermes/dispatch_poller.py` | 186-204 | `is_agent_idle()` — ps grep for running SDK |
| `deployment/hermes/dispatch_poller.py` | 148-178 | `_local_queue_active()` — PID liveness check |
| `scripts/work_queue.py` | 186-192 | `os.kill(pid, 0)` — signal 0 existence check only |
| `deployment/vm/run_dispatch_poller.py` | all | Thin wrapper, no signal handling of its own |

### Server-side recovery (not signal-related, but context)

| File | Lines | What to check |
|------|-------|---------------|
| `tech_dev_agents/ops_console/services/dispatch_service.py` | 38, 107-153 | `STALE_CLAIM_SECONDS = 300` — still at 5 min; only moves DB state, doesn't send signals |
| `tech_dev_agents/ops_console/main.py` | 37-52 | `_stale_claim_recovery_loop` — runs every 60s server-side |

---

## Fix Candidates

### Fix 1: Register SIGTERM handler in the MAIN thread (critical)

Move `install_shutdown_handler()` from `run_sdlc_phases()` (daemon thread) to `poll_loop()` or `run_dispatch_poller.py` (main thread). The handler must be registered where `signal.signal()` can actually succeed.

### Fix 2: Protect SDK subprocess from cgroup SIGTERM

Add `KillMode=process` to `dispatch-poller.service` so systemd only sends SIGTERM to the main PID, not children. Combined with Fix 1, the main process catches SIGTERM, gracefully shuts down the SDK child, and exits 143.

Alternatively, use `start_new_session=True` in the `subprocess.run()` call for the SDK, placing it in its own process group/session. systemd's cgroup kill would still reach it, but this provides defense-in-depth.

### Fix 3: Add SIGTERM handling in claude_sdk_tool.py itself

As defense-in-depth, `claude_sdk_tool.py` should install its own SIGTERM handler that commits partial work before exiting. This protects against direct SIGTERM from ANY source.

### Fix 4: Increase STALE_CLAIM_SECONDS (server-side, separate PR)

`STALE_CLAIM_SECONDS = 300` (5 min) is far too aggressive for phases that legitimately run 20-60 minutes. The dispatch context mentions STORY-511 Fix #1 intended to raise this to 3600s. This doesn't prevent SIGTERM but reduces false stale-claim recovery that could cause duplicate work.

---

## Acceptance Criteria

- [ ] AC1: `_graceful_shutdown` SIGTERM handler is registered in the main thread and actually fires on SIGTERM
- [ ] AC2: SDK child process is protected from systemd cgroup kill (via KillMode=process or process group isolation)
- [ ] AC3: When the dispatch-poller receives SIGTERM, partial work is committed and pushed before exit
- [ ] AC4: `rc=-15` events do not occur during normal 10-minute phase execution
- [ ] AC5: Unit tests verify rc=-15 propagation AND partial work saving on external SIGTERM
- [ ] AC6: Integration test confirms zero SIGTERMs from poller/recovery in a 3-minute SDK run
- [ ] AC7: Regression test: 10-minute Phase 6 scenario completes without SIGTERM

---

## Test Criteria (from dispatch brief — MANDATORY in Phase 7 test-design.md)

1. **Unit test:** Call `_save_partial_work` / `_run_phase_sdk` with a mock subprocess that simulates external SIGTERM at 60s. Assert rc=-15 is propagated correctly AND partial work is still saved (git add/commit).
2. **Integration test:** Run a dummy SDK on a test VM for 3 minutes. Poll the dispatch-poller journal for any process that sends signal 15 to the SDK PID. Assert zero SIGTERMs in normal operation.
3. **Regression test:** Exercise the scenario "Devon works Phase 6 for 10 minutes" — verify no SIGTERM is sent by the poller or recovery loop within that window.

## E2E Validation (MANDATORY)

After implementing any fix, run the dispatch queue for 1 hour with Medium-scope stories on at least one agent. Zero rc=-15 events acceptable. If ANY rc=-15 fires during the hour, the fix is incomplete.
