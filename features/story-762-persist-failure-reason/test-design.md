# Test Design — STORY-762: Persist `failure_reason` on Every Story Failure

## Scope
Small. Phase path: 1 → 7 → 8 → Done.

## Coverage Target
60% — new test file only; no existing production logic to cover beyond the contract.

## Context
Root cause (2026-04-29 incident, 11 stories STORY-007–STORY-017): every fail
path in the dispatch pipeline transitioned stories to `status='failed'` with
`failure_reason=NULL`. The dispatch poller's `_report_fail()` function sends
only `{"exit_code": exit_code}` in its POST body — `failure_reason` is never
passed to the API even though the route and DB service already support it.

Three source files have fail-emitting paths:
- `deployment/hermes/dispatch_poller.py` — `_report_fail()` and all its call sites
- `deployment/hermes/sdlc_phase_runner.py` — `branch_setup_failed`, phase-failure returns
- `tech_dev_agents/ops_console/services/dispatch_db_service.py` — `recover_stale_claims` Tier 2b

## Test File
`tests/deployment/test_failure_reason_always_populated.py`

**Approach:** Pure-Python AST scan + `hasattr` checks. No DB, no subprocess,
no network. Target: < 1 second total.

---

## Test Groups

### Group 1 — `TestEveryFailCallPassesReason` (SC-2, SC-4)

#### `test_report_fail_post_body_includes_failure_reason`

**Verifies:** `_report_fail()` in `dispatch_poller.py` passes `failure_reason`
in its POST body dict to `/api/dispatch/fail/{story_id}`.

**Why this matters:** The API route and DB service already accept
`failure_reason`, but the caller never sends it. This is the single
highest-impact fix — it closes the NULL gap for all paths that call
`_report_fail()`.

**Arrange:** Read `dispatch_poller.py` source; parse AST.

**Act:** Walk the `_report_fail` function body; find the `session.post()` call
whose URL contains `dispatch/fail`; inspect the `json=` keyword's dict keys.

**Assert:**
- `_report_fail` function exists in the file
- The POST body dict contains a `'failure_reason'` key
- Diagnostic on failure: file + line + current keys present

**Currently RED because:** `json={"exit_code": exit_code}` — no `failure_reason` key.

---

#### `test_every_report_fail_call_has_error_message`

**Verifies:** Every call site that invokes `_report_fail(...)` in
`dispatch_poller.py` passes the `error_message=` keyword argument.

**Why this matters:** Even after fixing the POST body, the failure reason
value must reach `_report_fail()`. If callers don't pass `error_message`,
`failure_reason` will be empty/None at every site.

**Arrange:** Read `dispatch_poller.py`; parse AST; check allowlist.

**Act:** Walk all `_report_fail(...)` call nodes; collect keyword arg names.

**Assert:** Every call includes `error_message` in its keyword set.
Allowlist entries (with mandatory `reason` field) are exempt.

**Diagnostic on failure:** `dispatch_poller.py:{line}: '{src_line}' — missing error_message kwarg`

**Currently RED because:** Multiple `_report_fail()` call sites (lines ~940,
~975, ~1015, ~1118, ~1124, ~1262, ~1286) omit `error_message`.

---

### Group 2 — `TestBranchSetupFailedPopulatesReason` (SC-3)

#### `test_branch_setup_failed_returns_error_string`

**Verifies:** The `except RuntimeError` block near `_ensure_branch()` in
`sdlc_phase_runner.py` does NOT return `(False, None, None)` — the error
string from `str(exc)` must be included in the return tuple.

**Why this matters:** This is the 2026-04-29 incident's canonical path.
The structured `branch_setup_failed` event was logged but `str(exc)` was
discarded. The caller (dispatch_poller) then had no error to pass to
`_report_fail`, so `failure_reason=NULL` reached the DB.

**Arrange:** Read `sdlc_phase_runner.py`; parse AST; identify all
`Return(Tuple[False, None, None])` nodes.

**Act:** For each 3-element `(False, None, None)` return, check a 15-line
backward window for `branch_setup_failed` context.

**Assert:** No `(False, None, None)` return exists within `branch_setup_failed` context.

**Expected fix:** `return (False, None, f"branch_setup_failed: {str(exc)[:500]}")`

**Currently RED because:** Line 2411 returns `(False, None, None)`.

---

### Group 3 — `TestPhaseTimeoutPopulatesReason` (SC-2)

#### `test_phase_timeout_return_includes_failure_reason`

**Verifies:** In `sdlc_phase_runner.py`, every `return (False, None, None)`
within a phase-failure context (near `rc != 0`, `TIMEOUT`, `FAILED`,
`rate_limited`, `TimeoutExpired`) has been replaced with a 3- or 4-element
return that includes a non-None failure reason.

**Why this matters:** Phase timeouts (`rc=-1, tail="timeout"`) and phase
failures (`rc != 0`) both reach `run_sdlc_phases` returning `(False, None, None)`.
The dispatch_poller's `_run_and_complete` then calls `_report_fail()` with
no error context. Every phase failure leaves `failure_reason=NULL`.

**Arrange:** Read `sdlc_phase_runner.py`; parse AST; identify 3-tuple
`(False, None, None)` returns.

**Act:** For each such return, check a 20-line backward window for
phase-failure keywords.

**Assert:** No `(False, None, None)` return exists in phase-failure context.

**Expected fix:** `return (False, None, f"phase_{phase_num}_failed: rc={rc} {tail[:200]}")`

**Currently RED because:** Lines 2643 (rate-limited) and 2652 (rc != 0) return
`(False, None, None)`.

---

### Group 4 — `TestSdkDiedPopulatesReason` (SC-2, AC-6)

#### `test_sdk_died_report_fail_has_error_message`

**Verifies:** Inside `_run_and_complete` (nested function in `dispatch_poller.py`),
every `_report_fail()` call passes `error_message=`.

**Why this matters:** The "genuine failure" branch (not rate-limited, not
`needs_info`, not `already_done`) calls `_report_fail()` without
`error_message` — this is the SDK-died path that produces `NULL` in DB.

**Arrange:** Read `dispatch_poller.py`; parse AST; find `_run_and_complete`
function body (may be nested inside `start_story`).

**Act:** Walk all `_report_fail(...)` calls inside `_run_and_complete`; collect
kwarg names.

**Assert:** Every call includes `error_message` in kwargs.

**Currently RED because:** The call at the "genuine failure" else-branch
(~line 1015) lacks `error_message`.

---

#### `test_recover_stale_claims_sets_agent_died_reason`

**Verifies:** `recover_stale_claims()` Tier 2b SQL UPDATE in
`dispatch_db_service.py` includes `failure_reason` in its SET clause.

**Why this matters:** Tier 2b (`stale_release_count >= 3`) sets
`status='failed'` via direct SQL UPDATE. The `fail()` service method is not
called, so `failure_reason` must be in the SQL itself. Without this, every
heartbeat-stale failure also produces `failure_reason=NULL`.

**Arrange:** Read `dispatch_db_service.py` source; locate the
`stale_release_count >= 3` SQL block.

**Act:** Inspect the 25-line context before `stale_release_count >= 3` for
`failure_reason` in the SET clause.

**Assert:** `failure_reason` appears in the SQL UPDATE near the Tier 2b block.

**Currently RED because:** The SQL UPDATE sets `status`, `claim_heartbeat_at`,
and `updated_at` — no `failure_reason` column.

---

### Group 5 — `TestFailureReasonTruncated` (AC-11)

#### `test_failure_reason_truncated_to_1000_chars`

**Verifies:** A helper function (`_truncate_failure_reason` or
`_build_failure_reason`) exists in `dispatch_poller.py` and returns a string
of ≤1000 characters even for long inputs.

**Why this matters:** Git stderr output, stack traces, and SDK logs can be
very long. AC-11 requires a 1000-char cap to prevent DB bloat and ensure
clean log entries.

**Arrange:** Import `dispatch_poller`; check for helper via `hasattr`.

**Act:** Call the helper with `prefix="sdk_died"` and a 2000-char detail string.

**Assert:**
- Helper exists (error if missing: "add def _truncate_failure_reason(...)")
- Return value is a `str`
- `len(result) <= 1000`

**Currently RED because:** No `_truncate_failure_reason` or
`_build_failure_reason` function exists in `dispatch_poller.py`.

---

#### `test_failure_reason_nonempty_on_common_prefixes`

**Verifies:** Common taxonomy prefixes (`branch_setup_failed`, `sdk_died`,
`phase_N_failed`, `quota_exceeded`) produce non-empty structured strings.

**Why this matters:** The self-healing stack reads `failure_reason` and routes
based on prefix. An empty or None value is treated as "unknown" and skips
classification.

**Arrange/Act/Assert:** Call helper with each expected prefix; assert non-empty
result containing the prefix.

**Currently RED because:** Helper doesn't exist yet.

---

### Group 6 — `TestNoCredentialsInFailureReason` (Security constraint)

#### `test_no_credentials_in_failure_reason`

**Verifies:** The failure_reason building logic strips credential patterns
(e.g. `API_KEY=value`, `Bearer <token>`) before the string is sent to the API.

**Why this matters:** Git stderr and SDK output may include env vars or tokens
if a subprocess inherits the environment. The security constraint says
`failure_reason` MUST NOT contain credentials, tokens, or PII.

**Arrange:** Import `dispatch_poller`; check for sanitizer function.

**Act:** Call the helper/sanitizer with a string containing
`OPS_CONSOLE_API_KEY=s3cr3t`.

**Assert:**
- Sanitizer exists
- The literal secret value `s3cr3t` is absent from the output

**Currently RED because:** No sanitizer exists; the helper itself doesn't exist yet.

---

## API Mock Verification
No Playwright tests in this story (frontend=false). No route mocks needed.

## Output-Variance Tests
Not applicable — this story adds string population, not data transformation.
The contract test (`test_every_fail_call_passes_reason`) verifies the presence
and structure of `failure_reason` values rather than output variance.

## Allowlist Pattern (STORY-760 reference)
The test file exports `ALLOWLIST: list[dict]`. Each entry must include:
```python
{
    "file": "dispatch_poller.py",
    "line": <int>,
    "reason": "<why this site is exempt from the failure_reason requirement>"
}
```
An allowlist entry without a `reason` field is itself a contract violation.

## RED State Confirmation
All 7 tests fail for the right reason (AssertionError with diagnostic message)
because:
1. `_report_fail()` POST body only contains `exit_code`
2. Multiple call sites lack `error_message`
3. `branch_setup_failed` returns `(False, None, None)`
4. Phase-failure returns are `(False, None, None)`
5. `_run_and_complete` calls lack `error_message`
6. `recover_stale_claims` Tier 2b SQL lacks `failure_reason`
7. No `_truncate_failure_reason`/`_build_failure_reason` helper exists
8. No sanitizer exists

## Checklist (Phase 7 Complete)

- [x] Happy paths covered (failure_reason populated correctly)
- [x] Error cases covered (failure paths send structured reason to API)
- [x] Edge cases covered (truncation at 1000 chars, credential sanitization)
- [x] Security requirements from seed (no creds/tokens in failure_reason)
- [x] Output-variance: N/A (string population, not transformation)
- [x] Integration-path tests: AST + import verify real code paths
- [x] DB constraint: Tier 2b SQL UPDATE verified
- [x] Allowlist pattern documented (STORY-760 ref)
- [x] `pytest --collect-only` discovers all tests
- [x] All tests FAIL with AssertionError (RED state, not import errors)
- [x] Every fail-emitting path has a test
- [x] Negative-case demo documented in Validation section of seed.md

## LLM Error-Prone Areas Covered
- Conditional errors: Boundary on 1000-char truncation tested
- Output format: Exact structure of failure_reason string verified
- Null/None boundary: Test verifies non-empty helper output
- Error observability: AC-10 (logging) covered by AC-2 (every fail call populates reason, which is logged at the route layer)
