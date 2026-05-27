"""Regression tests for claude_sdk_tool._exec_bash timeout/process-group path.

Background: on 2026-05-25 the fleet wedged because the 120s timeout in
``_exec_bash`` only SIGKILLed the bash leader and left pytest descendants
running as orphans. The model retried and stacked 8-10 zombie trees per
agent (5+ GB RAM).

A first fix added ``start_new_session=True`` + ``killpg``, but used
``subprocess.TimeoutExpired.pid`` — an attribute that does not exist on
that exception, so the timeout handler could raise ``AttributeError``
(Codex adversarial review 2026-05-26).

This file pins the expected behaviour:

1. A command that completes quickly returns its captured stdout/stderr.
2. A command that exceeds 120s timeout returns the bounded
   ``[ERROR] ...`` string with no exception escaping the call.
3. On timeout, descendants of the bash leader are reaped (no zombie
   children, no orphan processes outliving the call).
"""

from __future__ import annotations

import importlib.util
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "deployment" / "vm" / "claude_sdk_tool.py"


@pytest.fixture(scope="module")
def claude_sdk_tool():
    """Load the deployment/vm/claude_sdk_tool.py module dynamically.

    The file lives outside the importable package tree on purpose
    (deployed to the agent VMs via push-code.sh). Tests import it by
    file path to avoid changing PYTHONPATH for the rest of the suite.
    """
    spec = importlib.util.spec_from_file_location(
        "claude_sdk_tool_under_test", MODULE_PATH
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["claude_sdk_tool_under_test"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop("claude_sdk_tool_under_test", None)


# -- happy path ------------------------------------------------------------


def test_exec_bash_returns_stdout(tmp_path, claude_sdk_tool):
    """A fast command returns its stdout cleanly."""
    out = claude_sdk_tool._exec_bash({"command": "echo hello-watchdog"}, str(tmp_path))
    assert "hello-watchdog" in out


def test_exec_bash_captures_stderr_with_marker(tmp_path, claude_sdk_tool):
    """Stderr is appended under a [STDERR] marker, not lost."""
    out = claude_sdk_tool._exec_bash(
        {"command": "echo ok && echo bad 1>&2"}, str(tmp_path)
    )
    assert "ok" in out
    assert "[STDERR]" in out
    assert "bad" in out


# -- timeout path ----------------------------------------------------------


def test_exec_bash_timeout_returns_error_string_not_exception(
    tmp_path, claude_sdk_tool, monkeypatch
):
    """The 120s timeout returns the [ERROR] marker without raising.

    Patches the module-level subprocess.communicate to fire
    TimeoutExpired immediately so the test takes <2s instead of 120s.
    The point is to exercise the *handler*, not the timer.
    """
    real_popen = claude_sdk_tool.subprocess.Popen

    class _ImmediateTimeoutPopen:
        """Wrapper that raises TimeoutExpired on the first communicate()."""

        def __init__(self, *args, **kwargs):
            # Use a real Popen so .pid / killpg paths are exercised; the
            # underlying command is short-lived but communicate is
            # short-circuited to raise before reading output.
            self._inner = real_popen(*args, **kwargs)
            self._timeout_raised = False

        @property
        def pid(self):
            return self._inner.pid

        def communicate(self, timeout=None):
            if not self._timeout_raised:
                self._timeout_raised = True
                raise subprocess.TimeoutExpired(cmd="bash", timeout=120)
            return self._inner.communicate(timeout=timeout)

        def kill(self):
            return self._inner.kill()

        def poll(self):
            return self._inner.poll()

    monkeypatch.setattr(claude_sdk_tool.subprocess, "Popen", _ImmediateTimeoutPopen)
    out = claude_sdk_tool._exec_bash(
        {"command": "sleep 1"},  # short-lived, would normally succeed
        str(tmp_path),
    )
    assert out.startswith("[ERROR]")
    assert "120s timeout" in out


def test_exec_bash_timeout_reaps_descendants(tmp_path, claude_sdk_tool, monkeypatch):
    """Timeout path calls killpg so descendants don't survive.

    Spies on os.killpg to verify it was invoked at least once with
    SIGKILL during the timeout handler. Robust to environments where
    killpg actually returns (the real call is best-effort and may
    no-op on already-dead processes).
    """
    real_popen = claude_sdk_tool.subprocess.Popen
    killpg_calls: list[tuple[int, int]] = []
    real_killpg = os.killpg

    def _spy_killpg(pgid, sig):
        killpg_calls.append((pgid, sig))
        # Allow the real kill so the test doesn't leak a sleep process.
        try:
            real_killpg(pgid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            pass

    monkeypatch.setattr(claude_sdk_tool.os, "killpg", _spy_killpg)

    class _ImmediateTimeoutPopen:
        def __init__(self, *args, **kwargs):
            self._inner = real_popen(*args, **kwargs)
            self._timeout_raised = False

        @property
        def pid(self):
            return self._inner.pid

        def communicate(self, timeout=None):
            if not self._timeout_raised:
                self._timeout_raised = True
                raise subprocess.TimeoutExpired(cmd="bash", timeout=120)
            return self._inner.communicate(timeout=timeout)

        def kill(self):
            return self._inner.kill()

        def poll(self):
            return self._inner.poll()

    monkeypatch.setattr(claude_sdk_tool.subprocess, "Popen", _ImmediateTimeoutPopen)

    out = claude_sdk_tool._exec_bash(
        # spawn a child the bash leader holds — verifies the process
        # group genuinely contains descendants when killpg fires.
        {"command": "sleep 30 & wait"},
        str(tmp_path),
    )
    assert out.startswith("[ERROR]")
    # killpg must have been called at least once with SIGKILL.
    assert any(sig == signal.SIGKILL for _pg, sig in killpg_calls), (
        f"expected killpg(SIGKILL) call, got {killpg_calls!r}"
    )


def test_exec_bash_timeout_with_dead_pid_does_not_raise(
    tmp_path, claude_sdk_tool, monkeypatch
):
    """If the process is already gone when we killpg, the handler swallows."""
    real_popen = claude_sdk_tool.subprocess.Popen

    def _fake_killpg(pgid, sig):
        raise ProcessLookupError("already gone")

    monkeypatch.setattr(claude_sdk_tool.os, "killpg", _fake_killpg)

    class _ImmediateTimeoutPopen:
        def __init__(self, *args, **kwargs):
            self._inner = real_popen(*args, **kwargs)
            self._timeout_raised = False

        @property
        def pid(self):
            return self._inner.pid

        def communicate(self, timeout=None):
            if not self._timeout_raised:
                self._timeout_raised = True
                raise subprocess.TimeoutExpired(cmd="bash", timeout=120)
            return self._inner.communicate(timeout=timeout)

        def kill(self):
            return self._inner.kill()

        def poll(self):
            return self._inner.poll()

    monkeypatch.setattr(claude_sdk_tool.subprocess, "Popen", _ImmediateTimeoutPopen)
    # Must return cleanly — no AttributeError, no ProcessLookupError.
    out = claude_sdk_tool._exec_bash({"command": "true"}, str(tmp_path))
    assert out.startswith("[ERROR]")
