"""Regression tests for the rate-limit death loop.

On 2026-04-24 19:33Z, Daisy was rate-limited but the poller still claimed
5 stories (304, 306, 307, 308, 309) in 5 minutes. Each SDK run exited in
1-3 seconds with "You've hit your limit", was marked failed, and the next
claim fired on the next 60 s tick.

Root cause had two parts:

  A. ``claude_sdk_tool.py:main()`` did ``asyncio.run(run(...))`` without
     ever inspecting the result — so even when the SDK produced
     ``is_error=True`` (rate limit), the process exited rc=0 and the
     poller's rc-level error detection was useless.

  B. ``dispatch_poller.poll_once()`` trusted ``poll_loop()`` to gate on
     the pause flag. But ``_run_and_complete`` runs in a daemon thread,
     and the flag is written AFTER the SDK exits. On the race window
     between the next main-loop tick and the flag write,
     ``poll_once`` would claim a new story with the agent still
     rate-limited.

These tests pin the behavioral fix at call-site level:
  - SDK tool exits non-zero on ``is_error=True``.
  - ``poll_once`` re-checks the pause flag before issuing ``/dispatch/next``.
  - The phase runner does NOT call ``systemctl disable`` (STORY-538 said
     remove; the grep test only covered dispatch_poller.py and the call
     survived inside sdlc_phase_runner.py).
"""

from __future__ import annotations

import inspect
import os
import sys
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Part A — claude_sdk_tool.py exits non-zero on rate-limit / is_error
# ---------------------------------------------------------------------------


def _import_sdk_tool():
    """Import claude_sdk_tool with claude_agent_sdk stubbed.

    The test environment has no claude_agent_sdk wheel. The module only
    uses it at module-load time, so a MagicMock is enough. We load the
    module by file path via importlib so we don't mutate ``sys.path`` —
    inserting the ``deployment/vm`` directory at the front of sys.path
    shadows the ``deployment`` package and breaks other tests that do
    ``from deployment.hermes import dispatch_poller``.
    """
    if "claude_agent_sdk" not in sys.modules:
        sys.modules["claude_agent_sdk"] = MagicMock()
    if "claude_sdk_tool" in sys.modules:
        return sys.modules["claude_sdk_tool"]
    import importlib.util
    sdk_path = os.path.normpath(os.path.join(
        os.path.dirname(__file__), "..", "..", "deployment", "vm", "claude_sdk_tool.py"
    ))
    spec = importlib.util.spec_from_file_location("claude_sdk_tool", sdk_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["claude_sdk_tool"] = mod
    spec.loader.exec_module(mod)
    return mod


class TestSdkToolPropagatesErrorExitCode:
    """Part A: ``main()`` must exit non-zero when ``run()`` reports an error."""

    def test_main_exits_nonzero_when_run_returns_true(self):
        """A1: ``asyncio.run(run(...))`` returning True → ``sys.exit(1)``.

        This is the fix for the rc=0-on-rate-limit bug. If this test
        fails, the poller cannot detect rate limits via exit code and
        the death loop is a race on text-grep of session logs.
        """
        sdk = _import_sdk_tool()

        with patch.object(sys, "argv", ["claude_sdk_tool", "-p", "noop"]), \
             patch.object(sdk, "asyncio") as mock_asyncio:
            mock_asyncio.run.return_value = True  # had_error=True
            with pytest.raises(SystemExit) as exc_info:
                sdk.main()
        assert exc_info.value.code == 1, (
            f"main() must sys.exit(1) when run() returns True (had_error), "
            f"got exit code {exc_info.value.code}"
        )

    @pytest.mark.filterwarnings("ignore:coroutine .* was never awaited:RuntimeWarning")
    def test_main_exits_zero_when_run_returns_false(self):
        """A2: Clean run → no non-zero exit. Either normal return or
        SystemExit(0). The contract is: rc=0 iff no error detected.
        """
        sdk = _import_sdk_tool()

        with patch.object(sys, "argv", ["claude_sdk_tool", "-p", "noop"]), \
             patch.object(sdk, "asyncio") as mock_asyncio:
            mock_asyncio.run.return_value = False  # no error
            try:
                sdk.main()
            except SystemExit as exc:
                assert exc.code in (None, 0), (
                    f"main() must not exit non-zero on a clean run, got {exc.code}"
                )

    def test_run_coroutine_returns_bool(self):
        """A3: Source-level check that ``run()`` is annotated to return bool.
        Without this, main() can't branch on the return value meaningfully.
        """
        sdk = _import_sdk_tool()
        src = inspect.getsource(sdk.run)
        assert "-> bool" in src.splitlines()[0] or "had_error" in src, (
            "run() must return bool (had_error) so main() can exit non-zero. "
            "Current signature missing '-> bool' annotation and 'had_error' not referenced."
        )

    def test_run_sets_had_error_on_is_error_message(self):
        """A4: The had_error path must trigger when a result message has
        ``is_error=True``. Source-level assertion because async test of
        real SDK iteration requires a running loop + fake SDK wheel.
        """
        sdk = _import_sdk_tool()
        src = inspect.getsource(sdk.run)
        assert "is_error" in src and "had_error" in src, (
            "run() must set had_error=True when is_error is observed on a "
            "result message. Either the is_error read is gone or had_error "
            "is not assigned."
        )


# ---------------------------------------------------------------------------
# Part B — poll_once re-checks pause flag before claiming
# ---------------------------------------------------------------------------


class TestPollOncePauseFlagRecheck:
    """Part B: ``poll_once`` must return without network calls when
    ``/var/run/dispatch-poller-paused-until`` exists and is not expired.
    Prior to this fix, only ``poll_loop`` gated on the flag; the daemon
    thread could write it AFTER ``poll_loop``'s check and before the
    main thread's next ``poll_once`` call — the race that let 5 stories
    burn in 5 minutes.
    """

    def _setup_poll_once(self, session_mock):
        """Common patches to let poll_once reach the pause check."""
        from deployment.hermes import dispatch_poller
        # agent is idle (ready to claim)
        idle_patch = patch.object(dispatch_poller, "is_agent_idle", return_value=True)
        return idle_patch

    def test_poll_once_returns_paused_when_flag_exists(self, tmp_path, monkeypatch):
        """B1: poll_once sees active pause flag → returns 'paused',
        makes ZERO HTTP calls.
        """
        from deployment.hermes import dispatch_poller

        pause_file = tmp_path / "dispatch-poller-paused-until"
        # Write a future reset time so pause is clearly still active
        pause_file.write_text("23:59 (UTC)")

        session_mock = MagicMock()
        with self._setup_poll_once(session_mock), \
             patch.object(dispatch_poller, "PAUSE_FLAG_PATH", str(pause_file), create=True):
            result = dispatch_poller.poll_once(
                session=session_mock,
                base_url="http://ops:8000",
                api_key="test-key",
                agent_name="daisy",
                workspace="/tmp/ws",
            )

        assert result == "paused", (
            f"poll_once must return 'paused' when pause flag exists, got {result!r}"
        )
        session_mock.get.assert_not_called()
        session_mock.post.assert_not_called()

    def test_poll_once_proceeds_when_flag_absent(self, tmp_path, monkeypatch):
        """B2: No pause flag → poll_once proceeds to /dispatch/next as usual."""
        from deployment.hermes import dispatch_poller

        pause_file = tmp_path / "does-not-exist"  # deliberately missing
        session_mock = MagicMock()
        session_mock.get.return_value = MagicMock(status_code=204)  # empty queue

        with self._setup_poll_once(session_mock), \
             patch.object(dispatch_poller, "PAUSE_FLAG_PATH", str(pause_file), create=True):
            result = dispatch_poller.poll_once(
                session=session_mock,
                base_url="http://ops:8000",
                api_key="test-key",
                agent_name="daisy",
                workspace="/tmp/ws",
            )

        assert result == "empty"
        session_mock.get.assert_called_once()  # hit /dispatch/next

    def test_poll_once_clears_stale_flag_beyond_1h_cap(self, tmp_path):
        """B3: unparseable reset + flag older than 1h safety cap → poll_once
        clears the flag and proceeds. Matches poll_loop's auto-clear so the
        agent doesn't sit idle forever if the reset string garbled.
        """
        from deployment.hermes import dispatch_poller

        pause_file = tmp_path / "dispatch-poller-paused-until"
        pause_file.write_text("gibberish-not-a-time")
        # Backdate the file beyond the 1h safety cap
        ancient = 0  # epoch
        os.utime(pause_file, (ancient, ancient))

        session_mock = MagicMock()
        session_mock.get.return_value = MagicMock(status_code=204)

        with self._setup_poll_once(session_mock), \
             patch.object(dispatch_poller, "PAUSE_FLAG_PATH", str(pause_file), create=True):
            result = dispatch_poller.poll_once(
                session=session_mock,
                base_url="http://ops:8000",
                api_key="test-key",
                agent_name="daisy",
                workspace="/tmp/ws",
            )

        assert not pause_file.exists(), "Stale pause flag should be cleared by poll_once"
        assert result == "empty"
        session_mock.get.assert_called_once()


# ---------------------------------------------------------------------------
# Part C — sdlc_phase_runner must NOT call systemctl disable
# ---------------------------------------------------------------------------


class TestNoSystemctlDisableInPhaseRunner:
    """Part C: STORY-538 said remove the ``systemctl disable --now
    dispatch-poller`` one-way-trip. The grep test only covered
    ``dispatch_poller.py`` — the call lived on in ``sdlc_phase_runner.py``
    and would leave the service disabled long after the rate limit cleared.
    """

    def test_phase_runner_has_no_active_systemctl_disable_call(self):
        """Match subprocess.run(['sudo','systemctl','disable',...]) etc.
        Comments referencing the (removed) behavior are fine.
        """
        import re as _re
        from deployment.hermes import sdlc_phase_runner

        src = inspect.getsource(sdlc_phase_runner)
        # Scan only executable lines, not comments
        code_lines = [
            ln for ln in src.splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]
        code_only = "\n".join(code_lines)
        # Active call patterns we forbid
        forbidden = _re.compile(
            r"""(subprocess\.(run|Popen|call|check_output)\s*\(\s*\[[^\]]*systemctl[^\]]*disable
                |os\.system\s*\(\s*["'][^"']*systemctl[^"']*disable)""",
            _re.VERBOSE,
        )
        matches = forbidden.findall(code_only)
        assert not matches, (
            f"sdlc_phase_runner.py still has an active systemctl disable call: "
            f"{matches}. The pause flag is the single source of truth; disabling "
            "the service is a one-way trip that leaves agents dead past the "
            "reset window."
        )

    def test_phase_runner_has_no_sudo_systemctl_disable_list(self):
        from deployment.hermes import sdlc_phase_runner

        src = inspect.getsource(sdlc_phase_runner)
        assert '"sudo", "systemctl", "disable"' not in src, (
            "sdlc_phase_runner.py contains the forbidden "
            '["sudo","systemctl","disable",...] subprocess pattern'
        )
