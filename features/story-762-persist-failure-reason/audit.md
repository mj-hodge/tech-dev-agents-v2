# Audit — STORY-762: Persist `failure_reason` on Every Story Failure

## Scope

Files audited per SC-1:
- `deployment/hermes/dispatch_poller.py`
- `deployment/hermes/sdlc_phase_runner.py`
- `tech_dev_agents/ops_console/services/dispatch_db_service.py`

---

## Call Sites (SC-1, SC-2)

### Call site 1 — `dispatch_poller.py` `_report_fail()` POST body

| Field | Value |
|-------|-------|
| File | `deployment/hermes/dispatch_poller.py` |
| Location | Inside `_report_fail()`, `session.post(f"{base_url}/api/dispatch/fail/{story_id}", json=...)` |
| Previous state | `json={"exit_code": exit_code}` — no `failure_reason` |
| Fix | `json={"exit_code": exit_code, "failure_reason": error_message or None}` |
| Prefix | Supplied by caller via `error_message=` |

**Root cause of 2026-04-29 incident:** The route and DB service already accepted `failure_reason`, but this call never sent it. Every downstream consumer (STORY-701 classifier, requeue-failed, STORY-727) received NULL.

---

### Call site 2 — `dispatch_poller.py` gate-rejection path

| Field | Value |
|-------|-------|
| File | `deployment/hermes/dispatch_poller.py` |
| Location | Inside `_run_and_complete()`, `complete_status == 422` branch |
| Prefix | `gate_rejected_code_bug` |
| Detail | `"completion rejected by SDLC gate (exit_code=422)"` |

---

### Call site 3 — `dispatch_poller.py` rate-limit release fallback

| Field | Value |
|-------|-------|
| File | `deployment/hermes/dispatch_poller.py` |
| Location | Inside `_run_and_complete()`, `rate_limited` branch, release endpoint unreachable fallback |
| Prefix | `quota_exceeded` |
| Detail | `"rate_limit release failed — release endpoint unreachable"` |

---

### Call site 4 — `dispatch_poller.py` genuine-failure path

| Field | Value |
|-------|-------|
| File | `deployment/hermes/dispatch_poller.py` |
| Location | Inside `_run_and_complete()`, `else` branch (not rate-limited, not needs_info) |
| Prefix | `sdk_died` |
| Detail | `phase_reason` string from `run_sdlc_phases()` (e.g. `"phase_8_failed: rc=1 phase=implementation"`) |

---

### Call site 5 — `dispatch_poller.py` rate-limited legacy single-shot path

| Field | Value |
|-------|-------|
| File | `deployment/hermes/dispatch_poller.py` |
| Location | Inside `_run_and_complete()`, `rate_limited_until` block |
| Prefix | `quota_exceeded` |
| Detail | `"rate_limited — pause flag written, re-pend via pause mechanism"` |

---

### Call site 6 — `dispatch_poller.py` finally block (error=True)

| Field | Value |
|-------|-------|
| File | `deployment/hermes/dispatch_poller.py` |
| Location | `finally:` block, `if error:` branch |
| Prefix | `sdk_died` |
| Detail | Exception message or `phase_reason` or `f"rc={rc}"` |

---

### Call site 7 — `dispatch_poller.py` finally block (no commit SHA)

| Field | Value |
|-------|-------|
| File | `deployment/hermes/dispatch_poller.py` |
| Location | `finally:` block, `else:` (no commit SHA) branch |
| Prefix | `no_commit_sha` |
| Detail | `f"session completed but no pushed branch/commit found rc={rc}"` |

---

### Call site 8 — `sdlc_phase_runner.py` `branch_setup_failed`

| Field | Value |
|-------|-------|
| File | `deployment/hermes/sdlc_phase_runner.py` |
| Location | `except RuntimeError as exc:` block after `_ensure_branch()` call |
| Previous state | `return False, None, None` — error string discarded |
| Fix | `return False, None, f"branch_setup_failed: {str(exc)[:500]}"` |
| Prefix | `branch_setup_failed` |

**This is the canonical 2026-04-29 incident path.** STORY-007–STORY-017 all failed here with
`failure_reason=NULL` because the error string from the `RuntimeError` was never propagated.

---

### Call site 9 — `sdlc_phase_runner.py` rate-limited phase return

| Field | Value |
|-------|-------|
| File | `deployment/hermes/sdlc_phase_runner.py` |
| Location | Inside phase loop, `if rc == -429:` branch |
| Previous state | `return False, None, None` |
| Fix | `return False, None, f"quota_exceeded: phase={phase_num} ({phase_name}) rate_limited rc={rc}"` |
| Prefix | `quota_exceeded` |

---

### Call site 10 — `sdlc_phase_runner.py` non-zero rc phase return

| Field | Value |
|-------|-------|
| File | `deployment/hermes/sdlc_phase_runner.py` |
| Location | Inside phase loop, `if rc != 0:` branch |
| Previous state | `return False, None, None` |
| Fix | `return False, None, f"phase_{phase_num}_failed: rc={rc} phase={phase_name}"` |
| Prefix | `phase_N_failed` |

---

### Call site 11 — `dispatch_db_service.py` Tier 2b heartbeat-stale SQL UPDATE

| Field | Value |
|-------|-------|
| File | `tech_dev_agents/ops_console/services/dispatch_db_service.py` |
| Location | `recover_stale_claims()`, SQL UPDATE with `stale_release_count >= 3` WHERE clause |
| Previous state | `SET status = 'failed', claim_heartbeat_at = NULL, updated_at = now()` — no `failure_reason` |
| Fix | Added `failure_reason = 'agent_died'` to SET clause |
| Prefix | `agent_died` (static literal — no dynamic data) |

---

## Failure Reason Taxonomy (Updated)

| Prefix | Meaning | Source |
|--------|---------|--------|
| `branch_setup_failed` | Git branch setup failed before any phases ran | STORY-762 (new) |
| `gate_rejected_code_bug` | SDLC completion gate rejected the story | STORY-701 / STORY-762 |
| `quota_exceeded` | Claude API rate limit hit | STORY-701 / STORY-762 |
| `sdk_died` | Claude SDK exited non-zero (genuine phase failure) | STORY-741 / STORY-762 |
| `phase_N_failed` | Phase N returned non-zero rc | STORY-762 (new) |
| `no_commit_sha` | Session completed but no branch/commit was pushed | STORY-762 (new) |
| `agent_died` | VM heartbeat stale for 3+ release cycles | STORY-762 (new) |

---

## Helper Functions Added

### `_truncate_failure_reason(prefix, detail)` — `dispatch_poller.py`

```python
def _truncate_failure_reason(prefix: str, detail: str) -> str:
```

- Sanitizes credential-like patterns (`ENV_VAR=value`, `Bearer <token>`) via regex
- Caps output at 1000 characters (AC-11)
- Returns `f"{prefix}: {sanitized}"[:1000]`

---

## Backfill Measurement (SC-5)

**Query to measure existing NULL rows:**

```sql
SELECT
    COUNT(*) AS total_failed_null_reason,
    MIN(created_at) AS earliest,
    MAX(created_at) AS latest
FROM dispatch_items
WHERE status = 'failed'
  AND failure_reason IS NULL;
```

**Sample rows:**

```sql
SELECT story_id, status, created_at, updated_at
FROM dispatch_items
WHERE status = 'failed'
  AND failure_reason IS NULL
ORDER BY updated_at DESC
LIMIT 10;
```

**Note:** No backfill mutation is performed in this PR (out of scope per seed.md § Out of Scope).
A follow-up STORY-765 should be filed if backfill is needed urgently.

The 2026-04-29 incident produced 11 rows (STORY-007 through STORY-017) with `failure_reason=NULL`.
These remain as historical data debt. All new failures after this PR merges will have `failure_reason` populated.

---

## Security Constraints Verified

- `_truncate_failure_reason` strips patterns matching `[A-Z_]{4,}(KEY|TOKEN|SECRET|...)[=:\s]+\S+`
  and `Bearer <token>` before including any subprocess output in `failure_reason`.
- Git stderr and SDK output are truncated at 500 chars for `branch_setup_failed`, 1000 chars overall.
- No env vars, credentials, or tokens will appear in `failure_reason` strings stored in DB.

---

## Negative-Case Demo

To verify the contract test fails loudly when a call site is non-compliant:

```bash
# 1. Temporarily break one call site (remove error_message from genuine-failure path)
# In dispatch_poller.py, edit the else-branch _report_fail call to remove error_message=

# 2. Run the contract test
pytest tests/deployment/test_failure_reason_always_populated.py::TestSdkDiedPopulatesReason::test_sdk_died_report_fail_has_error_message -v

# Expected output (FAIL with diagnostic):
# E  AssertionError: Found 1 _report_fail() call(s) in _run_and_complete without error_message=:
# E    Violation: dispatch_poller.py:NNNN: '_report_fail(...)' — failure_reason=NULL will reach DB

# 3. Revert the edit → test returns GREEN
```
