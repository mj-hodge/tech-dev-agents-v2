# STORY-740 Test Design — Dispatch State-Machine Contract

## Overview

| Field | Value |
|-------|-------|
| Scope | Small |
| Coverage target | 70% (contract surface coverage — every Pydantic model, SQL CHECK, index) |
| Test file | `tests/ops_console/test_dispatch_contract.py` |
| Stub module | `tech_dev_agents/ops_console/models/dispatch_state.py` |
| Total tests | 13 (11 RED, 2 GREEN meta-diagnostic) |

## Test Structure

```
tests/ops_console/
└── test_dispatch_contract.py        # 13 tests, 7 groups (A–G)

tech_dev_agents/ops_console/models/
└── dispatch_state.py                # Phase 7 stub (empty frozensets); Phase 8 populates
```

## Test Groups

### Group A — Canonical Module Internal Consistency (6 tests)

| Test | What It Verifies | RED Reason |
|------|-----------------|------------|
| `test_states_is_populated` | STATES is non-empty | Stub has `frozenset()` |
| `test_states_partition_into_active_and_terminal` | STATES == ACTIVE ∪ TERMINAL, no overlap | Stub empty |
| `test_is_active_agrees_with_active_states` | `is_active()` returns True for exactly ACTIVE_STATES | Stub empty |
| `test_is_terminal_agrees_with_terminal_states` | `is_terminal()` returns True for exactly TERMINAL_STATES | Stub empty |
| `test_field_requirements_reference_only_known_states` | FIELD_REQUIREMENTS keys ⊂ STATES | Stub empty |
| `test_queue_bucket_map_references_only_active_states` | QUEUE_BUCKET_MAP keys ⊂ ACTIVE_STATES | Stub empty |

### Group B — DispatchStatusEnum vs Canonical (1 test)

| Test | What It Verifies | RED Reason |
|------|-----------------|------------|
| `test_enum_values_equal_canonical_states` | Enum member values == STATES (SC-2, AC-2) | Stub STATES empty |

### Group C — DispatchItem Fields vs Canonical (1 test)

| Test | What It Verifies | RED Reason |
|------|-----------------|------------|
| `test_dispatch_item_covers_field_requirements` | DispatchItem has every field in FIELD_REQUIREMENTS (SC-3, AC-4) | Stub FIELD_REQUIREMENTS empty |

### Group D — DispatchQueueResponse Buckets vs Canonical (1 test)

| Test | What It Verifies | RED Reason |
|------|-----------------|------------|
| `test_every_active_state_has_queue_bucket` | Each active state → a bucket field on DispatchQueueResponse (SC-3, AC-3) | Stub ACTIVE_STATES empty |

### Group E — SQL CHECK Constraint vs Canonical (1 test)

| Test | What It Verifies | RED Reason |
|------|-----------------|------------|
| `test_check_constraint_states_equal_canonical` | Latest migration CHECK enum == STATES (SC-4, AC-5) | Stub STATES empty |

### Group F — Unique Active Index vs Canonical (1 test)

| Test | What It Verifies | RED Reason |
|------|-----------------|------------|
| `test_active_index_states_equal_canonical` | Latest migration index WHERE clause == ACTIVE_STATES (SC-4, AC-6) | Stub ACTIVE_STATES empty |

### Group G — Diagnostic Message Quality (2 tests — GREEN meta-tests)

| Test | What It Verifies | Status |
|------|-----------------|--------|
| `test_simulated_enum_drift_names_surface` | Drift message mentions "DispatchStatusEnum" and the phantom state (AC-7) | GREEN (format validation) |
| `test_simulated_item_field_drift_names_surface` | Drift message mentions "DispatchItem" and the missing field (AC-7) | GREEN (format validation) |

## AC → Test Mapping

| AC | Test(s) |
|----|---------|
| AC-1 (canonical module exists) | Group A — all 6 tests import from `dispatch_state.py` |
| AC-2 (enum validated) | `test_enum_values_equal_canonical_states` |
| AC-3 (queue response buckets) | `test_every_active_state_has_queue_bucket` |
| AC-4 (DispatchItem fields) | `test_dispatch_item_covers_field_requirements` |
| AC-5 (SQL CHECK) | `test_check_constraint_states_equal_canonical` |
| AC-6 (active index) | `test_active_index_states_equal_canonical` |
| AC-7 (specific diagnostics) | Group G diagnostic tests + custom assertion messages in all tests |
| AC-8 (single-run catch-all) | All 11 contract tests run in one `pytest` invocation |
| AC-9 (zero regressions) | Verified at Phase 8 completion |
| AC-10 (PR checklist) | Phase 8 deliverable |
| AC-11 (failure log names surface) | Every assertion message names the surface (e.g., "DispatchStatusEnum diverges...") |

## SQL Parsing Strategy

The contract test parses migration `.sql` files on disk (no DB connection):

1. **CHECK constraint**: regex extracts `ADD CONSTRAINT *status* CHECK (status IN (...))` from all migrations; takes the last match (latest migration wins).
2. **Active index**: regex extracts `CREATE UNIQUE INDEX ... uq_story_*active_idx ... WHERE status IN (...)` from all migrations; takes the last match.

Migration directories scanned: `scripts/migrations/`, `sql/`.

## Known Finding: Active Index Drift

Migration `008_composite_key_story_repo.sql` defines `uq_story_repo_active_idx` with `WHERE status IN ('pending', 'claimed', 'in_review', 'paused')` — **missing `needs_info`**. Migration `007_needs_info_state.sql` had correctly included it, but `008` recreated the index without it.

This is a latent bug that the contract test is designed to catch. Per SC-6, Phase 8 must fix this inconsistency so the contract test goes GREEN. Per the Escalation Contract, if fixing this requires a new migration, the implementer should ask before proceeding.

## API Mock Verification

N/A — this story has no endpoints, no Playwright tests, no route mocks.

## Gates Applicability

| Gate | Applies? | Notes |
|------|----------|-------|
| Gate 1 (Null/None) | No | No service methods with optional params |
| Gate 2a (External API isolation) | No | No external APIs |
| Gate 2b (External API degradation) | No | No external APIs |
| Gate 3 (DB constraint) | No | No new models/columns |
| Gate 4 (Input validation) | No | No user-influenced input |
| Gate 5 (Creation path matrix) | No | No creation paths |
| Gate 6 (Tenant isolation) | No | No endpoints |
| Gate 7 (File upload) | No | No uploads |
| Gate 8 (Migration) | No | No new migrations in this story |
| Gate 9 (Failure recovery) | No | No stateful operations |
| Gate 10 (Error observability) | No | No except blocks in new production code |
| Gate 11 (Fixture compilation) | Yes | Stub `dispatch_state.py` uses frozenset/dict — validated by import |
| Gate 12 (Integration smoke) | No | No external adapters |

## RED State Verification

```
$ python3 -m pytest tests/ops_console/test_dispatch_contract.py -v --tb=short
11 failed, 2 passed in 0.15s
```

All 11 failures are AssertionError with descriptive messages (not import errors):
- "dispatch_state.STATES is empty -- canonical module not yet populated"
- "FIELD_REQUIREMENTS is empty -- canonical module not yet populated"
- "ACTIVE_STATES is empty -- canonical module not yet populated"

2 passes are diagnostic meta-tests (message format validation).

## Follow-ups

- **STORY-741 candidate**: `dispatch_db_service.py` SQL string literals (e.g., `status = 'claimed'`) should reference the canonical enum. Out of scope for this story.
- **Frontend TypeScript enum consolidation**: separate story.
- **Mermaid state diagram**: could be added to `state-machine.md` if trivial, but not required.
