from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tech_dev_agents.ops_console.services.self_healing import (
    _stale_unlinked_in_review_tick,
    dispatch_stale_unlinked_in_review_sweeper,
)


def _rec(data: dict):
    r = MagicMock()
    r.__getitem__ = lambda self, k: data[k]
    r.get = lambda k, d=None: data.get(k, d)
    return r


@pytest.mark.asyncio
async def test_tick_requeues_each_stale_unlinked_job():
    pool = MagicMock()
    pool.fetch = AsyncMock(
        return_value=[_rec({"job_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"})]
    )

    with patch(
        "tech_dev_agents.ops_console.services.self_healing.record_event",
        new=AsyncMock(),
    ) as rec_ev:
        await _stale_unlinked_in_review_tick(pool=pool)

    pool.fetch.assert_called_once()
    rec_ev.assert_called_once()
    assert rec_ev.call_args.args[0] == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    assert rec_ev.call_args.args[1] == "requeued"


@pytest.mark.asyncio
async def test_sweeper_loop_calls_tick_and_honors_cancel():
    pool = MagicMock()
    calls = {"n": 0}

    async def _tick(*, pool):
        calls["n"] += 1

    with patch(
        "tech_dev_agents.ops_console.services.self_healing._stale_unlinked_in_review_tick",
        side_effect=_tick,
    ):
        task = asyncio.create_task(dispatch_stale_unlinked_in_review_sweeper(pool=pool))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert calls["n"] >= 1
