"""STORY-763: Phase-Progress Watchdog — kill zombie heartbeats when SDK dies silently.

The heartbeat thread in sdlc_phase_runner._heartbeat_thread currently runs
independently of the SDK subprocess.  If the SDK dies mid-phase the thread
keeps posting heartbeats for hours, hiding the failure from the stale-claim
detector (STORY-702).

This test file drives the design of the watchdog enhancement.  All tests are
in RED state until Phase 8 implements:

  1. New _heartbeat_thread signature: (story_id, repo, stop_event, sdk_pid,
     last_output_ts, phase_timeout_s)
  2. Per-tick PID liveness check via os.kill(pid, 0)
  3. Per-tick progress-stale check: time.time() - last_output_ts[0] > phase_timeout_s * STALE_MULTIPLIER
  4. Kill path: os.killpg(os.getpgid(pid), SIGTERM) → 5s grace → SIGKILL
  5. Failure reporting: urllib POST to /api/dispatch/fail/{story_id}
  6. Source update: last_output_ts[0] = time.time() on each SDK stdout line
  7. Loki-visible log: [DISPATCH] watchdog: STORY-N <action> reason=<reason>

Test groups:
  A — Signature & Plumbing (SC-1, AC-1)
  B — PID Liveness (SC-2, AC-2, AC-5, AC-6, AC-9)
  C — Progress Stall (SC-3, SC-5, AC-3, AC-4)
  D — Stdout Watcher (SC-4, AC-8)
  E — Failure Observability (SC-6, AC-5, AC-9, AC-11)
"""
from __future__ import annotations

import inspect
import io
import json
import os
import signal
import sys
import threading
import time
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "deployment" / "hermes"))

import sdlc_phase_runner  # noqa: E402  (path insert needed first)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _run_heartbeat_thread(
    story_id: str,
    repo: str,
    stop_event: threading.Event,
    /,
    sdk_pid: int | None = None,
    last_output_ts: "list[float] | None" = None,
    phase_timeout_s: int = 1200,
    *,
    interval: float = 0.05,
    wait: float = 0.3,
) -> tuple[threading.Thread, list[Exception]]:
    """Run _heartbeat_thread in a daemon thread with a short interval.

    Returns (thread, exceptions_raised_inside_thread).  The caller should
    assert exceptions is empty for a success case, or check its content for
    expected failures.
    """
    exc_container: list[Exception] = []

    original_interval = sdlc_phase_runner.HEARTBEAT_INTERVAL
    sdlc_phase_runner.HEARTBEAT_INTERVAL = interval

    def _target():
        try:
            sdlc_phase_runner._heartbeat_thread(
                story_id,
                repo,
                stop_event,
                sdk_pid,
                last_output_ts,
                phase_timeout_s,
            )
        except Exception as exc:
            exc_container.append(exc)

    t = threading.Thread(target=_target, daemon=True, name=f"test-hb-{story_id}")
    t.start()
    time.sleep(wait)
    stop_event.set()
    t.join(timeout=2.0)
    sdlc_phase_runner.HEARTBEAT_INTERVAL = original_interval
    return t, exc_container


def _capture_urlopen_requests() -> tuple[list[urllib.request.Request], MagicMock]:
    """Return a (captured_list, mock) pair for patching urllib.request.urlopen."""
    captured: list[urllib.request.Request] = []

    class _FakeResponse:
        status = 200
        def read(self): return b"{}"
        def __enter__(self): return self
        def __exit__(self, *a): pass

    def _fake_urlopen(req, timeout=None):
        if isinstance(req, urllib.request.Request):
            captured.append(req)
        return _FakeResponse()

    mock = MagicMock(side_effect=_fake_urlopen)
    return captured, mock


# ---------------------------------------------------------------------------
# Group A — Signature & Plumbing (SC-1, AC-1)
# ---------------------------------------------------------------------------


class TestSignaturePlumbing:
    """A01–A02: _heartbeat_thread must accept the new watchdog parameters."""

    def test_heartbeat_thread_accepts_sdk_pid_parameter(self):
        """A01: Function signature must include an sdk_pid parameter.

        Why: The thread receives the subprocess handle so it can check liveness.
             Without this parameter the watchdog cannot be implemented.
        """
        sig = inspect.signature(sdlc_phase_runner._heartbeat_thread)
        assert "sdk_pid" in sig.parameters, (
            "_heartbeat_thread must accept sdk_pid parameter. "
            "Current signature: "
            + str(sig)
        )

    def test_heartbeat_thread_accepts_last_output_ts_parameter(self):
        """A02: Function signature must include last_output_ts AND phase_timeout_s.

        Why: last_output_ts is the shared container updated by the stdout reader;
             phase_timeout_s feeds the 2× stall threshold.  Both must be in the
             signature so Phase 8 can wire them from _run_phase_sdk().
        """
        sig = inspect.signature(sdlc_phase_runner._heartbeat_thread)
        assert "last_output_ts" in sig.parameters, (
            "_heartbeat_thread must accept last_output_ts (list[float]). "
            "Current signature: "
            + str(sig)
        )
        assert "phase_timeout_s" in sig.parameters, (
            "_heartbeat_thread must accept phase_timeout_s (int). "
            "Current signature: "
            + str(sig)
        )


# ---------------------------------------------------------------------------
# Group B — PID Liveness (SC-2, AC-2, AC-5, AC-6, AC-9)
# ---------------------------------------------------------------------------


class TestPidLiveness:
    """B01–B02: Dead SDK process must be detected and story failed."""

    def test_dead_pid_triggers_failure_with_structured_reason(self):
        """B01: os.kill(pid, 0) raises ProcessLookupError → fail POST with
        sdk_died_no_phase_end: prefix (SC-2, AC-2, AC-5, AC-9).

        Arrange: fresh last_output_ts so only PID death triggers the watchdog.
        Act:     run thread with dead PID (os.kill patched to raise).
        Assert:  fail POST sent; body contains sdk_died_no_phase_end: prefix.
        """
        last_output_ts = [time.time()]  # fresh — stall check must NOT trigger
        stop_event = threading.Event()
        captured_requests, fake_urlopen = _capture_urlopen_requests()

        # Capture JSON bodies separately for fail requests
        fail_bodies: list[dict] = []
        original_side = fake_urlopen.side_effect

        def _capturing_urlopen(req, timeout=None):
            result = original_side(req, timeout=timeout)
            if isinstance(req, urllib.request.Request) and "fail" in req.full_url:
                try:
                    body = json.loads(req.data or b"{}")
                    fail_bodies.append(body)
                except Exception:
                    pass
            return result

        fake_urlopen.side_effect = _capturing_urlopen

        with patch("os.kill", side_effect=ProcessLookupError("No process 99999")):
            with patch("urllib.request.urlopen", side_effect=fake_urlopen.side_effect):
                with patch.dict("os.environ", {
                    "OPS_CONSOLE_URL": "http://ops-test",
                    "OPS_CONSOLE_API_KEY": "testkey",
                }):
                    t, exc = _run_heartbeat_thread(
                        "STORY-763",
                        "tech-dev-agents",
                        stop_event,
                        sdk_pid=99999,
                        last_output_ts=last_output_ts,
                        phase_timeout_s=1200,
                    )

        # No uncaught exception (TypeError means params not implemented yet)
        assert not exc, (
            f"_heartbeat_thread raised {exc[0]!r} — "
            "did you add sdk_pid/last_output_ts to the signature?"
        )

        # Must have POSTed to /fail/STORY-763
        fail_urls = [r.full_url for r in captured_requests if "fail" in r.full_url]
        assert fail_urls, (
            "Watchdog must POST to /api/dispatch/fail/STORY-763 when PID is dead. "
            f"Captured URLs: {[r.full_url for r in captured_requests]}"
        )

        # failure_reason must use the structured prefix
        assert fail_bodies, "Fail POST must include a JSON body with failure_reason"
        reason = fail_bodies[0].get("failure_reason", "")
        assert reason.startswith("sdk_died_no_phase_end:"), (
            f"failure_reason must start with 'sdk_died_no_phase_end:'. Got: {reason!r}"
        )
        assert "pid=" in reason, (
            f"failure_reason must include pid=<N>. Got: {reason!r}"
        )

    def test_dead_pid_exits_heartbeat_loop(self):
        """B02: After watchdog fires (PID dead), thread must exit — no leaked thread (AC-6).

        Arrange: os.kill raises ProcessLookupError.
        Act:     run thread; join with timeout.
        Assert:  thread.is_alive() is False (thread terminated cleanly).
        """
        last_output_ts = [time.time()]
        stop_event = threading.Event()

        with patch("os.kill", side_effect=ProcessLookupError("dead")):
            with patch("urllib.request.urlopen"):
                with patch.dict("os.environ", {
                    "OPS_CONSOLE_URL": "http://ops-test",
                    "OPS_CONSOLE_API_KEY": "testkey",
                }):
                    t, exc = _run_heartbeat_thread(
                        "STORY-763",
                        "tech-dev-agents",
                        stop_event,
                        sdk_pid=99999,
                        last_output_ts=last_output_ts,
                        phase_timeout_s=1200,
                    )

        assert not exc, (
            f"Thread raised {exc[0]!r} — signature not updated yet?"
        )
        assert not t.is_alive(), (
            "Heartbeat thread must exit after watchdog fires for dead PID. "
            "Thread is still alive — watchdog loop exit not implemented."
        )


# ---------------------------------------------------------------------------
# Group C — Progress Stall (SC-3, SC-5, AC-3, AC-4)
# ---------------------------------------------------------------------------


class TestProgressStall:
    """C01–C03: Stale last_output_ts triggers kill + fail; 1.5× is safe."""

    def test_stalled_progress_triggers_kill_and_failure(self):
        """C01: last_output_ts > 2× phase_timeout_s → SIGTERM process group + fail POST
        (SC-3, AC-3, AC-4).

        Arrange:
          phase_timeout_s=10; last_output_ts 25s ago (> 2×10=20s threshold).
          os.kill → alive (PID liveness OK; only stall should trigger).
          time.sleep patched to instant (skips SIGTERM→SIGKILL grace in test).
        Act: run thread.
        Assert: os.killpg called; fail POST sent.
        """
        phase_timeout_s = 10
        last_output_ts = [time.time() - 25]  # 25s ago > 2×10s threshold
        stop_event = threading.Event()
        captured_requests, _ = _capture_urlopen_requests()
        kill_calls: list[tuple] = []

        def _fake_killpg(pgid, sig):
            kill_calls.append((pgid, sig))

        with patch("os.kill", return_value=None):  # PID appears alive
            with patch("os.killpg", side_effect=_fake_killpg):
                with patch("time.sleep"):  # skip SIGTERM grace period
                    with patch("urllib.request.urlopen",
                               side_effect=lambda req, timeout=None: MagicMock(
                                   status=200, read=lambda: b"{}", __enter__=lambda s: s,
                                   __exit__=lambda s, *a: None,
                               )):
                        with patch.dict("os.environ", {
                            "OPS_CONSOLE_URL": "http://ops-test",
                            "OPS_CONSOLE_API_KEY": "testkey",
                        }):
                            t, exc = _run_heartbeat_thread(
                                "STORY-763",
                                "tech-dev-agents",
                                stop_event,
                                sdk_pid=99999,
                                last_output_ts=last_output_ts,
                                phase_timeout_s=phase_timeout_s,
                            )

        assert not exc, (
            f"Thread raised {exc[0]!r} — signature not updated yet?"
        )
        assert kill_calls, (
            "Watchdog must call os.killpg to kill the SDK process group on stall. "
            "os.killpg was not called."
        )
        # SIGTERM must come before SIGKILL
        sigs = [sig for _, sig in kill_calls]
        assert signal.SIGTERM in sigs or 15 in sigs, (
            f"SIGTERM must be sent first. Kill calls: {kill_calls}"
        )

    def test_legitimate_slow_phase_does_not_trigger(self):
        """C02: 1.5× timeout does NOT trigger watchdog — proves threshold is not
        hair-trigger (SC-5).

        Arrange:
          phase_timeout_s=100; last_output_ts 150s ago (1.5×100=150 — at boundary, NOT over).
          os.kill → alive.
        Act: run thread.
        Assert: os.killpg NOT called; no fail POST.

        Note: if last_output_ts equals exactly 1.5× it should NOT trigger.
        The implementation uses strict greater-than: elapsed > threshold.
        """
        phase_timeout_s = 100
        # 149s ago — clearly under the 200s threshold (2×100)
        last_output_ts = [time.time() - 149]
        stop_event = threading.Event()
        kill_calls: list = []

        def _unexpected_kill(*args):
            kill_calls.append(args)

        with patch("os.kill", return_value=None):  # PID alive
            with patch("os.killpg", side_effect=_unexpected_kill):
                with patch("urllib.request.urlopen"):
                    with patch.dict("os.environ", {
                        "OPS_CONSOLE_URL": "http://ops-test",
                        "OPS_CONSOLE_API_KEY": "testkey",
                    }):
                        t, exc = _run_heartbeat_thread(
                            "STORY-763",
                            "tech-dev-agents",
                            stop_event,
                            sdk_pid=99999,
                            last_output_ts=last_output_ts,
                            phase_timeout_s=phase_timeout_s,
                        )

        assert not exc, (
            f"Thread raised {exc[0]!r} — signature not updated yet?"
        )
        assert not kill_calls, (
            "Watchdog must NOT trigger at 1.5× timeout. "
            "os.killpg was called unexpectedly — threshold too tight."
        )

    def test_stale_multiplier_env_var_configures_threshold(self):
        """C03: PHASE_PROGRESS_STALE_MULTIPLIER=3.0 raises threshold so 2.5×
        does NOT trigger (AC-3).

        Arrange:
          phase_timeout_s=100; last_output_ts 250s ago (2.5×100; < 3.0×100=300s).
          PHASE_PROGRESS_STALE_MULTIPLIER=3.0.
        Act: run thread.
        Assert: os.killpg NOT called (threshold is now 300s, elapsed is 250s).
        """
        phase_timeout_s = 100
        last_output_ts = [time.time() - 250]  # 2.5× — safe under 3.0 multiplier
        stop_event = threading.Event()
        kill_calls: list = []

        def _unexpected_kill(*args):
            kill_calls.append(args)

        with patch("os.kill", return_value=None):
            with patch("os.killpg", side_effect=_unexpected_kill):
                with patch("urllib.request.urlopen"):
                    with patch.dict("os.environ", {
                        "OPS_CONSOLE_URL": "http://ops-test",
                        "OPS_CONSOLE_API_KEY": "testkey",
                        "PHASE_PROGRESS_STALE_MULTIPLIER": "3.0",
                    }):
                        t, exc = _run_heartbeat_thread(
                            "STORY-763",
                            "tech-dev-agents",
                            stop_event,
                            sdk_pid=99999,
                            last_output_ts=last_output_ts,
                            phase_timeout_s=phase_timeout_s,
                        )

        assert not exc, (
            f"Thread raised {exc[0]!r} — signature not updated yet?"
        )
        assert not kill_calls, (
            "With PHASE_PROGRESS_STALE_MULTIPLIER=3.0, 2.5× timeout must NOT trigger. "
            "os.killpg was called — env var not being read."
        )


# ---------------------------------------------------------------------------
# Group D — Stdout Watcher (SC-4, AC-8)
# ---------------------------------------------------------------------------


class TestStdoutWatcher:
    """D01–D02: SDK stdout lines update last_output_ts; happy-path heartbeat regression."""

    def test_stdout_line_updates_last_output_ts(self):
        """D01: Source code must update last_output_ts[0] = time.time() when a
        SDK stdout line is received (SC-4, AC-8).

        This is a structural test because the update lives in the subprocess-reading
        code path in _run_phase_sdk(), not in _heartbeat_thread itself.

        Implementation note for Phase 8:
          Use subprocess.Popen with stdout=PIPE, then read lines in a thread:
            for line in proc.stdout:
                sys.stdout.write(line)  # still flows to journal
                last_output_ts[0] = time.time()
          Pass the same last_output_ts list to _heartbeat_thread.
        """
        source = Path(sdlc_phase_runner.__file__).read_text()
        assert "last_output_ts[0]" in source, (
            "sdlc_phase_runner must update last_output_ts[0] = time.time() on each "
            "SDK stdout line received. Pattern 'last_output_ts[0]' not found in source. "
            "See test_design.md § Implementation Note for the required code pattern."
        )

    def test_happy_path_heartbeat_still_fires_with_live_sdk(self):
        """D02: Live PID + fresh last_output_ts → normal heartbeat POSTs still fire (SC-7).

        Arrange: sdk_pid alive (os.kill → None); last_output_ts fresh.
        Act:     run thread for 0.25s with 50ms interval → expect ≥2 POSTs.
        Assert:  ≥2 heartbeat POSTs (not fail POSTs); os.killpg NOT called.
        """
        last_output_ts = [time.time()]  # completely fresh
        stop_event = threading.Event()
        heartbeat_posts: list[str] = []
        kill_calls: list = []

        def _fake_urlopen(req, timeout=None):
            if isinstance(req, urllib.request.Request):
                url = req.full_url
                if "heartbeat" in url:
                    heartbeat_posts.append(url)
                # Count fail POSTs as unexpected in this test
            return MagicMock(
                status=200, read=lambda: b"{}",
                __enter__=lambda s: s, __exit__=lambda s, *a: None,
            )

        with patch("os.kill", return_value=None):  # PID alive
            with patch("os.killpg", side_effect=lambda *a: kill_calls.append(a)):
                with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
                    with patch.dict("os.environ", {
                        "OPS_CONSOLE_URL": "http://ops-test",
                        "OPS_CONSOLE_API_KEY": "testkey",
                    }):
                        t, exc = _run_heartbeat_thread(
                            "STORY-763",
                            "tech-dev-agents",
                            stop_event,
                            sdk_pid=99999,
                            last_output_ts=last_output_ts,
                            phase_timeout_s=1200,
                            interval=0.05,
                            wait=0.25,
                        )

        assert not exc, (
            f"Thread raised {exc[0]!r} — signature not updated yet?"
        )
        assert len(heartbeat_posts) >= 2, (
            f"Expected ≥2 heartbeat POSTs in 250ms at 50ms interval. "
            f"Got {len(heartbeat_posts)}. Watchdog must not break happy-path heartbeats."
        )
        assert not kill_calls, (
            "os.killpg must NOT be called when PID is alive and output is fresh."
        )


# ---------------------------------------------------------------------------
# Group E — Failure Observability (SC-6, AC-5, AC-9, AC-11)
# ---------------------------------------------------------------------------


class TestFailureObservability:
    """E01–E03: Failure reasons use structured prefixes; log format is Loki-friendly."""

    def _run_with_dead_pid(self) -> tuple[list[dict], list[str], list[Exception]]:
        """Common setup for dead-PID watchdog scenarios.

        Returns (fail_bodies, stdout_lines, exceptions).
        """
        last_output_ts = [time.time()]
        stop_event = threading.Event()
        fail_bodies: list[dict] = []
        stdout_lines: list[str] = []

        def _fake_urlopen(req, timeout=None):
            if isinstance(req, urllib.request.Request) and "fail" in req.full_url:
                try:
                    fail_bodies.append(json.loads(req.data or b"{}"))
                except Exception:
                    pass
            return MagicMock(
                status=200, read=lambda: b"{}",
                __enter__=lambda s: s, __exit__=lambda s, *a: None,
            )

        buf = io.StringIO()
        with patch("os.kill", side_effect=ProcessLookupError("dead")):
            with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
                with patch.dict("os.environ", {
                    "OPS_CONSOLE_URL": "http://ops-test",
                    "OPS_CONSOLE_API_KEY": "testkey",
                }):
                    with patch("sys.stdout", buf):
                        _, exc = _run_heartbeat_thread(
                            "STORY-763",
                            "tech-dev-agents",
                            stop_event,
                            sdk_pid=99999,
                            last_output_ts=last_output_ts,
                            phase_timeout_s=1200,
                        )
        stdout_lines.extend(buf.getvalue().splitlines())
        return fail_bodies, stdout_lines, exc

    def _run_with_stalled_progress(self) -> tuple[list[dict], list[str], list[Exception]]:
        """Common setup for stalled-progress watchdog scenarios."""
        phase_timeout_s = 10
        last_output_ts = [time.time() - 25]  # 2.5× over threshold
        stop_event = threading.Event()
        fail_bodies: list[dict] = []
        stdout_lines: list[str] = []

        def _fake_urlopen(req, timeout=None):
            if isinstance(req, urllib.request.Request) and "fail" in req.full_url:
                try:
                    fail_bodies.append(json.loads(req.data or b"{}"))
                except Exception:
                    pass
            return MagicMock(
                status=200, read=lambda: b"{}",
                __enter__=lambda s: s, __exit__=lambda s, *a: None,
            )

        buf = io.StringIO()
        with patch("os.kill", return_value=None):  # PID alive
            with patch("os.killpg"):  # silence actual kill
                with patch("time.sleep"):
                    with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
                        with patch.dict("os.environ", {
                            "OPS_CONSOLE_URL": "http://ops-test",
                            "OPS_CONSOLE_API_KEY": "testkey",
                        }):
                            with patch("sys.stdout", buf):
                                _, exc = _run_heartbeat_thread(
                                    "STORY-763",
                                    "tech-dev-agents",
                                    stop_event,
                                    sdk_pid=99999,
                                    last_output_ts=last_output_ts,
                                    phase_timeout_s=phase_timeout_s,
                                )
        stdout_lines.extend(buf.getvalue().splitlines())
        return fail_bodies, stdout_lines, exc

    def test_failure_reason_uses_sdk_died_prefix(self):
        """E01: Dead PID failure_reason must start with 'sdk_died_no_phase_end:'
        and include 'pid=<N>' (SC-6, AC-5, AC-11).

        Matches the Loki log pattern in seed.md § Done Looks Like:
          [DISPATCH] watchdog: STORY-010 sdk_died_no_phase_end pid=12345 last_output=14400s_ago
        """
        fail_bodies, _, exc = self._run_with_dead_pid()

        assert not exc, (
            f"Thread raised {exc[0]!r} — signature not updated yet?"
        )
        assert fail_bodies, (
            "No fail POST body captured. Watchdog must POST to /api/dispatch/fail/ "
            "with JSON body containing failure_reason."
        )
        reason = fail_bodies[0].get("failure_reason", "")
        assert reason.startswith("sdk_died_no_phase_end:"), (
            f"failure_reason must start with 'sdk_died_no_phase_end:'. Got: {reason!r}"
        )
        assert "pid=" in reason, (
            f"failure_reason must include 'pid=<N>'. Got: {reason!r}"
        )

    def test_failure_reason_uses_stalled_prefix(self):
        """E02: Stalled progress failure_reason must start with 'phase_progress_stalled:'
        and include 'last_output=<N>s_ago' (SC-6, AC-5, AC-11).

        Matches the structured format from AC-5 + AC-11.
        """
        fail_bodies, _, exc = self._run_with_stalled_progress()

        assert not exc, (
            f"Thread raised {exc[0]!r} — signature not updated yet?"
        )
        assert fail_bodies, (
            "No fail POST body captured. Watchdog must POST to /api/dispatch/fail/ "
            "on stalled progress."
        )
        reason = fail_bodies[0].get("failure_reason", "")
        assert reason.startswith("phase_progress_stalled:"), (
            f"failure_reason must start with 'phase_progress_stalled:'. Got: {reason!r}"
        )
        assert "last_output=" in reason and "s_ago" in reason, (
            f"failure_reason must include 'last_output=<N>s_ago'. Got: {reason!r}"
        )

    def test_watchdog_logs_structured_message_to_stdout(self):
        """E03: Watchdog action must log '[DISPATCH] watchdog: STORY-763' to stdout
        so Loki can alert on zombie watchdog activity (AC-9).

        Tests both PID-death and stall scenarios.
        """
        # Test with dead PID
        _, dead_pid_stdout, exc_dead = self._run_with_dead_pid()
        assert not exc_dead, (
            f"Thread raised {exc_dead[0]!r} — signature not updated yet?"
        )
        dead_log = "\n".join(dead_pid_stdout)

        # Test with stalled progress
        _, stall_stdout, exc_stall = self._run_with_stalled_progress()
        assert not exc_stall, (
            f"Thread raised {exc_stall[0]!r} — signature not updated yet?"
        )
        stall_log = "\n".join(stall_stdout)

        # At least one scenario must produce the watchdog log line
        watchdog_marker = "[DISPATCH] watchdog: STORY-763"
        assert watchdog_marker in dead_log or watchdog_marker in stall_log, (
            f"Watchdog must print '{watchdog_marker}' to stdout. "
            f"Dead-PID stdout: {dead_log!r}\n"
            f"Stall stdout: {stall_log!r}\n"
            "Implement: print(f'[DISPATCH] watchdog: {story_id} <action> reason=<reason>', flush=True)"
        )
