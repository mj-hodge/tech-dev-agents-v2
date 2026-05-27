"""SDK progress watchdog — kill stalled SDK runs and release the lease.

STORY-2026-05-25: the fleet wedged because long-running SDK subprocesses
stopped producing output (silent stalls) while the lease kept ticking,
and in some cases the lease was lost (409) but the SDK happily kept
burning tokens until the 2h subprocess.communicate timeout fired. The
StuckAgentWatcher reclaim path then re-fired the same poison stories on
every agent in turn.

This module is a daemon thread launched alongside the lease-heartbeat
thread inside ``_run_sdk``. On every tick (default 15s) it checks four
independent signals and, if any trip, it:

  1. SIGTERMs the SDK process group, waits ``grace_seconds`` (default 10s),
     then SIGKILLs.
  2. Calls ``release_claim`` with ``reason=watchdog_<trip>``.
  3. Clears the active-lease state file via ``_clear_active_lease``
     (passed in by the caller — the watchdog must not import poller
     internals at module import time).
  4. Emits a ``watchdog_fired`` log line with the trip name + measured
     value so the dispatch failure classifier picks one of the four
     non-retryable classes:

       - ``watchdog_progress_stall`` — silence_max_seconds exceeded
       - ``watchdog_lease_lost``     — heartbeat 409 seen
       - ``watchdog_oom_guard``      — rss_max_mb exceeded
       - ``watchdog_turn_max``       — turn_max_seconds exceeded (wall clock)

Non-retryable classification is set in
``tech_dev_agents/ops_console/services/dispatch_failure_policy.py`` so a
poison story does not re-fire across the fleet.

Design contract
---------------

  * The watchdog is constructed with an already-running ``Popen`` and a
    ``threading.Event`` that the heartbeat thread sets on 409.
  * The caller owns ``last_output_ts`` — a single-element ``list[float]``
    holding the most recent monotonic timestamp at which an
    ``[ASSISTANT]`` or ``[TOOL_RESULT]`` line was observed on the SDK's
    stdout. The caller updates this list from its stdout tailer.
  * The watchdog never touches stdout — it only reads ``last_output_ts``.
    This keeps it decoupled from the existing ``proc.communicate``
    pattern in ``_run_sdk``; we can wire the tailer in a follow-up
    without changing the watchdog contract.
  * ``psutil`` is preferred for RSS measurement; if unavailable, we fall
    back to ``/proc/<pid>/status`` VmRSS parsing (sum across the process
    tree via ``/proc/<pid>/task/*/children``).
  * All trip paths are robust against ``ProcessLookupError`` and
    ``PermissionError`` — a missing process means "already gone" and is
    treated as a successful kill.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
import time
from typing import Any, Callable

logger = logging.getLogger("sdk_progress_watchdog")

# ---------------------------------------------------------------------------
# Defaults — tunable per-call but spelled out as module constants so tests
# and operators can find them by grep.
# ---------------------------------------------------------------------------

DEFAULT_TURN_MAX_SECONDS = 2400          # 40 min wall clock
# Silence trip is DISABLED by default (0 = off). The watchdog reads
# last_output_ts[0] but nothing in the poller updates it yet — until a
# stdout tailer is wired, a non-zero silence_max would false-trip every
# job that takes longer than the threshold. Set SDK_WATCHDOG_SILENCE_MAX_S
# > 0 only when the tailer lands.
DEFAULT_SILENCE_MAX_SECONDS = 0
DEFAULT_RSS_MAX_MB = 3500                # ~3.5 GB tree-wide
DEFAULT_TICK_SECONDS = 15                # poll cadence
DEFAULT_GRACE_SECONDS = 10               # SIGTERM → SIGKILL grace

TRIP_PROGRESS_STALL = "watchdog_progress_stall"
TRIP_LEASE_LOST = "watchdog_lease_lost"
TRIP_OOM_GUARD = "watchdog_oom_guard"
TRIP_TURN_MAX = "watchdog_turn_max"


# ---------------------------------------------------------------------------
# RSS measurement — psutil preferred, /proc fallback for slim VMs.
# ---------------------------------------------------------------------------


def _rss_mb_psutil(pid: int) -> float | None:
    """Return total RSS (MB) of the process tree using psutil, or None."""
    try:
        import psutil  # type: ignore
    except ImportError:
        return None
    try:
        proc = psutil.Process(pid)
        total = proc.memory_info().rss
        for child in proc.children(recursive=True):
            try:
                total += child.memory_info().rss
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return total / (1024 * 1024)
    except Exception:  # psutil.NoSuchProcess, AccessDenied, etc.
        return None


def _rss_mb_proc(pid: int) -> float | None:
    """Return total RSS (MB) via /proc parsing. Fallback when psutil missing.

    Walks /proc/<pid>/task/*/children to discover the subtree, then sums
    VmRSS from each /proc/<descendant>/status. Best-effort: missing files
    or unreadable entries are skipped silently.
    """
    def _vm_rss_kb(p: int) -> int:
        try:
            with open(f"/proc/{p}/status", "r", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("VmRSS:"):
                        # "VmRSS:    12345 kB"
                        parts = line.split()
                        if len(parts) >= 2 and parts[1].isdigit():
                            return int(parts[1])
        except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
            return 0
        return 0

    def _walk(p: int, seen: set[int]) -> list[int]:
        if p in seen:
            return []
        seen.add(p)
        result = [p]
        try:
            task_dir = f"/proc/{p}/task"
            for tid in os.listdir(task_dir):
                child_file = f"{task_dir}/{tid}/children"
                try:
                    with open(child_file, "r", encoding="utf-8") as fh:
                        for child_pid_str in fh.read().split():
                            try:
                                child_pid = int(child_pid_str)
                            except ValueError:
                                continue
                            result.extend(_walk(child_pid, seen))
                except (FileNotFoundError, PermissionError, OSError):
                    continue
        except (FileNotFoundError, PermissionError, OSError):
            return result
        return result

    try:
        tree = _walk(pid, set())
    except RecursionError:
        return None
    if not tree:
        return None
    total_kb = sum(_vm_rss_kb(p) for p in tree)
    return total_kb / 1024.0


def measure_rss_mb(pid: int) -> float | None:
    """Measure total RSS (MB) for a process tree.

    Tries psutil first, falls back to /proc parsing. Returns None if both
    paths fail (e.g. process is already gone, or non-Linux without psutil).
    """
    val = _rss_mb_psutil(pid)
    if val is not None:
        return val
    return _rss_mb_proc(pid)


# ---------------------------------------------------------------------------
# Kill helper — SIGTERM → grace → SIGKILL on the process group.
# ---------------------------------------------------------------------------


def kill_process_group(
    pid: int,
    *,
    grace_seconds: float = DEFAULT_GRACE_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Kill a process group: SIGTERM → grace → SIGKILL.

    ``pid`` must have been launched with ``start_new_session=True`` so it
    owns a process group; if ``getpgid`` fails we fall back to using
    ``pid`` itself as the group id, matching the STORY-763 pattern in
    ``sdlc_phase_runner.py``.

    All signal calls are wrapped to swallow ``ProcessLookupError`` and
    ``PermissionError`` — a missing process is the desired end state.

    ``sleep`` is injected so tests can patch it without monkeypatching the
    module global.
    """
    try:
        pgid = os.getpgid(pid)
    except (ProcessLookupError, PermissionError, OSError):
        pgid = pid

    try:
        os.killpg(pgid, signal.SIGTERM)
        logger.warning(
            "sdk_progress_watchdog: SIGTERM sent pid=%s pgid=%s", pid, pgid
        )
    except (ProcessLookupError, PermissionError) as exc:
        logger.info(
            "sdk_progress_watchdog: SIGTERM no-op pid=%s pgid=%s err=%s",
            pid, pgid, exc,
        )

    sleep(grace_seconds)

    try:
        os.killpg(pgid, signal.SIGKILL)
        logger.warning(
            "sdk_progress_watchdog: SIGKILL sent pid=%s pgid=%s", pid, pgid
        )
    except (ProcessLookupError, PermissionError):
        # Expected when SIGTERM already finished the job.
        pass


# ---------------------------------------------------------------------------
# ProgressWatchdog
# ---------------------------------------------------------------------------


class ProgressWatchdog:
    """Daemon-thread watchdog for an SDK subprocess.

    Parameters
    ----------
    proc : subprocess.Popen
        The running SDK subprocess. Must have been launched with
        ``start_new_session=True`` so ``killpg`` reaches descendants.
    claim : Any
        The active claim object (carries ``job_id``, ``lease_token``).
        Opaque to the watchdog — passed straight to ``release_fn``.
    last_output_ts : list[float]
        Single-element list with the monotonic timestamp of the most
        recent ASSISTANT / TOOL_RESULT line. The caller's stdout tailer
        is responsible for updating ``last_output_ts[0]``. The watchdog
        only reads.
    lease_lost_event : threading.Event
        Set by the heartbeat thread when a 409 stale-lease response is
        seen. When set, the watchdog trips ``watchdog_lease_lost``.
    session, headers : Any
        Passed to ``release_fn`` so the release call carries the agent's
        auth headers.
    release_fn : callable
        Function with signature compatible with
        ``release_claim(claim, *, session, headers, reason)``. Injected
        rather than imported so tests don't need to import the poller.
    clear_lease_fn : callable | None
        Optional function with signature ``() -> None`` called after a
        successful release to remove the on-disk active-lease state.
    thresholds : dict | None
        Optional overrides. Recognised keys (all optional):
          - turn_max_seconds
          - silence_max_seconds
          - rss_max_mb
          - tick_seconds
          - grace_seconds

    Lifecycle
    ---------
    Call ``.start()`` to launch the daemon thread. The thread exits
    automatically when:
      * The subprocess has exited (``proc.poll() is not None``).
      * ``.stop()`` is called and the internal stop event fires.
      * The watchdog fires a trip (after kill + release).
    """

    def __init__(
        self,
        proc: subprocess.Popen,
        claim: Any,
        last_output_ts: list[float],
        lease_lost_event: threading.Event,
        *,
        session: Any,
        headers: Any,
        release_fn: Callable[..., None],
        clear_lease_fn: Callable[[], None] | None = None,
        thresholds: dict[str, Any] | None = None,
    ) -> None:
        self._proc = proc
        self._claim = claim
        self._last_output_ts = last_output_ts
        self._lease_lost_event = lease_lost_event
        self._session = session
        self._headers = headers
        self._release_fn = release_fn
        self._clear_lease_fn = clear_lease_fn

        t = thresholds or {}
        self._turn_max = float(t.get("turn_max_seconds", DEFAULT_TURN_MAX_SECONDS))
        self._silence_max = float(
            t.get("silence_max_seconds", DEFAULT_SILENCE_MAX_SECONDS)
        )
        self._rss_max_mb = float(t.get("rss_max_mb", DEFAULT_RSS_MAX_MB))
        self._tick = float(t.get("tick_seconds", DEFAULT_TICK_SECONDS))
        self._grace = float(t.get("grace_seconds", DEFAULT_GRACE_SECONDS))

        self._stop_event = threading.Event()
        self._fired = False
        self._fired_reason: str | None = None
        self._thread: threading.Thread | None = None
        self._started_at = time.monotonic()

    # -- public API ----------------------------------------------------

    def start(self) -> None:
        """Launch the daemon thread. Idempotent."""
        if self._thread is not None:
            return
        self._started_at = time.monotonic()
        self._thread = threading.Thread(
            target=self._run,
            name="sdk-progress-watchdog",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Signal graceful exit. Does not block on the thread."""
        self._stop_event.set()

    @property
    def fired(self) -> bool:
        return self._fired

    @property
    def fired_reason(self) -> str | None:
        return self._fired_reason

    # -- internals -----------------------------------------------------

    def _check_trip(self) -> str | None:
        """Return the trip reason if any signal fires, else None."""
        # Lease lost is fastest to check and most decisive — short-circuit.
        if self._lease_lost_event.is_set():
            return TRIP_LEASE_LOST

        now = time.monotonic()

        # Wall-clock turn max.
        elapsed = now - self._started_at
        if elapsed > self._turn_max:
            logger.warning(
                "sdk_progress_watchdog: turn_max trip elapsed=%.0fs threshold=%.0fs",
                elapsed, self._turn_max,
            )
            return TRIP_TURN_MAX

        # Silence (no ASSISTANT/TOOL_RESULT output). Disabled when
        # silence_max <= 0 — needed until a stdout tailer updates
        # last_output_ts[0] (otherwise every long-running healthy job
        # would false-trip).
        if (
            self._silence_max > 0
            and self._last_output_ts
            and self._last_output_ts[0] > 0
        ):
            silence = now - self._last_output_ts[0]
            if silence > self._silence_max:
                logger.warning(
                    "sdk_progress_watchdog: silence trip silence=%.0fs threshold=%.0fs",
                    silence, self._silence_max,
                )
                return TRIP_PROGRESS_STALL

        # RSS guard — best-effort; None means "couldn't measure", not "OK".
        rss_mb = measure_rss_mb(self._proc.pid)
        if rss_mb is not None and rss_mb > self._rss_max_mb:
            logger.warning(
                "sdk_progress_watchdog: rss trip rss_mb=%.0f threshold=%.0f",
                rss_mb, self._rss_max_mb,
            )
            return TRIP_OOM_GUARD

        return None

    def _fire(self, reason: str) -> None:
        """Kill the SDK, release the claim, clear lease state, mark fired."""
        self._fired = True
        self._fired_reason = reason
        logger.error(
            "sdk_progress_watchdog: watchdog_fired reason=%s job_id=%s",
            reason, getattr(self._claim, "job_id", "<unknown>"),
        )

        # 1. Kill the process group (SIGTERM → grace → SIGKILL).
        pid = self._proc.pid
        try:
            if self._proc.poll() is None:
                kill_process_group(pid, grace_seconds=self._grace)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "sdk_progress_watchdog: kill_process_group failed pid=%s err=%s",
                pid, exc,
            )

        # 2. Release the lease back to the queue.
        try:
            self._release_fn(
                self._claim,
                session=self._session,
                headers=self._headers,
                reason=reason,
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "sdk_progress_watchdog: release_fn failed reason=%s err=%s",
                reason, exc,
            )

        # 3. Clear on-disk lease state (best-effort).
        if self._clear_lease_fn is not None:
            try:
                self._clear_lease_fn()
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(
                    "sdk_progress_watchdog: clear_lease_fn failed err=%s", exc
                )

    def _run(self) -> None:
        """Watch loop. Exits on SDK exit, stop_event, or trip."""
        while not self._stop_event.wait(self._tick):
            # SDK already exited normally — nothing to do.
            try:
                if self._proc.poll() is not None:
                    return
            except Exception:  # pragma: no cover - defensive
                return

            try:
                reason = self._check_trip()
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(
                    "sdk_progress_watchdog: _check_trip raised — continuing: %s",
                    exc,
                )
                continue

            if reason is not None:
                self._fire(reason)
                return
