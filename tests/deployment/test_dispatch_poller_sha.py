"""Tests for dispatch poller commit_sha reporting.

STORY-253: Commit-Gated Dispatch Completion
Phase 7: RED state — poller must send real commit_sha on completion.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch, call

import pytest


class TestReportCompleteWithSha:
    """P01-P03: _report_complete sends commit_sha."""

    def test_report_complete_sends_commit_sha_in_body(self):
        """P01: _report_complete includes commit_sha in POST body."""
        from deployment.hermes.dispatch_poller import _report_complete

        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_session.post.return_value = mock_resp

        sha = "a" * 40

        _report_complete(
            session=mock_session,
            base_url="http://ops:8000",
            api_key="test-key",
            story_id="STORY-253",
            commit_sha=sha,
            duration_seconds=120,
        )

        mock_session.post.assert_called_once()
        call_args = mock_session.post.call_args
        body = call_args[1].get("json") or call_args.kwargs.get("json")
        assert body is not None
        assert body.get("commit_sha") == sha

    def test_report_complete_resolves_head_sha(self):
        """P02: start_story's completion thread resolves HEAD via git rev-parse."""
        import time
        from deployment.hermes.dispatch_poller import start_story

        fake_sha = "b" * 40

        with patch("deployment.hermes.dispatch_poller.subprocess.Popen") as mock_popen, \
             patch("deployment.hermes.dispatch_poller._report_complete") as mock_report, \
             patch("deployment.hermes.dispatch_poller.subprocess.run") as mock_run, \
             patch.dict("os.environ", {
                 "OPS_CONSOLE_URL": "http://ops:8000",
                 "OPS_CONSOLE_API_KEY": "test-key",
             }):
            # SDK process exits cleanly
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.wait.return_value = None
            mock_popen.return_value = mock_proc

            # git rev-parse HEAD returns the SHA
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=fake_sha + "\n",
            )

            start_story(
                story_id="STORY-253",
                repo="tech-dev-agents",
                scope="small",
                prompt="Phase 8",
                workspace="/home/hermes/workspace",
            )

            # Wait for daemon thread
            time.sleep(0.5)

            mock_report.assert_called_once()
            call_kwargs = mock_report.call_args
            # commit_sha should be in the kwargs
            assert call_kwargs is not None
            # Check commit_sha was passed
            all_kwargs = call_kwargs.kwargs if call_kwargs.kwargs else {}
            if not all_kwargs:
                # Try positional named args
                all_kwargs = dict(zip(
                    ["session", "base_url", "api_key", "story_id"],
                    call_kwargs.args if call_kwargs.args else [],
                ))
                all_kwargs.update(call_kwargs.kwargs or {})
            assert all_kwargs.get("commit_sha") == fake_sha

    def test_report_complete_sends_none_sha_when_git_fails(self):
        """P03: When git rev-parse fails, commit_sha is None (still attempts completion)."""
        import time
        from deployment.hermes.dispatch_poller import start_story

        with patch("deployment.hermes.dispatch_poller.subprocess.Popen") as mock_popen, \
             patch("deployment.hermes.dispatch_poller._report_complete") as mock_report, \
             patch("deployment.hermes.dispatch_poller.subprocess.run") as mock_run, \
             patch.dict("os.environ", {
                 "OPS_CONSOLE_URL": "http://ops:8000",
                 "OPS_CONSOLE_API_KEY": "test-key",
             }):
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.wait.return_value = None
            mock_popen.return_value = mock_proc

            # git rev-parse fails
            mock_run.return_value = MagicMock(
                returncode=128,
                stdout="",
            )

            start_story(
                story_id="STORY-253",
                repo="tech-dev-agents",
                scope="small",
                prompt="Phase 8",
                workspace="/home/hermes/workspace",
            )

            time.sleep(0.5)

            mock_report.assert_called_once()
            call_kwargs = mock_report.call_args
            assert call_kwargs is not None
            all_kwargs = call_kwargs.kwargs if call_kwargs.kwargs else {}
            if not all_kwargs:
                all_kwargs = dict(zip(
                    ["session", "base_url", "api_key", "story_id"],
                    call_kwargs.args if call_kwargs.args else [],
                ))
                all_kwargs.update(call_kwargs.kwargs or {})
            # commit_sha should be None when git fails
            assert all_kwargs.get("commit_sha") is None
