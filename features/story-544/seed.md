# STORY-544 — Deterministic Contract-Test Harness with Critical-Path Scenarios

**Scope:** medium
**Repo:** tech-dev-agents

## Context

Our system has several mission-critical invariants that, if broken, cause cascading failures:
the completion endpoint's fail-closed behavior (GitHub SHA verification rejecting on network
errors prevents fraudulent completions) and the usage→quota pipeline (Loki [USAGE] aggregation
→ QuotaInfo → pacing derivation ensures agents don't silently exceed token budgets).

Today, contract-style tests exist but are scattered across `tests/ops_console/` and `tests/skills/`
with no shared structure, no common fixture patterns, and no CI gating that distinguishes
"contract-critical" failures from ordinary unit test failures. A broken contract (e.g., the
completion gate silently switching to fail-open) would be buried in a wall of test output
rather than immediately halting the pipeline.

This story introduces a **deterministic contract-test harness** with a plugin-based scenario
structure, ships the first two critical-path scenarios, and adds a CI job that gates merges
on contract-critical test health.

## Problem Statement

Critical system invariants (completion fail-closed, usage→quota pipeline integrity) lack
a dedicated, structured test harness with CI-level gating. Contract violations can merge
undetected because they are not distinguished from ordinary test failures.

## Target User / Use Case

- **Dev team**: Needs fast, deterministic contract tests that run without infrastructure
  (no Postgres, no Loki, no GitHub API) and fail loudly when invariants break.
- **CI pipeline**: Needs a dedicated `contract-critical` job that blocks merges independently
  of the broader test suite, with clear failure messages identifying which contract broke.

## Success Criteria

- [ ] `tests/contracts/` directory exists with `harness/`, `scenarios/`, and `fixtures/` subdirectories
- [ ] Contract harness provides: scenario registration, deterministic fixture injection, structured pass/fail reporting with contract name + violated invariant in output
- [ ] Plugin structure: each scenario is a self-contained module in `scenarios/` that registers itself with the harness via pytest markers or a decorator
- [ ] **Scenario 1 — Completion Fail-Closed**: Exercises `POST /dispatch/complete/{story_id}` contract:
  - GitHub API unreachable → returns 422 (fail-closed, not fail-open)
  - GitHub API returns 404 (commit not found) → returns 422
  - GitHub API returns 200 (commit exists) + SDLC files present → returns 200 (happy path)
  - SDLC deliverable check network failure → logs warning but does NOT block (fail-open on SDLC, fail-closed on commit SHA)
  - All assertions use mock HTTP; no real GitHub calls
- [ ] **Scenario 2 — Usage→Quota Pipeline**: Exercises the Loki→QuotaInfo→Pacing pipeline contract:
  - Valid [USAGE] lines → QuotaInfo with source=LOKI, correct token aggregation, correct pacing derivation
  - Loki unreachable (LokiError) → QuotaInfo with source=NO_DATA, no_data_reason=LOKI_ERROR
  - Loki returns empty results → source=NO_DATA, no_data_reason=EMPTY_RESULT
  - [USAGE] lines present but unparseable → source=NO_DATA, no_data_reason=PARSE_MISS
  - Pacing thresholds: tokens/p90 < 0.6 → ON_TRACK, 0.6–0.9 → APPROACHING_LIMIT, ≥ 0.9 → EXCEEDED
  - Cache behavior: repeated calls within 5-min TTL return cached result
  - All assertions use mock Loki responses; no real Loki calls
- [ ] `README.md` in `tests/contracts/` documents: how to add a new scenario, naming conventions, fixture patterns, CI integration
- [ ] CI workflow job `contract-critical` runs `pytest tests/contracts/ -m contract_critical` and gates merge
- [ ] All contract tests are deterministic (no network, no database, no timing dependencies)
- [ ] Existing scattered contract tests (`test_stale_recovery_contract.py`, `test_completion_gate_slug_relaxed.py`) are NOT moved in this story (migration is a future story) — but the README documents the migration path

## Constraints

| Constraint | Value |
|------------|-------|
| Budget | Single medium story |
| Infrastructure | Zero — all tests mock-only, no Docker/Postgres/Loki required |
| Determinism | Every test must produce identical results on every run (no time.time(), no random, no network) |
| Speed | Full contract suite < 5 seconds wall-clock |
| Compatibility | pytest 7+, Python 3.11+ |

## Performance Requirements

- Contract test suite completes in < 5 seconds (target: < 2 seconds)
- No external I/O in any contract test (enforced by fixture that patches socket)

## Security Constraints

- No hardcoded secrets or tokens in fixtures
- Mock GitHub tokens use obviously-fake values (`ghp_FAKE_TOKEN_FOR_TESTING`)
- No real API endpoints referenced in test fixtures

## Acceptance Diff

The implementation MUST add or modify these files:

- `tests/contracts/__init__.py` — must-contain `contract`
- `tests/contracts/harness/__init__.py` — must-contain `contract`
- `tests/contracts/harness/base.py` — must-contain `ContractScenario`, must-contain `contract_critical`
- `tests/contracts/scenarios/__init__.py` — must-contain `contract`
- `tests/contracts/scenarios/test_completion_fail_closed.py` — must-contain `contract_critical`, must-contain `422`, must-contain `fail-closed`
- `tests/contracts/scenarios/test_usage_quota_pipeline.py` — must-contain `contract_critical`, must-contain `QuotaInfo`, must-contain `NO_DATA`, must-contain `LOKI`
- `tests/contracts/fixtures/__init__.py` — must-contain `contract`
- `tests/contracts/fixtures/completion_fixtures.py` — must-contain `mock_github`, must-contain `commit_sha`
- `tests/contracts/fixtures/quota_fixtures.py` — must-contain `mock_loki`, must-contain `USAGE`
- `tests/contracts/conftest.py` — must-contain `contract_critical`, must-contain `pytest`
- `tests/contracts/README.md` — must-contain `contract`, must-contain `scenario`, must-contain `CI`
- `.github/workflows/contract-critical.yml` — must-contain `contract_critical`, must-contain `pytest`

## Test Criteria

Every assertion below must have a pytest-level test:

1. **Harness plugin registration**: A scenario decorated/marked with `contract_critical` is collected by `pytest tests/contracts/ -m contract_critical` and appears in the test output with its contract name.
   - RED: No `tests/contracts/` directory, marker not registered → collection error
   - GREEN: `pytest --collect-only tests/contracts/ -m contract_critical` lists both scenarios

2. **Completion fail-closed — network error → 422**:
   - RED: `_github_commit_exists` returns True on network error (fail-open) → test expects 422 but gets 200
   - GREEN: Mock httpx raising `ConnectError` → endpoint returns 422 with "fail-closed" in message

3. **Completion fail-closed — commit not found → 422**:
   - RED: GitHub returns 404 but endpoint returns 200 → assertion fails
   - GREEN: Mock GitHub 404 → endpoint returns 422

4. **Completion fail-closed — happy path → 200**:
   - RED: Valid commit + SDLC files but endpoint rejects → assertion fails
   - GREEN: Mock GitHub 200 + mock SDLC tree → endpoint returns 200

5. **Completion SDLC gate — network failure is fail-open**:
   - RED: SDLC check network failure blocks completion → gets 422 when should get 200
   - GREEN: SDLC check raises but commit SHA verified → endpoint returns 200 (logged warning)

6. **Quota pipeline — valid [USAGE] lines → correct QuotaInfo**:
   - RED: `query_agent_quota` returns wrong token sum or wrong source → assertion fails
   - GREEN: 3 [USAGE] lines with known tokens → QuotaInfo.current_block_tokens equals sum, source=LOKI

7. **Quota pipeline — Loki unreachable → NO_DATA with LOKI_ERROR reason**:
   - RED: LokiError raised but source != NO_DATA → assertion fails
   - GREEN: Mock LokiError → QuotaInfo(source=NO_DATA), no_data counter incremented for loki_error

8. **Quota pipeline — empty result → NO_DATA with EMPTY_RESULT reason**:
   - RED: Empty Loki response but source != NO_DATA → assertion fails
   - GREEN: Mock empty response → QuotaInfo(source=NO_DATA, no_data_reason=EMPTY_RESULT)

9. **Quota pipeline — unparseable lines → NO_DATA with PARSE_MISS reason**:
   - RED: Garbage [USAGE] lines but source != NO_DATA → assertion fails
   - GREEN: Mock response with malformed lines → QuotaInfo(source=NO_DATA, no_data_reason=PARSE_MISS)

10. **Quota pipeline — pacing thresholds**:
    - RED: tokens/p90 = 0.5 but pacing != ON_TRACK → assertion fails
    - GREEN: Parameterized test with (0.3→ON_TRACK, 0.7→APPROACHING_LIMIT, 0.95→EXCEEDED)

11. **CI workflow file exists and targets contract tests**:
    - RED: No `.github/workflows/contract-critical.yml` → structural assertion fails
    - GREEN: File exists, contains `pytest tests/contracts/` and `contract_critical` marker

## Implementation Notes for the SDK Agent

### Harness Design
- Use pytest markers (`@pytest.mark.contract_critical`) rather than a custom registration framework — simpler, standard, well-supported
- `harness/base.py` should provide a `ContractScenario` base class or protocol that scenarios can optionally inherit for shared setup/teardown patterns
- `conftest.py` in `tests/contracts/` registers the `contract_critical` marker and provides shared fixtures (e.g., `no_network` fixture that patches `socket.socket` to prevent accidental real I/O)

### Fixture Design
- `completion_fixtures.py`: Provides `mock_github_api()` context manager that patches `httpx.AsyncClient` responses for the GitHub commit-exists check, plus `fake_dispatch_item()` factory
- `quota_fixtures.py`: Provides `mock_loki_response()` that returns canned [USAGE] log lines, `mock_loki_error()` that raises LokiError, plus `fake_quota_info()` factory

### Scenario Design
- Each scenario file is fully self-contained — imports its fixtures, uses `@pytest.mark.contract_critical`, documents which contract invariant it tests in the docstring
- Use `pytest.mark.parametrize` for pacing threshold tests and multi-variant error scenarios

### CI Integration
- `.github/workflows/contract-critical.yml`: Triggered on push to `main` and on PRs; runs `pytest tests/contracts/ -m contract_critical --tb=short -q`; fails the job (and blocks merge) on any contract test failure
- Keep this separate from the main test workflow so contract failures are immediately visible in the PR checks list

### Code Under Test
- Completion scenario tests the route handler logic in `tech_dev_agents/ops_console/routes/dispatch.py` (specifically `_github_commit_exists` and the completion endpoint)
- Quota scenario tests `tech_dev_agents/ops_console/services/loki_client.py` (`query_agent_quota`, `_parse_usage_line`) and `tech_dev_agents/ops_console/routes/agents.py` (quota fetcher, pacing derivation, cache)

### What This Story Does NOT Do
- Does NOT move existing scattered contract tests into the new structure (future migration story)
- Does NOT add infrastructure-dependent tests (Postgres, real Loki)
- Does NOT change production code — this is purely additive test infrastructure

## Manual Steps (Mark)

_None — no submodule changes or infrastructure provisioning required._

## Validation

After Phase 8 lands:
1. `contract-critical` CI workflow is GREEN on the PR.
2. The harness runs locally in under 30 s against the checked-in fixtures.
3. A hand-introduced regression in a dispatch-service helper (revert one line) trips a specific contract test with a clear, named failure — verified by temporarily reverting and running the suite.
