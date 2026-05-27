# Test Design — STORY-726: Parallel Agent Coordination

**Phase:** 7 (Test Design — RED state)
**Scope:** Small/Medium
**Date:** 2026-04-26

---

## Overview

Eight tests covering the three gaps introduced by this story. All tests are written
before implementation and are expected to fail (RED) until Phase 8.

Test file: `tests/deployment/test_parallel_coordination_726.py`

---

## T1 — Jitter fires on 409

**Gap:** 1 (backoff)
**File under test:** `deployment/hermes/dispatch_poller.py`

Assert that when `CLAIM_BACKOFF=1` and `poll_once` receives a 409 from the claim
endpoint, `time.sleep` is called with a value >= 0.5 (the minimum jitter bound).
Uses `unittest.mock.patch` on `time.sleep` and a `responses` / `requests_mock`
mock server that returns 409 on the first claim attempt then 200 to allow the
loop to exit.

---

## T2 — No jitter with CLAIM_BACKOFF=0

**Gap:** 1 (backoff flag)
**File under test:** `deployment/hermes/dispatch_poller.py`

Assert that with `CLAIM_BACKOFF=0` the same 409 → 200 sequence never calls
`time.sleep` with a positive value (i.e., either sleep is not called at all or
is called only with 0). The retry path must not introduce any latency when the
feature flag is off.

---

## T3 — `preferred_scope` forwarded by poller

**Gap:** 2 (scope-aware routing, agent side)
**File under test:** `deployment/hermes/dispatch_poller.py`

Assert that when `AGENT_PREFERRED_SCOPE=backend` is set in the environment,
the GET request to `/api/dispatch/next` includes `preferred_scope=backend` as a
query parameter. Uses `requests_mock` to capture the actual URL called.

---

## T4 — Scope-aware SQL soft-preference

**Gap:** 2 (scope-aware routing, server DB service)
**File under test:** `tech_dev_agents/ops_console/services/dispatch_db_service.py`

Assert that `next_pending(preferred_scope="small")` executes a query that
contains `CASE WHEN scope` (the soft-preference ORDER BY clause), while
`next_pending(preferred_scope=None)` executes a query that does NOT contain that
clause. Uses a mocked asyncpg connection pool; does not require a live database.

---

## T5 — `preferred_scope=None` gives FIFO ordering

**Gap:** 2 (scope-aware routing, no scope given)
**File under test:** `tech_dev_agents/ops_console/services/dispatch_db_service.py`

Assert that when `preferred_scope=None`, the SQL sent to the DB contains
`ORDER BY priority DESC, enqueued_at ASC` and does NOT contain any `CASE WHEN`.
Companion to T4 — ensures the unscoped path is unchanged.

---

## T6 — `_record_completion` writes atomically

**Gap:** 3 (durable completion guard)
**File under test:** `deployment/hermes/dispatch_poller.py`

Assert that after calling `_record_completion("STORY-999")` with
`COMPLETIONS_FILE` pointing to a temp path:
1. The file exists and is valid JSON.
2. The JSON contains `"STORY-999"` as a key.
3. A second call with `"STORY-888"` appends without clobbering: both keys are
   present in the resulting file.

Uses `tmp_path` pytest fixture and patches `_COMPLETIONS_FILE` via environment.

---

## T7 — `_load_recent_completions` returns empty dict on missing file

**Gap:** 3 (durable completion guard)
**File under test:** `deployment/hermes/dispatch_poller.py`

Assert that when `COMPLETIONS_FILE` points to a path that does not exist,
`_load_recent_completions()` returns an empty `set` (no exception raised). Also
assert that when the file exists with one entry that is less than 24 hours old,
the returned set contains that story ID.

---

## T8 — Story in recent-completions is skipped

**Gap:** 3 (durable completion guard, integration)
**File under test:** `deployment/hermes/dispatch_poller.py`

Assert that when `_load_recent_completions()` returns `{"STORY-700"}` and
`_LOCALLY_COMPLETED` is pre-seeded with that set, `poll_once` skips the story
returned by `/api/dispatch/next` (logs the "already completed locally" message
and does not POST to `/api/dispatch/claim`).

---

## RED / GREEN Status

| Test | Function targeted | RED reason |
|------|-------------------|------------|
| T1 | `poll_once` 409 branch with `time.sleep` | `_CLAIM_BACKOFF` constant and `time.sleep` call don't exist yet |
| T2 | `poll_once` 409 branch, backoff flag off | Same |
| T3 | `poll_once` GET params | `_AGENT_PREFERRED_SCOPE` + `params=` not yet added |
| T4 | `next_pending(preferred_scope=...)` | Parameter doesn't exist on the method |
| T5 | `next_pending()` no-scope path | Will pass once T4 implemented; captures regression risk |
| T6 | `_record_completion` | Function doesn't exist yet |
| T7 | `_load_recent_completions` | Function doesn't exist yet |
| T8 | `poll_once` skip-if-completed integration | Depends on T6/T7 helpers |
