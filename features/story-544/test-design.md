# Test Design: STORY-544 Contract-Test Harness

## Summary

| Metric | Value |
|--------|-------|
| Total tests | 23 |
| Test files | 2 scenario files |
| RED (failing) | 15 |
| GREEN (passing) | 8 (pacing threshold pure-function tests against existing code) |
| Coverage target | 60% of contract-critical paths |
| Scope | Medium |

## Test Structure

```
tests/contracts/
├── __init__.py
├── conftest.py                              # Marker registration
├── harness/
│   ├── __init__.py
│   └── base.py                              # contract_critical marker re-export
├── scenarios/
│   ├── __init__.py
│   ├── test_completion_fail_closed.py       # 6 tests (all RED)
│   └── test_usage_quota_pipeline.py         # 17 tests (8 GREEN, 9 RED)
└── fixtures/
    ├── __init__.py
    ├── completion_fixtures.py               # Stubs → NotImplementedError
    └── quota_fixtures.py                    # Stubs → NotImplementedError
```

## Test Categories

### Scenario 1: Completion Fail-Closed (6 tests, all RED)

| Test | Class | What It Verifies | RED Reason |
|------|-------|-----------------|------------|
| `test_network_error_returns_false` | TestCompletionFailClosed | ConnectError → `_github_commit_exists` returns False (fail-closed) | fixture NotImplementedError |
| `test_commit_not_found_returns_false` | TestCompletionFailClosed | GitHub 404 → returns False | fixture NotImplementedError |
| `test_commit_found_returns_true` | TestCompletionFailClosed | GitHub 200 → returns True (happy path) | fixture NotImplementedError |
| `test_retries_on_failure_then_fails_closed` | TestCompletionFailClosed | Retries max_retries times, then False; verifies call count | fixture NotImplementedError |
| `test_sdlc_network_failure_does_not_block` | TestCompletionSdlcGateFailOpen | SDLC tree-fetch failure → completion proceeds (fail-open) | fixture NotImplementedError |
| `test_success_and_failure_produce_different_results` | TestCompletionOutputVariance | 200 → True, 404 → False (not a constant-return stub) | fixture NotImplementedError |

### Scenario 2: Usage → Quota Pipeline (17 tests, 8 GREEN / 9 RED)

| Test | Class | What It Verifies | State |
|------|-------|-----------------|-------|
| `test_valid_usage_lines_aggregate_correctly` | TestQuotaLokiAggregation | 3 [USAGE] lines → correct token sum | RED (fixture) |
| `test_source_is_loki_on_success` | TestQuotaLokiAggregation | source == LOKI when entries parse | RED (fixture) |
| `test_cost_aggregation_correct` | TestQuotaLokiAggregation | cost_usd fields summed correctly | RED (fixture) |
| `test_loki_error_returns_no_data_with_loki_error_reason` | TestQuotaNoDataReasons | LokiError → NO_DATA, counter++ | RED (fixture) |
| `test_empty_result_returns_no_data_with_empty_result_reason` | TestQuotaNoDataReasons | Empty response → NO_DATA, counter++ | RED (fixture) |
| `test_unparseable_lines_returns_no_data_with_parse_miss_reason` | TestQuotaNoDataReasons | Garbage lines → NO_DATA, counter++ | RED (fixture) |
| `test_pacing_threshold[10000-250-200000-on_track]` | TestQuotaPacingThresholds | Low usage → ON_TRACK | **GREEN** |
| `test_pacing_threshold[80000-150-200000-approaching_limit]` | TestQuotaPacingThresholds | Moderate usage → APPROACHING_LIMIT | **GREEN** |
| `test_pacing_threshold[180000-150-200000-exceeded]` | TestQuotaPacingThresholds | High usage → EXCEEDED | **GREEN** |
| `test_pacing_threshold[None-150-200000-unknown]` | TestQuotaPacingThresholds | Missing tokens → UNKNOWN | **GREEN** |
| `test_pacing_threshold[50000-None-200000-unknown]` | TestQuotaPacingThresholds | Missing remaining_min → UNKNOWN | **GREEN** |
| `test_pacing_threshold[50000-150-None-unknown]` | TestQuotaPacingThresholds | Missing p90 → UNKNOWN | **GREEN** |
| `test_pacing_threshold[50000-150-0-unknown]` | TestQuotaPacingThresholds | p90=0 (division guard) → UNKNOWN | **GREEN** |
| `test_unavailable_source_returns_unknown` | TestQuotaPacingThresholds | source='unavailable' → UNKNOWN | **GREEN** |
| `test_different_token_amounts_produce_different_sums` | TestQuotaOutputVariance | Different inputs → different outputs (stub detection) | RED (fixture) |
| `test_workflow_file_exists` | TestCiWorkflowExists | CI workflow YAML exists | RED (file missing) |
| `test_workflow_contains_contract_critical_marker` | TestCiWorkflowExists | CI workflow targets contract_critical marker | RED (file missing) |

## RED → GREEN Plan (Phase 8)

Phase 8 must implement these to reach GREEN:

1. **Fixture implementations** (12 RED tests depend on these):
   - `completion_fixtures.py`: `fake_commit_sha`, `fake_dispatch_item`, `mock_github_api_*`, `mock_sdlc_tree_response`
   - `quota_fixtures.py`: `make_usage_lines`, `mock_loki_entries`, `mock_loki_client_*`, `fake_quota_info`

2. **Test body completions** (scenario tests that need full pipeline calls):
   - Loki aggregation tests: replace stub assertions with actual `query_agent_quota()` calls via patched `LokiClient`
   - No-data counter tests: call `query_agent_quota()` with mocked error conditions, then assert counters

3. **CI workflow file** (2 RED tests):
   - Create `.github/workflows/contract-critical.yml`

4. **Harness finalization**:
   - `harness/base.py`: Add `ContractScenario` Protocol
   - `conftest.py`: Add `no_network` autouse fixture
   - `README.md`: Document conventions

## Gate Compliance

### Gate 1: Null/None Boundary Tests
- [x] `_derive_pacing` tested with `tokens=None`, `remaining_min=None`, `p90=None`, `p90=0` (4 parametrized cases)

### Gate 2a: External API Isolation Tests
- [x] `no_network` fixture (Phase 8) will patch `socket.socket` to prevent accidental real I/O
- [x] All tests use mock fixtures — zero real HTTP calls by design

### Gate 2b: External API Degradation Tests
- [x] Loki unreachable → NO_DATA with LOKI_ERROR reason
- [x] Loki empty response → NO_DATA with EMPTY_RESULT reason
- [x] Loki unparseable lines → NO_DATA with PARSE_MISS reason
- [x] GitHub unreachable → fail-closed (False/422)
- [x] GitHub 404 → fail-closed (False/422)

### Gate 10: Error Observability
- [x] No-data reason tests verify `logger.warning` is emitted (via counter increment which follows the log call)
- [x] _github_commit_exists logs warnings on failure (tested via retry count verification)

### Gate 11: Fixture Compilation
- [x] Fixture stubs define signatures matching production types (AsyncMock, MagicMock)
- [x] Phase 8 implementations will use real production classes for type validation

### Output-Variance Tests
- [x] `test_success_and_failure_produce_different_results` — completion scenario
- [x] `test_different_token_amounts_produce_different_sums` — quota scenario

## Verification

```
$ python3 -m pytest tests/contracts/ --collect-only -q
23 tests collected

$ python3 -m pytest tests/contracts/ -m contract_critical --collect-only -q
23 tests collected  (marker filter works)

$ python3 -m pytest tests/contracts/ -m contract_critical --tb=short -q
15 failed, 8 passed in 0.71s  (RED confirmed)
```

All 23 tests import cleanly. 15 are RED (fixtures not implemented, CI file missing). 8 are GREEN (pure-function pacing tests against existing production code).
