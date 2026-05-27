"""Dispatch queue polling loop — agent-side auto-pickup daemon.

STORY-027: Dispatch Queue Auto-Pickup
STORY-040: Fix Stale Local Work Queue (reconciliation, PID liveness, file locking)

Background daemon thread that polls GET /api/dispatch/next every N seconds
when the agent is idle. On receiving a story, claims it via POST /api/dispatch/claim/{story_id},
adds to local WorkQueue, and starts claude_sdk_tool.py.

Environment variables:
  OPS_CONSOLE_URL       — Base URL of ops console API (required)
  OPS_CONSOLE_API_KEY   — API key for auth (required)
  DISPATCH_POLL_INTERVAL — Seconds between polls (default: 60)
  AGENT_NAME            — Agent name for claim requests (required)
  AGENT_WORKSPACE       — Default workdir for SDK (default: /home/hermes/workspace)
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import re
import subprocess
import sys
import time

import requests

logger = logging.getLogger("dispatch_poller")

# STORY-723 H-1: import _extract_story_folder at module level with guard.
# sdlc_phase_runner may not be on sys.path in all deployment layouts.
try:
    from sdlc_phase_runner import _extract_story_folder
except ImportError:
    _extract_story_folder = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SDK_TOOL_PATH = "/opt/agent/claude_sdk_tool.py"
PYTHON_PATH = sys.executable or "python3"
RECONCILE_INTERVAL = 300  # 5 minutes — safety net for stale queue entries
_COMPLETION_TRACK_PATH = os.path.expanduser("~/.hermes/dispatch-completed-stories.json")

# STORY-772/STORY-725: Phase Path pattern shared with sdlc_phase_runner.
_DISPATCH_PHASE_PATH_PATTERN = re.compile(
    r'(?im)^\s*\|?\s*Phase\s+Path\s*:?\s*\|?\s*([^\|]+?)\s*\|?\s*$'
)


def parse_seed_phase_path(seed_path: str) -> "list[str] | None":
    """Read seed.md from seed_path and return the Phase Path token list.

    STORY-725/STORY-772: File-path variant for use at dispatch time.
    The sdlc_phase_runner variant operates on seed text directly; this variant
    takes a file path and handles I/O errors with an observable WARNING log.

    Returns:
      - list[str]: raw phase tokens from the Phase Path line
        (e.g., ['1', '7', '8', 'Done']). 'Done' is NOT stripped here.
      - None + WARNING log: if file cannot be read (misconfigured worktree).
      - None (no warning): if file is readable but has no Phase Path line.
    """
    try:
        with open(seed_path, "r") as f:
            seed_text = f.read()
    except (OSError, IOError) as exc:
        logger.warning(
            "parse_seed_phase_path: cannot read seed at %r: %s — falling back to scope default",
            seed_path, exc,
        )
        return None

    m = _DISPATCH_PHASE_PATH_PATTERN.search(seed_text)
    if not m:
        return None  # No Phase Path line — not an error, caller uses scope default

    raw = m.group(1)
    # Extract phase identifiers: digit+optional suffix (6b, 8b, etc.) OR literal 'Done'
    tokens = re.findall(r'\d+[a-zA-Z]*|Done', raw)
    return tokens if tokens else None


def _load_local_completions() -> set[str]:
    """Load local completion memory from disk (best-effort)."""
    try:
        with open(_COMPLETION_TRACK_PATH) as f:
            data = json.load(f)
        if isinstance(data, list):
            return {str(x) for x in data if isinstance(x, str)}
    except Exception:
        pass
    return set()


_LOCALLY_COMPLETED: set[str] = _load_local_completions()


def _record_completion(story_id: str) -> None:
    """Persist locally completed stories to avoid duplicate completion loops."""
    try:
        _LOCALLY_COMPLETED.add(story_id)
        os.makedirs(os.path.dirname(_COMPLETION_TRACK_PATH), exist_ok=True)
        with open(_COMPLETION_TRACK_PATH, "w") as f:
            json.dump(sorted(_LOCALLY_COMPLETED), f)
    except Exception as exc:
        print(f"[DISPATCH] warning: unable to persist local completion memory: {exc}", flush=True)

# ---------------------------------------------------------------------------
# Queue path helper (STORY-040)
# ---------------------------------------------------------------------------


def _get_queue_path() -> str:
    """Return the path to the work queue JSON file."""
    return os.path.expanduser("~/.hermes/work-queue.json")


# STORY-253 (rate-limit pause): parse Claude Code's "resets X" string into
# a UTC unix timestamp so the poller can auto-unpause precisely instead of
# waiting on the 6h safety cap.
#
# Observed formats from the Claude Code CLI:
#   "9pm (UTC)"      → 21:00 today UTC
#   "12am (UTC)"     → 00:00 tomorrow UTC
#   "9:30pm (UTC)"   → 21:30 today UTC
#   "21:00 UTC"      → 21:00 today UTC
# Returns None if the string can't be parsed; caller should fall back to
# a conservative 1h cap in that case.

_RESET_PATTERNS = (
    re.compile(r"^\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)\s*\(?utc\)?\s*$", re.IGNORECASE),
    re.compile(r"^\s*(\d{1,2}):(\d{2})\s*\(?utc\)?\s*$", re.IGNORECASE),
)


def _parse_reset_time(reset_str: str, now_utc: _dt.datetime | None = None) -> float | None:
    """Parse Claude Code's reset-time string into a UTC unix timestamp.

    Picks the next occurrence of that wall-clock time relative to `now_utc`
    (defaults to current time). If the time has already passed today, returns
    tomorrow's occurrence. Returns None if the string doesn't match a known
    format.
    """
    if not reset_str:
        return None
    now = now_utc or _dt.datetime.now(_dt.timezone.utc)

    hour_24: int | None = None
    minute = 0

    # Format 1: 12-hour with am/pm
    m1 = _RESET_PATTERNS[0].match(reset_str)
    if m1:
        h = int(m1.group(1))
        minute = int(m1.group(2) or 0)
        meridiem = m1.group(3).lower()
        if not (1 <= h <= 12):
            return None
        if meridiem == "am":
            hour_24 = 0 if h == 12 else h
        else:
            hour_24 = 12 if h == 12 else h + 12
    else:
        # Format 2: 24-hour HH:MM UTC
        m2 = _RESET_PATTERNS[1].match(reset_str)
        if m2:
            hour_24 = int(m2.group(1))
            minute = int(m2.group(2))
            if not (0 <= hour_24 <= 23 and 0 <= minute <= 59):
                return None

    if hour_24 is None:
        return None

    candidate = now.replace(
        hour=hour_24, minute=minute, second=0, microsecond=0
    )
    # If the parsed time is in the past (or right now), it must mean tomorrow
    if candidate <= now:
        candidate = candidate + _dt.timedelta(days=1)
    return candidate.timestamp()


# ---------------------------------------------------------------------------
# Rate-limit pause flag helpers (STORY-507)
# ---------------------------------------------------------------------------

PAUSE_FLAG_PATH = "/var/run/dispatch-poller-paused-until"


def _is_paused() -> bool:
    """Return True if the agent is rate-limit-paused.

    Reads PAUSE_FLAG_PATH and auto-clears expired flags.
    Used by poll_once() to close the daemon-thread race: the flag is written
    by _run_and_complete AFTER the SDK exits, so poll_once must re-check
    before hitting /dispatch/next even if poll_loop already passed.
    """
    if not os.path.exists(PAUSE_FLAG_PATH):
        return False
    try:
        with open(PAUSE_FLAG_PATH) as f:
            until = f.read().strip()
    except Exception:
        return True  # fail-closed — flag present but unreadable
    until_ts = _parse_reset_time(until)
    if until_ts is not None and time.time() >= until_ts:
        try:
            os.remove(PAUSE_FLAG_PATH)
            print(f"[DISPATCH] reset window passed ({until}) — unpausing", flush=True)
        except Exception as exc:
            print(f"[DISPATCH] failed to clear pause flag: {exc}", flush=True)
        return False
    if until_ts is None:
        # Couldn't parse — apply a 1h conservative cap
        try:
            age = time.time() - os.path.getmtime(PAUSE_FLAG_PATH)
            if age > 3600:
                os.remove(PAUSE_FLAG_PATH)
                print(f"[DISPATCH] cleared pause flag (couldn't parse '{until}', >1h cap)", flush=True)
                return False
        except Exception:
            pass
    return True


# ---------------------------------------------------------------------------
# Pre-claim rate-limit budget check (STORY-507 AC-7)
# ---------------------------------------------------------------------------

RATE_LIMIT_BUDGET_THRESHOLD_S = 900  # 15 minutes


def _check_rate_limit_budget() -> bool:
    """Return True if the Claude Code rate-limit window is nearly exhausted.

    Checks ccusage blocks --json for the current 5-hour block's resets_at.
    If fewer than RATE_LIMIT_BUDGET_THRESHOLD_S (900 s = 15 min) remain,
    returns True (caller should skip the poll cycle).

    Returns False on any parse error or missing data — fail-open so a broken
    ccusage installation never permanently blocks the poller.
    """
    try:
        result = subprocess.run(
            ["ccusage", "blocks", "--json"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return False
        import json as _json
        blocks = _json.loads(result.stdout)
        if not blocks:
            return False
        block = blocks[0]
        resets_at = block.get("resets_at")
        if resets_at is None:
            return False
        now_ts = int(_dt.datetime.now(_dt.timezone.utc).timestamp())
        remaining_s = int(resets_at) - now_ts
        if remaining_s <= RATE_LIMIT_BUDGET_THRESHOLD_S:
            print(
                f"[DISPATCH] rate-limit budget low: {remaining_s}s remaining in block "
                f"(threshold={RATE_LIMIT_BUDGET_THRESHOLD_S}s)",
                flush=True,
            )
            return True
        return False
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Local queue check
# ---------------------------------------------------------------------------


def _local_queue_active() -> bool:
    """Check if the local WorkQueue has an active item.

    STORY-040: Also checks PID liveness. If the queue has an active item
    but the corresponding PID is dead, clears the stale entry and returns False.
    """
    try:
        sys.path.insert(0, "/opt/agent")
        from work_queue import WorkQueue
        wq = WorkQueue(path=_get_queue_path())
        active = wq.resume()
        if active is None:
            return False

        # STORY-040: Check if PID is still alive
        pid = active.get("pid")
        if pid is not None:
            try:
                os.kill(pid, 0)  # Signal 0 = existence check
            except ProcessLookupError:
                # PID is dead — clear stale entry
                story_id = active.get("story_id", "unknown")
                print(f"[DISPATCH] Stale queue entry detected: {story_id} (PID {pid} dead), clearing", flush=True)
                wq.clear_stale()
                return False
            except PermissionError:
                pass  # Process exists but we can't signal — treat as alive

        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Idle detection
# ---------------------------------------------------------------------------


def is_agent_idle() -> bool:
    """Check if the agent is idle (no SDK process, no active local queue item).

    Returns True only when:
    - No claude_sdk_tool.py process is running (pgrep returns non-zero)
    - Local WorkQueue has no active item (with PID liveness check — STORY-040)
    """
    # Use [c] trick in grep to avoid matching the grep process itself
    result = subprocess.run(
        ["bash", "-c", "ps aux | grep '[c]laude_sdk_tool.py -p'"],
        capture_output=True, text=True,
    )
    if result.stdout.strip():
        return False

    if _local_queue_active():
        return False

    return True


# ---------------------------------------------------------------------------
# Reconciliation (STORY-040)
# ---------------------------------------------------------------------------


def reconcile_stale_queue() -> None:
    """Periodic safety net: clear stale active entries from the work queue.

    Called every RECONCILE_INTERVAL seconds from poll_loop.
    Detects active items whose PID is dead and clears them.
    """
    try:
        sys.path.insert(0, "/opt/agent")
        from work_queue import WorkQueue
        wq = WorkQueue(path=_get_queue_path())
        cleared = wq.clear_stale()
        if cleared:
            print("[DISPATCH] Reconciliation: cleared stale queue entry", flush=True)
    except Exception as exc:
        print(f"[DISPATCH] Reconciliation error: {exc}", flush=True)


# ---------------------------------------------------------------------------
# Start a claimed story
# ---------------------------------------------------------------------------


def _report_complete(
    *,
    session,
    base_url: str,
    api_key: str,
    story_id: str,
    repo: str = "",
    commit_sha: str,
    pr_number: int | None = None,
    duration_seconds: int | None = None,
    cost_usd: float | None = None,
    turns: int | None = None,
    error: bool = False,
) -> int:
    """POST /api/dispatch/complete/{story_id} — best-effort.

    STORY-253: `commit_sha` is now required by the server. Extras like
    duration/cost/turns are still posted for telemetry but ignored by the
    endpoint's Pydantic model.
    """
    try:
        resp = session.post(
            f"{base_url}/api/dispatch/complete/{story_id}",
            json={
                "repo": repo,
                "commit_sha": commit_sha,
                "pr_number": pr_number,
                "duration_seconds": duration_seconds,
                "cost_usd": cost_usd,
                "turns": turns,
                "error": error,
            },
            headers={"X-API-Key": api_key},
            timeout=10,
        )
        print(
            f"[DISPATCH] Complete {story_id}: {resp.status_code} sha={commit_sha[:12]}",
            flush=True,
        )
        if resp.status_code == 422:
            # Log the body so we can diagnose why the server rejected it.
            try:
                print(f"[DISPATCH] 422 body: {resp.text[:300]}", flush=True)
            except Exception:
                pass
        return int(resp.status_code)
    except Exception as exc:
        print(f"[DISPATCH] Failed to report complete for {story_id}: {exc}", flush=True)
        return 0


# ---------------------------------------------------------------------------
# STORY-560: /complete retry + sidecar helpers
# ---------------------------------------------------------------------------

_COMPLETE_RETRY_ATTEMPTS = 2  # total attempts (1 initial + 1 retry)
_COMPLETE_RETRY_BACKOFF_S = 5  # base sleep between attempts


def _get_complete_sidecar_path(story_id: str) -> str:
    """Return the path for the /complete failure sidecar file.

    STORY-560: When all /complete attempts exhaust, write a sidecar so Morris
    can detect stale completions (story shipped code + PR, but audit trail blank).

    Path: /home/hermes/state/<agent>/complete-failed/<story_id>.json
    """
    agent_name = os.environ.get("AGENT_NAME", "unknown")
    return f"/home/hermes/state/{agent_name}/complete-failed/{story_id}.json"


def _report_complete_with_retry(
    *,
    session,
    base_url: str,
    api_key: str,
    story_id: str,
    repo: str = "",
    commit_sha: str,
    pr_number: int | None = None,
    duration_seconds: int | None = None,
    cost_usd: float | None = None,
    turns: int | None = None,
    error: bool = False,
) -> int:
    """Call _report_complete with exponential-backoff retry and sidecar on failure.

    STORY-560: The bare _report_complete call was fire-and-forget; transient HTTP
    errors silently dropped audit data (pr_number=None / commit_sha=None in DB).
    This wrapper retries up to _COMPLETE_RETRY_ATTEMPTS times with a 5s base
    backoff, then writes a sidecar JSON so Morris can detect missed completions.

    Returns the final HTTP status code (200 on success, last error code otherwise).
    """
    last_status = 0
    for attempt in range(_COMPLETE_RETRY_ATTEMPTS):
        last_status = _report_complete(
            session=session,
            base_url=base_url,
            api_key=api_key,
            story_id=story_id,
            repo=repo,
            commit_sha=commit_sha,
            pr_number=pr_number,
            duration_seconds=duration_seconds,
            cost_usd=cost_usd,
            turns=turns,
            error=error,
        )
        if last_status == 200:
            return last_status
        if attempt < _COMPLETE_RETRY_ATTEMPTS - 1:
            backoff = _COMPLETE_RETRY_BACKOFF_S * (2 ** attempt)  # 5s, 10s, …
            print(
                f"[DISPATCH] /complete returned {last_status} for {story_id} "
                f"(attempt {attempt + 1}/{_COMPLETE_RETRY_ATTEMPTS}) — "
                f"retrying in {backoff}s",
                flush=True,
            )
            time.sleep(backoff)

    # All attempts exhausted — write sidecar so Morris can detect stale completions
    sidecar_path = _get_complete_sidecar_path(story_id)
    try:
        os.makedirs(os.path.dirname(sidecar_path), exist_ok=True)
        import json as _json_sidecar
        with open(sidecar_path, "w") as f:
            _json_sidecar.dump(
                {
                    "story_id": story_id,
                    "repo": repo,
                    "commit_sha": commit_sha,
                    "pr_number": pr_number,
                    "last_http_status": last_status,
                    "attempts": _COMPLETE_RETRY_ATTEMPTS,
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                },
                f,
                indent=2,
            )
        print(
            f"[DISPATCH] /complete sidecar written for {story_id}: {sidecar_path}",
            flush=True,
        )
    except Exception as exc:
        print(
            f"[DISPATCH] Warning: could not write /complete sidecar for {story_id}: {exc}",
            flush=True,
        )
    return last_status


MAX_RETRY_ATTEMPTS = 3
_RETRY_TAG = "[RETRY "  # e.g. "[RETRY 2/3]" prepended to prompt
_STORY_REF_RE = re.compile(r"\bSTORY-\d+\b", re.IGNORECASE)

# STORY-764: Regex to match a leading [RETRY N/N] prefix.
# Anchored to start-of-string. Case-sensitive. Allows optional leading
# whitespace and optional trailing whitespace after the closing bracket.
_RETRY_PREFIX_RE = re.compile(r"^\s*\[RETRY \d+/\d+\]\s*")

# STORY-764: Maximum prompt length for dispatch (Pydantic model limit).
_MAX_PROMPT_LENGTH = 5000


def _strip_retry_prefix(prompt: str) -> str:
    """Strip any leading ``[RETRY N/N]`` prefix from *prompt*.

    Returns the prompt with the prefix removed, or unchanged if no prefix
    is present.  Idempotent — safe to call on prompts that were never
    prefixed.  Only strips a prefix anchored to the start of the string;
    ``[RETRY ...]`` tokens appearing mid-prompt are preserved.

    Regex: ``^\\s*\\[RETRY \\d+/\\d+\\]\\s*``  (AC-2)
    """
    return _RETRY_PREFIX_RE.sub("", prompt, count=1)


def _count_retries(prompt: str) -> int:
    """Extract the attempt number from a prompt's [RETRY N/3] tag, or 0."""
    m = re.search(r"\[RETRY\s+(\d+)/", prompt)
    return int(m.group(1)) if m else 0


def _prompt_references_other_story(prompt: str, story_id: str) -> bool:
    """Return True when prompt mentions STORY-* values other than story_id."""
    canonical = story_id.upper()
    found = {m.group(0).upper() for m in _STORY_REF_RE.finditer(prompt or "")}
    return any(ref != canonical for ref in found)


# ---------------------------------------------------------------------------
# STORY-741 / STORY-641: Failure classification for smart retry suppression
# ---------------------------------------------------------------------------

# Non-retryable failure classes: patterns that indicate a deterministic failure
# where retrying with identical inputs will always produce the same outcome.
# Keyed by class name → tuple of lowercase substrings to match in error_message.
_NEVER_RETRY_PATTERN_MAP: dict[str, tuple[str, ...]] = {
    "branch_mismatch_code_bug": (
        "branch_mismatch",
        "not on expected branch",
        "branch mismatch",
    ),
    "gate_rejected_code_bug": (
        "acceptance diff missing",
        "tests failed",
        "gate_rejected",
        "gate rejected",
        "deliverable missing",
    ),
    "auth_credential": (
        "permission denied",
        "http 403",
        "http 401",
    ),
    "disk_full": (
        "no space left on device",
        "enospc",
    ),
    # STORY-803 Bug 3: Phase 8 returned rc=0 with zero commits — deterministic
    # signal that the agent misread the prompt or hit a tool issue. Retrying
    # with unchanged inputs will produce the same outcome. Route to 'failed'
    # (not pending for retry) so the story surfaces in the failed queue.
    "phase8_silent_exit": (
        "phase8_silent_exit",
    ),
}

# The set of class names that suppress auto-retry in _report_fail.
NEVER_RETRY_CLASSES: frozenset[str] = frozenset(_NEVER_RETRY_PATTERN_MAP.keys())


def _classify_failure(error_message: str | None) -> str:
    """Classify a failure message into a retry-policy class.

    Returns one of the NEVER_RETRY_CLASSES strings, or 'unknown' for anything
    that should follow the default retry path.

    STORY-741 / STORY-641: deterministic failures (branch mismatch, gate
    rejection, auth errors, disk full) must not burn retry attempts because
    retrying with unchanged state will always produce the same failure.
    """
    if not error_message:
        return "unknown"
    lower = error_message.lower()
    for class_name, patterns in _NEVER_RETRY_PATTERN_MAP.items():
        if any(pat in lower for pat in patterns):
            return class_name
    return "unknown"


_CREDENTIAL_PATTERN = re.compile(
    r"(?:[A-Z_]{4,}(?:KEY|TOKEN|SECRET|PASSWORD|PASS|PWD|CREDENTIAL|AUTH|API)[A-Z_]*"
    r"|Bearer\s+\S+)"
    r"[=:\s]+\S+",
    re.IGNORECASE,
)


def _truncate_failure_reason(prefix: str, detail: str) -> str:
    """Build a structured failure_reason string, sanitizing credentials and capping at 1000 chars.

    STORY-762: every fail-emitting path must populate failure_reason with a
    non-empty structured string. This helper:
      - Strips ENV_VAR=<value> and Bearer <token> patterns from detail (security constraint)
      - Caps the final string at 1000 chars (AC-11)
      - Guarantees the prefix is included in the output

    Args:
        prefix: taxonomy prefix, e.g. "branch_setup_failed", "sdk_died",
                "phase_7_failed", "quota_exceeded"
        detail: raw error output (git stderr, exception message, etc.)
    """
    # Sanitize credential-like patterns (ENV_VAR=secret, Bearer token, etc.)
    sanitized = _CREDENTIAL_PATTERN.sub("<redacted>", detail or "")
    result = f"{prefix}: {sanitized}"
    return result[:1000]


def _report_fail(
    *,
    session,
    base_url: str,
    api_key: str,
    story_id: str,
    exit_code: int | None = None,
    repo: str = "",
    scope: str = "small",
    prompt: str = "",
    pr_branch: str = "",
    base_story_id: str = "",
    rework_of: str | None = None,
    duration_seconds: int | None = None,
    error_message: str | None = None,
):
    """POST /api/dispatch/fail/{story_id} then auto-retry if under the cap.

    Auto-retry: if this story has been attempted < MAX_RETRY_ATTEMPTS times,
    re-enqueue it with a [RETRY N/3] tag. The next agent to claim it gets
    another shot. Rate-limit failures (exit_code=429) are NOT retried — the
    pause-flag mechanism handles those separately. Phantom-claim guard: if
    duration_seconds < 30, the SDK never actually ran (daily cap or startup
    failure), so retry is suppressed to avoid rapid claim-and-fail loops.
    Failure classification (STORY-741/641): deterministic non-transient errors
    (branch mismatch, gate rejection, auth, disk full) skip retry regardless
    of duration.
    """
    # Build structured failure_reason — callers pass error_message as a pre-formatted
    # string via _truncate_failure_reason(prefix, detail). AC-10: log it so Loki
    # shows the reason without needing a DB query during incident response.
    if error_message:
        print(
            f"[DISPATCH] {story_id} failure_reason={error_message!r}",
            flush=True,
        )
    try:
        resp = session.post(
            f"{base_url}/api/dispatch/fail/{story_id}",
            json={
                "exit_code": exit_code,
                "failure_reason": error_message or None,
            },
            headers={"X-API-Key": api_key},
            timeout=10,
        )
        print(f"[DISPATCH] Fail {story_id}: {resp.status_code}", flush=True)
    except Exception as exc:
        print(f"[DISPATCH] Failed to report fail for {story_id}: {exc}", flush=True)
        return

    # Auto-retry (skip rate-limit failures — handled by pause flag)
    if exit_code == 429:
        logger.warning("[DISPATCH] %s: exit_code=429 — skipping retry, pause-flag handles this", story_id)
        return

    # Phantom-claim guard (STORY-556): if the SDK exited in <30s it never ran.
    # Retrying immediately would produce another phantom claim. Skip.
    if duration_seconds is not None and duration_seconds < 30:
        logger.warning(
            "[DISPATCH] %s: phantom claim suppressed — duration=%ds < 30s threshold",
            story_id, duration_seconds,
        )
        return
    if not repo or not prompt:
        return

    # Failure classification gate (STORY-741 / STORY-641): skip retry for
    # deterministic non-transient failures. Retrying with identical state will
    # always produce the same outcome and just burns agent tokens.
    failure_class = _classify_failure(error_message)
    if failure_class in NEVER_RETRY_CLASSES:
        print(
            f"[DISPATCH] {story_id}: failure_class={failure_class} retry_decision=skip "
            f"(non-transient — no auto-retry)",
            flush=True,
        )
        return
    print(
        f"[DISPATCH] {story_id}: failure_class={failure_class} retry_decision=allowed",
        flush=True,
    )

    attempt = _count_retries(prompt) + 1
    if attempt > MAX_RETRY_ATTEMPTS:
        print(f"[DISPATCH] {story_id} exhausted {MAX_RETRY_ATTEMPTS} retries — flagging for respec", flush=True)
        # Create an issue file in the repo so the story gets human review.
        # Morris's fleet-vigilance skill Check 5 (failed stories) will also
        # surface this in his next 15-min cycle.
        try:
            agent_name = os.environ.get("AGENT_NAME", "unknown")
            flag_msg = (
                f"STORY {story_id} FAILED {MAX_RETRY_ATTEMPTS}x\n"
                f"Agent: {agent_name}\n"
                f"Repo: {repo}\n"
                f"Scope: {scope}\n"
                f"Exit codes from retries may indicate: prompt too complex, "
                f"missing repo context, SDK permission blocks, or a genuine "
                f"code defect in the seed/spec. Needs human review + respec.\n"
            )
            flag_path = f"/home/hermes/state/{agent_name}/failed-stories/{story_id}.txt"
            os.makedirs(os.path.dirname(flag_path), exist_ok=True)
            with open(flag_path, "w") as f:
                f.write(flag_msg)
            print(f"[DISPATCH] wrote failure flag to {flag_path}", flush=True)
        except Exception as flag_exc:
            print(f"[DISPATCH] could not write failure flag: {flag_exc}", flush=True)
        return

    # STORY-764: Strip any existing [RETRY N/M] prefix before adding the new one
    clean_prompt = _strip_retry_prefix(prompt)
    retry_prompt = f"[RETRY {attempt}/{MAX_RETRY_ATTEMPTS}] {clean_prompt}"

    # AC-8: Log post-strip prompt length so size issues are visible in Loki
    print(
        f"[DISPATCH] retry prompt length: {len(retry_prompt)} chars (after strip)",
        flush=True,
    )

    # AC-9: Overflow guard — if the retry prompt exceeds the dispatch limit,
    # skip the retry and log a warning rather than POST a guaranteed 422.
    if len(retry_prompt) > _MAX_PROMPT_LENGTH:
        print(
            f"[DISPATCH] {story_id}: retry prompt ({len(retry_prompt)} chars) "
            f"exceeds {_MAX_PROMPT_LENGTH} — skip retry to avoid 422",
            flush=True,
        )
        return

    retry_body: dict = {
        "story_id": story_id,
        "repo": repo,
        "scope": scope,
        "prompt": retry_prompt,
        "enqueued_by": "dispatch-poller-retry",
    }
    # Retry prompts can include references to parent/related stories.
    # Explicitly opt in so the enqueue validator does not reject legitimate retries.
    if _prompt_references_other_story(retry_prompt, story_id):
        retry_body["cross_story_reference"] = True
    if pr_branch:
        retry_body["pr_branch"] = pr_branch
    if base_story_id:
        retry_body["base_story_id"] = base_story_id
    if rework_of:
        retry_body["rework_of"] = rework_of
    try:
        r = session.post(
            f"{base_url}/api/dispatch",
            json=retry_body,
            headers={"X-API-Key": api_key},
            timeout=10,
        )
        if r.status_code in (201, 409):
            print(f"[DISPATCH] auto-retry {story_id} (attempt {attempt}/{MAX_RETRY_ATTEMPTS}): {r.status_code}", flush=True)
        else:
            print(f"[DISPATCH] auto-retry {story_id} failed: {r.status_code} {r.text[:200]}", flush=True)
    except Exception as exc:
        print(f"[DISPATCH] auto-retry {story_id} error: {exc}", flush=True)


def _register_dispatch(
    *,
    session,
    base_url: str,
    api_key: str,
    story_id: str,
    repo: str,
    scope: str,
    prompt: str,
    agent_name: str,
):
    """Register Teams-dispatched work in the dispatch queue (AC-15)."""
    try:
        resp = session.post(
            f"{base_url}/api/dispatch",
            json={
                "story_id": story_id,
                "repo": repo,
                "scope": scope,
                "prompt": prompt,
                "enqueued_by": agent_name,
                "source": "teams",
            },
            headers={"X-API-Key": api_key},
            timeout=10,
        )
        if resp.status_code in (201, 409):
            # Claim it
            session.post(
                f"{base_url}/api/dispatch/claim/{story_id}",
                json={"agent_name": agent_name},
                headers={"X-API-Key": api_key},
                timeout=10,
            )
    except Exception as exc:
        print(f"[DISPATCH] Teams auto-register failed for {story_id}: {exc}", flush=True)


def start_story(
    *,
    story_id: str,
    repo: str,
    scope: str,
    prompt: str,
    workspace: str,
    base_url: str = "",
    api_key: str = "",
    pr_branch: str = "",
    base_story_id: str = "",
    rework_of: str | None = None,
    resumed_question_path: str | None = None,
) -> None:
    """Add story to local WorkQueue and launch claude_sdk_tool.py.

    STORY-040: The poller is the sole owner of the work queue.
    Sets DISPATCHED_BY_POLLER=1 so the SDK skips its own queue writes.
    Records the SDK subprocess PID for stale detection.
    """
    # Try multiple repo locations — workspace, dev/hpi-gorillacommerce
    candidates = [
        os.path.join(workspace, repo),
        os.path.expanduser(f"~/dev/hpi-gorillacommerce/{repo}"),
        os.path.expanduser(f"~/workspace/{repo}"),
    ]
    workdir = next((p for p in candidates if os.path.isdir(p)), None)

    # If repo not found, fall back to home dir — let the agent clone it
    if workdir is None:
        fallback = os.path.expanduser("~")
        print(f"[DISPATCH] Repo {repo} not found locally, starting SDK from {fallback} — agent will need to clone it", flush=True)
        # Prepend clone instruction to the prompt
        prompt = f"IMPORTANT: The repo '{repo}' is not cloned on this machine. Clone it first: git clone https://github.com/hpi-gorillacommerce/{repo}.git ~/dev/hpi-gorillacommerce/{repo} then cd into it and proceed.\n\n{prompt}"
        workdir = fallback

    # SDLC compliance block — appended to every dispatch prompt so agents
    # can't claim they didn't know the requirements.
    _SDLC_REQUIRED = {
        "small": ["seed.md", "test-design.md"],
        "medium": ["seed.md", "analysis.md", "feature-spec.md", "test-design.md", "code-review.md"],
        "large": ["seed.md", "research.md", "analysis.md", "feature-spec.md", "test-design.md", "code-review.md"],
    }
    required_files = _SDLC_REQUIRED.get(scope, _SDLC_REQUIRED["small"])

    if pr_branch:
        # Rework story: agent must check out the existing PR branch, not create a new one.
        # SDLC files go in the base story's folder (not this rework story's folder).
        folder_story = base_story_id or story_id
        sdlc_block = (
            f"\n\n--- REWORK DISPATCH (MANDATORY) ---\n"
            f"This is a PR rework. DO NOT create a new branch.\n"
            f"Check out the existing PR branch: git fetch origin && git checkout {pr_branch}\n"
            f"SDLC deliverables go in features/<folder matching {folder_story}>/ — NOT a new story folder.\n"
            f"Story scope: {scope}. Required files: {', '.join(required_files)}\n"
            f"Push to {pr_branch} after every commit. Update the existing PR (do not open a new one).\n"
            f"--- END REWORK ---"
        )
    else:
        sdlc_block = (
            f"\n\n--- SDLC COMPLIANCE (MANDATORY) ---\n"
            f"Story scope: {scope}. You MUST produce these deliverables in features/<story-folder>/:\n"
            f"  {', '.join(required_files)}\n"
            f"Write test-design.md BEFORE implementation code (Phase 7 before Phase 8).\n"
            f"The completion guard will verify these files exist in the repo before accepting.\n"
            f"Push every commit. Create a PR with gh pr create when implementation is done.\n"
            f"--- END SDLC ---"
        )
    prompt = prompt + sdlc_block

    # STORY-040: Set env var so SDK subprocess skips its own queue writes
    env = os.environ.copy()
    env["DISPATCHED_BY_POLLER"] = "1"

    cmd = [
        PYTHON_PATH,
        SDK_TOOL_PATH,
        "-p", prompt,
        "-w", workdir,
    ]
    print(f"[DISPATCH] Starting SDK: {' '.join(cmd[:6])}...", flush=True)

    # Prepare session for completion reporting
    _session = requests.Session()
    _base_url = base_url or os.environ.get("OPS_CONSOLE_URL", "")
    _api_key = api_key or os.environ.get("OPS_CONSOLE_API_KEY", "")

    def _run_and_complete():
        """Run SDK subprocess, report completion, and clear local queue on exit."""
        start_time = time.time()
        error = False
        rc = -1
        sdk_pid = None

        # Try the multi-phase SDLC runner first
        try:
            sys.path.insert(0, "/opt/agent")
            from sdlc_phase_runner import run_sdlc_phases
            print(f"[DISPATCH] Using SDLC phase runner for {story_id} (scope={scope})", flush=True)

            # Record in local queue
            try:
                from work_queue import WorkQueue
                wq = WorkQueue(path=_get_queue_path())
                wq.enqueue(story_id, phase=1, scope=scope, source="dispatch-queue")
                wq.set_active(story_id, phase=1, pid=os.getpid())
            except Exception as exc:
                print(f"[DISPATCH] Warning: failed to update local queue: {exc}", flush=True)

            _phase_result = run_sdlc_phases(
                story_id=story_id,
                repo=repo,
                scope=scope,
                prompt=prompt,
                workdir=workdir,
                env=env,
                resumed_question_path=resumed_question_path,
                rework_of=rework_of,
            )
            # run_sdlc_phases returns (success, commit_sha, reason, pr_number) since
            # STORY-560. reason ∈ {None, "needs_info", "already_done",
            # "rate_limited"}. Backward-compat: pre-560 runners return 2- or 3-tuple.
            if len(_phase_result) == 2:
                success, commit_sha = _phase_result
                phase_reason = None
                pr_number = None
            elif len(_phase_result) == 3:
                success, commit_sha, phase_reason = _phase_result
                pr_number = None
            else:
                success, commit_sha, phase_reason, pr_number = _phase_result

            # Check if failure was due to rate limit (pause flag written by
            # phase runner). In that case, do NOT retry — the pause mechanism
            # handles it. Retrying rate-limited stories burns tokens and creates
            # churn (608 rate-limit hits over 2026-04-18/19 weekend).
            rate_limited = (
                os.path.exists("/var/run/dispatch-poller-paused-until")
                or phase_reason == "rate_limited"
            )

            if success and commit_sha:
                rc = 0

                # STORY-723: Adversarial review gate — run after Phase 8 for
                # medium/large/new scope. BLOCK verdict prevents completion and
                # triggers a fix-task dispatch. Skip for small/trivial (no Phase
                # 6 spec to review against). Controlled by ADVERSARIAL_REVIEW env var.
                _adv_enabled = os.environ.get("ADVERSARIAL_REVIEW", "1") == "1"
                if _adv_enabled and scope in ("medium", "large", "new"):
                    try:
                        from adversarial_reviewer import run_adversarial_review
                        _story_folder = _extract_story_folder(story_id, workdir) if callable(_extract_story_folder) else f"story-{story_id.lower().replace('story-', '')}"
                        _adv_result = run_adversarial_review(
                            story_id=story_id,
                            story_folder=_story_folder,
                            scope=scope,
                            workdir=workdir,
                        )
                        if _adv_result.get("dispatch_fix_task"):
                            print(
                                f"[DISPATCH] {story_id} adversarial review BLOCKED "
                                f"({_adv_result['critical_count']} CRITICAL findings) — "
                                "skipping completion, dispatching fix task",
                                flush=True,
                            )
                            rc = 1
                            error = True
                            success = False
                    except Exception as _adv_exc:
                        print(f"[DISPATCH] {story_id} adversarial review error (non-blocking): {_adv_exc}", flush=True)

                # Report completion with the real SHA. Only add to
                # _LOCALLY_COMPLETED if the server ACCEPTS the completion
                # (HTTP 200). On 422 (SDLC gate rejection, unreachable SHA,
                # etc.) the story stays pending on the server and we must
                # NOT mark it locally-complete — otherwise the poller
                # spins the "already completed locally — skipping" no-op
                # loop forever (STORY-511 regression 2026-04-22: both
                # Daisy and Devon were looping on STORY-521 after gate
                # rejection, burning cycles at ~67% and ~57% of their
                # 5-hour billing windows respectively).
                if success and _base_url and _api_key:
                    # STORY-560: use retry wrapper so transient HTTP errors don't
                    # silently drop pr_number + commit_sha from the audit trail.
                    # pr_number captured by run_sdlc_phases from gh pr list/create.
                    complete_status = _report_complete_with_retry(
                        session=_session,
                        base_url=_base_url,
                        api_key=_api_key,
                        story_id=story_id,
                        repo=repo,  # STORY-531: disambiguate by repo (closure var)
                        commit_sha=commit_sha,
                        pr_number=pr_number,
                        duration_seconds=int(time.time() - start_time),
                    )
                    if complete_status == 200:
                        _LOCALLY_COMPLETED.add(story_id)
                        _record_completion(story_id)
                    elif complete_status == 422:
                        # Gate rejected — the story isn't really done.
                        # Convert to a failure so the auto-retry path can
                        # produce the missing deliverable (or the ticket
                        # can be closed by a manager). Without this, the
                        # poller would infinitely skip the story.
                        print(
                            f"[DISPATCH] {story_id} completion rejected by gate — "
                            "converting to fail so auto-retry can run",
                            flush=True,
                        )
                        _report_fail(
                            session=_session,
                            base_url=_base_url,
                            api_key=_api_key,
                            story_id=story_id,
                            exit_code=422,
                            repo=repo,
                            scope=scope,
                            prompt=prompt,
                            duration_seconds=int(time.time() - start_time),
                            rework_of=rework_of,
                            error_message=_truncate_failure_reason(
                                "gate_rejected_code_bug",
                                "completion rejected by SDLC gate (exit_code=422)",
                            ),
                        )
                        rc = 422
                        error = True
                    # Other non-200: log but don't add to locally-completed
                    # (will be retried on next poll — fine, not a loop).
                else:
                    # No base_url/api_key configured — single-agent test
                    # mode. Mark locally-complete to preserve prior behavior.
                    _LOCALLY_COMPLETED.add(story_id)
                    _record_completion(story_id)
            elif rate_limited:
                # STORY-538: Rate limit — release the claim back to pending
                # (not fail, which is destructive). The poller's pause flag
                # mechanism handles unpausing. This is the fix for Devon's bug:
                # environmental failures release, not fail+re-dispatch+re-claim.
                error = True
                rc = 429
                print(f"[DISPATCH] {story_id} hit rate limit — releasing claim (not failing)", flush=True)
                if _base_url and _api_key:
                    try:
                        release_resp = _session.post(
                            f"{_base_url}/api/dispatch/release/{story_id}",
                            headers={"X-API-Key": _api_key},
                            timeout=10,
                        )
                        print(f"[DISPATCH] Release {story_id}: {release_resp.status_code}", flush=True)
                    except Exception as rel_exc:
                        print(f"[DISPATCH] Release {story_id} failed: {rel_exc} — falling back to fail", flush=True)
                        _report_fail(
                            session=_session,
                            base_url=_base_url,
                            api_key=_api_key,
                            story_id=story_id,
                            exit_code=429,
                            repo=repo,
                            scope=scope,
                            prompt=prompt,
                            rework_of=rework_of,
                            error_message=_truncate_failure_reason(
                                "quota_exceeded",
                                "rate_limit release failed — release endpoint unreachable",
                            ),
                        )
            elif phase_reason == "needs_info":
                # Agent wrote QUESTION.md and the phase runner already POSTed
                # /api/dispatch/needs-info. Story is now in needs_info state on
                # ops-console — DO NOT call _report_fail (that would move it
                # needs_info → failed and trigger an auto-retry loop, which is
                # the 2026-04-23 STORY-528/STORY-505 regression). Leave it for
                # the operator to answer via dashboard + /resume.
                error = False  # not an error from dispatcher's POV
                rc = 0
                print(
                    f"[DISPATCH] {story_id} marked needs_info — operator input required, "
                    f"skipping auto-retry",
                    flush=True,
                )
            elif phase_reason == "already_done":
                # Pre-loop guard in phase runner detected this story is already
                # in a terminal state upstream. Treat as success; no retry.
                error = False
                rc = 0
                print(
                    f"[DISPATCH] {story_id} already done on ops-console — "
                    f"skipping phase loop",
                    flush=True,
                )
            else:
                # Genuine failure (not rate limit, not needs_info) — retry is allowed
                error = True
                rc = 1
                if _base_url and _api_key:
                    _report_fail(
                        session=_session,
                        base_url=_base_url,
                        api_key=_api_key,
                        story_id=story_id,
                        exit_code=1,
                        repo=repo,
                        scope=scope,
                        prompt=prompt,
                        rework_of=rework_of,
                        error_message=_truncate_failure_reason(
                            "sdk_died",
                            phase_reason or "phase runner returned failure",
                        ),
                    )

            # Clear local queue
            try:
                from work_queue import WorkQueue
                wq = WorkQueue(path=_get_queue_path())
                wq.complete(story_id)
                print(f"[DISPATCH] Cleared {story_id} from local queue", flush=True)
            except Exception as exc:
                print(f"[DISPATCH] Warning: failed to clear local queue: {exc}", flush=True)

            duration = int(time.time() - start_time)
            print(f"[DISPATCH] {story_id} {'COMPLETE' if success else 'FAILED'} in {duration}s", flush=True)
            return  # Skip the legacy single-shot path

        except ImportError:
            print(f"[DISPATCH] sdlc_phase_runner not available — falling back to single-shot", flush=True)
        except Exception as exc:
            print(f"[DISPATCH] Phase runner error: {exc} — falling back to single-shot", flush=True)

        # Legacy single-shot fallback (only if phase runner fails to import)
        try:
            proc = subprocess.Popen(cmd, env=env)
            sdk_pid = proc.pid

            # STORY-040: Record SDK PID in the work queue for stale detection
            try:
                sys.path.insert(0, "/opt/agent")
                from work_queue import WorkQueue
                wq = WorkQueue(path=_get_queue_path())
                wq.enqueue(story_id, phase=7, scope=scope, source="dispatch-queue")
                wq.set_active(story_id, phase=7, pid=sdk_pid)
            except Exception as exc:
                print(f"[DISPATCH] Warning: failed to update local queue: {exc}", flush=True)

            proc.wait()  # Block until SDK finishes
            rc = proc.returncode
            error = rc != 0
            print(f"[DISPATCH] SDK exited for {story_id} (rc={rc})", flush=True)
            # STORY-253: carry these out to the finally block so _report_complete
            # can send the SHA and PR number the ops-console guard requires.
            validation_commit_sha: str | None = None
            validation_pr_number: int | None = None

            # Rate-limit detection: scan the SDK's own session log file for the
            # Claude Code "You've hit your limit · resets <time>" message.
            # The SDK writes every line to /tmp/claude-sdlc-logs/session-<ts>.log
            # (see LOG_FILE in claude_sdk_tool.py). journalctl is unreliable on
            # these VMs — read the log file directly instead.
            rate_limited_until: str | None = None
            try:
                log_dir = "/tmp/claude-sdlc-logs"
                if os.path.isdir(log_dir):
                    # Find log files created/modified during this SDK run
                    candidates = []
                    for fname in os.listdir(log_dir):
                        fpath = os.path.join(log_dir, fname)
                        try:
                            if os.path.getmtime(fpath) >= start_time:
                                candidates.append(fpath)
                        except OSError:
                            pass
                    # Read newest first; stop as soon as the pattern is found
                    for fpath in sorted(candidates, key=os.path.getmtime, reverse=True):
                        try:
                            with open(fpath) as lf:
                                content = lf.read(65536)  # first 64 KB is enough
                            m = re.search(
                                r"hit your limit.*?resets\s+(\S+(?:\s+\([^)]+\))?)",
                                content,
                                re.IGNORECASE,
                            )
                            if m:
                                rate_limited_until = m.group(1).strip()
                                break
                        except Exception:
                            pass
            except Exception:
                pass

            if rate_limited_until:
                # Hand the story back without completing or failing it. Mark this
                # agent as paused so the poll loop skips claims until reset.
                print(f"[DISPATCH] RATE LIMITED on {story_id} — releasing, paused until {rate_limited_until}", flush=True)
                try:
                    pause_path = "/var/run/dispatch-poller-paused-until"
                    with open(pause_path, "w") as f:
                        f.write(rate_limited_until)
                except Exception as exc:
                    print(f"[DISPATCH] could not write pause flag: {exc}", flush=True)
                # Mark as failed with a special exit code so ops-console knows to
                # re-pend (don't pretend it succeeded; don't claim a SHA we don't have)
                if _base_url and _api_key:
                    _report_fail(
                        session=_session,
                        base_url=_base_url,
                        api_key=_api_key,
                        story_id=story_id,
                        exit_code=429,
                        error_message=_truncate_failure_reason(
                            "quota_exceeded",
                            "rate_limited — pause flag written, re-pend via pause mechanism",
                        ),
                    )
                # Skip the rest of the validation/complete path
                rc = 429
                error = True

            if rc == 0:
                # Post-completion validation: check for proof of work
                validation_passed = True
                try:
                    # For rework stories, validate against the existing PR branch;
                    # for new work, search by the story number slug.
                    if pr_branch:
                        branch_pattern = pr_branch.split("/")[0] + "/" + pr_branch.split("/")[1] if "/" in pr_branch else pr_branch
                        branch_glob = f"*{branch_pattern.replace('origin/', '')}*"
                    else:
                        branch_glob = f"*{story_id.split('-')[1]}*"

                    # Check 1: a story branch exists on REMOTE (must be pushed,
                    # not just local — local-only commits can be stale from a prior
                    # session and create a fraud window).
                    branch_check = subprocess.run(
                        ["git", "-C", workdir, "branch", "-r", "--list", branch_glob],
                        capture_output=True, text=True, timeout=10,
                    )
                    has_branch = bool(branch_check.stdout.strip())
                    # Resolve the actual remote branch ref (first match)
                    remote_branch = None
                    if has_branch:
                        first = branch_check.stdout.strip().split("\n")[0].strip()
                        # strip leading "  origin/" if present
                        remote_branch = first.lstrip().split()[0]

                    # Check 2: commits exist between origin/main and the REMOTE
                    # branch tip (not local HEAD — defends against orphaned local
                    # commits from prior failed sessions).
                    has_commits = False
                    if remote_branch:
                        diff_check = subprocess.run(
                            ["git", "-C", workdir, "log", "--oneline",
                             f"origin/main..{remote_branch}", "--max-count=1"],
                            capture_output=True, text=True, timeout=10,
                        )
                        has_commits = bool(diff_check.stdout.strip())

                    # STORY-253: extract the tip SHA from the REMOTE branch.
                    if has_commits and remote_branch:
                        sha_check = subprocess.run(
                            ["git", "-C", workdir, "log", f"origin/main..{remote_branch}",
                             "--format=%H", "--max-count=1"],
                            capture_output=True, text=True, timeout=10,
                        )
                        sha = sha_check.stdout.strip()
                        if sha and len(sha) >= 7:
                            validation_commit_sha = sha

                    # Check 3: PR exists?
                    # branch_slug: for rework stories use the cleaned pr_branch;
                    # for new work use the numeric story-ID fragment (e.g. "337").
                    # This mirrors the branch_glob derivation above so both checks
                    # target the same branch.  Using cwd= + list form avoids both
                    # the shell injection surface and the NameError from the
                    # previously undefined `branch_slug` variable.
                    if pr_branch:
                        branch_slug = pr_branch.replace("origin/", "")
                    else:
                        branch_slug = story_id.split("-")[1]
                    pr_check = subprocess.run(
                        ["gh", "pr", "list",
                         "--head", f"*{branch_slug}*",
                         "--json", "number",
                         "--jq", ".[0].number"],
                        capture_output=True, text=True, timeout=15,
                        cwd=workdir,
                    )
                    pr_out = pr_check.stdout.strip()
                    has_pr = bool(pr_out) and pr_out != "0"
                    if has_pr:
                        try:
                            validation_pr_number = int(pr_out)
                        except ValueError:
                            pass

                    # FIX: AND -> OR. Either a missing pushed branch OR no commits
                    # on that branch is a validation failure. The previous AND meant
                    # both had to be missing — Derrick's stale-SHA fraud (4 stories
                    # with 2 unique stale SHAs on 2026-04-15) slipped through because
                    # commits-yes from old work satisfied the half-check.
                    if not has_branch or not has_commits:
                        print(f"[DISPATCH] VALIDATION FAILED {story_id}: missing pushed branch or new commits (branch={'yes' if has_branch else 'no'}, commits={'yes' if has_commits else 'no'}) — work not shipped", flush=True)
                        validation_passed = False
                    elif not validation_commit_sha:
                        print(f"[DISPATCH] VALIDATION FAILED {story_id}: no extractable commit SHA — cannot satisfy guard", flush=True)
                        validation_passed = False
                    elif not has_pr:
                        print(f"[DISPATCH] VALIDATION WARNING {story_id}: completed but no PR created (branch=yes commits=yes sha={validation_commit_sha[:12]})", flush=True)
                    else:
                        print(f"[DISPATCH] VALIDATION OK {story_id}: branch=yes commits=yes pr=yes sha={validation_commit_sha[:12]}", flush=True)
                except Exception as val_exc:
                    print(f"[DISPATCH] VALIDATION SKIPPED {story_id}: {val_exc}", flush=True)
                    validation_passed = False

                if validation_passed:
                    print(f"[DISPATCH] completed {story_id}", flush=True)
                else:
                    print(f"[DISPATCH] RETRY {story_id} — session succeeded but no git artifacts, re-enqueuing", flush=True)
                    # Re-enqueue to central queue so the agent picks it up again
                    try:
                        _session.post(
                            f"{_base_url}/api/dispatch",
                            json={
                                "story_id": story_id,
                                "repo": repo,
                                "scope": scope,
                                "prompt": f"RETRY: {story_id} ran but produced no branch/commits/PR. Re-read the seed and try again. You MUST: 1) Create branch story-{story_id.split('-')[1]}/retry off main 2) Push after every commit 3) Create a PR with gh pr create when done.\n\n{prompt}",
                                "enqueued_by": "dispatch-poller-retry",
                            },
                            headers={"X-API-Key": _api_key},
                            timeout=10,
                        )
                        print(f"[DISPATCH] Re-enqueued {story_id} for retry", flush=True)
                    except Exception as retry_exc:
                        print(f"[DISPATCH] Failed to re-enqueue {story_id}: {retry_exc}", flush=True)
            else:
                print(f"[DISPATCH] failed {story_id} (rc={rc})", flush=True)
        except Exception as exc:
            error = True
            print(f"[DISPATCH] failed {story_id}: {exc}", flush=True)
            print(f"[DISPATCH] SDK process error for {story_id}: {exc}", flush=True)
        finally:
            duration = int(time.time() - start_time)
            # Report final state to central dispatch queue (best-effort)
            if _base_url and _api_key:
                # Common kwargs for _report_fail auto-retry
                _fail_ctx = dict(
                    repo=repo, scope=scope, prompt=prompt,
                    pr_branch=pr_branch, base_story_id=base_story_id,
                )
                if error:
                    _report_fail(
                        session=_session,
                        base_url=_base_url,
                        api_key=_api_key,
                        story_id=story_id,
                        exit_code=rc if rc >= 0 else None,
                        **_fail_ctx,
                        error_message=_truncate_failure_reason(
                            "sdk_died",
                            str(locals().get("exc") or phase_reason or f"rc={rc}"),
                        ),
                    )
                elif locals().get("validation_commit_sha"):
                    _report_complete(
                        session=_session,
                        base_url=_base_url,
                        api_key=_api_key,
                        story_id=story_id,
                        commit_sha=locals()["validation_commit_sha"],
                        pr_number=locals().get("validation_pr_number"),
                        duration_seconds=duration,
                        error=False,
                    )
                else:
                    print(
                        f"[DISPATCH] no commit SHA for {story_id}, reporting as fail",
                        flush=True,
                    )
                    _report_fail(
                        session=_session,
                        base_url=_base_url,
                        api_key=_api_key,
                        story_id=story_id,
                        exit_code=rc if rc >= 0 else None,
                        **_fail_ctx,
                        error_message=_truncate_failure_reason(
                            "no_commit_sha",
                            f"session completed but no pushed branch/commit found rc={rc}",
                        ),
                    )
            # Always clear local queue so poller can pick up next story
            try:
                sys.path.insert(0, "/opt/agent")
                from work_queue import WorkQueue
                wq = WorkQueue(path=_get_queue_path())
                wq.complete(story_id)
                print(f"[DISPATCH] Cleared {story_id} from local queue", flush=True)
            except Exception as exc:
                print(f"[DISPATCH] Warning: failed to clear local queue: {exc}", flush=True)

    import threading
    threading.Thread(target=_run_and_complete, daemon=True, name=f"sdk-{story_id}").start()


# ---------------------------------------------------------------------------
# Single poll cycle
# ---------------------------------------------------------------------------

MAX_RETRIES = 3  # Max consecutive 409 retries per cycle


def poll_once(
    *,
    session: requests.Session | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    agent_name: str | None = None,
    workspace: str | None = None,
) -> str:
    """Execute a single poll cycle.

    All parameters are optional — if omitted, values are resolved from
    environment variables (DISPATCH_API_BASE / OPS_CONSOLE_URL,
    DISPATCH_API_KEY / OPS_CONSOLE_API_KEY, AGENT_NAME, AGENT_WORKSPACE).
    This allows the function to be called without arguments in tests.

    Returns:
        "busy"    — agent is not idle, or rate-limit budget is low
        "paused"  — rate-limit pause flag is active (no claim issued)
        "empty"   — queue has no pending/paused stories
        "claimed" — successfully claimed and started a story
        "error"   — network or unexpected error
    """
    # Resolve parameters from environment if not provided
    if base_url is None:
        base_url = os.environ.get("DISPATCH_API_BASE") or os.environ.get("OPS_CONSOLE_URL", "")
    if api_key is None:
        api_key = os.environ.get("DISPATCH_API_KEY") or os.environ.get("OPS_CONSOLE_API_KEY", "")
    if agent_name is None:
        agent_name = os.environ.get("AGENT_NAME", "")
    if workspace is None:
        workspace = os.environ.get("AGENT_WORKSPACE", "/home/hermes/workspace")
    if session is None:
        session = requests.Session()

    if not is_agent_idle():
        print("[DISPATCH] busy, skipping", flush=True)
        return "busy"

    # Re-check the rate-limit pause flag BEFORE hitting /dispatch/next.
    # poll_loop already gates on this, but _run_and_complete runs in a daemon
    # thread and writes the flag AFTER the SDK exits — meaning the flag may
    # appear between poll_loop's check and poll_once's claim.
    if _is_paused():
        print("[DISPATCH] paused (rate-limited), skipping claim", flush=True)
        return "paused"

    # STORY-507 AC-7: Pre-claim rate-limit budget check. If fewer than 15
    # minutes remain in the current 5-hour block, skip claim. Fail-open on
    # ccusage errors so a broken installation never permanently blocks.
    if _check_rate_limit_budget():
        print("[DISPATCH] busy (rate-limit budget low), skipping claim", flush=True)
        return "busy"

    headers = {"X-API-Key": api_key, "X-Agent-Name": agent_name}

    for attempt in range(MAX_RETRIES):
        try:
            resp = session.get(
                f"{base_url}/api/dispatch/next",
                headers=headers,
                timeout=10,
            )
        except Exception as exc:
            print(f"[DISPATCH] Error fetching next story: {exc}", flush=True)
            return "error"

        if resp.status_code == 204:
            print("[DISPATCH] queue empty", flush=True)
            return "empty"

        if resp.status_code != 200:
            print(f"[DISPATCH] Unexpected status from /next: {resp.status_code}", flush=True)
            return "error"

        data = resp.json()
        item = data["item"]
        story_id = item["story_id"]
        title = item.get("title") or ""
        label = f"{story_id} — {title}" if title else story_id
        print(f"[DISPATCH] Found {label}, claiming...", flush=True)

        # Attempt to claim
        try:
            claim_resp = session.post(
                f"{base_url}/api/dispatch/claim/{story_id}",
                json={"agent_name": agent_name},
                headers=headers,
                timeout=10,
            )
        except Exception as exc:
            print(f"[DISPATCH] Error claiming {story_id}: {exc}", flush=True)
            return "error"

        if claim_resp.status_code == 200:
            print(f"[DISPATCH] Claimed {label}", flush=True)
            claim_data = {}
            try:
                claim_data = claim_resp.json()
            except Exception:
                pass
            needs_info_path = None
            if isinstance(claim_data, dict):
                needs_info_path = claim_data.get("needs_info_path")
                if needs_info_path is None:
                    nested_item = claim_data.get("item")
                    if isinstance(nested_item, dict):
                        needs_info_path = nested_item.get("needs_info_path")
            start_story(
                story_id=story_id,
                repo=item["repo"],
                scope=item.get("scope", "small"),
                prompt=item["prompt"],
                workspace=workspace,
                base_url=base_url,
                api_key=api_key,
                pr_branch=item.get("pr_branch", ""),
                base_story_id=item.get("base_story_id", ""),
                rework_of=item.get("rework_of"),
                resumed_question_path=needs_info_path,
            )
            return "claimed"

        if claim_resp.status_code == 409:
            print(f"[DISPATCH] {story_id} already claimed, retrying...", flush=True)
            continue

        print(f"[DISPATCH] Unexpected claim status: {claim_resp.status_code}", flush=True)
        return "error"

    print("[DISPATCH] Max retries reached", flush=True)
    return "error"


# ---------------------------------------------------------------------------
# Continuous polling loop
# ---------------------------------------------------------------------------


def poll_loop(
    *,
    base_url: str,
    api_key: str,
    agent_name: str,
    workspace: str,
    poll_interval: int = 60,
) -> None:
    """Run the dispatch polling loop forever.

    STORY-040: Includes periodic reconciliation of stale queue entries.
    Catches all exceptions except KeyboardInterrupt to ensure
    the polling daemon never crashes silently.
    """
    print(f"[DISPATCH] Starting polling loop: interval={poll_interval}s agent={agent_name}", flush=True)
    session = requests.Session()
    last_reconcile = time.time()

    while True:
        try:
            # STORY-253: skip claim cycle if SDK is rate-limited. The pause
            # flag is written by _run_and_complete when it detects the Claude
            # Code "You've hit your limit" message. Without this skip, the
            # poller burns through claims at 1 second each (each fast-fails
            # on the limit), masquerades them as completions, and creates
            # ghost-completed stories.
            if os.path.exists("/var/run/dispatch-poller-paused-until"):
                try:
                    with open("/var/run/dispatch-poller-paused-until") as f:
                        until = f.read().strip()
                except Exception:
                    until = ""

                # Auto-clear: parse the "resets X" string and unpause the
                # moment the wall clock passes that time. Falls back to a
                # 1h conservative cap if the string can't be parsed.
                cleared = False
                until_ts = _parse_reset_time(until)
                if until_ts is not None and time.time() >= until_ts:
                    try:
                        os.remove("/var/run/dispatch-poller-paused-until")
                        print(f"[DISPATCH] reset window passed ({until}) — unpausing", flush=True)
                        cleared = True
                    except Exception as exc:
                        print(f"[DISPATCH] failed to clear pause flag: {exc}", flush=True)
                elif until_ts is None:
                    # Couldn't parse — apply a 1h conservative cap (Claude
                    # Code reset windows are typically ≤1h on Team plans).
                    try:
                        age = time.time() - os.path.getmtime("/var/run/dispatch-poller-paused-until")
                        if age > 3600:
                            os.remove("/var/run/dispatch-poller-paused-until")
                            print(f"[DISPATCH] cleared pause flag (couldn't parse '{until}', >1h cap)", flush=True)
                            cleared = True
                    except Exception:
                        pass

                if cleared:
                    # Fall through to poll_once on the same tick — no need to
                    # wait another 60s after the reset just clicked over.
                    poll_once(
                        session=session,
                        base_url=base_url,
                        api_key=api_key,
                        agent_name=agent_name,
                        workspace=workspace,
                    )
                else:
                    print(f"[DISPATCH] PAUSED — Claude Code rate limit, resets {until}. Skipping poll.", flush=True)
            else:
                poll_once(
                    session=session,
                    base_url=base_url,
                    api_key=api_key,
                    agent_name=agent_name,
                    workspace=workspace,
                )
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            print(f"[DISPATCH] Poll cycle error: {exc}", flush=True)

        # STORY-040: Periodic reconciliation — every 5 minutes
        now = time.time()
        if now - last_reconcile >= RECONCILE_INTERVAL:
            try:
                reconcile_stale_queue()
            except Exception as exc:
                print(f"[DISPATCH] Reconciliation error: {exc}", flush=True)
            last_reconcile = now

        time.sleep(poll_interval)


# ---------------------------------------------------------------------------
# Entry point (started as daemon thread from health_server.py)
# ---------------------------------------------------------------------------


def start_dispatch_poller() -> None:
    """Start the dispatch polling loop as a daemon thread.

    Reads configuration from environment variables.
    """
    import threading

    base_url = os.environ.get("OPS_CONSOLE_URL", "")
    api_key = os.environ.get("OPS_CONSOLE_API_KEY", "")
    agent_name = os.environ.get("AGENT_NAME", "")
    workspace = os.environ.get("AGENT_WORKSPACE", "/home/hermes/workspace")
    poll_interval = int(os.environ.get("DISPATCH_POLL_INTERVAL", "60"))

    if not base_url or not api_key or not agent_name:
        print("[DISPATCH] Missing required env vars (OPS_CONSOLE_URL, OPS_CONSOLE_API_KEY, AGENT_NAME), poller disabled", flush=True)
        return

    thread = threading.Thread(
        target=poll_loop,
        kwargs={
            "base_url": base_url,
            "api_key": api_key,
            "agent_name": agent_name,
            "workspace": workspace,
            "poll_interval": poll_interval,
        },
        daemon=True,
        name="dispatch-poller",
    )
    thread.start()
    print(f"[DISPATCH] Poller thread started: {thread.name}", flush=True)
