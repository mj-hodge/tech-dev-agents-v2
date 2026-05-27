## PR Review â STORY-740: Dispatch State-Machine Contract Consolidation

**Size:** Medium (+643/-7, 9 files) | **Verdict: REQUEST_CHANGES**

*Review by Morris (Claude Code analysis â 2026-04-27)*

### CI Status

| Check | Result |
|-------|--------|
| Contract-critical invariant tests | â Pass |
| **Python contract + unit tests** | â **FAIL** |
| Full pytest suite (non-blocking) | â Pass |
| Playwright e2e | â Pass |

**Root cause:** `features/story-740-dispatch-state-machine-contract/seed.md` (on `main`) is missing `## Test Criteria`, `## Validation` headings, and `Frontend: false` field that SDLC compliance tests now require. This PR can fix it cheaply.

### SDLC Compliance

| Check | Result |
|-------|--------|
| `.project` updated | â |
| `features/story-740-dispatch-state-machine-contract/` exists | â |
| `seed.md` | â on main (but causes CI failure â see above) |
| `test-design.md` | â added in this PR |
| `state-machine.md` developer guide | â added in this PR |
| `backlog.md` updated | â |
| `CHANGELOG.md` updated | â |
| AC-10: PR body has new-state checklist | â PR body is dispatch boilerplate |

### What this PR does

Introduces `dispatch_state.py` â canonical immutable source of truth (frozensets) for all dispatch states, transitions, and schema â and a contract test suite that fails CI when any of four drift surfaces (`DispatchStatusEnum`, `DispatchItem`, `DispatchQueueResponse`, SQL CHECK + partial unique index) diverges. Motivated by 4 hotfix PRs in 36h. Real latent bug found and fixed: migration 008 partial unique index was missing `needs_info`.

Strengths: clean design, SQL parsed from disk (no DB connection), diagnostic messages name the diverging surface, contract covers all surfaces SC-1..SC-7.

### Findings

**ð´ H-1 (BLOCKING) â Migration 008 edited in place; deployed databases will NOT receive the fix**

`scripts/migrations/008_composite_key_story_repo.sql` was modified to add `needs_info` to the partial index WHERE clause. Because the migration uses `CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS`, re-running it on a DB where 008 was already applied is a **no-op** â the existing broken index stays untouched. The contract test passes (it reads the file text), but production still has the bug.

Fix (pick one):
- Add `scripts/migrations/009_fix_active_index_needs_info.sql` with `DROP INDEX CONCURRENTLY uq_story_repo_active_idx` + corrected recreate
- OR document in PR body + CHANGELOG that migration 008 has NOT been applied to any environment (fresh DB only)

The CHANGELOG line "Migration 008 was missing `needs_info`â¦ contract test now prevents recurrence" implies the fix is live â misleading if prod has the old index.

**ð¡ L-1 â `TRANSITIONS` matrix not contract-tested**

`dispatch_state.py` declares a state transition matrix but no test asserts `TRANSITIONS.keys() == STATES`, every to-state is in `STATES`, or terminal states have empty outgoing sets. A new state added without wiring `TRANSITIONS` won't fail CI. Fix: add `test_transitions_internal_consistency` to Group A.

**ð¡ L-2 â Group G diagnostic tests are tautological**

`test_simulated_enum_drift_names_surface` / `test_simulated_item_field_drift_names_surface` build the diagnostic string inside the test body and assert it contains words â they do not exercise the real production assertion path. Replace with `pytest.raises(AssertionError, match=...)` against injected drift, or remove them.

**ð¡ L-3 â AC-10 unsatisfied**

Seed AC-10 requires the PR body to include the "How to add a new dispatch state" checklist from `state-machine.md`. PR body is fallback boilerplate. Paste the checklist into the description.

**â¹ï¸ Nit** â `.project` `Completed Phases: 1, 4, 6, 7, 8, 8 (STORY-740)` has duplicate `8` with parenthetical. Per-story completion belongs in the Story Status table (which is correctly updated there).

### Verdict: REQUEST_CHANGES

Required before merge:
1. **H-1:** Add migration 009 (DROP + recreate with `needs_info`) OR document that 008 is undeployed
2. **CI green:** Add `## Test Criteria`, `## Validation`, `Frontend: false` to `seed.md`
3. **L-1:** Add `test_transitions_internal_consistency` to Group A
4. **L-3:** Paste `state-machine.md` checklist into PR body (closes AC-10)

The core `dispatch_state.py` + contract test contribution is solid â it catches the exact drift class that cost ~36 agent-hours. These fixes are all straightforward; ready to merge immediately after.

*â Morris (2026-04-27)*
