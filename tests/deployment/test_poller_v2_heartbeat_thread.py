"""Tests for the v2 poller heartbeat thread + last_action sidecar.

Covers (Phase A — stall detection):
  - _read_last_action returns None / trimmed text / 500-char truncated
  - send_heartbeat forwards last_action when sidecar present
  - _heartbeat_loop ticks while subprocess is alive, stops on exit
  - _heartbeat_loop stops on stale-lease signal from send_heartbeat
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest


def _claim():
    from deployment.hermes.dispatch_poller_v2 import _ActiveClaim
    return _ActiveClaim(
        job_id="job-stall-test",
        lease_token="lease-stall-test",
        expires_at="2099-01-01T00:00:00Z",
        repo="tech-dev-agents",
        story_id="STORY-STALL-TEST",
        prompt="do work",
        scope="small",
    )


@pytest.fixture
def sidecar_path(tmp_path, monkeypatch):
    p = tmp_path / "last_action.txt"
    monkeypatch.setenv("DISPATCH_LAST_ACTION_PATH", str(p))
    return p


# ---------------------------------------------------------------------------
# _read_last_action
# ---------------------------------------------------------------------------


class TestReadLastAction:
    def test_missing_file_returns_none(self, sidecar_path):
        from deployment.hermes import dispatch_poller_v2 as mod
        assert not sidecar_path.exists()
        assert mod._read_last_action() is None

    def test_empty_file_returns_none(self, sidecar_path):
        from deployment.hermes import dispatch_poller_v2 as mod
        sidecar_path.write_text("")
        assert mod._read_last_action() is None

    def test_returns_trimmed_text(self, sidecar_path):
        from deployment.hermes import dispatch_poller_v2 as mod
        sidecar_path.write_text("  committed abc123 (Phase 8)  \n")
        assert mod._read_last_action() == "committed abc123 (Phase 8)"

    def test_truncates_to_500_chars(self, sidecar_path):
        from deployment.hermes import dispatch_poller_v2 as mod
        sidecar_path.write_text("x" * 800)
        out = mod._read_last_action()
        assert out is not None
        assert len(out) == 500


# ---------------------------------------------------------------------------
# send_heartbeat — last_action forwarding
# ---------------------------------------------------------------------------


class TestSendHeartbeatLastAction:
    def test_includes_last_action_from_sidecar(self, sidecar_path):
        from deployment.hermes import dispatch_poller_v2 as mod

        sidecar_path.write_text("drafting test cases (Phase 7)")
        claim = _claim()
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"expires_at": "2099-01-01T00:15:00Z"}

        with patch.object(mod, "_post", return_value=mock_resp) as p:
            ok = mod.send_heartbeat(
                claim, session=MagicMock(), headers={}
            )

        assert ok is True
        # _post called with /heartbeat path and a payload containing last_action
        args, kwargs = p.call_args
        assert args[0] == "/heartbeat"
        payload = args[1]
        assert payload["last_action"] == "drafting test cases (Phase 7)"
        assert payload["lease_token"] == claim.lease_token

    def test_omits_last_action_when_no_sidecar(self, sidecar_path):
        from deployment.hermes import dispatch_poller_v2 as mod

        # No sidecar file written
        claim = _claim()
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"expires_at": "2099-01-01T00:15:00Z"}

        with patch.object(mod, "_post", return_value=mock_resp) as p:
            mod.send_heartbeat(claim, session=MagicMock(), headers={})

        payload = p.call_args.args[1]
        assert "last_action" not in payload

    def test_returns_false_on_stale_lease(self, sidecar_path):
        from deployment.hermes import dispatch_poller_v2 as mod

        claim = _claim()
        mock_resp = MagicMock(status_code=409)
        mock_resp.json.return_value = {"detail": "stale"}

        with patch.object(mod, "_post", return_value=mock_resp):
            ok = mod.send_heartbeat(claim, session=MagicMock(), headers={})

        assert ok is False


# ---------------------------------------------------------------------------
# _heartbeat_loop
# ---------------------------------------------------------------------------


class _FakeProc:
    """Mimics subprocess.Popen.poll: returns None until told to stop."""

    def __init__(self):
        self._returncode: int | None = None

    def poll(self):
        return self._returncode

    def exit(self, code: int = 0):
        self._returncode = code


class TestHeartbeatLoop:
    def test_ticks_while_proc_alive(self, sidecar_path):
        from deployment.hermes import dispatch_poller_v2 as mod

        claim = _claim()
        proc = _FakeProc()
        stop = threading.Event()
        ticks = []

        def fake_send_heartbeat(c, *, session, headers, **kwargs):
            ticks.append(time.monotonic())
            if len(ticks) >= 3:
                proc.exit(0)
            return True

        with patch.object(mod, "send_heartbeat", side_effect=fake_send_heartbeat):
            t = threading.Thread(
                target=mod._heartbeat_loop,
                args=(claim, proc),
                kwargs={
                    "session": MagicMock(),
                    "headers": {},
                    "interval_sec": 0,  # tight loop for test
                    "stop_event": stop,
                },
                daemon=True,
            )
            t.start()
            t.join(timeout=2)

        assert len(ticks) >= 3, f"Expected ≥3 heartbeats, got {len(ticks)}"

    def test_stops_when_stop_event_set(self, sidecar_path):
        from deployment.hermes import dispatch_poller_v2 as mod

        claim = _claim()
        proc = _FakeProc()
        stop = threading.Event()
        ticks = []

        def fake_send_heartbeat(c, **kwargs):
            ticks.append(1)
            return True

        with patch.object(mod, "send_heartbeat", side_effect=fake_send_heartbeat):
            t = threading.Thread(
                target=mod._heartbeat_loop,
                args=(claim, proc),
                kwargs={
                    "session": MagicMock(),
                    "headers": {},
                    "interval_sec": 0.05,
                    "stop_event": stop,
                },
                daemon=True,
            )
            t.start()
            time.sleep(0.15)
            stop.set()
            t.join(timeout=1)

        # Thread exited cleanly; ticks may be 0-3 depending on scheduling.
        assert not t.is_alive()

    def test_stops_on_stale_lease(self, sidecar_path):
        from deployment.hermes import dispatch_poller_v2 as mod

        claim = _claim()
        proc = _FakeProc()
        stop = threading.Event()
        ticks = []

        def fake_send_heartbeat(c, **kwargs):
            ticks.append(1)
            return False  # 409 simulated — stale lease

        with patch.object(mod, "send_heartbeat", side_effect=fake_send_heartbeat):
            t = threading.Thread(
                target=mod._heartbeat_loop,
                args=(claim, proc),
                kwargs={
                    "session": MagicMock(),
                    "headers": {},
                    "interval_sec": 0,
                    "stop_event": stop,
                },
                daemon=True,
            )
            t.start()
            t.join(timeout=1)

        assert not t.is_alive(), "Thread should stop on first stale lease"
        # Should have heart-beated exactly once before bailing.
        assert ticks == [1]
