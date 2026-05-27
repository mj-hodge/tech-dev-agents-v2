"""Tests for STORY-494 claim-after-retry behavior.

Historical context
------------------
STORY-494 originally added a claim-after-retry block in ``_report_fail``:
after the poller re-enqueued a failed story (HTTP 201 from /dispatch), it
immediately POSTed /api/dispatch/claim/<story_id> so the queue UI would show
"claimed" during the retry window instead of briefly flipping to "pending".

On 2026-04-24 that block was deleted (``fix/claim-after-retry-ghost-claim``)
because it created a ghost-claim pathway: /api/dispatch/next never returns a
row that is already claimed, so the poller could not rediscover its own
planted claim, and the row sat claimed with the agent idle forever.
STORY-505's Phase 7 acceptance-gate fail at 01:28:24Z burned 18+ minutes
of wall clock on exactly this path.

These tests now assert the inverse of the original STORY-494 behavior: after
a retry-eligible failure, ``_report_fail`` must NOT POST /api/dispatch/claim.
The row is left pending and the main poll loop picks it up on the next tick.

Test IDs:
  T494-P-01: _report_fail does NOT call /api/dispatch/claim after a 201 re-enqueue.
  T494-P-02: _report_fail does NOT call /api/dispatch/claim even if we simulate
             the 409 "already claimed by another agent" re-enqueue path.
  T494-P-03: _report_fail for exit_code=429 (rate limit) makes only the /fail POST.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest


class TestReportFailNoClaimAfterRetry:
    """T494-P-01 through T494-P-03: claim-after-retry block is gone."""

    def _make_session(self, *, fail_status=200, enqueue_status=201):
        """Build a mock requests.Session with configurable response codes.

        Only two responses are queued now (fail, enqueue). If the production
        code still POSTs a third time (the removed claim-after-retry), the
        mock will raise StopIteration and the test will fail loudly.
        """
        session = MagicMock()
        fail_resp = MagicMock(status_code=fail_status)
        enqueue_resp = MagicMock(status_code=enqueue_status)
        enqueue_resp.text = ""
        session.post.side_effect = [fail_resp, enqueue_resp]
        return session

    def test_report_fail_does_not_claim_after_reenqueue(self):
        """T494-P-01: After re-enqueueing for retry, _report_fail must NOT claim.

        Ghost-claim fix: the row is left pending; the next poll_once tick
        discovers it via /api/dispatch/next and claims it through the canonical
        path (which also invokes start_story).
        """
        with patch.dict(os.environ, {"AGENT_NAME": "daisy"}):
            from deployment.hermes.dispatch_poller import _report_fail

            session = self._make_session()

            _report_fail(
                session=session,
                base_url="http://ops:8000",
                api_key="test-key",
                story_id="STORY-494",
                exit_code=1,
                repo="tech-dev-agents",
                scope="small",
                prompt="Implement something",
            )

        # Exactly 2 POSTs: /fail and /dispatch. No /claim.
        assert session.post.call_count == 2, (
            f"Expected 2 POSTs (fail, re-enqueue), got {session.post.call_count}. "
            "The claim-after-retry block must be deleted."
        )
        call_args = session.post.call_args_list
        assert "/api/dispatch/fail/STORY-494" in call_args[0][0][0]
        assert call_args[1][0][0].endswith("/api/dispatch")
        claim_hits = [
            c for c in call_args if "/api/dispatch/claim/" in c[0][0]
        ]
        assert claim_hits == [], (
            f"_report_fail POSTed to /api/dispatch/claim: {claim_hits}. "
            "This is the ghost-claim pathway and must not happen."
        )

    def test_report_fail_does_not_claim_when_reenqueue_returns_409(self):
        """T494-P-02: 409 re-enqueue (already claimed by another agent) also skips claim.

        The pre-fix code special-cased 201 vs 409 to avoid stomping another
        agent's claim. Post-fix there is no claim call in either branch, so
        the distinction is moot — verify both branches stay claim-free.
        """
        with patch.dict(os.environ, {"AGENT_NAME": "daisy"}):
            from deployment.hermes.dispatch_poller import _report_fail

            session = self._make_session(enqueue_status=409)

            _report_fail(
                session=session,
                base_url="http://ops:8000",
                api_key="test-key",
                story_id="STORY-494",
                exit_code=1,
                repo="tech-dev-agents",
                scope="small",
                prompt="Implement something",
            )

        assert session.post.call_count == 2
        claim_hits = [
            c for c in session.post.call_args_list
            if "/api/dispatch/claim/" in c[0][0]
        ]
        assert claim_hits == []

    def test_report_fail_skips_retry_for_rate_limit(self):
        """T494-P-03: Rate-limit failures (exit_code=429) skip retry entirely.

        Only the /fail POST happens. No re-enqueue, no claim.
        """
        from deployment.hermes.dispatch_poller import _report_fail

        session = MagicMock()
        fail_resp = MagicMock(status_code=200)
        session.post.return_value = fail_resp

        _report_fail(
            session=session,
            base_url="http://ops:8000",
            api_key="test-key",
            story_id="STORY-494",
            exit_code=429,
            repo="tech-dev-agents",
            scope="small",
            prompt="Implement something",
        )

        assert session.post.call_count == 1
        assert "/api/dispatch/fail/STORY-494" in session.post.call_args[0][0]
