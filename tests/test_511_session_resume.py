"""STORY-511 AC-9 — integration tests for session-resume threading.

Covers the state-management contract that threads Claude Code session IDs
across phases. Doesn't exercise the full SDK (that's the benchmark script)
— tests the persist/read/clear lifecycle and the phase_end event shape.

Run: pytest tests/test_511_session_resume.py -v
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import uuid

import pytest


@pytest.fixture
def phase_runner(monkeypatch):
    """Import the phase-runner module from deployment/hermes, isolate state files to a tmp dir."""
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    mod_path = repo_root / "deployment" / "hermes" / "sdlc_phase_runner.py"
    assert mod_path.exists(), f"phase runner not found at {mod_path}"
    spec = importlib.util.spec_from_file_location("sdlc_phase_runner_test", mod_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # Redirect the session-id file to a temp dir so tests don't collide with
    # running pollers or each other.
    tmpdir = tempfile.mkdtemp(prefix="story-511-test-")
    monkeypatch.setattr(
        mod,
        "_SESSION_ID_FILE",
        str(pathlib.Path(tmpdir) / "phase-session-{story_id}.id"),
    )
    return mod


def test_read_session_id_returns_none_when_missing(phase_runner):
    assert phase_runner._read_story_session_id("STORY-99001") is None


def test_persist_and_read_session_id_roundtrip(phase_runner):
    sid = str(uuid.uuid4())
    path = phase_runner._SESSION_ID_FILE.format(story_id="STORY-99002")
    pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        f.write(sid)
    assert phase_runner._read_story_session_id("STORY-99002") == sid


def test_clear_session_id_removes_file(phase_runner):
    sid = str(uuid.uuid4())
    path = phase_runner._SESSION_ID_FILE.format(story_id="STORY-99003")
    pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        f.write(sid)
    assert phase_runner._read_story_session_id("STORY-99003") == sid

    phase_runner._clear_story_session_id("STORY-99003")
    assert phase_runner._read_story_session_id("STORY-99003") is None


def test_clear_on_missing_file_does_not_raise(phase_runner):
    # Should be a no-op — used in terminal-state cleanup where file may not exist
    phase_runner._clear_story_session_id("STORY-99004")


def test_resume_disabled_via_env_var(phase_runner, monkeypatch):
    """With PHASE_SESSION_RESUME=0 the read-path short-circuits and returns None.

    This is the feature-flag escape hatch (STORY-511 AC-11). Setting the env
    var to 0 restores the pre-511 behavior — every phase starts fresh.
    """
    # Simulate disabled state by monkeypatching the module-level constant.
    monkeypatch.setattr(phase_runner, "_PHASE_SESSION_RESUME", False)
    sid = str(uuid.uuid4())
    path = phase_runner._SESSION_ID_FILE.format(story_id="STORY-99005")
    pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        f.write(sid)
    # Even with a stored sid, disabled flag means we don't return it.
    assert phase_runner._read_story_session_id("STORY-99005") is None


# ---------------------------------------------------------------------------
# journalctl-based capture (STORY-511 fix 2026-04-22)
#
# Context: the prior tests here mocked ``glob.glob`` on
# ``/tmp/claude-sdlc-logs/session-*.log`` — a path the phase runner never
# actually touches. Those tests passed but validated nothing; in production
# every phase_end logged ``session_id: "unknown"``. The new tests mock
# ``subprocess.run`` the way the production code calls it: a ``journalctl``
# invocation whose stdout contains ``[SESSION] <uuid>`` lines.
# ---------------------------------------------------------------------------


class _FakeCompletedProcess:
    """Stand-in for subprocess.CompletedProcess — only the attrs our code reads."""

    def __init__(self, stdout: str = "", returncode: int = 0, stderr: str = ""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


def _install_fake_journalctl(monkeypatch, phase_runner, journal_text: str, *, returncode: int = 0):
    """Patch ``subprocess.run`` inside the phase-runner module so any journalctl
    call returns ``journal_text``. Non-journalctl calls fall through to the real
    subprocess.run so we don't break unrelated invocations."""
    import subprocess as _sub
    real_run = _sub.run
    seen_args: list[list[str]] = []

    def fake_run(cmd, *args, **kwargs):
        if isinstance(cmd, (list, tuple)) and cmd and cmd[0] == "journalctl":
            seen_args.append(list(cmd))
            return _FakeCompletedProcess(stdout=journal_text, returncode=returncode)
        return real_run(cmd, *args, **kwargs)

    # The phase runner imports subprocess at module level and calls
    # subprocess.run(...). Patch the attribute on the module's subprocess ref
    # so the code under test sees our fake without affecting the global module.
    monkeypatch.setattr(phase_runner.subprocess, "run", fake_run)
    return seen_args


def test_capture_from_journal_parses_session_line(phase_runner, monkeypatch):
    """Happy path: one [SESSION] line in the journal window → sid is persisted."""
    sid = str(uuid.uuid4())
    journal = (
        "Apr 22 02:00:15 vm-daisy python3[123]: [CONFIG] workdir=/tmp\n"
        f"Apr 22 02:00:16 vm-daisy python3[123]: [SESSION] {sid}\n"
        "Apr 22 02:00:20 vm-daisy python3[123]: [DONE] turns=10 cost=$0.12\n"
    )
    seen = _install_fake_journalctl(monkeypatch, phase_runner, journal)

    phase_runner._capture_session_id_from_log("STORY-99006", since_ts="2026-04-22 02:00:00")

    assert phase_runner._read_story_session_id("STORY-99006") == sid
    # Confirm we actually shelled out to journalctl (not some other path).
    assert len(seen) == 1
    assert "journalctl" in seen[0][0]
    assert "dispatch-poller" in seen[0]
    # The --since value must be the phase-start timestamp the caller passed in,
    # NOT a hardcoded "-15 minutes". This is the contract that scopes capture
    # to the current phase and prevents cross-phase sid collision.
    assert "2026-04-22 02:00:00" in seen[0]


def test_capture_from_journal_no_session_line_does_not_persist(phase_runner, monkeypatch):
    """Empty/no-match journal output → no sid persisted, no crash."""
    journal = "Apr 22 02:00:15 vm-daisy python3[123]: [CONFIG] workdir=/tmp\n[DONE] turns=1\n"
    _install_fake_journalctl(monkeypatch, phase_runner, journal)

    phase_runner._capture_session_id_from_log("STORY-99007", since_ts="2026-04-22 02:00:00")

    assert phase_runner._read_story_session_id("STORY-99007") is None


def test_capture_picks_most_recent_session_when_multiple_lines(phase_runner, monkeypatch):
    """Journal has N [SESSION] lines (mid-phase crash + restart) → last wins.
    Otherwise ``--resume`` would target a dead session and the SDK 404s."""
    sid_old = str(uuid.uuid4())
    sid_new = str(uuid.uuid4())
    journal = (
        f"Apr 22 02:00:05 vm-daisy python3[123]: [SESSION] {sid_old}\n"
        "Apr 22 02:00:10 vm-daisy python3[123]: [WORKING] 5 tool calls\n"
        f"Apr 22 02:00:30 vm-daisy python3[456]: [SESSION] {sid_new}\n"
        "Apr 22 02:00:45 vm-daisy python3[456]: [DONE]\n"
    )
    _install_fake_journalctl(monkeypatch, phase_runner, journal)

    phase_runner._capture_session_id_from_log("STORY-99008", since_ts="2026-04-22 02:00:00")

    assert phase_runner._read_story_session_id("STORY-99008") == sid_new


def test_capture_tolerates_journalctl_nonzero_exit(phase_runner, monkeypatch):
    """If journalctl exits non-zero (permission denied, not-a-systemd-host in
    tests, etc.) capture must no-op silently — never crash the phase."""
    _install_fake_journalctl(monkeypatch, phase_runner, journal_text="", returncode=1)

    phase_runner._capture_session_id_from_log("STORY-99009", since_ts="2026-04-22 02:00:00")

    assert phase_runner._read_story_session_id("STORY-99009") is None


def test_capture_tolerates_subprocess_exception(phase_runner, monkeypatch):
    """If subprocess.run raises (timeout, OSError), capture must swallow and
    return. A session-resume failure must not kill the phase."""

    def boom(cmd, *args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=15)

    monkeypatch.setattr(phase_runner.subprocess, "run", boom)

    # Should not raise. Should not persist anything.
    phase_runner._capture_session_id_from_log("STORY-99010", since_ts="2026-04-22 02:00:00")
    assert phase_runner._read_story_session_id("STORY-99010") is None


def test_fast_phase_is_not_auto_rate_limited(phase_runner):
    """Regression for 2026-04-22 13:20:55: a phase that exits rc=0 in <15 s
    must NOT be auto-classified as rate-limited based on duration alone.

    Prior code:
        if not rate_limited and duration < 15 and proc.returncode == 0:
            rate_limited = True  # false positive

    Daisy's Phase 7 for STORY-515 ran 14 s because the resume logic
    correctly detected all deliverables already existed on the branch.
    The duration-only check flagged it as rate-limited, the poller
    wrote /var/run/dispatch-poller-paused-until, and systemctl disable
    --now dispatch-poller took Daisy offline for 20 minutes until I
    spotted it while the queue backed up to 7 pending stories.

    The guard now requires the literal "hit your limit" text. This test
    pins that contract at source level so a future edit can't re-introduce
    the duration-only branch.
    """
    import inspect
    source = inspect.getsource(phase_runner._run_phase_sdk)
    # Pattern specifically checks: the line that sets rate_limited=True
    # should NOT key off duration alone.
    bad_pattern = "duration < 15 and proc.returncode == 0:\n            rate_limited = True"
    assert bad_pattern not in source, (
        "duration-only rate-limit heuristic is back — it caused a "
        "false positive on 2026-04-22 (Daisy offline for 20 min). "
        "Only trigger rate_limited=True on explicit 'hit your limit' text."
    )
    # Also confirm the rate_limited set is driven by the text check.
    assert '"hit your limit" in all_output.lower()' in source, (
        "explicit rate-limit text check missing from _run_phase_sdk"
    )


def test_capture_without_since_ts_uses_fallback_window(phase_runner, monkeypatch):
    """Backwards compat: callers that don't pass since_ts get a -15m window.
    Keeps old callsites working even after we add the since_ts parameter."""
    sid = str(uuid.uuid4())
    journal = f"Apr 22 02:00:16 vm-daisy python3[123]: [SESSION] {sid}\n"
    seen = _install_fake_journalctl(monkeypatch, phase_runner, journal)

    phase_runner._capture_session_id_from_log("STORY-99011")  # no since_ts

    assert phase_runner._read_story_session_id("STORY-99011") == sid
    # Fallback value is the "-15 minutes" literal journalctl understands.
    assert "-15 minutes" in seen[0]
