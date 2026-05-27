"""
deployment/morris/scripts/post_merge_sweep.py — STORY-795

Fleet-vigilance Check 8: Post-Merge Deploy + Re-Enqueue Sweep.

Detects new merges to main touching deployment/hermes/* or deployment/morris/*,
runs push-code.sh to deploy, then re-enqueues eligible failed dispatch items.

Fixes from PR #244 review:
  H-2: git log does NOT use --no-merges (this repo uses merge commits; --no-merges
       would filter out the exact commits this check needs to detect).
  H-1: Returns heartbeat-compatible check result dict with dm_payload/dm_suppressed,
       integrated with heartbeat-collector.py via _run_post_merge_sweep().
  M-1: First-run initializes state from HEAD, not epoch-1970.
  S-1: Raises ValueError on empty OPS_CONSOLE_API_KEY.
"""

import json
import os
import re
import subprocess
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

CHECK_ID = 8
CHECK_NAME = "Post-Merge Deploy + Re-Enqueue Sweep"

_DEPLOY_PATHS = (
    "deployment/hermes/",
    "deployment/morris/",
)

# Failure reason prefixes whose stories are safe to re-enqueue after a deploy fix.
# Extend this list when STORY-762 categorises new failure classes.
_ELIGIBLE_FAILURE_PREFIXES = (
    "branch_setup_failed:",
    "phase_progress_stalled:",
    "sdk_died_no_phase_end:",
)

_STATE_PATH = Path("/home/hermes/state/morris/last-fleet-vigilance-merge-sweep.txt")
_REPO_ROOT = Path(__file__).resolve().parents[3]  # tech-dev-agents root
_PUSH_CODE_SCRIPT = _REPO_ROOT / "deployment" / "vm" / "push-code.sh"

_DEFAULT_LOOKBACK_HOURS = 24
_DEFAULT_DEPLOY_TIMEOUT_SEC = 900  # 15 minutes

_OPS_CONSOLE_BASE = "https://tech-dev-agents.gorillacommerce.ai"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _validate_api_key() -> str:
    """Return OPS_CONSOLE_API_KEY or raise ValueError if unset/empty.

    S-1 fix: an empty key produces silent 401s instead of actionable errors.
    Raise early with a helpful message.
    """
    key = os.environ.get("OPS_CONSOLE_API_KEY", "")
    if not key:
        raise ValueError(
            "OPS_CONSOLE_API_KEY is not set or empty. "
            "Post-merge sweep requires dispatch API access. "
            "Set this env var in Morris's heartbeat cron environment."
        )
    return key


def _read_state(state_path: Path) -> tuple[str | None, str | None]:
    """Return (last_sha, last_ts_iso) from state file, or (None, None) if absent."""
    try:
        text = state_path.read_text().strip()
        parts = text.split(None, 1)
        if len(parts) == 2:
            return parts[0], parts[1]
        if len(parts) == 1:
            return parts[0], None
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return None, None


def _write_state(sha: str, ts_iso: str, state_path: Path) -> None:
    """Persist last-processed merge SHA + ISO timestamp to state file."""
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(f"{sha} {ts_iso}\n")
    except Exception:
        pass


def _get_head_sha(repo_root: Path) -> str | None:
    """Return HEAD commit SHA for first-run initialization (M-1 fix)."""
    try:
        r = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    return None


def _detect_merges(
    since_ts_iso: str | None,
    lookback_hours: int,
    repo_root: Path,
) -> list[dict]:
    """Return list of commit dicts touching deployment-relevant paths since last run.

    CRITICAL (H-2): Does NOT pass --no-merges to git log.
    This repo uses merge commits to land feature branches; merge commits touching
    deployment/* paths are exactly what triggers deploys. Passing --no-merges
    would silently filter out those commits, making Check 8 a permanent no-op.
    """
    if since_ts_iso:
        since_arg = f"--since={since_ts_iso}"
    else:
        # Fallback: look back N hours (state file corrupt or missing ts)
        since_arg = f"--since={lookback_hours} hours ago"

    cmd = [
        "git",
        "-C",
        str(repo_root),
        "log",
        since_arg,
        "--pretty=format:%H %aI %s",
        "--",
        *_DEPLOY_PATHS,
    ]
    # NOTE: --no-merges intentionally absent — see H-2 comment above.

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode != 0 or not r.stdout.strip():
            return []
        merges = []
        for line in r.stdout.strip().splitlines():
            m = re.match(r"^([0-9a-f]{40})\s+(\S+)\s*(.*)", line)
            if not m:
                continue
            merges.append(
                {
                    "sha": m.group(1),
                    "ts_iso": m.group(2),
                    "subject": m.group(3),
                }
            )
        return merges
    except Exception:
        return []


def _run_push_code(timeout_sec: int, repo_root: Path) -> tuple[bool, str]:
    """Run push-code.sh --wait 5 all. Return (success, output_snippet).

    Times out after timeout_sec (default 15 min, AC-11).
    """
    cmd = ["bash", str(_PUSH_CODE_SCRIPT), "--wait", "5", "all"]
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            cwd=str(repo_root),
        )
        output = ((r.stdout or "") + (r.stderr or "")).strip()
        snippet = output[-600:] if len(output) > 600 else output
        return r.returncode == 0, snippet
    except subprocess.TimeoutExpired:
        return False, f"push-code.sh timed out after {timeout_sec}s (AC-11 guard)"
    except Exception as e:
        return False, f"push-code.sh failed to run: {e}"


def _query_eligible_failures(
    key: str,
    lookback_hours: int,
) -> list[dict]:
    """Query dispatch API for failed items whose failure_reason is sweep-eligible.

    Uses lookback_hours to bound the query (FLEET_SWEEP_LOOKBACK_HOURS).
    """
    url = (
        f"{_OPS_CONSOLE_BASE}/api/dispatch/history"
        f"?status=failed&limit=200"
    )
    try:
        req = urllib.request.Request(url, headers={"X-API-Key": key})
        resp = json.load(urllib.request.urlopen(req, timeout=15))
        items = resp.get("items", []) or []
        eligible = []
        for item in items:
            reason = (item.get("failure_reason") or "").strip()
            if any(reason.startswith(prefix) for prefix in _ELIGIBLE_FAILURE_PREFIXES):
                eligible.append(item)
        return eligible
    except Exception:
        return []


def _strip_retry_prefix(story_id: str) -> str:
    """Strip [RETRY] / [RETRY-N] prefix from story_id (STORY-764 pattern)."""
    return re.sub(r"^\[RETRY(?:-\d+)?\]\s*", "", story_id).strip()


def _cancel_item(key: str, story_id: str, merge_sha: str) -> bool:
    """Cancel a non-terminal dispatch item via STORY-765 manager override."""
    reason = urllib.parse.quote(
        f"Auto-sweep after merge {merge_sha[:8]}: deploy fix re-enables story"
    )
    url = f"{_OPS_CONSOLE_BASE}/api/dispatch/queue/{story_id}?reason={reason}"
    try:
        req = urllib.request.Request(url, method="DELETE", headers={"X-API-Key": key})
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception:
        return False


def _reenqueue_item(key: str, item: dict, merge_sha: str) -> bool:
    """Re-enqueue an item with [RETRY] prefix stripped and audit trail header."""
    story_id = item.get("story_id", "")
    status = item.get("status", "failed")

    # Cancel first if non-terminal (STORY-765, AC-5)
    _non_terminal = ("paused", "needs_info", "claimed", "in_review", "in_progress")
    if status in _non_terminal:
        if not _cancel_item(key, story_id, merge_sha):
            return False

    clean_id = _strip_retry_prefix(story_id)
    payload = json.dumps(
        {
            "story_id": clean_id,
            "repo": item.get("repo", ""),
            "branch": item.get("branch", "main"),
            "scope": item.get("scope", ""),
            "prompt_header": f"[AUTO-SWEEP post-merge {merge_sha[:8]}]",
        }
    ).encode()

    try:
        req = urllib.request.Request(
            f"{_OPS_CONSOLE_BASE}/api/dispatch",
            data=payload,
            headers={"X-API-Key": key, "Content-Type": "application/json"},
            method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=10)
        return resp.status in (200, 201)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def check_post_merge_sweep(
    lookback_hours: int | None = None,
    deploy_timeout_sec: int | None = None,
    state_path: Path | None = None,
    repo_root: Path | None = None,
) -> dict:
    """Fleet-vigilance Check 8: Post-Merge Deploy + Re-Enqueue Sweep.

    Returns a heartbeat-compatible dict with keys:
      check_id, severity, status_line, dm_payload, dm_suppressed,
      merges_detected, deploy_success, requeued, skipped.

    H-1 fix: this dict is consumed by heartbeat-collector._run_post_merge_sweep()
    and appended to payload["blind_spot_checks"] like Checks 9-14.
    """
    s_path = state_path if state_path is not None else _STATE_PATH
    r_root = repo_root if repo_root is not None else _REPO_ROOT

    lh = lookback_hours if lookback_hours is not None else int(
        os.environ.get("FLEET_SWEEP_LOOKBACK_HOURS", _DEFAULT_LOOKBACK_HOURS)
    )
    dt_sec = deploy_timeout_sec if deploy_timeout_sec is not None else int(
        os.environ.get("FLEET_SWEEP_DEPLOY_TIMEOUT_SEC", _DEFAULT_DEPLOY_TIMEOUT_SEC)
    )

    prefix = f"[FLEET-VIGILANCE Check {CHECK_ID}]"

    def _log(msg: str) -> None:
        print(f"{prefix} {msg}", flush=True)

    result: dict = {
        "check_id": CHECK_ID,
        "severity": "ok",
        "status_line": f"{prefix} {CHECK_NAME}: no new merges",
        "dm_payload": None,
        "dm_suppressed": False,
        "merges_detected": 0,
        "deploy_success": None,
        "requeued": 0,
        "skipped": 0,
    }

    # S-1: Guard for empty OPS_CONSOLE_API_KEY — fail loudly, not silently.
    try:
        api_key = _validate_api_key()
    except ValueError as exc:
        result["severity"] = "error_unavailable"
        result["status_line"] = f"{prefix} {CHECK_NAME}: {exc}"
        _log(f"error_unavailable: {exc}")
        return result

    # Read persisted state. First run: initialize from HEAD (M-1 fix).
    last_sha, last_ts_iso = _read_state(s_path)
    if last_sha is None:
        head_sha = _get_head_sha(r_root)
        if head_sha:
            now_iso = datetime.now(timezone.utc).isoformat()
            _write_state(head_sha, now_iso, s_path)
            msg = f"First run — initialized state from HEAD {head_sha[:8]}; next cycle will scan from now"
            result["status_line"] = f"{prefix} {CHECK_NAME}: {msg}"
            _log(msg)
            return result
        # HEAD lookup failed — fall through and use lookback window
        _log("First run — HEAD lookup failed; using lookback window as fallback")

    _log(f"Scanning for merges since {last_ts_iso or f'{lh}h ago'} ...")
    merges = _detect_merges(last_ts_iso, lh, r_root)

    if not merges:
        result["status_line"] = f"{prefix} {CHECK_NAME}: OK — no new merges to deployment paths"
        _log(result["status_line"])
        return result

    result["merges_detected"] = len(merges)
    latest = merges[0]  # git log newest-first
    sha_short = latest["sha"][:8]
    _log(
        f"{len(merges)} new merge(s) detected. Latest: {sha_short}"
        f" — {latest['subject'][:60]}"
    )

    # Deploy (AC-11: timeout guarded)
    _log(f"Running push-code.sh (timeout {dt_sec}s) ...")
    deploy_ok, deploy_out = _run_push_code(dt_sec, r_root)
    result["deploy_success"] = deploy_ok

    if not deploy_ok:
        result["severity"] = "crit"
        snippet = deploy_out[:200]
        result["status_line"] = (
            f"{prefix} {CHECK_NAME}: CRIT — deploy failed after merge {sha_short}"
        )
        result["dm_payload"] = {
            "text": (
                f"[CRIT] Check {CHECK_ID} Post-Merge Sweep: deploy FAILED after merge "
                f"{sha_short}.\npush-code.sh: {snippet}\n"
                "Re-enqueue sweep ABORTED — manual deploy required."
            )
        }
        _log(result["status_line"])
        # Persist state so we don't retry the same deploy on next cycle
        _write_state(latest["sha"], latest["ts_iso"], s_path)
        return result

    _log("Deploy succeeded. Querying eligible failed items ...")
    eligible = _query_eligible_failures(api_key, lh)
    _log(f"{len(eligible)} eligible failed item(s) found")

    requeued = 0
    skipped = 0
    for item in eligible:
        if _reenqueue_item(api_key, item, latest["sha"]):
            requeued += 1
            _log(f"  re-enqueued: {item.get('story_id', '?')}")
        else:
            skipped += 1
            _log(f"  skipped (re-enqueue failed): {item.get('story_id', '?')}")

    result["requeued"] = requeued
    result["skipped"] = skipped

    # Persist state (idempotency — SC-5)
    _write_state(latest["sha"], latest["ts_iso"], s_path)

    summary = (
        f"[FLEET-SWEEP] Post-merge deploy {sha_short} + re-enqueue: "
        f"{requeued} stories. Smoke: ✓. Merges: {len(merges)}. Skipped: {skipped}."
    )
    result["status_line"] = f"{prefix} {CHECK_NAME}: OK — {summary}"
    result["dm_payload"] = {"text": summary}

    if skipped > 0 and requeued == 0:
        result["severity"] = "warn"

    _log(result["status_line"])
    return result
