"""Tests for STORY-857 SIGTERM handling and lease-state file lifecycle.

Verifies:
  - SIGTERM during _run_sdk_with_lease releases the lease via /release.
  - SIGTERM terminates the SDK subprocess before exit.
  - Lease state file is written on claim, cleared on release/transition,
    and removed on SIGTERM-initiated release.
  - drain_leases.py reads the state file and POSTs /release.
"""

from __future__ import annotations

import json
import os
import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


def _claim():
    from deployment.hermes.dispatch_poller_v2 import _ActiveClaim
    return _ActiveClaim(
        job_id="job-857",
        lease_token="lease-857",
        expires_at="2099-01-01T00:00:00Z",
        repo="tech-dev-agents",
        story_id="STORY-857",
        prompt="do work",
        scope="small",
    )


@pytest.fixture
def lease_path(tmp_path, monkeypatch):
    """Redirect ACTIVE_LEASE_PATH to a temp file for hermetic tests."""
    p = tmp_path / "active_lease.json"
    monkeypatch.setenv("ACTIVE_LEASE_PATH", str(p))
    return p


# ---------------------------------------------------------------------------
# Lease state file lifecycle
# ---------------------------------------------------------------------------


def test_write_active_lease_creates_file(lease_path):
    from deployment.hermes import dispatch_poller_v2 as mod
    mod._write_active_lease(_claim())
    assert lease_path.exists()
    data = json.loads(lease_path.read_text())
    assert data["job_id"] == "job-857"
    assert data["lease_token"] == "lease-857"
    assert data["story_id"] == "STORY-857"


def test_clear_active_lease_removes_file(lease_path):
    from deployment.hermes import dispatch_poller_v2 as mod
    mod._write_active_lease(_claim())
    assert lease_path.exists()
    mod._clear_active_lease()
    assert not lease_path.exists()


def test_clear_active_lease_idempotent(lease_path):
    """Clearing a non-existent file must not raise."""
    from deployment.hermes import dispatch_poller_v2 as mod
    assert not lease_path.exists()
    mod._clear_active_lease()  # should be a no-op
    assert not lease_path.exists()


def test_active_lease_path_falls_back_to_tmp(monkeypatch):
    """When /var/run/dispatch-poller is not writable, fall back to /tmp."""
    from deployment.hermes import dispatch_poller_v2 as mod
    monkeypatch.delenv("ACTIVE_LEASE_PATH", raising=False)
    # Force the primary check to fail by patching os.path.isdir
    with patch.object(mod.os.path, "isdir", return_value=False):
        path = mod._active_lease_path()
    assert path == mod.ACTIVE_LEASE_FALLBACK_PATH


# ---------------------------------------------------------------------------
# SIGTERM handling
# ---------------------------------------------------------------------------


def test_sigterm_handler_releases_lease_and_terminates_sdk(lease_path, monkeypatch):
    """SIGTERM during _run_sdk_with_lease must terminate SDK + call /release."""
    from deployment.hermes import dispatch_poller_v2 as mod

    # Set up registered active state as if _run_sdk_with_lease was running.
    fake_proc = MagicMock()
    fake_proc.poll.return_value = None  # still running
    claim = _claim()
    session = MagicMock()
    headers = {"X-API-Key": "x", "X-Worker-Version": "2.0"}

    mod._active_sdk_proc = fake_proc
    mod._active_claim_for_signal = claim
    mod._active_session_for_signal = session
    mod._active_headers_for_signal = headers
    mod._write_active_lease(claim)
    assert lease_path.exists()

    release_calls = []

    def _fake_release(claim_arg, *, session, headers, reason="released"):
        release_calls.append({"job_id": claim_arg.job_id, "reason": reason})

    with patch.object(mod, "release_claim", side_effect=_fake_release), \
         pytest.raises(SystemExit) as exc_info:
        mod._handle_sigterm(15, None)

    assert exc_info.value.code == 0
    # SDK proc.terminate() called
    fake_proc.terminate.assert_called_once()
    # Lease released
    assert release_calls == [{"job_id": "job-857", "reason": "sigterm_shutdown"}]
    # State file cleared
    assert not lease_path.exists()
    # Reset module-level state for other tests
    mod._active_sdk_proc = None
    mod._active_claim_for_signal = None
    mod._active_session_for_signal = None
    mod._active_headers_for_signal = None


def test_sigterm_handler_no_active_claim_still_exits(lease_path):
    """SIGTERM with no claim registered should still exit 0 cleanly."""
    from deployment.hermes import dispatch_poller_v2 as mod
    mod._active_sdk_proc = None
    mod._active_claim_for_signal = None
    mod._active_session_for_signal = None
    mod._active_headers_for_signal = None
    with pytest.raises(SystemExit) as exc_info:
        mod._handle_sigterm(15, None)
    assert exc_info.value.code == 0


def test_sigterm_handler_kills_sdk_after_terminate_timeout(lease_path):
    """If SDK doesn't exit on SIGTERM within 10s, force kill."""
    from deployment.hermes import dispatch_poller_v2 as mod
    fake_proc = MagicMock()
    fake_proc.poll.return_value = None
    fake_proc.wait.side_effect = subprocess.TimeoutExpired(cmd="x", timeout=10)
    claim = _claim()
    mod._active_sdk_proc = fake_proc
    mod._active_claim_for_signal = claim
    mod._active_session_for_signal = MagicMock()
    mod._active_headers_for_signal = {}

    with patch.object(mod, "release_claim"), \
         pytest.raises(SystemExit):
        mod._handle_sigterm(15, None)

    fake_proc.terminate.assert_called_once()
    fake_proc.kill.assert_called_once()
    mod._active_sdk_proc = None
    mod._active_claim_for_signal = None
    mod._active_session_for_signal = None
    mod._active_headers_for_signal = None


def test_install_sigterm_handler_idempotent():
    """Calling _install_sigterm_handler twice should not raise."""
    from deployment.hermes import dispatch_poller_v2 as mod
    mod._install_sigterm_handler()
    mod._install_sigterm_handler()  # second call must succeed


# ---------------------------------------------------------------------------
# _run_sdk registers proc with the SIGTERM handler, clears it on return
# ---------------------------------------------------------------------------


def test_run_sdk_clears_proc_ref_on_success(lease_path):
    """After _run_sdk returns successfully, _active_sdk_proc must be cleared."""
    from deployment.hermes import dispatch_poller_v2 as mod

    claim = _claim()
    fake_proc = MagicMock()
    fake_proc.communicate.return_value = ("", "")
    fake_proc.returncode = 0

    with patch.object(mod.subprocess, "Popen", return_value=fake_proc), \
         patch.object(mod, "_resolve_workspace", return_value="/tmp"):
        success, _ = mod._run_sdk(claim)

    assert success is True
    # The proc ref is cleared by the finally block so an idle-window SIGTERM
    # does not see a stale subprocess.
    assert mod._active_sdk_proc is None


def test_run_sdk_clears_proc_ref_on_exception(lease_path):
    """Even on exception, the finally block must clear _active_sdk_proc."""
    from deployment.hermes import dispatch_poller_v2 as mod

    claim = _claim()
    fake_proc = MagicMock()
    fake_proc.communicate.side_effect = RuntimeError("boom")

    with patch.object(mod.subprocess, "Popen", return_value=fake_proc), \
         patch.object(mod, "_resolve_workspace", return_value="/tmp"):
        success, output = mod._run_sdk(claim)

    assert success is False
    assert "boom" in output
    assert mod._active_sdk_proc is None


# ---------------------------------------------------------------------------
# drain_leases.py
# ---------------------------------------------------------------------------


def test_drain_leases_reads_state_file_and_posts_release(tmp_path, monkeypatch):
    """drain_leases.main() must POST /release for the recorded lease."""
    from deployment.hermes import drain_leases as drain

    state_file = tmp_path / "active_lease.json"
    state_file.write_text(json.dumps({
        "job_id": "job-857",
        "lease_token": "lease-857",
        "story_id": "STORY-857",
        "repo": "tech-dev-agents",
    }))

    monkeypatch.setenv("ACTIVE_LEASE_PATH", str(state_file))
    monkeypatch.setenv("OPS_CONSOLE_URL", "http://test.local")
    monkeypatch.setenv("OPS_CONSOLE_API_KEY", "k")

    posted = []

    class _Resp:
        status_code = 200
        text = ""

    class _Session:
        def post(self, url, json, headers, timeout):
            posted.append({"url": url, "json": json})
            return _Resp()

    with patch.object(drain.requests, "Session", return_value=_Session()):
        rc = drain.main()

    assert rc == 0
    assert len(posted) == 1
    assert posted[0]["url"].endswith("/api/dispatch/v2/release")
    assert posted[0]["json"]["job_id"] == "job-857"
    assert posted[0]["json"]["lease_token"] == "lease-857"
    # File removed after drain.
    assert not state_file.exists()


def test_drain_leases_no_state_file_is_noop(tmp_path, monkeypatch):
    """Missing state file should exit 0 without making any HTTP calls."""
    from deployment.hermes import drain_leases as drain
    monkeypatch.setenv("ACTIVE_LEASE_PATH", str(tmp_path / "missing.json"))

    posted = []

    class _Session:
        def post(self, *a, **kw):
            posted.append(kw)
            raise AssertionError("should not be called")

    with patch.object(drain.requests, "Session", return_value=_Session()):
        rc = drain.main()

    assert rc == 0
    assert posted == []


def test_drain_leases_idempotent_on_409(tmp_path, monkeypatch):
    """A 409 stale lease response should be treated as 'already released'."""
    from deployment.hermes import drain_leases as drain

    state_file = tmp_path / "active_lease.json"
    state_file.write_text(json.dumps({
        "job_id": "job-857",
        "lease_token": "lease-stale",
    }))
    monkeypatch.setenv("ACTIVE_LEASE_PATH", str(state_file))

    class _Resp:
        status_code = 409
        text = "stale lease"

    class _Session:
        def post(self, *a, **kw):
            return _Resp()

    with patch.object(drain.requests, "Session", return_value=_Session()):
        rc = drain.main()

    assert rc == 0
    assert not state_file.exists()


def test_drain_leases_handles_list_of_leases(tmp_path, monkeypatch):
    """State file containing a list of leases should release each one."""
    from deployment.hermes import drain_leases as drain

    state_file = tmp_path / "active_lease.json"
    state_file.write_text(json.dumps([
        {"job_id": "job-1", "lease_token": "lt-1"},
        {"job_id": "job-2", "lease_token": "lt-2"},
    ]))
    monkeypatch.setenv("ACTIVE_LEASE_PATH", str(state_file))

    posted = []

    class _Resp:
        status_code = 200
        text = ""

    class _Session:
        def post(self, url, json, headers, timeout):
            posted.append(json["job_id"])
            return _Resp()

    with patch.object(drain.requests, "Session", return_value=_Session()):
        rc = drain.main()

    assert rc == 0
    assert sorted(posted) == ["job-1", "job-2"]
