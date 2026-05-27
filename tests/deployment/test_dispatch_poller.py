"""Unit tests for dispatch_poller — agent-side polling loop for central dispatch queue.

STORY-027: Dispatch Queue Auto-Pickup
Phase 7: RED state — tests written before implementation.

Test IDs:
  T01: is_agent_idle returns True when no SDK process and empty queue
  T02: is_agent_idle returns False when SDK process is running
  T03: is_agent_idle returns False when local queue has active item
  T04: poll_once skips cycle when agent is busy
  T05: poll_once calls GET /api/dispatch/next when idle
  T06: poll_once does nothing on 204 (queue empty)
  T07: poll_once claims story on 200 and starts SDK subprocess
  T08: poll_once retries on 409 (race condition)
  T09: poll_once logs each cycle with [DISPATCH] prefix
  T10: start_story adds to local WorkQueue and starts subprocess
  T11: poll_loop runs multiple cycles with sleep interval
  T12: poll_loop survives exceptions without crashing
  T13: poll_once handles network errors gracefully
"""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch, call

import pytest


class TestIsAgentIdle:
    """T01-T03: Idle detection logic."""

    def test_idle_when_no_sdk_and_empty_queue(self, tmp_path):
        """T01: Agent is idle when no claude_sdk_tool.py and local queue is empty."""
        from deployment.hermes.dispatch_poller import is_agent_idle

        with patch("deployment.hermes.dispatch_poller.subprocess.run") as mock_run:
            # No SDK process found — stdout is empty
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            with patch("deployment.hermes.dispatch_poller._local_queue_active", return_value=False):
                assert is_agent_idle() is True

    def test_busy_when_sdk_process_running(self):
        """T02: Agent is busy when claude_sdk_tool.py process is running."""
        from deployment.hermes.dispatch_poller import is_agent_idle

        with patch("deployment.hermes.dispatch_poller.subprocess.run") as mock_run:
            # SDK process found — stdout has a process line
            mock_run.return_value = MagicMock(returncode=0, stdout="hermes  12345  claude_sdk_tool.py -p")
            assert is_agent_idle() is False

    def test_busy_when_local_queue_has_active_item(self):
        """T03: Agent is busy when local work queue has active item."""
        from deployment.hermes.dispatch_poller import is_agent_idle

        with patch("deployment.hermes.dispatch_poller.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            with patch("deployment.hermes.dispatch_poller._local_queue_active", return_value=True):
                assert is_agent_idle() is False


class TestPollOnce:
    """T04-T09: Single poll cycle."""

    def test_skips_when_busy(self):
        """T04: poll_once returns early when agent is not idle."""
        from deployment.hermes.dispatch_poller import poll_once

        mock_session = MagicMock()
        with patch("deployment.hermes.dispatch_poller.is_agent_idle", return_value=False):
            result = poll_once(
                session=mock_session,
                base_url="http://ops:8000",
                api_key="test-key",
                agent_name="dan",
                workspace="/home/hermes/workspace",
            )
        assert result == "busy"
        mock_session.get.assert_not_called()

    def test_calls_dispatch_next_when_idle(self):
        """T05: poll_once calls GET /api/dispatch/next when idle."""
        from deployment.hermes.dispatch_poller import poll_once

        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 204
        mock_session.get.return_value = mock_resp

        with patch("deployment.hermes.dispatch_poller.is_agent_idle", return_value=True):
            poll_once(
                session=mock_session,
                base_url="http://ops:8000",
                api_key="test-key",
                agent_name="dan",
                workspace="/home/hermes/workspace",
            )
        mock_session.get.assert_called_once_with(
            "http://ops:8000/api/dispatch/next",
            headers={"X-API-Key": "test-key", "X-Agent-Name": "dan"},
            timeout=10,
        )

    def test_returns_empty_on_204(self):
        """T06: poll_once returns 'empty' on 204 (no pending stories)."""
        from deployment.hermes.dispatch_poller import poll_once

        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 204
        mock_session.get.return_value = mock_resp

        with patch("deployment.hermes.dispatch_poller.is_agent_idle", return_value=True):
            result = poll_once(
                session=mock_session,
                base_url="http://ops:8000",
                api_key="test-key",
                agent_name="dan",
                workspace="/home/hermes/workspace",
            )
        assert result == "empty"

    def test_claims_and_starts_story(self):
        """T07: poll_once claims story on 200 and starts SDK subprocess."""
        from deployment.hermes.dispatch_poller import poll_once

        mock_session = MagicMock()

        # GET /dispatch/next returns a story
        next_resp = MagicMock()
        next_resp.status_code = 200
        next_resp.json.return_value = {
            "item": {
                "story_id": "STORY-094",
                "repo": "advertising-amazon",
                "scope": "small",
                "prompt": "Start Phase 7 for STORY-094",
                "enqueued_at": "2026-04-09T10:00:00+00:00",
                "enqueued_by": "mark",
                "status": "pending",
                "claimed_by": None,
                "claimed_at": None,
            },
            "queue_depth": 1,
        }

        # POST /dispatch/claim returns success
        claim_resp = MagicMock()
        claim_resp.status_code = 200
        claim_resp.json.return_value = {
            "story_id": "STORY-094",
            "claimed_by": "dan",
            "claimed_at": "2026-04-09T10:01:00+00:00",
            "item": {},
        }

        mock_session.get.return_value = next_resp
        mock_session.post.return_value = claim_resp

        with patch("deployment.hermes.dispatch_poller.is_agent_idle", return_value=True), \
             patch("deployment.hermes.dispatch_poller.start_story") as mock_start:
            result = poll_once(
                session=mock_session,
                base_url="http://ops:8000",
                api_key="test-key",
                agent_name="dan",
                workspace="/home/hermes/workspace",
            )

        assert result == "claimed"
        mock_session.post.assert_called_once_with(
            "http://ops:8000/api/dispatch/claim/STORY-094",
            json={"agent_name": "dan"},
            headers={"X-API-Key": "test-key", "X-Agent-Name": "dan"},
            timeout=10,
        )
        mock_start.assert_called_once()
        call_kwargs = mock_start.call_args[1]
        assert call_kwargs["story_id"] == "STORY-094"
        assert call_kwargs["repo"] == "advertising-amazon"
        assert call_kwargs["scope"] == "small"
        assert call_kwargs["prompt"] == "Start Phase 7 for STORY-094"
        assert call_kwargs["workspace"] == "/home/hermes/workspace"
        assert call_kwargs["base_url"] == "http://ops:8000"
        assert call_kwargs["api_key"] == "test-key"
        assert call_kwargs["rework_of"] is None
        assert call_kwargs["resumed_question_path"] is None

    def test_claims_and_starts_story_with_nested_needs_info_path(self):
        """Regression: poll_once forwards nested claim.item.needs_info_path."""
        from deployment.hermes.dispatch_poller import poll_once

        mock_session = MagicMock()

        next_resp = MagicMock()
        next_resp.status_code = 200
        next_resp.json.return_value = {
            "item": {
                "story_id": "STORY-639",
                "repo": "advertising-amazon",
                "scope": "small",
                "prompt": "Resume from needs_info",
            },
            "queue_depth": 1,
        }

        claim_resp = MagicMock()
        claim_resp.status_code = 200
        claim_resp.json.return_value = {
            "story_id": "STORY-639",
            "item": {
                "needs_info_path": "features/story-639/QUESTION.md",
            },
        }

        mock_session.get.return_value = next_resp
        mock_session.post.return_value = claim_resp

        with patch("deployment.hermes.dispatch_poller.is_agent_idle", return_value=True), \
             patch("deployment.hermes.dispatch_poller.start_story") as mock_start:
            result = poll_once(
                session=mock_session,
                base_url="http://ops:8000",
                api_key="test-key",
                agent_name="dan",
                workspace="/home/hermes/workspace",
            )

        assert result == "claimed"
        kwargs = mock_start.call_args[1]
        assert kwargs["story_id"] == "STORY-639"
        assert kwargs["resumed_question_path"] == "features/story-639/QUESTION.md"

    def test_retries_on_409_claim(self):
        """T08: poll_once retries GET /dispatch/next after 409 on claim."""
        from deployment.hermes.dispatch_poller import poll_once

        mock_session = MagicMock()

        # First GET /dispatch/next returns STORY-001
        next_resp_1 = MagicMock()
        next_resp_1.status_code = 200
        next_resp_1.json.return_value = {
            "item": {
                "story_id": "STORY-001",
                "repo": "test-repo",
                "scope": "small",
                "prompt": "Do work",
                "enqueued_at": "2026-04-09T10:00:00+00:00",
                "enqueued_by": "mark",
                "status": "pending",
                "claimed_by": None,
                "claimed_at": None,
            },
            "queue_depth": 2,
        }

        # POST /dispatch/claim returns 409 (already claimed)
        claim_409 = MagicMock()
        claim_409.status_code = 409

        # Second GET /dispatch/next returns 204 (empty)
        next_resp_2 = MagicMock()
        next_resp_2.status_code = 204

        mock_session.get.side_effect = [next_resp_1, next_resp_2]
        mock_session.post.return_value = claim_409

        with patch("deployment.hermes.dispatch_poller.is_agent_idle", return_value=True):
            result = poll_once(
                session=mock_session,
                base_url="http://ops:8000",
                api_key="test-key",
                agent_name="dan",
                workspace="/home/hermes/workspace",
            )

        assert result == "empty"
        assert mock_session.get.call_count == 2
        assert mock_session.post.call_count == 1

    def test_logs_dispatch_prefix(self, capsys):
        """T09: poll_once logs with [DISPATCH] prefix."""
        from deployment.hermes.dispatch_poller import poll_once

        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 204
        mock_session.get.return_value = mock_resp

        with patch("deployment.hermes.dispatch_poller.is_agent_idle", return_value=True):
            poll_once(
                session=mock_session,
                base_url="http://ops:8000",
                api_key="test-key",
                agent_name="dan",
                workspace="/home/hermes/workspace",
            )

        captured = capsys.readouterr()
        assert "[DISPATCH]" in captured.out


class TestStartStory:
    """T10: Starting the SDK subprocess."""

    def test_start_story_launches_subprocess(self):
        """T10: start_story calls subprocess.Popen with correct args."""
        from deployment.hermes.dispatch_poller import start_story

        with patch("deployment.hermes.dispatch_poller.subprocess.Popen") as mock_popen:
            start_story(
                story_id="STORY-094",
                repo="advertising-amazon",
                scope="small",
                prompt="Start Phase 7 for STORY-094",
                workspace="/home/hermes/workspace",
            )

        mock_popen.assert_called_once()
        call_args = mock_popen.call_args
        cmd = call_args[0][0]  # first positional arg (the command list)
        assert "claude_sdk_tool.py" in cmd[1]
        assert "-p" in cmd
        assert "-w" in cmd


class TestPollLoop:
    """T11-T12: Continuous polling loop."""

    def test_poll_loop_runs_multiple_cycles(self):
        """T11: poll_loop calls poll_once at the configured interval."""
        from deployment.hermes.dispatch_poller import poll_loop

        call_count = 0

        def mock_poll_once(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                raise KeyboardInterrupt  # Stop the loop
            return "empty"

        with patch("deployment.hermes.dispatch_poller.poll_once", side_effect=mock_poll_once), \
             patch("deployment.hermes.dispatch_poller.time.sleep") as mock_sleep, \
             patch("deployment.hermes.dispatch_poller.requests.Session") as mock_session_cls:
            mock_session_cls.return_value = MagicMock()
            try:
                poll_loop(
                    base_url="http://ops:8000",
                    api_key="test-key",
                    agent_name="dan",
                    workspace="/home/hermes/workspace",
                    poll_interval=30,
                )
            except KeyboardInterrupt:
                pass

        assert call_count == 3
        assert mock_sleep.call_count >= 2
        mock_sleep.assert_called_with(30)

    def test_poll_loop_survives_exceptions(self):
        """T12: poll_loop continues after poll_once raises an exception."""
        from deployment.hermes.dispatch_poller import poll_loop

        call_count = 0

        def mock_poll_once(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("network down")
            if call_count >= 3:
                raise KeyboardInterrupt
            return "empty"

        with patch("deployment.hermes.dispatch_poller.poll_once", side_effect=mock_poll_once), \
             patch("deployment.hermes.dispatch_poller.time.sleep"), \
             patch("deployment.hermes.dispatch_poller.requests.Session") as mock_session_cls:
            mock_session_cls.return_value = MagicMock()
            try:
                poll_loop(
                    base_url="http://ops:8000",
                    api_key="test-key",
                    agent_name="dan",
                    workspace="/home/hermes/workspace",
                    poll_interval=10,
                )
            except KeyboardInterrupt:
                pass

        # Should have continued past the exception
        assert call_count == 3


class TestPollOnceNetworkError:
    """T13: Graceful handling of network errors."""

    def test_handles_connection_error(self):
        """T13: poll_once returns 'error' on network failure."""
        from deployment.hermes.dispatch_poller import poll_once
        import requests

        mock_session = MagicMock()
        mock_session.get.side_effect = requests.ConnectionError("refused")

        with patch("deployment.hermes.dispatch_poller.is_agent_idle", return_value=True):
            result = poll_once(
                session=mock_session,
                base_url="http://ops:8000",
                api_key="test-key",
                agent_name="dan",
                workspace="/home/hermes/workspace",
            )

        assert result == "error"


# ---------------------------------------------------------------------------
# STORY-028: Completion reporting and Teams auto-register tests
# Phase 7: RED state — tests written before implementation.
# ---------------------------------------------------------------------------


class TestReportComplete:
    """PL-01 to PL-03: _report_complete function."""

    def test_report_complete_sends_post(self):
        """PL-01: _report_complete POSTs to /api/dispatch/complete/{story_id}."""
        from deployment.hermes.dispatch_poller import _report_complete

        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_session.post.return_value = mock_resp

        _report_complete(
            session=mock_session,
            base_url="http://ops:8000",
            api_key="test-key",
            story_id="STORY-094",
            commit_sha="abc1234def567890abc1234def567890abc12345",
            duration_seconds=229,
        )

        mock_session.post.assert_called_once()
        call_args = mock_session.post.call_args
        assert "/api/dispatch/complete/STORY-094" in call_args[0][0]
        # STORY-253: body must include the commit_sha
        body = call_args.kwargs.get("json", {})
        assert body.get("commit_sha") == "abc1234def567890abc1234def567890abc12345"

    def test_report_complete_handles_network_errors(self, capsys):
        """PL-02: _report_complete swallows network errors (best-effort)."""
        from deployment.hermes.dispatch_poller import _report_complete

        mock_session = MagicMock()
        mock_session.post.side_effect = ConnectionError("refused")

        # Should NOT raise
        _report_complete(
            session=mock_session,
            base_url="http://ops:8000",
            api_key="test-key",
            story_id="STORY-094",
            commit_sha="abc1234def567890abc1234def567890abc12345",
        )

        captured = capsys.readouterr()
        assert "[DISPATCH]" in captured.out

    def test_report_complete_includes_all_fields(self):
        """PL-03: _report_complete sends cost/turns/duration/error in body."""
        from deployment.hermes.dispatch_poller import _report_complete

        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_session.post.return_value = mock_resp

        _report_complete(
            session=mock_session,
            base_url="http://ops:8000",
            api_key="test-key",
            story_id="STORY-094",
            commit_sha="abc1234def567890abc1234def567890abc12345",
            cost_usd=1.42,
            turns=42,
            duration_seconds=229,
            error=True,
        )

        call_args = mock_session.post.call_args
        body = call_args[1]["json"] if "json" in call_args[1] else call_args[1].get("json")
        assert body["cost_usd"] == 1.42
        assert body["turns"] == 42
        assert body["duration_seconds"] == 229
        assert body["error"] is True

    def test_report_complete_accepts_repo_and_returns_status(self):
        """Regression: _report_complete must accept repo kwarg used by retry wrapper."""
        from deployment.hermes.dispatch_poller import _report_complete

        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_session.post.return_value = mock_resp

        status = _report_complete(
            session=mock_session,
            base_url="http://ops:8000",
            api_key="test-key",
            story_id="STORY-094",
            repo="tech-dev-agents",
            commit_sha="abc1234def567890abc1234def567890abc12345",
        )

        assert status == 500
        body = mock_session.post.call_args.kwargs.get("json", {})
        assert body.get("repo") == "tech-dev-agents"


class TestRegisterDispatch:
    """PL-04 to PL-06: _register_dispatch function (Teams auto-register)."""

    def test_register_enqueues_and_claims(self):
        """PL-04: _register_dispatch POSTs to /api/dispatch then /claim."""
        from deployment.hermes.dispatch_poller import _register_dispatch

        mock_session = MagicMock()
        enqueue_resp = MagicMock()
        enqueue_resp.status_code = 201
        claim_resp = MagicMock()
        claim_resp.status_code = 200
        mock_session.post.side_effect = [enqueue_resp, claim_resp]

        _register_dispatch(
            session=mock_session,
            base_url="http://ops:8000",
            api_key="test-key",
            story_id="STORY-094",
            repo="advertising-amazon",
            scope="small",
            prompt="Teams dispatch",
            agent_name="hermes",
        )

        assert mock_session.post.call_count == 2
        # First call: enqueue
        first_call = mock_session.post.call_args_list[0]
        assert "/api/dispatch" in first_call[0][0]
        body = first_call[1]["json"]
        assert body["source"] == "teams"
        # Second call: claim
        second_call = mock_session.post.call_args_list[1]
        assert "/api/dispatch/claim/STORY-094" in second_call[0][0]

    def test_register_handles_409_already_queued(self):
        """PL-05: _register_dispatch proceeds to claim on 409 (already queued)."""
        from deployment.hermes.dispatch_poller import _register_dispatch

        mock_session = MagicMock()
        enqueue_resp = MagicMock()
        enqueue_resp.status_code = 409  # already in queue
        claim_resp = MagicMock()
        claim_resp.status_code = 200
        mock_session.post.side_effect = [enqueue_resp, claim_resp]

        _register_dispatch(
            session=mock_session,
            base_url="http://ops:8000",
            api_key="test-key",
            story_id="STORY-094",
            repo="test-repo",
            scope="small",
            prompt="Already queued",
            agent_name="hermes",
        )

        # Should still attempt claim
        assert mock_session.post.call_count == 2

    def test_register_handles_network_error(self, capsys):
        """PL-06: _register_dispatch swallows network errors."""
        from deployment.hermes.dispatch_poller import _register_dispatch

        mock_session = MagicMock()
        mock_session.post.side_effect = ConnectionError("refused")

        # Should NOT raise
        _register_dispatch(
            session=mock_session,
            base_url="http://ops:8000",
            api_key="test-key",
            story_id="STORY-094",
            repo="test-repo",
            scope="small",
            prompt="Network error",
            agent_name="hermes",
        )

        captured = capsys.readouterr()
        assert "[DISPATCH]" in captured.out


class TestStartStoryReportsComplete:
    """PL-07: start_story calls _report_complete after SDK exit."""

    def test_start_story_calls_report_complete(self):
        """PL-07: After SDK process exits with a real commit, _report_complete is called
        with the commit SHA (STORY-253 guard).
        """
        import threading as real_threading
        from deployment.hermes.dispatch_poller import start_story

        # STORY-253: validation reads git state via subprocess.run. Mock those
        # to return plausible results so the commit SHA is extracted.
        def fake_run(cmd, *args, **kwargs):
            out = MagicMock()
            out.returncode = 0
            # branch -r --list *094*
            if "branch" in cmd:
                out.stdout = "origin/story-094/work\n"
            # log origin/main..HEAD --oneline --max-count=1
            elif "log" in cmd and "--oneline" in cmd:
                out.stdout = "abc1234 feat(STORY-094): thing\n"
            # log origin/main..HEAD --format=%H --max-count=1
            elif "log" in cmd and "--format=%H" in cmd:
                out.stdout = "abc1234def567890abc1234def567890abc12345\n"
            # gh pr list ...
            elif "bash" in cmd[0] if isinstance(cmd, list) and cmd else False:
                out.stdout = "7\n"
            else:
                out.stdout = ""
            return out

        with patch("deployment.hermes.dispatch_poller.subprocess.Popen") as mock_popen, \
             patch("deployment.hermes.dispatch_poller.subprocess.run", side_effect=fake_run), \
             patch("deployment.hermes.dispatch_poller._report_complete") as mock_report, \
             patch.dict("os.environ", {"OPS_CONSOLE_URL": "http://ops:8000", "OPS_CONSOLE_API_KEY": "test-key"}):
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.wait.return_value = None
            mock_popen.return_value = mock_proc

            # Run start_story — it spawns a daemon thread
            start_story(
                story_id="STORY-094",
                repo="advertising-amazon",
                scope="small",
                prompt="Start Phase 7",
                workspace="/home/hermes/workspace",
            )

            # Wait briefly for the daemon thread to run
            import time
            time.sleep(0.5)

            # _report_complete should have been called in the thread
            mock_report.assert_called_once()
            call_kwargs = mock_report.call_args
            assert call_kwargs is not None
            # STORY-253: commit_sha must be forwarded
            assert call_kwargs.kwargs.get("commit_sha") == "abc1234def567890abc1234def567890abc12345"


class TestParseResetTime:
    """STORY-253 rate-limit pause: parse Claude Code's reset-time string."""

    def _at(self, hour, minute=0):
        import datetime as dt
        return dt.datetime(2026, 4, 15, hour, minute, 0, tzinfo=dt.timezone.utc)

    def test_pm_format_today(self):
        from deployment.hermes.dispatch_poller import _parse_reset_time
        # at 8pm UTC, "9pm (UTC)" → 9pm same day
        ts = _parse_reset_time("9pm (UTC)", now_utc=self._at(20))
        assert ts == self._at(21).timestamp()

    def test_pm_format_already_past_rolls_to_tomorrow(self):
        from deployment.hermes.dispatch_poller import _parse_reset_time
        # at 10pm UTC, "9pm (UTC)" → 9pm tomorrow
        ts = _parse_reset_time("9pm (UTC)", now_utc=self._at(22))
        assert ts == self._at(21).timestamp() + 86400

    def test_am_midnight_rolls_correctly(self):
        from deployment.hermes.dispatch_poller import _parse_reset_time
        # 12am = midnight; at 11pm → midnight tomorrow
        ts = _parse_reset_time("12am (UTC)", now_utc=self._at(23))
        assert ts == self._at(0).timestamp() + 86400

    def test_with_minutes(self):
        from deployment.hermes.dispatch_poller import _parse_reset_time
        ts = _parse_reset_time("9:30pm (UTC)", now_utc=self._at(20))
        assert ts == self._at(21, 30).timestamp()

    def test_24h_format(self):
        from deployment.hermes.dispatch_poller import _parse_reset_time
        ts = _parse_reset_time("21:00 UTC", now_utc=self._at(20))
        assert ts == self._at(21).timestamp()

    def test_garbage_returns_none(self):
        from deployment.hermes.dispatch_poller import _parse_reset_time
        for bad in ["", "soon", "tomorrow", "9pm EST", "25pm (UTC)", "13am (UTC)"]:
            assert _parse_reset_time(bad, now_utc=self._at(20)) is None, f"should reject {bad!r}"

    def test_no_parens_around_utc(self):
        from deployment.hermes.dispatch_poller import _parse_reset_time
        ts = _parse_reset_time("9pm UTC", now_utc=self._at(20))
        assert ts == self._at(21).timestamp()


# ---------------------------------------------------------------------------
# STORY-336 (real fixes): rate-limit detection, rework dispatch, SDK exit code
# ---------------------------------------------------------------------------


class TestRateLimitDetectionViaSessionLog:
    """RL-01 to RL-04: rate-limit detection reads session log files, not journalctl."""

    def test_pause_flag_written_when_session_log_contains_rate_limit(self, tmp_path):
        """RL-01: pause flag is written when session log has the rate-limit string."""
        import threading
        import time
        from deployment.hermes.dispatch_poller import start_story

        log_dir = tmp_path / "claude-sdlc-logs"
        log_dir.mkdir()
        pause_path = tmp_path / "dispatch-poller-paused-until"

        # SDK log file written during the run
        session_log = log_dir / "session-9999.log"
        session_log.write_text(
            "[START] doing work\n"
            "[Claude Code] You've hit your limit · resets 9pm (UTC)\n"
            "[END] total=2s\n"
        )

        def fake_run(cmd, *a, **kw):
            out = MagicMock()
            out.returncode = 0
            out.stdout = ""
            return out

        with patch("deployment.hermes.dispatch_poller.subprocess.Popen") as mock_popen, \
             patch("deployment.hermes.dispatch_poller.subprocess.run", side_effect=fake_run), \
             patch("deployment.hermes.dispatch_poller._report_fail") as mock_fail, \
             patch("deployment.hermes.dispatch_poller.os.listdir", return_value=[session_log.name]), \
             patch("deployment.hermes.dispatch_poller.os.path.isdir", return_value=True), \
             patch("deployment.hermes.dispatch_poller.os.path.getmtime", return_value=time.time() + 1), \
             patch("builtins.open", side_effect=lambda p, *a, **kw: (
                 open(str(session_log)) if str(session_log) in str(p) else open(str(pause_path), "w")
             )), \
             patch.dict("os.environ", {"OPS_CONSOLE_URL": "http://ops:8000", "OPS_CONSOLE_API_KEY": "key"}):

            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.pid = 12345
            mock_proc.wait.return_value = None
            mock_popen.return_value = mock_proc

            start_story(
                story_id="STORY-304",
                repo="tech-dev-agents",
                scope="small",
                prompt="STORY-304 work",
                workspace="/home/hermes/workspace",
            )
            time.sleep(0.3)

        # _report_fail must have been called with exit_code=429
        mock_fail.assert_called()
        call_kwargs = mock_fail.call_args.kwargs
        assert call_kwargs.get("exit_code") == 429
        assert call_kwargs.get("story_id") == "STORY-304"

    def test_no_pause_flag_when_log_clean(self, tmp_path):
        """RL-02: no pause flag written for a clean (non-rate-limited) run."""
        import time
        from deployment.hermes.dispatch_poller import start_story

        log_dir = tmp_path / "claude-sdlc-logs"
        log_dir.mkdir()
        session_log = log_dir / "session-1234.log"
        session_log.write_text("[START] work\n[DONE] turns=5\n[END] total=30s\n")

        def fake_run(cmd, *a, **kw):
            out = MagicMock()
            out.returncode = 0
            if "branch" in cmd:
                out.stdout = "  origin/story-304/work\n"
            elif "log" in cmd and "--oneline" in cmd:
                out.stdout = "abc1234 feat\n"
            elif "log" in cmd and "--format=%H" in cmd:
                out.stdout = "abc1234def567890abc1234def567890abc12345\n"
            elif isinstance(cmd, list) and cmd and "bash" in cmd[0]:
                out.stdout = "42\n"
            else:
                out.stdout = ""
            return out

        with patch("deployment.hermes.dispatch_poller.subprocess.Popen") as mock_popen, \
             patch("deployment.hermes.dispatch_poller.subprocess.run", side_effect=fake_run), \
             patch("deployment.hermes.dispatch_poller._report_complete") as mock_complete, \
             patch("deployment.hermes.dispatch_poller.os.path.isdir", return_value=True), \
             patch("deployment.hermes.dispatch_poller.os.listdir", return_value=[session_log.name]), \
             patch("deployment.hermes.dispatch_poller.os.path.getmtime", return_value=time.time() + 1), \
             patch("builtins.open", side_effect=lambda p, *a, **kw: open(str(session_log))), \
             patch("deployment.hermes.dispatch_poller.os.path.exists", return_value=False), \
             patch.dict("os.environ", {"OPS_CONSOLE_URL": "http://ops:8000", "OPS_CONSOLE_API_KEY": "key"}):

            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.pid = 99
            mock_proc.wait.return_value = None
            mock_popen.return_value = mock_proc

            start_story(
                story_id="STORY-304",
                repo="tech-dev-agents",
                scope="small",
                prompt="STORY-304 work",
                workspace="/home/hermes/workspace",
            )
            time.sleep(0.3)

        mock_complete.assert_called()

    def test_rate_limit_detection_no_log_dir(self):
        """RL-03: gracefully no-ops when log dir doesn't exist (no crash)."""
        import time
        from deployment.hermes.dispatch_poller import start_story

        def fake_run(cmd, *a, **kw):
            out = MagicMock()
            out.returncode = 0
            out.stdout = ""
            return out

        with patch("deployment.hermes.dispatch_poller.subprocess.Popen") as mock_popen, \
             patch("deployment.hermes.dispatch_poller.subprocess.run", side_effect=fake_run), \
             patch("deployment.hermes.dispatch_poller._report_fail") as mock_fail, \
             patch("deployment.hermes.dispatch_poller.os.path.isdir", return_value=False), \
             patch.dict("os.environ", {"OPS_CONSOLE_URL": "http://ops:8000", "OPS_CONSOLE_API_KEY": "key"}):

            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.pid = 7
            mock_proc.wait.return_value = None
            mock_popen.return_value = mock_proc

            # Should not raise
            start_story(
                story_id="STORY-304",
                repo="tech-dev-agents",
                scope="small",
                prompt="work",
                workspace="/tmp",
            )
            time.sleep(0.2)

        # No 429 — just fell through to validation failure (no branch)
        if mock_fail.called:
            assert mock_fail.call_args.kwargs.get("exit_code") != 429

    def test_poll_loop_skips_claims_when_paused(self, tmp_path):
        """RL-04: poll_loop skips poll_once when pause flag exists and reset not reached."""
        import time as _time
        from deployment.hermes.dispatch_poller import poll_loop

        pause_path = "/var/run/dispatch-poller-paused-until"
        call_count = [0]

        def mock_poll_once(**kwargs):
            call_count[0] += 1
            raise KeyboardInterrupt

        far_future = _time.time() + 7200  # 2h from now

        def fake_parse_reset(s, now_utc=None):
            return far_future

        with patch("deployment.hermes.dispatch_poller.poll_once", side_effect=mock_poll_once), \
             patch("deployment.hermes.dispatch_poller.time.sleep"), \
             patch("deployment.hermes.dispatch_poller.os.path.exists", return_value=True), \
             patch("deployment.hermes.dispatch_poller._parse_reset_time", side_effect=fake_parse_reset), \
             patch("deployment.hermes.dispatch_poller.time.time", return_value=_time.time()), \
             patch("builtins.open", return_value=MagicMock(__enter__=lambda s: s, __exit__=lambda *a: None, read=lambda: "9pm (UTC)")), \
             patch("deployment.hermes.dispatch_poller.requests.Session") as mock_sess:
            mock_sess.return_value = MagicMock()
            try:
                poll_loop(
                    base_url="http://ops:8000",
                    api_key="key",
                    agent_name="daisy",
                    workspace="/tmp",
                    poll_interval=1,
                )
            except KeyboardInterrupt:
                pass

        assert call_count[0] == 0, "poll_once should not be called while paused"


class TestReworkDispatch:
    """RW-01 to RW-05: rework stories check out existing branch, validate correctly."""

    def test_start_story_rework_prompt_contains_checkout_instruction(self):
        """RW-01: when pr_branch is set, prompt instructs agent to check out existing branch."""
        from deployment.hermes.dispatch_poller import start_story

        captured_prompt = []

        def fake_popen(cmd, *a, **kw):
            # Extract prompt from -p arg
            for i, arg in enumerate(cmd):
                if arg == "-p" and i + 1 < len(cmd):
                    captured_prompt.append(cmd[i + 1])
            m = MagicMock()
            m.pid = 1
            return m

        with patch("deployment.hermes.dispatch_poller.subprocess.Popen", side_effect=fake_popen):
            start_story(
                story_id="STORY-570",
                repo="tech-dev-agents",
                scope="small",
                prompt="Fix PR #169 review comments",
                workspace="/home/hermes/workspace",
                pr_branch="story-169/fix-auth",
                base_story_id="STORY-169",
            )

        assert captured_prompt, "Popen was not called"
        prompt = captured_prompt[0]
        assert "git checkout story-169/fix-auth" in prompt
        assert "DO NOT create a new branch" in prompt
        assert "STORY-169" in prompt

    def test_start_story_no_rework_block_without_pr_branch(self):
        """RW-02: without pr_branch, prompt uses the standard SDLC block."""
        from deployment.hermes.dispatch_poller import start_story

        captured_prompt = []

        def fake_popen(cmd, *a, **kw):
            for i, arg in enumerate(cmd):
                if arg == "-p" and i + 1 < len(cmd):
                    captured_prompt.append(cmd[i + 1])
            m = MagicMock()
            m.pid = 1
            return m

        with patch("deployment.hermes.dispatch_poller.subprocess.Popen", side_effect=fake_popen):
            start_story(
                story_id="STORY-576",
                repo="tech-dev-agents",
                scope="small",
                prompt="New feature work",
                workspace="/home/hermes/workspace",
            )

        assert captured_prompt
        prompt = captured_prompt[0]
        assert "SDLC COMPLIANCE" in prompt
        assert "REWORK DISPATCH" not in prompt

    def test_branch_validation_uses_pr_branch_for_rework(self):
        """RW-03: validation searches for pr_branch pattern, not the rework story slug."""
        import time
        from deployment.hermes.dispatch_poller import start_story

        def fake_run(cmd, *a, **kw):
            out = MagicMock()
            out.returncode = 0
            # Should be looking for *story-169/fix-auth* not *570*
            if "branch" in cmd:
                branch_arg = [a for a in cmd if "*" in a]
                if branch_arg and "169" in branch_arg[0]:
                    out.stdout = "  origin/story-169/fix-auth\n"
                else:
                    out.stdout = ""
            elif "log" in cmd and "--oneline" in cmd:
                out.stdout = "abc1234 fix\n"
            elif "log" in cmd and "--format=%H" in cmd:
                out.stdout = "abc1234def567890abc1234def567890abc12345\n"
            elif isinstance(cmd, list) and cmd and "bash" in cmd[0]:
                out.stdout = "169\n"
            else:
                out.stdout = ""
            return out

        with patch("deployment.hermes.dispatch_poller.subprocess.Popen") as mock_popen, \
             patch("deployment.hermes.dispatch_poller.subprocess.run", side_effect=fake_run), \
             patch("deployment.hermes.dispatch_poller._report_complete") as mock_complete, \
             patch("deployment.hermes.dispatch_poller.os.path.isdir", return_value=False), \
             patch.dict("os.environ", {"OPS_CONSOLE_URL": "http://ops:8000", "OPS_CONSOLE_API_KEY": "key"}):

            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.pid = 5
            mock_proc.wait.return_value = None
            mock_popen.return_value = mock_proc

            start_story(
                story_id="STORY-570",
                repo="tech-dev-agents",
                scope="small",
                prompt="Fix PR #169",
                workspace="/home/hermes/workspace",
                pr_branch="story-169/fix-auth",
                base_story_id="STORY-169",
            )
            time.sleep(0.3)

        mock_complete.assert_called()
        call_kwargs = mock_complete.call_args.kwargs
        assert call_kwargs.get("commit_sha") == "abc1234def567890abc1234def567890abc12345"

    def test_retry_preserves_pr_branch(self):
        """RW-04: _report_fail re-enqueues with pr_branch and base_story_id intact."""
        from deployment.hermes.dispatch_poller import _report_fail

        mock_session = MagicMock()
        fail_resp = MagicMock()
        fail_resp.status_code = 200
        retry_resp = MagicMock()
        retry_resp.status_code = 201
        mock_session.post.side_effect = [fail_resp, retry_resp]

        _report_fail(
            session=mock_session,
            base_url="http://ops:8000",
            api_key="key",
            story_id="STORY-570",
            exit_code=1,
            repo="tech-dev-agents",
            scope="small",
            prompt="Fix PR #169",
            pr_branch="story-169/fix-auth",
            base_story_id="STORY-169",
            rework_of="STORY-169",
        )

        assert mock_session.post.call_count == 2
        retry_call = mock_session.post.call_args_list[1]
        body = retry_call.kwargs.get("json") or retry_call[1].get("json", {})
        assert body.get("pr_branch") == "story-169/fix-auth"
        assert body.get("base_story_id") == "STORY-169"
        assert body.get("rework_of") == "STORY-169"
        assert body.get("prompt", "").startswith("[RETRY 1/3]")

    def test_retry_strips_stacked_prefix(self):
        """RW-05: a second retry does not stack [RETRY 1/3][RETRY 2/3] prefixes."""
        from deployment.hermes.dispatch_poller import _report_fail

        mock_session = MagicMock()
        fail_resp = MagicMock()
        fail_resp.status_code = 200
        retry_resp = MagicMock()
        retry_resp.status_code = 201
        mock_session.post.side_effect = [fail_resp, retry_resp]

        _report_fail(
            session=mock_session,
            base_url="http://ops:8000",
            api_key="key",
            story_id="STORY-570",
            exit_code=1,
            repo="tech-dev-agents",
            scope="small",
            prompt="[RETRY 1/3] Fix PR #169",  # already has prefix
        )

        retry_call = mock_session.post.call_args_list[1]
        body = retry_call.kwargs.get("json") or retry_call[1].get("json", {})
        prompt = body.get("prompt", "")
        # Should be "[RETRY 2/3] Fix PR #169", not "[RETRY 2/3] [RETRY 1/3] Fix PR #169"
        assert prompt == "[RETRY 2/3] Fix PR #169"


class TestSdkExitCode:
    """SDK-01 to SDK-02: claude_sdk_tool exits non-zero on is_error."""

    def test_run_returns_true_on_is_error(self):
        """SDK-01: run() returns True when SDK result has is_error=True."""
        import asyncio
        import sys
        sys.path.insert(0, "/home/hermes/dev/hpi-gorillacommerce/tech-dev-agents/deployment/vm")

        # Build a fake SDK result message
        result_msg = MagicMock()
        result_msg.type = "result"
        result_msg.total_cost_usd = 0.01
        result_msg.is_error = True
        result_msg.stop_reason = "error"
        result_msg.num_turns = 1
        result_msg.duration_ms = 1000

        async def fake_query(*a, **kw):
            yield result_msg

        with patch.dict("sys.modules", {"claude_agent_sdk": MagicMock(
            query=fake_query,
            ClaudeAgentOptions=MagicMock(return_value=MagicMock()),
            PermissionResultAllow=MagicMock,
            PermissionResultDeny=MagicMock,
        )}):
            # Re-import to pick up mock
            import importlib
            import deployment.vm.claude_sdk_tool as sdk_mod
            importlib.reload(sdk_mod)

            had_error = asyncio.run(sdk_mod.run("test prompt", "/tmp", 0))

        assert had_error is True

    def test_run_returns_false_on_clean_session(self):
        """SDK-02: run() returns False when SDK result has is_error=False."""
        import asyncio

        result_msg = MagicMock()
        result_msg.type = "result"
        result_msg.total_cost_usd = 0.05
        result_msg.is_error = False
        result_msg.stop_reason = "end_turn"
        result_msg.num_turns = 3
        result_msg.duration_ms = 5000

        async def fake_query(*a, **kw):
            yield result_msg

        with patch.dict("sys.modules", {"claude_agent_sdk": MagicMock(
            query=fake_query,
            ClaudeAgentOptions=MagicMock(return_value=MagicMock()),
            PermissionResultAllow=MagicMock,
            PermissionResultDeny=MagicMock,
        )}):
            import importlib
            import deployment.vm.claude_sdk_tool as sdk_mod
            importlib.reload(sdk_mod)

            had_error = asyncio.run(sdk_mod.run("test prompt", "/tmp", 0))

        assert had_error is False
