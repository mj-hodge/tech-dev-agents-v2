# STORY-725 — Queue Reliability Test Gaps: Empty Error, Rate-Limit Resume, needs_info Cross-Agent Guard

## 1. Overview

| Field          | Value                                                                                       |
| -------------- | ------------------------------------------------------------------------------------------- |
| Story ID       | STORY-725                                                                                   |
| Title          | Queue Reliability Test Gaps — empty error_message, RL resume, needs_info, seed-path fallback |
| Mode           | bug_fix                                                                                     |
| Scope          | small                                                                                       |
| Frontend       | false                                                                                       |
| Phase Path     | 1 → 7 → 8 → Done                                                                            |
| Priority       | 95 (CRITICAL — hardens production-critical dispatch queue path)                             |
| Branch         | `story-725/queue-reliability-test-gaps`                                                     |
| Owner Agent    | TBD via dispatch                                                                            |
| Repo           | tech-dev-agents                                                                             |
| Related Files  | `deployment/hermes/dispatch_poller.py`, `tech_dev_agents/ops_console/services/dispatch_db_service.py`, `tests/deployment/test_dispatch_poller.py`, `tests/deployment/test_dispatch_poller_retry_classification.py`, `tests/deployment/test_dispatch_poller_needs_info_guard.py`, `tests/deployment/test_phase_runner_seed_path.py` |
| Risk           | Medium — touches two hot paths (`_report_fail`, `poll_loop`); fully covered by new tests    |

---

## 2. Idea / Trigger

A failure audit of the tech-dev-agents dispatch queue on **2026-04-26** surfaced four production failure patterns that are NOT covered by the existing test suite. The headline incident: **STORY-644** failed at `02:36:07` and was reported as failed at `02:36:09` — a 2-second life cycle that produced no stdout and no stderr from the SDK subprocess. The auto-retry classifier saw `error_message=None`, returned `"unknown"`, fired a retry, and the cycle repeated until all three retries were exhausted. Three retry cycles burned in ~9 seconds, and the story was flagged `needs_info` with no useful diagnostic content.

Adjacent gaps came out of the same audit:

- The rate-limit pause/resume code (STORY-253) has a unit test for the *pause* side (RL-04) but no test for the *resume* side. A regression that prevents flag deletion would freeze the poller indefinitely — exactly what happened in the 2026-03-19 incident.
- STORY-323 and STORY-592 are both stuck at `RETRY 3/3` with unanswered `QUESTION.md` files. The existing local-side `needs_info` guard test covers the agent-side filter, but there is no test that the server-side `next_pending` query in `dispatch_db_service.py` excludes `needs_info` rows from being returned to *any* agent.
- The seed-path parser silently falls back to scope-default phases when a seed file is unreadable, with no warning, masking misconfigured stories.

This story closes those four gaps with the minimum viable change set: two surgical production fixes plus four new tests.

---

## 3. Problem Statement

Four distinct gaps, each tied to a real production failure mode:

### Gap 1 — Empty/null `error_message` triggers retry storm (CRITICAL)

When the SDK subprocess exits in < 5 seconds and produces no stdout/stderr (e.g., container env var missing, immediate ImportError, permission denial before any `print()`), `_report_fail` is invoked with `error_message=None` and a tiny `duration_seconds`. The current logic does not consult `duration_seconds`; the (planned) `_classify_failure_reason` returns `"unknown"`; the retry path proceeds. The retry fails identically in 2 seconds (same deterministic pre-flight defect). After three rounds, the story is flagged for human review — but the diagnostic record contains no useful context, and ~9 seconds of queue time were burned producing a misleading retry count.

Production case: STORY-644, 2026-04-26 02:36:07 → 02:36:09, three rounds completed by 02:36:36.

### Gap 2 — Rate-limit pause resume path untested (HIGH)

`dispatch_poller.poll_loop` writes `/var/run/dispatch-poller-paused-until` on rate-limit detection (STORY-253), then on each tick reads the flag and either skips polling (if `time.time() < reset_ts`) or deletes the flag and resumes polling. The pause-side behavior is covered by RL-04. The **resume-side** behavior — flag is deleted AND `poll_once` is invoked on the same tick when `reset_ts` has passed — is **not** tested. A future change that breaks `os.remove(pause_path)` (e.g., wrong path, permission error suppressed) would leave the poller paused forever with no automated detection.

### Gap 3 — `needs_info` cross-agent guard missing on server side (HIGH)

Existing test `test_dispatch_poller_needs_info_guard.py` validates the *local* (agent-side) guard: an agent will not re-claim a story it already has flagged `needs_info` locally. But the production failure mode is **cross-agent**: agent A writes `QUESTION.md`, has no local record because Hermes was redeployed/cleared, and agent B claims the story from `/api/dispatch/next` and fails to answer it. The server-side defense is `next_pending` in `dispatch_db_service.py` — its `WHERE status = 'pending'` clause should exclude `needs_info` rows. That is implicit in the query today, but no test pins it down. A future migration that introduces `needs_info` as a sub-state of `pending` (or any equivalent regression) would silently re-expose the bug.

Concrete cases: STORY-323 and STORY-592, both at `RETRY 3/3`, both with `QUESTION.md` unanswered, both at risk of being re-claimed by a different agent if the queue is re-scanned.

### Gap 4 — Phase router silently swallows unreadable seed (MEDIUM)

The phase-path router's seed parser (planned location: `deployment/hermes/dispatch_poller.py` or a co-located helper, callsite at line ~429 where the scope-default map lives) reads the seed file to look for an explicit `Phase Path:` declaration, falling back to scope defaults if the file is missing or unreadable. The fallback is silent — no warning log, no telemetry. A misconfigured worktree (missing seed file, wrong story-folder slug) produces a Medium scope-default phase set even when the prompt body explicitly declared `Phase Path: 1 → 8 → Done`. The override is invisible at runtime.

---

## 4. Scope Classification

**Scope: small.** Justification:

- Two surgical production-code edits, both <20 LOC each:
  - `_report_fail` — add a duration/error-text guard in front of the auto-retry decision.
  - `poll_loop` — no behavior change required; the resume path is already implemented (lines 855–895 in `dispatch_poller.py`). This story adds the missing test only.
- Four new tests, all in `tests/deployment/`. No new modules, no schema changes, no API changes, no frontend, no migration.
- No taxonomy expansion beyond reusing the existing `"agent_died"` failure_reason value.

Phase path is `1 → 7 → 8 → Done` (skip Phase 6 — design is fully captured here in §5).

---

## 5. Codebase Context

### Fix 1 — Instant-failure classifier branch in `_report_fail`

**File:** `deployment/hermes/dispatch_poller.py`
**Function:** `_report_fail` (defined line 261, retry decision begins line 293)
**Edit point:** Insert a new guard between line 295 (rate-limit early return) and line 296 (the `if not repo or not prompt:` short-circuit).

**Logic:**

```python
# STORY-725 Fix 1: deterministic pre-flight failures produce no output and
# exit in < 30s. Retrying burns queue time without changing the outcome.
# Treat as "agent_died" and skip auto-retry — let the failure flag at line
# 316 capture the story for human review on the FIRST failure rather than
# the third.
if (
    duration_seconds is not None
    and duration_seconds < 30
    and exit_code not in (None, 0)
    and not (error_text or "").strip()
):
    print(
        f"[DISPATCH] {story_id} pre-flight failure "
        f"(duration={duration_seconds}s, exit={exit_code}, no output) — "
        f"classified agent_died, skipping auto-retry",
        flush=True,
    )
    # Reuse the failure-flag write path so Morris's fleet-vigilance Check 5
    # still surfaces it. Jump to the >MAX_RETRY_ATTEMPTS branch logic by
    # returning early after writing the flag.
    _write_failure_flag(story_id, repo, scope, reason="agent_died_preflight")
    return
```

**Required signature change:** `_report_fail` must accept `duration_seconds: int | None = None` and `error_text: str | None = None`. The `duration_seconds` param is already plumbed through (line 701 passes it). `error_text` needs to be threaded through from the `_run_and_complete` call sites at lines 555, 685, 709.

**Test file:** `tests/deployment/test_dispatch_poller_retry_classification.py` (NEW or extend if present). Test group identifier: **Group E — Pre-flight failure classification.**

- **E-01:** `_report_fail(duration_seconds=2, exit_code=1, error_text=None)` → POSTs `/api/dispatch/fail/{story_id}`, does NOT POST `/api/dispatch` (no retry), DOES write failure flag file.
- **E-02:** `_report_fail(duration_seconds=120, exit_code=1, error_text=None)` → preserves existing retry behavior (long-duration silent failure is *not* preflight; retry fires).
- **E-03:** `_report_fail(duration_seconds=2, exit_code=1, error_text="ImportError: foo")` → preserves existing retry behavior (output exists; retry fires).

### Fix 2 — Rate-limit resume path test (RL-05)

**Production code:** `deployment/hermes/dispatch_poller.py` `poll_loop` lines 847–897. **No production-code change required** — the resume logic is implemented (see lines 866–871). This story closes the test gap.

**Test file:** `tests/deployment/test_dispatch_poller.py`
**Test class:** `TestRateLimitDetectionViaSessionLog` (line 651) — append `test_pause_flag_cleared_on_reset_and_polls_same_tick` (RL-05).

**Test logic:**

1. Monkeypatch `/var/run/dispatch-poller-paused-until` to a `tmp_path` fixture (the source uses a hardcoded literal — patch via `monkeypatch.setattr` on a module-level constant added for testability, OR use `unittest.mock.patch` on `os.path.exists` and `open` and `os.remove`).
2. Write the pause flag file with a string that `_parse_reset_time` resolves to `time.time() - 60` (60 seconds in the past).
3. Mock `poll_once` to a `MagicMock`.
4. Call `poll_loop` with a stub that runs exactly one iteration (raise `KeyboardInterrupt` after first cycle, OR refactor `poll_loop`'s body into a `_poll_tick()` helper and call it directly — preferred since it avoids long-running loop in tests).
5. Assert: pause file no longer exists (`assert not os.path.exists(pause_path)`).
6. Assert: `poll_once.call_count == 1`.
7. Assert: log line `"reset window passed"` appears in captured stderr/stdout.

If `poll_loop` cannot be cleanly tested without refactor, the implementation phase MAY extract the pause-handling block (lines 855–897) into `_handle_pause_flag(now: float) -> bool` returning `True` if `poll_once` should run on this tick. RL-05 then targets the helper directly.

### Fix 3 — Server-side `needs_info` exclusion test

**Production code:** `tech_dev_agents/ops_console/services/dispatch_db_service.py` `next_pending` (line 136). The query is `WHERE status = 'pending'`, so `needs_info` rows are already excluded by virtue of having a non-`pending` status. **No production-code change required** if `needs_info` is stored as its own status string. Verify this by running the test against the current implementation.

**Test file:** `tests/deployment/test_dispatch_poller_needs_info_guard.py` (extend existing) OR `tests/ops_console/test_dispatch_db_service_needs_info.py` (new, preferred — it's a service-layer test, not a poller test).

**Test logic (NI-S-01, "S" = server-side):**

1. Set up an in-memory or test-pool `DispatchDBService` with three rows:
   - `STORY-A` status `pending`, enqueued_at `t0`.
   - `STORY-B` status `needs_info`, enqueued_at `t0 - 60` (older — would be returned first if status filter were broken).
   - `STORY-C` status `claimed`, enqueued_at `t0 - 120`.
2. Call `await service.next_pending()`.
3. Assert returned row is `STORY-A`.
4. Assert `STORY-B` is NOT returned even though its `enqueued_at` is older.

**Optional NI-S-02:** With ONLY a `needs_info` row in the table, `next_pending()` returns `None` (queue is effectively empty from the dispatcher's perspective).

If the production schema does not have a `needs_info` status enum value, this story's implementation phase must add it (one-line migration or enum extension) — but the failure audit referenced live `needs_info` rows, so the value already exists.

### Fix 4 — Seed-path fallback warning (B-05)

**Production code:** seed-path parser. Searched for `parse_seed_phase_path`, `seed_phase`, `Phase Path` — no current implementation exists in `deployment/hermes/dispatch_poller.py`. This is consistent with the audit finding: the fallback exists *implicitly* via the scope-default map at line 429 (`"small": ["seed.md", "test-design.md"]`, etc.). The implementation phase will add an explicit parser:

```python
def parse_seed_phase_path(seed_path: str | os.PathLike) -> list[str] | None:
    """Read the seed file and extract an explicit Phase Path declaration.

    Returns the parsed phase list (e.g., ["1", "7", "8", "Done"]) when
    found. Returns None when the seed cannot be read OR has no Phase Path
    line — caller should fall back to scope defaults. Logs a WARNING when
    the seed is unreadable (vs. simply absent of a Phase Path line).
    """
```

**Test file:** `tests/deployment/test_phase_runner_seed_path.py` (NEW). Test ID **B-05.**

**Test logic:**

1. **B-05a (unreadable seed → warning):** Call `parse_seed_phase_path("/nonexistent/path/seed.md")`. Assert returns `None`. Assert `caplog` (pytest log fixture) contains a `WARNING` record with text matching `seed.*not.*readable` or similar.
2. **B-05b (readable seed, no Phase Path line → no warning, returns None):** Write a seed file with no `Phase Path:` line. Assert returns `None`. Assert no WARNING logged.
3. **B-05c (readable seed with Phase Path → returns parsed list):** Write a seed with `Phase Path: 1 → 7 → 8 → Done`. Assert returns `["1", "7", "8", "Done"]`.
4. **B-05d (caller integration):** The downstream consumer (presumably in `dispatch_poller.py` near line 429) calls `parse_seed_phase_path(seed_path) or SCOPE_DEFAULTS[scope]`. Assert when seed is unreadable, the scope default is used AND the warning is emitted.

---

## 6. Out of Scope

- **Redesigning the retry system.** No change to `MAX_RETRY_ATTEMPTS = 3`. No change to the retry tag format `[RETRY N/3]`. No change to the rate-limit pause-flag mechanism itself.
- **New failure_reason taxonomy categories beyond `"agent_died"`.** A future story may introduce `"preflight_failure"` as a distinct category for telemetry; this story reuses the existing `"agent_died"` value to avoid coupling.
- **Frontend.** No ops-console UI surfacing of the new pre-flight classification. Morris's fleet-vigilance Check 5 already surfaces failed-stories flag files, which is sufficient for this story.
- **Backfilling STORY-323, STORY-592, STORY-644.** The remediation of those specific stuck stories is operational work (answer `QUESTION.md`, re-enqueue), not in scope here. This story prevents recurrence.
- **Refactoring `poll_loop` into smaller functions** beyond what's strictly required to make RL-05 testable. If the test can be written without refactor (via `monkeypatch` on the constants and `os` calls), no refactor occurs.
- **Cross-agent coordination protocols.** The needs_info fix is purely a server-side query guarantee — no inter-agent messaging changes.

---

## Test Criteria

**Gap 1 — Pre-flight failure classification:**

1. **E-01** `_report_fail(duration_seconds=2, exit_code=1, error_text=None)` does NOT POST `/api/dispatch` (i.e., no retry enqueue happens).
2. **E-02** `_report_fail(duration_seconds=120, exit_code=1, error_text=None)` DOES POST `/api/dispatch` (long-duration silent failure still retries — preserves existing behavior outside the preflight window).

**Gap 2 — Rate-limit resume:**

3. **RL-05a** When pause flag's reset timestamp is in the past, after one tick the flag file no longer exists.
4. **RL-05b** On the same tick that clears the flag, `poll_once` is invoked exactly once.

**Gap 3 — Server-side needs_info exclusion:**

5. **NI-S-01** `next_pending()` skips a `needs_info` row even when its `enqueued_at` is older than a `pending` row in the same table.
6. **NI-S-02** `next_pending()` returns `None` when the only rows present are `needs_info`.

**Gap 4 — Seed-path fallback:**

7. **B-05a** `parse_seed_phase_path("/nonexistent")` returns `None` AND emits exactly one `WARNING`-level log record mentioning the seed path.
8. **B-05c** `parse_seed_phase_path(seed_with_phase_path_line)` returns the parsed list and emits NO warnings.

All eight assertions must be RED at end of Phase 7 and GREEN at end of Phase 8.

---

## Validation

### Fix 1 — Pre-flight retry suppression

**Force-fail scenario:** Enqueue a story whose prompt deliberately triggers an instant SDK failure. Easiest method: register a story with `repo="nonexistent/repo-does-not-exist"` and a one-line prompt. The SDK subprocess will fail in <5s with no useful output.

**Expected log signature** (in `journalctl -u dispatch-poller`):

```
[DISPATCH] Fail STORY-XXX: 200
[DISPATCH] STORY-XXX pre-flight failure (duration=2s, exit=1, no output) — classified agent_died, skipping auto-retry
[DISPATCH] wrote failure flag to /home/hermes/state/<agent>/failed-stories/STORY-XXX.txt
```

**Negative signal (regression):** Three `[DISPATCH] auto-retry STORY-XXX (attempt N/3)` lines within 10 seconds → fix is broken.

### Fix 2 — Rate-limit resume

**Force-fail scenario:** Manually write `/var/run/dispatch-poller-paused-until` with a past timestamp string (e.g., `"2026-01-01 12:00 (UTC)"`). Wait one poll interval (60s).

**Expected log signature:**

```
[DISPATCH] reset window passed (2026-01-01 12:00 (UTC)) — unpausing
[DISPATCH] poll_once: claimed/skipped/...
```

**Verification command:**

```bash
test ! -f /var/run/dispatch-poller-paused-until && echo "RESUME OK"
```

### Fix 3 — needs_info server-side exclusion

**Force-fail scenario:** Manually transition a row to `needs_info` via psql:

```sql
UPDATE dispatch_items SET status='needs_info' WHERE story_id='STORY-XXX-test';
```

Then `curl -H "X-API-Key: ..." https://hermes.../api/dispatch/next` repeatedly. Verify the needs_info story is never returned. Cross-check with `SELECT story_id, status FROM dispatch_items WHERE status='needs_info'` — those rows should remain in `needs_info` indefinitely until manually requeued.

### Fix 4 — Seed-path fallback warning

**Force-fail scenario:** Enqueue a story with a `Phase Path: 1 → 7 → Done` declaration in the prompt but a deliberately wrong story-folder slug so the seed file path resolves to a non-existent file.

**Expected log signature:**

```
WARNING dispatch_poller: seed file /home/hermes/state/<agent>/work/.../seed.md not readable; falling back to scope defaults for scope=small
```

**Negative signal:** No warning emitted AND the agent ran scope-default phases when the prompt explicitly declared a phase path → fix is broken.

---

## 9. Dispatch Notes

- **Scope:** small (two surgical production edits + four tests).
- **Priority:** 95 (CRITICAL — production-critical queue path; STORY-644-class incidents are silent retry storms that consume queue time and pollute the failure-flag corpus).
- **Branch:** `story-725/queue-reliability-test-gaps` (off `main`).
- **Phase Path:** 1 → 7 → 8 → Done. (Skip Phase 6: design captured fully in §5 above. Skip Phase 9/10: no refinement or runbook needed for a small fix-and-test.)
- **Estimated test additions:** ~80 LOC across 4 test files.
- **Estimated production edits:** ~25 LOC in `dispatch_poller.py` (Fix 1 guard + Fix 4 parser stub) and 0 LOC in `dispatch_db_service.py` (existing query is correct; only test added).
- **Pre-merge gate:** all eight assertions in §7 GREEN; existing test suite remains GREEN; `pytest tests/deployment/ -q` runs in <30s.
- **Post-merge verification:** within 24h, run the four force-fail scenarios in §8 against staging Hermes and capture the expected log signatures.
- **Tracking docs to update on advance:** `.project` (Phase Routing → STORY-725), `backlog.md` (move STORY-725 to In Progress), `development-tasks.md` (add STORY-725 row), Monday.com task (comment with phase summaries).
- **Model policy:** Phase 1 done by Opus (this file). Phase 7 by Sonnet (test design + RED tests). Phase 8 by Sonnet (implementation to GREEN). No code review (Phase 8b) required at small scope.
