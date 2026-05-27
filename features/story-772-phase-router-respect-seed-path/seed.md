# STORY-772 — Phase Router Must Respect Seed's Declared `Phase Path`, Not Default Per-Scope Map

## Overview
| Field | Value |
|-------|-------|
| Mode | feature_update |
| Scope | small |
| Feature Name | Phase router seed-path respect |
| Phase Path | 1 → 7 → 8 → Done |
| Repo | tech-dev-agents |
| Frontend | false |
| Related | STORY-640 (phase router honors seed's declared phase path) — appears partially shipped but not fully working per evidence below |

## Problem Statement

The dispatch poller / phase runner is dispatching phases that **do not exist in the story's seed.md `Phase Path` field**. Agents end up in `needs_info` asking "Phase 2 was dispatched but my path skips Phase 2" — which is a routing bug, not a question Mark should be answering.

Concrete evidence captured 2026-04-30 from 11 simultaneous needs_info stories, all on the same pattern:

| Story | Seed `Phase Path` | What was dispatched | Expected next phase |
|-------|-------------------|---------------------|---------------------|
| STORY-008 | `1 → 4 → 5 → 6 → 6b → 7 → 8 → 8b → 11 → Done` | Phase 2 (Research) | Phase 4 |
| STORY-009 | `1 → 4 → 5 → 6 → 6b → 6c → 7 → 8 → 8b → 11 → Done` | Phase 2 (Research) | Phase 4 |
| STORY-010 | (pattern same) | Phase 2 (Research) | Phase 4 |
| STORY-011 | `1 → 4 → 6 → 6b → 7 → 8 → 8b → 11 → Done` | Phase 7 (Test Design) — but Phase 6b not done | Phase 6b |
| STORY-014 | `1 → 4 → 5 → 6 → 6b → 7 → 8 → 8b → 11 → Done` | Phase 2 (Research) | Phase 4 |
| STORY-015 | (Medium-Large path) | Phase 7 — but Phase 6 (`feature-spec.md`) not done | Phase 6 |
| ... 5 more | (same patterns) | (same wrong dispatch) | |

Per agent quotes from the QUESTION.md files:
- "Phase 2 (Research) was dispatched for STORY-008, but **STORY-008's phase path does not include Phase 2**."
- "Phase 7 was dispatched, but Phase 6b (Security Review) has not been completed for this story."

The dispatch poller / phase runner appears to be using a default per-scope phase map (e.g., `medium → [1, 2, 3, 4, 5, 6, 7, 8]`) and ignoring the seed's `Phase Path` field.

## Target User / Use Case

**User:** every dispatched story (the dispatch poller's claim path).
**Today:** the poller dispatches phases by scope, not by seed. Agents reach a non-seed phase and get stuck asking Mark.
**After this story:** the poller parses `Phase Path` from seed.md (the standard SDLC field) and uses it to determine the next phase. Falls back to scope-default ONLY when the seed has no `Phase Path` field.

## Success Criteria

1. **SC-1 — Parse `Phase Path` from seed.md.** Helper `_extract_phase_path(seed_md_text: str) -> list[str | int]` returns the list of phase identifiers (e.g., `[1, 4, 5, 6, '6b', '6c', 7, 8, '8b', 11]`) parsed from the seed. Pattern: `Phase Path | 1 → 4 → 5 → 6 → 6b → 7 → 8 → 8b → 11 → Done` (any whitespace, `→` or `->`, `Done` always last).
2. **SC-2 — Phase runner uses parsed path.** When the dispatch poller selects the next phase for a story, it reads `seed.md` from the agent's workspace, calls `_extract_phase_path`, and dispatches the next phase NOT in `completed_phases`. If `Phase Path` is absent from seed, falls back to scope-default (existing behavior — preserves backward compat).
3. **SC-3 — Skip phases not in seed's path.** If `.project` indicates next phase is X but X is NOT in seed's path, the runner SKIPS X and dispatches the next seed-listed phase. Logs `[DISPATCH] STORY-N phase X skipped (not in seed path), dispatching phase Y instead`.
4. **SC-4 — `.project` becomes secondary.** When in conflict, seed's path WINS. `.project` tracking is updated to reflect the actual next phase.
5. **SC-5 — Backward compat.** Stories with NO `Phase Path` field in seed (older stories) continue to work via scope-default. No regressions.
6. **SC-6 — Logging.** Every dispatch logs `[DISPATCH] STORY-N: phase path source=seed/scope-default, next=N`.
7. **SC-7 — Tests cover all incident classes.** Unit tests for the parser + integration tests for skip-non-seed-phase behavior + a fixture replicating each of the 11 incident-pattern stories.
8. **SC-8 — Zero regressions** on existing dispatch_poller / sdlc_phase_runner tests.

## Verification Plan

| SC | Command | Expected |
|----|---------|----------|
| SC-1 | `pytest tests/deployment/test_phase_path_parser.py -v` | All parser tests PASS |
| SC-2 | `pytest tests/deployment/test_phase_router_seed_respect.py::test_uses_seed_path_when_present -v` | PASSED |
| SC-3 | `pytest tests/deployment/test_phase_router_seed_respect.py::test_skips_non_seed_phase -v` | PASSED |
| SC-4 | Documented in code: seed path wins | Inspection |
| SC-5 | `pytest tests/deployment/test_phase_router_seed_respect.py::test_no_seed_path_fallback_to_scope_default -v` | PASSED |
| SC-6 | Existing dispatch tests assert log line presence | Tested |
| SC-7 | `pytest tests/deployment/test_phase_router_seed_respect.py -v` | ≥ 11 fixture tests pass (one per incident story) |
| SC-8 | `pytest tests/ -x --ignore=tests/e2e -q` | All pass; zero regressions |

## Test Criteria

- **Parser tests** with hardcoded fixture seeds covering: standard format, missing field, malformed, nested `Phase Path` in non-Overview tables, multi-line.
- **Integration tests** with realistic .project + seed.md fixtures, mocked dispatch DB.
- **Fixture per incident** — at minimum: STORY-008 pattern (Phase 2 not in path), STORY-011 pattern (Phase 7 dispatched, 6b not done), STORY-015 pattern (Phase 7 dispatched, Phase 6 not done).
- All deterministic, < 2 sec total.

## Validation

| Step | Command | Pass |
|------|---------|------|
| 1 | `pytest tests/deployment/test_phase_path_parser.py tests/deployment/test_phase_router_seed_respect.py -v` | All GREEN |
| 2 | `pytest tests/ -x --ignore=tests/e2e -q` | Zero regressions |
| 3 | After deploy: dispatch a fresh medium-scope story whose seed says `Phase Path: 1 → 4 → 8 → Done`. Observe agent runs Phase 4 next, NOT Phase 2 or Phase 6. | Documented in PR body |
| 4 | All 11 currently-needs_info stories from this incident, after re-claim, should advance per their seed's path WITHOUT going back to needs_info on the same routing question. | Observation post-merge |

## Acceptance Criteria

- [ ] AC-1: `_extract_phase_path` helper added to `deployment/hermes/sdlc_phase_runner.py` (or appropriate dispatch-flow module).
- [ ] AC-2: Phase routing in `_route_next_phase` (or equivalent) consults `_extract_phase_path` first; fallback to scope map only when seed lacks the field.
- [ ] AC-3: Parser handles regex `Phase\s*Path\s*\|\s*([^|]+)\|` (Overview table format) AND optional bold form `\*\*Phase Path:\*\*\s*(.+)$`.
- [ ] AC-4: Phase identifiers parsed as: integers (1, 4, 7) and string-suffix variants (6b, 6c, 8b). `Done` is always the terminator and stripped.
- [ ] AC-5: `.project` Phase Routing section updated by phase runner to reflect ACTUAL next phase (per seed) on every dispatch, so `.project` and seed agree post-update.
- [ ] AC-6: When seed's path includes a phase that isn't in the scope-default map, the runner runs it (e.g., Phase 6b for medium stories that include it).
- [ ] AC-7: When seed's path EXCLUDES a default phase (e.g., medium story skipping Phase 2), the runner skips it.
- [ ] AC-8: Logging — `[DISPATCH] STORY-N: seed path=[1,4,6,6b,7,8], completed=[1,4,6], next=6b` per dispatch.
- [ ] AC-9: Existing tests pass; zero regressions.
- [ ] AC-10: PR body includes a worked example of a story that previously would have hit needs_info but now advances cleanly.
- [ ] AC-11: Error/logging AC — if seed.md is missing or unparseable, log WARN and fall back to scope default. Don't fail the dispatch.

## Constraints
| Constraint | Value |
|------------|-------|
| Budget | small — parser + routing helper + tests |
| Timeline | URGENT — every dispatched story risks hitting this bug |
| Tech | Python 3.12 stdlib regex; no new deps |

## Performance Requirements
- Parser overhead per dispatch: < 5ms (regex on a 5-20KB seed.md text).
- Routing decision overhead: < 10ms.

## Security Constraints
- [ ] Parser handles malformed seed without raising (returns `None` or empty list, falls back).
- [ ] No new auth surface.

## Operational Lifecycle
- **Configuration:** none — feature is always-on.
- **Tuning:** none.
- **Monitoring:** Loki picks up `[DISPATCH] STORY-N: seed path=...` lines. Drop in scope-default fallback usage = good signal.

## Boundaries

| Always Do | Ask First | Never Do |
|-----------|-----------|----------|
| Parse `Phase Path` from seed first | Whether to also support a `phases.yaml` config file as override (out of scope) | Hardcode phase paths in the runner |
| Fall back to scope default when seed has no field | Whether to add a `force_phase` override flag (out of scope) | Skip seed parsing on a "performance optimization" basis |
| Log every routing decision for observability | Whether to also include declared `Phase Path` in the queue API response (probably yes — could be STORY-773 follow-up) | Silent fallback — always log which source was used |
| Run any phase the seed declares (including 6b/6c/9/10 for medium stories) | Whether to validate phase identifiers exist as skills before dispatching | Reject seed phases that don't exist in scope-default map |

## Files to Modify

- `deployment/hermes/sdlc_phase_runner.py` — add parser + routing logic.
- `tests/deployment/test_phase_path_parser.py` — **new**, parser unit tests.
- `tests/deployment/test_phase_router_seed_respect.py` — **new**, routing integration tests.
- `features/story-772-phase-router-respect-seed-path/test-design.md` — Phase 7 deliverable.
- `.project`, `backlog.md` — tracking.

## Files to NOT Modify

- The Phase 1 seed.md template — this story consumes the existing field, doesn't change the template.
- Existing scope-default phase map — preserve as fallback.
- The dispatch DB schema — no migration needed.
- Frontend — out of scope.

## Done Looks Like

```
$ pytest tests/deployment/test_phase_*_seed* -v
test_parser_standard_overview_table_format PASSED
test_parser_missing_field_returns_none PASSED
test_parser_handles_done_terminator PASSED
test_parser_string_suffix_phases_6b_6c_8b PASSED
test_uses_seed_path_when_present PASSED
test_skips_non_seed_phase PASSED
test_no_seed_path_fallback_to_scope_default PASSED
test_seed_path_includes_6b_for_medium_story PASSED
test_seed_path_excludes_phase_2_for_medium_story PASSED
test_logs_seed_path_source PASSED
========== 10 passed in 0.42s ==========

# After deploy, Loki shows:
[DISPATCH] STORY-N: seed path=[1, 4, 6, '6b', 7, 8], completed=[1, 4], next=6
[DISPATCH] STORY-N phase 2 skipped (not in seed path), dispatching phase 4 instead
```

## Escalation Contract

1. **Some seeds have `Phase Path` written differently** (older format without the Overview table) — parser must handle the bold-field form `**Phase Path:** 1 → 4 → ...`. Document each variant tried.
2. **Seed's path is malformed** (e.g., `1 → → 4`) — log WARN, fall back to scope default.
3. **Story has no seed.md at all** (no Phase 1 was run — STORY-783 pattern) — that's a different bug; this story should NOT auto-fix the no-seed case. Falls back gracefully to scope default + logs.
4. **STORY-640 already partially shipped this fix** — investigate the existing implementation, extend rather than replace. Document what STORY-640 left undone.

**Default if no story-specific rule fires:** if more than 1 turn is spent guessing, write QUESTION.md and pause.

## Codebase Context

| Aspect | Details |
|--------|---------|
| Affected files | `deployment/hermes/sdlc_phase_runner.py` (phase routing function) |
| Reference incident | 2026-04-30 — 11 simultaneous needs_info stories all asking Mark "should I skip Phase 2?" / "Phase 7 dispatched but Phase 6 not done" |
| Architecture | Dispatch poller → SDLC phase runner → claims story → reads .project → dispatches next phase. Bug: doesn't read seed.md's Phase Path field. |
| Test pattern | mock subprocess + fixture seed.md files in `tests/fixtures/seeds/` |

## Out of Scope

- Adding a `force_phase` override (separate small story if motivated).
- Multi-line / non-table-form seed parsing beyond the two main variants.
- Auto-detecting that a seed is missing entirely (covered by STORY-767's no-seed dispatch detection check).
- Validating that every phase in a seed's path corresponds to a real skill (could be STORY-773 follow-up).

## Notes for Implementer

- Reference STORY-640 in the commit log — that story claimed to fix this. Either it shipped partially or didn't ship at all. Investigate before duplicating work.
- The 11 incident stories are documented in their respective `features/story-NNN-*/QUESTION.md` files. Use them as fixtures.
- The parser regex SHOULD handle `→` (Unicode arrow) and `->` (ASCII fallback). Test both.
- The fallback path is critical — if a story's seed has no `Phase Path`, scope-default must work as today. Don't break legacy stories.
- `Done` is always the terminator and is excluded from the parsed list.
