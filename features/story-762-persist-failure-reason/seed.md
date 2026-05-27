# STORY-762 — Persist `failure_reason` on Every Story Failure (Branch Setup, Phase Errors, All Paths)

## Overview
| Field | Value |
|-------|-------|
| Mode | feature_update |
| Scope | small |
| Feature Name | failure_reason persistence — universal |
| Phase Path | 1 → 7 → 8 → Done |
| Repo | tech-dev-agents |
| Frontend | false |
| Unblocks | STORY-727 (continuous self-improvement loop), STORY-701 classifier downstream consumers, requeue-failed skill |

## Problem Statement

`dispatch_items.failure_reason` is the schema column the entire self-healing stack reads. STORY-701's classifier categorises it. The `requeue-failed` skill decides retry-vs-escalate from it. STORY-724 Morris orchestrator routes alerts based on it. STORY-727 (when it ships) is supposed to do pattern detection across stories using it.

**It's NULL on every story that fails via `branch_setup_failed` and several other paths.**

Concrete evidence (2026-04-29 incident, 11 stories STORY-007–STORY-017):
- All 11 stories reached `status=failed` in the DB.
- All 11 had `failure_reason=NULL`.
- The dispatch poller emitted a structured event log line: `{"event": "branch_setup_failed", "story_id": "STORY-009", "agent": "dan", ..., "error": "[DISPATCH] git checkout main failed for STORY-009 (rc=1): error: pathspec 'main' did not match any file(s) known to git\\n"}`.
- The DB row got `status=failed` but the `failure_reason` column was never written.
- Result: every downstream self-healing component had no signal. requeue-failed couldn't classify. Morris had nothing to alert on. STORY-701's classifier returned default. The 11 stories silently retried into oblivion.

There are likely other failure paths with the same gap. STORY-741's NEVER_RETRY_CLASSES implementation tightened classification but didn't audit every fail-emitting code path for `failure_reason` population.

## Target User / Use Case

**User:** Morris, requeue-failed skill, STORY-727's pattern detector, and any future self-healing consumer.
**Today:** they read `failure_reason`, see NULL, and either skip the row or default to a meaningless category. The whole self-healing stack is data-starved.
**After this story:** every code path that transitions a story to `failed` MUST populate `failure_reason` with a non-empty, structured string. CI fails any PR that adds a fail-emitting path without populating it.

## Success Criteria

1. **SC-1 — Audit every fail path.** Identify every call site in `deployment/hermes/dispatch_poller.py`, `deployment/hermes/sdlc_phase_runner.py`, and `tech_dev_agents/ops_console/services/dispatch_db_service.py` that transitions a dispatch_item to `status='failed'` (whether via API call, direct SQL update, or service method). Document each in `features/story-762-persist-failure-reason/audit.md`.
2. **SC-2 — Every fail path populates `failure_reason`.** Each call site identified in SC-1 passes a non-empty `failure_reason` string with a structured prefix matching the existing taxonomy (e.g., `branch_setup_failed: <details>`, `phase_runner_timeout: phase=N`, `sdk_died: rc=<code>`, `quota_exceeded`, etc.). New prefixes are documented in `audit.md`.
3. **SC-3 — `branch_setup_failed` populates `failure_reason`.** Specifically: `dispatch_poller.py`'s call to `Fail STORY-N: 200` (around line N — to be located in implementation) must include a `failure_reason` string. The 2026-04-29 incident's NULL records prove this is missing today.
4. **SC-4 — Contract test forbids NULL.** A new test `tests/deployment/test_failure_reason_always_populated.py` AST-scans the three named files and FAILS if any subprocess/API call to a `/dispatch/{id}/fail` or `db_svc.fail` method passes a NULL/empty `failure_reason` parameter. Allowlist supported with `reason` field per STORY-760's pattern.
5. **SC-5 — Backfill audit.** A one-time SQL query in `audit.md` documents how many existing rows in `dispatch_items` have `status='failed' AND failure_reason IS NULL` so we know the existing data debt. **No backfill mutation in this story** — just measure.
6. **SC-6 — Zero regressions.** All existing tests pass. Behavior on the happy path is unchanged.

## Verification Plan

| SC | Command | Expected |
|----|---------|----------|
| SC-1 | `cat features/story-762-persist-failure-reason/audit.md \| grep -c "Call site"` | ≥ 5 (every fail-emitting path documented) |
| SC-2 | `pytest tests/deployment/test_failure_reason_always_populated.py::test_every_fail_call_passes_reason -v` | PASSED |
| SC-3 | `pytest tests/deployment/test_failure_reason_always_populated.py::test_branch_setup_failed_populates_reason -v` | PASSED |
| SC-4 | Demonstrate negative case — temporarily remove `failure_reason` from one fail call, run test → FAILS with named violation. Revert. | Test fails with diagnostic; revert restores GREEN |
| SC-5 | `cat features/story-762-persist-failure-reason/audit.md \| grep -A2 "Backfill measurement"` | Shows row count and example NULL records |
| SC-6 | `pytest tests/ -x --ignore=tests/e2e -q` | All pass; zero regressions |

## Test Criteria

The contract test is the central deliverable. It must:
- **Fail loudly when a fail-call is missing `failure_reason`.** Specific diagnostic: file + line + which fail-call path is non-compliant.
- **Be deterministic.** Pure-Python AST scan; no DB, no subprocess, no network.
- **Run fast.** < 1 second.
- **Cover the negative case.** PR body must include a demo where temporarily removing the failure_reason from one call site makes the test fail with a specific diagnostic.

## Validation

Phase 8 is NOT complete until ALL demonstrated in PR body:

| Step | Command | Pass criterion |
|------|---------|----------------|
| 1 | `pytest tests/deployment/test_failure_reason_always_populated.py -v` | All tests GREEN |
| 2 | `pytest tests/ -x --ignore=tests/e2e -q` | Full suite GREEN; zero regressions |
| 3 | Negative-case demo (remove failure_reason from one site) | Test fails with named violation; revert restores GREEN |
| 4 | After deploy, manually trigger a `branch_setup_failed` (e.g., dispatch a story with a non-existent branch) and verify the resulting `dispatch_items` row has `failure_reason` populated | Documented in PR body — SQL query shows non-NULL value |
| 5 | Audit document `features/story-762-persist-failure-reason/audit.md` lists ≥ 5 fail-call sites + the backfill measurement | File exists, content matches |

## Acceptance Criteria

- [ ] AC-1: `audit.md` documents every fail-emitting call site with file path, line range, and the chosen `failure_reason` prefix.
- [ ] AC-2: Each fail-emitting call passes `failure_reason=<non-empty structured string>`.
- [ ] AC-3: `branch_setup_failed` specifically populates `failure_reason` with at minimum `"branch_setup_failed: <git command output truncated to 500 chars>"`.
- [ ] AC-4: Contract test asserts no fail-call lacks `failure_reason`. AST-based, allowlist-capable.
- [ ] AC-5: Allowlist entries (if any) have a `reason` field per STORY-760 pattern.
- [ ] AC-6: SDK-died, phase-timeout, and any other fail paths use distinct, documented prefixes — not all collapsed to one generic string.
- [ ] AC-7: Backfill measurement (count of existing NULL rows) recorded in `audit.md`. NO mutation of existing rows in this PR.
- [ ] AC-8: PR body shows the negative-case demo verbatim.
- [ ] AC-9: Existing tests pass with zero regressions.
- [ ] AC-10: Error/logging AC — when a fail call is made, the populated `failure_reason` is also logged to stdout (visible in Loki) so operators can see it without a DB query.
- [ ] AC-11: Length cap on `failure_reason` — 1000 chars max (truncate if longer). Avoid bloat in DB.

## Constraints
| Constraint | Value |
|------------|-------|
| Budget | minimal — text strings + AST test |
| Timeline | URGENT — unblocks STORY-727 and the entire self-healing stack |
| Scale | n/a |
| Tech | Python 3.12, no new deps |

## Performance Requirements
n/a — populating a string field per fail event is sub-millisecond.

## Security Constraints
- [ ] `failure_reason` strings MUST NOT contain credentials, tokens, or PII. Truncate stderr/git output to 500 chars and never include env vars.
- [ ] No new endpoints / no auth surface change.

## Operational Lifecycle
- **Configuration changes after deploy?** None.
- **How operators use this?** Via SQL: `SELECT story_id, failure_reason FROM dispatch_items WHERE status='failed' ORDER BY failed_at DESC LIMIT 50;`. The Morris orchestrator and STORY-727 pattern detector consume it programmatically.
- **Monitoring?** Loki shows the failure_reason on every Fail event. Alert if any new failure_reason prefix appears (indicates new failure mode).

## Boundaries

| Always Do | Ask First | Never Do |
|-----------|-----------|----------|
| Pass a structured `failure_reason` string at every fail call | Adding a new failure-reason prefix not in the audit doc | Pass empty string or NULL as failure_reason |
| Document each new prefix in `audit.md` | Whether to retrofit existing failed rows (NULL → classified) | Mutate existing failed rows in this PR — out of scope |
| Truncate stderr/output to 500 chars | Whether the contract test should also scan morris/* and tools/* (current scope: 3 files) | Include credentials, tokens, env vars in failure_reason |
| Use existing taxonomy from STORY-701 where applicable | Whether to extend the taxonomy with new categories (must align with STORY-701) | Define new categories that conflict with STORY-701's taxonomy |

## Files to Modify

- `deployment/hermes/dispatch_poller.py` — every fail-emitting call site (likely 2-4 sites). Audit will identify exact lines.
- `deployment/hermes/sdlc_phase_runner.py` — `_git_check` failures, phase timeouts, branch-sync failures (STORY-759 path).
- `tech_dev_agents/ops_console/services/dispatch_db_service.py` — `fail()` method signature already accepts failure_reason; verify all callers pass it.
- `tests/deployment/test_failure_reason_always_populated.py` — **new file**, contract test.
- `features/story-762-persist-failure-reason/audit.md` — **new file**, audit + backfill measurement.
- `features/story-762-persist-failure-reason/test-design.md` — Phase 7 deliverable.
- `.project`, `backlog.md` — tracking.

## Files to NOT Modify

- Existing `dispatch_items` rows (no UPDATE statements in this PR — audit only).
- `features/story-701-*/seed.md` and STORY-701's classifier — that's the consumer, not the producer.
- The `failure_reason` column schema (it already exists).
- Frontend / dashboard — out of scope.

## Done Looks Like

```
$ pytest tests/deployment/test_failure_reason_always_populated.py -v
============================= test session starts ==============================
test_every_fail_call_passes_reason PASSED
test_branch_setup_failed_populates_reason PASSED
test_phase_timeout_populates_reason PASSED
test_sdk_died_populates_reason PASSED
test_failure_reason_truncated_to_1000_chars PASSED
test_no_credentials_in_failure_reason PASSED
============================== 6 passed in 0.31s ===============================

$ pytest tests/ -x --ignore=tests/e2e -q
ALL PASSED

$ cat features/story-762-persist-failure-reason/audit.md | head -20
[shows N call sites + backfill measurement]

$ # Negative-case proof:
$ # Temporarily strip failure_reason from one site, run test → FAILS
$ # Specific diagnostic:
E  Violation: deployment/hermes/dispatch_poller.py:N call to db_svc.fail()
   passes failure_reason=None. → Pass a structured string like
   'branch_setup_failed: <details>'.
```

## Escalation Contract

1. **A fail call site uses internal state that doesn't have a meaningful prefix yet** → STOP, write QUESTION.md, propose a new prefix to Mark before adding it to the taxonomy.
2. **Existing call site is already passing failure_reason but it's getting overwritten somewhere downstream** (data integrity issue) → write QUESTION.md, do NOT paper over.
3. **The contract test would require importing modules that have side effects on import** → fall back to regex over AST. Document in test code.
4. **Audit reveals more than 10 fail-emitting call sites** — that's a smell; ask Mark whether to scope down or split into 762a/762b.
5. **Backfill is needed urgently** — file STORY-765 as follow-up; this story explicitly does measurement only.

**Default if no story-specific rule fires:** if more than 1 turn is spent guessing, write QUESTION.md and pause.

## Codebase Context

| Aspect | Details |
|--------|---------|
| Affected files | `deployment/hermes/dispatch_poller.py`, `sdlc_phase_runner.py`, `dispatch_db_service.py` |
| Reference | STORY-701 failure taxonomy lives in `tech_dev_agents/ops_console/services/dispatch_db_service.py` near `_classify_failure_reason` (search for it) |
| Current behavior | Some fail calls populate failure_reason; many don't (NULL). branch_setup_failed never does. |
| Desired change | Every fail call populates failure_reason. Contract test enforces. |
| Test coverage | New test file with ≥ 5 assertions; full suite continues GREEN. |
| Architecture constraints | Pure-Python static analysis for the contract test. No new deps. |

## Out of Scope

- Backfill of existing NULL rows (separate STORY-765 if motivated).
- Adding new failure categories beyond what's needed by current sites.
- Frontend display of failure_reason on the dashboard (separate story).
- Migrating string-literal status fields (separate hygiene story).

## Notes for Implementer

- **This story unblocks STORY-727.** Treat as foundation.
- The 2026-04-29 incident (STORY-007–STORY-017) is the canonical example. Look at journalctl on dan's VM for `branch_setup_failed` events to see the data shape that needs to land in the column.
- Reference STORY-701's failure taxonomy as the source of standard prefixes.
- STORY-760's contract-test pattern (AST scan + allowlist with `reason`) is the reference shape for the new test.
- AC-10 (logging the populated reason) is so we don't need DB queries in incident response — Loki shows it.
