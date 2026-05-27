"""Contract: Usage -> Quota pipeline integrity.

Invariant: Valid [USAGE] lines -> QuotaInfo(source=LOKI) with correct aggregation.
Invariant: Loki failure -> QuotaInfo(source=NO_DATA) with classified reason code.
Invariant: Pacing thresholds are correctly derived from token/P90 ratio.

Code under test:
  - tech_dev_agents.ops_console.services.loki_client.LokiClient.query_agent_quota
  - tech_dev_agents.ops_console.services.loki_client._parse_usage_line
  - tech_dev_agents.ops_console.services.loki_client._no_data
  - tech_dev_agents.ops_console.routes.agents._derive_pacing

STORY-544: Contract-critical tests for usage->quota pipeline.
"""
import pytest
from unittest.mock import AsyncMock, patch

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


def _make_loki_client_with_mock_query_range(mock_query_range: AsyncMock):
    """Create a real LokiClient but replace query_range with the mock.

    This lets query_agent_quota run its real aggregation logic while
    controlling what query_range returns.
    """
    from tech_dev_agents.ops_console.services.loki_client import LokiClient

    client = LokiClient(
        base_url="http://fake-loki:3100",
        api_key="fake-key",
        http_client=AsyncMock(),  # unused — query_range is patched
    )
    client.query_range = mock_query_range
    return client


class TestQuotaLokiAggregation:
    """Verifies [USAGE] log line aggregation into QuotaInfo."""

    contract_name = "usage-quota-loki-aggregation"
    invariant = "Valid [USAGE] lines -> QuotaInfo(source=LOKI, correct sums)"

    @pytest.mark.asyncio
    async def test_valid_usage_lines_aggregate_correctly(self):
        """3 [USAGE] lines with known tokens -> QuotaInfo.current_block_tokens == sum.

        Arrange: Mock LokiClient with query_range returning 3 entries:
                 [USAGE] total_tokens=10000, [USAGE] total_tokens=20000, [USAGE] total_tokens=30000
        Act: Call query_agent_quota("test-agent")
        Assert: current_block_tokens == 60000, sessions_in_block == 3
        """
        from tech_dev_agents.ops_console.models.responses import QuotaSourceEnum

        token_amounts = [10000, 20000, 30000]
        lines = make_usage_lines(token_amounts)
        entries = mock_loki_entries(lines)

        client = _make_loki_client_with_mock_query_range(
            AsyncMock(return_value=entries)
        )
        result = await client.query_agent_quota("test-agent")

        assert result.source == QuotaSourceEnum.LOKI
        assert result.current_block_tokens == 60000, (
            f"CONTRACT VIOLATION: expected 60000 tokens (10000+20000+30000), "
            f"got {result.current_block_tokens}"
        )
        assert result.sessions_in_block == 3

    @pytest.mark.asyncio
    async def test_source_is_loki_on_success(self):
        """QuotaInfo.source must be QuotaSourceEnum.LOKI when entries parse.

        Arrange: Mock LokiClient with valid [USAGE] entries
        Act: Call query_agent_quota("test-agent")
        Assert: result.source == QuotaSourceEnum.LOKI
        """
        from tech_dev_agents.ops_console.models.responses import QuotaSourceEnum

        lines = make_usage_lines([50000])
        entries = mock_loki_entries(lines)

        client = _make_loki_client_with_mock_query_range(
            AsyncMock(return_value=entries)
        )
        result = await client.query_agent_quota("test-agent")

        assert result.source == QuotaSourceEnum.LOKI, (
            "CONTRACT VIOLATION: QuotaInfo.source must be LOKI when [USAGE] lines parse successfully."
        )

    @pytest.mark.asyncio
    async def test_cost_aggregation_correct(self):
        """[USAGE] cost_usd fields are summed correctly in QuotaInfo.

        Arrange: Mock LokiClient with known cost values
        Act: Call query_agent_quota
        Assert: current_block_cost_usd matches expected sum
        """
        token_amounts = [10000, 20000]
        lines = make_usage_lines(token_amounts)
        entries = mock_loki_entries(lines)

        client = _make_loki_client_with_mock_query_range(
            AsyncMock(return_value=entries)
        )
        result = await client.query_agent_quota("test-agent")

        # Cost is ~$3/1M tokens: 10000*0.000003 + 20000*0.000003 = 0.09
        expected_cost = round(10000 * 0.000003 + 20000 * 0.000003, 6)
        assert result.current_block_cost_usd is not None
        assert abs(result.current_block_cost_usd - expected_cost) < 0.001, (
            f"CONTRACT VIOLATION: expected cost ~{expected_cost}, got {result.current_block_cost_usd}"
        )


class TestQuotaNoDataReasons:
    """Verifies no_data reason code classification for each failure mode."""

    contract_name = "usage-quota-no-data-reasons"
    invariant = "Each failure mode -> QuotaInfo(source=NO_DATA) with distinct reason"

    @pytest.fixture(autouse=True)
    def reset_counters(self):
        """Reset no_data counters before each test for isolation."""
        from tech_dev_agents.ops_console.services.loki_client import _reset_no_data_counters

        _reset_no_data_counters()
        yield
        _reset_no_data_counters()

    @pytest.mark.asyncio
    async def test_loki_error_returns_no_data_with_loki_error_reason(self):
        """LokiError -> QuotaInfo(source=NO_DATA), counter incremented for loki_error.

        Arrange: Mock LokiClient whose query_range raises LokiError(502, "Bad Gateway")
        Act: Call query_agent_quota("test-agent")
        Assert: result.source == NO_DATA, get_no_data_counters()["loki_error"] == 1
        """
        from tech_dev_agents.ops_console.models.responses import QuotaSourceEnum
        from tech_dev_agents.ops_console.services.loki_client import (
            LokiError,
            get_no_data_counters,
        )

        client = _make_loki_client_with_mock_query_range(
            AsyncMock(side_effect=LokiError(502, "Bad Gateway"))
        )
        result = await client.query_agent_quota("test-agent")

        assert result.source == QuotaSourceEnum.NO_DATA, (
            "CONTRACT VIOLATION: LokiError must produce source=NO_DATA."
        )
        counters = get_no_data_counters()
        assert counters["loki_error"] == 1, (
            "CONTRACT VIOLATION: LokiError must increment loki_error counter."
        )

    @pytest.mark.asyncio
    async def test_empty_result_returns_no_data_with_empty_result_reason(self):
        """Empty Loki response -> QuotaInfo(source=NO_DATA), reason=EMPTY_RESULT.

        Arrange: Mock LokiClient whose query_range returns []
        Act: Call query_agent_quota("test-agent")
        Assert: result.source == NO_DATA, get_no_data_counters()["empty_result"] == 1
        """
        from tech_dev_agents.ops_console.models.responses import QuotaSourceEnum
        from tech_dev_agents.ops_console.services.loki_client import get_no_data_counters

        client = _make_loki_client_with_mock_query_range(
            AsyncMock(return_value=[])
        )
        result = await client.query_agent_quota("test-agent")

        assert result.source == QuotaSourceEnum.NO_DATA, (
            "CONTRACT VIOLATION: Empty Loki result must produce source=NO_DATA."
        )
        counters = get_no_data_counters()
        assert counters["empty_result"] == 1, (
            "CONTRACT VIOLATION: Empty Loki result must increment empty_result counter."
        )

    @pytest.mark.asyncio
    async def test_unparseable_lines_returns_no_data_with_parse_miss_reason(self):
        """Garbage [USAGE] lines -> QuotaInfo(source=NO_DATA), reason=PARSE_MISS.

        Arrange: Mock LokiClient whose query_range returns entries with unparseable lines
        Act: Call query_agent_quota("test-agent")
        Assert: result.source == NO_DATA, get_no_data_counters()["parse_miss"] == 1
        """
        from tech_dev_agents.ops_console.models.responses import QuotaSourceEnum
        from tech_dev_agents.ops_console.services.loki_client import get_no_data_counters

        garbage_lines = ["not a usage line", "random garbage", "[USAGE] malformed"]
        entries = mock_loki_entries(garbage_lines)

        client = _make_loki_client_with_mock_query_range(
            AsyncMock(return_value=entries)
        )
        result = await client.query_agent_quota("test-agent")

        assert result.source == QuotaSourceEnum.NO_DATA, (
            "CONTRACT VIOLATION: Unparseable [USAGE] lines must produce source=NO_DATA."
        )
        counters = get_no_data_counters()
        assert counters["parse_miss"] == 1, (
            "CONTRACT VIOLATION: Unparseable [USAGE] lines must increment parse_miss counter."
        )


class TestQuotaPacingThresholds:
    """Verifies pacing status derivation from projected token usage vs P90.

    Tests _derive_pacing() directly -- it's a pure function with no I/O.
    The pacing logic projects current usage to end of 5-hour block and
    compares against the P90 limit.
    """

    contract_name = "usage-quota-pacing"
    invariant = "Pacing status derived correctly from projected usage vs P90"

    @pytest.mark.parametrize("tokens,remaining_min,p90,expected", [
        # ON_TRACK: low projected usage relative to P90
        (10000, 250, 200000, "on_track"),
        # APPROACHING_LIMIT: moderate projected usage
        (80000, 150, 200000, "approaching_limit"),
        # EXCEEDED: high projected usage
        (180000, 150, 200000, "exceeded"),
        # UNKNOWN: missing tokens
        (None, 150, 200000, "unknown"),
        # UNKNOWN: missing remaining_min
        (50000, None, 200000, "unknown"),
        # UNKNOWN: missing p90
        (50000, 150, None, "unknown"),
        # UNKNOWN: p90 is zero (division guard)
        (50000, 150, 0, "unknown"),
    ])
    def test_pacing_threshold(self, tokens, remaining_min, p90, expected):
        """_derive_pacing returns correct PacingStatusEnum for each input.

        This is a pure function test -- no mocking needed.
        """
        from tech_dev_agents.ops_console.routes.agents import _derive_pacing
        from tech_dev_agents.ops_console.models.responses import PacingStatusEnum

        result = _derive_pacing(tokens, remaining_min, p90, source="loki")

        expected_enum = PacingStatusEnum(expected)
        assert result == expected_enum, (
            f"CONTRACT VIOLATION: _derive_pacing({tokens}, {remaining_min}, {p90}) "
            f"returned {result.value}, expected {expected}."
        )

    def test_unavailable_source_returns_unknown(self):
        """_derive_pacing returns UNKNOWN when source is 'unavailable'.

        Even with valid token/p90 values, unavailable source -> UNKNOWN.
        """
        from tech_dev_agents.ops_console.routes.agents import _derive_pacing
        from tech_dev_agents.ops_console.models.responses import PacingStatusEnum

        result = _derive_pacing(50000, 150, 200000, source="unavailable")

        assert result == PacingStatusEnum.UNKNOWN, (
            "CONTRACT VIOLATION: source='unavailable' must return UNKNOWN regardless of token values."
        )


class TestQuotaOutputVariance:
    """Output-variance gate: different Loki inputs -> different QuotaInfo outputs.

    Verifies the pipeline actually processes inputs (not a stub returning constant).
    """

    contract_name = "usage-quota-output-variance"
    invariant = "Different [USAGE] inputs -> different QuotaInfo token sums"

    @pytest.mark.asyncio
    async def test_different_token_amounts_produce_different_sums(self):
        """10000 tokens vs 90000 tokens -> different current_block_tokens."""
        small_lines = make_usage_lines([10000])
        small_entries = mock_loki_entries(small_lines)
        large_lines = make_usage_lines([90000])
        large_entries = mock_loki_entries(large_lines)

        small_client = _make_loki_client_with_mock_query_range(
            AsyncMock(return_value=small_entries)
        )
        large_client = _make_loki_client_with_mock_query_range(
            AsyncMock(return_value=large_entries)
        )

        result_small = await small_client.query_agent_quota("test-agent")
        result_large = await large_client.query_agent_quota("test-agent")

        assert result_small.current_block_tokens != result_large.current_block_tokens, (
            "STUB DETECTION: different [USAGE] inputs must produce different token sums."
        )
        assert result_small.current_block_tokens == 10000
        assert result_large.current_block_tokens == 90000


class TestCiWorkflowExists:
    """Structural test: CI workflow file exists and targets contract tests."""

    contract_name = "ci-contract-critical-workflow"
    invariant = "CI workflow gates merges on contract-critical test health"

    def test_workflow_file_exists(self):
        """`.github/workflows/contract-critical.yml` must exist."""
        from pathlib import Path

        workflow = Path(".github/workflows/contract-critical.yml")
        assert workflow.exists(), (
            "CONTRACT VIOLATION: .github/workflows/contract-critical.yml must exist "
            "to gate merges on contract-critical test health."
        )

    def test_workflow_contains_contract_critical_marker(self):
        """CI workflow must run pytest with -m contract_critical."""
        from pathlib import Path

        workflow = Path(".github/workflows/contract-critical.yml")
        if not workflow.exists():
            pytest.fail("Workflow file not yet created.")

        content = workflow.read_text()
        assert "contract_critical" in content, (
            "CONTRACT VIOLATION: CI workflow must filter on contract_critical marker."
        )
        assert "pytest" in content, (
            "CONTRACT VIOLATION: CI workflow must run pytest."
        )
        assert "tests/contracts/" in content, (
            "CONTRACT VIOLATION: CI workflow must target tests/contracts/ directory."
        )
