"""STORY-WATCHDOG-2026-05-25 — ProgressWatchdog unit tests.

These tests pin the four trip conditions and the SIGTERM → grace →
SIGKILL kill sequence. They use either:

  * a real subprocess that sleeps + ignores SIGTERM (for the grace-period
    test), or
  * a fake Popen-like object with poll()/pid attributes (for trip-logic
    tests where we don't need a real process).

``os.killpg`` is patched so tests don't actually signal real processes,
and ``release_fn`` / ``clear_lease_fn`` are recorded by Mock objects so
we can assert the watchdog called them with the right reason.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from deployment.hermes.sdk_progress_watchdog import (
    DEFAULT_GRACE_SECONDS,
    TRIP_LEASE_LOST,
    TRIP_OOM_GUARD,
    TRIP_PROGRESS_STALL,
    TRIP_TURN_MAX,
    ProgressWatchdog,
    kill_process_group,
    measure_rss_mb,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeProc:
    """Stand-in for subprocess.Popen — only exposes pid + poll()."""

    def __init__(self, pid: int = 99999, alive: bool = True) -> None:
        self.pid = pid
        self._alive = alive

    def poll(self):
        return None if self._alive else 0

    def set_exited(self) -> None:
        self._alive = False


def _make_watchdog(
    *,
    last_output_ts: list[float] | None = None,
    lease_lost: threading.Event | None = None,
    thresholds: dict | None = None,
    proc: FakeProc | None = None,
):
    proc = proc or FakeProc()
    claim = SimpleNamespace(job_id="job-test-001", lease_token="lt-1")
    last_output_ts = last_output_ts if last_output_ts is not None else [time.monotonic()]
    lease_lost = lease_lost or threading.Event()
    release_fn = MagicMock()
    clear_lease_fn = MagicMock()
    wd = ProgressWatchdog(
        proc,
        claim,
        last_output_ts,
        lease_lost,
        session=MagicMock(),
        headers={"X-API-Key": "test"},
        release_fn=release_fn,
        clear_lease_fn=clear_lease_fn,
        thresholds=thresholds or {"tick_seconds": 0.05, "grace_seconds": 0.05},
    )
    return wd, release_fn, clear_lease_fn, proc, lease_lost, last_output_ts


# ---------------------------------------------------------------------------
# Trip: lease lost
# ---------------------------------------------------------------------------


def test_lease_lost_event_trips_watchdog() -> None:
    """When the heartbeat thread sets lease_lost_event, watchdog fires."""
    wd, release_fn, clear_lease_fn, proc, lease_lost, _ = _make_watchdog()

    with patch("deployment.hermes.sdk_progress_watchdog.os.killpg") as killpg, \
         patch("deployment.hermes.sdk_progress_watchdog.os.getpgid", return_value=proc.pid):
        wd.start()
        lease_lost.set()
        # Wait up to 2s for the watchdog to detect + fire.
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and not wd.fired:
            time.sleep(0.02)
        # After .fired flips True the daemon thread is still inside
        # release_fn / clear_lease_fn — give it a moment to finish.
        if wd._thread is not None:
            wd._thread.join(timeout=2.0)
        wd.stop()

    assert wd.fired is True
    assert wd.fired_reason == TRIP_LEASE_LOST
    release_fn.assert_called_once()
    assert release_fn.call_args.kwargs["reason"] == TRIP_LEASE_LOST
    clear_lease_fn.assert_called_once()
    # Both SIGTERM and SIGKILL should have been sent.
    sent_signals = [c.args[1] for c in killpg.call_args_list]
    assert signal.SIGTERM in sent_signals
    assert signal.SIGKILL in sent_signals


# ---------------------------------------------------------------------------
# Trip: progress stall (silence)
# ---------------------------------------------------------------------------


def test_silence_trips_progress_stall() -> None:
    """When last_output_ts is older than silence_max, watchdog fires."""
    # last_output_ts in the deep past → silence > threshold immediately
    last = [time.monotonic() - 1000.0]
    wd, release_fn, _, proc, _, _ = _make_watchdog(
        last_output_ts=last,
        thresholds={
            "tick_seconds": 0.05,
            "silence_max_seconds": 1,
            "grace_seconds": 0.05,
        },
    )

    with patch("deployment.hermes.sdk_progress_watchdog.os.killpg"), \
         patch("deployment.hermes.sdk_progress_watchdog.os.getpgid", return_value=proc.pid):
        wd.start()
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and not wd.fired:
            time.sleep(0.02)
        # After .fired flips True the daemon thread is still inside
        # release_fn / clear_lease_fn — give it a moment to finish.
        if wd._thread is not None:
            wd._thread.join(timeout=2.0)
        wd.stop()

    assert wd.fired is True
    assert wd.fired_reason == TRIP_PROGRESS_STALL
    release_fn.assert_called_once()
    assert release_fn.call_args.kwargs["reason"] == TRIP_PROGRESS_STALL


# ---------------------------------------------------------------------------
# Trip: turn-max wall clock
# ---------------------------------------------------------------------------


def test_turn_max_trips_wall_clock() -> None:
    """Wall-clock since start exceeds turn_max → watchdog fires."""
    wd, release_fn, _, proc, _, _ = _make_watchdog(
        thresholds={
            "tick_seconds": 0.05,
            "turn_max_seconds": 0.2,
            "grace_seconds": 0.05,
        },
    )

    with patch("deployment.hermes.sdk_progress_watchdog.os.killpg"), \
         patch("deployment.hermes.sdk_progress_watchdog.os.getpgid", return_value=proc.pid):
        wd.start()
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and not wd.fired:
            time.sleep(0.05)
        if wd._thread is not None:
            wd._thread.join(timeout=2.0)
        wd.stop()

    assert wd.fired is True
    assert wd.fired_reason == TRIP_TURN_MAX
    release_fn.assert_called_once()
    assert release_fn.call_args.kwargs["reason"] == TRIP_TURN_MAX


# ---------------------------------------------------------------------------
# Trip: RSS / OOM guard
# ---------------------------------------------------------------------------


def test_rss_trips_oom_guard() -> None:
    """When measure_rss_mb returns above threshold, watchdog fires."""
    wd, release_fn, _, proc, _, _ = _make_watchdog(
        thresholds={
            "tick_seconds": 0.05,
            "rss_max_mb": 100,
            "grace_seconds": 0.05,
        },
    )

    with patch(
        "deployment.hermes.sdk_progress_watchdog.measure_rss_mb",
        return_value=9999.0,  # well above 100 MB threshold
    ), patch("deployment.hermes.sdk_progress_watchdog.os.killpg"), \
       patch("deployment.hermes.sdk_progress_watchdog.os.getpgid", return_value=proc.pid):
        wd.start()
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and not wd.fired:
            time.sleep(0.02)
        # After .fired flips True the daemon thread is still inside
        # release_fn / clear_lease_fn — give it a moment to finish.
        if wd._thread is not None:
            wd._thread.join(timeout=2.0)
        wd.stop()

    assert wd.fired is True
    assert wd.fired_reason == TRIP_OOM_GUARD
    release_fn.assert_called_once()
    assert release_fn.call_args.kwargs["reason"] == TRIP_OOM_GUARD


def test_rss_none_does_not_trip() -> None:
    """When measure_rss_mb returns None (couldn't measure), no trip."""
    wd, release_fn, _, proc, _, _ = _make_watchdog(
        thresholds={
            "tick_seconds": 0.05,
            "rss_max_mb": 1,  # would trip if measurement succeeded
            "turn_max_seconds": 999,
            "silence_max_seconds": 999,
            "grace_seconds": 0.05,
        },
    )

    with patch(
        "deployment.hermes.sdk_progress_watchdog.measure_rss_mb",
        return_value=None,
    ), patch("deployment.hermes.sdk_progress_watchdog.os.killpg"), \
       patch("deployment.hermes.sdk_progress_watchdog.os.getpgid", return_value=proc.pid):
        wd.start()
        time.sleep(0.3)
        wd.stop()

    assert wd.fired is False
    release_fn.assert_not_called()


# ---------------------------------------------------------------------------
# Watchdog exits cleanly when SDK exits on its own
# ---------------------------------------------------------------------------


def test_watchdog_exits_when_proc_exits() -> None:
    """If proc.poll() returns non-None, watchdog stops without firing."""
    proc = FakeProc(alive=True)
    wd, release_fn, _, _, _, _ = _make_watchdog(proc=proc)

    wd.start()
    time.sleep(0.1)
    proc.set_exited()
    time.sleep(0.2)
    wd.stop()

    assert wd.fired is False
    release_fn.assert_not_called()


def test_watchdog_stop_is_idempotent() -> None:
    """stop() can be called repeatedly without exception."""
    wd, _, _, _, _, _ = _make_watchdog()
    wd.start()
    wd.stop()
    wd.stop()
    wd.stop()


# ---------------------------------------------------------------------------
# kill_process_group — SIGTERM, grace, SIGKILL ordering
# ---------------------------------------------------------------------------


def test_kill_process_group_sigterm_then_sigkill_with_grace() -> None:
    """SIGTERM is sent, then after grace_seconds, SIGKILL is sent."""
    sleep_calls: list[float] = []
    killpg_calls: list[tuple[int, int]] = []

    def fake_sleep(s: float) -> None:
        sleep_calls.append(s)

    def fake_killpg(pgid: int, sig: int) -> None:
        killpg_calls.append((pgid, sig))

    with patch("deployment.hermes.sdk_progress_watchdog.os.killpg", side_effect=fake_killpg), \
         patch("deployment.hermes.sdk_progress_watchdog.os.getpgid", return_value=12345):
        kill_process_group(12345, grace_seconds=7.5, sleep=fake_sleep)

    # SIGTERM first, then SIGKILL — both targeting the pgid.
    assert killpg_calls == [(12345, signal.SIGTERM), (12345, signal.SIGKILL)]
    # Grace sleep happened between them with the configured duration.
    assert sleep_calls == [7.5]


def test_kill_process_group_swallows_process_lookup_error() -> None:
    """ProcessLookupError on SIGTERM or SIGKILL must not propagate."""
    def fake_killpg(pgid: int, sig: int) -> None:
        raise ProcessLookupError()

    with patch("deployment.hermes.sdk_progress_watchdog.os.killpg", side_effect=fake_killpg), \
         patch("deployment.hermes.sdk_progress_watchdog.os.getpgid", return_value=12345):
        # Should NOT raise.
        kill_process_group(12345, grace_seconds=0.0, sleep=lambda _: None)


def test_kill_process_group_getpgid_failure_falls_back_to_pid() -> None:
    """When getpgid raises, fall back to using pid as pgid."""
    killpg_calls: list[tuple[int, int]] = []

    def fake_killpg(pgid: int, sig: int) -> None:
        killpg_calls.append((pgid, sig))

    def raise_lookup(_pid: int) -> int:
        raise ProcessLookupError()

    with patch("deployment.hermes.sdk_progress_watchdog.os.killpg", side_effect=fake_killpg), \
         patch("deployment.hermes.sdk_progress_watchdog.os.getpgid", side_effect=raise_lookup):
        kill_process_group(77777, grace_seconds=0.0, sleep=lambda _: None)

    # pgid fallback === pid
    assert killpg_calls == [(77777, signal.SIGTERM), (77777, signal.SIGKILL)]


# ---------------------------------------------------------------------------
# Grace-period integration: real subprocess that ignores SIGTERM
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals only")
def test_kill_process_group_real_subprocess_grace_then_sigkill() -> None:
    """Integration: a real child that ignores SIGTERM gets SIGKILLed.

    Uses a tiny Python one-liner that installs SIG_IGN for SIGTERM and
    then sleeps. After SIGTERM is sent the child stays alive until the
    grace period expires and SIGKILL is sent. We assert the child is
    actually dead after the call returns.
    """
    code = (
        "import signal, time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "time.sleep(60)"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        start_new_session=True,
    )
    try:
        # Give the child time to install the handler.
        time.sleep(0.3)
        # Real call, real signals, short grace.
        kill_process_group(proc.pid, grace_seconds=0.5)
        # Give the OS a moment to reap.
        proc.wait(timeout=5)
        assert proc.poll() is not None
    finally:
        if proc.poll() is None:  # safety
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# measure_rss_mb — basic sanity (real process)
# ---------------------------------------------------------------------------


def test_measure_rss_mb_returns_positive_for_self() -> None:
    """Measuring our own process should return a positive number or None.

    Returns None only on weird platforms with no psutil and no /proc;
    on the CI/dev box it must succeed.
    """
    val = measure_rss_mb(os.getpid())
    # Either we got a positive measurement, or both psutil + /proc are
    # unavailable — in which case None is acceptable.
    assert val is None or val > 0
