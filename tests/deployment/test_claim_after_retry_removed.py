"""Tests for the ghost-claim fix: claim-after-retry block removed.

Background
----------
The "claim-after-retry" block in ``dispatch_poller._report_fail`` was added
by STORY-494 so the queue UI would show "claimed" immediately after a
retry-eligible failure. In production this created a ghost claim: the row
flipped to ``claimed`` in the DB but ``/api/dispatch/next`` will never return
a claimed row, so the poller could not rediscover its own planted claim.
The main loop kept polling, seeing "queue empty", and the story sat idle
until human intervention.

Observed incidents:
  * STORY-505 (2026-04-24 01:28:24Z): Phase 7 acceptance-gate failure. After
    ``_report_fail`` fired auto-retry + claim, Devon idled for 18 straight
    minutes of "queue empty" log lines with the row pinned to ``claimed``.
  * STORY-528 (earlier): same class of bug via a different entry point
    (needs-info). That path was separately hardened via Track B
    (``NEEDS_INFO_STORIES`` early-return); this fix addresses the general
    case — every other phase-fail path (acceptance gates, timeouts, crashes,
    SDK errors) also hit the claim-after-retry code.

Fix
---
Delete the claim-after-retry block. After a retry-eligible failure the row
stays ``pending``. The next main-loop tick (60s) receives it from /next
and claims it through the canonical path (``poll_once`` → claim → start_story),
preserving every invariant that matters: branch setup, SDK-quota check,
session-resume capture, lifespan lifecycle.

Test IDs:
  T-CAR-01: _report_fail in retry-eligible state (201 from /dispatch) does
            NOT POST to /api/dispatch/claim/{story_id}.
  T-CAR-02: After _report_fail returns, the next poll_once cycle with /next
            returning the retried story invokes start_story with the expected
            args (i.e. the canonical claim-then-execute flow still works).
  T-CAR-03: Terminal failure (MAX_RETRY_ATTEMPTS exhausted) writes the
            failure flag file and does NOT re-enqueue. Regression guard.
"""

from __future__ import annotations

import os
import sys
import pathlib
import tempfile
from unittest.mock import MagicMock, patch

import pytest


REPO_ROOT = pathlib.Path(__file__).parents[2]
HERMES_DIR = REPO_ROOT / "deployment" / "hermes"
if str(HERMES_DIR) not in sys.path:
    sys.path.insert(0, str(HERMES_DIR))


class TestClaimAfterRetryRemoved:
    """T-CAR-01: _report_fail must not POST /api/dispatch/claim after re-enqueue."""

    def _make_session(self, *, fail_status=200, enqueue_status=201):
        session = MagicMock()
        fail_resp = MagicMock(status_code=fail_status)
        enqueue_resp = MagicMock(status_code=enqueue_status)
        enqueue_resp.text = ""
        session.post.side_effect = [fail_resp, enqueue_resp]
        return session

    def test_no_claim_post_after_retry_enqueue(self):
        """T-CAR-01: After a retry-eligible failure, no POST to /api/dispatch/claim."""
        with patch.dict(os.environ, {"AGENT_NAME": "devon"}):
            from deployment.hermes.dispatch_poller import _report_fail

            session = self._make_session()

            _report_fail(
                session=session,
                base_url="http://ops:8000",
                api_key="test-key",
                story_id="STORY-505",
                exit_code=1,
                repo="tech-dev-agents",
                scope="medium",
                prompt="Run Phase 7 for STORY-505",
            )

        posted_urls = [c[0][0] for c in session.post.call_args_list]
        claim_hits = [u for u in posted_urls if "/api/dispatch/claim/" in u]
        assert claim_hits == [], (
            f"_report_fail POSTed to /api/dispatch/claim: {claim_hits}. "
            "The claim-after-retry block was the root cause of the ghost-claim "
            "incident on STORY-505 (2026-04-24 01:28:24Z). It must not run."
        )
        # The canonical 2-call shape: /fail then /dispatch.
        assert any(u.endswith("/api/dispatch/fail/STORY-505") for u in posted_urls)
        assert any(u.endswith("/api/dispatch") for u in posted_urls)
        assert session.post.call_count == 2


class TestRetriedStoryReclaimedViaMainLoop:
    """T-CAR-02: The next /next tick picks up the retried pending row."""

    def test_main_loop_claims_and_starts_retried_story(self):
        """T-CAR-02: poll_once against a stub /next that returns the retried
        story invokes start_story with the retry prompt.

        This proves the post-fix flow end-to-end: row stays pending → /next
        returns it on the next tick → poll_once claims and starts.
        """
        from deployment.hermes.dispatch_poller import poll_once

        mock_session = MagicMock()

        next_resp = MagicMock()
        next_resp.status_code = 200
        next_resp.json.return_value = {
            "item": {
                "story_id": "STORY-505",
                "repo": "tech-dev-agents",
                "scope": "medium",
                "prompt": "[RETRY 1/3] Run Phase 7 for STORY-505",
                "enqueued_at": "2026-04-24T01:29:00+00:00",
                "enqueued_by": "dispatch-poller-retry",
                "status": "pending",
                "claimed_by": None,
                "claimed_at": None,
            },
            "queue_depth": 1,
        }

        claim_resp = MagicMock()
        claim_resp.status_code = 200
        mock_session.get.return_value = next_resp
        mock_session.post.return_value = claim_resp

        with patch("deployment.hermes.dispatch_poller.is_agent_idle", return_value=True), \
             patch("deployment.hermes.dispatch_poller.start_story") as mock_start, \
             patch("subprocess.run") as mock_subproc:
            # Duplicate-story guard runs `ps aux | grep` — make it return 1 (no dup)
            mock_subproc.return_value = MagicMock(returncode=1)

            result = poll_once(
                session=mock_session,
                base_url="http://ops:8000",
                api_key="test-key",
                agent_name="devon",
                workspace="/home/hermes/workspace",
            )

        assert result == "claimed", (
            f"poll_once returned {result!r}; expected 'claimed' — the main loop "
            "must be able to rediscover the retried pending row."
        )
        mock_start.assert_called_once()
        call_kwargs = mock_start.call_args[1]
        assert call_kwargs["story_id"] == "STORY-505"
        assert call_kwargs["repo"] == "tech-dev-agents"
        assert call_kwargs["scope"] == "medium"
        assert "[RETRY 1/3]" in call_kwargs["prompt"]


class TestTerminalFailureNoReEnqueue:
    """T-CAR-03: Exhausted-retry path still writes the failure flag and skips enqueue."""

    def test_exhausted_retries_writes_flag_and_skips_enqueue(self, tmp_path, monkeypatch):
        """T-CAR-03: After MAX_RETRY_ATTEMPTS, no re-enqueue POST fires.

        Regression guard for the terminal branch in _report_fail. The deleted
        claim-after-retry block sat after the re-enqueue, but the exhausted
        branch returns before reaching either — we want to prove the
        reorganization did not perturb that branch.
        """
        # Redirect the flag-file write into a tmp dir by patching HOME expansion.
        fake_state_home = tmp_path / "state"
        real_makedirs = os.makedirs

        def _redirected_makedirs(path, exist_ok=False):
            # Route /home/hermes/state/... writes to tmp_path/state/...
            if str(path).startswith("/home/hermes/state/"):
                path = str(fake_state_home) + str(path)[len("/home/hermes/state"):]
            return real_makedirs(path, exist_ok=exist_ok)

        real_open = open

        def _redirected_open(path, *args, **kwargs):
            if isinstance(path, str) and path.startswith("/home/hermes/state/"):
                path = str(fake_state_home) + path[len("/home/hermes/state"):]
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr("os.makedirs", _redirected_makedirs)
        monkeypatch.setattr("builtins.open", _redirected_open)

        with patch.dict(os.environ, {"AGENT_NAME": "devon"}):
            from deployment.hermes.dispatch_poller import _report_fail

            session = MagicMock()
            fail_resp = MagicMock(status_code=200)
            session.post.return_value = fail_resp

            # Prompt already exhausted: 3 retries stamped on it.
            exhausted_prompt = "[RETRY 3/3] Run Phase 7 for STORY-505"

            _report_fail(
                session=session,
                base_url="http://ops:8000",
                api_key="test-key",
                story_id="STORY-505",
                exit_code=1,
                repo="tech-dev-agents",
                scope="medium",
                prompt=exhausted_prompt,
            )

        # Only the /fail POST fires — no re-enqueue, no claim.
        assert session.post.call_count == 1
        posted_url = session.post.call_args[0][0]
        assert "/api/dispatch/fail/STORY-505" in posted_url
        # Flag file was written under the redirected tmp dir.
        expected_flag = fake_state_home / "devon" / "failed-stories" / "STORY-505.txt"
        assert expected_flag.exists(), (
            f"Expected failure flag at {expected_flag}; directory contents: "
            f"{list(fake_state_home.rglob('*'))}"
        )
        contents = expected_flag.read_text()
        assert "STORY STORY-505 FAILED" in contents
