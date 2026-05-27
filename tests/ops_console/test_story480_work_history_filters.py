"""Tests for STORY-480: Dashboard Overhaul — Work history query param filters + total_cost_usd.

RED STATE — these tests FAIL until the following are implemented:
  1. `GET /api/work-history` accepts `?agent=<name>` query param and filters results to that agent.
  2. `GET /api/work-history` accepts `?since=<YYYY-MM-DD>` query param and excludes stories
     whose completed_at is before that date.
  3. `CompletedStory` gains field: `total_cost_usd: float | None = None`.

Test IDs: T480-11 through T480-15.
"""

from __future__ import annotations

import pytest
from unittest.mock import patch

from tests.ops_console.conftest import inject_mock_services


# ---------------------------------------------------------------------------
# Fake PR data for patching _fetch_prs
# ---------------------------------------------------------------------------

_DAN_PR = {
    "number": 101,
    "title": "STORY-480: Dashboard overhaul",
    "html_url": "https://github.com/org/repo/pull/101",
    "head": {"ref": "feature/story-480-dan"},
    "user": {"login": "bot-dan"},
    "state": "open",
    "merged_at": None,
    "updated_at": "2026-04-15T10:00:00Z",
}

_DERRICK_PR = {
    "number": 102,
    "title": "STORY-481: Another feature",
    "html_url": "https://github.com/org/repo/pull/102",
    "head": {"ref": "feature/story-481-derrick"},
    "user": {"login": "bot-derrick"},
    "state": "merged",
    "merged_at": "2026-04-10T10:00:00Z",
    "updated_at": "2026-04-10T10:00:00Z",
}

_OLD_DAN_PR = {
    "number": 100,
    "title": "STORY-479: Old feature",
    "html_url": "https://github.com/org/repo/pull/100",
    "head": {"ref": "feature/story-479-dan"},
    "user": {"login": "bot-dan"},
    "state": "merged",
    "merged_at": "2026-03-01T10:00:00Z",
    "updated_at": "2026-03-01T10:00:00Z",
}


async def _fake_fetch_prs(repo, token, state="all"):
    return [_DAN_PR, _DERRICK_PR, _OLD_DAN_PR]


# ---------------------------------------------------------------------------
# T480-11 — ?agent=dan returns only dan's stories
# ---------------------------------------------------------------------------


class TestWorkHistoryAgentFilter:
    """T480-11 & T480-12: GET /api/work-history?agent= filters by agent name."""

    @pytest.mark.asyncio
    async def test_agent_filter_returns_only_target_agent(self, client):
        """T480-11: ?agent=dan returns only stories where agent == 'dan'.

        Fails until the route reads the `agent` query param and filters stories.
        """
        with patch(
            "tech_dev_agents.ops_console.routes.work_history._fetch_prs",
            side_effect=_fake_fetch_prs,
        ):
            resp = await client.get("/api/work-history?agent=dan")

        assert resp.status_code == 200
        data = resp.json()

        assert len(data["stories"]) > 0, (
            "Expected at least one dan story but got none — "
            "check that ?agent= filter doesn't discard all results"
        )
        agents_in_response = {s["agent"] for s in data["stories"]}
        assert agents_in_response == {"dan"}, (
            f"Expected only 'dan' stories, got agents: {agents_in_response}"
        )

    @pytest.mark.asyncio
    async def test_agent_filter_excludes_other_agents(self, client):
        """T480-12: ?agent=derrick excludes dan's stories entirely.

        Fails until the route reads the `agent` query param and filters stories.
        """
        with patch(
            "tech_dev_agents.ops_console.routes.work_history._fetch_prs",
            side_effect=_fake_fetch_prs,
        ):
            resp = await client.get("/api/work-history?agent=derrick")

        assert resp.status_code == 200
        data = resp.json()

        agents_in_response = {s["agent"] for s in data["stories"]}
        assert "dan" not in agents_in_response, (
            f"?agent=derrick should exclude dan, but got agents: {agents_in_response}"
        )


# ---------------------------------------------------------------------------
# T480-13 / T480-14 — ?since= filters by date
# ---------------------------------------------------------------------------


class TestWorkHistorySinceFilter:
    """T480-13 & T480-14: GET /api/work-history?since= filters by completed_at date."""

    @pytest.mark.asyncio
    async def test_since_filter_excludes_old_stories(self, client):
        """T480-13: ?since=2026-04-01 excludes stories completed before April 1, 2026.

        _OLD_DAN_PR has merged_at=2026-03-01 so STORY-479 should be excluded.

        Fails until the route reads the `since` query param and filters stories.
        """
        with patch(
            "tech_dev_agents.ops_console.routes.work_history._fetch_prs",
            side_effect=_fake_fetch_prs,
        ):
            resp = await client.get("/api/work-history?since=2026-04-01")

        assert resp.status_code == 200
        data = resp.json()

        story_ids = [s["story_id"] for s in data["stories"]]
        assert "STORY-479" not in story_ids, (
            f"STORY-479 (merged 2026-03-01) should be excluded by ?since=2026-04-01, "
            f"but found in response: {story_ids}"
        )

    @pytest.mark.asyncio
    async def test_since_filter_keeps_recent_stories(self, client):
        """T480-14: ?since=2026-04-01 keeps stories on or after April 1, 2026.

        _DAN_PR has updated_at=2026-04-15 so STORY-480 should be included.

        Fails until the route reads the `since` query param and filters stories.
        """
        with patch(
            "tech_dev_agents.ops_console.routes.work_history._fetch_prs",
            side_effect=_fake_fetch_prs,
        ):
            resp = await client.get("/api/work-history?since=2026-04-01")

        assert resp.status_code == 200
        data = resp.json()

        story_ids = [s["story_id"] for s in data["stories"]]
        assert "STORY-480" in story_ids, (
            f"STORY-480 (updated 2026-04-15) should be kept by ?since=2026-04-01, "
            f"but not found in response: {story_ids}"
        )


# ---------------------------------------------------------------------------
# T480-15 — CompletedStory includes total_cost_usd
# ---------------------------------------------------------------------------


class TestCompletedStoryTotalCostField:
    """T480-15: Each CompletedStory in work-history response MUST include total_cost_usd."""

    @pytest.mark.asyncio
    async def test_each_story_has_total_cost_usd_field(self, client):
        """T480-15: Every story object in work-history response MUST contain 'total_cost_usd'.

        Fails until total_cost_usd is added to CompletedStory model and serialised in the response.
        """
        with patch(
            "tech_dev_agents.ops_console.routes.work_history._fetch_prs",
            side_effect=_fake_fetch_prs,
        ):
            resp = await client.get("/api/work-history")

        assert resp.status_code == 200
        data = resp.json()

        assert len(data["stories"]) > 0, (
            "Expected at least one story in the response but got none"
        )
        for story in data["stories"]:
            assert "total_cost_usd" in story, (
                f"Story '{story.get('story_id', '?')}' missing 'total_cost_usd' field — "
                "add `total_cost_usd: float | None = None` to CompletedStory"
            )
            value = story["total_cost_usd"]
            assert value is None or isinstance(value, (int, float)), (
                f"total_cost_usd must be float or null, got {type(value).__name__!r}"
            )
