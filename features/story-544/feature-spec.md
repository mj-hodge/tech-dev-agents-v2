# Feature Specification: Contract-Test Harness with Critical-Path Scenarios

## Overview

| Field | Value |
|-------|-------|
| Story | STORY-544 |
| Scope | Medium |
| Approach | A — Pytest-Native Marker Harness (from analysis.md) |
| Production code changes | None — purely additive test infrastructure |
| Files added | 12 (see Acceptance Diff in seed.md) |

## Architecture

### Directory Structure

```
tests/contracts/
├── __init__.py                          # Package marker
├── conftest.py                          # Marker registration + shared fixtures
├── README.md                            # Convention docs
├── harness/
│   ├── __init__.py                      # Package marker
│   └── base.py                          # ContractScenario Protocol + helpers
├── scenarios/
│   ├── __init__.py                      # Package marker
│   ├── test_completion_fail_closed.py   # Scenario 1: completion gate
│   └── test_usage_quota_pipeline.py     # Scenario 2: usage→quota
└── fixtures/
    ├── __init__.py                      # Package marker
    ├── completion_fixtures.py           # Mock GitHub API + dispatch item factory
    └── quota_fixtures.py               # Mock Loki responses + QuotaInfo factory
```

### Domain Boundaries

| Domain | Responsibility | Interface |
|--------|----------------|-----------|
| **Harness** (`harness/`) | Marker definition, ContractScenario Protocol, shared test utilities | `ContractScenario` Protocol, `contract_critical` marker |
| **Fixtures** (`fixtures/`) | Deterministic mock factories for each code-under-test dependency | Importable functions — `mock_github_api()`, `mock_loki_response()`, etc. |
| **Scenarios** (`scenarios/`) | Self-contained test modules exercising specific invariants | Standard pytest test functions with `@pytest.mark.contract_critical` |
| **CI** (`.github/workflows/`) | Merge gating on contract health | Separate workflow job |

Boundaries are clean: scenarios import from fixtures explicitly. Harness provides the Protocol and marker. No circular dependencies.

---

## Component Specifications

### 1. `tests/contracts/conftest.py`

Registers the `contract_critical` marker and provides the `no_network` safety fixture.

```python
import pytest

def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "contract_critical: marks tests as contract-critical invariant checks"
    )

@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Prevent accidental real network I/O in contract tests.

    Patches socket.socket to raise immediately if any test accidentally
    bypasses mock injection and attempts real network access.
    """
    import socket
    def _blocked(*args, **kwargs):
        raise RuntimeError(
            "Contract tests must not make real network calls. "
            "Use mock fixtures from tests/contracts/fixtures/."
        )
    monkeypatch.setattr(socket, "socket", _blocked)
```

**Design decision — `no_network` as defense-in-depth:** Primary determinism comes from mock injection (mock replaces transport layer before socket is ever reached). The `no_network` fixture is a safety net, not the primary mechanism. If a test somehow skips mock setup, this catches it with a clear error message.

### 2. `tests/contracts/harness/base.py`

Provides the `ContractScenario` Protocol and the `contract_critical` marker constant for import convenience.

```python
from typing import Protocol, runtime_checkable
import pytest

# Re-export marker for convenient use: @contract_critical
contract_critical = pytest.mark.contract_critical


@runtime_checkable
class ContractScenario(Protocol):
    """Optional typing protocol for contract test scenarios.

    Scenarios are NOT required to implement this — they can be plain
    pytest functions with @contract_critical. This Protocol exists for:
    1. Type-checking guidance (IDE autocomplete)
    2. Documentation of the expected scenario shape
    3. Future: programmatic scenario discovery if needed

    Usage:
        class TestCompletionFailClosed:
            contract_name: str = "completion-fail-closed"
            invariant: str = "GitHub API unreachable → 422 (not fail-open)"

            async def test_network_error_returns_422(self, ...): ...
    """
    contract_name: str
    invariant: str
```

**Why Protocol, not ABC:** The seed specifies scenarios should be self-contained pytest test files. An ABC would force inheritance and a rigid `setup/execute/verify` lifecycle that doesn't fit structural assertions (like the CI workflow file check). A Protocol provides type guidance without enforcement overhead.

### 3. `tests/contracts/fixtures/completion_fixtures.py`

Provides mock factories for the completion endpoint's dependencies.

```python
"""Deterministic fixtures for completion fail-closed contract tests.

All fixtures return mock objects — no real GitHub API, no real database.
Import explicitly in scenario files; these are NOT auto-injected via conftest.
"""
from unittest.mock import AsyncMock, MagicMock
from typing import Any

# Obviously-fake token (seed security constraint)
FAKE_GITHUB_TOKEN = "ghp_FAKE_TOKEN_FOR_TESTING"

def fake_commit_sha(valid: bool = True) -> str:
    """Return a deterministic commit SHA for testing."""
    return "a" * 40 if valid else "dead" + "beef" * 9

def fake_dispatch_item(
    story_id: str = "STORY-999",
    repo: str = "tech-dev-agents",
    scope: str = "medium",
    status: str = "claimed",
) -> dict[str, Any]:
    """Factory for dispatch item dicts as returned by db_svc.get()."""
    return {
        "id": 1,
        "story_id": story_id,
        "repo": repo,
        "scope": scope,
        "status": status,
        "claimed_by": "test-agent",
    }

def mock_github_api_success(commit_sha: str | None = None) -> AsyncMock:
    """Mock httpx.AsyncClient that returns 200 for GitHub commit check."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    client = AsyncMock()
    client.get = AsyncMock(return_value=mock_response)
    return client

def mock_github_api_not_found() -> AsyncMock:
    """Mock httpx.AsyncClient that returns 404 for GitHub commit check."""
    mock_response = MagicMock()
    mock_response.status_code = 404
    client = AsyncMock()
    client.get = AsyncMock(return_value=mock_response)
    return client

def mock_github_api_network_error() -> AsyncMock:
    """Mock httpx.AsyncClient that raises on GitHub commit check.

    Simulates network failure — the completion endpoint MUST fail-closed (422).
    """
    import httpx
    client = AsyncMock()
    client.get = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
    return client

def mock_sdlc_tree_response(
    story_num: int = 999,
    files: list[str] | None = None,
) -> MagicMock:
    """Mock GitHub tree API response with SDLC deliverable files.

    Default files match medium scope requirements:
    seed.md, analysis.md, feature-spec.md, test-design.md
    """
    if files is None:
        prefix = f"features/story-{story_num}-test-slug"
        files = [
            f"{prefix}/seed.md",
            f"{prefix}/analysis.md",
            f"{prefix}/feature-spec.md",
            f"{prefix}/test-design.md",
        ]
    tree = [{"path": f, "type": "blob"} for f in files]
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"tree": tree}
    return response
```

### 4. `tests/contracts/fixtures/quota_fixtures.py`

Provides mock factories for the Loki→QuotaInfo pipeline.

```python
"""Deterministic fixtures for usage→quota pipeline contract tests.

All fixtures return mock objects — no real Loki, no real agent VMs.
Import explicitly in scenario files.
"""
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Any

def make_usage_lines(token_amounts: list[int]) -> list[str]:
    """Generate deterministic [USAGE] log lines with known token amounts.

    Args:
        token_amounts: List of token counts. Each generates one [USAGE] line.

    Returns:
        List of log line strings matching the format _parse_usage_line expects.
    """
    lines = []
    for i, tokens in enumerate(token_amounts):
        cost = round(tokens * 0.000003, 6)  # ~$3/1M tokens
        lines.append(f"[USAGE] total_tokens={tokens} cost_usd={cost}")
    return lines

def mock_loki_entries(lines: list[str]) -> list:
    """Create mock LokiLogEntry objects from raw line strings.

    Returns objects with .line attribute matching LokiLogEntry interface.
    """
    entries = []
    for line in lines:
        entry = MagicMock()
        entry.line = line
        entry.timestamp = "2026-04-23T12:00:00Z"
        entry.labels = {"agent": "test-agent"}
        entries.append(entry)
    return entries

def mock_loki_client_success(token_amounts: list[int]) -> AsyncMock:
    """Mock LokiClient whose query_range returns valid [USAGE] entries.

    Args:
        token_amounts: Token counts for each [USAGE] line.
    """
    lines = make_usage_lines(token_amounts)
    entries = mock_loki_entries(lines)
    client = AsyncMock()
    client.query_range = AsyncMock(return_value=entries)
    return client

def mock_loki_client_error() -> AsyncMock:
    """Mock LokiClient whose query_range raises LokiError.

    Simulates Loki unreachable — should produce NO_DATA with LOKI_ERROR reason.
    """
    from tech_dev_agents.ops_console.services.loki_client import LokiError
    client = AsyncMock()
    client.query_range = AsyncMock(side_effect=LokiError(502, "Bad Gateway"))
    return client

def mock_loki_client_empty() -> AsyncMock:
    """Mock LokiClient whose query_range returns empty list.

    Simulates no matching entries — should produce NO_DATA with EMPTY_RESULT.
    """
    client = AsyncMock()
    client.query_range = AsyncMock(return_value=[])
    return client

def mock_loki_client_unparseable() -> AsyncMock:
    """Mock LokiClient whose query_range returns entries that don't parse.

    Simulates [USAGE] format drift — should produce NO_DATA with PARSE_MISS.
    """
    garbage_lines = ["not a usage line", "random garbage", "[USAGE] malformed"]
    entries = mock_loki_entries(garbage_lines)
    client = AsyncMock()
    client.query_range = AsyncMock(return_value=entries)
    return client

def fake_quota_info(**overrides) -> dict[str, Any]:
    """Factory for QuotaInfo-compatible dicts with sensible defaults."""
    defaults = {
        "source": "loki",
        "current_block_tokens": 50000,
        "current_block_cost_usd": 0.15,
        "sessions_in_block": 3,
        "reset_in_minutes": 120,
        "remaining_tokens": 150000,
        "block_start": "10:00Z",
        "block_end": "15:00Z",
    }
    defaults.update(overrides)
    return defaults
```

### 5. `tests/contracts/scenarios/test_completion_fail_closed.py`

**Contract under test:** `POST /dispatch/complete/{story_id}` MUST fail-closed when GitHub API is unreachable (422), fail-closed when commit not found (422), succeed when commit exists + SDLC files present (200), and fail-open on SDLC check network failure (warn but don't block).

**Test strategy:** Call `_github_commit_exists()` directly with mocked `http_client` for the commit-verification invariants. For the SDLC fail-open invariant, mock the tree-fetch response to raise, then verify the completion flow continues. This tests the actual production functions, not reimplementations.

```python
"""Contract: Completion endpoint fail-closed behavior.

Invariant: GitHub API unreachable → 422 (never fail-open on commit verification).
Invariant: SDLC deliverable check failure → warn + continue (fail-open on SDLC, not on SHA).

Code under test:
  - tech_dev_agents.ops_console.routes.dispatch._github_commit_exists
  - tech_dev_agents.ops_console.routes.dispatch.complete_story (route handler)
"""
import pytest
from tests.contracts.harness.base import contract_critical
from tests.contracts.fixtures.completion_fixtures import (
    FAKE_GITHUB_TOKEN,
    fake_commit_sha,
    fake_dispatch_item,
    mock_github_api_success,
    mock_github_api_not_found,
    mock_github_api_network_error,
)

pytestmark = [contract_critical]


class TestCompletionFailClosed:
    """Verifies fail-closed semantics for GitHub commit SHA verification."""

    contract_name = "completion-fail-closed"
    invariant = "GitHub API unreachable → 422 (never fail-open)"

    @pytest.mark.asyncio
    async def test_network_error_returns_false(self):
        """_github_commit_exists returns False on network error (fail-closed)."""
        ...

    @pytest.mark.asyncio
    async def test_commit_not_found_returns_false(self):
        """_github_commit_exists returns False when GitHub returns 404."""
        ...

    @pytest.mark.asyncio
    async def test_commit_found_returns_true(self):
        """_github_commit_exists returns True when GitHub returns 200."""
        ...


class TestCompletionSdlcGateFailOpen:
    """Verifies SDLC deliverable check is fail-open (warn, don't block)."""

    contract_name = "completion-sdlc-gate"
    invariant = "SDLC check network failure → warn + continue (fail-open)"

    @pytest.mark.asyncio
    async def test_sdlc_network_failure_does_not_block(self):
        """When SDLC tree-fetch fails but commit SHA was verified, completion proceeds."""
        ...
```

**Key design decisions:**

1. **Test `_github_commit_exists` directly** — It's the function that implements the fail-closed contract. Testing it directly (with mocked `http_client`) is more precise than testing the full route handler, which would require mocking the entire FastAPI dependency chain (db_svc, settings, etc.).

2. **Use `retry_delay_seconds=0`** — Pass `retry_delay_seconds=0` to `_github_commit_exists` in tests to eliminate `asyncio.sleep` calls, keeping tests fast and deterministic.

3. **SDLC fail-open test** — For the SDLC gate fail-open test, we test the route handler logic more broadly since the SDLC check is inline in the handler. This requires mocking `db_svc`, `settings`, and `http_client` — but the fixture factories make this manageable.

### 6. `tests/contracts/scenarios/test_usage_quota_pipeline.py`

**Contract under test:** Loki [USAGE] aggregation → QuotaInfo → pacing status derivation.

**Test strategy:** Two layers tested independently:
- **Layer 1:** `LokiClient.query_agent_quota()` — mock `self.query_range()` to return canned entries; verify QuotaInfo fields
- **Layer 2:** `_derive_pacing()` — pure function, no mocking needed; parametrize threshold inputs

```python
"""Contract: Usage → Quota pipeline integrity.

Invariant: Valid [USAGE] lines → QuotaInfo(source=LOKI) with correct aggregation.
Invariant: Loki failure → QuotaInfo(source=NO_DATA) with classified reason code.
Invariant: Pacing thresholds are correctly derived from token/P90 ratio.

Code under test:
  - tech_dev_agents.ops_console.services.loki_client.query_agent_quota
  - tech_dev_agents.ops_console.services.loki_client._parse_usage_line
  - tech_dev_agents.ops_console.routes.agents._derive_pacing
"""
import pytest
from tests.contracts.harness.base import contract_critical
from tests.contracts.fixtures.quota_fixtures import (
    make_usage_lines,
    mock_loki_entries,
    mock_loki_client_success,
    mock_loki_client_error,
    mock_loki_client_empty,
    mock_loki_client_unparseable,
)

pytestmark = [contract_critical]


class TestQuotaLokiAggregation:
    """Verifies [USAGE] log line aggregation into QuotaInfo."""

    contract_name = "usage-quota-loki-aggregation"
    invariant = "Valid [USAGE] lines → QuotaInfo(source=LOKI, correct sums)"

    @pytest.mark.asyncio
    async def test_valid_usage_lines_aggregate_correctly(self):
        """3 [USAGE] lines with known tokens → sum matches."""
        ...

    @pytest.mark.asyncio
    async def test_source_is_loki_on_success(self):
        """QuotaInfo.source must be LOKI when entries parse successfully."""
        ...


class TestQuotaNoDataReasons:
    """Verifies no_data reason code classification."""

    contract_name = "usage-quota-no-data-reasons"
    invariant = "Each failure mode → QuotaInfo(source=NO_DATA) with distinct reason"

    @pytest.mark.asyncio
    async def test_loki_error_returns_no_data_with_loki_error_reason(self):
        """LokiError → source=NO_DATA, counter incremented for loki_error."""
        ...

    @pytest.mark.asyncio
    async def test_empty_result_returns_no_data_with_empty_result_reason(self):
        """Empty Loki response → source=NO_DATA, reason=EMPTY_RESULT."""
        ...

    @pytest.mark.asyncio
    async def test_unparseable_lines_returns_no_data_with_parse_miss_reason(self):
        """Garbage [USAGE] lines → source=NO_DATA, reason=PARSE_MISS."""
        ...


class TestQuotaPacingThresholds:
    """Verifies pacing status derivation from token/P90 ratio."""

    contract_name = "usage-quota-pacing"
    invariant = "Pacing status derived correctly from projected usage vs P90"

    @pytest.mark.parametrize("tokens,remaining_min,p90,expected", [
        # ON_TRACK: low usage, plenty of time
        (10000, 200, 200000, "on_track"),
        # APPROACHING_LIMIT: moderate projected usage
        (80000, 150, 200000, "approaching_limit"),
        # EXCEEDED: high projected usage
        (180000, 150, 200000, "exceeded"),
        # UNKNOWN: missing inputs
        (None, 150, 200000, "unknown"),
        (50000, None, 200000, "unknown"),
        (50000, 150, None, "unknown"),
        (50000, 150, 0, "unknown"),
    ])
    def test_pacing_threshold(self, tokens, remaining_min, p90, expected):
        """_derive_pacing returns correct status for each input combination."""
        ...
```

**Key design decisions:**

1. **Test `query_agent_quota` via method patching** — Create a real `LokiClient` instance, then patch `self.query_range` as `AsyncMock`. This tests the actual aggregation logic without calling Loki. Requires patching `datetime.now()` to freeze time for deterministic block window calculations.

2. **Test `_derive_pacing` directly** — It's a pure function (no I/O, no state). Parametrized tests cover the threshold boundaries precisely. No mocking needed.

3. **Counter reset between tests** — Each no_data test calls `_reset_no_data_counters()` in setup (via a fixture or explicit call) to isolate counter assertions.

### 7. `.github/workflows/contract-critical.yml`

```yaml
name: Contract Critical Tests

on:
  pull_request:
    branches: [main]
  push:
    branches: [main]
  workflow_dispatch:

concurrency:
  group: contract-critical-${{ github.ref }}
  cancel-in-progress: true

jobs:
  contract-critical:
    name: Contract-critical invariant tests
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - uses: actions/checkout@v4
        with:
          submodules: false

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'
          cache: 'pip'

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install pytest pytest-asyncio pytest-timeout respx
          if [ -f requirements.txt ]; then pip install -r requirements.txt; fi
          if [ -f requirements-test.txt ]; then pip install -r requirements-test.txt; fi

      - name: Run contract-critical tests
        env:
          PYTHONPATH: ${{ github.workspace }}
        run: |
          pytest tests/contracts/ \
            -m contract_critical \
            --tb=short -q --timeout=30
```

**Design decision — separate workflow vs. job in test.yml:** The seed specifies "Keep this separate from the main test workflow so contract failures are immediately visible in the PR checks list." A separate workflow file means `contract-critical` appears as its own check in the PR, not nested inside the existing `Tests` workflow. This makes contract violations impossible to miss.

### 8. `tests/contracts/README.md`

Documents conventions for adding new scenarios, naming patterns, fixture patterns, and CI integration. Key sections:

- **Adding a New Scenario**: Create file in `scenarios/`, add `@pytest.mark.contract_critical`, document `contract_name` and `invariant` in class or module docstring
- **Naming Conventions**: `test_{system}_{invariant}.py` (e.g., `test_completion_fail_closed.py`)
- **Fixture Patterns**: Create fixture module in `fixtures/`, import explicitly in scenario
- **CI Integration**: Contract tests run in `.github/workflows/contract-critical.yml`, gate merges independently
- **Migration Path**: Existing scattered contract tests (`test_stale_recovery_contract.py`, etc.) can be migrated by: (1) moving to `scenarios/`, (2) adding `@pytest.mark.contract_critical`, (3) updating CI workflow. No code changes needed in the tests themselves.

---

## Error Handling Design

| Error Condition | Behavior | Rationale |
|----------------|----------|-----------|
| `no_network` fixture triggered | `RuntimeError` with clear message pointing to fixture docs | Developer must use mock fixtures; real I/O is a test bug |
| Import error in fixture module | Standard Python ImportError | Fixtures import production code; if production code moves, tests fail loudly at import time |
| Missing `contract_critical` marker | Test not collected by `-m contract_critical` filter | Silently excluded — this is pytest standard behavior, caught by "all scenarios collected" structural check |
| Flaky test (non-deterministic) | Should never happen — all I/O mocked, time frozen | If detected, the `no_network` fixture and frozen time fixtures prevent the root causes |

---

## Failure Modes Table

| Dependency | Unavailable Behavior | Rationale |
|------------|---------------------|-----------|
| Production code imports (`dispatch.py`, `loki_client.py`, `agents.py`) | ImportError → tests fail to collect | Fail-closed: if production code structure changes, contract tests break immediately — this is intentional, it's the contract |
| pytest-asyncio | Tests skip or error | Required dependency; installed in CI workflow |
| socket (patched by `no_network`) | RuntimeError on any real socket call | Fail-closed: contract tests must never make real network calls |

---

## Implementation Plan

### Build Order

| Step | Component | Depends On | Estimated Effort |
|------|-----------|------------|-----------------|
| 1 | `tests/contracts/` directory + `__init__.py` files | Nothing | 5 min |
| 2 | `harness/base.py` (Protocol + marker) | Step 1 | 10 min |
| 3 | `conftest.py` (marker registration + `no_network`) | Step 1 | 10 min |
| 4 | `fixtures/completion_fixtures.py` | Step 1 | 15 min |
| 5 | `fixtures/quota_fixtures.py` | Step 1 | 15 min |
| 6 | `scenarios/test_completion_fail_closed.py` | Steps 2, 3, 4 | 30 min |
| 7 | `scenarios/test_usage_quota_pipeline.py` | Steps 2, 3, 5 | 30 min |
| 8 | `.github/workflows/contract-critical.yml` | Steps 6, 7 | 10 min |
| 9 | `README.md` | All above | 15 min |

### Commit Strategy

1. **Commit 1:** Harness infrastructure (steps 1-3): `__init__.py` files, `base.py`, `conftest.py`
2. **Commit 2:** Fixtures (steps 4-5): `completion_fixtures.py`, `quota_fixtures.py`
3. **Commit 3:** Scenario 1 (step 6): `test_completion_fail_closed.py` — all tests GREEN
4. **Commit 4:** Scenario 2 (step 7): `test_usage_quota_pipeline.py` — all tests GREEN
5. **Commit 5:** CI + docs (steps 8-9): `contract-critical.yml`, `README.md`

---

## Follow-ups (Out of Scope)

- **Migration story:** Move existing `test_stale_recovery_contract.py`, `test_completion_gate_slug_relaxed.py`, `test_quota_no_data_reason_codes.py` into `tests/contracts/scenarios/`
- **Coverage reporting:** Add contract test coverage to CI output
- **Contract inventory command:** `pytest --collect-only tests/contracts/ -m contract_critical` is sufficient for now; a custom CLI would be over-engineering
