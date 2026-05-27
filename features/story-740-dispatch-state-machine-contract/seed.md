# STORY-740 — Dispatch State-Machine Contract Consolidation

## Overview
| Field | Value |
|-------|-------|
| Mode | feature_update |
| Scope | small |
| Feature Name | Dispatch state-machine canonical contract |
| Phase Path | 1 → 7 → 8 → Done |
| Repo | tech-dev-agents |
| Frontend | false |

## Problem Statement
The dispatch state machine has no single source of truth. Its four Pydantic surfaces — `DispatchStatusEnum`, `DispatchRequest`, `DispatchItem`, `DispatchQueueResponse` (all in `tech_dev_agents/ops_console/models/responses.py`) — plus the Postgres `dispatch_items.status` CHECK constraint and the `uq_story_active_idx` partial unique index, are maintained independently. Every time a new state or per-state field is added, **at least one surface gets forgotten**, the route silently drops the data on the floor or 500s, and a follow-up patch PR ships a day later.

Recent history (2026-04-26 to 2026-04-27, ~36 hours):

| PR | Surface that drifted | Fix |
|----|---------------------|-----|
| #170 | `DispatchStatusEnum` missing `IN_REVIEW`, `PAUSED`, `NEEDS_INFO` | Add three enum members |
| #171 | `DispatchQueueResponse` fields didn't match what the route returned | Add `in_progress`, `in_review`, `paused`, `needs_info` buckets |
| #173 | `DispatchRequest` / `DispatchItem` missing `rework_of` + split-state fields | Add `rework_of`, `paused_at`, `current_phase`, `needs_info_path` |
| 5cd51ce (unmerged) | `DispatchRequest` missing `cross_story_reference` | Add field — STORY-523 path |

Each fix was correct in isolation. The **pattern** is the bug: there is no contract test that fails when one surface forgets a state or field that another surface uses. So drift is invisible until production 500s or silent data loss reveals it.

## Target User / Use Case
**User:** any agent (human or bot) that adds a new dispatch state, transition, or per-state field.
**Today:** they edit one of four classes in `responses.py`, ship the PR, get merged, deploy, and discover at runtime that one of the other three surfaces was also supposed to change. Hotfix PR follows.
**After this story:** a single canonical definition lives in one module. Adding a state or field is a one-place edit. CI fails the PR if any of the four Pydantic surfaces, the SQL CHECK constraint, or the partial unique index drifts from the canonical definition.

## Success Criteria
1. **SC-1 — Canonical state machine module exists.** A new module `tech_dev_agents/ops_console/models/dispatch_state.py` (or equivalent — name negotiable in Phase 7) defines: (a) the set of valid states, (b) the allowed transition matrix (from-state → set of to-states), (c) for each state, which `DispatchItem` fields are required vs. cleared on entry.
2. **SC-2 — `DispatchStatusEnum` is derived from or validated against the canonical states.** Adding a state in the canonical module without updating the enum (or vice versa) makes the contract test fail.
3. **SC-3 — `DispatchItem`, `DispatchRequest`, `DispatchQueueResponse` are validated against the canonical definition.** Specifically: every state in the canonical machine must have a corresponding bucket in `DispatchQueueResponse` (or be explicitly listed as terminal/non-bucketed); every per-state field declared in the canonical module must exist on `DispatchItem`.
4. **SC-4 — SQL CHECK constraint and `uq_story_active_idx` are validated against the canonical states.** The contract test reads the latest `*_status.sql` migration (or queries the canonical states module) and asserts the CHECK constraint enum and the partial-index `WHERE` clause match the canonical state set, partitioned correctly between active and terminal states.
5. **SC-5 — Contract test runs in CI and fails on any drift.** A new test file `tests/ops_console/test_dispatch_contract.py` is added to the default pytest run. It must fail with a clear message identifying which surface diverged.
6. **SC-6 — Zero behavior change for end users.** All existing tests pass unchanged. No new dispatch states, no new fields, no transition changes — this story consolidates contracts only. Any existing latent inconsistency surfaced by the new contract test is fixed in this PR (so the test goes GREEN), but no new functionality.
7. **SC-7 — Docs entry.** A short developer guide is added at `features/story-740-dispatch-state-machine-contract/state-machine.md` (in the deliverable folder, lives in repo) documenting how to add a new state or field — single-place edit instructions plus what the contract test checks.

## Verification Plan

| SC | Command | Expected Output |
|----|---------|-----------------|
| SC-1 | `python -c "from tech_dev_agents.ops_console.models.dispatch_state import STATES, TRANSITIONS, FIELD_REQUIREMENTS; print(sorted(STATES))"` | `['cancelled', 'claimed', 'completed', 'failed', 'in_review', 'needs_info', 'paused', 'pending']` (or whatever final canonical set is) — exits 0 |
| SC-2 | `pytest tests/ops_console/test_dispatch_contract.py::test_status_enum_matches_canonical -v` | `PASSED` |
| SC-3 | `pytest tests/ops_console/test_dispatch_contract.py::test_response_models_match_canonical -v` | `PASSED` |
| SC-4 | `pytest tests/ops_console/test_dispatch_contract.py::test_sql_constraint_matches_canonical -v` | `PASSED` |
| SC-5 | Demonstrate failure: temporarily add `FOO = "foo"` to `DispatchStatusEnum` without touching the canonical module → `pytest tests/ops_console/test_dispatch_contract.py` exits non-zero with a message containing `DispatchStatusEnum diverges from canonical states` (or similar). Revert. | Test FAILS with diagnostic message; reverting makes it PASS again |
| SC-6 | `pytest tests/ -x --ignore=tests/e2e -q` | All tests pass (existing + new contract test). No regressions in dispatch route tests. |
| SC-7 | `test -f features/story-740-dispatch-state-machine-contract/state-machine.md && grep -q "single-place edit" features/story-740-dispatch-state-machine-contract/state-machine.md && echo OK` | `OK` |

## Test Criteria
The contract test (`tests/ops_console/test_dispatch_contract.py`) is the central deliverable. It must:
- **Fail loudly on drift.** Any divergence between canonical states and any of the four Pydantic surfaces, the SQL `CHECK` constraint, or `uq_story_active_idx` produces a named, diff-style assertion message — not a bare `AssertionError`.
- **Be deterministic.** Pure-Python; no DB, no network, no fixture brittleness. Reads SQL files from disk.
- **Run fast.** < 1 second total. Does not gate developer iteration.
- **Be exhaustive in one run.** A single test invocation surfaces every diverging surface so adding a hypothetical state requires only one test cycle to find all the places that need updating.
- **Cover the negative case.** A demonstrable proof (in `state-machine.md` and the PR body) that introducing a hypothetical state in the enum without touching the canonical module makes the test fail with a specific diagnostic, and reverting makes it pass again.
- **Cover all surfaces:** `DispatchStatusEnum` membership, `DispatchItem` per-state field presence, `DispatchQueueResponse` bucket-per-active-state, SQL `CHECK` constraint enum set, `uq_story_active_idx` `WHERE status IN (...)` clause.

## Acceptance Criteria
- [ ] AC-1: Canonical `dispatch_state.py` module exists, exports `STATES`, `TRANSITIONS`, `FIELD_REQUIREMENTS`, and at least one helper (e.g., `is_terminal(state) -> bool`, `is_active(state) -> bool`).
- [ ] AC-2: `DispatchStatusEnum` either (a) is generated from `STATES`, or (b) has a contract test that asserts member names == `STATES`.
- [ ] AC-3: Contract test asserts `DispatchQueueResponse` has one bucket field per non-terminal state (or that any missing bucket is explicitly listed as a known omission with a comment, not silently dropped).
- [ ] AC-4: Contract test asserts `DispatchItem` has every per-state field declared in `FIELD_REQUIREMENTS`.
- [ ] AC-5: Contract test parses the SQL CHECK constraint and asserts its enum set == `STATES`.
- [ ] AC-6: Contract test parses the `uq_story_active_idx` `WHERE status IN (...)` clause and asserts it == active (non-terminal) states.
- [ ] AC-7: Contract test fails with a **specific diagnostic** when any surface diverges (not just "AssertionError"). Example: `DispatchStatusEnum is missing canonical state(s): {'foo'}`.
- [ ] AC-8: Adding a hypothetical state in `dispatch_state.py` and rerunning tests must surface every surface that needs updating in a single test run (not state-by-state). This proves the contract catches drift in one shot.
- [ ] AC-9: Full test suite (`pytest tests/ -x --ignore=tests/e2e`) passes after this story merges. Zero regressions.
- [ ] AC-10: PR description includes a "How to add a new dispatch state" checklist derived from `state-machine.md`.
- [ ] AC-11: Error/logging AC — when a contract test fails in CI, the failure log identifies the surface name (e.g., `DispatchStatusEnum`, `DispatchQueueResponse.bucket_fields`) and the diff (missing/extra). No silent assertion errors.

## Constraints
| Constraint | Value |
|------------|-------|
| Budget | minimal — pure contract + test work, no new behavior |
| Timeline | flexible — but lands before next dispatch-state PR (high probability inside 7 days) |
| Scale | n/a — code-only |
| Tech | Python 3.12, Pydantic v2, asyncpg/raw SQL parsing OK for SC-4 |

## Performance Requirements
n/a — this story adds a contract test, not runtime code on a hot path. Test must run in < 1s (it's a pure-Python check, no DB).

## Security Constraints
- [ ] No new endpoints — no auth surface added.
- [ ] Contract test reads SQL files from disk, not from a live DB connection — no credentials introduced.
- [ ] Existing dispatch authz unchanged.

## Operational Lifecycle
- **Configuration changes after deploy?** None — this is a code-only change behind a CI gate.
- **How operators change behavior?** They don't. This story enforces consistency; behavior is untouched.
- **Monitoring?** CI is the monitor. The contract test failing is the alert.

## Boundaries

| Always Do | Ask First | Never Do |
|-----------|-----------|----------|
| Treat the canonical state module as the single source of truth | Adding a new state, transition, or per-state field as part of this story (this story is consolidation only) | Add new dispatch states or behavior in this PR |
| Make the contract test failure messages name the diverging surface and show the diff | Renaming `DispatchStatusEnum` members (would break callers) | Rename enum members or change their string values |
| Keep `DispatchStatusEnum` member names in lockstep with canonical state strings | Refactoring `dispatch_db_service.py` SQL string literals to use the enum (out of scope for this story; file as STORY-741 if motivated) | Touch `dispatch_db_service.py` SQL string literals — they're caller-side and outside this story's scope |
| Make any latent contract violations surfaced by the new test GREEN in this PR | Removing the deprecated `claimed: list[DispatchItem] = []` alias on `DispatchQueueResponse` | Remove the deprecated `claimed` alias — it's a back-compat seam |
| Add `state-machine.md` to the story folder | Whether to also generate a state-diagram image (Mermaid OK if trivial; skip if it adds dependencies) | Introduce a code-generation step (e.g., a build-time script) — keep it pure-Python validation |
| Run the full pytest suite before declaring Phase 8 done | Whether SC-4's SQL parsing should target the latest migration only or all migrations | Add a new migration |

## Files to Modify
- `tech_dev_agents/ops_console/models/responses.py` — `DispatchStatusEnum`, `DispatchRequest`, `DispatchItem`, `DispatchQueueResponse` may get small adjustments (e.g., import canonical states; possibly minor field reordering for clarity). No semantic changes.
- `tech_dev_agents/ops_console/models/dispatch_state.py` — **new file** with `STATES`, `TRANSITIONS`, `FIELD_REQUIREMENTS`, helpers.
- `tests/ops_console/test_dispatch_contract.py` — **new file** with the contract test.
- `features/story-740-dispatch-state-machine-contract/state-machine.md` — **new file**, developer-facing guide.
- `features/story-740-dispatch-state-machine-contract/test-design.md` — Phase 7 deliverable.
- `.project`, `backlog.md`, `development-tasks.md` — tracking updates.

## Files to NOT Modify
- `tech_dev_agents/ops_console/routes/dispatch.py` — caller-side; uses the enum, doesn't define it. Out of scope.
- `tech_dev_agents/ops_console/services/dispatch_db_service.py` — has hardcoded SQL string literals (`status = 'claimed'`, etc.). Refactoring those to use the enum is a separate hygiene story; **do not touch in this PR**.
- `scripts/migrations/*.sql` — read-only for this story. The contract test reads them; it does not modify them. **Do not write a new migration.**
- `tech_dev_agents/ops_console/services/dispatch_events.py` — unrelated to the state-machine contract.
- Frontend (`tech_dev_agents/dashboard/`) — string-side enums in TypeScript are a separate consolidation story.
- Phase runner / dispatch poller — caller-side.

## Done Looks Like

```
$ git checkout story-740/dispatch-state-machine-contract
$ pytest tests/ops_console/test_dispatch_contract.py -v
============================= test session starts ==============================
tests/ops_console/test_dispatch_contract.py::test_status_enum_matches_canonical PASSED
tests/ops_console/test_dispatch_contract.py::test_dispatch_item_has_per_state_fields PASSED
tests/ops_console/test_dispatch_contract.py::test_queue_response_has_bucket_per_active_state PASSED
tests/ops_console/test_dispatch_contract.py::test_sql_check_constraint_matches_canonical PASSED
tests/ops_console/test_dispatch_contract.py::test_unique_active_index_matches_canonical PASSED
tests/ops_console/test_dispatch_contract.py::test_diagnostic_message_names_diverging_surface PASSED
============================== 6 passed in 0.34s ===============================

$ pytest tests/ -x --ignore=tests/e2e -q
.................................................. (existing tests)
====== ALL PASSED in 38.21s ======

# Negative-case proof: temporarily add FOO state to enum, run contract test
$ python -c "import re; p='tech_dev_agents/ops_console/models/responses.py'; s=open(p).read(); open(p,'w').write(s.replace('FAILED = \"failed\"', 'FAILED = \"failed\"\n    FOO = \"foo\"'))"
$ pytest tests/ops_console/test_dispatch_contract.py::test_status_enum_matches_canonical -v
============================= test session starts ==============================
tests/ops_console/test_dispatch_contract.py::test_status_enum_matches_canonical FAILED

================================== FAILURES ===================================
AssertionError: DispatchStatusEnum has extra state(s) not in canonical STATES: {'foo'}.
Add 'foo' to dispatch_state.STATES (and define its transitions / field requirements),
or remove it from DispatchStatusEnum.
=========================== 1 failed in 0.12s ================================

$ git checkout tech_dev_agents/ops_console/models/responses.py  # revert demo

$ gh pr view --web
# PR open, CI green, ready for review.
```

## Validation
Phase 8 is NOT complete until ALL of the following are GREEN on the agent's branch and demonstrated in the PR description:

| Step | Command | Pass criterion |
|------|---------|----------------|
| 1 | `pytest tests/ops_console/test_dispatch_contract.py -v` | All assertions pass |
| 2 | `pytest tests/ -x --ignore=tests/e2e -q` | Full suite passes; zero regressions vs. main |
| 3 | Negative-case demo: temporarily add `FOO = "foo"` to `DispatchStatusEnum`, run the contract test, observe a specific diagnostic naming `DispatchStatusEnum`, then revert | Test fails with diagnostic, reverting restores GREEN |
| 4 | `python -c "from tech_dev_agents.ops_console.models.dispatch_state import STATES, TRANSITIONS, FIELD_REQUIREMENTS; print(sorted(STATES))"` | Exits 0; prints the canonical state set |
| 5 | Inspection: `grep -n "Frontend" features/story-740-dispatch-state-machine-contract/seed.md` | Field present |
| 6 | Test count: `pytest tests/ops_console/test_dispatch_contract.py --collect-only -q | tail -n 1` | ≥ 5 tests collected |

The PR body must include the negative-case demo output (step 3) verbatim. A PR without that proof is not done — Mark will reject it.

## Escalation Contract

If during Phase 7 or Phase 8 the agent finds:
1. **Existing inconsistency that requires a behavior change to fix** (e.g., a state used in routes that has no DB CHECK support) → **stop, write QUESTION.md, mark needs_info**. Do not change behavior to make tests green.
2. **`dispatch_db_service.py` SQL literals diverge from the canonical enum** → **document the divergence as a finding** in `state-machine.md`'s "Known follow-ups" section and file STORY-741 (string-literal → enum migration) **as a follow-up**, but do NOT fix it in this PR. Out of scope.
3. **A migration has the wrong CHECK constraint set** (e.g., older migration is missing a state added later) → that's a real bug; **stop and ask**, don't ship a corrective migration as part of this story.
4. **Contract test would require parsing complex multi-statement SQL** → if a parser library is needed, **ask first**. Prefer a simple regex against the latest `*_status.sql` migration if achievable; only escalate if regex isn't sufficient.
5. **Pydantic v2 limitations prevent generating the enum from STATES** → that's fine, fall back to a contract test that asserts equality. The goal is the test, not code generation.

**Default if no story-specific rule fires:** if more than 1 turn is spent guessing, write QUESTION.md and pause. Don't grind.

## Codebase Context
| Aspect | Details |
|--------|---------|
| Affected files | `tech_dev_agents/ops_console/models/responses.py` (lines 282–351 — the four classes), new `tech_dev_agents/ops_console/models/dispatch_state.py`, new `tests/ops_console/test_dispatch_contract.py` |
| Related components | Dispatch route (`routes/dispatch.py`), dispatch DB service (`services/dispatch_db_service.py`), Postgres migrations under `scripts/migrations/` and `sql/` |
| Current behavior | All four contract surfaces maintained independently. No automated check that they agree. Drift discovered post-merge by 500s, missing data on dashboard, or quota/queue test failures. |
| Desired change | Canonical state-machine module is the single source of truth. CI test fails any PR that introduces drift in any of the four Pydantic surfaces, the SQL CHECK constraint, or the active-index `WHERE` clause. |
| Test coverage | New contract test file (~6 tests). Existing 394+ test suite must continue to pass with zero regressions. |
| Architecture constraints | Must work with Pydantic v2. Must not introduce a new build step or codegen. Must be pure-Python verification (no DB connection in test). |

## Out of Scope
- Migrating `dispatch_db_service.py` SQL string literals to use the enum (separate story — STORY-741 candidate).
- Frontend TypeScript enum consolidation (separate story).
- Adding new dispatch states or transitions.
- Changing the deprecated `claimed: list[DispatchItem]` alias on `DispatchQueueResponse`.
- Auto-generating Pydantic models from the canonical module (we just validate; codegen is overkill).
- Mermaid state diagrams (nice to have; only if trivial — do not add a dependency).

## Notes for Implementer
- The four classes you're harmonizing live at `responses.py:282-351`. Read those lines first.
- Migrations under `scripts/migrations/` use `004_paused_status.sql` / `007_needs_info_state.sql` / `sql/002_in_review_status.sql` as the precedent for state-adding migrations. The latest is what the contract test should validate against.
- The active-state set today is `{'pending', 'claimed', 'in_review', 'paused', 'needs_info'}` per `uq_story_active_idx` in `007_needs_info_state.sql`. Terminal states are `{'completed', 'cancelled', 'failed'}`. Your canonical module should expose this active/terminal partition.
- `DispatchQueueResponse.claimed` is a deprecated alias that retains backward compat; the new contract test must NOT require its removal.
- If you discover during Phase 8 that a state is missing from any surface (latent bug), that's an "Escalation Contract" item — surface it, don't paper over it.
