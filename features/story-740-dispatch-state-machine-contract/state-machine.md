# Dispatch State Machine — Developer Guide

## Single Source of Truth

All dispatch states, transitions, per-state fields, and queue buckets are defined in one module:

```
tech_dev_agents/ops_console/models/dispatch_state.py
```

The contract test (`tests/ops_console/test_dispatch_contract.py`) validates that every downstream surface agrees with this module. CI fails any PR that introduces drift.

## Current States

| State | Category | Queue Bucket | Description |
|-------|----------|-------------|-------------|
| `pending` | active | `pending` | Awaiting claim by an agent |
| `claimed` | active | `in_progress` | Actively being worked by an agent |
| `in_review` | active | `in_review` | Agent submitted work for review |
| `paused` | active | `paused` | Work suspended (manual or automatic) |
| `needs_info` | active | `needs_info` | Blocked on human input (QUESTION.md) |
| `completed` | terminal | — | Story finished successfully |
| `cancelled` | terminal | — | Story cancelled by operator |
| `failed` | terminal | — | Story failed after exhausting retries |

## How to Add a New Dispatch State

This is a single-place edit in `dispatch_state.py`, plus updates to the surfaces the contract test enforces.

### Checklist

1. **`dispatch_state.py`** — Add the state string to `ACTIVE_STATES` or `TERMINAL_STATES` (it will automatically appear in `STATES`).
2. **`dispatch_state.py`** — Add an entry to `TRANSITIONS` for the new state (from-state -> to-states) and update existing states that can transition *to* the new state.
3. **`dispatch_state.py`** — If the state requires specific fields on `DispatchItem` when entered, add an entry to `FIELD_REQUIREMENTS`.
4. **`dispatch_state.py`** — If the state is active (non-terminal), add a queue bucket mapping to `QUEUE_BUCKET_MAP`, or add it to `QUEUE_BUCKET_KNOWN_OMISSIONS` if it intentionally has no bucket.
5. **`responses.py`** — Add the state to `DispatchStatusEnum`.
6. **`responses.py`** — If `FIELD_REQUIREMENTS` references new fields, add them to `DispatchItem`.
7. **`responses.py`** — If a new queue bucket is needed, add the field to `DispatchQueueResponse`.
8. **New migration** — Add the state to the `CHECK (status IN (...))` constraint and, if active, to the `uq_story_repo_active_idx WHERE status IN (...)` clause.
9. **Run** `pytest tests/ops_console/test_dispatch_contract.py -v` — all 13 tests must pass.

### What the Contract Test Checks

| Surface | Test | What It Asserts |
|---------|------|----------------|
| `DispatchStatusEnum` | `test_enum_values_equal_canonical_states` | Enum member values == `STATES` |
| `DispatchItem` | `test_dispatch_item_covers_field_requirements` | Model has every field in `FIELD_REQUIREMENTS` |
| `DispatchQueueResponse` | `test_every_active_state_has_queue_bucket` | Each active state maps to a response field (or is in `QUEUE_BUCKET_KNOWN_OMISSIONS`) |
| SQL CHECK constraint | `test_check_constraint_states_equal_canonical` | Latest migration CHECK enum == `STATES` |
| Active unique index | `test_active_index_states_equal_canonical` | Latest migration index WHERE clause == `ACTIVE_STATES` |
| Internal consistency | Group A (6 tests) | STATES partitions cleanly, helpers agree, no dangling references |
| Diagnostic quality | Group G (2 tests) | Failure messages name the diverging surface and the diff |

## How to Add a Per-State Field

1. Add the field name to the relevant state's entry in `FIELD_REQUIREMENTS` in `dispatch_state.py`.
2. Add the field to `DispatchItem` in `responses.py`.
3. Run the contract test to confirm GREEN.

## Known Follow-ups

- **STORY-741 candidate**: `dispatch_db_service.py` contains hardcoded SQL string literals (e.g., `status = 'claimed'`). These should reference the canonical enum but are out of scope for STORY-740.
- **Frontend TypeScript enum consolidation**: The TypeScript `DispatchStatus` type in the dashboard is maintained separately. A future story should validate it against the canonical Python module.
