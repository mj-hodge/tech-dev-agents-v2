# STORY-253: Test Design — Commit-Gated Dispatch Completion

## Test Strategy

**Scope:** Small — focused unit + integration tests for the commit-gate guard.

**Key risk:** False negatives (allowing completions without valid commits) or false positives (blocking valid completions when GitHub is flaky). Tests must cover both the happy path and the fail-closed behavior.

## Test Matrix

### Route Tests (`tests/ops_console/test_dispatch_complete_gate.py`)

| ID | Test | AC | Type |
|----|------|----|------|
| T01 | Complete with valid commit_sha returns 200 | AC-1,3 | Integration |
| T02 | Complete without body returns 422 | AC-1 | Unit |
| T03 | Complete with empty commit_sha returns 422 | AC-1 | Unit |
| T04 | Complete with short SHA (7 chars) returns 422 | AC-2 | Unit |
| T05 | Complete with uppercase SHA returns 422 | AC-2 | Unit |
| T06 | Complete with non-hex chars returns 422 | AC-2 | Unit |
| T07 | GitHub 404 (commit not in repo) returns 422 | AC-3 | Integration |
| T08 | GitHub network error returns 502 (fail-closed) | AC-3 | Integration |
| T09 | GitHub 5xx returns 502 (fail-closed) | AC-3 | Integration |
| T10 | commit_sha stored in DB after successful completion | AC-4 | Integration |
| T11 | CompleteResponse includes commit_sha field | AC-5 | Unit |
| T12 | DispatchItem in history includes commit_sha | AC-5 | Integration |
| T13 | Existing /dispatch/fail endpoint still works (no body change) | Regression | Unit |
| T14 | Existing /dispatch/queue endpoint still works | Regression | Unit |
| T15 | GitHub token missing returns 502 (fail-closed, not silent pass) | AC-3 | Unit |

### Poller Tests (`tests/deployment/test_dispatch_poller_sha.py`)

| ID | Test | AC | Type |
|----|------|----|------|
| P01 | _report_complete sends commit_sha in body | AC-6 | Unit |
| P02 | _report_complete resolves HEAD via git rev-parse | AC-6 | Unit |
| P03 | _report_complete sends None sha when git fails | AC-6 | Unit |

### Ops Fixes (manual verification — no automated test)

| ID | Check | AC |
|----|-------|----|
| O01 | docker-compose.yml mounts 002 + 003 migrations | AC-7 |
| O02 | README.md references all three migration files | AC-8 |

## Test Dependencies

- PostgreSQL test database (existing `ops_console_test`)
- Mocked GitHub API (httpx mock, no real network calls in tests)
- Existing conftest fixtures (`client`, `app`, `dispatch_db_service`)

## RED State

All tests written to fail against current codebase. Implementation in Phase 8 will turn them GREEN.
