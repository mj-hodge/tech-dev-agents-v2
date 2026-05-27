# STORY-769 — Dependency-Aware Dispatch: Hide Stories With Unmet `DO NOT START until STORY-X` Until Dependency Merges

## Overview
| Field | Value |
|-------|-------|
| Mode | feature_update |
| Scope | small |
| Feature Name | Dependency-aware dispatch claim gating |
| Phase Path | 1 → 7 → 8 → Done |
| Repo | tech-dev-agents |
| Frontend | false |

## Problem Statement

Several recent stories have hard dependencies on earlier stories that haven't merged yet. The current pattern is to encode the dependency in the prompt:

```
prompt: "DO NOT START until STORY-632 PR is merged."
```

But the dispatch poller treats every pending story as claimable. Result:
- Agent claims the dependent story
- Reads the prompt, sees "DO NOT START"
- Either: (a) writes QUESTION.md asking "is STORY-X merged?" (consumes a dispatch cycle, requires Mark to answer), OR (b) starts work anyway, fails, retries, fails again
- Either way, dispatch capacity is wasted

Concrete examples from this session (2026-04-29/30):
- **STORY-766** (Morris fleet-vigilance Check 9) — prompt explicitly said "HARD DEPENDENCY: STORY-765 must be MERGED to main first." Agent claimed it anyway → went to needs_info asking "is 765 merged?" → wasted cycles.
- **STORY-760** (contract test for STORY-759 fix) — prompt said "verify STORY-759 has merged before claiming Phase 7". Agent claimed it pre-merge → forced to wait/escalate.

Today's pattern: Mark adds a "DO NOT START until STORY-X" line to the prompt; agents claim regardless; we burn a cycle on each.

The fix: **parse the prompt at claim time, look up STORY-X's status in `dispatch_items`, refuse to give the story to the agent if STORY-X isn't `completed` (or its PR isn't merged).** Story stays `pending` but is invisible to claim-poll until the dependency clears.

## Target User / Use Case

**User:** dispatch poller (claim path) + Mark (when dispatching dependent stories).
**Today:** Mark adds `DO NOT START until STORY-X` to prompts manually; agents ignore the marker and claim anyway, burning dispatch cycles.
**After this story:** the marker is parsed from the prompt at claim time. If STORY-X is not yet in a satisfied state (`completed`, OR merged PR exists), the claim returns 204/404 (story not yet eligible) and the agent moves on. The story stays `pending` and becomes claimable as soon as STORY-X completes.

## Success Criteria

1. **SC-1 — Dependency parser.** Helper `_extract_dependencies(prompt: str) -> list[str]` returns a list of STORY-IDs found in `DO NOT START until STORY-N` patterns in the prompt. Regex: `(?i)DO\s+NOT\s+START\s+until\s+(STORY-\d+)`. Multiple dependencies allowed; deduped. Idempotent on prompts without the marker (returns `[]`).
2. **SC-2 — Claim gate.** When the dispatch poller's claim path picks a candidate `pending` row, it calls `_extract_dependencies(prompt)`. If non-empty, query `dispatch_items` for each dependency_id; if ANY is not in a satisfied state (definition: `status='completed'` AND `pr_number IS NOT NULL`, OR equivalent terminal-success indicator), the candidate is SKIPPED — the poller moves to the next pending row.
3. **SC-3 — No new failures.** A blocked candidate is not transitioned to a different state. It remains `pending`. The poller logs `[DISPATCH] STORY-N skipped: dependency STORY-X not yet completed (status=<X.status>)` for observability.
4. **SC-4 — Polling efficiency.** Dependency check is at most 1 indexed query per claim attempt per dependency. Total claim-path latency increase < 50ms even for 5 dependencies.
5. **SC-5 — Handle unknown dependency.** If `STORY-X` referenced in the marker isn't in `dispatch_items` at all (typo, not yet enqueued), treat it as "blocked, dependency missing" — log WARN, leave story pending. Don't claim.
6. **SC-6 — Visibility on the queue API.** `GET /api/dispatch/queue` includes `dependencies_unmet: list[str]` for each pending row. Empty list = claimable. Non-empty = blocked. Dashboard can render this; this story does NOT add the dashboard rendering (separate frontend story if motivated).
7. **SC-7 — Test coverage.** Unit tests for the parser (regex edge cases) + integration tests for the claim-path skip behavior + a fixture confirming a story becomes claimable the moment its dependency transitions to `completed`.
8. **SC-8 — Zero regressions.** Existing claim-path tests pass. Stories with no `DO NOT START until` markers have unchanged behavior.

## Verification Plan

| SC | Command | Expected |
|----|---------|----------|
| SC-1 | `pytest tests/ops_console/test_dispatch_dependency_parser.py -v` | All parser tests PASS |
| SC-2 | `pytest tests/ops_console/test_dispatch_claim_dependency_gate.py::test_blocked_candidate_skipped -v` | PASSED |
| SC-3 | Logging assertion in claim test: `[DISPATCH] STORY-N skipped: dependency STORY-X not yet completed` | Asserted in test |
| SC-4 | Performance test: claim-path with 5 dependencies completes in < 50ms | PASSED |
| SC-5 | `pytest tests/ops_console/test_dispatch_claim_dependency_gate.py::test_unknown_dependency_blocks -v` | PASSED |
| SC-6 | `curl /api/dispatch/queue` returns rows with `dependencies_unmet` field | Asserted in API test |
| SC-7 | `pytest tests/ops_console/test_dispatch_claim_dependency_gate.py::test_unblocks_on_dep_completion -v` | PASSED |
| SC-8 | `pytest tests/ -x --ignore=tests/e2e -q` | Zero regressions |

## Test Criteria

- **Parser tests** cover: no marker, single marker, multiple markers, lowercase variants, "Until" vs "until", "STORY-7" vs "STORY-007", whitespace variants. Pure-Python regex tests, < 100ms total.
- **Claim-gate tests** mock the asyncpg query and assert the SKIP behavior, not actual DB writes. Use existing `tests/ops_console/test_dispatch_route_*.py` fixture pattern.
- **Integration test** (one per file) sets up a fixture with story-A pending + dep on story-B, asserts story-A NOT claimed; transitions story-B to completed; asserts story-A NOW claimable.
- **No live DB writes** in unit tests.

## Validation

| Step | Command | Pass criterion |
|------|---------|----------------|
| 1 | `pytest tests/ops_console/test_dispatch_*dependency* -v` | All ≥ 8 tests GREEN |
| 2 | `pytest tests/ -x --ignore=tests/e2e -q` | Full suite GREEN; zero regressions |
| 3 | After deploy: enqueue a story with prompt containing `DO NOT START until STORY-NNNN` (NNNN not yet enqueued); call `/api/dispatch/claim`; verify the story is NOT given to the agent; verify `dependencies_unmet` field shows in queue listing | Documented in PR body |
| 4 | Continue test 3: enqueue STORY-NNNN as completed; re-claim; verify story now claimable | Documented |

## Acceptance Criteria

- [ ] AC-1: New helper `_extract_dependencies(prompt) -> list[str]` lives in `tech_dev_agents/ops_console/services/dispatch_db_service.py` (alongside other claim-path logic).
- [ ] AC-2: Claim path (`db_svc.claim()` and equivalent agent-side claim API) calls the helper before transitioning row state. Blocked candidates skipped, NOT transitioned.
- [ ] AC-3: Queries dependency status via existing `dispatch_items` table — single indexed lookup per dependency.
- [ ] AC-4: New field `dependencies_unmet: list[str]` on the `DispatchItem` Pydantic model. Populated by the queue route from the same parser.
- [ ] AC-5: Migration: NO schema change required (query reads existing `status`, `pr_number`).
- [ ] AC-6: Logging: `[DISPATCH] STORY-N skipped: dependency STORY-X not yet completed (status=...)` per skip event. Visible in Loki.
- [ ] AC-7: Parser regex anchored: `(?im)^.*?DO\s+NOT\s+START\s+until\s+(STORY-\d+)` — multi-line + case-insensitive + non-greedy. Documented in code comment.
- [ ] AC-8: Multiple markers per prompt supported (caller uses `re.findall`).
- [ ] AC-9: Tests pass; zero regressions.
- [ ] AC-10: PR description includes a worked example showing a story being skipped → dependency completing → story being claimed.
- [ ] AC-11: Error/logging AC — if the dependency lookup itself fails (DB hiccup), DEFAULT-FAIL-SAFE: do NOT claim. Log the error with the story_id + dep id. The next poll will retry.

## Constraints
| Constraint | Value |
|------------|-------|
| Budget | small — single helper + claim-path branch + tests |
| Timeline | important — every dependent dispatch wastes cycles otherwise |
| Tech | Python 3.12, asyncpg, FastAPI; no new deps |

## Performance Requirements
- Per-claim overhead with no markers: < 1ms (regex on a < 5KB string).
- Per-claim overhead with N markers: < 10ms × N (one indexed query per).
- Acceptable upper bound: 50ms total even with 5 markers.

## Security Constraints
- [ ] Parser never executes prompt content as code (just regex match).
- [ ] No new auth surface; reuses existing claim-path authz.
- [ ] Dependency lookup respects existing `dispatch_items` access control.

## Operational Lifecycle

- **Configuration:** none — feature is always-on. (Optional: env var `DISPATCH_DEPENDENCY_GATE_ENABLED=1` for safe-rollback in case of unexpected behavior.)
- **How operators tune:** not needed.
- **Monitoring:** Loki picks up `[DISPATCH] STORY-N skipped` lines. Spike could indicate a dependency stuck in a non-terminal state.

## Boundaries

| Always Do | Ask First | Never Do |
|-----------|-----------|----------|
| Use the existing dispatch_items.status + pr_number columns to decide "satisfied" | Whether to also accept `BLOCKED BY` or `requires`/`depends on` aliases (current scope: only `DO NOT START until`) | Add new dependency syntax |
| Keep blocked stories in `pending` state — don't move them | Whether to add a `blocked` status (separate story; not now) | Auto-cancel blocked stories |
| Log every skip for observability | Whether to emit a Teams DM when many stories are blocked (probably overkill — the queue API already shows it) | Silent skips |
| Default-fail-safe on DB error | Whether to add a CLI override (e.g., `?force=1` on /claim) — out of scope | Bypass the gate via undocumented flags |
| Treat missing-dependency as blocked | Whether unknown deps should auto-resolve after a TTL (out of scope) | Silently claim if dep not found |

## Files to Modify

- `tech_dev_agents/ops_console/services/dispatch_db_service.py` — add `_extract_dependencies` helper + claim-gate logic.
- `tech_dev_agents/ops_console/models/responses.py` — add `dependencies_unmet: list[str] = []` field on `DispatchItem`.
- `tech_dev_agents/ops_console/routes/dispatch.py` — populate `dependencies_unmet` in the queue listing route.
- `tests/ops_console/test_dispatch_dependency_parser.py` — **new**, parser unit tests.
- `tests/ops_console/test_dispatch_claim_dependency_gate.py` — **new**, integration tests.
- `features/story-769-dependency-aware-dispatch/test-design.md` — Phase 7 deliverable.
- `.project`, `backlog.md` — tracking.

## Files to NOT Modify

- DB schema — no migration needed.
- Existing claim-path handlers (only add the gate; preserve all existing logic).
- Frontend — out of scope.
- Other route handlers (e.g., enqueue, fail) — they don't need dependency awareness.

## Done Looks Like

```
$ pytest tests/ops_console/test_dispatch_*dependency* -v
test_parser_no_marker PASSED
test_parser_single_marker PASSED
test_parser_multiple_markers PASSED
test_parser_case_insensitive PASSED
test_parser_whitespace_variants PASSED
test_blocked_candidate_skipped PASSED
test_unknown_dependency_blocks PASSED
test_unblocks_on_dep_completion PASSED
test_dependencies_unmet_in_queue_listing PASSED
test_default_fail_safe_on_db_error PASSED
========== 10 passed in 0.34s ==========

# After deploy:
$ curl -X POST /api/dispatch \
  -d '{"story_id":"STORY-900","prompt":"DO NOT START until STORY-899 PR is merged. ...","scope":"small"}'
HTTP 201

$ curl /api/dispatch/queue
{"pending":[{"story_id":"STORY-900","dependencies_unmet":["STORY-899"], ...}]}

$ # Agent calls /claim — STORY-900 NOT given, despite being pending
$ curl -X POST /api/dispatch/claim --data '{"agent_name":"dan"}'
HTTP 204 (no claim available)

$ # Mark STORY-899 completed
$ curl -X POST /api/dispatch/STORY-899/complete --data '{"commit_sha":"...","pr_number":NN}'

$ # Now agent claim succeeds
$ curl -X POST /api/dispatch/claim --data '{"agent_name":"dan"}'
{"story_id":"STORY-900", ...}
```

## Escalation Contract

1. **Dependency syntax in prompt is ambiguous** (e.g., "blocked by STORY-N" but not "DO NOT START until") → out of scope; document and don't expand syntax.
2. **A dependency loop exists** (STORY-A blocks STORY-B, STORY-B blocks STORY-A) → both block forever; that's correct behavior. Mark must cancel one.
3. **Existing in-flight stories that have completed implicitly satisfy a marker** but `pr_number` is NULL → query falls back to `status='completed'` only. Document this edge case.
4. **A STORY-X referenced is across repos** (in api-retail-target, not tech-dev-agents) → still works since dispatch_items is shared across repos; dispatch DB has the rows.
5. **Performance regression on claim-path** → measure first; if regression > 50ms p95, add caching with short TTL (e.g., 30s). Out of scope for v1 unless measured.

**Default if no story-specific rule fires:** if more than 1 turn is spent guessing, write QUESTION.md and pause.

## Codebase Context

| Aspect | Details |
|--------|---------|
| Affected files | `services/dispatch_db_service.py` (claim path), `models/responses.py` (DispatchItem), `routes/dispatch.py` (queue listing) |
| Reference incidents | STORY-766 + STORY-760 in this session — both had `DO NOT START until ...` markers, both were claimed pre-dep-merge, both went to needs_info wasting cycles. |
| Architecture | Existing dispatch_items table + claim-path SQL transactions. New gate is additive + read-only on existing data. |
| Test pattern | Existing `tests/ops_console/test_dispatch_*.py` files. Mock asyncpg + TestClient. |

## Out of Scope

- Frontend rendering of `dependencies_unmet` (separate small story).
- Topological-sort batch claim (claim N stories at once respecting deps) — out of scope.
- Auto-completion when downstream dep merges (the natural polling cycle handles this within 30s).
- Cross-repo dep resolution against external systems (Linear, GitHub Issues) — only dispatch_items.

## Notes for Implementer

- Reference Mark's pattern: prompts say `DO NOT START until STORY-N PR is merged`. Treat "PR is merged" as equivalent to `pr_number IS NOT NULL AND status='completed'`.
- The fail-safe (AC-11) is critical — if we can't verify a dep, we DON'T claim. Better to delay than to start work on something that's actually blocked.
- The `dependencies_unmet` field on the queue listing is observability — it's how the dashboard (and operators with `curl`) can see why a story isn't being claimed.
- Parser regex: prefer `re.findall` over `re.search` since multiple deps are allowed.
- This story unlocks safe dispatch chains: Mark can dispatch a 5-story sequence with each story marking its predecessor as the dependency, and they'll process in order automatically.
