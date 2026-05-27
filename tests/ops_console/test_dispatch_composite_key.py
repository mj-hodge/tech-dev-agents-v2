"""STORY-531: Composite (story_id, repo) Unique Key — Integration Tests

Phase 7 — RED state. All tests FAIL until Phase 8 implementation is complete.

Tests verify that the dispatch_items table's partial unique index covers
(story_id, repo) rather than (story_id) alone, and that DispatchDBService
methods accept an optional ``repo=`` qualifier for disambiguation.

Groups:
  A (T01–T02): Cross-repo enqueue — same story_id different repos both succeed
  B (T03–T04): Terminal-state isolation — delete/complete scoped to (story_id, repo)
  C (T05–T07): Service disambiguation — complete() with/without repo qualifier
  D (T08–T11): Other mutating methods — claim/fail/cancel/pause with repo qualifier
  E (T12):     get() repo scoping
  F (T13):     History preservation across re-enqueue within same repo
  G (T14–T15): Route-level — ?repo= query param on HTTP endpoints
  H (T16–T18): Schema / index validation

Requires: PostgreSQL ops_console_test with migrations 001–007 applied.
Skip: automatically skipped when PostgreSQL is not reachable.
"""

from __future__ import annotations

import asyncio
from typing import Any

import asyncpg
import pytest
import pytest_asyncio

from tests.ops_console.conftest import TEST_API_KEY, inject_mock_services
from tech_dev_agents.ops_console.services.dispatch_db_service import (
    AlreadyClaimedError,
    DispatchDBError,
    DispatchDBService,
    DuplicateDispatchError,
    NotFoundError,
)

# AmbiguousStoryError is added in Phase 8. Import defensively so the test
# file can at least be *collected*; tests that require it will fail with
# AssertionError until the class is defined.
try:
    from tech_dev_agents.ops_console.services.dispatch_db_service import (
        AmbiguousStoryError,
    )
    _AMBIGUOUS_IMPLEMENTED = True
except ImportError:
    # Placeholder so type-checking and isinstance() calls compile.
    class AmbiguousStoryError(DispatchDBError):  # type: ignore[no-redef]
        """Placeholder — replaced by real implementation in Phase 8."""
        def __init__(self, story_id: str = "", candidate_repos: list[str] | None = None):
            self.story_id = story_id
            self.candidate_repos = candidate_repos or []
            super().__init__(str(story_id))

    _AMBIGUOUS_IMPLEMENTED = False

TEST_DATABASE_URL = "postgresql://ops_console:ops_console@localhost/ops_console_test"

REPO_ALPHA = "repo-alpha"
REPO_BETA = "repo-beta"
STORY = "STORY-531-TEST"


# ---------------------------------------------------------------------------
# Infrastructure
# ---------------------------------------------------------------------------


def _pg_is_reachable() -> bool:
    async def _check() -> bool:
        try:
            conn = await asyncpg.connect(TEST_DATABASE_URL, timeout=3)
            await conn.close()
            return True
        except Exception:
            return False

    try:
        return asyncio.run(_check())
    except Exception:
        return False


_PG_AVAILABLE = _pg_is_reachable()
pytestmark = pytest.mark.skipif(
    not _PG_AVAILABLE, reason="PostgreSQL ops_console_test not reachable"
)


@pytest_asyncio.fixture
async def db_pool():
    """asyncpg pool to test database; truncate dispatch_items between tests."""
    pool = await asyncpg.create_pool(TEST_DATABASE_URL, min_size=1, max_size=3)
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE dispatch_items, agents RESTART IDENTITY CASCADE")
    yield pool
    await pool.close()


@pytest_asyncio.fixture
async def svc(db_pool) -> DispatchDBService:
    """DispatchDBService backed by the test pool."""
    return DispatchDBService(db_pool)


def _enqueue_kw(
    story_id: str = STORY,
    repo: str = REPO_ALPHA,
    scope: str = "small",
    prompt: str = "STORY-531-TEST: Composite key integration test.",
    enqueued_by: str = "mark",
    title: str | None = None,
) -> dict[str, Any]:
    return {
        "story_id": story_id,
        "repo": repo,
        "scope": scope,
        "prompt": prompt,
        "enqueued_by": enqueued_by,
        "title": title,
    }


# ---------------------------------------------------------------------------
# Group A: Cross-repo enqueue
# ---------------------------------------------------------------------------


class TestCrossRepoEnqueue:
    """A (T01–T02): Same story_id may coexist across different repos."""

    @pytest.mark.asyncio
    async def test_cross_repo_both_enqueue_succeed(self, svc: DispatchDBService):
        """T01: Two enqueues with same story_id but different repos both return 201.

        RED: fails until migration 007 replaces uq_story_active_idx (story_id)
        with uq_story_repo_active_idx (story_id, repo).
        With old index: second enqueue raises DuplicateDispatchError.
        """
        row_alpha = await svc.enqueue(**_enqueue_kw(repo=REPO_ALPHA))
        row_beta = await svc.enqueue(**_enqueue_kw(repo=REPO_BETA))

        assert row_alpha["story_id"] == STORY
        assert row_alpha["repo"] == REPO_ALPHA
        assert row_alpha["status"] == "pending"

        assert row_beta["story_id"] == STORY
        assert row_beta["repo"] == REPO_BETA
        assert row_beta["status"] == "pending"

        # Both rows exist as distinct DB records.
        assert row_alpha["id"] != row_beta["id"]

    @pytest.mark.asyncio
    async def test_same_repo_duplicate_active_raises(self, svc: DispatchDBService):
        """T02: Same (story_id, repo) pair while active → DuplicateDispatchError.

        GREEN with current code (existing behaviour preserved after fix).
        """
        await svc.enqueue(**_enqueue_kw(repo=REPO_ALPHA))

        with pytest.raises(DuplicateDispatchError):
            await svc.enqueue(**_enqueue_kw(repo=REPO_ALPHA))


# ---------------------------------------------------------------------------
# Group B: Terminal-state isolation
# ---------------------------------------------------------------------------


class TestTerminalStateIsolation:
    """B (T03–T04): Terminal rows in repo-B unaffected by repo-A operations."""

    @pytest.mark.asyncio
    async def test_reenqueue_does_not_delete_terminal_in_other_repo(
        self, svc: DispatchDBService, db_pool
    ):
        """T03: reenqueue STORY-X in repo-A preserves terminal row for repo-B.

        RED: enqueue() currently deletes by story_id alone (line 116 in service).
        After fix it only deletes within the same repo.
        """
        # Complete STORY in repo-beta
        await svc.enqueue(**_enqueue_kw(repo=REPO_BETA))
        await svc.claim(STORY, "dan")  # old code — no repo param yet
        await svc.complete(STORY)

        # Re-enqueue STORY in repo-alpha (different repo)
        await svc.enqueue(**_enqueue_kw(repo=REPO_ALPHA))

        # The repo-beta completed row must still exist in history
        async with db_pool.acquire() as conn:
            beta_rows = await conn.fetch(
                "SELECT * FROM dispatch_items WHERE story_id = $1 AND repo = $2",
                STORY, REPO_BETA,
            )
        assert len(beta_rows) == 1, (
            "repo-beta terminal row was deleted when reenqueing repo-alpha — "
            "enqueue() must scope its DELETE to (story_id, repo)"
        )
        assert beta_rows[0]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_complete_one_repo_does_not_affect_other_repo(
        self, svc: DispatchDBService, db_pool
    ):
        """T04: Completing STORY-X in repo-A does not change STORY-X row in repo-B.

        RED: requires cross-repo active rows, impossible under old single-column index.
        After fix: both rows can be active simultaneously.
        """
        # Enqueue both repos — only possible after migration 007
        await svc.enqueue(**_enqueue_kw(repo=REPO_ALPHA))
        await svc.enqueue(**_enqueue_kw(repo=REPO_BETA))

        # Claim both
        await svc.claim(STORY, "dan", repo=REPO_ALPHA)
        await svc.claim(STORY, "derrick", repo=REPO_BETA)

        # Complete only repo-alpha
        await svc.complete(STORY, repo=REPO_ALPHA)

        # repo-beta row must still be claimed
        async with db_pool.acquire() as conn:
            beta_row = await conn.fetchrow(
                "SELECT status FROM dispatch_items WHERE story_id = $1 AND repo = $2",
                STORY, REPO_BETA,
            )
        assert beta_row is not None
        assert beta_row["status"] == "claimed", (
            f"repo-beta status changed to {beta_row['status']!r} when repo-alpha was completed"
        )


# ---------------------------------------------------------------------------
# Group C: Service disambiguation — complete()
# ---------------------------------------------------------------------------


class TestCompleteDisambiguation:
    """C (T05–T07): complete() scopes to (story_id, repo) and handles ambiguity."""

    @pytest.mark.asyncio
    async def test_complete_with_repo_scopes_to_correct_row(
        self, svc: DispatchDBService, db_pool
    ):
        """T05: complete(story_id, repo=alpha) completes only the alpha row.

        RED: requires cross-repo active rows + repo= kwarg on complete().
        """
        await svc.enqueue(**_enqueue_kw(repo=REPO_ALPHA))
        await svc.enqueue(**_enqueue_kw(repo=REPO_BETA))
        await svc.claim(STORY, "dan", repo=REPO_ALPHA)
        await svc.claim(STORY, "derrick", repo=REPO_BETA)

        row = await svc.complete(STORY, repo=REPO_ALPHA)

        assert row["status"] == "completed"
        assert row["repo"] == REPO_ALPHA

        # repo-beta must remain claimed
        async with db_pool.acquire() as conn:
            beta_status = await conn.fetchval(
                "SELECT status FROM dispatch_items WHERE story_id = $1 AND repo = $2",
                STORY, REPO_BETA,
            )
        assert beta_status == "claimed"

    @pytest.mark.asyncio
    async def test_complete_raises_ambiguous_without_repo_two_active_rows(
        self, svc: DispatchDBService
    ):
        """T06: complete(story_id) without repo when two active rows → AmbiguousStoryError.

        RED: AmbiguousStoryError not yet implemented; also requires cross-repo rows.
        """
        assert _AMBIGUOUS_IMPLEMENTED, (
            "AmbiguousStoryError is not yet importable from dispatch_db_service — Phase 8 needed"
        )

        await svc.enqueue(**_enqueue_kw(repo=REPO_ALPHA))
        await svc.enqueue(**_enqueue_kw(repo=REPO_BETA))
        await svc.claim(STORY, "dan", repo=REPO_ALPHA)
        await svc.claim(STORY, "derrick", repo=REPO_BETA)

        with pytest.raises(AmbiguousStoryError) as exc_info:
            await svc.complete(STORY)  # no repo — ambiguous

        assert exc_info.value.story_id == STORY
        assert set(exc_info.value.candidate_repos) == {REPO_ALPHA, REPO_BETA}

    @pytest.mark.asyncio
    async def test_complete_backward_compat_single_row_no_repo(
        self, svc: DispatchDBService
    ):
        """T07: complete(story_id) without repo works when exactly one active row.

        Backward-compatible with existing callers that don't pass repo.
        GREEN with current code (single-row case unchanged).
        """
        await svc.enqueue(**_enqueue_kw(repo=REPO_ALPHA))
        await svc.claim(STORY, "dan")

        row = await svc.complete(STORY)  # no repo — one row, backward compat

        assert row["status"] == "completed"
        assert row["repo"] == REPO_ALPHA


# ---------------------------------------------------------------------------
# Group D: Other mutating methods — claim / fail / cancel / pause
# ---------------------------------------------------------------------------


class TestMutatingMethodDisambiguation:
    """D (T08–T11): claim/fail/cancel/pause accept optional repo= qualifier."""

    @pytest.mark.asyncio
    async def test_claim_with_repo_scopes_correctly(
        self, svc: DispatchDBService, db_pool
    ):
        """T08: claim(story_id, agent, repo=alpha) claims only the alpha row.

        RED: requires cross-repo rows + repo= kwarg on claim().
        """
        await svc.enqueue(**_enqueue_kw(repo=REPO_ALPHA))
        await svc.enqueue(**_enqueue_kw(repo=REPO_BETA))

        row = await svc.claim(STORY, "dan", repo=REPO_ALPHA)

        assert row["repo"] == REPO_ALPHA
        assert row["claimed_by"] == "dan"

        # repo-beta must remain pending
        async with db_pool.acquire() as conn:
            beta_status = await conn.fetchval(
                "SELECT status FROM dispatch_items WHERE story_id = $1 AND repo = $2",
                STORY, REPO_BETA,
            )
        assert beta_status == "pending"

    @pytest.mark.asyncio
    async def test_fail_with_repo_scopes_correctly(
        self, svc: DispatchDBService, db_pool
    ):
        """T09: fail(story_id, repo=alpha) fails only the alpha row.

        RED: requires cross-repo rows + repo= kwarg on fail().
        """
        await svc.enqueue(**_enqueue_kw(repo=REPO_ALPHA))
        await svc.enqueue(**_enqueue_kw(repo=REPO_BETA))
        await svc.claim(STORY, "dan", repo=REPO_ALPHA)
        await svc.claim(STORY, "derrick", repo=REPO_BETA)

        row = await svc.fail(STORY, repo=REPO_ALPHA)

        assert row["status"] == "failed"
        assert row["repo"] == REPO_ALPHA

        async with db_pool.acquire() as conn:
            beta_status = await conn.fetchval(
                "SELECT status FROM dispatch_items WHERE story_id = $1 AND repo = $2",
                STORY, REPO_BETA,
            )
        assert beta_status == "claimed"

    @pytest.mark.asyncio
    async def test_cancel_with_repo_scopes_correctly(
        self, svc: DispatchDBService, db_pool
    ):
        """T10: cancel(story_id, repo=alpha) cancels only the alpha row.

        RED: requires cross-repo rows + repo= kwarg on cancel().
        """
        await svc.enqueue(**_enqueue_kw(repo=REPO_ALPHA))
        await svc.enqueue(**_enqueue_kw(repo=REPO_BETA))

        await svc.cancel(STORY, repo=REPO_ALPHA)

        async with db_pool.acquire() as conn:
            alpha_status = await conn.fetchval(
                "SELECT status FROM dispatch_items WHERE story_id = $1 AND repo = $2",
                STORY, REPO_ALPHA,
            )
            beta_status = await conn.fetchval(
                "SELECT status FROM dispatch_items WHERE story_id = $1 AND repo = $2",
                STORY, REPO_BETA,
            )
        assert alpha_status == "cancelled"
        assert beta_status == "pending"

    @pytest.mark.asyncio
    async def test_pause_with_repo_scopes_correctly(
        self, svc: DispatchDBService, db_pool
    ):
        """T11: pause(story_id, agent, repo=alpha) pauses only the alpha row.

        RED: requires cross-repo rows + repo= kwarg on pause().
        """
        await svc.enqueue(**_enqueue_kw(repo=REPO_ALPHA))
        await svc.enqueue(**_enqueue_kw(repo=REPO_BETA))
        await svc.claim(STORY, "dan", repo=REPO_ALPHA)
        await svc.claim(STORY, "derrick", repo=REPO_BETA)

        row = await svc.pause(STORY, "dan", repo=REPO_ALPHA)

        assert row["status"] == "paused"
        assert row["repo"] == REPO_ALPHA

        async with db_pool.acquire() as conn:
            beta_status = await conn.fetchval(
                "SELECT status FROM dispatch_items WHERE story_id = $1 AND repo = $2",
                STORY, REPO_BETA,
            )
        assert beta_status == "claimed"


# ---------------------------------------------------------------------------
# Group E: get() repo scoping
# ---------------------------------------------------------------------------


class TestGetScoping:
    """E (T12): get() with repo= returns None when no match for that repo."""

    @pytest.mark.asyncio
    async def test_get_with_nonexistent_repo_returns_none(self, svc: DispatchDBService):
        """T12: get(story_id, repo=beta) returns None when only alpha row exists.

        RED: current get() ignores any repo keyword arg (doesn't accept it).
        """
        await svc.enqueue(**_enqueue_kw(repo=REPO_ALPHA))

        result = await svc.get(STORY, repo=REPO_BETA)

        assert result is None, (
            f"get() with repo={REPO_BETA!r} should return None when only "
            f"repo={REPO_ALPHA!r} row exists; got: {result}"
        )


# ---------------------------------------------------------------------------
# Group F: History preservation
# ---------------------------------------------------------------------------


class TestHistoryPreservation:
    """F (T13): Terminal rows survive re-enqueue within the same repo."""

    @pytest.mark.asyncio
    async def test_history_preserved_across_reenqueue_same_repo(
        self, svc: DispatchDBService, db_pool
    ):
        """T13: reenqueue STORY in repo-alpha preserves its prior completed row.

        RED: enqueue() currently DELETE FROM dispatch_items WHERE story_id = $1
        (line 116), erasing the completed row. After fix it only deletes within
        the same (story_id, repo), and old rows are retained for history.
        """
        # First lifecycle: enqueue → claim → complete
        await svc.enqueue(**_enqueue_kw(repo=REPO_ALPHA))
        await svc.claim(STORY, "dan")
        completed_row = await svc.complete(STORY)
        first_id = completed_row["id"]

        # Re-enqueue same (story_id, repo)
        new_row = await svc.enqueue(**_enqueue_kw(repo=REPO_ALPHA))

        # The original completed row MUST still be present for history
        async with db_pool.acquire() as conn:
            history_row = await conn.fetchrow(
                "SELECT id, status FROM dispatch_items WHERE id = $1",
                first_id,
            )
        assert history_row is not None, (
            f"Completed row (id={first_id}) was deleted during re-enqueue — "
            "enqueue() must preserve terminal rows for history"
        )
        assert history_row["status"] == "completed"

        # The new row must be a distinct pending row
        assert new_row["id"] != first_id
        assert new_row["status"] == "pending"


# ---------------------------------------------------------------------------
# Group G: Route-level tests
# ---------------------------------------------------------------------------


class TestRouteRepoParam:
    """G (T14–T15): HTTP endpoints accept optional ?repo= query parameter."""

    @pytest.mark.asyncio
    async def test_route_claim_accepts_repo_query_param(
        self, client, app, db_pool
    ):
        """T14: POST /dispatch/claim/STORY?repo=alpha claims only the alpha row.

        RED: route currently has no ?repo= param; FastAPI either ignores it
        (no effect, wrong row may be claimed) or returns 422. After fix,
        the route passes repo= to db_svc.claim().
        """
        svc = DispatchDBService(db_pool)
        inject_mock_services(app, dispatch_db_service=svc)

        # Seed two rows — only possible after migration 007
        await svc.enqueue(**_enqueue_kw(repo=REPO_ALPHA))
        await svc.enqueue(**_enqueue_kw(repo=REPO_BETA))

        resp = await client.post(
            f"/api/dispatch/claim/{STORY}",
            params={"repo": REPO_ALPHA},
            json={"agent_name": "dan"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["item"]["repo"] == REPO_ALPHA

        # repo-beta must still be pending
        async with db_pool.acquire() as conn:
            beta_status = await conn.fetchval(
                "SELECT status FROM dispatch_items WHERE story_id = $1 AND repo = $2",
                STORY, REPO_BETA,
            )
        assert beta_status == "pending"

    @pytest.mark.asyncio
    async def test_route_complete_returns_409_on_ambiguous(
        self, client, app, db_pool
    ):
        """T15: POST /dispatch/complete/STORY without ?repo returns 409 when ambiguous.

        RED: route currently has no ?repo= param; AmbiguousStoryError not raised.
        After fix: 409 with {detail, story_id, candidate_repos} in body.
        """
        assert _AMBIGUOUS_IMPLEMENTED, (
            "AmbiguousStoryError not yet implemented — skipping route ambiguity check"
        )

        svc = DispatchDBService(db_pool)
        inject_mock_services(app, dispatch_db_service=svc)
        # Override GitHub token check so complete() isn't blocked by proof-of-work
        app.state.settings.github_token = ""

        await svc.enqueue(**_enqueue_kw(repo=REPO_ALPHA))
        await svc.enqueue(**_enqueue_kw(repo=REPO_BETA))
        await svc.claim(STORY, "dan", repo=REPO_ALPHA)
        await svc.claim(STORY, "derrick", repo=REPO_BETA)

        resp = await client.post(
            f"/api/dispatch/complete/{STORY}",
            json={"commit_sha": "a" * 40},
        )

        assert resp.status_code == 409
        data = resp.json()
        assert "candidate_repos" in data
        assert set(data["candidate_repos"]) == {REPO_ALPHA, REPO_BETA}

    @pytest.mark.asyncio
    async def test_route_complete_returns_409_on_ambiguous_with_github_token_enabled(
        self, client, app, db_pool
    ):
        """T15b: Ambiguous complete must return 409 even when GitHub checks are enabled.

        Regression guard for uncaught AmbiguousStoryError in the github_token
        pre-check path (was surfacing as HTTP 500 in production logs).
        """
        assert _AMBIGUOUS_IMPLEMENTED, (
            "AmbiguousStoryError not yet implemented — skipping route ambiguity check"
        )

        svc = DispatchDBService(db_pool)
        inject_mock_services(app, dispatch_db_service=svc)
        app.state.settings.github_token = "test-token-enabled"

        await svc.enqueue(**_enqueue_kw(repo=REPO_ALPHA))
        await svc.enqueue(**_enqueue_kw(repo=REPO_BETA))
        await svc.claim(STORY, "dan", repo=REPO_ALPHA)
        await svc.claim(STORY, "derrick", repo=REPO_BETA)

        resp = await client.post(
            f"/api/dispatch/complete/{STORY}",
            json={"commit_sha": "a" * 40},
        )

        assert resp.status_code == 409
        data = resp.json()
        assert "candidate_repos" in data
        assert set(data["candidate_repos"]) == {REPO_ALPHA, REPO_BETA}

    @pytest.mark.asyncio
    async def test_route_complete_accepts_repo_in_body_for_disambiguation(
        self, client, app, db_pool
    ):
        """T15c: body.repo disambiguates /complete when query ?repo is omitted."""
        svc = DispatchDBService(db_pool)
        inject_mock_services(app, dispatch_db_service=svc)
        app.state.settings.github_token = ""  # bypass GitHub checks in this route test

        await svc.enqueue(**_enqueue_kw(repo=REPO_ALPHA))
        await svc.enqueue(**_enqueue_kw(repo=REPO_BETA))
        await svc.claim(STORY, "dan", repo=REPO_ALPHA)
        await svc.claim(STORY, "derrick", repo=REPO_BETA)

        resp = await client.post(
            f"/api/dispatch/complete/{STORY}",
            json={"commit_sha": "a" * 40, "repo": REPO_ALPHA},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["item"]["repo"] == REPO_ALPHA

        # beta row should remain claimed
        async with db_pool.acquire() as conn:
            beta_status = await conn.fetchval(
                "SELECT status FROM dispatch_items WHERE story_id = $1 AND repo = $2",
                STORY, REPO_BETA,
            )
        assert beta_status == "claimed"


# ---------------------------------------------------------------------------
# Group H: Schema / index validation
# ---------------------------------------------------------------------------


class TestSchemaValidation:
    """H (T16–T18): Database schema reflects migration 007."""

    @pytest.mark.asyncio
    async def test_composite_active_index_exists(self, db_pool):
        """T16: uq_story_repo_active_idx exists on dispatch_items after migration 007.

        RED: index does not exist until migration 007 is applied.
        """
        async with db_pool.acquire() as conn:
            exists = await conn.fetchval(
                """
                SELECT COUNT(*)
                FROM pg_indexes
                WHERE tablename = 'dispatch_items'
                  AND indexname = 'uq_story_repo_active_idx'
                """
            )
        assert exists == 1, (
            "uq_story_repo_active_idx not found — "
            "run scripts/migrations/007_composite_key_story_repo.sql first"
        )

    @pytest.mark.asyncio
    async def test_old_single_column_index_dropped(self, db_pool):
        """T17: uq_story_active_idx (story_id alone) no longer exists after migration 007.

        RED: index exists until migration 007 drops it.
        """
        async with db_pool.acquire() as conn:
            exists = await conn.fetchval(
                """
                SELECT COUNT(*)
                FROM pg_indexes
                WHERE tablename = 'dispatch_items'
                  AND indexname = 'uq_story_active_idx'
                """
            )
        assert exists == 0, (
            "uq_story_active_idx still exists — "
            "migration 007 DROP INDEX has not run"
        )

    @pytest.mark.asyncio
    async def test_in_review_status_covered_by_composite_index(
        self, svc: DispatchDBService, db_pool
    ):
        """T18: Two active in_review rows with same (story_id, repo) → UniqueViolationError.

        Verifies that 'in_review' is included in the composite index's WHERE clause,
        closing the latent gap from migration 004 (which only covered pending/claimed/paused).

        RED: old index did not cover in_review; duplicate in_review rows were possible.
        After migration 007: uq_story_repo_active_idx covers in_review.
        """
        await svc.enqueue(**_enqueue_kw(repo=REPO_ALPHA))
        await svc.claim(STORY, "dan")
        await svc.transition_to_review(STORY)

        # Direct INSERT bypasses service to test the DB constraint directly.
        async with db_pool.acquire() as conn:
            with pytest.raises(asyncpg.UniqueViolationError):
                await conn.execute(
                    """INSERT INTO dispatch_items
                           (story_id, repo, scope, prompt, enqueued_by, status)
                       VALUES ($1, $2, 'small', 'duplicate in_review', 'test', 'in_review')""",
                    STORY, REPO_ALPHA,
                )
