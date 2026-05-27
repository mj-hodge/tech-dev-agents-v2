## 2026-05-11 PR Review Session — api-levanta

### PR #3 — STORY-933: Levanta Creator Connections Pipeline
- Author: agent-dan-gc
- Size: Large
- CI: lint FAIL (mypy 3 errors), tests PASS
- Verdict: REQUEST_CHANGES
- Review posted: https://github.com/hpi-gorillacommerce/api-levanta/pull/3#issuecomment-4420563922

**Blockers:**
1. mypy CI failure — missing [[tool.mypy.overrides]] for azure.appconfiguration, azure.identity, fsspec
2. [STUB-FOUND] DAG trigger_dbt_run/trigger_dbt_test are logging-only stubs — dbt models never run
3. [TEST-MISSING] AC-04: write_jsonl_gz() has no test

**Fix dispatch:** BLOCKED — OPS_CONSOLE_API_KEY not set. Mark needs to manually dispatch rework of STORY-933 (branch: story-933-v3, PR: #3).

## PR Review Session — 2026-05-11

### advertising-amazon (2 open PRs reviewed)

**PR #438 — STORY-608: Missing Authorization Gates (SEC-13/SEC-14)**
- Size: Small (4 files, ~180 lines) | CI: ALL PASS
- Verdict: APPROVE
- Findings: [INFO] Late SDLC deliverables (implementation merged via #366 before docs); [MEDIUM] auth-critical ACs unit-only coverage; [NIT] hardcoded ADMIN_GROUP_ID
- Comment: https://github.com/hpi-gorillacommerce/advertising-amazon/pull/438#issuecomment-4421492314
- Action: Approved. Awaiting Mark's merge (branch protection + PR description asks for his review).

**PR #374 — phase-8(STORY-655): AMS ads dataset ingest pipeline (Round 5)**
- Size: Large (33 files, ~1,824 lines) | CI: FAILING (UAT Critical Assertions — stale, likely resolved) | CONFLICTING
- Verdict: REQUEST_CHANGES
- Blockers: merge conflicts; stale UAT CI failure (retrigger after rebase); missing site-reliability.md (Phase 10 CRIT)
- Warns: missing analysis.md (Phase 4); missing refinement-report.md (Phase 9)
- Comment: https://github.com/hpi-gorillacommerce/advertising-amazon/pull/374#issuecomment-4421502886
- Dispatch: PENDING — OPS_CONSOLE_API_KEY not available in this session. Mark needs to dispatch STORY-960 (rework of STORY-655) or run dispatch manually.

### Key Action for Mark
1. Approve + merge PR #438 (STORY-608 auth gates SDLC docs — clean, APPROVED)
2. Dispatch rework STORY-960 for PR #374: rebase + site-reliability.md + analysis.md + refinement-report.md + NIT

---

## PR Review Session - api-levanta (Round 2)

### PRs Reviewed

**PR #3 - STORY-933: Levanta Creator Connections Pipeline**
- Author: agent-dan-gc | Size: Large (2762+, 217-, 41 files)
- CI: mypy FAIL (3 errors) / tests PASS
- Verdict: REQUEST_CHANGES (4 blockers)
- Blockers:
  1. [STUB-FOUND] trigger_dbt_run/trigger_dbt_test are no-ops (AC-06/07 unimplemented)
  2. [CI-FAILING] mypy fails - missing stubs for azure.appconfiguration, azure.identity, fsspec
  3. [TEST-MISSING AC-02] fetch_reports pagination - zero test coverage
  4. [TEST-MISSING AC-04] write_jsonl_gz/canonical_bronze_path - zero test coverage
- Review comment: https://github.com/hpi-gorillacommerce/api-levanta/pull/3#issuecomment-4421511633
- Fix dispatch: FAILED (401 - OPS_CONSOLE_API_KEY not set)
  - **Action needed from Mark:** Dispatch STORY-934 as rework of STORY-933 with the 4 blockers above, or set OPS_CONSOLE_API_KEY on Morris's VM

### Knowledge Gaps Identified (for wiki curator)
1. Platform convention for suppressing mypy import-not-found/import-untyped for azure-sdk and fsspec
2. Approved pattern for in-process OTel span attribute scrubbing vs Collector transform
3. Platform Airflow backfill convention - single-date-per-run vs multi-date conf key
4. Minimum test coverage expectations for pipeline HTTP client and storage I/O
5. Platform HTTP error handling pattern - explicit checks vs raise_for_status
6. Platform convention for sharing default config constants between internal modules
