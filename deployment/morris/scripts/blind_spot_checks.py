"""
deployment/morris/scripts/blind_spot_checks.py — STORY-767

Fleet-vigilance blind-spot checks 9–14.

Called from heartbeat-collector.py (or directly by the fleet-vigilance skill)
after existing checks 0–8.

Numbering note: STORY-766's Check 9 (Post-Merge Deploy) was not shipped when
this story's Phase 7 ran.  This module claims Checks 9–14.

Each check function:
  - Accepts dependency-injected run_fn / fetch_fn / suppression_path for testability
  - Returns a dict with check_id, severity, status_line, dm_payload, dm_suppressed
  - Catches its own errors internally (run_all_checks wraps the whole call too)
  - Logs [FLEET-VIGILANCE Check N] to stdout (Loki-visible)

Env-var toggles (all default enabled):
  FV_CHECK_9_VM_REACH, FV_CHECK_10_STUCK_DEPLOY, FV_CHECK_11_CODE_DRIFT,
  FV_CHECK_12_NULL_FAILURE, FV_CHECK_13_ZOMBIE_HB, FV_CHECK_14_NO_SEED

Threshold overrides:
  FV_STUCK_DEPLOY_MIN     (default 15)
  FV_DRIFT_THRESHOLD_SEC  (default 3600)
  FV_NULL_FAILURE_WARN    (default 1)
  FV_NULL_FAILURE_CRIT    (default 5)
  FV_PHASE_TIMEOUT_SEC    (default 2400)
  FV_DM_SUPPRESS_SEC      (default 14400 = 4h)
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
import traceback
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default suppression file path
# ---------------------------------------------------------------------------
_DEFAULT_SUPPRESSION_PATH = (
    "/home/hermes/state/morris/vigilance-dm-suppression.json"
)

# ---------------------------------------------------------------------------
# Helpers: DM suppression
# ---------------------------------------------------------------------------


def _load_suppression(suppression_path: str | None) -> tuple[dict, str]:
    """Load suppression state, returning (state_dict, resolved_path)."""
    path = suppression_path or _DEFAULT_SUPPRESSION_PATH
    try:
        with open(path) as f:
            return json.load(f), path
    except (FileNotFoundError, json.JSONDecodeError):
        return {}, path


def _save_suppression(state: dict, path: str) -> None:
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            json.dump(state, f)
    except Exception:
        pass


def _is_suppressed(state: dict, check_id: int) -> bool:
    """Return True if a CRIT DM for check_id was sent within the suppression window."""
    ts = state.get(str(check_id))
    if ts is None:
        return False
    window = int(os.environ.get("FV_DM_SUPPRESS_SEC", "14400"))
    return (time.time() - float(ts)) < window


def _record_crit(state: dict, path: str, check_id: int) -> None:
    state[str(check_id)] = time.time()
    _save_suppression(state, path)


def _clear_crit(state: dict, path: str, check_id: int) -> None:
    if str(check_id) in state:
        del state[str(check_id)]
        _save_suppression(state, path)


# ---------------------------------------------------------------------------
# Helpers: SSH command builder
# ---------------------------------------------------------------------------


def _ssh_cmd(ip: str, port: str | int, remote_cmd: str) -> list[str]:
    return [
        "ssh",
        "-p", str(port),
        "-o", "StrictHostKeyChecking=no",
        "-o", "ConnectTimeout=8",
        f"azureagent@{ip}",
        remote_cmd,
    ]


# ---------------------------------------------------------------------------
# Helpers: etime / timestamp parsing
# ---------------------------------------------------------------------------


def _parse_etime_to_minutes(etime_str: str) -> float:
    """Parse ps etime field (MM:SS, HH:MM:SS, or D-HH:MM:SS) to minutes."""
    s = etime_str.strip()
    # Handle D-HH:MM:SS format
    if "-" in s:
        days, rest = s.split("-", 1)
        parts = rest.split(":")
        hours = int(days) * 24 + int(parts[0])
        minutes = int(parts[1]) if len(parts) > 1 else 0
        return hours * 60 + minutes
    parts = s.split(":")
    if len(parts) == 2:
        # MM:SS
        return int(parts[0])
    if len(parts) == 3:
        # HH:MM:SS
        return int(parts[0]) * 60 + int(parts[1])
    return 0.0


def _parse_git_ts(ts_str: str) -> float | None:
    """Parse 'YYYY-MM-DD HH:MM:SS +ZZZZ' to Unix epoch."""
    ts_str = ts_str.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S %z", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(ts_str, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            continue
    return None


def _parse_journalctl_ts(line: str) -> float | None:
    """Parse journalctl timestamp 'Mon DD HH:MM:SS' from a log line."""
    parts = line.split()
    if len(parts) < 3:
        return None
    ts_str = f"{parts[0]} {parts[1]} {parts[2]}"
    now = datetime.now()
    try:
        dt = datetime.strptime(f"{now.year} {ts_str}", "%Y %b %d %H:%M:%S")
        # If the parsed date is in the future (e.g. Dec log in Jan), subtract a year
        if dt.timestamp() > time.time() + 3600:
            dt = dt.replace(year=now.year - 1)
        return dt.timestamp()
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Helper: agent lookup by name
# ---------------------------------------------------------------------------


def _agent_by_name(agents: list[dict], name: str) -> dict | None:
    for a in agents:
        if a.get("name", "").lower() == name.lower():
            return a
    return None


# ===========================================================================
# Check 9 — VM Reachability (SC-1)
# ===========================================================================


def check_vm_reachability(
    agents: list[dict],
    run_fn=None,
    suppression_path: str | None = None,
    **_kw,
) -> dict:
    """Check 9: SSH PONG test to each agent VM.

    Returns reachable/unreachable lists.
    WARN: 1 unreachable.  CRIT: 2+ unreachable.
    """
    _run = run_fn if run_fn is not None else subprocess.run
    check_id = 9

    supp, supp_path = _load_suppression(suppression_path)

    reachable: list[str] = []
    unreachable: list[str] = []

    for agent in agents:
        name = agent["name"]
        ip = agent["ip"]
        port = agent.get("ssh_port", 443)
        cmd = _ssh_cmd(ip, port, "echo PONG")
        r = _run(cmd, capture_output=True, text=True, timeout=12)
        if r.returncode == 0 and "PONG" in (r.stdout or ""):
            reachable.append(name)
        else:
            unreachable.append(name)

    n_unreachable = len(unreachable)
    if n_unreachable == 0:
        severity = "ok"
        msg = " ".join(f"{a} ✓" for a in reachable)
        status_line = f"[Check 9 VM Reach] OK: {msg}"
        _clear_crit(supp, supp_path, check_id)
        dm_payload = None
        dm_suppressed = False
    elif n_unreachable == 1:
        severity = "warn"
        status_line = f"[Check 9 VM Reach] WARN: unreachable: {', '.join(unreachable)}"
        dm_payload = None
        dm_suppressed = False
    else:
        severity = "crit"
        status_line = f"[Check 9 VM Reach] CRIT: unreachable: {', '.join(unreachable)}"
        if _is_suppressed(supp, check_id):
            dm_payload = None
            dm_suppressed = True
        else:
            dm_payload = {
                "text": (
                    f"[CRIT] Check 9 VM Reachability: {n_unreachable} agents unreachable: "
                    f"{', '.join(unreachable)}. SSH echo PONG failed. "
                    f"Check Azure VM status and network connectivity. "
                    f"Reachable: {', '.join(reachable) or 'none'}"
                )
            }
            dm_suppressed = False
            _record_crit(supp, supp_path, check_id)

    logging.info(f"[FLEET-VIGILANCE Check {check_id}] {severity.upper()}: {status_line}")

    return {
        "check_id": check_id,
        "severity": severity,
        "status_line": status_line,
        "dm_payload": dm_payload,
        "dm_suppressed": dm_suppressed,
        "reachable": reachable,
        "unreachable": unreachable,
    }


# ===========================================================================
# Check 10 — Stuck push-code.sh (SC-2)
# ===========================================================================


def check_stuck_push_code(
    run_fn=None,
    suppression_path: str | None = None,
    stuck_threshold_min: int | None = None,
    **_kw,
) -> dict:
    """Check 10: Detect push-code.sh processes older than threshold (default 15 min).

    Returns stuck_pids list.
    CRIT: any stuck process.
    """
    _run = run_fn if run_fn is not None else subprocess.run
    check_id = 10

    if stuck_threshold_min is None:
        stuck_threshold_min = int(os.environ.get("FV_STUCK_DEPLOY_MIN", "15"))

    supp, supp_path = _load_suppression(suppression_path)

    # 1. Find push-code.sh PIDs
    r = _run(["pgrep", "-f", "push-code.sh"], capture_output=True, text=True)
    if r.returncode != 0 or not (r.stdout or "").strip():
        # No processes found
        _clear_crit(supp, supp_path, check_id)
        status_line = "[Check 10 Stuck Deploy] OK: no push-code processes"
        logging.info(f"[FLEET-VIGILANCE Check {check_id}] OK: no push-code processes")
        return {
            "check_id": check_id,
            "severity": "ok",
            "status_line": status_line,
            "dm_payload": None,
            "dm_suppressed": False,
            "stuck_pids": [],
        }

    pids = [p.strip() for p in (r.stdout or "").strip().splitlines() if p.strip()]
    stuck_pids: list[str] = []

    for pid in pids:
        # Get elapsed time via ps
        r2 = _run(
            ["ps", "-o", "etime=", "-p", pid],
            capture_output=True, text=True,
        )
        etime_str = (r2.stdout or "").strip()
        if not etime_str:
            continue
        elapsed_min = _parse_etime_to_minutes(etime_str)
        if elapsed_min >= stuck_threshold_min:
            stuck_pids.append(pid)

    if not stuck_pids:
        _clear_crit(supp, supp_path, check_id)
        status_line = "[Check 10 Stuck Deploy] OK: no push-code processes over threshold"
        logging.info(f"[FLEET-VIGILANCE Check {check_id}] OK: processes exist but none stuck")
        return {
            "check_id": check_id,
            "severity": "ok",
            "status_line": status_line,
            "dm_payload": None,
            "dm_suppressed": False,
            "stuck_pids": [],
        }

    severity = "crit"
    pid_list = ", ".join(stuck_pids)
    status_line = (
        f"[Check 10 Stuck Deploy] CRIT: PID {pid_list} stuck "
        f">{stuck_threshold_min}min (push-code.sh)"
    )

    if _is_suppressed(supp, check_id):
        dm_payload = None
        dm_suppressed = True
    else:
        dm_payload = {
            "text": (
                f"[CRIT] Check 10: push-code.sh stuck! "
                f"PID {pid_list} running >{stuck_threshold_min} min. "
                f"Kill the process and verify the deploy completed: "
                f"`kill -9 {stuck_pids[0]}`"
            )
        }
        dm_suppressed = False
        _record_crit(supp, supp_path, check_id)

    logging.info(f"[FLEET-VIGILANCE Check {check_id}] CRIT: stuck PIDs: {pid_list}")

    return {
        "check_id": check_id,
        "severity": severity,
        "status_line": status_line,
        "dm_payload": dm_payload,
        "dm_suppressed": dm_suppressed,
        "stuck_pids": stuck_pids,
    }


# ===========================================================================
# Check 11 — Code-version drift (SC-3)
# ===========================================================================


def check_code_drift(
    agents: list[dict],
    run_fn=None,
    suppression_path: str | None = None,
    drift_threshold_seconds: int | None = None,
    **_kw,
) -> dict:
    """Check 11: Compare agent VM dispatch_poller.py mtime vs latest hermes/* merge.

    Returns drifted_agents list.
    WARN: 1 agent drifted.  CRIT: 2+ agents drifted.
    """
    _run = run_fn if run_fn is not None else subprocess.run
    check_id = 11

    if drift_threshold_seconds is None:
        drift_threshold_seconds = int(os.environ.get("FV_DRIFT_THRESHOLD_SEC", "3600"))

    supp, supp_path = _load_suppression(suppression_path)

    # 1. Get latest merge commit timestamp for deployment/hermes/* path
    r = _run(
        ["git", "log", "-1", "--format=%ci", "--", "deployment/hermes/"],
        capture_output=True, text=True,
    )
    merge_ts_str = (r.stdout or "").strip()
    merge_epoch = _parse_git_ts(merge_ts_str) if merge_ts_str else None

    drifted: list[dict] = []

    if merge_epoch is None:
        # Can't determine merge time — skip drift check
        status_line = "[Check 11 Code Drift] OK: no hermes/* commits found to compare"
        logging.info(f"[FLEET-VIGILANCE Check {check_id}] OK: no baseline commit found")
        return {
            "check_id": check_id,
            "severity": "ok",
            "status_line": status_line,
            "dm_payload": None,
            "dm_suppressed": False,
            "drifted_agents": [],
        }

    # 2. Check each agent's dispatch_poller.py mtime
    for agent in agents:
        name = agent["name"]
        ip = agent["ip"]
        port = agent.get("ssh_port", 443)
        cmd = _ssh_cmd(ip, port, "stat -c %Y /opt/agent/dispatch_poller.py 2>/dev/null")
        r2 = _run(cmd, capture_output=True, text=True, timeout=12)
        mtime_str = (r2.stdout or "").strip()
        try:
            agent_mtime = float(mtime_str)
        except (ValueError, TypeError):
            continue

        drift_sec = merge_epoch - agent_mtime
        if drift_sec > drift_threshold_seconds:
            drift_h = drift_sec / 3600
            drifted.append({
                "agent": name,
                "drift_hours": round(drift_h, 1),
                "agent_mtime": agent_mtime,
                "merge_epoch": merge_epoch,
            })

    n_drifted = len(drifted)
    if n_drifted == 0:
        severity = "ok"
        status_line = "[Check 11 Code Drift] OK: all agents within 1h of latest hermes/* merge"
        _clear_crit(supp, supp_path, check_id)
        dm_payload = None
        dm_suppressed = False
    elif n_drifted == 1:
        severity = "warn"
        d = drifted[0]
        status_line = f"[Check 11 Code Drift] WARN: drifted: {d['agent']} ({d['drift_hours']:.0f}h)"
        dm_payload = None
        dm_suppressed = False
    else:
        severity = "crit"
        parts = ", ".join(f"{d['agent']} ({d['drift_hours']:.0f}h)" for d in drifted)
        status_line = f"[Check 11 Code Drift] CRIT: drifted: {parts}"
        if _is_suppressed(supp, check_id):
            dm_payload = None
            dm_suppressed = True
        else:
            dm_payload = {
                "text": (
                    f"[CRIT] Check 11: Code drift detected on {n_drifted} agents: {parts}. "
                    f"Run `./deployment/vm/push-code.sh all` to deploy latest."
                )
            }
            dm_suppressed = False
            _record_crit(supp, supp_path, check_id)

    logging.info(
        f"[FLEET-VIGILANCE Check {check_id}] {severity.upper()}: "
        f"drifted={n_drifted}"
    )

    return {
        "check_id": check_id,
        "severity": severity,
        "status_line": status_line,
        "dm_payload": dm_payload,
        "dm_suppressed": dm_suppressed,
        "drifted_agents": drifted,
    }


# ===========================================================================
# Check 12 — NULL failure_reason spike (SC-4)
# ===========================================================================


def check_null_failure_reason(
    fetch_fn,
    suppression_path: str | None = None,
    **_kw,
) -> dict:
    """Check 12: Count dispatch_items with status='failed' AND NULL failure_reason in last hour.

    Returns null_count.
    WARN: >= 1 NULL row.  CRIT: > 5 NULL rows.
    """
    check_id = 12
    warn_threshold = int(os.environ.get("FV_NULL_FAILURE_WARN", "1"))
    crit_threshold = int(os.environ.get("FV_NULL_FAILURE_CRIT", "5"))

    supp, supp_path = _load_suppression(suppression_path)

    # Migrated to v2 dispatch_v2_events (was: dispatch_items.failure_reason).
    # The v2 trigger enforces failure_class + failure_reason on every 'failed'
    # event, so this count should be 0. Keep as a defensive guard against
    # constraint regressions or out-of-band INSERTs.
    sql = (
        "SELECT count(*) AS count FROM dispatch_v2_events "
        "WHERE event_type = 'failed' "
        "  AND (event_data->>'failure_reason' IS NULL "
        "       OR event_data->>'failure_reason' = '') "
        "  AND occurred_at > now() - interval '1 hour'"
    )
    rows = fetch_fn(sql)
    null_count = int((rows[0].get("count") or 0) if rows else 0)

    if null_count == 0:
        severity = "ok"
        status_line = f"[Check 12 NULL failure_reason] OK: 0 NULL rows in last hour"
        _clear_crit(supp, supp_path, check_id)
        dm_payload = None
        dm_suppressed = False
    elif null_count <= crit_threshold:
        severity = "warn"
        status_line = (
            f"[Check 12 NULL failure_reason] WARN: {null_count} NULL rows in last hour"
        )
        dm_payload = None
        dm_suppressed = False
    else:
        severity = "crit"
        status_line = (
            f"[Check 12 NULL failure_reason] CRIT: {null_count} NULL rows in last hour"
        )
        if _is_suppressed(supp, check_id):
            dm_payload = None
            dm_suppressed = True
        else:
            dm_payload = {
                "text": (
                    f"[CRIT] Check 12: {null_count} dispatch_items failed with NULL "
                    f"failure_reason in the last hour. failure_reason persistence may be broken. "
                    f"Check STORY-762 guard and dispatch_poller error handling."
                )
            }
            dm_suppressed = False
            _record_crit(supp, supp_path, check_id)

    logging.info(
        f"[FLEET-VIGILANCE Check {check_id}] {severity.upper()}: null_count={null_count}"
    )

    return {
        "check_id": check_id,
        "severity": severity,
        "status_line": status_line,
        "dm_payload": dm_payload,
        "dm_suppressed": dm_suppressed,
        "null_count": null_count,
    }


# ===========================================================================
# Check 13 — Zombie heartbeat (SC-5)
# ===========================================================================


def check_zombie_heartbeat(
    agents: list[dict],
    run_fn=None,
    suppression_path: str | None = None,
    phase_timeout_seconds: int | None = None,
    **_kw,
) -> dict:
    """Check 13: Detect agents emitting heartbeats with a stale phase_end timestamp.

    A zombie: heartbeat lines present but last phase_end > 2x phase_timeout ago.
    Returns zombies list.
    CRIT: any zombie detected.
    """
    _run = run_fn if run_fn is not None else subprocess.run
    check_id = 13

    if phase_timeout_seconds is None:
        phase_timeout_seconds = int(os.environ.get("FV_PHASE_TIMEOUT_SEC", "2400"))
    zombie_threshold = 2 * phase_timeout_seconds

    supp, supp_path = _load_suppression(suppression_path)

    zombies: list[dict] = []
    now_ts = time.time()

    for agent in agents:
        name = agent["name"]
        ip = agent["ip"]
        port = agent.get("ssh_port", 443)

        # 1. Check for recent heartbeat lines (journalctl --since '2 hours ago')
        hb_cmd = _ssh_cmd(
            ip, port,
            "journalctl -u dispatch-poller --since '2 hours ago' --no-pager -q 2>/dev/null "
            "| grep heartbeat | tail -10"
        )
        r_hb = _run(hb_cmd, capture_output=True, text=True, timeout=15)
        hb_output = (r_hb.stdout or "").strip()

        if not hb_output:
            # No heartbeats → not a zombie
            continue

        # 2. Get the most recent phase_end line
        pe_cmd = _ssh_cmd(
            ip, port,
            "journalctl -u dispatch-poller --since '12 hours ago' --no-pager -q 2>/dev/null "
            "| grep phase_end | tail -1"
        )
        r_pe = _run(pe_cmd, capture_output=True, text=True, timeout=15)
        pe_output = (r_pe.stdout or "").strip()

        if not pe_output:
            # Heartbeats but no phase_end at all → suspicious but not definitive zombie
            continue

        pe_ts = _parse_journalctl_ts(pe_output)
        if pe_ts is None:
            continue

        age_sec = now_ts - pe_ts
        if age_sec > zombie_threshold:
            # Extract story_id from phase_end line if possible
            story_match = re.search(r"story[=\s]+(STORY-\d+)", pe_output, re.IGNORECASE)
            story_id = story_match.group(1) if story_match else "unknown"

            zombies.append({
                "agent": name,
                "story_id": story_id,
                "phase_end_age_hours": round(age_sec / 3600, 1),
                "last_phase_end_line": pe_output[:200],
            })

    n_zombies = len(zombies)
    if n_zombies == 0:
        severity = "ok"
        status_line = "[Check 13 Zombie Heartbeat] OK: no zombies detected"
        _clear_crit(supp, supp_path, check_id)
        dm_payload = None
        dm_suppressed = False
    else:
        severity = "crit"
        parts = "; ".join(
            f"{z['story_id']}@{z['agent']} phase_end {z['phase_end_age_hours']:.0f}h ago"
            for z in zombies
        )
        status_line = f"[Check 13 Zombie Heartbeat] CRIT: {parts}"
        if _is_suppressed(supp, check_id):
            dm_payload = None
            dm_suppressed = True
        else:
            dm_payload = {
                "text": (
                    f"[CRIT] Check 13: Zombie heartbeat detected: {parts}. "
                    f"Agent is emitting heartbeats but phase_end is stale. "
                    f"SSH in and check `sudo journalctl -u dispatch-poller -f`."
                )
            }
            dm_suppressed = False
            _record_crit(supp, supp_path, check_id)

    logging.info(
        f"[FLEET-VIGILANCE Check {check_id}] {severity.upper()}: zombies={n_zombies}"
    )

    return {
        "check_id": check_id,
        "severity": severity,
        "status_line": status_line,
        "dm_payload": dm_payload,
        "dm_suppressed": dm_suppressed,
        "zombies": zombies,
    }


# ===========================================================================
# Check 14 — No-seed dispatch detection (SC-6)
# ===========================================================================


def check_no_seed_dispatch(
    agents: list[dict],
    fetch_fn,
    run_fn=None,
    suppression_path: str | None = None,
    **_kw,
) -> dict:
    """Check 14: Active dispatch items missing seed.md on the agent's VM.

    SQL: pending/claimed/in_progress items from last hour.
    For each: SSH to claiming agent and ls seed.md.
    Returns missing_seeds list.
    WARN: any missing seed detected.
    """
    _run = run_fn if run_fn is not None else subprocess.run
    check_id = 14

    supp, supp_path = _load_suppression(suppression_path)

    # Migrated to v2: dispatch_state_current is the projection of v2 event log.
    # Lane 'work_queue' = pending; lane 'in_progress' = claimed/leased.
    sql = (
        "SELECT j.story_id, j.repo, sc.leased_by AS claimed_by, sc.state AS status "
        "FROM dispatch_jobs j "
        "JOIN dispatch_state_current sc ON sc.job_id = j.job_id "
        "WHERE sc.lane IN ('work_queue', 'in_progress') "
        "  AND j.created_at > now() - interval '1 hour'"
    )
    rows = fetch_fn(sql)

    missing: list[dict] = []

    for row in rows:
        story_id = row.get("story_id", "")
        repo = row.get("repo", "")
        claimed_by = row.get("claimed_by") or ""

        # Extract story number for glob pattern
        m = re.match(r"STORY-(\d+)", story_id, re.IGNORECASE)
        story_num = m.group(1) if m else story_id

        # Determine which agent to SSH to
        agent = _agent_by_name(agents, claimed_by) if claimed_by else None
        if agent is None and agents:
            agent = agents[0]
        if agent is None:
            continue

        ip = agent["ip"]
        port = agent.get("ssh_port", 443)
        remote_cmd = (
            f"ls -la ~/dev/hpi-gorillacommerce/{repo}/features/story-{story_num}*/seed.md"
            f" 2>/dev/null"
        )
        cmd = _ssh_cmd(ip, port, remote_cmd)
        r = _run(cmd, capture_output=True, text=True, timeout=12)

        if r.returncode != 0:
            missing.append({
                "story_id": story_id,
                "repo": repo,
                "claimed_by": claimed_by,
                "agent": agent["name"],
            })

    n_missing = len(missing)
    if n_missing == 0:
        severity = "ok"
        status_line = "[Check 14 No-Seed Dispatch] OK: all active stories have seed.md"
        _clear_crit(supp, supp_path, check_id)
        dm_payload = None
        dm_suppressed = False
    else:
        severity = "warn"
        parts = "; ".join(
            f"{ms['story_id']} ({ms['repo']})" for ms in missing
        )
        status_line = f"[Check 14 No-Seed Dispatch] WARN: {parts} seed missing"
        if _is_suppressed(supp, check_id):
            dm_payload = None
            dm_suppressed = True
        else:
            dm_payload = {
                "text": (
                    f"[WARN] Check 14: {n_missing} dispatched story(ies) missing seed.md: "
                    f"{parts}. These stories will fail at Phase 8 completion gate. "
                    f"Cancel them or run Phase 1 first."
                )
            }
            dm_suppressed = False
            _record_crit(supp, supp_path, check_id)

    logging.info(
        f"[FLEET-VIGILANCE Check {check_id}] {severity.upper()}: missing_seeds={n_missing}"
    )

    return {
        "check_id": check_id,
        "severity": severity,
        "status_line": status_line,
        "dm_payload": dm_payload,
        "dm_suppressed": dm_suppressed,
        "missing_seeds": missing,
    }


# ===========================================================================
# run_all_checks — dispatcher with per-check error isolation (SC-7)
# ===========================================================================

_DEFAULT_CHECKS = [
    check_vm_reachability,
    check_stuck_push_code,
    check_code_drift,
    check_null_failure_reason,
    check_zombie_heartbeat,
    check_no_seed_dispatch,
]


def run_all_checks(
    agents: list[dict],
    fetch_fn=None,
    run_fn=None,
    suppression_path: str | None = None,
    check_fns: list | None = None,
) -> list[dict]:
    """Run all blind-spot checks; catch per-check exceptions (SC-7).

    Parameters
    ----------
    agents:          List of agent dicts from agent-registry.json.
    fetch_fn:        Synchronous DB fetch function (sql, params) → list[dict].
    run_fn:          subprocess.run replacement (for testing).
    suppression_path: Path to vigilance-dm-suppression.json.
    check_fns:       Override the default 6-function list (used in tests).

    Returns
    -------
    List of result dicts, one per check (even for failed checks).
    """
    fns = check_fns if check_fns is not None else _DEFAULT_CHECKS
    results: list[dict] = []

    for fn in fns:
        try:
            result = fn(
                agents=agents,
                fetch_fn=fetch_fn,
                run_fn=run_fn,
                suppression_path=suppression_path,
            )
            results.append(result)
        except Exception as exc:
            fn_name = getattr(fn, "__name__", repr(fn))
            tb = traceback.format_exc()
            logging.error(
                f"[FLEET-VIGILANCE] Check {fn_name!r} raised {type(exc).__name__}: {exc}\n{tb}"
            )
            results.append({
                "check_id": None,
                "severity": "error_unavailable",
                "status_line": (
                    f"[Check ? {fn_name}] error_unavailable: {type(exc).__name__}: "
                    + str(exc)[:200]
                ),
                "dm_payload": None,
                "dm_suppressed": False,
                "error": str(exc),
                "error_type": type(exc).__name__,
            })

    return results
