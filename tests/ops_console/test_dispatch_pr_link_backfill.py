"""STORY-902 — PR Link Backfill Sweeper: Phase 7 Tests (RED -> GREEN).

Tests for dispatch_pr_link_backfill_sweeper and _pr_link_backfill_tick in
tech_dev_agents/ops_console/services/self_healing.py.

Groups:
  A (T01)     — Happy path: 1 PR found, pr_number updated
  B (T02)     — No PR found: DB unchanged, DEBUG logged
  C (T03)     — Multiple PRs: most-recent (highest created_at) picked
  D (T04)     — Idempotent: SQL uses pr_number IS NULL filter
  E (T05)     — GitHub API error: WARNING logged, loop continues
  F (T06)     — Non-STORY-N story_id: row skipped silently
  G (T07-T08) — Background loop: CancelledError exit, pool=None no-op
"""
from __future__ import annotations

import asyncio
import urllib.error
import uuid
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Import targets -- RED until Phase 8 creates the implementation
# ---------------------------------------------------------------------------
from tech_dev_agents.ops_console.services.self_healing import (
    _build_gh_search_url,
    _extract_pr_number_fallback,
    _pr_link_backfill_tick,
    dispatch_pr_link_backfill_sweeper,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

JOB_ID_1 = str(uuid.UUID("aaaaaaaa-0001-0001-0001-000000000001"))
JOB_ID_2 = str(uuid.UUID("aaaaaaaa-0002-0002-0002-000000000002"))


def _rec(data: dict):
    """Mock asyncpg Record-like object from dict."""
    r = MagicMock()
    r.__getitem__ = lambda self, k: data[k]
    r.get = lambda k, d=None: data.get(k, d)
    return r


def _null_row(
    job_id: str = JOB_ID_1,
    repo: str = "tech-dev-agents",
    story_id: str = "STORY-885",
    title: str | None = None,
    prompt: str = "",
):
    return _rec({
        "job_id": job_id,
        "repo": repo,
        "story_id": story_id,
        "title": title,
        "prompt": prompt,
    })


def _mock_pool(rows=None):
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=rows or [])
    pool.execute = AsyncMock()
    return pool


# ---------------------------------------------------------------------------
# Group A -- Happy path: single PR found, pr_number updated (T01)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_T01_happy_path_single_pr_updates_db():
    """T01: gh returns 1 matching PR -> pr_number written to dispatch_jobs."""
    pool = _mock_pool(rows=[_null_row(story_id="STORY-885")])
    gh_result = [{"number": 42, "created_at": "2026-05-01T12:00:00Z"}]
    gh_fetch = AsyncMock(return_value=gh_result)

    await _pr_link_backfill_tick(pool=pool, gh_fetch_fn=gh_fetch)

    # gh was called with branch prefix story-885/
    assert gh_fetch.call_count == 1
    call_kwargs = gh_fetch.call_args
    # branch arg should contain story-885
    assert "story-885" in str(call_kwargs)

    # DB updated with pr_number=42
    pool.execute.assert_called_once()
    exec_args = pool.execute.call_args[0]
    assert 42 in exec_args or any(42 == a for a in exec_args)


@pytest.mark.asyncio
async def test_T01b_happy_path_logs_info(caplog):
    """T01b: INFO log emitted with story=, repo=, pr= when pr_number linked."""
    import logging

    pool = _mock_pool(rows=[_null_row(story_id="STORY-885", repo="tech-dev-agents")])
    gh_result = [{"number": 42, "created_at": "2026-05-01T12:00:00Z"}]
    gh_fetch = AsyncMock(return_value=gh_result)

    with caplog.at_level(logging.INFO, logger="tech_dev_agents.ops_console.services.self_healing"):
        await _pr_link_backfill_tick(pool=pool, gh_fetch_fn=gh_fetch)

    log_text = caplog.text
    assert "pr_link_backfill" in log_text
    assert "STORY-885" in log_text
    assert "42" in log_text


# ---------------------------------------------------------------------------
# Group B -- No PR found: DB unchanged, tick returns cleanly (T02)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_T02_no_pr_found_db_unchanged():
    """T02: gh returns [] -> pool.execute never called."""
    pool = _mock_pool(rows=[_null_row(story_id="STORY-900")])
    gh_fetch = AsyncMock(return_value=[])

    await _pr_link_backfill_tick(pool=pool, gh_fetch_fn=gh_fetch)

    pool.execute.assert_not_called()


@pytest.mark.asyncio
async def test_T02b_no_pr_found_logs_debug(caplog):
    """T02b: DEBUG log emitted when no matching PR found."""
    import logging

    pool = _mock_pool(rows=[_null_row(story_id="STORY-900")])
    gh_fetch = AsyncMock(return_value=[])

    with caplog.at_level(logging.DEBUG, logger="tech_dev_agents.ops_console.services.self_healing"):
        await _pr_link_backfill_tick(pool=pool, gh_fetch_fn=gh_fetch)

    # Should NOT have an INFO pr_link_backfill: linked message
    assert "pr_link_backfill: linked" not in caplog.text


# ---------------------------------------------------------------------------
# Group C -- Multiple PRs: most-recent (highest created_at) picked (T03)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_T03_multiple_prs_picks_most_recent():
    """T03: gh returns 3 PRs -> the one with highest created_at is picked."""
    pool = _mock_pool(rows=[_null_row(story_id="STORY-900")])
    gh_result = [
        {"number": 10, "created_at": "2026-04-01T00:00:00Z"},
        {"number": 20, "created_at": "2026-04-30T00:00:00Z"},  # most recent
        {"number": 15, "created_at": "2026-04-15T00:00:00Z"},
    ]
    gh_fetch = AsyncMock(return_value=gh_result)

    await _pr_link_backfill_tick(pool=pool, gh_fetch_fn=gh_fetch)

    # pr_number=20 must be passed to execute
    exec_args = pool.execute.call_args[0]
    assert 20 in exec_args or any(20 == a for a in exec_args)


# ---------------------------------------------------------------------------
# Group D -- Idempotent: SQL WHERE clause filters NULL rows (T04)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_T04_sql_filters_null_pr_number():
    """T04: pool.fetch is called with SQL containing pr_number IS NULL."""
    pool = _mock_pool(rows=[])  # no NULL rows -> nothing to do
    gh_fetch = AsyncMock(return_value=[])

    await _pr_link_backfill_tick(pool=pool, gh_fetch_fn=gh_fetch)

    pool.fetch.assert_called_once()
    sql_arg = pool.fetch.call_args[0][0]
    assert "pr_number IS NULL" in sql_arg or "pr_number is NULL" in sql_arg.lower()


@pytest.mark.asyncio
async def test_T04b_sql_filters_in_review_state():
    """T04b: SQL also filters state='in_review'."""
    pool = _mock_pool(rows=[])
    gh_fetch = AsyncMock(return_value=[])

    await _pr_link_backfill_tick(pool=pool, gh_fetch_fn=gh_fetch)

    sql_arg = pool.fetch.call_args[0][0]
    assert "in_review" in sql_arg


# ---------------------------------------------------------------------------
# Group D2 -- URL construction: _build_gh_search_url (T04c, T04d)
# ---------------------------------------------------------------------------


def test_T04c_build_gh_search_url_uses_search_api():
    """T04c: _build_gh_search_url returns a search/issues URL, not /pulls?head=."""
    url = _build_gh_search_url("tech-dev-agents", "story-885/")
    assert "/search/issues" in url
    # Must NOT use the /pulls?head= pattern (which breaks with slashes)
    assert "/pulls?" not in url


def test_T04d_build_gh_search_url_encodes_branch_with_slash():
    """T04d: branch prefix with slash (story-NNN/slug) is properly encoded in the URL."""
    url = _build_gh_search_url("tech-dev-agents", "story-885/")
    # The query should contain head:story-885/ (URL-encoded)
    assert "head%3Astory-885%2F" in url or "head:story-885/" in url
    # Repo qualifier must be present
    assert "hpi-gorillacommerce" in url
    assert "tech-dev-agents" in url
    assert "is%3Apr" in url or "is:pr" in url


# ---------------------------------------------------------------------------
# Group E -- GitHub API error: WARNING logged, no crash (T05)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_T05_github_api_error_logged_no_crash(caplog):
    """T05: gh_fetch_fn raises URLError -> WARNING logged, no exception re-raised."""
    import logging

    pool = _mock_pool(rows=[_null_row(story_id="STORY-900")])
    gh_fetch = AsyncMock(side_effect=urllib.error.URLError("network failure"))

    with caplog.at_level(logging.WARNING, logger="tech_dev_agents.ops_console.services.self_healing"):
        # Must not raise
        await _pr_link_backfill_tick(pool=pool, gh_fetch_fn=gh_fetch)

    pool.execute.assert_not_called()


@pytest.mark.asyncio
async def test_T05b_github_http_error_no_crash():
    """T05b: gh_fetch_fn raises OSError (HTTP 500 path) -> tick returns without crash."""
    pool = _mock_pool(rows=[_null_row(story_id="STORY-900")])
    gh_fetch = AsyncMock(side_effect=OSError("HTTP Error 500"))

    # Must not raise
    await _pr_link_backfill_tick(pool=pool, gh_fetch_fn=gh_fetch)

    pool.execute.assert_not_called()


# ---------------------------------------------------------------------------
# Group F -- Non-STORY-N story_id: row skipped silently (T06)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_T06_non_story_id_skipped():
    """T06: story_id='EPIC-5' doesn't match STORY-N pattern -> gh not called."""
    pool = _mock_pool(rows=[_null_row(story_id="EPIC-5")])
    gh_fetch = AsyncMock(return_value=[])

    await _pr_link_backfill_tick(pool=pool, gh_fetch_fn=gh_fetch)

    gh_fetch.assert_not_called()
    pool.execute.assert_not_called()


@pytest.mark.asyncio
async def test_T06b_empty_story_id_skipped():
    """T06b: story_id=None or empty string -> row skipped."""
    pool = _mock_pool(rows=[_null_row(story_id="")])
    gh_fetch = AsyncMock(return_value=[])

    await _pr_link_backfill_tick(pool=pool, gh_fetch_fn=gh_fetch)

    gh_fetch.assert_not_called()
    pool.execute.assert_not_called()


# ---------------------------------------------------------------------------
# Group G -- Background loop behavior (T07, T08)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_T07_cancelled_error_exits_loop():
    """T07: CancelledError from asyncio.sleep propagates, loop exits cleanly."""
    tick_mock = AsyncMock()
    pool = MagicMock()

    async def _fake_sleep(_):
        raise asyncio.CancelledError()

    with patch(
        "tech_dev_agents.ops_console.services.self_healing._pr_link_backfill_tick",
        tick_mock,
    ):
        with patch("asyncio.sleep", side_effect=_fake_sleep):
            with pytest.raises(asyncio.CancelledError):
                await dispatch_pr_link_backfill_sweeper(pool=pool)

    # tick was called at least once before cancel
    assert tick_mock.call_count >= 1


@pytest.mark.asyncio
async def test_T08_pool_none_tick_skipped():
    """T08: pool=None -> tick still called (pool check inside tick), loop continues."""
    call_count = 0

    async def _fake_sleep(_):
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            raise asyncio.CancelledError()

    tick_mock = AsyncMock()

    with patch(
        "tech_dev_agents.ops_console.services.self_healing._pr_link_backfill_tick",
        tick_mock,
    ):
        with patch("asyncio.sleep", side_effect=_fake_sleep):
            with pytest.raises(asyncio.CancelledError):
                await dispatch_pr_link_backfill_sweeper(pool=None)

    # tick called each iteration regardless of pool (pool=None guard is inside tick)
    assert tick_mock.call_count >= 1


# ===========================================================================
# STORY-919 — Fallback regex parser for rework jobs
# ===========================================================================


# ---------------------------------------------------------------------------
# Group H — _extract_pr_number_fallback regex tests (T09-T14)
# ---------------------------------------------------------------------------


class TestExtractPrNumberFallback:
    """Unit tests for _extract_pr_number_fallback."""

    def test_T09_pr_hash_number(self):
        """T09: 'PR #320 rework: foo' -> 320."""
        row = _rec({"title": "PR #320 rework: foo", "prompt": ""})
        assert _extract_pr_number_fallback(row) == 320

    def test_T09b_pr_space_number(self):
        """T09b: 'Rebase PR 319 onto main' -> 319."""
        row = _rec({"title": "Rebase PR 319 onto main", "prompt": ""})
        assert _extract_pr_number_fallback(row) == 319

    def test_T09c_fix_pr_hash(self):
        """T09c: 'Fix PR #404 review findings' -> 404."""
        row = _rec({"title": "Fix PR #404 review findings", "prompt": ""})
        assert _extract_pr_number_fallback(row) == 404

    def test_T09d_pulls_slash(self):
        """T09d: 'pulls/352' -> 352."""
        row = _rec({"title": "pulls/352", "prompt": ""})
        assert _extract_pr_number_fallback(row) == 352

    def test_T09e_pull_slash(self):
        """T09e: 'pull/90 needs update' -> 90."""
        row = _rec({"title": "pull/90 needs update", "prompt": ""})
        assert _extract_pr_number_fallback(row) == 90

    def test_T10_no_pr_keyword_returns_none(self):
        """T10: 'STORY-822 rework' -> None (no PR keyword)."""
        row = _rec({"title": "STORY-822 rework", "prompt": ""})
        assert _extract_pr_number_fallback(row) is None

    def test_T10b_empty_title_and_prompt(self):
        """T10b: empty title + empty prompt -> None."""
        row = _rec({"title": "", "prompt": ""})
        assert _extract_pr_number_fallback(row) is None

    def test_T10c_none_title(self):
        """T10c: None title + empty prompt -> None."""
        row = _rec({"title": None, "prompt": ""})
        assert _extract_pr_number_fallback(row) is None

    def test_T11_prompt_fallback(self):
        """T11: title has no PR ref, prompt has 'Fix PR #99' -> 99."""
        row = _rec({"title": "some rework task", "prompt": "Fix PR #99 review findings"})
        assert _extract_pr_number_fallback(row) == 99

    def test_T11b_title_takes_precedence(self):
        """T11b: title has PR #10, prompt has PR #20 -> title wins (10)."""
        row = _rec({"title": "PR #10 rework", "prompt": "Fix PR #20"})
        assert _extract_pr_number_fallback(row) == 10

    def test_T12_real_world_rebase(self):
        """T12: Real-world example: 'PR 319 rebase: STORY-640 approved needs rebase' -> 319."""
        row = _rec({
            "title": "PR 319 rebase: STORY-640 approved needs rebase",
            "prompt": "",
        })
        assert _extract_pr_number_fallback(row) == 319

    def test_T12b_real_world_rebase_pr_hash(self):
        """T12b: Real-world: 'Rebase PR #398 (STORY-707 OODA Scorer)' -> 398."""
        row = _rec({
            "title": "Rebase PR #398 (STORY-707 OODA Scorer)",
            "prompt": "",
        })
        assert _extract_pr_number_fallback(row) == 398

    def test_T12c_real_world_rework(self):
        """T12c: Real-world: 'PR #260 rework: seed.md...' -> 260."""
        row = _rec({"title": "PR #260 rework: seed.md needs fixing", "prompt": ""})
        assert _extract_pr_number_fallback(row) == 260

    def test_T12d_real_world_fix_review(self):
        """T12d: Real-world: 'Fix PR #404 review findings (STORY-713 Approval Queue)' -> 404."""
        row = _rec({
            "title": "Fix PR #404 review findings (STORY-713 Approval Queue)",
            "prompt": "",
        })
        assert _extract_pr_number_fallback(row) == 404

    def test_T13_bare_number_rejected(self):
        """T13: '2025 is the year' -> None (no PR keyword)."""
        row = _rec({"title": "2025 is the year", "prompt": ""})
        assert _extract_pr_number_fallback(row) is None

    def test_T13b_too_long_number_rejected(self):
        """T13b: 'PR #12345678' -> None (exceeds 6-digit cap)."""
        row = _rec({"title": "PR #12345678", "prompt": ""})
        assert _extract_pr_number_fallback(row) is None

    def test_T14_case_insensitive(self):
        """T14: 'pr #55 rework' (lowercase) -> 55."""
        row = _rec({"title": "pr #55 rework", "prompt": ""})
        assert _extract_pr_number_fallback(row) == 55


# ---------------------------------------------------------------------------
# Group I — Fallback integration in _pr_link_backfill_tick (T15-T19)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_T15_fallback_happy_path_title():
    """T15: branch search empty + title has PR #42 + verify 200 -> pr_number=42."""
    row = _null_row(
        story_id="STORY-839",
        title="PR #42 rebase: STORY-640 approved",
        prompt="",
    )
    pool = _mock_pool(rows=[row])
    gh_fetch = AsyncMock(return_value=[])  # branch search finds nothing
    gh_verify = AsyncMock(return_value={
        "number": 42,
        "base": {"repo": {"name": "tech-dev-agents"}},
    })

    await _pr_link_backfill_tick(pool=pool, gh_fetch_fn=gh_fetch, gh_verify_fn=gh_verify)

    # Verify was called with candidate PR 42
    gh_verify.assert_called_once_with("tech-dev-agents", 42)
    # DB updated
    pool.execute.assert_called_once()
    exec_args = pool.execute.call_args[0]
    assert 42 in exec_args


@pytest.mark.asyncio
async def test_T15b_fallback_happy_path_logs_info(caplog):
    """T15b: fallback link emits INFO with source=fallback_regex marker."""
    import logging

    row = _null_row(
        story_id="STORY-839",
        title="PR #42 rebase",
        prompt="",
    )
    pool = _mock_pool(rows=[row])
    gh_fetch = AsyncMock(return_value=[])
    gh_verify = AsyncMock(return_value={
        "number": 42,
        "base": {"repo": {"name": "tech-dev-agents"}},
    })

    with caplog.at_level(logging.INFO, logger="tech_dev_agents.ops_console.services.self_healing"):
        await _pr_link_backfill_tick(pool=pool, gh_fetch_fn=gh_fetch, gh_verify_fn=gh_verify)

    assert "fallback-linked" in caplog.text
    assert "42" in caplog.text


@pytest.mark.asyncio
async def test_T16_fallback_from_prompt():
    """T16: title is None, prompt has 'Fix PR #99' + verify 200 -> pr_number=99."""
    row = _null_row(
        story_id="STORY-900",
        title=None,
        prompt="Fix PR #99 review findings",
    )
    pool = _mock_pool(rows=[row])
    gh_fetch = AsyncMock(return_value=[])
    gh_verify = AsyncMock(return_value={
        "number": 99,
        "base": {"repo": {"name": "tech-dev-agents"}},
    })

    await _pr_link_backfill_tick(pool=pool, gh_fetch_fn=gh_fetch, gh_verify_fn=gh_verify)

    gh_verify.assert_called_once_with("tech-dev-agents", 99)
    pool.execute.assert_called_once()
    exec_args = pool.execute.call_args[0]
    assert 99 in exec_args


@pytest.mark.asyncio
async def test_T17_fallback_verify_404_no_update():
    """T17: fallback candidate PR #42 but verify returns None (404) -> no DB update."""
    row = _null_row(
        story_id="STORY-839",
        title="PR #42 rebase",
        prompt="",
    )
    pool = _mock_pool(rows=[row])
    gh_fetch = AsyncMock(return_value=[])
    gh_verify = AsyncMock(return_value=None)  # 404

    await _pr_link_backfill_tick(pool=pool, gh_fetch_fn=gh_fetch, gh_verify_fn=gh_verify)

    pool.execute.assert_not_called()


@pytest.mark.asyncio
async def test_T18_no_pr_in_text_no_verify():
    """T18: branch empty + title/prompt have no PR pattern -> verify not called."""
    row = _null_row(
        story_id="STORY-839",
        title="STORY-640 rework",
        prompt="Please fix the tests",
    )
    pool = _mock_pool(rows=[row])
    gh_fetch = AsyncMock(return_value=[])
    gh_verify = AsyncMock(return_value=None)

    await _pr_link_backfill_tick(pool=pool, gh_fetch_fn=gh_fetch, gh_verify_fn=gh_verify)

    gh_verify.assert_not_called()
    pool.execute.assert_not_called()


@pytest.mark.asyncio
async def test_T19_branch_search_succeeds_no_fallback():
    """T19: branch search returns PRs -> fallback not attempted."""
    row = _null_row(
        story_id="STORY-885",
        title="PR #999 this should be ignored",
        prompt="",
    )
    pool = _mock_pool(rows=[row])
    gh_fetch = AsyncMock(return_value=[{"number": 42, "created_at": "2026-05-01T12:00:00Z"}])
    gh_verify = AsyncMock(return_value=None)

    await _pr_link_backfill_tick(pool=pool, gh_fetch_fn=gh_fetch, gh_verify_fn=gh_verify)

    # Verify was NOT called (branch search succeeded)
    gh_verify.assert_not_called()
    # DB updated with branch-search result, not the title PR
    pool.execute.assert_called_once()
    exec_args = pool.execute.call_args[0]
    assert 42 in exec_args


@pytest.mark.asyncio
async def test_T20_fallback_repo_mismatch_no_update():
    """T20: verify returns 200 but repo name doesn't match -> no DB update."""
    row = _null_row(
        story_id="STORY-839",
        title="PR #42 rebase",
        repo="tech-dev-agents",
    )
    pool = _mock_pool(rows=[row])
    gh_fetch = AsyncMock(return_value=[])
    gh_verify = AsyncMock(return_value={
        "number": 42,
        "base": {"repo": {"name": "other-repo"}},
    })

    await _pr_link_backfill_tick(pool=pool, gh_fetch_fn=gh_fetch, gh_verify_fn=gh_verify)

    pool.execute.assert_not_called()


@pytest.mark.asyncio
async def test_T21_fallback_verify_error_no_crash():
    """T21: verify raises network error -> WARNING logged, no crash, no DB update."""
    row = _null_row(
        story_id="STORY-839",
        title="PR #42 rebase",
        prompt="",
    )
    pool = _mock_pool(rows=[row])
    gh_fetch = AsyncMock(return_value=[])
    gh_verify = AsyncMock(side_effect=OSError("network failure"))

    # Must not raise
    await _pr_link_backfill_tick(pool=pool, gh_fetch_fn=gh_fetch, gh_verify_fn=gh_verify)

    pool.execute.assert_not_called()


@pytest.mark.asyncio
async def test_T22_sql_now_selects_title_and_prompt():
    """T22: SQL query includes dj.title and dj.prompt columns."""
    pool = _mock_pool(rows=[])
    gh_fetch = AsyncMock(return_value=[])

    await _pr_link_backfill_tick(pool=pool, gh_fetch_fn=gh_fetch)

    sql_arg = pool.fetch.call_args[0][0]
    assert "dj.title" in sql_arg
    assert "dj.prompt" in sql_arg
