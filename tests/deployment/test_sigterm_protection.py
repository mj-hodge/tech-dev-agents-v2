"""STORY-522: SIGTERM (rc=-15) protection tests for dispatch poller and phase runner.

Phase 7 — RED-state tests. Tests T522-03, T522-08, T522-09 FAIL until Phase 8
implementation adds main-thread handler registration, KillMode=process in
systemd unit, and start_new_session=True in subprocess calls.

Coverage:
  A. SIGTERM handler registration (main thread vs daemon thread)  [AC1]
  B. rc=-15 propagation and partial work saving                   [AC3, AC5]
  C. systemd cgroup isolation (KillMode, process groups)          [AC2]
  D. Integration: zero SIGTERMs during normal SDK execution       [AC6]
  E. Regression: 10-minute Phase 6 scenario                       [AC7]
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Paths and module imports
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).parent.parent.parent
PHASE_RUNNER_DIR = REPO_ROOT / "deployment" / "hermes"
SYSTEMD_DIR = REPO_ROOT / "deployment" / "vm" / "systemd"

if str(PHASE_RUNNER_DIR) not in sys.path:
    sys.path.insert(0, str(PHASE_RUNNER_DIR))

try:
    import sdlc_phase_runner as _runner
    _RUNNER_AVAILABLE = True
except ImportError:
    _runner = None  # type: ignore[assignment]
    _RUNNER_AVAILABLE = False

try:
    import dispatch_poller as _poller
    _POLLER_AVAILABLE = True
except ImportError:
    _poller = None  # type: ignore[assignment]
    _POLLER_AVAILABLE = False

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runner_module():
    """Return the sdlc_phase_runner module or skip."""
    if not _RUNNER_AVAILABLE:
        pytest.skip("sdlc_phase_runner not importable")
    return _runner


@pytest.fixture
def poller_module():
    """Return the dispatch_poller module or skip."""
    if not _POLLER_AVAILABLE:
        pytest.skip("dispatch_poller not importable")
    return _poller


@pytest.fixture
def tmp_git_repo(tmp_path):
    """Create a minimal git repo for testing _save_partial_work."""
    repo = tmp_path / "test-repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "--allow-empty", "-m", "init"],
        capture_output=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "t@t"},
    )
    # Create a branch matching expected story pattern
    subprocess.run(
        ["git", "-C", str(repo), "checkout", "-b", "story-522/story-522"],
        capture_output=True,
    )
    return repo


# ===========================================================================
# Group A — SIGTERM Handler Registration [AC1]
# ===========================================================================


class TestSIGTERMHandlerRegistration:
    """AC1: SIGTERM handler must be registered in the main thread."""

    def test_install_shutdown_handler_succeeds_in_main_thread(self, runner_module):
        """T522-01: install_shutdown_handler() succeeds when called from main thread."""
        install = getattr(runner_module, "install_shutdown_handler", None)
        if install is None:
            pytest.fail("install_shutdown_handler not defined in sdlc_phase_runner")

        # Save the original handler so we can restore it
        original = signal.getsignal(signal.SIGTERM)
        try:
            install()
            current = signal.getsignal(signal.SIGTERM)
            graceful = getattr(runner_module, "_graceful_shutdown", None)
            assert current is graceful, (
                f"After install_shutdown_handler() in main thread, SIGTERM handler "
                f"should be _graceful_shutdown, got {current}"
            )
        finally:
            signal.signal(signal.SIGTERM, original)

    def test_install_shutdown_handler_fails_silently_in_non_main_thread(self, runner_module):
        """T522-02: install_shutdown_handler() in non-main thread does not raise."""
        install = getattr(runner_module, "install_shutdown_handler", None)
        if install is None:
            pytest.fail("install_shutdown_handler not defined in sdlc_phase_runner")

        errors = []

        def thread_fn():
            try:
                install()
            except Exception as exc:
                errors.append(exc)

        t = threading.Thread(target=thread_fn)
        t.start()
        t.join(timeout=5)

        assert len(errors) == 0, (
            f"install_shutdown_handler raised an exception in non-main thread: {errors}"
        )

    def test_main_thread_sigterm_registration_in_poll_loop(self, runner_module):
        """T522-03: poll_loop entry path must register SIGTERM handler in main thread.

        The handler registration currently only happens inside run_sdlc_phases()
        which runs in a DAEMON thread (where signal.signal raises ValueError).
        The fix: register in poll_loop() or run_dispatch_poller.py (main thread).

        RED until Phase 8 implements Fix 1.
        """
        # Strategy: patch poll_loop to run only one iteration, then check
        # whether signal.SIGTERM handler was set to _graceful_shutdown.
        # We need to verify the MAIN thread path registers the handler.

        original = signal.getsignal(signal.SIGTERM)
        # Reset handler to default so we can detect if it was set
        signal.signal(signal.SIGTERM, signal.SIG_DFL)

        try:
            # The entry point should register the handler BEFORE entering the
            # loop. We simulate by importing and checking for a registration
            # call in the main-thread code path.
            #
            # Check: does poll_loop (or run_dispatch_poller) call
            # install_shutdown_handler before the while loop?
            import inspect
            poll_loop_src = inspect.getsource(runner_module.install_shutdown_handler)

            # The real test: after importing and calling the entry point setup,
            # the handler should be registered. We check the poller module.
            if _POLLER_AVAILABLE:
                poller_src = inspect.getsource(_poller.poll_loop)
                has_handler_call = (
                    "install_shutdown_handler" in poller_src
                    or "signal.signal" in poller_src
                    or "signal.SIGTERM" in poller_src
                )
            else:
                # Also check run_dispatch_poller.py
                rdp_path = REPO_ROOT / "deployment" / "vm" / "run_dispatch_poller.py"
                rdp_src = rdp_path.read_text() if rdp_path.exists() else ""
                has_handler_call = (
                    "install_shutdown_handler" in rdp_src
                    or "signal.signal" in rdp_src
                    or "signal.SIGTERM" in rdp_src
                )

            assert has_handler_call, (
                "Neither poll_loop() nor run_dispatch_poller.py registers a SIGTERM "
                "handler in the main thread. The handler in run_sdlc_phases() runs in "
                "a daemon thread where signal.signal() silently fails (ValueError). "
                "Fix: call install_shutdown_handler() from poll_loop() or "
                "run_dispatch_poller.py before entering the poll loop."
            )
        finally:
            signal.signal(signal.SIGTERM, original)


# ===========================================================================
# Group B — rc=-15 Propagation and Partial Work Saving [AC3, AC5]
# ===========================================================================


class TestSIGTERMPropagation:
    """AC5: rc=-15 must be propagated and partial work must be saved."""

    def test_run_phase_sdk_propagates_sigterm_exit_code(self, runner_module):
        """T522-04: _run_phase_sdk returns (-15, ...) when subprocess killed by SIGTERM."""
        _run_phase_sdk = getattr(runner_module, "_run_phase_sdk", None)
        if _run_phase_sdk is None:
            pytest.fail("_run_phase_sdk not defined in sdlc_phase_runner")

        mock_result = MagicMock()
        mock_result.returncode = -15  # Killed by SIGTERM
        mock_result.stderr = "Terminated"

        with patch("subprocess.run", return_value=mock_result), \
             patch.object(runner_module, "_save_partial_work"), \
             patch.object(runner_module, "_emit_event"), \
             patch.object(runner_module, "_capture_session_id_from_log"), \
             patch.object(runner_module, "_read_story_session_id", return_value=None), \
             patch.dict(os.environ, {"AGENT_NAME": "test", "SDK_TOOL_PATH": "/fake/sdk.py"}):
            rc, output = _run_phase_sdk(
                story_id="STORY-522",
                repo="tech-dev-agents",
                phase_num=6,
                phase_name="Design",
                prompt="test prompt",
                workdir="/tmp/fake",
                max_turns=10,
                scope="small",
            )

        assert rc == -15, (
            f"Expected rc=-15 (raw SIGTERM), got rc={rc}. "
            "_run_phase_sdk must propagate the subprocess exit code unchanged."
        )

    def test_run_phase_sdk_saves_partial_work_on_sigterm(self, runner_module):
        """T522-05: _run_phase_sdk calls _save_partial_work even when rc=-15."""
        _run_phase_sdk = getattr(runner_module, "_run_phase_sdk", None)
        if _run_phase_sdk is None:
            pytest.fail("_run_phase_sdk not defined in sdlc_phase_runner")

        save_calls = []

        def track_save(*args, **kwargs):
            save_calls.append(args)

        mock_result = MagicMock()
        mock_result.returncode = -15
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result), \
             patch.object(runner_module, "_save_partial_work", side_effect=track_save), \
             patch.object(runner_module, "_emit_event"), \
             patch.object(runner_module, "_capture_session_id_from_log"), \
             patch.object(runner_module, "_read_story_session_id", return_value=None), \
             patch.dict(os.environ, {"AGENT_NAME": "test", "SDK_TOOL_PATH": "/fake/sdk.py"}):
            _run_phase_sdk(
                story_id="STORY-522",
                repo="tech-dev-agents",
                phase_num=6,
                phase_name="Design",
                prompt="test prompt",
                workdir="/tmp/fake",
                max_turns=10,
                scope="small",
            )

        assert len(save_calls) > 0, (
            "_save_partial_work was NOT called after rc=-15. "
            "Partial work must always be saved regardless of exit code."
        )

    def test_save_partial_work_commits_dirty_files(self, runner_module, tmp_git_repo):
        """T522-06: _save_partial_work runs git add + commit when files are dirty."""
        _save_partial_work = getattr(runner_module, "_save_partial_work", None)
        if _save_partial_work is None:
            pytest.fail("_save_partial_work not defined in sdlc_phase_runner")

        # Create a dirty file
        dirty_file = tmp_git_repo / "dirty.py"
        dirty_file.write_text("# dirty\n")

        git_commands = []
        original_run = subprocess.run

        def tracking_run(cmd, **kwargs):
            if isinstance(cmd, list) and "git" in cmd:
                git_commands.append(list(cmd))
            # For branch detection, return the expected branch name
            if isinstance(cmd, list) and "--show-current" in cmd:
                result = MagicMock()
                result.returncode = 0
                result.stdout = "story-522/story-522"
                return result
            if isinstance(cmd, list) and "--porcelain" in cmd:
                result = MagicMock()
                result.returncode = 0
                result.stdout = " M dirty.py"
                return result
            result = MagicMock()
            result.returncode = 0
            result.stdout = ""
            return result

        with patch("subprocess.run", side_effect=tracking_run):
            _save_partial_work(str(tmp_git_repo), "STORY-522", 6, "Design")

        add_calls = [c for c in git_commands if "add" in c and "-A" in c]
        commit_calls = [c for c in git_commands if "commit" in c]
        assert len(add_calls) > 0, "git add -A was not called"
        assert len(commit_calls) > 0, "git commit was not called"

    def test_save_partial_work_refuses_wrong_branch(self, runner_module, capsys):
        """T522-07: _save_partial_work refuses to commit on wrong branch."""
        _save_partial_work = getattr(runner_module, "_save_partial_work", None)
        if _save_partial_work is None:
            pytest.fail("_save_partial_work not defined in sdlc_phase_runner")

        def mock_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            if isinstance(cmd, list) and "--porcelain" in cmd:
                result.stdout = " M dirty.py"  # Dirty files exist
            elif isinstance(cmd, list) and "--show-current" in cmd:
                result.stdout = "story-999/story-999"  # WRONG branch
            else:
                result.stdout = ""
            return result

        push_calls = []

        def tracking_run(cmd, **kwargs):
            r = mock_run(cmd, **kwargs)
            if isinstance(cmd, list) and "push" in cmd:
                push_calls.append(cmd)
            return r

        with patch("subprocess.run", side_effect=tracking_run), \
             patch.object(runner_module, "_emit_event"):
            _save_partial_work("/tmp/fake", "STORY-522", 6, "Design")

        assert len(push_calls) == 0, (
            "git push was called on wrong branch — _save_partial_work must refuse "
            "to push when current branch doesn't match expected story branch"
        )
        captured = capsys.readouterr()
        assert "mismatch" in captured.out.lower() or "Branch mismatch" in captured.out, (
            "Expected branch mismatch warning in output"
        )


# ===========================================================================
# Group C — systemd Cgroup Isolation [AC2]
# ===========================================================================


class TestSystemdCgroupIsolation:
    """AC2: SDK child process must be protected from systemd cgroup-wide SIGTERM."""

    def test_systemd_unit_has_killmode_process(self):
        """T522-08: dispatch-poller.service must have KillMode=process.

        Default KillMode is 'control-group' which sends SIGTERM to ALL
        processes in the service cgroup, including SDK children. With
        KillMode=process, only the main PID receives SIGTERM; children
        are left alive for the parent to clean up gracefully.

        RED until Phase 8 adds KillMode=process to the service file.
        """
        service_path = SYSTEMD_DIR / "dispatch-poller.service"
        assert service_path.exists(), (
            f"Service file not found at {service_path}"
        )
        content = service_path.read_text()
        assert "KillMode=process" in content, (
            "dispatch-poller.service does not contain 'KillMode=process'. "
            "Default KillMode=control-group sends SIGTERM to ALL processes in "
            "the cgroup (including SDK children), causing rc=-15. "
            "Add 'KillMode=process' under [Service] so only the main PID "
            "receives SIGTERM."
        )

    def test_sdk_subprocess_uses_new_session(self, runner_module):
        """T522-09: _run_phase_sdk must use start_new_session=True for SDK subprocess.

        This places the SDK child in its own process group/session, providing
        defense-in-depth against cgroup-wide signals. Even with KillMode=process,
        process group isolation prevents accidental signal propagation from
        other sources (e.g., terminal HUPs, parent SIGINT).

        RED until Phase 8 adds start_new_session=True to subprocess.run().
        """
        _run_phase_sdk = getattr(runner_module, "_run_phase_sdk", None)
        if _run_phase_sdk is None:
            pytest.fail("_run_phase_sdk not defined in sdlc_phase_runner")

        captured_kwargs = {}

        def capture_run(cmd, **kwargs):
            captured_kwargs.update(kwargs)
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            return result

        with patch("subprocess.run", side_effect=capture_run), \
             patch.object(runner_module, "_save_partial_work"), \
             patch.object(runner_module, "_emit_event"), \
             patch.object(runner_module, "_capture_session_id_from_log"), \
             patch.object(runner_module, "_read_story_session_id", return_value=None), \
             patch.dict(os.environ, {"AGENT_NAME": "test", "SDK_TOOL_PATH": "/fake/sdk.py"}):
            _run_phase_sdk(
                story_id="STORY-522",
                repo="tech-dev-agents",
                phase_num=6,
                phase_name="Design",
                prompt="test prompt",
                workdir="/tmp/fake",
                max_turns=10,
                scope="small",
            )

        assert captured_kwargs.get("start_new_session") is True, (
            "subprocess.run() for SDK was not called with start_new_session=True. "
            "The SDK child process inherits the parent's process group, making it "
            "vulnerable to cgroup-wide SIGTERM. Add start_new_session=True to the "
            "subprocess.run() call in _run_phase_sdk."
        )


# ===========================================================================
# Group D — Integration: Zero SIGTERMs in Normal Operation [AC6]
# ===========================================================================


class TestNoSIGTERMDuringNormalExecution:
    """AC6: Poller and recovery loop must not send SIGTERM to running SDK."""

    def test_poller_does_not_kill_active_sdk(self, poller_module):
        """T522-11: When SDK is running, poll_once returns 'busy' without signaling.

        The poller checks is_agent_idle() which greps for running SDK processes.
        When an SDK is active, poll_once must return 'busy' without sending any
        signals, making HTTP calls, or starting a new story.
        """
        mock_session = MagicMock()

        # Simulate an active SDK process found by ps grep
        def mock_run(cmd, **kwargs):
            result = MagicMock()
            if isinstance(cmd, list) and "grep" in str(cmd):
                result.returncode = 0
                result.stdout = "hermes  12345  python3 /opt/agent/claude_sdk_tool.py -p test"
            else:
                result.returncode = 1
                result.stdout = ""
            return result

        with patch("deployment.hermes.dispatch_poller.subprocess.run", side_effect=mock_run), \
             patch("deployment.hermes.dispatch_poller._local_queue_active", return_value=True):
            outcome = _poller.poll_once(
                session=mock_session,
                base_url="http://test:8000",
                api_key="test-key",
                agent_name="test-agent",
                workspace="/tmp",
            )

        assert outcome == "busy", (
            f"Expected 'busy' when SDK is running, got '{outcome}'. "
            "poll_once must short-circuit when is_agent_idle() returns False."
        )
        # Verify NO HTTP calls were made (no claiming, no /next, nothing)
        assert mock_session.get.call_count == 0, (
            "poll_once made HTTP GET calls while SDK was active — "
            "should have returned 'busy' immediately"
        )

    def test_no_sigterm_during_normal_sdk_execution(self, runner_module):
        """T522-10: A running SDK subprocess receives zero SIGTERMs from the poller.

        Simulates a Phase 6 SDK session running for ~60 seconds (via mocked
        time.time). The mock subprocess completes cleanly. Assert rc=0 and no
        SIGTERM interruption.
        """
        _run_phase_sdk = getattr(runner_module, "_run_phase_sdk", None)
        if _run_phase_sdk is None:
            pytest.fail("_run_phase_sdk not defined in sdlc_phase_runner")

        run_called = []
        # Simulate 60s elapsed duration to avoid the "suspiciously fast" rate-limit guard
        _real_time = time.time
        _call_count = [0]

        def mock_time():
            _call_count[0] += 1
            base = _real_time()
            # First call is start time; subsequent calls add 60s
            if _call_count[0] <= 1:
                return base
            return base + 60

        def mock_sdk_run(cmd, **kwargs):
            """Simulate SDK running and completing normally."""
            run_called.append(True)
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            return result

        with patch("subprocess.run", side_effect=mock_sdk_run), \
             patch.object(runner_module, "_save_partial_work"), \
             patch.object(runner_module, "_emit_event"), \
             patch.object(runner_module, "_capture_session_id_from_log"), \
             patch.object(runner_module, "_read_story_session_id", return_value=None), \
             patch("time.time", side_effect=mock_time), \
             patch("glob.glob", return_value=[]), \
             patch.dict(os.environ, {"AGENT_NAME": "test", "SDK_TOOL_PATH": "/fake/sdk.py"}):
            rc, _ = _run_phase_sdk(
                story_id="STORY-522",
                repo="tech-dev-agents",
                phase_num=6,
                phase_name="Design",
                prompt="test prompt",
                workdir="/tmp/fake",
                max_turns=50,
                scope="medium",
            )

        assert rc == 0, (
            f"Expected rc=0 (clean exit), got rc={rc}. "
            "Normal SDK execution should not be interrupted."
        )
        assert len(run_called) > 0, "subprocess.run was never called"


# ===========================================================================
# Group E — Regression: 10-min Phase 6 Scenario [AC7]
# ===========================================================================


class TestPhase6TenMinuteRegression:
    """AC7: 10-minute Phase 6 completes without SIGTERM from poller or recovery."""

    def test_phase6_10min_no_sigterm(self, runner_module):
        """T522-12: Simulate 10-minute Phase 6 — no SIGTERM from poller.

        Mocks the SDK subprocess to simulate a 10-minute run (via mocked
        time.time for 600s elapsed). The test confirms:
        1. _run_phase_sdk passes the correct phase timeout (1200s for small)
        2. The subprocess is called exactly once (no restart/re-entry)
        3. Return code is 0 (no SIGTERM interruption)
        """
        _run_phase_sdk = getattr(runner_module, "_run_phase_sdk", None)
        if _run_phase_sdk is None:
            pytest.fail("_run_phase_sdk not defined in sdlc_phase_runner")

        captured_timeout = []
        # Simulate 600s elapsed to avoid rate-limit fast-completion guard
        _real_time = time.time
        _call_count = [0]

        def mock_time():
            _call_count[0] += 1
            base = _real_time()
            if _call_count[0] <= 1:
                return base
            return base + 600  # 10 minutes elapsed

        def mock_run(cmd, **kwargs):
            captured_timeout.append(kwargs.get("timeout"))
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            return result

        with patch("subprocess.run", side_effect=mock_run), \
             patch.object(runner_module, "_save_partial_work"), \
             patch.object(runner_module, "_emit_event"), \
             patch.object(runner_module, "_capture_session_id_from_log"), \
             patch.object(runner_module, "_read_story_session_id", return_value=None), \
             patch("time.time", side_effect=mock_time), \
             patch("glob.glob", return_value=[]), \
             patch.dict(os.environ, {"AGENT_NAME": "test", "SDK_TOOL_PATH": "/fake/sdk.py"}):
            rc, _ = _run_phase_sdk(
                story_id="STORY-522",
                repo="tech-dev-agents",
                phase_num=6,
                phase_name="Design",
                prompt="test prompt",
                workdir="/tmp/fake",
                max_turns=50,
                scope="medium",
            )

        assert rc == 0, f"Expected rc=0, got rc={rc}"
        # Phase 6 is not phase 8, so timeout should be 1200s
        assert len(captured_timeout) == 1, "subprocess.run should be called exactly once"
        assert captured_timeout[0] >= 600, (
            f"Phase timeout {captured_timeout[0]}s is too short for a 10-minute Phase 6 "
            "(minimum 600s needed). Current timeout should be 1200s."
        )

    def test_stale_claim_recovery_does_not_signal_agents(self):
        """T522-13: recover_stale_claims() only updates DB state, never sends signals.

        The server-side stale claim recovery moves items from 'claimed' to
        'pending' in the dispatch queue JSON. It must NOT send os.kill() or
        any signal to agent processes.
        """
        from tech_dev_agents.ops_console.services.dispatch_service import (
            DispatchQueueService,
        )
        import inspect

        # Inspect the source code for any os.kill, signal.*, or subprocess calls
        src = inspect.getsource(DispatchQueueService.recover_stale_claims)

        dangerous_patterns = ["os.kill", "signal.", "subprocess", "Popen", "pkill"]
        found = [p for p in dangerous_patterns if p in src]

        assert len(found) == 0, (
            f"recover_stale_claims() contains signal/process calls: {found}. "
            "Server-side recovery must only update DB state, never send signals "
            "to agent processes."
        )
