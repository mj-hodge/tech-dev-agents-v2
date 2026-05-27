"""STORY-734: Morris Operating Mode Controller.

Manages Morris's operating mode (full / light / minimal) based on
quota consumption and time-of-day. Mode state persists in a JSON file
so every cron invocation reads the same state.

Public API:
    get_mode()                  → "full" | "light" | "minimal"
    set_mode(mode, reason, actor, config?, session?, quota_pct?)
    check_and_auto_transition(config, session) → current mode
    get_quota_pct(config, session?) → float | None
    overnight_window(config)    → bool
    mode_allows(mode, job_class) → bool
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MODE_STATE_PATH = Path(os.environ.get("MORRIS_MODE_STATE", "/opt/morris/mode_state.json"))

MODES = ("full", "light", "minimal")

# Job-class → set of modes that allow it
JOB_CLASS: dict[str, set[str]] = {
    "shell_infra":       {"full", "light", "minimal"},
    "orchestrator_poll": {"full", "light"},
    "briefing":          {"full"},
    "fleet_check_llm":   {"full"},
    "self_improvement":  {"full"},
    "contact_summary":   {"full"},
}


# ---------------------------------------------------------------------------
# Mode state I/O
# ---------------------------------------------------------------------------


def _read_state() -> dict:
    """Read mode state from disk. Returns default state on any error."""
    try:
        if MODE_STATE_PATH.exists():
            data = json.loads(MODE_STATE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("mode") in MODES:
                return data
            logger.warning("[MODE] Invalid mode state data, defaulting to full")
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("[MODE] Could not read %s: %s — defaulting to full", MODE_STATE_PATH, exc)
    return {"mode": "full", "set_at": datetime.now(timezone.utc).isoformat(), "reason": "default", "actor": "system"}


def _write_state(state: dict) -> None:
    """Atomically write mode state (temp file + rename)."""
    MODE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(MODE_STATE_PATH.parent),
        prefix=".mode_state_",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
            f.write("\n")
        os.rename(tmp_path, str(MODE_STATE_PATH))
    except Exception:
        # Clean up temp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_mode() -> str:
    """Return the current operating mode. Defaults to 'full' on any error."""
    return _read_state().get("mode", "full")


def mode_allows(mode: str, job_class: str) -> bool:
    """Return True if *mode* permits *job_class* to run."""
    allowed_modes = JOB_CLASS.get(job_class, set())
    return mode in allowed_modes


def set_mode(
    mode: str,
    reason: str,
    actor: str,
    config: Optional[dict] = None,
    session: Optional[Any] = None,
    quota_pct: Optional[float] = None,
) -> None:
    """Persist a new operating mode and notify via Teams DM.

    Parameters
    ----------
    mode : str
        One of "full", "light", "minimal".
    reason : str
        Machine-readable reason (e.g. "quota_approaching", "overnight_schedule").
    actor : str
        Who triggered: "auto", "cron", "mark", "admin".
    config : dict, optional
        Orchestrator config (needed for Teams DM).
    session : requests.Session, optional
        HTTP session (needed for Teams DM).
    quota_pct : float, optional
        Current quota percentage at time of transition.
    """
    if mode not in MODES:
        raise ValueError(f"Invalid mode: {mode!r}. Must be one of {MODES}")

    state = {
        "mode": mode,
        "set_at": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
        "actor": actor,
        "quota_pct_at_set": quota_pct,
    }

    _write_state(state)
    logger.info("[MODE] Morris → %s (reason=%s, actor=%s, quota=%s)", mode, reason, actor, quota_pct)

    # Post Teams DM
    if config is not None:
        _post_transition_dm(mode, reason, actor, quota_pct, config, session)


def _post_transition_dm(
    mode: str,
    reason: str,
    actor: str,
    quota_pct: Optional[float],
    config: dict,
    session: Optional[Any],
) -> None:
    """Post a Teams DM about the mode transition."""
    pct_str = f"{quota_pct:.0%}" if quota_pct is not None else "unknown"

    if mode == "full":
        detail = "All jobs running normally."
    elif mode == "light":
        detail = "Shell jobs continue. Claude-consuming jobs suspended."
    else:  # minimal
        detail = "Only graph token + cost monitor running.\nOrchestrator loop suspended until quota recovers."

    bullets = [
        f"Reason: {reason} (quota at {pct_str})",
        detail,
        'To override: message Morris "set mode full"',
    ]

    try:
        from interventions import post_dm
        post_dm(
            "[MODE]",
            f"Morris → {mode}",
            bullets,
            session,
            config,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[MODE] Failed to post transition DM: %s", exc)


def get_quota_pct(config: dict, session: Optional[Any] = None) -> Optional[float]:
    """Fetch quota percentage from ops console. Returns None on any error (fail-open).

    The endpoint returns {"percent_used": 0.73, ...}.
    """
    try:
        base_url = config.get("ops_console", {}).get("url", "").rstrip("/")
        if not base_url:
            logger.warning("[MODE] No ops_console URL in config — cannot check quota")
            return None

        if session is not None:
            resp = session.get(
                f"{base_url}/api/agents/morris/quota",
                timeout=10,
            )
            data = resp.json()
        else:
            # Fallback: use urllib if no session available
            import urllib.request
            import urllib.error
            api_key = config.get("ops_console", {}).get("api_key", "")
            req = urllib.request.Request(
                f"{base_url}/api/agents/morris/quota",
                headers={"X-Api-Key": api_key, "Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())

        pct = data.get("percent_used")
        if pct is None or data.get("source") == "unavailable":
            logger.warning("[MODE] Quota endpoint returned unavailable data: %s", data)
            return None

        return float(pct)

    except Exception as exc:  # noqa: BLE001
        logger.warning("[MODE] Quota check failed (fail-open): %s", exc)
        return None


def overnight_window(config: dict) -> bool:
    """Return True if the current time is within the overnight window (UTC)."""
    modes_cfg = config.get("operating_modes", {})
    start_str = modes_cfg.get("overnight_start_utc", "05:00")
    end_str = modes_cfg.get("overnight_end_utc", "12:00")

    now = datetime.now(timezone.utc)
    now_minutes = now.hour * 60 + now.minute

    start_parts = start_str.split(":")
    start_minutes = int(start_parts[0]) * 60 + int(start_parts[1])

    end_parts = end_str.split(":")
    end_minutes = int(end_parts[0]) * 60 + int(end_parts[1])

    if start_minutes <= end_minutes:
        return start_minutes <= now_minutes < end_minutes
    else:
        # Wraps midnight
        return now_minutes >= start_minutes or now_minutes < end_minutes


def check_and_auto_transition(config: dict, session: Optional[Any] = None) -> str:
    """Read quota, apply threshold logic, transition if needed. Returns current mode."""
    state = _read_state()
    current_mode = state.get("mode", "full")

    # Respect manual-override cooldown
    if state.get("actor") == "mark":
        cooldown_min = config.get("operating_modes", {}).get("mark_cooldown_minutes", 120)
        set_at_str = state.get("set_at", "")
        try:
            set_at = datetime.fromisoformat(set_at_str)
            if set_at.tzinfo is None:
                set_at = set_at.replace(tzinfo=timezone.utc)
            elapsed_min = (datetime.now(timezone.utc) - set_at).total_seconds() / 60
            if elapsed_min < cooldown_min:
                logger.info(
                    "[MODE] Mark override active (%.0f min ago, cooldown=%d min) — skipping auto-transition",
                    elapsed_min, cooldown_min,
                )
                return current_mode
        except (ValueError, TypeError):
            pass  # Can't parse set_at — proceed with auto-transition

    # Get quota
    quota_pct = get_quota_pct(config, session)
    if quota_pct is None:
        logger.info("[MODE] Quota unavailable — skipping auto-transition (fail-open)")
        return current_mode

    modes_cfg = config.get("operating_modes", {})
    minimal_threshold = modes_cfg.get("minimal_quota_threshold", 0.90)
    light_threshold = modes_cfg.get("light_quota_threshold", 0.70)
    recovery_to_light = modes_cfg.get("recovery_to_light_threshold", 0.60)
    recovery_to_full = modes_cfg.get("recovery_to_full_threshold", 0.50)

    # Apply transition logic
    if quota_pct >= minimal_threshold and current_mode != "minimal":
        set_mode("minimal", "quota_critical", "auto", config, session, quota_pct)
        return "minimal"

    if quota_pct >= light_threshold and current_mode == "full":
        set_mode("light", "quota_approaching", "auto", config, session, quota_pct)
        return "light"

    if quota_pct < recovery_to_light and current_mode == "minimal":
        set_mode("light", "quota_recovering", "auto", config, session, quota_pct)
        return "light"

    if quota_pct < recovery_to_full and current_mode == "light" and not overnight_window(config):
        set_mode("full", "quota_healthy", "auto", config, session, quota_pct)
        return "full"

    return current_mode
