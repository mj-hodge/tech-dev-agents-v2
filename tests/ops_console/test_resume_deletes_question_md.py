"""Resume loop fix (2026-04-24): deleting QUESTION.md on resumed claim.

The bug: agent writes QUESTION.md → /needs-info → operator answers & calls
/resume → next agent claims → _check_for_questions sees the stale file →
/needs-info posted again → infinite loop. Mark / Morris have been manually
deleting and pushing QUESTION.md for days to break the loop.

Fix design (hybrid): resume_from_needs_info preserves needs_info_path on the
row (status=pending, path=<the QUESTION.md path>). The /claim response
surfaces that path. The poller plumbs it into run_sdlc_phases which — before
running any phase — reads the file, captures prior Q&A, deletes the file,
and commits the removal. Next _check_for_questions returns None → no loop.

Groups:

Group A — Service: resume preserves needs_info_path (REAL DB required).
  A-01: resume_from_needs_info returns needs_info_path unchanged.
  A-02: a resumed row, re-claimed, carries needs_info_path in the row dict.

Group B — Route: ClaimResponse includes needs_info_path field.
  B-01: POST /api/dispatch/claim after resume returns needs_info_path in body.
  B-02: POST /api/dispatch/claim on a fresh pending story returns
        needs_info_path=None.

Requires PostgreSQL ops_console_test. Skipped if unreachable (same pattern
as test_needs_info_state.py).
"""

from __future__ import annotations

import asyncio

import asyncpg
import pytest
import pytest_asyncio

from tests.ops_console.conftest import inject_mock_services
from tech_dev_agents.ops_console.services.dispatch_db_service import DispatchDBService

TEST_DATABASE_URL = "postgresql://ops_console:ops_console@localhost/ops_console_test"


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
    pool = await asyncpg.create_pool(TEST_DATABASE_URL, min_size=1, max_size=3)
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE dispatch_items, agents RESTART IDENTITY CASCADE")
    yield pool
    await pool.close()


@pytest_asyncio.fixture
async def dispatch_db_service(db_pool) -> DispatchDBService:
    return DispatchDBService(db_pool)


def _enqueue_payload(story_id: str = "STORY-RDQM") -> dict:
    return {
        "story_id": story_id,
        "repo": "tech-dev-agents",
        "scope": "medium",
        "prompt": "resume-deletes-question-md test prompt",
        "enqueued_by": "hermes",
    }


def _inject(app, svc: DispatchDBService) -> None:
    inject_mock_services(app, dispatch_db_service=svc)


# ---------------------------------------------------------------------------
# Group A — Service
# ---------------------------------------------------------------------------


class TestResumePreservesPath:
    """A-01, A-02: needs_info_path is preserved on resume and carried on claim."""

    @pytest.mark.asyncio
    async def test_resume_preserves_needs_info_path(self, dispatch_db_service):
        """A-01: resume_from_needs_info does NOT null out needs_info_path.

        RED until resume_from_needs_info stops writing `needs_info_path = NULL`.
        """
        svc = dispatch_db_service
        story = "STORY-RDQM-A01"
        await svc.enqueue(**_enqueue_payload(story))
        await svc.claim(story, agent_name="devon")
        await svc.needs_info(story, "features/story-rdqm/QUESTION.md")

        row = await svc.resume_from_needs_info(story)

        assert row["status"] == "pending"
        assert row.get("needs_info_path") == "features/story-rdqm/QUESTION.md", (
            f"needs_info_path was cleared on resume — this breaks the resume "
            f"signal for the phase runner. Got {row.get('needs_info_path')!r}."
        )

    @pytest.mark.asyncio
    async def test_reclaim_after_resume_carries_path(self, dispatch_db_service):
        """A-02: claim() on a resumed (pending) story returns row with needs_info_path set.

        This is the signal the /claim route surfaces to the poller.
        """
        svc = dispatch_db_service
        story = "STORY-RDQM-A02"
        await svc.enqueue(**_enqueue_payload(story))
        await svc.claim(story, agent_name="devon")
        await svc.needs_info(story, "features/story-rdqm-a02/QUESTION.md")
        await svc.resume_from_needs_info(story)

        # Fresh claim after resume
        row = await svc.claim(story, agent_name="daisy")

        assert row["status"] == "claimed"
        assert row.get("needs_info_path") == "features/story-rdqm-a02/QUESTION.md", (
            f"claim() on a resumed row must carry needs_info_path through so "
            f"the /claim response can signal 'this is a resume, delete QUESTION.md'. "
            f"Got {row.get('needs_info_path')!r}."
        )


# ---------------------------------------------------------------------------
# Group B — Route: ClaimResponse includes needs_info_path
# ---------------------------------------------------------------------------


class TestClaimResponseCarriesNeedsInfoPath:
    """B-01, B-02: POST /api/dispatch/claim/{id} surfaces needs_info_path."""

    @pytest.mark.asyncio
    async def test_claim_after_resume_returns_needs_info_path(
        self, client, app, dispatch_db_service
    ):
        """B-01: After resume, re-claim response includes needs_info_path.

        RED until ClaimResponse adds the field AND the claim route populates
        it from the row.
        """
        _inject(app, dispatch_db_service)

        story = "STORY-RDQM-B01"
        await client.post("/api/dispatch", json=_enqueue_payload(story))
        await client.post(f"/api/dispatch/claim/{story}", json={"agent_name": "devon"})
        ni = await client.post(
            f"/api/dispatch/needs-info/{story}",
            json={
                "agent": "devon",
                "question_file_path": "features/story-rdqm-b01/QUESTION.md",
                "phase": 1,
            },
        )
        assert ni.status_code == 200, f"needs-info endpoint precondition failed: {ni.text}"

        resume = await client.post(f"/api/dispatch/resume/{story}")
        assert resume.status_code == 200, f"resume precondition failed: {resume.text}"

        claim = await client.post(
            f"/api/dispatch/claim/{story}", json={"agent_name": "daisy"}
        )
        assert claim.status_code == 200, f"claim failed: {claim.text}"
        body = claim.json()

        # needs_info_path surfaces at top-level OR inside item — accept either
        top_level = body.get("needs_info_path")
        nested = body.get("item", {}).get("needs_info_path")
        assert top_level == "features/story-rdqm-b01/QUESTION.md" or nested == "features/story-rdqm-b01/QUESTION.md", (
            f"/api/dispatch/claim response must include needs_info_path after "
            f"resume — the poller uses it to trigger QUESTION.md deletion.\n"
            f"top-level needs_info_path={top_level!r}, nested={nested!r}, "
            f"full body={body}"
        )

    @pytest.mark.asyncio
    async def test_claim_fresh_story_has_null_needs_info_path(
        self, client, app, dispatch_db_service
    ):
        """B-02: A never-paused story's claim response has needs_info_path=None.

        Sanity check that the resume signal is only present when it should be.
        """
        _inject(app, dispatch_db_service)

        story = "STORY-RDQM-B02"
        await client.post("/api/dispatch", json=_enqueue_payload(story))
        claim = await client.post(
            f"/api/dispatch/claim/{story}", json={"agent_name": "daisy"}
        )
        assert claim.status_code == 200, f"claim failed: {claim.text}"
        body = claim.json()

        top_level = body.get("needs_info_path")
        nested = body.get("item", {}).get("needs_info_path")
        assert top_level in (None, ""), (
            f"Fresh-pending story should not carry a needs_info_path: {top_level!r}"
        )
        assert nested in (None, ""), (
            f"Fresh-pending story should not carry a needs_info_path (nested): {nested!r}"
        )
