# Test Design: SDLC Execution Engine Slice

> Phase 7 -- Test Design
> Story: STORY-006
> Date: 2026-03-26
> Scope: Large

## Goal

Cover the Phase 7 -> Phase 8 engine slice with deterministic unit tests for:

- scope classification with explicit overrides
- canonical phase-path resolution for all scope tiers
- advance-category waiting-state transitions
- bracketed parallel phase-group metadata
- checkpoint persistence and recovery with atomic JSON writes
- deliverable verification for phase-specific expected files

## Test Cases

1. Override classification returns the requested scope and `method="override"`.
2. Deterministic scoring maps representative signals to `small`, `medium`, and `large`.
3. Phase paths resolve canonically for `trivial`, `small`, `medium`, `large`, and `epic`.
4. Bracketed groups are represented with explicit metadata for sequential phase entries.
5. `gate`, `confirm`, and `auto` categories transition to the correct waiting/completed state.
6. Waiting states resume correctly for approval and confirmation signals.
7. Checkpoint writes are atomic and round-trip through JSON intact.
8. Corrupted or missing checkpoints load as `None`.
9. Deliverable verification reports expected missing files for phase 7 and phase 8 slices.

## Notes

- The implementation should stay local and deterministic.
- The tests intentionally avoid any `.project`, backlog, or Asana mutations.
