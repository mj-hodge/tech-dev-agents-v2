# Test Design: STORY-095 Dispatch Queue Smoke Tests

**Story:** STORY-095
**Phase:** 7 (Test Design)
**Date:** 2026-05-01
**Status:** RED (tests written, implementation pending)

---

## Test Strategy

All tests live in `tests/test_dispatch_queue_smoke.py` and are marked with `@pytest.mark.smoke`. They use the file-based `DispatchQueueService` and the local `WorkQueue` -- both operate on temp files with no external dependencies.

Each test is self-contained: creates a fresh service instance backed by a `tmp_path` file, exercises one concern, and asserts the expected state. Total runtime target: <2 seconds.

## Test Matrix

| ID | Test | Covers |
|----|------|--------|
| SM-01 | `test_smoke_enqueue_creates_pending_item` | AC-1, AC-2 |
| SM-02 | `test_smoke_enqueue_claim_complete_lifecycle` | AC-1, AC-2 |
| SM-03 | `test_smoke_fifo_ordering` | AC-1, AC-4 |
| SM-04 | `test_smoke_duplicate_enqueue_same_pending` | AC-1, AC-5 |
| SM-05 | `test_smoke_claim_empty_queue_returns_none` | AC-1, AC-5 |
| SM-06 | `test_smoke_local_work_queue_lifecycle` | AC-1, AC-3 |
| SM-07 | `test_smoke_local_work_queue_fifo` | AC-1, AC-3, AC-4 |
| SM-08 | `test_smoke_stale_claim_recovery` | AC-1, AC-2 |

## Test Details

### SM-01: `test_smoke_enqueue_creates_pending_item`
**Given** an empty dispatch queue
**When** a story is enqueued with story_id, repo, scope, and prompt
**Then** the queue has exactly 1 pending item with matching story_id

### SM-02: `test_smoke_enqueue_claim_complete_lifecycle`
**Given** a story enqueued in the dispatch queue
**When** the story is claimed by an agent, then completed
**Then** the pending list is empty, the claimed list is empty, and the completed list contains the story

### SM-03: `test_smoke_fifo_ordering`
**Given** three stories enqueued in order (A, B, C)
**When** the pending list is inspected
**Then** the first item is A, second is B, third is C (FIFO order preserved)

### SM-04: `test_smoke_duplicate_enqueue_same_pending`
**Given** a story already pending in the queue
**When** the same story_id is enqueued again
**Then** the queue still has exactly 1 pending item (duplicate rejected or idempotent)

### SM-05: `test_smoke_claim_empty_queue_returns_none`
**Given** an empty dispatch queue with no pending items
**When** a claim is attempted
**Then** no item is returned (None or empty result)

### SM-06: `test_smoke_local_work_queue_lifecycle`
**Given** a fresh local WorkQueue
**When** a story is enqueued, set_active, then completed
**Then** the queue is empty (resume returns None, list returns [])

### SM-07: `test_smoke_local_work_queue_fifo`
**Given** two stories enqueued in order (X, Y)
**When** the queue is listed
**Then** X appears before Y (FIFO order)

### SM-08: `test_smoke_stale_claim_recovery`
**Given** a claimed item with a timestamp older than STALE_CLAIM_SECONDS
**When** `recover_stale_claims()` is called
**Then** the item moves back to pending

## Coverage Map

| AC | Tests |
|----|-------|
| AC-1 (smoke marker collected) | SM-01 through SM-08 |
| AC-2 (central queue lifecycle) | SM-01, SM-02, SM-08 |
| AC-3 (local WorkQueue lifecycle) | SM-06, SM-07 |
| AC-4 (FIFO ordering) | SM-03, SM-07 |
| AC-5 (error paths) | SM-04, SM-05 |
| AC-6 (fast, no deps) | All -- file-based, tmp_path |
| AC-7 (predeploy passes) | Verified by running `pytest -m smoke` |

## Run Commands

```bash
# Run only smoke tests
pytest -m smoke --tb=short -q

# Run only dispatch queue smoke tests
pytest tests/test_dispatch_queue_smoke.py --tb=short -q

# Verify predeploy gate
bash tests/predeploy/check_smoke.sh
```
