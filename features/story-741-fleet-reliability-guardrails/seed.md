# Seed: STORY-741 — Fleet Reliability Guardrails

**Frontend:** false

## Problem Statement

Five interrelated gaps in the fleet's failure-handling pipeline allow silent waste
of agent capacity and operator confusion:

1. **`--model` CLI contract mismatch** — `sdlc_phase_runner.py` passes `--model opus`
   to `claude_sdk_tool.py` for Opus phases (1, 6, 9, 10), but `claude_sdk_tool.py`'s
   argparse does not declare a `--model` argument. argparse rejects the flag and exits
   rc=2 immediately. Every dispatched Opus phase fails in <1 s with no useful output.
   The phase runner classifies this as a generic failure and retries it — same
   deterministic failure, up to 3 times.

2. **Stale-log rate-limit false positive** — `_run_phase_sdk` reads the newest log
   file by mtime (`sorted(glob(...), key=os.path.getmtime)[-1]`) without filtering
   by the current run's start time. A prior phase's rate-limit message in a stale
   log file causes `rate_limited = True` on a clean phase, pausing the agent for
   up to 1 hour when it should be working.

3. **rc=2 not classified as non-transient** — Return code 2 (argparse rejection,
   startup errors) is indistinguishable from rc=1 (SDK runtime failure). Both enter
   the generic retry path. Because argparse exits instantly (<1 s), the phantom-claim
   guard (duration <30 s) suppresses retry after the first attempt — but the first
   retry still fires, wasting a claim cycle.

4. **No structured failure classification in the poller** — `_report_fail` retries
   all non-429, non-phantom failures identically. STORY-641 and STORY-701 defined
   `_classify_failure` / `_classify_failure_reason` in RED tests that were never
   implemented. Deterministic failures (branch mismatch, gate rejection, code bugs)
   burn all 3 retries when they should be flagged immediately.

5. **No E2E failure-chain test** — The full path (queue entry -> claim -> phase
   runner execution -> failure -> poller retry/exhaust) has no integration-level
   fixture. Each component is tested in isolation but the handoff contracts
   (return codes, failure reasons, retry decisions) are not verified end-to-end.

## Target User

Dispatch operators (Mark) and autonomous agents (Dan, Daisy, Devon, Derrick).

## Success Criteria

A single pytest run covers all five gap areas with GREEN tests that will regress
if any of the contracts are broken.

## Scope Classification

**Small** — All changes are test-only (new regression tests) plus two surgical
production fixes (add `--model` to argparse in `claude_sdk_tool.py`; scope log
file reads by start time in `sdlc_phase_runner.py`). No API/DB changes. No
architectural impact. Isolated to `deployment/vm/claude_sdk_tool.py`,
`deployment/hermes/sdlc_phase_runner.py`, and `deployment/hermes/dispatch_poller.py`.

## Phase Path

`1 -> 7 -> 8 -> Done` (small scope)

## Test Criteria
Each of the five gaps must be covered by a unit/integration test that fails on the current code (RED) and passes after the fix (GREEN). Tests must be:
- **Deterministic** — no live VMs, no real Loki/Foundry calls; mock at the boundary.
- **Fast** — full suite for this story under 10 seconds.
- **Specific** — each test's assertion message names the guardrail it enforces (e.g., "phase runner must not pass --model to claude_sdk_tool").
Regression-style tests are required: each gap's fix ships with a test that fails if the guardrail is removed.

## Validation
Phase 8 is NOT complete until ALL of the following are GREEN on the agent's branch and demonstrated in the PR description:

| Step | Command | Pass criterion |
|------|---------|----------------|
| 1 | `pytest tests/ -x --ignore=tests/e2e -q` | Full suite passes; zero regressions vs. main |
| 2 | New tests for each of the 5 gaps | All pass; each names the guardrail it enforces |
| 3 | `pytest tests/test_sdlc_framework_compliance.py -v` | All compliance assertions pass |
| 4 | Manual: trigger one of the 5 gaps locally and confirm the new guardrail catches it | Documented in PR body with command + observed behaviour |

The PR body must enumerate which test corresponds to which gap (1–5).

## Acceptance Criteria

### AC1: `claude_sdk_tool.py` accepts `--model` argument
`claude_sdk_tool.py` argparse declares `--model` with choices `["opus", "sonnet", "haiku"]`
(default: `None`, meaning use the agent's default from `~/.claude/settings.json`).
When provided, the model value is passed to the SDK's `ClaudeAgentOptions` as
`opts.model = args.model`. Test: invoke `main()` with `["--model", "opus", "-p", "test", "-w", "/tmp"]`
and assert no argparse error (rc != 2).

### AC2: Phase runner `--model` flag reaches SDK tool without argparse rejection
Unit test monkeypatches `subprocess.run` and asserts that when `phase_num in {1, 6, 9, 10}`,
the `cmd` list contains `["--model", "opus"]`, and when `phase_num == 8`, the `cmd`
list does NOT contain `"--model"`. Pair with AC1 to confirm the flag is accepted.

### AC3: Stale-log rate-limit immunity — log file filtered by start time
`_run_phase_sdk` only reads log files whose `os.path.getmtime >= start_time` (the
timestamp captured before `subprocess.run`). A log file from a prior run (mtime
before `start_time`) is ignored even if it is the newest file in the directory.
Test: create two log files — one stale (mtime = start - 60) containing "hit your
limit", one fresh (mtime = start + 10) containing clean output. Assert
`rate_limited == False`.

### AC4: Stale-log immunity does NOT suppress real rate limits
Same setup as AC3 but the fresh log file contains "hit your limit". Assert
`rate_limited == True`. Ensures the time filter does not accidentally discard
current-run rate-limit signals.

### AC5: rc=2 classified as non-transient — no retry
When `_run_phase_sdk` returns rc=2 (argparse rejection), the phase runner returns
a distinguishable signal so the poller's `_report_fail` does NOT re-enqueue.
Test: mock `_run_phase_sdk` returning `(2, "unrecognized arguments: --model")`,
assert `_report_fail` is called with a marker that suppresses retry (e.g.,
`exit_code=2` and duration < 30 triggers phantom-claim guard already — verify
this path explicitly).

### AC6: rc=2 with duration < 30 s triggers phantom-claim suppression
Parametric test: `(exit_code=2, duration=1)` -> retry suppressed;
`(exit_code=1, duration=60)` -> retry allowed;
`(exit_code=1, duration=5)` -> retry suppressed (silent-429 path).
Verifies the phantom-claim guard covers the argparse-rejection scenario.

### AC7: Poller retry classification — non-transient patterns suppress retry
Test `_report_fail` with error messages matching non-transient patterns:
- `"unrecognized arguments"` (argparse rejection)
- `"branch mismatch"` / `"not on expected branch"`
- `"gate rejected"` / `"deliverable missing"`
Assert that these are NOT retried (either via phantom-claim guard or a
`NEVER_RETRY_PATTERNS` check). Transient patterns (`"timeout"`, `"connection reset"`)
MUST still be retried.

### AC8: SILENT-429 heuristic does not misfire on argparse rc=2
The STORY-625 silent-429 heuristic (`duration < 10 and rc != 0 and output < 200 chars`)
would incorrectly flag an argparse rejection as a rate limit. Test: `rc=2`,
`duration=0`, output=`"unrecognized arguments: --model"` (< 200 chars). With the
fix in place, assert `rate_limited == False` (argparse errors should NOT trigger
rate-limit pause). This requires the stale-log and rc classification fixes to
disambiguate startup failures from silent 429s.

### AC9: E2E failure-chain fixture — queue -> claim -> runner fail -> retry
Integration-level test using mocked HTTP (requests-mock or similar):
1. POST `/api/dispatch` enqueues a story (201)
2. GET `/api/dispatch/next` returns the story
3. POST `/api/dispatch/claim/{id}` succeeds (200)
4. `run_sdlc_phases` is monkeypatched to return `(False, None, None)` with
   `duration=60` (transient failure)
5. Assert POST `/api/dispatch/fail/{id}` is called
6. Assert a retry POST `/api/dispatch` is called with `[RETRY 1/3]` prefix
7. On third failure, assert retry is NOT re-enqueued and failure flag file is written

### AC10: E2E failure-chain — rate-limit path releases instead of failing
Same fixture as AC9 but `run_sdlc_phases` returns rate-limited signal.
Assert POST `/api/dispatch/release/{id}` is called (not `/fail`), and the
pause flag file is written. No retry enqueue.

### AC11: All existing tests remain GREEN (zero regressions)
The full `pytest tests/` suite passes. No existing test is broken by the
`--model` argparse addition, log-file time filtering, or rc=2 classification.
Specifically: `test_sdk_invocation_contract.py`, `test_poller_rate_limit_recovery.py`,
`test_daily_cap_phantom_claim.py`, and `test_rate_limit_release_path.py` all pass.

## Affected Files

| File | Change Type |
|------|-------------|
| `deployment/vm/claude_sdk_tool.py` | **Fix** — add `--model` argparse argument, pass to SDK options |
| `deployment/hermes/sdlc_phase_runner.py` | **Fix** — filter log files by `mtime >= start_time`; classify rc=2 as non-transient |
| `deployment/hermes/dispatch_poller.py` | **Enhancement** — add `NEVER_RETRY_PATTERNS` to `_report_fail` for non-transient errors |
| `tests/deployment/test_sdk_model_cli_contract.py` | **New** — AC1, AC2 |
| `tests/deployment/test_phase_runner_stale_log_immunity.py` | **New** — AC3, AC4, AC8 |
| `tests/deployment/test_rc2_non_transient_classification.py` | **New** — AC5, AC6 |
| `tests/deployment/test_poller_retry_classification_741.py` | **New** — AC7 |
| `tests/deployment/test_e2e_failure_chain.py` | **New** — AC9, AC10 |

## Dependencies

- STORY-556 (fleet reliability test backfill — complete, 40/40 GREEN)
- STORY-625 (silent-429 detection — complete, provides the heuristic AC8 tests against)
- STORY-641 (auto-retry classify failure — RED tests exist, this story builds on them)

## Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| `--model` argparse fix changes SDK behavior on deployed agents | Medium | High | Test-only deploy first; push-code.sh deploys + restarts + smoke-tests |
| Stale-log time filter too aggressive (discards valid current-run logs) | Low | Medium | AC4 explicitly tests that current-run rate limits are still detected |
| Silent-429 heuristic conflicts with rc=2 classification | Medium | Medium | AC8 explicitly tests that argparse rc=2 is NOT classified as silent-429 |
| E2E fixture too tightly coupled to internal implementation | Low | Low | Test HTTP contract boundaries, not internal function calls |

## Out of Scope

- Implementing the full `_classify_failure_reason` from STORY-701 (DLQ) — that is a separate medium-scope story
- Implementing the `_classify_failure` from STORY-641 — this story adds only the `NEVER_RETRY_PATTERNS` subset
- Changing the poller's `MAX_RETRY_ATTEMPTS` constant
- Adding Prometheus/Grafana metrics for failure classification
- Deploying to production agents (separate ops task)

## Notes

- The `--model` bug is currently masked in production because `claude_sdk_tool.py` is
  deployed to agent VMs via `push-code.sh`, and the agents' default model in
  `~/.claude/settings.json` happens to be correct for most dispatched work (Sonnet).
  Opus phases (1, 6, 9, 10) silently fail and get retried, eventually succeeding
  when the agent's Sonnet default produces acceptable output. This wastes ~3 claim
  cycles per Opus phase.
- The stale-log false positive was observed on 2026-04-22 (documented in code comments
  at line 1208-1218 of `sdlc_phase_runner.py`). The duration-only heuristic was removed
  but the mtime-based log selection remains vulnerable.
