# STORY-040: Fix Stale Local Work Queue

## Problem

The dispatch poller's local WorkQueue (`scripts/work_queue.py`, persisted at `~/.hermes/work-queue.json`) gets stuck with "active" stories that have already finished. When this happens, the poller sees `wq.resume() != None`, returns `is_agent_idle() = False`, and logs `[DISPATCH] busy, skipping` forever. This blocks ALL queue pickup until someone manually SSHs in and clears it. This has happened 10+ times in the last 3 days.

## Root Cause Analysis

1. **Dual writes (race condition):** Both `dispatch_poller.py` (finally block in `_run_and_complete`) AND `claude_sdk_tool.py` (result handler) call `wq.complete()` — two separate processes writing the same JSON file with no coordination.

2. **No file locking:** `work_queue.py` uses atomic writes (tmp+rename) but has no `fcntl` advisory locking. Two concurrent writes can race: SDK writes `complete()`, then poller overwrites with stale data.

3. **No PID liveness check:** `is_agent_idle()` calls `_local_queue_active()` which just reads the JSON. If the SDK process dies (OOM, SIGKILL, timeout) without calling `complete()`, the queue stays stuck forever.

4. **No reconciliation:** Nothing periodically checks for stale entries. The only cleanup path is the `complete()` call, which is exactly what fails in the bug scenario.

5. **SDK also calls `set_active()`:** `claude_sdk_tool.py` line 140 calls `_work_queue.set_active(_story_id, phase=8)` which can overwrite the poller's entry with different phase/timing data.

## Fixes Applied

| Fix | File | Description |
|-----|------|-------------|
| File locking | `scripts/work_queue.py` | Added `fcntl.flock` advisory locking to `_save()` to serialize writes |
| PID tracking | `scripts/work_queue.py` | `set_active()` now records the SDK subprocess PID |
| `clear_stale()` | `scripts/work_queue.py` | New method: checks if active entry's PID is alive, clears if dead |
| PID check in idle detection | `deployment/hermes/dispatch_poller.py` | `_local_queue_active()` now checks PID liveness and auto-clears stale entries |
| Reconciliation loop | `deployment/hermes/dispatch_poller.py` | `reconcile_stale_queue()` runs every 5 min in `poll_loop()` as safety net |
| Remove dual writes | `deployment/vm/claude_sdk_tool.py` | When `DISPATCHED_BY_POLLER=1` env var is set, SDK skips all queue writes |
| Poller owns queue | `deployment/hermes/dispatch_poller.py` | `start_story()` sets `DISPATCHED_BY_POLLER=1` and records SDK PID after Popen |

## Test Coverage

10 tests in `tests/deployment/test_stale_work_queue.py`:
- T01: Stale active cleared when no matching process
- T02: Reconciliation loop clears stale entries
- T03: File locking prevents corruption (10 concurrent threads)
- T04: Poller clears queue on SDK kill
- T05: Teams dispatch does not leave stale entry
- T06: is_agent_idle with dead PID returns True
- T07: clear_stale preserves live process (does NOT clear running work)
- T08: SDK tool skips queue when dispatched by poller
- T09: File lock blocks concurrent write (barrier synchronization)
- T10: Reconciliation runs periodically in poll_loop

## Deploy Checklist

- [ ] Update `dispatch_poller.py` on both VMs (`/opt/agent/dispatch_poller.py`)
- [ ] Update `work_queue.py` on both VMs (`/opt/agent/work_queue.py` or `/opt/hermes-agent/scripts/work_queue.py`)
- [ ] Update `claude_sdk_tool.py` on both VMs (`/opt/agent/claude_sdk_tool.py`)
- [ ] Restart dispatch-poller service on Dan (20.228.224.243)
- [ ] Restart dispatch-poller service on Derrick (20.121.210.186)
- [ ] Verify: dispatch a test story, let it complete, confirm poller shows "queue empty" not "busy, skipping"
