# STORY-425 — Circuit Breaker for Dispatch Poller (Seed Retrofit, Case Study)

> **Purpose:** Post-mortem case study. STORY-425 burned 3 PR attempts (#54, #56, #57 — all CLOSED) before being abandoned / absorbed into later work. No original `seed.md` survives in the tree because the story was dispatched directly from a thin prompt.
>
> **Why a retrofit:** To illustrate what the seed *should have been* under the 2026-04-21 required-sections format. Reference for Morris + future dispatches. This file is intentionally written as if before work started, not as a retrospective.

---

## Overview

| Field | Value |
|-------|-------|
| Mode | feature_update |
| Scope | Medium |
| Feature Name | Circuit Breaker for Dispatch Poller |
| Story ID | STORY-425 |

## Problem Statement

The dispatch poller on each agent VM re-enqueues failed stories immediately. When an upstream dependency is down (GitHub API, Monday.com, Claude SDK rate-limited), the same story fails → re-enqueues → fails → re-enqueues in a tight loop, burning tokens and flooding logs. Derrick recorded 67 fast-failures in a single day (2026-04-16). We need a circuit breaker that trips after N rapid consecutive failures on a given story, blocks retries during a cooldown, and probes a single retry in half-open state before fully resuming.

## Target User / Use Case

- **Operations (Mark, Morris)** — need the fleet to self-protect against upstream outages without manual intervention
- **Agents (Dan, Derrick, Hermes)** — must stop burning tokens on broken stories; cooldown must not permanently block valid work

## Success Criteria

- [ ] SC-1: Circuit trips to OPEN after N consecutive failures each within `rapid_failure_window` seconds of the previous (per-story, not global)
- [ ] SC-2: While OPEN, `_report_fail()` does NOT re-enqueue — the story is quarantined
- [ ] SC-3: After `cooldown` seconds in OPEN, transitions to HALF-OPEN and allows exactly one probe retry
- [ ] SC-4: Probe success → CLOSED (normal ops resume); probe failure → back to OPEN with fresh cooldown
- [ ] SC-5: Per-story isolation — a tripped breaker for STORY-A does not block STORY-B
- [ ] SC-6: Threshold, window, and cooldown are configurable via env vars (`CIRCUIT_BREAKER_CONSECUTIVE_FAILURES`, `CIRCUIT_BREAKER_RAPID_FAILURE_WINDOW`, `CIRCUIT_BREAKER_COOLDOWN`)
- [ ] SC-7: When breaker trips, a `circuit_broken` flag file is written with story_id + timestamp + failure count metadata (for Morris's fleet-check to detect)
- [ ] SC-8: 429 rate-limit responses are recognized separately and do NOT count toward circuit-breaker consecutive failures (they use the existing rate-limit backoff path)
- [ ] SC-9: `story_id` is validated against `^[A-Z0-9_-]{1,64}$` before any filesystem path is constructed (prevents path traversal via flag-file path)

## Verification Plan

| SC # | Command | Expected Output |
|------|---------|-----------------|
| SC-1 | `pytest tests/test_circuit_breaker.py::test_trips_on_rapid_consecutive_failures -v` | `1 passed` |
| SC-2 | `pytest tests/test_circuit_breaker.py::test_report_fail_skips_reenqueue_when_open -v` | `1 passed` |
| SC-3 | `pytest tests/test_circuit_breaker.py -k "cooldown or half_open" -v` | `≥3 passed` |
| SC-4 | `pytest tests/test_circuit_breaker.py -k "probe" -v` | `≥2 passed` |
| SC-5 | `pytest tests/test_circuit_breaker.py::test_per_story_isolation -v` | `1 passed` |
| SC-6 | `CIRCUIT_BREAKER_CONSECUTIVE_FAILURES=2 pytest tests/test_circuit_breaker.py::test_env_var_override -v` | `1 passed` |
| SC-7 | `pytest tests/test_circuit_breaker.py::test_flag_file_written_on_trip -v` | `1 passed` |
| SC-8 | `pytest tests/test_circuit_breaker.py::test_429_bypasses_breaker -v` | `1 passed` |
| SC-9 | `pytest tests/test_circuit_breaker.py -k "path_traversal or story_id_validation" -v` | `≥3 passed` (valid, traversal, lowercase, empty) |
| All | `pytest tests/test_circuit_breaker.py tests/test_dispatch_poller.py -q` | `≥27 passed` (21 circuit + existing poller tests) |

## Boundaries

| Always Do | Ask First | Never Do |
|-----------|-----------|----------|
| Run `pytest tests/test_circuit_breaker.py tests/test_dispatch_poller.py -q` after every edit | Change the existing 429 / rate-limit backoff logic | Edit `.project` — use `update_story_status()` |
| Push after every commit | Rename `CircuitBreaker` class / public API | Commit secrets or modify env-loading to hardcode defaults |
| Use `./deployment/vm/push-code.sh derrick` (NOT scp) after commit to deploy to VM | Change poll interval | Open a PR with any test RED |
| Message Mark in Teams if the existing `_report_fail` signature change breaks a caller | Add a new env var beyond the 3 listed | Skip the VM smoke test (push-code.sh runs it — don't bypass) |
| Message Mark if you discover the circuit breaker needs cross-process persistence | Swap thread-safety model | Write to `/opt/agent/` without restarting the poller (see CLAUDE.md deploy rule) |

## Files to Modify

| File | Why |
|------|-----|
| `deployment/hermes/dispatch_poller.py` | Add `CircuitBreaker` class; wire into `_report_fail()` after 429 early-return and before re-enqueue |
| `tests/test_circuit_breaker.py` | NEW — unit tests for CircuitBreaker state machine + story_id validation |
| `tests/test_dispatch_poller.py` | Extend — verify `_report_fail` skips re-enqueue when breaker open; verify 429 bypass |
| `deployment/vm/env.sample` | Document 3 new env vars (`CIRCUIT_BREAKER_*`) with defaults |

## Files to NOT Modify

| File | Reason / Owner |
|------|----------------|
| `.project` | Shared state — use `update_story_status()` |
| `deployment/hermes/claude_sdk_tool.py` | Owned by STORY-336 (rate-limit fixes already landed) |
| `deployment/hermes/project_file.py` | Owned by STORY-440 — concurrent work |
| `deployment/hermes/work_queue.py` | Out of scope — circuit breaker lives in poller, not queue |
| `tech_dev_agents/ops_console/**` | Ops console is separate from agent poller code |
| `scripts/fleet_review.py` | Owned by STORY-324 — the circuit-broken flag can be consumed later, not in this story |
| `config.yaml` | Env vars, not config-driven |

## Done Looks Like

```
$ pytest tests/test_circuit_breaker.py tests/test_dispatch_poller.py -q
...........................
27 passed in 0.52s

$ ./deployment/vm/push-code.sh derrick
[push-code] rsync complete
[push-code] hash verification: OK
[push-code] restarting dispatch-poller on derrick...
[push-code] smoke test: import OK, claude CLI OK, no errors in last 60s
[push-code] deploy to derrick: SUCCESS

$ ssh -p 443 hermes@20.121.210.186 "systemctl status dispatch-poller"
● dispatch-poller.service — active (running)

$ gh pr view
CI: ✓ all checks passing
```

## Escalation Contract

| Situation | Action |
|-----------|--------|
| Unclear whether breaker state should persist across poller restarts | Message Mark with options (in-memory only vs. flag-file state vs. DB) before implementing |
| Existing `_report_fail` callers break after signature change (`duration` param added) | Revert the signature change; add breaker check as a separate guard instead. Message Mark. |
| `push-code.sh` smoke test fails after deploy | The script already stops the poller on failure — do NOT retry deploy. Investigate locally, fix, redeploy. Message Mark if stuck. |
| Tests RED after 2 implementation attempts | Message Mark with test output + current diff. Do NOT open a PR. |
| Azure quota exceeded during deploy (eastus full) | Do NOT provision a new VM — that's Mark's call per agent memory. Message Mark. |

## Security Constraints

- [x] `story_id` validated against `^[A-Z0-9_-]{1,64}$` BEFORE any filesystem use (AC-9)
- [x] Flag file path uses validated story_id only; no user input reaches `pathlib` without validation
- [x] All 3 env vars validated as positive integers at load; invalid values fall back to defaults + log a warning
- [x] Log messages include story_id but never credentials / tokens
- [x] No hardcoded thresholds — all come from env with safe defaults

---

## What the 3 closed PRs missed (post-mortem reasoning for this retrofit)

| Failure in PRs #54/#56/#57 | Retrofit section that would have caught it |
|---|---|
| PR #54 had no `story_id` validation — introduced path traversal vulnerability Morris flagged | **Security Constraints** + SC-9 / Verification row for SC-9 |
| PR #56 attempted to cap failure counter but broke per-story isolation | **Files to Modify** scoped narrowly + SC-5 explicit verification command |
| Deploy skipped restart — cached old code ran in prod (see CLAUDE.md incident 2026-04-18/19) | **Boundaries → Always Do** `push-code.sh` + **Done Looks Like** showing smoke-test output |
| Agent changed `_report_fail` signature and broke other callers | **Boundaries → Ask First** rename / signature change |
| PR opened with 2 tests still RED | **Boundaries → Never Do** PR with RED tests; **Done Looks Like** literal `27 passed` |
| Agent considered switching to a threaded implementation mid-story | **Boundaries → Ask First** swap thread-safety model |
