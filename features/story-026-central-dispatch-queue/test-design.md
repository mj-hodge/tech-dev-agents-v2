# Test Design: Central Dispatch Queue

> Phase 7 — Test Design
> Story: STORY-026 — Central Dispatch Queue
> Date: 2026-04-08
> Scope: Medium
> Target: RED state — all tests written, all failing

---

## Test Strategy

Two test files covering the dispatch queue:

1. **`tests/ops_console/test_dispatch_service.py`** — Unit tests for `DispatchQueueService` (file I/O, locking, stale recovery)
2. **`tests/ops_console/test_routes_dispatch.py`** — API integration tests for all 5 dispatch endpoints

### Traceability Matrix

| AC | Test IDs | Description |
|----|----------|-------------|
| AC-1 | T01, T02, T03, T04 | POST /api/dispatch adds story to queue |
| AC-2 | T10, T11, T12 | GET /api/dispatch/queue returns FIFO list |
| AC-3 | T13, T14 | GET /api/dispatch/next returns oldest pending |
| AC-4 | T15, T16, T17 | POST /api/dispatch/claim marks as claimed |
| AC-5 | — | Agent polling (integration, not unit-testable here) |
| AC-6 | — | Direct Teams unchanged (no change = no test) |
| AC-7 | — | Dashboard (frontend, tested separately) |
| AC-8 | — | Dispatch skill (skill-level test, deferred) |
| AC-9 | T16 | Double-claim returns 409 |
| AC-10 | T30, T31, T32 | Stale claim recovery |

---

## Test File 1: Service Unit Tests

**File:** `tests/ops_console/test_dispatch_service.py`

| ID | Test | Asserts |
|----|------|---------|
| T20 | `test_load_creates_file_if_missing` | New service creates empty queue file |
| T21 | `test_load_returns_empty_on_missing_file` | load() returns empty queue structure |
| T22 | `test_save_and_load_roundtrip` | save() persists, load() reads back identical data |
| T23 | `test_save_updates_last_updated` | last_updated field is set on save |
| T24 | `test_atomic_write_creates_no_temp_files` | No .tmp files left after save |
| T25 | `test_load_handles_corrupt_json` | Returns empty queue on invalid JSON |
| T30 | `test_recover_stale_claims_moves_expired` | Claims older than 5 min return to pending |
| T31 | `test_recover_stale_claims_keeps_fresh` | Claims under 5 min stay claimed |
| T32 | `test_recover_stale_claims_empty_queue` | No-op on empty queue |

## Test File 2: Route Integration Tests

**File:** `tests/ops_console/test_routes_dispatch.py`

| ID | Test | Asserts |
|----|------|---------|
| T01 | `test_enqueue_story_success` | 201, item in response, queue_depth=1 |
| T02 | `test_enqueue_duplicate_returns_409` | 409 on same story_id |
| T03 | `test_enqueue_queue_full_returns_422` | 422 when 50 pending items |
| T04 | `test_enqueue_validates_story_id_format` | 422 on invalid story_id pattern |
| T10 | `test_list_queue_empty` | 200, pending=[], claimed=[] |
| T11 | `test_list_queue_with_items` | 200, correct counts |
| T12 | `test_list_queue_fifo_order` | First enqueued = first in list |
| T13 | `test_next_returns_oldest_pending` | 200, returns first item |
| T14 | `test_next_empty_returns_204` | 204 No Content |
| T15 | `test_claim_success` | 200, story moves to claimed |
| T16 | `test_claim_already_claimed_returns_409` | 409 Conflict |
| T17 | `test_claim_not_found_returns_404` | 404 for non-existent story |
| T18 | `test_cancel_pending_success` | 200, story removed |
| T19 | `test_cancel_claimed_returns_409` | 409, can't cancel claimed |
| T05 | `test_cancel_not_found_returns_404` | 404 for non-existent story |
| T06 | `test_all_endpoints_require_auth` | 401 without API key |
