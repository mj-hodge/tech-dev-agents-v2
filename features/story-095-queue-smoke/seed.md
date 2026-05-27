# Seed: Dispatch Queue Smoke Tests

**Story:** STORY-095
**Date:** 2026-05-01
**Scope:** Small
**Phase Path:** 1 -> 7 -> 8 -> Done
**Assignee:** Hermes
**Frontend:** false

---

## Problem Statement

The predeploy gate runs `pytest -m smoke` to validate critical paths before deployment (see `tests/predeploy/check_smoke.sh` and `check_smoke_tests.sh`). Today the entire dispatch queue system -- the backbone of autonomous agent work execution -- has **zero smoke-marked tests**. Only one smoke test exists in the whole repo (`test_smoke_runtime_identity_contract` in `test_runtime_identity.py`).

This means a broken dispatch queue (enqueue, claim, complete, status transitions) could pass predeploy and ship to production. The 2026-04-20 incident (broken `-w` flag burned tokens for hours) showed how important fast smoke detection is -- `push-code.sh` now has its own runtime smoke test, but the Python test suite's `@pytest.mark.smoke` gate has no queue coverage.

## Solution

Add `@pytest.mark.smoke` markers to a focused set of dispatch queue tests that exercise the critical happy path: enqueue -> claim -> complete. These tests must:

1. **Run fast** (<2 seconds total) -- smoke tests gate every deploy
2. **Cover the critical path** -- the enqueue/claim/complete lifecycle that agents depend on
3. **Use in-memory/file-based service** -- no PostgreSQL dependency (the JSON-file `DispatchQueueService` is the right target since it runs without infrastructure)
4. **Validate the local WorkQueue** -- the agent-side `~/.hermes/work-queue.json` FIFO queue that survives restarts

The tests will be added to a new file `tests/test_dispatch_queue_smoke.py` with the `@pytest.mark.smoke` decorator, keeping them isolated and easy to run via `pytest -m smoke`.

## Acceptance Criteria

| ID | Criterion |
|----|-----------|
| AC-1 | `pytest -m smoke` collects and runs dispatch queue smoke tests |
| AC-2 | Smoke tests cover enqueue -> claim -> complete lifecycle (central queue) |
| AC-3 | Smoke tests cover local WorkQueue enqueue -> set_active -> complete lifecycle |
| AC-4 | Smoke tests cover FIFO ordering (first enqueued = first claimed) |
| AC-5 | Smoke tests cover error paths: duplicate enqueue rejection, claim-on-empty-queue |
| AC-6 | All smoke tests run in <2 seconds with no external dependencies (no PostgreSQL, no HTTP) |
| AC-7 | `tests/predeploy/check_smoke.sh` passes (exits 0) |

## Files to Create/Change

| File | Action |
|------|--------|
| `tests/test_dispatch_queue_smoke.py` | Create -- smoke-marked dispatch queue tests |
| `features/story-095-queue-smoke/seed.md` | Create -- this file |
| `features/story-095-queue-smoke/test-design.md` | Create -- test design |

## Test Criteria

| Test | Validates |
|------|-----------|
| `test_smoke_enqueue_claim_complete_lifecycle` | AC-2: central queue happy path end-to-end |
| `test_smoke_local_workqueue_lifecycle` | AC-3: local WorkQueue enqueue → set_active → complete |
| `test_smoke_fifo_ordering` | AC-4: first-enqueued = first-claimed ordering guarantee |
| `test_smoke_duplicate_enqueue_rejected` | AC-5: duplicate enqueue raises / returns error |
| `test_smoke_claim_empty_queue` | AC-5: claim on empty queue returns None / raises |
| `test_smoke_collection_by_marker` | AC-1: `pytest -m smoke` collects all smoke tests |
| `test_smoke_runs_under_2_seconds` | AC-6: total wall-clock < 2 s, no external deps |

All tests are mock-only / file-based — no PostgreSQL, no HTTP, no subprocess.

## Validation

After merge:

1. **Predeploy gate:** `tests/predeploy/check_smoke.sh` exits 0 (AC-7).
2. **Marker collection:** `pytest -m smoke --collect-only` lists all new dispatch queue smoke tests (AC-1).
3. **Speed budget:** `pytest -m smoke` completes in < 2 seconds (AC-6).
4. **CI green:** "Python contract + unit tests" check passes on the PR.

## Out of Scope

- Adding smoke markers to existing tests (focus on new, purpose-built smoke tests)
- PostgreSQL-backed `DispatchDBService` smoke tests (require DB infrastructure)
- Dispatch poller smoke tests (require subprocess mocking, too slow/complex for smoke)
- API route smoke tests (require FastAPI test client setup)
- Modifying push-code.sh runtime smoke test (separate concern)
