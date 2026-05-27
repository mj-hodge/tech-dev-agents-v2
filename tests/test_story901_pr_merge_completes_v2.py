"""STORY-901: PR-merge → v2 completed transition.

Phase 7 — RED state. All tests FAIL until Phase 8 implementation is complete.

The bug: when an agent opens a PR and the v2 row transitions to `in_review`,
nothing emits the `accepted` event when the PR is merged. This suite covers
the `dispatch_pr_merge_sweeper` background task added to self_healing.py.

Test cases:
  TC-1: emits `accepted` for a merged PR (mergedAt != null)
  TC-2: idempotent — completed rows do NOT appear in `in_review` query;
         record_event not called for already-terminal jobs
  TC-3: does NOT emit for open PRs (state == "OPEN")
  TC-4: does NOT emit for PRs closed without merge (state == "CLOSED", no mergedAt)
  TC-5: skips rows with pr_number IS NULL (no PR linked yet)
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Import the functions under test.
# These will ImportError until Phase 8 adds them to self_healing.py.
# ---------------------------------------------------------------------------

def _import_sweeper():
    from tech_dev_agents.ops_console.services.self_healing import (
        _pr_merge_sweeper_tick,
        dispatch_pr_merge_sweeper,
    )
    return _pr_merge_sweeper_tick, dispatch_pr_merge_sweeper


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pool_row(**kwargs) -> dict[str, Any]:
    """Build a fake asyncpg-style row dict representing a dispatch_state_current row."""
    defaults = {
        "job_id": "aaaaaaaa-0000-0000-0000-000000000001",
        "repo": "hpi-gorillacommerce/tech-dev-agents",
        "pr_number": 309,
        "state": "in_review",
    }
    defaults.update(kwargs)
    return defaults


async def _fake_gh_merged(repo: str, pr_number: int) -> dict[str, Any]:
    """Simulate gh pr view returning a merged PR."""
    return {"state": "MERGED", "mergedAt": "2026-05-05T17:50:00Z"}


async def _fake_gh_open(repo: str, pr_number: int) -> dict[str, Any]:
    """Simulate gh pr view returning an open PR."""
    return {"state": "OPEN", "mergedAt": None}


async def _fake_gh_closed_no_merge(repo: str, pr_number: int) -> dict[str, Any]:
    """Simulate gh pr view returning a closed (not merged) PR."""
    return {"state": "CLOSED", "mergedAt": None}


# ---------------------------------------------------------------------------
# TC-1: emits accepted for a merged PR
# ---------------------------------------------------------------------------


def test_tc1_emits_accepted_for_merged_pr():
    """Sweeper tick emits 'accepted' when gh reports the PR is merged."""
    _pr_merge_sweeper_tick, _ = _import_sweeper()

    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[_make_pool_row(pr_number=309)])

    record_calls: list[dict] = []

    async def fake_record_event(job_id, event_type, event_data, *, pool, actor):
        record_calls.append({
            "job_id": job_id,
            "event_type": event_type,
            "event_data": event_data,
            "actor": actor,
        })

    asyncio.run(_pr_merge_sweeper_tick(
        pool=pool,
        gh_caller=_fake_gh_merged,
        _record_event=fake_record_event,
    ))

    assert len(record_calls) == 1, f"Expected 1 record_event call, got {len(record_calls)}"
    call = record_calls[0]
    assert call["event_type"] == "accepted", f"Expected accepted, got {call['event_type']}"
    assert call["actor"] == "pr-merge-sweeper"
    assert call["event_data"].get("pr_number") == 309
    assert call["event_data"].get("repo") == "hpi-gorillacommerce/tech-dev-agents"


# ---------------------------------------------------------------------------
# TC-2: idempotent — completed rows don't appear in the query
# ---------------------------------------------------------------------------


def test_tc2_idempotent_completed_row_not_re_emitted():
    """After a row transitions to completed, it leaves in_review.

    The sweeper only queries state='in_review' rows. Once accepted is emitted,
    the row moves to lane=terminal, so it never appears in the next sweep.
    This test verifies that a pool returning NO rows produces no record_event calls.
    """
    _pr_merge_sweeper_tick, _ = _import_sweeper()

    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[])  # no in_review rows (already completed)

    record_calls: list[dict] = []

    async def fake_record_event(job_id, event_type, event_data, *, pool, actor):
        record_calls.append({"job_id": job_id, "event_type": event_type})

    asyncio.run(_pr_merge_sweeper_tick(
        pool=pool,
        gh_caller=_fake_gh_merged,
        _record_event=fake_record_event,
    ))

    assert len(record_calls) == 0, "record_event should not be called for empty result set"


# ---------------------------------------------------------------------------
# TC-3: does NOT emit for open PRs
# ---------------------------------------------------------------------------


def test_tc3_no_emit_for_open_pr():
    """Sweeper tick does NOT emit when gh reports the PR is still open."""
    _pr_merge_sweeper_tick, _ = _import_sweeper()

    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[_make_pool_row(pr_number=310)])

    record_calls: list[dict] = []

    async def fake_record_event(job_id, event_type, event_data, *, pool, actor):
        record_calls.append({"job_id": job_id, "event_type": event_type})

    asyncio.run(_pr_merge_sweeper_tick(
        pool=pool,
        gh_caller=_fake_gh_open,
        _record_event=fake_record_event,
    ))

    assert len(record_calls) == 0, "record_event should not be called for an open PR"


# ---------------------------------------------------------------------------
# TC-4: does NOT emit for PRs closed without merge
# ---------------------------------------------------------------------------


def test_tc4_no_emit_for_closed_unmerged_pr():
    """Sweeper tick does NOT emit when PR is CLOSED but not merged."""
    _pr_merge_sweeper_tick, _ = _import_sweeper()

    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[_make_pool_row(pr_number=311)])

    record_calls: list[dict] = []

    async def fake_record_event(job_id, event_type, event_data, *, pool, actor):
        record_calls.append({"job_id": job_id, "event_type": event_type})

    asyncio.run(_pr_merge_sweeper_tick(
        pool=pool,
        gh_caller=_fake_gh_closed_no_merge,
        _record_event=fake_record_event,
    ))

    assert len(record_calls) == 0, "record_event should not be called for unmerged closed PR"


# ---------------------------------------------------------------------------
# TC-5: skips rows with pr_number IS NULL
# ---------------------------------------------------------------------------


def test_tc5_skips_rows_with_null_pr_number():
    """Sweeper tick skips rows where pr_number is None (no PR linked yet)."""
    _pr_merge_sweeper_tick, _ = _import_sweeper()

    pool = MagicMock()
    # Row with pr_number=None — the DB query filters these out via pr_number IS NOT NULL,
    # but we also test the defensive code path if a None sneaks through.
    pool.fetch = AsyncMock(return_value=[_make_pool_row(pr_number=None)])

    gh_calls: list[tuple] = []

    async def fake_gh_caller(repo: str, pr_number: int) -> dict[str, Any]:
        gh_calls.append((repo, pr_number))
        return {"state": "MERGED", "mergedAt": "2026-05-05T17:50:00Z"}

    record_calls: list[dict] = []

    async def fake_record_event(job_id, event_type, event_data, *, pool, actor):
        record_calls.append({"job_id": job_id, "event_type": event_type})

    asyncio.run(_pr_merge_sweeper_tick(
        pool=pool,
        gh_caller=fake_gh_caller,
        _record_event=fake_record_event,
    ))

    assert len(gh_calls) == 0, "gh_caller should not be invoked for null pr_number rows"
    assert len(record_calls) == 0, "record_event should not be called for null pr_number rows"
