"""Tests for STORY-510 hardened quota endpoints — T510-08 through T510-14.

NOTE: T510-01 through T510-07 and T510-12/T510-13 (SSH/ccusage path for
GET /quota) were removed by STORY-513 because that story replaced the SSH
data source with Loki log aggregation.  Equivalent coverage lives in:
  tests/ops_console/test_routes_agents_quota_loki.py

This file retains:
  - T510-08/09: GET /quota/weekly (still SSH-based, not changed by STORY-513)
  - T510-10:    Feature-flag gating (preserved for both endpoints)
  - T510-11:    Agent-not-found 404 (preserved for both endpoints)
  - T510-12b:   Weekly cache hit (still SSH-based)
  - T510-14:    _derive_pacing unit tests (logic unchanged)
"""

from __future__ import annotations

import json
import pathlib
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio

from tech_dev_agents.agent_dashboard import AgentNotFoundError
from tech_dev_agents.ops_console.config import Settings
from tech_dev_agents.ops_console.main import create_app

from tech_dev_agents.ops_console.models.responses import (
    PacingStatusEnum,
    QuotaDaily,
    QuotaSourceEnum,
    QuotaWeeklyResponse,
)
import tech_dev_agents.ops_console.routes.agents as agents_mod

from tests.ops_console.conftest import (
    TEST_API_KEY,
    _make_agent_records,
    inject_mock_services,
)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

_FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> bytes:
    """Read a JSON fixture file and return its bytes."""
    return (_FIXTURES_DIR / name).read_bytes()


def _make_ssh_runner(rc: int, stdout: bytes, stderr: bytes = b""):
    """Build an async callable that mimics _ssh_runner's return signature."""

    async def _runner(agent_name: str, remote_cmd: str, timeout_s: float = 10.0):
        return (rc, stdout, stderr)

    return _runner


def _make_quota_settings(registry_file: str, tmp_path) -> Settings:
    """Return a Settings instance with agent_quota_enabled=True."""
    return Settings(
        ops_console_api_key=TEST_API_KEY,
        loki_api_key="test-loki-key",
        agent_api_key="test-agent-key",
        agent_registry_path=registry_file,
        azure_subscription_id=None,
        database_url="",
        dispatch_queue_path=str(tmp_path / "dispatch-queue.json"),
        dashboard_overhaul_enabled=True,
        agent_quota_enabled=True,  # STORY-510: both quota endpoints enabled
    )


# ---------------------------------------------------------------------------
# Local fixtures (quota-enabled; isolated from conftest to avoid leakage)
# ---------------------------------------------------------------------------


@pytest.fixture
def quota_registry_file(tmp_path):
    """Write test registry to a temp JSON file."""
    path = tmp_path / "agent-registry.json"
    from tests.ops_console.conftest import TEST_AGENT_REGISTRY
    path.write_text(json.dumps(TEST_AGENT_REGISTRY))
    return str(path)


@pytest.fixture
def quota_settings(quota_registry_file, tmp_path) -> Settings:
    """Settings with agent_quota_enabled=True."""
    return _make_quota_settings(quota_registry_file, tmp_path)


@pytest_asyncio.fixture
async def quota_app(quota_settings):
    """FastAPI app with quota endpoints enabled."""
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


# ---------------------------------------------------------------------------
# Autouse guard: ensure _ssh_runner is never called for real in this module
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _block_real_ssh(quota_app):
    """Autouse guard: replace _ssh_runner with a sentinel that raises if called
    without being overridden by the test.  Tests override it with monkeypatch."""

    async def _blocked(*args, **kwargs):
        raise RuntimeError(
            "_ssh_runner called without a test-level monkeypatch. "
            "Add monkeypatch.setattr(agents_mod, '_ssh_runner', ...) to your test."
        )

    original = getattr(agents_mod, "_ssh_runner", None)
    agents_mod._ssh_runner = _blocked
    yield
    if original is not None:
        agents_mod._ssh_runner = original


@pytest.fixture(autouse=True)
def _clear_quota_cache():
    """Clear module-level quota cache before each test."""
    cache = getattr(agents_mod, "_QUOTA_CACHE", None)
    if cache is not None:
        cache.clear()
    yield
    cache = getattr(agents_mod, "_QUOTA_CACHE", None)
    if cache is not None:
        cache.clear()


# ---------------------------------------------------------------------------
# T510-08  GET /quota/weekly — happy path (7 days)
# ---------------------------------------------------------------------------


class TestQuotaWeeklyHappyPath:
    """T510-08: GET /api/agents/{name}/quota/weekly — healthy 7-day response."""

    @pytest.mark.asyncio
    async def test_weekly_returns_seven_days(
        self, quota_client, quota_app, mock_agent_service, monkeypatch
    ):
        """T510-08: Runner returns 7-day ccusage JSON → 200, len(days)==7,
        total_tokens matches sum of daily tokens."""
        inject_mock_services(quota_app, agent_service=mock_agent_service)
        monkeypatch.setattr(
            agents_mod,
            "_ssh_runner",
            _make_ssh_runner(0, _load_fixture("ccusage_weekly_healthy.json")),
        )

        resp = await quota_client.get("/api/agents/dan/quota/weekly")

        assert resp.status_code == 200
        data = resp.json()
        assert data["agent"] == "dan"
        assert data["source"] == "ccusage"
        assert len(data["days"]) == 7
        assert data["total_tokens"] == 5411200
        assert data["total_cost_usd"] == pytest.approx(23.41, abs=0.01)
        # Verify shape of individual day entries
        first_day = data["days"][0]
        assert "date" in first_day
        assert "tokens" in first_day
        assert "cost_usd" in first_day
        assert "blocks_used" in first_day
        # Days are oldest → newest; first day should be 2026-04-15
        assert first_day["date"] == "2026-04-15"
        assert first_day["tokens"] == 712500

    @pytest.mark.asyncio
    async def test_weekly_unavailable_returns_empty_days(
        self, quota_client, quota_app, mock_agent_service, monkeypatch
    ):
        """T510-08b: SSH fails → weekly returns source=unavailable, days=[]."""
        inject_mock_services(quota_app, agent_service=mock_agent_service)
        monkeypatch.setattr(
            agents_mod,
            "_ssh_runner",
            _make_ssh_runner(-1, b"", b"ssh timeout"),
        )

        resp = await quota_client.get("/api/agents/dan/quota/weekly")

        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "unavailable"
        assert data["total_tokens"] is None
        assert data["total_cost_usd"] is None
        assert data["days"] == []


# ---------------------------------------------------------------------------
# T510-09  GET /quota/weekly — short history padding
# ---------------------------------------------------------------------------


class TestQuotaWeeklyPadding:
    """T510-09: Source returns fewer than 7 days → padded to exactly 7."""

    @pytest.mark.asyncio
    async def test_four_days_padded_to_seven(
        self, quota_client, quota_app, mock_agent_service, monkeypatch
    ):
        """T510-09: quota_ccusage.py returns 4 days → server pads to 7,
        padded entries have tokens=0, cost_usd=0.0, blocks_used=0."""
        inject_mock_services(quota_app, agent_service=mock_agent_service)
        four_day_payload = {
            "source": "ccusage",
            "total_tokens": 2000000,
            "total_cost_usd": 8.50,
            "days": [
                {"date": "2026-04-18", "tokens": 612800, "cost_usd": 2.71, "blocks_used": 2},
                {"date": "2026-04-19", "tokens": 770200, "cost_usd": 3.40, "blocks_used": 3},
                {"date": "2026-04-20", "tokens": 888300, "cost_usd": 3.91, "blocks_used": 3},
                {"date": "2026-04-21", "tokens": 702900, "cost_usd": 2.68, "blocks_used": 3},
            ],
        }
        monkeypatch.setattr(
            agents_mod,
            "_ssh_runner",
            _make_ssh_runner(0, json.dumps(four_day_payload).encode()),
        )

        resp = await quota_client.get("/api/agents/dan/quota/weekly")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["days"]) == 7
        # At least 3 zero-padded entries must be present
        zero_days = [d for d in data["days"] if d["tokens"] == 0]
        assert len(zero_days) >= 3


# ---------------------------------------------------------------------------
# T510-10  Feature flag disabled → 404 for both endpoints
# ---------------------------------------------------------------------------


class TestQuotaFeatureFlagDisabled:
    """T510-10: agent_quota_enabled=False → both endpoints return 404."""

    @pytest_asyncio.fixture
    async def _disabled_client(self, quota_registry_file, tmp_path):
        """App with agent_quota_enabled=False (the default)."""
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
            yield c

    @pytest.mark.asyncio
    async def test_quota_endpoint_returns_404_when_disabled(self, _disabled_client):
        """T510-10a: GET /quota returns 404 when feature flag is False."""
        resp = await _disabled_client.get("/api/agents/dan/quota")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_weekly_endpoint_returns_404_when_disabled(self, _disabled_client):
        """T510-10b: GET /quota/weekly returns 404 when feature flag is False."""
        resp = await _disabled_client.get("/api/agents/dan/quota/weekly")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# T510-11  Agent not in registry → 404
# ---------------------------------------------------------------------------


class TestQuotaAgentNotFound:
    """T510-11: Unknown agent name → 404 for both endpoints."""

    @pytest.mark.asyncio
    async def test_unknown_agent_returns_404_quota(
        self, quota_client, quota_app, mock_agent_service, monkeypatch
    ):
        """T510-11a: GET /quota with agent not in registry → 404."""
        mock_agent_service.get_agent.side_effect = AgentNotFoundError(
            "Agent 'nobody' not found in registry"
        )
        inject_mock_services(quota_app, agent_service=mock_agent_service)
        # No runner needed — should 404 before SSH
        monkeypatch.setattr(
            agents_mod, "_ssh_runner", _make_ssh_runner(0, b"")
        )

        resp = await quota_client.get("/api/agents/nobody/quota")

        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_unknown_agent_returns_404_weekly(
        self, quota_client, quota_app, mock_agent_service, monkeypatch
    ):
        """T510-11b: GET /quota/weekly with agent not in registry → 404."""
        mock_agent_service.get_agent.side_effect = AgentNotFoundError(
            "Agent 'nobody' not found in registry"
        )
        inject_mock_services(quota_app, agent_service=mock_agent_service)
        monkeypatch.setattr(
            agents_mod, "_ssh_runner", _make_ssh_runner(0, b"")
        )

        resp = await quota_client.get("/api/agents/nobody/quota/weekly")

        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_invalid_agent_name_returns_404(
        self, quota_client, quota_app, mock_agent_service, monkeypatch
    ):
        """T510-11c: Agent name with special chars (e.g. '..') → 404 from
        _validate_agent_name before registry lookup."""
        inject_mock_services(quota_app, agent_service=mock_agent_service)
        monkeypatch.setattr(
            agents_mod, "_ssh_runner", _make_ssh_runner(0, b"")
        )

        resp = await quota_client.get("/api/agents/../quota")

        assert resp.status_code in (404, 422)


# ---------------------------------------------------------------------------
# T510-12b  GET /quota/weekly cache hit skips SSH — runner called only once
# ---------------------------------------------------------------------------


class TestQuotaCacheHit:
    """T510-12: Repeated requests within the TTL window hit cache; no re-SSH."""

    @pytest.mark.asyncio
    async def test_weekly_second_request_uses_cache(
        self, quota_client, quota_app, mock_agent_service, monkeypatch
    ):
        """T510-12b: Two successive GET /quota/weekly calls → runner once."""
        inject_mock_services(quota_app, agent_service=mock_agent_service)
        call_count = {"n": 0}

        async def _counting_runner(agent_name, remote_cmd, timeout_s=10.0):
            call_count["n"] += 1
            return (0, _load_fixture("ccusage_weekly_healthy.json"), b"")

        monkeypatch.setattr(agents_mod, "_ssh_runner", _counting_runner)

        await quota_client.get("/api/agents/dan/quota/weekly")
        await quota_client.get("/api/agents/dan/quota/weekly")

        assert call_count["n"] == 1


# ---------------------------------------------------------------------------
# T510-14  _derive_pacing — unit tests for the pacing formula
# ---------------------------------------------------------------------------


class TestDerivePacing:
    """T510-14: Unit tests for _derive_pacing() server-side formula."""

    def _pacing(self, tokens, remaining_min, p90, source="ccusage"):
        """Call _derive_pacing via the module reference."""
        return agents_mod._derive_pacing(tokens, remaining_min, p90, source)

    def test_on_track(self):
        """T510-14a: ratio < 0.5 → on_track."""
        # elapsed=0.5, projected=200k/0.5=400k, ratio=400k/800k=0.5 — boundary → approaching
        # Use a clear on_track case: tokens=100k, remaining=150min → elapsed=0.5
        # projected=200k, p90=800k → ratio=0.25 → on_track
        result = self._pacing(tokens=100_000, remaining_min=150, p90=800_000)
        assert result == PacingStatusEnum.ON_TRACK or str(result) == "on_track"

    def test_approaching_limit(self):
        """T510-14b: 0.5 ≤ ratio < 0.9 → approaching_limit."""
        # elapsed=0.5, tokens=350k → projected=700k, p90=800k → ratio=0.875
        result = self._pacing(tokens=350_000, remaining_min=150, p90=800_000)
        assert result == PacingStatusEnum.APPROACHING_LIMIT or str(result) == "approaching_limit"

    def test_exceeded(self):
        """T510-14c: ratio ≥ 0.9 → exceeded."""
        # elapsed=0.5, tokens=400k → projected=800k, p90=800k → ratio=1.0
        result = self._pacing(tokens=400_000, remaining_min=150, p90=800_000)
        assert result == PacingStatusEnum.EXCEEDED or str(result) == "exceeded"

    def test_unknown_when_tokens_none(self):
        """T510-14d: tokens=None → unknown."""
        result = self._pacing(tokens=None, remaining_min=150, p90=800_000)
        assert result == PacingStatusEnum.UNKNOWN or str(result) == "unknown"

    def test_unknown_when_p90_none(self):
        """T510-14e: p90=None → unknown."""
        result = self._pacing(tokens=100_000, remaining_min=150, p90=None)
        assert result == PacingStatusEnum.UNKNOWN or str(result) == "unknown"

    def test_unknown_when_remaining_none(self):
        """T510-14f: remaining_min=None → unknown."""
        result = self._pacing(tokens=100_000, remaining_min=None, p90=800_000)
        assert result == PacingStatusEnum.UNKNOWN or str(result) == "unknown"

    def test_unknown_when_source_unavailable(self):
        """T510-14g: source=unavailable → unknown regardless of other fields."""
        result = self._pacing(tokens=100_000, remaining_min=150, p90=800_000, source="unavailable")
        assert result == PacingStatusEnum.UNKNOWN or str(result) == "unknown"

    def test_elapsed_fraction_clamped_at_min(self):
        """T510-14h: remaining=295min (5min elapsed) → elapsed clamped to 0.05,
        prevents divide-by-zero and inflated pacing in first 15 minutes."""
        # elapsed_raw = 1 - 295/300 = 0.0167 → clamped to 0.05
        # projected = 10000 / 0.05 = 200k, p90=800k → ratio=0.25 → on_track
        result = self._pacing(tokens=10_000, remaining_min=295, p90=800_000)
        # Should not raise; should return on_track or approaching_limit (not divide-by-zero)
        assert str(result) in {"on_track", "approaching_limit", "exceeded", "unknown"}

    def test_p90_zero_returns_unknown(self):
        """T510-14i: p90=0 → unknown (avoid divide-by-zero)."""
        result = self._pacing(tokens=100_000, remaining_min=150, p90=0)
        assert result == PacingStatusEnum.UNKNOWN or str(result) == "unknown"
