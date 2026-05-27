"""
tests/morris/test_fleet_vigilance_blind_spots.py — STORY-767 Phase 7 (RED state).

Tests for deployment/morris/scripts/blind_spot_checks.py.
All external I/O (SSH, DB) is replaced by dependency injection / unittest.mock.

RED state: all 15 tests FAIL because blind_spot_checks.py does not exist yet.
  _load_bsc() raises ImportError → pytest.fail() → FAILED (not ERROR/SKIP).

Numbering note: STORY-766 had only seed.md when this phase ran (not shipped).
  Per escalation contract, this story claims Checks 9–14.
  The seed.md referred to them as Checks 10–15; all IDs here use 9–14.

Run:
  pytest tests/morris/test_fleet_vigilance_blind_spots.py -v
  pytest tests/ -x --ignore=tests/e2e -q   # full regression
"""
from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Module loader — keeps collection clean; FAIL (not ERROR) when missing
# ---------------------------------------------------------------------------
_BSC_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "deployment"
    / "morris"
    / "scripts"
    / "blind_spot_checks.py"
)


def _load_bsc():
    """Load the blind_spot_checks module.

    When called inside a test body, a missing file raises ImportError which
    is converted to pytest.fail() → test status = FAILED (not ERROR).
    """
    if not _BSC_PATH.exists():
        pytest.fail(
            f"blind_spot_checks.py not found at {_BSC_PATH}.\n"
            "Implement this module in Phase 8 to make these tests GREEN."
        )
    spec = importlib.util.spec_from_file_location("blind_spot_checks", _BSC_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Canonical agent list — mirrors deployment/vm/agent-registry.json
# ---------------------------------------------------------------------------
_AGENTS = [
    {"name": "dan",     "ip": "20.228.224.243", "ssh_port": 443},
    {"name": "derrick", "ip": "20.121.210.186",  "ssh_port": 443},
    {"name": "daisy",   "ip": "20.98.231.234",   "ssh_port": 443},
    {"name": "devon",   "ip": "20.186.26.130",   "ssh_port": 443},
]


# ===========================================================================
# Group A — Check 9: VM Reachability (SC-1)
# ===========================================================================


def test_vm_reachability_check():
    """A-1 (primary): Unreachable VMs → severity crit/warn, unreachable list populated.

    Maps to SC-1 / Check 9.
    Dan responds with PONG; derrick/daisy/devon time out (rc=255).
    """
    bsc = _load_bsc()

    def _ssh(cmd, **kw):
        r = MagicMock()
        r.stderr = ""
        cmd_str = " ".join(str(c) for c in cmd) if isinstance(cmd, list) else str(cmd)
        if "20.228.224.243" in cmd_str:  # dan — reachable
            r.returncode = 0
            r.stdout = "PONG\n"
        else:  # derrick, daisy, devon — unreachable
            r.returncode = 255
            r.stdout = ""
        return r

    with patch("subprocess.run", side_effect=_ssh):
        result = bsc.check_vm_reachability(_AGENTS)

    assert result["severity"] in ("warn", "crit"), (
        f"Expected warn or crit for unreachable VMs, got {result['severity']!r}"
    )
    assert "dan" in result["reachable"], "dan should be in reachable list"
    unreachable = set(result["unreachable"])
    assert {"derrick", "daisy", "devon"} <= unreachable, (
        f"Expected derrick/daisy/devon unreachable, got {unreachable!r}"
    )
    assert result["check_id"] == 9, f"Expected check_id=9, got {result['check_id']}"
    assert "[Check 9" in result["status_line"], (
        f"status_line must contain '[Check 9', got: {result['status_line']!r}"
    )


def test_vm_reachability_all_reachable():
    """A-2: All VMs respond with PONG → severity ok, no DM payload."""
    bsc = _load_bsc()

    pong = MagicMock(returncode=0, stdout="PONG\n", stderr="")
    with patch("subprocess.run", return_value=pong):
        result = bsc.check_vm_reachability(_AGENTS)

    assert result["severity"] == "ok"
    assert result["unreachable"] == []
    assert result.get("dm_payload") is None


# ===========================================================================
# Group B — Check 10: Stuck push-code.sh (SC-2)
# ===========================================================================


def test_stuck_push_code_detection():
    """B-1 (primary): push-code.sh older than 15 min → severity crit, DM with PID.

    Maps to SC-2 / Check 10.
    pgrep returns PID 12345; process elapsed time is 20 min → over threshold.
    """
    bsc = _load_bsc()

    def _run(cmd, **kw):
        r = MagicMock()
        r.returncode = 0
        r.stderr = ""
        cmd_str = " ".join(str(c) for c in cmd) if isinstance(cmd, list) else str(cmd)
        if "pgrep" in cmd_str:
            r.stdout = "12345\n"
        elif "ps" in cmd_str or "etime" in cmd_str:
            r.stdout = "20:00\n"  # 20-minute elapsed time
        else:
            r.stdout = "push-code.sh dan\n"  # cmdline / proc-tree
        return r

    with patch("subprocess.run", side_effect=_run):
        result = bsc.check_stuck_push_code()

    assert result["severity"] == "crit", (
        f"Expected crit for 20-min stuck process, got {result['severity']!r}"
    )
    assert len(result["stuck_pids"]) >= 1, "stuck_pids must be non-empty"
    assert result["check_id"] == 10, f"Expected check_id=10, got {result['check_id']}"
    assert "[Check 10" in result["status_line"]
    assert result["dm_payload"] is not None, "dm_payload must be set for crit"
    dm_str = json.dumps(result["dm_payload"])
    assert any(tok in dm_str for tok in ("12345", "push-code", "PID", "pid")), (
        f"DM payload should reference PID/push-code, got: {dm_str!r}"
    )


def test_stuck_push_code_no_processes():
    """B-2: No push-code.sh running → severity ok, empty stuck_pids."""
    bsc = _load_bsc()

    no_match = MagicMock(returncode=1, stdout="", stderr="")  # pgrep: no results
    with patch("subprocess.run", return_value=no_match):
        result = bsc.check_stuck_push_code()

    assert result["severity"] == "ok"
    assert result["stuck_pids"] == []
    assert result.get("dm_payload") is None


# ===========================================================================
# Group C — Check 11: Code-version drift (SC-3)
# ===========================================================================


def test_code_drift_detection():
    """C-1 (primary): Agent file mtime 7h behind latest hermes/* merge → drift crit/warn.

    Maps to SC-3 / Check 11.
    Merge commit: 2026-04-29 18:42:00 UTC (epoch 1745955720)
    Agent mtime:  2026-04-29 11:36:00 UTC (epoch 1745930160) — 7h behind
    """
    bsc = _load_bsc()

    MERGE_TS = "2026-04-29 18:42:00 +0000"
    AGENT_MTIME_EPOCH = "1777462920"  # 7 hours before merge (2026-04-29 11:42:00 UTC)

    def _run(cmd, **kw):
        r = MagicMock()
        r.returncode = 0
        r.stderr = ""
        cmd_str = " ".join(str(c) for c in cmd) if isinstance(cmd, list) else str(cmd)
        if "git log" in cmd_str:
            r.stdout = MERGE_TS + "\n"
        else:
            r.stdout = AGENT_MTIME_EPOCH + "\n"
        return r

    with patch("subprocess.run", side_effect=_run):
        result = bsc.check_code_drift(_AGENTS)

    assert result["severity"] in ("warn", "crit"), (
        f"Expected warn/crit for drifted agents, got {result['severity']!r}"
    )
    assert len(result["drifted_agents"]) > 0, "drifted_agents must be non-empty"
    assert result["check_id"] == 11
    assert "[Check 11" in result["status_line"]


def test_code_drift_ok():
    """C-2: All agents have mtime within 1h of latest merge → severity ok."""
    bsc = _load_bsc()

    MERGE_TS = "2026-04-29 18:42:00 +0000"
    FRESH_MTIME = "1777490520"  # 2026-04-29 19:22:00 UTC — 40 min after merge

    def _run(cmd, **kw):
        r = MagicMock()
        r.returncode = 0
        r.stderr = ""
        cmd_str = " ".join(str(c) for c in cmd) if isinstance(cmd, list) else str(cmd)
        if "git log" in cmd_str:
            r.stdout = MERGE_TS + "\n"
        else:
            r.stdout = FRESH_MTIME + "\n"
        return r

    with patch("subprocess.run", side_effect=_run):
        result = bsc.check_code_drift(_AGENTS)

    assert result["severity"] == "ok"
    assert result["drifted_agents"] == []


# ===========================================================================
# Group D — Check 12: NULL failure_reason spike (SC-4)
# ===========================================================================


def test_null_failure_reason_spike():
    """D-1 (primary): > 5 NULL failure_reason rows in last hour → severity crit.

    Maps to SC-4 / Check 12.
    """
    bsc = _load_bsc()

    mock_fetch = MagicMock(return_value=[{"count": 7}])
    result = bsc.check_null_failure_reason(fetch_fn=mock_fetch)

    assert result["severity"] == "crit", (
        f"Expected crit for 7 NULL rows, got {result['severity']!r}"
    )
    assert result["null_count"] == 7
    assert result["check_id"] == 12
    assert "[Check 12" in result["status_line"]


def test_null_failure_reason_warn():
    """D-2: 1–5 NULL rows → severity warn."""
    bsc = _load_bsc()

    mock_fetch = MagicMock(return_value=[{"count": 3}])
    result = bsc.check_null_failure_reason(fetch_fn=mock_fetch)

    assert result["severity"] == "warn"
    assert result["null_count"] == 3


def test_null_failure_reason_ok():
    """D-3: 0 NULL rows → severity ok, no DM."""
    bsc = _load_bsc()

    mock_fetch = MagicMock(return_value=[{"count": 0}])
    result = bsc.check_null_failure_reason(fetch_fn=mock_fetch)

    assert result["severity"] == "ok"
    assert result.get("dm_payload") is None


# ===========================================================================
# Group E — Check 13: Zombie heartbeat (SC-5)
# ===========================================================================


def test_zombie_heartbeat_detection():
    """E-1 (primary): Heartbeats present but phase_end 6h ago → severity crit, DM.

    Maps to SC-5 / Check 13.
    Default phase_timeout = 2400s → zombie threshold = 4800s (~80min).
    Heartbeat 20min ago, phase_end 6h ago → zombie.
    """
    bsc = _load_bsc()

    _HB_LINE = (
        "Apr 29 18:00:00 dispatch-poller[1]: heartbeat story=STORY-010 agent=dan\n"
    )
    _PE_LINE = (
        "Apr 29 12:00:00 dispatch-poller[1]: phase_end story=STORY-010 phase=8\n"
    )

    def _run(cmd, **kw):
        r = MagicMock()
        r.returncode = 0
        r.stderr = ""
        cmd_str = " ".join(str(c) for c in cmd) if isinstance(cmd, list) else str(cmd)
        if "heartbeat" in cmd_str:
            r.stdout = _HB_LINE
        elif "phase_end" in cmd_str:
            r.stdout = _PE_LINE
        else:
            r.stdout = ""
        return r

    with patch("subprocess.run", side_effect=_run):
        result = bsc.check_zombie_heartbeat(_AGENTS)

    assert result["severity"] == "crit", (
        f"Expected crit for zombie heartbeat, got {result['severity']!r}"
    )
    assert len(result["zombies"]) > 0, "zombies list must be non-empty"
    zombie = result["zombies"][0]
    assert "story_id" in zombie or "agent" in zombie, (
        f"Zombie entry must have story_id or agent field: {zombie!r}"
    )
    assert result["check_id"] == 13
    assert "[Check 13" in result["status_line"]
    assert result["dm_payload"] is not None, "dm_payload must be set for zombie crit"


def test_zombie_heartbeat_no_heartbeats():
    """E-2: No heartbeat lines from any agent → no zombies, ok."""
    bsc = _load_bsc()

    empty = MagicMock(returncode=0, stdout="", stderr="")
    with patch("subprocess.run", return_value=empty):
        result = bsc.check_zombie_heartbeat(_AGENTS)

    assert result["severity"] == "ok"
    assert result["zombies"] == []


# ===========================================================================
# Group F — Check 14: No-seed dispatch (SC-6)
# ===========================================================================


def test_no_seed_dispatch_detection():
    """F-1 (primary): Active dispatch row with no seed.md → warn/crit + DM.

    Maps to SC-6 / Check 14.
    DB returns STORY-784 in 'claimed' status; SSH ls for seed.md returns rc=1.
    """
    bsc = _load_bsc()

    pending_rows = [
        {
            "story_id": "STORY-784",
            "repo": "advertising-amazon",
            "claimed_by": "dan",
            "status": "claimed",
        }
    ]
    mock_fetch = MagicMock(return_value=pending_rows)

    no_seed = MagicMock(returncode=1, stdout="", stderr="No such file or directory")
    with patch("subprocess.run", return_value=no_seed):
        result = bsc.check_no_seed_dispatch(agents=_AGENTS, fetch_fn=mock_fetch)

    assert result["severity"] in ("warn", "crit"), (
        f"Expected warn/crit for missing seed, got {result['severity']!r}"
    )
    assert len(result["missing_seeds"]) >= 1, "missing_seeds must be non-empty"
    ms = result["missing_seeds"][0]
    assert ms["story_id"] == "STORY-784", (
        f"Expected STORY-784 in missing_seeds, got {ms!r}"
    )
    assert result["check_id"] == 14
    assert "[Check 14" in result["status_line"]
    assert result["dm_payload"] is not None, "dm_payload must be set for missing seed"


def test_no_seed_dispatch_all_have_seeds():
    """F-2: All dispatched stories have seed.md → severity ok."""
    bsc = _load_bsc()

    pending_rows = [
        {
            "story_id": "STORY-800",
            "repo": "tech-dev-agents",
            "claimed_by": "derrick",
            "status": "claimed",
        }
    ]
    mock_fetch = MagicMock(return_value=pending_rows)

    has_seed = MagicMock(returncode=0, stdout="seed.md\n", stderr="")
    with patch("subprocess.run", return_value=has_seed):
        result = bsc.check_no_seed_dispatch(agents=_AGENTS, fetch_fn=mock_fetch)

    assert result["severity"] == "ok"
    assert result["missing_seeds"] == []


# ===========================================================================
# Group G — SC-7: Check failures don't abort run
# ===========================================================================


def test_check_failures_dont_abort_run():
    """G-1 (primary): Exception in one check → error_unavailable, remaining checks run.

    SC-7: a check that raises must not abort the full vigilance run.
    run_all_checks() accepts check_fns= for injection; _bad_check raises,
    _ok_check succeeds. Both should appear in results.
    """
    bsc = _load_bsc()

    def _bad_check(agents=None, fetch_fn=None, run_fn=None, suppression_path=None, **kw):
        raise RuntimeError("Simulated SSH connection timed out")

    def _ok_check(agents=None, fetch_fn=None, run_fn=None, suppression_path=None, **kw):
        return {
            "check_id": 99,
            "severity": "ok",
            "status_line": "[Check 99 Test] OK: synthetic",
            "dm_payload": None,
            "dm_suppressed": False,
        }

    results = bsc.run_all_checks(
        agents=_AGENTS,
        fetch_fn=MagicMock(return_value=[{"count": 0}]),
        check_fns=[_bad_check, _ok_check],
    )

    assert isinstance(results, list), "run_all_checks must return a list"
    assert len(results) >= 2, "Expected results for both injected check functions"

    errored = [r for r in results if r.get("severity") == "error_unavailable"]
    assert len(errored) >= 1, (
        "Raising check should produce an error_unavailable entry, "
        f"got severities: {[r.get('severity') for r in results]}"
    )

    ok_results = [r for r in results if r.get("severity") == "ok"]
    assert len(ok_results) >= 1, (
        "Healthy check should still produce an ok result after sibling failure"
    )


# ===========================================================================
# Group H — AC-7: DM throttling per check
# ===========================================================================


def test_dm_throttling_per_check(tmp_path):
    """H-1 (primary): Check 9 CRIT within 4-hour suppression window → DM suppressed.

    AC-7: if a check fired CRIT < 4h ago, suppress DM (dm_payload=None or
    dm_suppressed=True). Suppression state stored in vigilance-dm-suppression.json.
    """
    bsc = _load_bsc()

    suppression_file = tmp_path / "vigilance-dm-suppression.json"
    # Check 9 last fired 1 hour ago — well within 4-hour window
    suppression_file.write_text(json.dumps({"9": time.time() - 3600}))

    # All VMs unreachable → would normally fire CRIT DM
    fail_ssh = MagicMock(returncode=255, stdout="", stderr="Connection refused")
    with patch("subprocess.run", return_value=fail_ssh):
        result = bsc.check_vm_reachability(
            _AGENTS,
            suppression_path=str(suppression_file),
        )

    assert result["severity"] in ("warn", "crit"), (
        "CRIT should still be detected even when DM is suppressed"
    )
    dm_suppressed = (result.get("dm_payload") is None) or (
        result.get("dm_suppressed") is True
    )
    assert dm_suppressed, (
        "DM should be suppressed when check fired within 4-hour window. "
        f"Got dm_payload={result.get('dm_payload')!r}, "
        f"dm_suppressed={result.get('dm_suppressed')!r}"
    )
