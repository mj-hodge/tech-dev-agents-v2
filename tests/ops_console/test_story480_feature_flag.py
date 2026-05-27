"""Tests for STORY-480: Dashboard Overhaul — Feature flag gating.

Verifies that /api/work-history filter params return 404 when
DASHBOARD_OVERHAUL_ENABLED is False (the production default).

Test IDs: T480-FF-01, T480-FF-02.
"""

from __future__ import annotations

import pytest
import httpx
import pytest_asyncio

from tech_dev_agents.ops_console.config import Settings
from tech_dev_agents.ops_console.main import create_app

TEST_API_KEY = "test-ops-console-key-12345"


@pytest.fixture
def flag_off_settings(registry_file, tmp_path):
    """Settings with DASHBOARD_OVERHAUL_ENABLED=False (production default)."""
    return Settings(
        ops_console_api_key=TEST_API_KEY,
        loki_api_key="test-loki-key",
        agent_api_key="test-agent-key",
        agent_registry_path=registry_file,
        azure_subscription_id=None,
        database_url="",
        dispatch_queue_path=str(tmp_path / "dispatch-queue-flag-off.json"),
        dashboard_overhaul_enabled=False,
    )


@pytest.fixture
def app_flag_off(flag_off_settings):
    """FastAPI app with dashboard overhaul feature flag disabled."""
    return create_app(settings=flag_off_settings)


@pytest_asyncio.fixture
async def client_flag_off(app_flag_off):
    """Authenticated httpx AsyncClient for feature-flag-off tests."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_flag_off), base_url="http://test"
    ) as c:
        c.headers["X-API-Key"] = TEST_API_KEY
        yield c


class TestWorkHistoryFeatureFlagOff:
    """T480-FF-01 & T480-FF-02: Filter params return 404 when flag is off."""

    @pytest.mark.asyncio
    async def test_agent_filter_returns_404_when_flag_off(self, client_flag_off):
        """T480-FF-01: GET /api/work-history?agent=dan returns 404 when DASHBOARD_OVERHAUL_ENABLED=False."""
        resp = await client_flag_off.get("/api/work-history?agent=dan")
        assert resp.status_code == 404, (
            f"Expected 404 when DASHBOARD_OVERHAUL_ENABLED=False and ?agent= is used, "
            f"got {resp.status_code}"
        )

    @pytest.mark.asyncio
    async def test_since_filter_returns_404_when_flag_off(self, client_flag_off):
        """T480-FF-02: GET /api/work-history?since=2026-04-01 returns 404 when DASHBOARD_OVERHAUL_ENABLED=False."""
        resp = await client_flag_off.get("/api/work-history?since=2026-04-01")
        assert resp.status_code == 404, (
            f"Expected 404 when DASHBOARD_OVERHAUL_ENABLED=False and ?since= is used, "
            f"got {resp.status_code}"
        )
