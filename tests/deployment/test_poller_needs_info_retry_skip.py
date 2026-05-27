"""Tests for the STORY-528 fix: poller auto-retry must skip needs_info stories.

Background
----------
On 2026-04-24 00:30–00:44Z a STORY-528 failure cascade produced:

  1. Phase runner detected QUESTION.md and called /needs-info.
  2. /needs-info returned 409 (endpoint wasn't idempotent).
  3. Runner fell back to _notify_teams + return (False, None).
  4. Poller treated (False, None) as a generic failure.
  5. _report_fail was called, which re-enqueued the story, claimed it, and
     launched a fresh SDK session that re-wrote QUESTION.md — the ghost
     claim loop.

The fix is two-layered:

  * Primary: the phase runner adds the story_id to a module-level
    NEEDS_INFO_STORIES set when it decides the story is blocked waiting
    for a human. The poller consults this set before calling
    _report_fail and skips if the story is there.
  * Defensive: this set is the poller's in-process source of truth. Even
    if /needs-info POST to ops-console failed, this agent's poller must
    not auto-retry.

Test IDs:
  T-NI-R-01: _report_fail is NOT called from the main loop when a needs_info
             story completes the phase runner with (False, None).
  T-NI-R-02: If _report_fail IS called on a needs_info story (e.g. from a
             code path that forgets to check), the auto-retry branch is
             skipped — no re-enqueue, no claim.
  T-NI-R-03: _report_fail with a non-needs_info story still auto-retries
             normally (regression guard for the skip logic).
"""

from __future__ import annotations

import os
import sys
import pathlib
from unittest.mock import MagicMock, patch

import pytest


REPO_ROOT = pathlib.Path(__file__).parents[2]
HERMES_DIR = REPO_ROOT / "deployment" / "hermes"
if str(HERMES_DIR) not in sys.path:
    sys.path.insert(0, str(HERMES_DIR))


def _make_session(*, fail_status=200, enqueue_status=201, claim_status=200):
    session = MagicMock()
    session.post.side_effect = [
        MagicMock(status_code=fail_status),
        MagicMock(status_code=enqueue_status),
        MagicMock(status_code=claim_status),
    ]
    return session


class TestReportFailSkipsNeedsInfo:
    """T-NI-R-02, T-NI-R-03: defensive skip inside _report_fail."""

    def test_report_fail_skips_auto_retry_for_needs_info_story(self):
        """T-NI-R-02: _report_fail consults NEEDS_INFO_STORIES before auto-retrying.

        This is the defensive backup per the STORY-528 fix spec:

          > the auto-retry branch in dispatch_poller.py should inspect the
          > story's current DB state before re-enqueuing. If state is
          > needs_info, skip the auto-retry entirely and log.

        The in-process NEEDS_INFO_STORIES set from sdlc_phase_runner is the
        cheapest, most reliable source of truth — it's populated by the same
        process in the same phase cycle.
        """
        with patch.dict(os.environ, {"AGENT_NAME": "daisy"}):
            import sdlc_phase_runner as runner_mod
            from dispatch_poller import _report_fail

            # Ensure the runner module exposes the set (RED if missing)
            if not hasattr(runner_mod, "NEEDS_INFO_STORIES"):
                pytest.fail(
                    "NEEDS_INFO_STORIES not on sdlc_phase_runner — add it first."
                )

            runner_mod.NEEDS_INFO_STORIES.clear()
            runner_mod.NEEDS_INFO_STORIES.add("STORY-528")

            session = self._session_factory()

            _report_fail(
                session=session,
                base_url="http://ops:8000",
                api_key="test-key",
                story_id="STORY-528",
                exit_code=1,
                repo="tech-dev-agents",
                scope="medium",
                prompt="Some prompt",
            )

            runner_mod.NEEDS_INFO_STORIES.clear()

        # Expect ONLY the /fail POST — no re-enqueue, no claim-after-retry.
        # (The /fail POST itself is still OK; ops-console will 409 or 200 as
        # appropriate. The critical invariant is: no auto-retry re-dispatch.)
        posted_urls = [
            (args[0][0] if args[0] else args[1].get("url", ""))
            for args in session.post.call_args_list
        ]
        reenqueue_hits = [u for u in posted_urls if u.endswith("/api/dispatch")]
        claim_hits = [u for u in posted_urls if "/api/dispatch/claim/" in u]

        assert len(reenqueue_hits) == 0, (
            f"Auto-retry re-enqueued a needs_info story: {reenqueue_hits}. "
            "The poller must skip re-enqueue when story_id is in "
            "NEEDS_INFO_STORIES."
        )
        assert len(claim_hits) == 0, (
            f"claim-after-retry fired on a needs_info story: {claim_hits}. "
            "This is the ghost-claim pathway from the STORY-528 incident."
        )

    def test_report_fail_still_retries_when_not_needs_info(self):
        """T-NI-R-03: Regression guard — non-needs_info stories still auto-retry.

        The skip logic must only fire for stories present in
        NEEDS_INFO_STORIES. Generic failures retain the existing
        fail → re-enqueue flow (claim deferred to next poll tick).
        """
        with patch.dict(os.environ, {"AGENT_NAME": "daisy"}):
            import sdlc_phase_runner as runner_mod
            from dispatch_poller import _report_fail

            if not hasattr(runner_mod, "NEEDS_INFO_STORIES"):
                pytest.fail("NEEDS_INFO_STORIES not on sdlc_phase_runner")

            runner_mod.NEEDS_INFO_STORIES.clear()

            session = self._session_factory()

            _report_fail(
                session=session,
                base_url="http://ops:8000",
                api_key="test-key",
                story_id="STORY-999",  # NOT in NEEDS_INFO_STORIES
                exit_code=1,
                repo="tech-dev-agents",
                scope="small",
                prompt="Fresh non-needs_info failure",
            )

        # Normal flow: fail, re-enqueue. Claim is intentionally deferred to
        # the next /dispatch/next poll tick.
        assert session.post.call_count == 2, (
            f"Expected 2 POSTs (fail, re-enqueue), got {session.post.call_count}. "
            "The skip logic must NOT fire when story is not in NEEDS_INFO_STORIES."
        )
        posted_urls = [args[0][0] for args in session.post.call_args_list]
        assert any("/api/dispatch/fail/STORY-999" in u for u in posted_urls)
        assert any(u.endswith("/api/dispatch") for u in posted_urls), (
            f"Expected a re-enqueue POST for STORY-999, got {posted_urls}"
        )

    def _session_factory(self):
        """Build a session that returns 200/201/200 for fail/enqueue/claim."""
        session = MagicMock()
        fail_resp = MagicMock(status_code=200)
        enqueue_resp = MagicMock(status_code=201)
        claim_resp = MagicMock(status_code=200)
        session.post.side_effect = [fail_resp, enqueue_resp, claim_resp]
        return session
