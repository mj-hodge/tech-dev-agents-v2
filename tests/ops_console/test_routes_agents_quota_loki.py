"""Tests for the Loki-based quota route — STORY-513 Phase 7 (RED state).

These tests verify that GET /api/agents/{name}/quota switches from SSH/ccusage
to Loki as its data source.  They are intentionally failing in RED state because:
  - QuotaSourceEnum.LOKI and QuotaSourceEnum.NO_DATA do not exist yet
  - The route still calls _ssh_runner / create_subprocess_exec
  - LokiClient.query_agent_quota() does not exist yet

They will pass once Phase 8 implements STORY-513.

AC-1: No subprocess.run / SSH called by quota endpoint
AC-3: Route returns 200 with source="loki" when Loki has data
AC-5: source="no_data" when Loki has no entries (not "unavailable")
AC-6: Both data and no-data paths return 200 (never 5xx)
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio

from tech_dev_agents.agent_dashboard import AgentNotFoundError
from tech_dev_agents.ops_console.config import Settings
from tech_dev_agents.ops_console.main import create_app
from tech_dev_agents.ops_console.models.responses import QuotaInfo
import tech_dev_agents.ops_console.routes.agents as agents_mod

from tests.ops_console.conftest import (
    TEST_API_KEY,
    TEST_AGENT_REGISTRY,
    _make_agent_records,
    inject_mock_services,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_quota_settings(registry_file: str, tmp_path) -> Settings:
    """Return Settings with agent_quota_enabled=True."""
    return Settings(
        ops_console_api_key=TEST_API_KEY,
        loki_api_key="test-loki-key",
        agent_api_key="test-agent-key",
        agent_registry_path=registry_file,
        azure_subscription_id=None,
        database_url="",
        dispatch_queue_path=str(tmp_path / "dispatch-queue.json"),
        dashboard_overhaul_enabled=True,
        agent_quota_enabled=True,
    )


def _make_no_op_ssh_runner():
    """Return an async callable that simulates 'SSH disabled' — returns empty."""
    async def _runner(agent_name: str, remote_cmd: str, timeout_s: float = 10.0):
        return (-1, b"", b"ssh_disabled_in_test")
    return _runner


def _make_loki_quota_info(source_str: str = "loki", **kwargs) -> QuotaInfo:
    """Build a QuotaInfo dict-like object that the mock loki_client returns.

    Note: QuotaSourceEnum.LOKI does not exist yet, so we build a QuotaInfo
    with source=UNAVAILABLE (default) and then override it via a MagicMock
    wrapper for assertions.  The route tests check the JSON string value.
    """
    # We cannot set source="loki" directly since QuotaSourceEnum.LOKI DNE.
    # Return a MagicMock that looks like QuotaInfo and serializes correctly.
    info = MagicMock(spec=QuotaInfo)
    info.source = source_str
    info.pacing_status = kwargs.get("pacing_status", "unknown")
    info.current_block_tokens = kwargs.get("current_block_tokens", None)
    info.current_block_cost_usd = kwargs.get("current_block_cost_usd", None)
    info.time_remaining_minutes = kwargs.get("time_remaining_minutes", None)
    info.percent_used = kwargs.get("percent_used", None)
    info.reset_in_minutes = kwargs.get("reset_in_minutes", None)
    info.block_start = kwargs.get("block_start", None)
    info.block_end = kwargs.get("block_end", None)
    info.remaining_tokens = kwargs.get("remaining_tokens", None)
    info.p90_limit = kwargs.get("p90_limit", None)
    info.sessions_in_block = kwargs.get("sessions_in_block", None)
    # model_dump() is used by FastAPI for serialization
    info.model_dump.return_value = {
        "source": source_str,
        "pacing_status": info.pacing_status,
        "current_block_tokens": info.current_block_tokens,
        "current_block_cost_usd": info.current_block_cost_usd,
        "time_remaining_minutes": info.time_remaining_minutes,
        "percent_used": info.percent_used,
        "reset_in_minutes": info.reset_in_minutes,
        "block_start": info.block_start,
        "block_end": info.block_end,
        "remaining_tokens": info.remaining_tokens,
        "p90_limit": info.p90_limit,
        "sessions_in_block": info.sessions_in_block,
    }
    return info


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def quota_registry_file(tmp_path):
    """Write test registry to a temp JSON file."""
    path = tmp_path / "agent-registry.json"
    path.write_text(json.dumps(TEST_AGENT_REGISTRY))
    return str(path)


@pytest.fixture
def quota_settings(quota_registry_file, tmp_path) -> Settings:
    """Settings with agent_quota_enabled=True."""
    return _make_quota_settings(quota_registry_file, tmp_path)


@pytest_asyncio.fixture
async def quota_app(quota_settings):
    """FastAPI app with quota feature flag enabled."""
    application = create_app(settings=quota_settings)
    yield application


@pytest_asyncio.fixture
async def quota_client(quota_app):
    """Authenticated httpx AsyncClient against the quota-enabled app."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=quota_app), base_url="http://test"
    ) as c:
        c.headers["X-API-Key"] = TEST_API_KEY
        yield c


@pytest.fixture
def mock_agent_service():
    """AgentService mock: 'dan' and 'derrick' are valid agents."""
    service = MagicMock()
    service.get_registry.return_value = _make_agent_records()
    service.get_agent.return_value = _make_agent_records()[0]
    service.get_all_health = AsyncMock(return_value=[])
    service.get_agent_health = AsyncMock(return_value=None)
    return service


@pytest.fixture
def mock_loki_client():
    """Mocked LokiClient with query_agent_quota as an AsyncMock."""
    client = AsyncMock()
    client.is_reachable.return_value = True
    client.query_range.return_value = []
    client.query_cost_summaries.return_value = []
    client.query_done_lines.return_value = []
    client.query_anomalies.return_value = []
    client.query_sdk_health.return_value = []
    client.query_terminal_guard.return_value = []
    client.get_agent_queue.return_value = None
    # STORY-513: new method — returns loki source by default
    client.query_agent_quota = AsyncMock(
        return_value=_make_loki_quota_info(
            source_str="loki",
            current_block_tokens=412900,
            current_block_cost_usd=1.87,
            sessions_in_block=7,
            reset_in_minutes=163,
            block_start="15:00Z",
            block_end="20:00Z",
        )
    )
    return client


@pytest.fixture(autouse=True)
def _clear_quota_cache():
    """Clear module-level quota cache before/after each test."""
    cache = getattr(agents_mod, "_QUOTA_CACHE", None)
    if cache is not None:
        cache.clear()
    yield
    cache = getattr(agents_mod, "_QUOTA_CACHE", None)
    if cache is not None:
        cache.clear()


@pytest.fixture(autouse=True)
def _disable_real_ssh(monkeypatch):
    """Autouse: patch _ssh_runner to a no-op sentinel so no real SSH is attempted."""
    monkeypatch.setattr(agents_mod, "_ssh_runner", _make_no_op_ssh_runner())


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


class TestQuotaRouteLoki:
    """STORY-513: Quota route switched from SSH to Loki data source."""

    @pytest.mark.asyncio
    async def test_quota_route_returns_loki_source_when_data_available(
        self, quota_client, quota_app, mock_agent_service, mock_loki_client
    ):
        """AC-3: Route returns 200 with source="loki" when Loki has data.

        RED state: Route returns source="unavailable" (uses SSH path, not Loki).
        """
        # Arrange
        inject_mock_services(
            quota_app,
            agent_service=mock_agent_service,
            loki_client=mock_loki_client,
        )

        # Act
        resp = await quota_client.get("/api/agents/dan/quota")

        # Assert
        assert resp.status_code == 200
        data = resp.json()
        assert data["agent"] == "dan"
        assert data["quota"]["source"] == "loki"  # FAILS in RED state

    @pytest.mark.asyncio
    async def test_quota_route_returns_no_data_not_unavailable_when_loki_empty(
        self, quota_client, quota_app, mock_agent_service, mock_loki_client
    ):
        """AC-5: Empty Loki data → source="no_data", not "unavailable".

        RED state: Route returns source="unavailable" (old SSH code path).
        """
        # Arrange — override loki mock to return no_data
        mock_loki_client.query_agent_quota.return_value = _make_loki_quota_info(
            source_str="no_data"
        )
        inject_mock_services(
            quota_app,
            agent_service=mock_agent_service,
            loki_client=mock_loki_client,
        )

        # Act
        resp = await quota_client.get("/api/agents/dan/quota")

        # Assert
        assert resp.status_code == 200
        quota = resp.json()["quota"]
        assert quota["source"] == "no_data"  # FAILS in RED state (returns "unavailable")
        assert quota["current_block_tokens"] is None

    @pytest.mark.asyncio
    async def test_quota_route_no_subprocess_called(
        self, quota_client, quota_app, mock_agent_service, mock_loki_client
    ):
        """AC-1: Regression — no asyncio subprocess called for quota endpoint.

        RED state: create_subprocess_exec IS called by the old SSH path.
        """
        # Arrange
        inject_mock_services(
            quota_app,
            agent_service=mock_agent_service,
            loki_client=mock_loki_client,
        )

        with patch("asyncio.create_subprocess_exec") as mock_subprocess:
            mock_subprocess.return_value = AsyncMock()

            # Act
            resp = await quota_client.get("/api/agents/dan/quota")

        # Assert
        assert resp.status_code == 200
        # FAILS in RED state — old SSH path calls create_subprocess_exec
        assert mock_subprocess.call_count == 0, (
            f"Expected no subprocess calls but got {mock_subprocess.call_count}. "
            "The quota route must use Loki, not SSH."
        )

    @pytest.mark.asyncio
    async def test_quota_route_feature_flag_disabled_returns_404(
        self, quota_registry_file, tmp_path
    ):
        """AC-6: agent_quota_enabled=False → 404 (feature gating preserved).

        This test PASSES in RED state — feature gating already works.
        """
        # Arrange — disabled settings
        settings = Settings(
            ops_console_api_key=TEST_API_KEY,
            loki_api_key="test-loki-key",
            agent_api_key="test-agent-key",
            agent_registry_path=quota_registry_file,
            azure_subscription_id=None,
            database_url="",
            dispatch_queue_path=str(tmp_path / "dispatch-queue.json"),
            dashboard_overhaul_enabled=True,
            agent_quota_enabled=False,
        )
        app = create_app(settings=settings)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as c:
            c.headers["X-API-Key"] = TEST_API_KEY

            # Act
            resp = await c.get("/api/agents/dan/quota")

        # Assert
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_quota_route_unknown_agent_returns_404(
        self, quota_client, quota_app, mock_agent_service, mock_loki_client
    ):
        """AC-6: Unknown agent → 404 (not 5xx). Preserved behavior.

        This test PASSES in RED state — 404 guard already works.
        """
        # Arrange
        mock_agent_service.get_agent.side_effect = AgentNotFoundError(
            "Agent 'nobody' not found"
        )
        inject_mock_services(
            quota_app,
            agent_service=mock_agent_service,
            loki_client=mock_loki_client,
        )

        # Act
        resp = await quota_client.get("/api/agents/nobody/quota")

        # Assert
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_quota_route_returns_200_with_no_data_source(
        self, quota_client, quota_app, mock_agent_service, mock_loki_client
    ):
        """AC-6: Route always returns 200 — even when Loki has no data (never 5xx).

        RED state: Route returns "unavailable" instead of "no_data", but 200 OK
        still holds. This test may partially pass in RED state.
        """
        # Arrange — Loki returns no_data
        mock_loki_client.query_agent_quota.return_value = _make_loki_quota_info(
            source_str="no_data"
        )
        inject_mock_services(
            quota_app,
            agent_service=mock_agent_service,
            loki_client=mock_loki_client,
        )

        # Act
        resp = await quota_client.get("/api/agents/dan/quota")

        # Assert — never 5xx
        assert resp.status_code == 200
        # Source must be either "loki" or "no_data" — never "unavailable" post-513
        source = resp.json()["quota"]["source"]
        assert source in {"loki", "no_data"}, (  # FAILS in RED state (returns "unavailable")
            f"Expected source 'loki' or 'no_data' but got {source!r}. "
            "After STORY-513, 'unavailable' is replaced by 'no_data'."
        )

    @pytest.mark.asyncio
    async def test_quota_route_output_varies_between_agents(
        self, quota_client, quota_app, mock_agent_service, mock_loki_client
    ):
        """AC-6: Different agents return different token counts from Loki.

        RED state: Route uses SSH, ignores loki_client entirely.
        Both calls return source="unavailable" with null tokens — difference test fails.
        """
        # Arrange — make agent_service return both dan and derrick
        def _get_agent_by_name(name: str):
            records = _make_agent_records()
            for r in records:
                if r.name == name:
                    return r
            raise AgentNotFoundError(f"Agent '{name}' not found")

        mock_agent_service.get_agent.side_effect = _get_agent_by_name

        # Loki returns different data per agent
        def _quota_for_agent(agent_name: str):
            if agent_name == "dan":
                return _make_loki_quota_info(
                    source_str="loki",
                    current_block_tokens=412900,
                )
            return _make_loki_quota_info(
                source_str="loki",
                current_block_tokens=85000,
            )

        mock_loki_client.query_agent_quota.side_effect = _quota_for_agent

        inject_mock_services(
            quota_app,
            agent_service=mock_agent_service,
            loki_client=mock_loki_client,
        )

        # Act
        dan_resp = await quota_client.get("/api/agents/dan/quota")
        derrick_resp = await quota_client.get("/api/agents/derrick/quota")

        # Assert
        assert dan_resp.status_code == 200
        assert derrick_resp.status_code == 200

        dan_tokens = dan_resp.json()["quota"]["current_block_tokens"]
        derrick_tokens = derrick_resp.json()["quota"]["current_block_tokens"]

        # FAILS in RED state — both return null (SSH path ignores loki_client)
        assert dan_tokens != derrick_tokens, (
            f"Expected different token counts for dan and derrick but both got {dan_tokens!r}. "
            "The route must delegate to loki_client.query_agent_quota(agent_name)."
        )

    @pytest.mark.asyncio
    async def test_quota_route_cache_hit_queries_loki_only_once(
        self, quota_client, quota_app, mock_agent_service, mock_loki_client
    ):
        """AC-6: Two successive requests → loki_client.query_agent_quota called once.

        RED state: loki_client.query_agent_quota is never called (route uses SSH).
        The call_count check fails because 0 != 1.
        """
        # Arrange
        inject_mock_services(
            quota_app,
            agent_service=mock_agent_service,
            loki_client=mock_loki_client,
        )

        # Act — two requests
        resp1 = await quota_client.get("/api/agents/dan/quota")
        resp2 = await quota_client.get("/api/agents/dan/quota")

        # Assert
        assert resp1.status_code == 200
        assert resp2.status_code == 200

        # FAILS in RED state — query_agent_quota is never called
        assert mock_loki_client.query_agent_quota.call_count == 1, (
            f"Expected query_agent_quota called exactly once (cache prevents duplicate "
            f"Loki queries), got {mock_loki_client.query_agent_quota.call_count} calls"
        )
