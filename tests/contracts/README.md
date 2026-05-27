# Contract Tests

STORY-544: Deterministic contract-critical test harness for system invariants.

## Purpose

Contract tests verify **critical system invariants** that, if broken, cause cascading failures. They run in CI as a dedicated merge-gating job, separate from the broader test suite. A contract violation blocks the merge immediately with a clear error message identifying which invariant broke.

## Structure

```
tests/contracts/
├── conftest.py          # Marker registration + no_network safety fixture
├── README.md            # This file
├── harness/
│   └── base.py          # ContractScenario Protocol + contract_critical marker
├── scenarios/
│   ├── test_completion_fail_closed.py   # Completion endpoint fail-closed behavior
│   └── test_usage_quota_pipeline.py     # Usage -> Quota -> Pacing pipeline
└── fixtures/
    ├── completion_fixtures.py           # Mock GitHub API factories
    └── quota_fixtures.py               # Mock Loki response factories
```

## Running Tests

```bash
# Run all contract tests
pytest tests/contracts/ -m contract_critical --tb=short -q

# Run a specific scenario
pytest tests/contracts/scenarios/test_completion_fail_closed.py -v

# Collect only (verify discovery)
pytest tests/contracts/ --collect-only -m contract_critical
```

## Adding a New Scenario

1. **Create a new file** in `scenarios/` named `test_{system}_{invariant}.py`
2. **Add the marker** at module level: `pytestmark = [contract_critical]`
3. **Import from harness**: `from tests.contracts.harness.base import contract_critical`
4. **Document the contract** in the module docstring: what invariant, what code under test
5. **Create fixtures** if needed in `fixtures/` — import them explicitly in the scenario
6. **Add class attributes** (optional): `contract_name` and `invariant` for discoverability

### Scenario Template

```python
"""Contract: {System} {invariant description}.

Invariant: {What must always be true}.

Code under test:
  - {module.path.function_name}
"""
import pytest
from tests.contracts.harness.base import contract_critical

pytestmark = [contract_critical]


class TestMyInvariant:
    contract_name = "my-invariant"
    invariant = "Description of what must hold"

    @pytest.mark.asyncio
    async def test_happy_path(self):
        ...

    @pytest.mark.asyncio
    async def test_failure_mode(self):
        ...
```

## Naming Conventions

| Item | Convention | Example |
|------|-----------|---------|
| Scenario file | `test_{system}_{invariant}.py` | `test_completion_fail_closed.py` |
| Test class | `Test{System}{Behavior}` | `TestCompletionFailClosed` |
| Test method | `test_{action}_{condition}_{expected}` | `test_network_error_returns_false` |
| Fixture module | `{system}_fixtures.py` | `completion_fixtures.py` |

## Fixture Patterns

- Fixtures are **importable modules** in `fixtures/`, not conftest auto-injection
- Each scenario **explicitly imports** what it needs — self-contained
- Factory functions return mock objects: `mock_github_api_success()`, `mock_loki_client_error()`
- No real network, no real database, no real Loki — all mocks
- The `no_network` autouse fixture in `conftest.py` blocks AF_INET/AF_INET6 sockets as defense-in-depth

## CI Integration

Contract tests run in `.github/workflows/contract-critical.yml`:
- Triggered on PRs to `main` and pushes to `main`
- Separate from the main `test.yml` workflow
- Appears as its own check in the PR checks list
- Blocks merge on any contract test failure

## Current Scenarios

| Scenario | File | Tests | Invariant |
|----------|------|-------|-----------|
| Completion Fail-Closed | `test_completion_fail_closed.py` | 6 | GitHub API unreachable -> 422 (never fail-open) |
| Usage->Quota Pipeline | `test_usage_quota_pipeline.py` | 17 | Loki -> QuotaInfo -> Pacing integrity |

## Migration Path (Existing Tests)

Existing scattered contract tests can be migrated to this structure:

1. **Move** the test file to `scenarios/`
2. **Add** `pytestmark = [contract_critical]` at module level
3. **Extract** fixture factories to `fixtures/` if reusable
4. **Update** CI workflow if needed

Candidates for future migration:
- `tests/ops_console/test_stale_recovery_contract.py`
- `tests/ops_console/test_completion_gate_slug_relaxed.py`
- `tests/ops_console/test_quota_no_data_reason_codes.py`
