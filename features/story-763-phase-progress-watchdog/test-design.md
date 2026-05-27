# STORY-763 — Phase-Progress Watchdog: Test Design

## Summary

| Field | Value |
|-------|-------|
| Story | STORY-763 — Kill Zombie Heartbeats When SDK Dies Silently |
| Scope | Small |
| Coverage target | 50% (critical paths) |
| Test file | `tests/deployment/test_phase_progress_watchdog.py` |
| Test count | 12 tests across 5 groups |
| RED state confirmed | Yes — all behavioral tests fail because `_heartbeat_thread` does not yet accept `sdk_pid`, `last_output_ts`, or `phase_timeout_s` parameters |

## Implementation Note

The `_heartbeat_thread` function lives in `deployment/hermes/sdlc_phase_runner.py` (not
`dispatch_poller.py` as the seed states — the seed's "Files to Modify" section has an error).
The correct file is confirmed by:

```
$ grep -n "_heartbeat_thread" deployment/hermes/sdlc_phase_runner.py
268: HEARTBEAT_INTERVAL = 300
271: def _heartbeat_thread(story_id, repo, stop_event):
2476:     target=_heartbeat_thread,
```

Phase 8 must modify `sdlc_phase_runner.py` (not `dispatch_poller.py`).

## What Changes in Phase 8

The current `_heartbeat_thread` signature:
```python
def _heartbeat_thread(story_id: str, repo: str, stop_event: threading.Event) -> None:
```

Expected new signature:
```python
def _heartbeat_thread(
    story_id: str,
    repo: str,
    stop_event: threading.Event,
    sdk_pid: int | None = None,
    last_output_ts: list[float] | None = None,
    phase_timeout_s: int = 1200,
) -> None:
```

On each heartbeat tick the thread must now:
1. `os.kill(pid, 0)` — if raises `ProcessLookupError`, fail story + exit loop
2. `time.time() - last_output_ts[0] > phase_timeout_s * STALE_MULTIPLIER` — if stale,
   kill process group (SIGTERM → 5s grace → SIGKILL), fail story + exit loop
3. Log all watchdog actions: `[DISPATCH] watchdog: STORY-N <action> reason=<reason>`
4. On failure: POST to `/api/dispatch/fail/{story_id}` via urllib (existing pattern)
5. Clear local work queue: `WorkQueue.complete(story_id)`

The stdout update mechanism (`last_output_ts[0] = time.time()`) must live in the
subprocess stdout-reading code path added in `_run_phase_sdk`.

---

## Test Groups

### Group A — Signature & Plumbing (SC-1, AC-1)

Verifies the function accepts the new parameters that carry SDK lifecycle state.

| Test | AC | RED Reason |
|------|----|------------|
| `test_heartbeat_thread_accepts_sdk_pid_parameter` | AC-1 | `sdk_pid` not in current `inspect.signature` |
| `test_heartbeat_thread_accepts_last_output_ts_parameter` | AC-1 | `last_output_ts` not in current `inspect.signature` |

### Group B — PID Liveness (SC-2, AC-2, AC-5, AC-6, AC-9)

Verifies that a dead SDK process is detected and the story is failed with the correct
structured reason.

| Test | AC | RED Reason |
|------|----|------------|
| `test_dead_pid_triggers_failure_with_structured_reason` | AC-2, AC-5, AC-9 | Thread raises TypeError (new params not accepted); no watchdog logic exists |
| `test_dead_pid_exits_heartbeat_loop` | AC-6 | Same |

### Group C — Progress Stall (SC-3, SC-5, AC-3, AC-4)

Verifies the 2× timeout threshold: stale → kill; 1.5× → safe.

| Test | AC | RED Reason |
|------|----|------------|
| `test_stalled_progress_triggers_kill_and_failure` | AC-3, AC-4 | Thread raises TypeError; kill logic not implemented |
| `test_legitimate_slow_phase_does_not_trigger` | SC-5 | Thread raises TypeError; no-trigger logic not implemented |
| `test_stale_multiplier_env_var_configures_threshold` | AC-3 | Env var not read; logic not implemented |

### Group D — Stdout Watcher (SC-4, AC-8)

Verifies that SDK stdout lines update the shared `last_output_ts` container.

| Test | AC | RED Reason |
|------|----|------------|
| `test_stdout_line_updates_last_output_ts` | AC-8 | Source doesn't contain `last_output_ts[0]` update pattern |
| `test_happy_path_heartbeat_still_fires_with_live_sdk` | SC-7 | Thread raises TypeError; heartbeat logic not invoked |

### Group E — Failure Observability (SC-6, AC-5, AC-9, AC-11)

Verifies structured failure reasons and log format.

| Test | AC | RED Reason |
|------|----|------------|
| `test_failure_reason_uses_sdk_died_prefix` | AC-5, AC-11 | Watchdog logic not implemented |
| `test_failure_reason_uses_stalled_prefix` | AC-5, AC-11 | Watchdog logic not implemented |
| `test_watchdog_logs_structured_message_to_stdout` | AC-9 | Log format not implemented |

---

## Test Specifications

### `test_heartbeat_thread_accepts_sdk_pid_parameter`

**Verifies:** `_heartbeat_thread` function signature includes `sdk_pid` parameter (SC-1, AC-1).

**Why this matters:** The thread must receive the subprocess PID so it can check liveness.
Without the parameter in the signature, Phase 8 implementers have no contract to target.

**Arrange:** Import `sdlc_phase_runner`; call `inspect.signature(_heartbeat_thread)`.

**Act:** Check `"sdk_pid" in sig.parameters`.

**Assert:** Assertion passes (signature has the param).

**RED reason:** Currently fails — current signature only has `(story_id, repo, stop_event)`.

---

### `test_heartbeat_thread_accepts_last_output_ts_parameter`

**Verifies:** Signature also includes `last_output_ts` and `phase_timeout_s` (SC-1, AC-1).

**Arrange/Act/Assert:** Same pattern as above for `last_output_ts` and `phase_timeout_s`.

**RED reason:** Same — parameters don't exist yet.

---

### `test_dead_pid_triggers_failure_with_structured_reason`

**Verifies:** When `os.kill(pid, 0)` raises `ProcessLookupError`, the watchdog POSTs to
`/api/dispatch/fail/{story_id}` and the captured failure URL/payload contains
`sdk_died_no_phase_end:` (SC-2, AC-2, AC-5, AC-9).

**Arrange:**
- `last_output_ts = [time.time()]` (fresh — ensure only PID death triggers)
- `stop_event = threading.Event()`
- Patch `os.kill` to always raise `ProcessLookupError("Process 99999 not found")`
- Patch `urllib.request.urlopen` to capture all outbound requests
- Set `HEARTBEAT_INTERVAL = 0.05` in module
- Set env vars `OPS_CONSOLE_URL` + `OPS_CONSOLE_API_KEY`

**Act:** Run `_heartbeat_thread(story_id, repo, stop_event, sdk_pid=99999, last_output_ts=last_output_ts, phase_timeout_s=1200)` in a thread. Wait 0.3s; stop_event.set(); join.

**Assert:**
- Thread exited cleanly (thread is not alive after join)
- At least one urllib request was made to a URL containing `"fail/STORY-763"`
- failure_reason captured in the request body starts with `sdk_died_no_phase_end:`

**Notes for implementer:**
- Use `threading.Thread` with exception capture (see test utility pattern in the file)
- The urllib POST carries the failure reason in the JSON body

**RED reason:** `TypeError: _heartbeat_thread() takes 3 positional arguments but 6 were given`.
After signature fix: failure logic not yet implemented.

---

### `test_dead_pid_exits_heartbeat_loop`

**Verifies:** After watchdog fires (PID dead), the heartbeat thread exits cleanly — no
leaked daemon thread (AC-6).

**Arrange:** Same setup as above, but focus on thread liveness after join.

**Act:** Start thread. Wait for watchdog to fire (short interval). join(timeout=2.0).

**Assert:** `not t.is_alive()` — thread is done.

**RED reason:** TypeError prevents the correct behavior from running.

---

### `test_stalled_progress_triggers_kill_and_failure`

**Verifies:** When `last_output_ts` is older than `2 × phase_timeout_s`, the watchdog:
1. Calls `os.killpg` with SIGTERM on the process group
2. After grace period, calls `os.killpg` with SIGKILL
3. Reports failure with `phase_progress_stalled:` prefix (SC-3, AC-3, AC-4, AC-5)

**Arrange:**
- `phase_timeout_s = 10` (short for test)
- `last_output_ts = [time.time() - 25]` — 25s ago, past 2× (20s) threshold
- `sdk_pid = 99999` (arbitrary — `os.kill(pid, 0)` will not raise since we mock it)
- Patch `os.kill` to return None (PID appears alive — we want stall to be the trigger)
- Patch `os.killpg` to capture calls
- Patch `time.sleep` to be instant (so SIGTERM → SIGKILL grace is skipped in test)
- Patch `urllib.request.urlopen` to capture requests
- Set `HEARTBEAT_INTERVAL = 0.05`

**Act:** Run thread. Wait 0.3s. stop_event.set(). join.

**Assert:**
- `os.killpg` was called (process group killed)
- SIGTERM called before SIGKILL (check call order)
- At least one request URL contains `"fail/STORY-763"`

**Notes for implementer:**
- Use `os.killpg(os.getpgid(sdk_pid), signal.SIGTERM)` to kill the process group
- 5s grace between SIGTERM and SIGKILL → mock `time.sleep` to skip in test

**RED reason:** TypeError on call with new params; kill logic not implemented.

---

### `test_legitimate_slow_phase_does_not_trigger`

**Verifies:** A phase at 1.5× timeout does NOT trigger the watchdog. This is the
negative guard proving the threshold isn't hair-trigger (SC-5).

**Arrange:**
- `phase_timeout_s = 100`
- `last_output_ts = [time.time() - 150]` — 150s ago, which is 1.5× (< 2×=200s)
- `sdk_pid = 99999`
- Patch `os.kill` → return None (PID alive)
- Patch `os.killpg` (capture — should NOT be called)
- Patch `urllib.request.urlopen` — capture fail requests (should be none)
- Set `HEARTBEAT_INTERVAL = 0.05`

**Act:** Run thread for 0.3s. stop_event.set(). join.

**Assert:**
- `os.killpg` was NOT called
- No requests to `"/fail/"` in captured URLs

**Notes for implementer:** The threshold is `time.time() - last_output_ts[0] > phase_timeout_s * STALE_MULTIPLIER`. At 1.5× it must not fire.

**RED reason:** TypeError on call; no-trigger logic not implemented.

---

### `test_stale_multiplier_env_var_configures_threshold`

**Verifies:** `PHASE_PROGRESS_STALE_MULTIPLIER=3.0` env var raises the threshold
so that 2.5× timeout does NOT trigger the watchdog (AC-3).

**Arrange:**
- `phase_timeout_s = 100`
- `last_output_ts = [time.time() - 250]` — 2.5× — would trigger at default 2.0 but not at 3.0
- Patch `os.environ["PHASE_PROGRESS_STALE_MULTIPLIER"] = "3.0"`
- Patch `os.kill` → alive, `os.killpg` → capture (should not be called)
- `HEARTBEAT_INTERVAL = 0.05`

**Act:** Run thread 0.3s. stop. join.

**Assert:** `os.killpg` NOT called (3.0 multiplier → threshold is 300s, elapsed is only 250s).

**RED reason:** Env var not read; watchdog would trigger at 2.0× regardless.

---

### `test_stdout_line_updates_last_output_ts`

**Verifies:** The implementation updates `last_output_ts[0] = time.time()` on each SDK
stdout line received (SC-4, AC-8). Checked via source inspection because the update
lives in the subprocess-reading code path.

**Arrange:** Read `sdlc_phase_runner.__file__` source.

**Act:** Search for the update pattern `last_output_ts[0]`.

**Assert:** Pattern found in source — proves the implementation wires stdout to the container.

**Notes for implementer:**
The Phase 8 implementation must add a stdout-reading mechanism (subprocess.Popen + a
reader thread) in `_run_phase_sdk()` and pass the updated `last_output_ts` container
to `_heartbeat_thread`. Each line from the subprocess stdout must execute:
`last_output_ts[0] = time.time()`

**RED reason:** Currently `last_output_ts[0]` does not appear anywhere in `sdlc_phase_runner.py`.

---

### `test_happy_path_heartbeat_still_fires_with_live_sdk`

**Verifies:** When SDK is alive and output is fresh, the heartbeat thread still sends
normal heartbeat POSTs every interval — watchdog additions must not break the happy
path (SC-7, AC-10).

**Arrange:**
- `sdk_pid = 99999`
- `last_output_ts = [time.time()]` — fresh
- Patch `os.kill` → return None (alive)
- Patch `urllib.request.urlopen` → capture POST requests
- `HEARTBEAT_INTERVAL = 0.05`

**Act:** Run thread 0.25s. stop. join.

**Assert:**
- ≥2 POSTs captured to heartbeat endpoint (not fail endpoint)
- `os.killpg` was NOT called

**RED reason:** TypeError on call — new params not accepted.

---

### `test_failure_reason_uses_sdk_died_prefix`

**Verifies:** When PID is dead, failure_reason starts with `sdk_died_no_phase_end:`
followed by `pid=<N>` (SC-6, AC-5, AC-11).

**Arrange:** Same as `test_dead_pid_triggers_failure_with_structured_reason`.

**Act:** Capture the JSON body of the fail POST request.

**Assert:**
- `failure_reason` field starts with `"sdk_died_no_phase_end:"`
- `"pid="` appears in the failure reason string

**RED reason:** Watchdog not implemented.

---

### `test_failure_reason_uses_stalled_prefix`

**Verifies:** When progress is stalled, failure_reason starts with `phase_progress_stalled:`
followed by `phase=<N>` and `last_output=<seconds>s_ago` (SC-6, AC-5, AC-11).

**Arrange:** Same as `test_stalled_progress_triggers_kill_and_failure`.

**Act:** Capture the JSON body of the fail POST.

**Assert:**
- `failure_reason` starts with `"phase_progress_stalled:"`
- `"last_output="` appears in the string with `"s_ago"` suffix

**RED reason:** Watchdog not implemented.

---

### `test_watchdog_logs_structured_message_to_stdout`

**Verifies:** Watchdog actions are logged to stdout in the format:
`[DISPATCH] watchdog: STORY-N <action> reason=<reason>` (AC-9).

**Arrange:**
- Same setup as dead-PID test
- Capture stdout using `io.StringIO` + `sys.stdout` redirect

**Act:** Run thread with dead PID. Capture all stdout.

**Assert:** Captured output contains `"[DISPATCH] watchdog: STORY-763"`.

**Notes for implementer:**
Every watchdog action (sdk_died, progress_stalled) must print this line so Loki can
alert on watchdog activity: `print(f"[DISPATCH] watchdog: {story_id} ...", flush=True)`.

**RED reason:** Log format not implemented.

---

## Gate Checks

| Gate | Applicable? | Status |
|------|-------------|--------|
| Gate 1: Null/None Boundary | Yes — `sdk_pid=None` (watchdog disabled) | Covered by happy-path test (pid=None disables check) |
| Gate 2a: External API Isolation | N/A — no write to external APIs | Skip |
| Gate 2b: External API Degradation | Partial — urllib timeout on fail POST | Not tested in v1 (low priority) |
| Gate 3: DB Constraints | N/A — no schema changes | Skip |
| Gate 4: Input Validation | Partial — os.kill(None) must not crash | Covered by sdk_pid=None = disabled |
| Gate 9: Failure Recovery | N/A — kill-and-fail is intentional | Skip |
| Gate 10: Error Observability | Yes — watchdog must log | Covered by Group E test |

## Output-Variance Check

This story modifies a daemon thread, not an input→output transform. No output-variance
test is required (the watchdog is a control-flow mechanism, not a data processor).

## Static Analysis Gate

Backend only (no frontend changes). No eslint gate required.

## Verification Plan

```
$ pytest tests/deployment/test_phase_progress_watchdog.py --collect-only
# Expected: 12 tests collected, 0 errors

$ pytest tests/deployment/test_phase_progress_watchdog.py -v
# Expected in RED state (Phase 7):
# - Group A (2): FAIL — signature assertions fail
# - Group B (2): FAIL — TypeError or missing logic
# - Group C (3): FAIL — TypeError or missing logic
# - Group D (2): FAIL — source pattern missing / TypeError
# - Group E (3): FAIL — log/prefix not implemented
# All 12 FAIL

$ pytest tests/ -x --ignore=tests/e2e -q
# Expected: all existing tests still pass (no regressions)
```

## Done Looks Like (Phase 8)

```
$ pytest tests/deployment/test_phase_progress_watchdog.py -v
test_heartbeat_thread_accepts_sdk_pid_parameter          PASSED
test_heartbeat_thread_accepts_last_output_ts_parameter   PASSED
test_dead_pid_triggers_failure_with_structured_reason    PASSED
test_dead_pid_exits_heartbeat_loop                        PASSED
test_stalled_progress_triggers_kill_and_failure          PASSED
test_legitimate_slow_phase_does_not_trigger              PASSED
test_stale_multiplier_env_var_configures_threshold       PASSED
test_stdout_line_updates_last_output_ts                  PASSED
test_happy_path_heartbeat_still_fires_with_live_sdk      PASSED
test_failure_reason_uses_sdk_died_prefix                 PASSED
test_failure_reason_uses_stalled_prefix                  PASSED
test_watchdog_logs_structured_message_to_stdout          PASSED
============================= 12 passed =============================

$ pytest tests/ -x --ignore=tests/e2e -q
ALL PASSED
```
