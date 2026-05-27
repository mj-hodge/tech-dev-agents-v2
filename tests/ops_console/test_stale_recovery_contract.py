"""STORY-511 mock-only regression test for ``recover_stale_claims``.

Runs without PostgreSQL. Verifies the SQL the method issues and the
default timeout value so that a future refactor can't silently restore
the buggy 300 s / ``claimed_at`` contract that caused the 2026-04-22
double-claim of STORY-495 and STORY-515.

The sibling integration test
``tests/ops_console/test_dispatch_db_service.py::TestRecoverStaleClaims``
covers the behavior against a real Postgres; this file covers the
contract at unit level so CI runs without a DB still catch regressions.
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest

from tech_dev_agents.ops_console.services.dispatch_db_service import (
    DispatchDBService,
)


class _FakeAcquireCtx:
    """Minimal context manager that returns the given fake connection."""

    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _service_with_fake_pool():
    """Return a service whose ``_pool.acquire()`` yields a Mock conn.

    The conn's ``.fetch`` is an ``AsyncMock`` so we can assert what SQL
    and args it was called with.
    """
    fake_conn = MagicMock()
    fake_conn.fetch = AsyncMock(return_value=[])

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_FakeAcquireCtx(fake_conn))

    svc = DispatchDBService(pool)
    return svc, fake_conn


@pytest.mark.asyncio
async def test_sql_filters_on_updated_at_not_claimed_at():
    """The phase-active-but-stale tier must key off ``updated_at``.

    Recovery keyed off ``claimed_at`` alone was the 2026-04-22 double-claim
    bug — Medium-scope phases legitimately run 10+ minutes. The phase runner
    bumps ``updated_at`` at every phase_start/phase_end. Recovery must
    respect that to avoid releasing live claims.

    The impl is two-tier (never-started + phase-active-but-stale) so it
    issues two fetches. The never-started tier is allowed to use
    ``claimed_at`` in its WHERE clause as long as it ALSO gates on
    ``updated_at = claimed_at`` (the never-moved guard). The phase-active
    tier MUST gate on ``updated_at < now()``.
    """
    svc, conn = _service_with_fake_pool()

    await svc.recover_stale_claims(timeout_seconds=1800)

    assert conn.fetch.await_count >= 1, "recover_stale_claims made no DB call"
    all_sql = [call.args[0] for call in conn.fetch.await_args_list]
    assert any("updated_at < now()" in sql for sql in all_sql), (
        "no query keys staleness off updated_at — STORY-511 regression. "
        "All queries:\n" + "\n---\n".join(all_sql)
    )
    for sql in all_sql:
        if "claimed_at <" in sql:
            assert "updated_at = claimed_at" in sql or "updated_at < " in sql, (
                "query uses claimed_at as staleness reference without an "
                "updated_at safety check:\n" + sql
            )


@pytest.mark.asyncio
async def test_default_timeout_is_at_least_3600_seconds():
    """Default ``timeout_seconds`` must be >= 3600 (1 hour).

    The old default of 300 s triggered mid-phase for every Medium/Large
    run and caused double-claim races. The ceiling is intentionally
    conservative — an hour of genuine silence means the agent has
    crashed, not that the phase is just taking a while.
    """
    sig = inspect.signature(DispatchDBService.recover_stale_claims)
    default = sig.parameters["timeout_seconds"].default
    assert default >= 3600, (
        f"default timeout is {default} s — must be >= 3600 s to avoid "
        "mid-phase recovery of Medium/Large claims"
    )


@pytest.mark.asyncio
async def test_caller_can_still_override_timeout_for_tighter_recovery():
    """The parameter must still accept a custom timeout for environments
    with short phases (e.g. staging/integration tests).

    The two-tier impl passes ``timeout_seconds`` to the phase-active tier
    query; the override must appear in at least one fetch call's args.
    """
    svc, conn = _service_with_fake_pool()
    await svc.recover_stale_claims(timeout_seconds=60)
    assert conn.fetch.await_count >= 1
    all_args = [call.args for call in conn.fetch.await_args_list]
    # asyncpg.fetch(sql, *params) — the timeout appears as a positional param
    # on whichever tier's query carried the caller's override.
    assert any(60.0 in args for args in all_args), (
        "recover_stale_claims dropped the caller's timeout_seconds override. "
        f"Per-call args={all_args}"
    )


@pytest.mark.asyncio
async def test_update_sets_status_to_pending_and_clears_claim_fields():
    """Recovery must reset status='pending' and clear claimed_by / claimed_at
    so the row is eligible for a fresh claim from any agent."""
    svc, conn = _service_with_fake_pool()
    await svc.recover_stale_claims(timeout_seconds=1800)
    sql = conn.fetch.await_args.args[0]
    assert "status = 'pending'" in sql
    assert "claimed_by = NULL" in sql
    assert "claimed_at = NULL" in sql
    assert "updated_at = now()" in sql, (
        "recovery must bump updated_at so the row's liveness reference "
        "resets and the next agent starts clean"
    )
