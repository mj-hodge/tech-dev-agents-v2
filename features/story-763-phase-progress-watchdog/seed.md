# STORY-763 — Phase-Progress Watchdog (Kill Zombie Heartbeats When SDK Dies Silently)

## Overview
| Field | Value |
|-------|-------|
| Mode | feature_update |
| Scope | small |
| Feature Name | Phase-progress watchdog |
| Phase Path | 1 → 7 → 8 → Done |
| Repo | tech-dev-agents |
| Frontend | false |
| Related | STORY-702 (heartbeat + stale-claim auto-release — this story complements it for live-but-stuck cases) |

## Problem Statement

The dispatch poller spawns a heartbeat thread when claiming a story (STORY-702). The heartbeat thread runs **independently of the SDK subprocess** — it pings every 5 minutes whether or not the SDK is making progress.

**Failure mode:** SDK subprocess dies silently mid-phase (unhandled exception, OOM, network blip, container restart). The poller's `subprocess.run(...)` returns or raises. The phase event log records the failure. The dispatch DB transitions to `status=failed`. **But the heartbeat thread keeps running** — it has no awareness of the SDK lifecycle. It heartbeats into the DB for hours.

Concrete evidence — STORY-010 on daisy, 2026-04-29:
```
12:43:28  Phase 6 (Design) for STORY-010 — starting SDK
12:44:09  [DISPATCH] heartbeat sent for STORY-010
12:49:09  [DISPATCH] heartbeat sent for STORY-010
... (every 5 min for the next 4 HOURS)
16:59:09  [DISPATCH] heartbeat sent for STORY-010
```

No `phase_end` event between `12:43:28` and `16:59:09`. No `Claude Code` output. No subprocess. The DB says `status=failed`. The agent has no zombie SDK process (`pgrep -af claude_sdk_tool.py` returns empty). **Yet the heartbeat thread is still alive in the poller**, pinging the DB at a row that is already failed.

This breaks STORY-702's stale-claim detector: stale-claim fires only when heartbeats *stop*. Here heartbeats are *present without progress*. Detector classifies the row as healthy and never reclaims it.

It also wastes ~1 row update per agent per 5 minutes for hours per zombie story. Currently 1 such zombie known.

## Target User / Use Case

**User:** STORY-702 stale-claim detector + Morris orchestrator + the agent itself (which is stuck in zombie mode rather than picking up new work).
**Today:** zombie heartbeats hide dead stories from detection, the agent's local poller queue thinks it has an active story (so it won't claim a new one), the DB shows healthy heartbeats on a story that's actually failed.
**After this story:** the heartbeat thread checks SDK liveness AND phase progress every cycle. If SDK is dead OR no `[Claude Code]` output observed in the last 2× phase timeout, the thread fails the story locally, kills its own loop, and frees the agent to claim the next story.

## Success Criteria

1. **SC-1 — Heartbeat thread tracks SDK PID + last-output timestamp.** The thread receives the SDK subprocess handle (or PID) and a shared `last_output_ts` reference. Each tick, it checks (a) is the PID alive? (b) is `last_output_ts` within the budget?
2. **SC-2 — SDK liveness check.** Per heartbeat tick, run the equivalent of `os.kill(pid, 0)`. If `ProcessLookupError` (PID dead): mark story failed locally with `failure_reason="sdk_died_no_phase_end: pid=<pid>"`, kill the heartbeat loop, free the local queue.
3. **SC-3 — Progress-stale check.** If `last_output_ts` is older than 2× the phase's declared timeout (e.g., default 1200s × 2 = 40 min for medium scope), kill the SDK subprocess (SIGTERM, then SIGKILL after 5s grace), mark story failed with `failure_reason="phase_progress_stalled: phase=<N> last_output=<seconds>s_ago"`, kill the heartbeat loop.
4. **SC-4 — Stdout watcher updates `last_output_ts`.** Every line emitted by the SDK to stdout (currently `[Claude Code] ...` lines) updates the shared timestamp. Already happens implicitly if poller pipes stdout — verify and surface.
5. **SC-5 — Watchdog DOES NOT trigger on legitimately slow phases.** A Sonnet phase that takes 15 minutes of "thinking" without intermediate stdout output is normal. The 2× timeout buffer is intentionally generous. Tests must include a fixture proving 1.5× timeout doesn't trigger; only beyond 2× does.
6. **SC-6 — Failure path populates failure_reason** (depends on STORY-762 landing first, but at minimum the local code passes a structured string). When STORY-762 ships, the watchdog's failure_reason becomes part of the contract-tested set.
7. **SC-7 — Zero regressions.** All existing tests pass. Happy-path heartbeats still fire every 5 minutes.

## Verification Plan

| SC | Command | Expected |
|----|---------|----------|
| SC-1 | `pytest tests/deployment/test_phase_progress_watchdog.py::test_heartbeat_thread_receives_sdk_pid -v` | PASSED |
| SC-2 | `pytest tests/deployment/test_phase_progress_watchdog.py::test_dead_pid_triggers_failure -v` | PASSED |
| SC-3 | `pytest tests/deployment/test_phase_progress_watchdog.py::test_stalled_progress_triggers_kill_and_failure -v` | PASSED |
| SC-4 | `pytest tests/deployment/test_phase_progress_watchdog.py::test_stdout_line_updates_last_output_ts -v` | PASSED |
| SC-5 | `pytest tests/deployment/test_phase_progress_watchdog.py::test_legitimate_slow_phase_does_not_trigger -v` | PASSED |
| SC-6 | The failure_reason emitted by the watchdog matches the structured prefix `sdk_died_no_phase_end:` or `phase_progress_stalled:` | Asserted in tests |
| SC-7 | `pytest tests/ -x --ignore=tests/e2e -q` | All pass |

## Test Criteria

- **Tests use mocked subprocess + injected timestamps** to control PID liveness and `last_output_ts` deterministically. No real SDK invocation.
- **Tests are fast.** Full suite < 1 second.
- **Negative case:** test fixture with PID alive and `last_output_ts` < 1.5× timeout asserts watchdog does NOT trigger (proves it's not over-aggressive).
- **Positive cases (×2):** dead PID → fail; alive PID + stalled progress → kill + fail.

## Validation

Phase 8 is NOT complete until ALL demonstrated in PR body:

| Step | Command | Pass criterion |
|------|---------|----------------|
| 1 | `pytest tests/deployment/test_phase_progress_watchdog.py -v` | All ≥ 5 tests GREEN |
| 2 | `pytest tests/ -x --ignore=tests/e2e -q` | Full suite GREEN |
| 3 | After deploy, the existing zombie STORY-010 heartbeat is killed within 1 watchdog cycle (~5 min) — agent free to claim new work | Loki query shows watchdog fail event + zombie heartbeat stops |
| 4 | Negative-case demo: simulate slow but alive SDK in test fixture; verify watchdog does NOT kill | Documented in PR body |

## Acceptance Criteria

- [ ] AC-1: Heartbeat thread function signature accepts SDK process handle (or PID + shared output-ts reference).
- [ ] AC-2: PID-liveness check uses `os.kill(pid, 0)` (or equivalent platform-portable check).
- [ ] AC-3: Progress-stale threshold: `2 × phase.timeout_s` (configurable via env var `PHASE_PROGRESS_STALE_MULTIPLIER`, default 2.0).
- [ ] AC-4: On stale: SIGTERM → 5s grace → SIGKILL. Captured in subprocess kill helper.
- [ ] AC-5: Failure_reason populated with structured prefix (`sdk_died_no_phase_end:` or `phase_progress_stalled:`).
- [ ] AC-6: Heartbeat thread cleanly exits when watchdog fires (no leaked thread).
- [ ] AC-7: Local agent queue is cleared (agent free to pick up next story) after watchdog fail.
- [ ] AC-8: Stdout watcher updates `last_output_ts` on every received line.
- [ ] AC-9: Logging — every watchdog action logs to stdout: `[DISPATCH] watchdog: STORY-N <action> reason=<reason>` so visible in Loki.
- [ ] AC-10: Existing happy-path tests pass; no regressions.
- [ ] AC-11: Error/logging AC — watchdog failure includes the elapsed seconds and last seen output line (truncated to 200 chars) in failure_reason.

## Constraints
| Constraint | Value |
|------------|-------|
| Budget | minimal — modify heartbeat thread, add 5-6 tests |
| Timeline | URGENT — STORY-010 zombie pattern is production reality today |
| Scale | n/a |
| Tech | Python 3.12, threading + subprocess; no new deps |

## Performance Requirements
- Watchdog overhead per tick: < 10ms (one `os.kill(pid, 0)` + one timestamp comparison).
- Watchdog tick interval: same as heartbeat (5 min default).

## Security Constraints
- [ ] No new credentials or auth surface.
- [ ] PID checks don't cross user boundaries — uses the same hermes user that owns the SDK subprocess.
- [ ] Failure_reason MUST NOT include credentials, tokens, or env vars (last-output line is truncated and sanitized).

## Operational Lifecycle
- **Configuration changes after deploy?** Optional `PHASE_PROGRESS_STALE_MULTIPLIER` env var, default 2.0.
- **How operators tune?** Restart poller with new env var. Default should rarely need tuning.
- **Monitoring?** Watchdog actions logged to Loki: alert if `watchdog: STORY-N` events spike (indicates new class of SDK hang).

## Boundaries

| Always Do | Ask First | Never Do |
|-----------|-----------|----------|
| Kill SDK on detected stall (SIGTERM → grace → SIGKILL) | Whether to support kill-and-resume vs kill-and-fail (current scope: kill-and-fail, agent free to claim next) | Kill SDK on first cycle without checking PID liveness |
| Mark story failed with structured failure_reason | Whether to dump SDK stdout buffer to a file before kill (probably overkill for v1) | Leave heartbeat thread orphaned after kill |
| Update last_output_ts on every stdout line | Whether to also watch stderr (probably yes; if yes add to seed) | Reset last_output_ts on heartbeat ticks (would defeat the watchdog) |
| Use 2× timeout as default threshold | Whether to make threshold per-phase rather than global | Make threshold so tight that legitimate Sonnet thinking phases trigger it |

## Files to Modify

- `deployment/hermes/dispatch_poller.py` — heartbeat thread function. Add SDK pid + shared `last_output_ts` plumbing. Add liveness check + stall check + cleanup.
- `tests/deployment/test_phase_progress_watchdog.py` — **new file**, ≥ 5 tests.
- `features/story-763-phase-progress-watchdog/test-design.md` — Phase 7 deliverable.
- `.project`, `backlog.md` — tracking.

## Files to NOT Modify

- `deployment/hermes/sdlc_phase_runner.py` — phase logic; out of scope.
- `claude_sdk_tool.py` — SDK side; out of scope.
- STORY-702 stale-claim detector — orthogonal mechanism. Don't try to merge them.
- Database schema — `failure_reason` column already exists.

## Done Looks Like

```
$ pytest tests/deployment/test_phase_progress_watchdog.py -v
============================= test session starts ==============================
test_heartbeat_thread_receives_sdk_pid PASSED
test_dead_pid_triggers_failure PASSED
test_stalled_progress_triggers_kill_and_failure PASSED
test_stdout_line_updates_last_output_ts PASSED
test_legitimate_slow_phase_does_not_trigger PASSED
test_failure_reason_uses_structured_prefix PASSED
============================== 6 passed in 0.42s ===============================

$ pytest tests/ -x --ignore=tests/e2e -q
ALL PASSED

# After deploy, Loki shows zombie STORY-010 cleaned up:
[DISPATCH] watchdog: STORY-010 sdk_died_no_phase_end pid=12345 last_output=14400s_ago
[DISPATCH] STORY-010 FAILED via watchdog
[DISPATCH] heartbeat thread for STORY-010 exited cleanly
[DISPATCH] agent free to claim next story
```

## Escalation Contract

1. **The heartbeat thread doesn't have access to the SDK subprocess handle today** — that's the design gap; the implementation needs to plumb it through. Don't workaround with PID-by-name lookup.
2. **Stdout pipe is not currently captured by the heartbeat thread** — it's captured by the main poller body. Refactor minimally to share the timestamp via shared object/queue. Don't introduce a new IPC mechanism.
3. **Existing slow phases legitimately exceed 2× timeout** (e.g., Phase 8 implementation on a complex story) → that's NOT a watchdog target; it's a phase-timeout-tuning issue. Document the case, do NOT lower the threshold.
4. **Killing the SDK subprocess leaves orphan child processes (claude CLI subprocesses)** → use `os.killpg(os.getpgid(pid), SIGTERM)` to kill the process group. Document in implementation.
5. **STORY-762 has not yet shipped failure_reason persistence** → this story still passes the structured string locally. When 762 lands, this watchdog's failure_reason becomes contract-tested. No change needed here.

**Default if no story-specific rule fires:** if more than 1 turn is spent guessing, write QUESTION.md and pause.

## Codebase Context

| Aspect | Details |
|--------|---------|
| Affected files | `deployment/hermes/dispatch_poller.py` heartbeat thread |
| Related | STORY-702 stale-claim auto-release (heartbeat-absence detector); STORY-762 failure_reason persistence (consumer of the structured string) |
| Current behavior | Heartbeat thread runs independently of SDK; doesn't notice subprocess death or output stalls. |
| Desired change | Heartbeat thread is the watchdog: every tick checks PID liveness + last-output staleness, kills SDK and fails story on either trigger. |
| Test coverage | New file with 5-6 unit tests; full suite continues GREEN. |
| Architecture constraints | Threading + subprocess only. No new deps. Pure Python. |

## Out of Scope

- Resume-after-kill semantics (kill-and-resume): out of scope for v1; v1 is kill-and-fail.
- Per-phase tunable thresholds: out of scope; global multiplier is fine for v1.
- Backfilling existing zombie heartbeats (one-time SQL cleanup): file STORY-766 if motivated.
- Watching stderr separately from stdout (combined is fine for v1).

## Notes for Implementer

- The 2026-04-29 STORY-010 zombie is the canonical example. Look at daisy's journalctl on `vm-daisy` (20.98.231.234) for the heartbeat-without-progress pattern from `12:43:28` onward.
- Mark made a memory note: "feedback_dont_stop_agents.md — Only pause agents on real quota caps or fundamental breaks, not heuristic false positives." 2× timeout is the deliberately conservative threshold so this doesn't false-positive on legitimately slow phases.
- The watchdog complements STORY-702 — that one catches no-heartbeats; this catches heartbeats-without-progress. Don't merge them.
- Use process groups (`os.killpg`) when killing the SDK so that any spawned `claude` CLI children also die. Otherwise we leak processes.
