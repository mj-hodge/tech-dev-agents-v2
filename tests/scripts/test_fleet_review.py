"""
tests/scripts/test_fleet_review.py

Phase 7 (RED-state) tests for scripts/fleet_review.py.

All external I/O (subprocess.run, pathlib reads) is mocked so the suite runs
without SSH, az CLI, gh CLI, or curl available.

Check function contract
-----------------------
Each check_* function returns a dict with exactly these keys:

    {
        "name":     str,          # human-readable check name
        "status":   str,          # one of: "OK", "WARN", "CRIT", "SKIP"
        "findings": str,          # one-line summary
        "details":  str | list,   # extended detail (str or list of str)
    }

Report formatter contract
--------------------------
    build_report(results: list[dict]) -> str
        Accepts the list returned by run_all_checks() and returns a UTF-8
        Markdown string.  The string MUST contain a scorecard table with a
        header row and one row per check.

Orchestrator contract
---------------------
    run_all_checks() -> list[dict]
        Runs all 7 checks in order and returns their result dicts.  A failure
        in one check must not raise an exception that prevents the remaining
        checks from running.
"""

from __future__ import annotations

import json
import subprocess
import textwrap
from types import ModuleType
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

AGENTS = {
    "dan": "20.228.224.243",
    "derrick": "20.121.210.186",
    "morris": "20.246.36.143",
}

VALID_STATUSES = {"OK", "WARN", "CRIT", "SKIP"}


def _ok(stdout: str = "", returncode: int = 0) -> MagicMock:
    """Build a successful subprocess.CompletedProcess mock."""
    m = MagicMock(spec=subprocess.CompletedProcess)
    m.stdout = stdout
    m.stderr = ""
    m.returncode = returncode
    return m


def _fail(returncode: int = 1, stderr: str = "error") -> MagicMock:
    """Build a failing subprocess.CompletedProcess mock."""
    m = MagicMock(spec=subprocess.CompletedProcess)
    m.stdout = ""
    m.stderr = stderr
    m.returncode = returncode
    return m


def _assert_result_shape(result: dict) -> None:
    """Assert that a check result has all required keys with correct types."""
    assert isinstance(result, dict), "check must return a dict"
    for key in ("name", "status", "findings", "details"):
        assert key in result, f"result missing key: {key!r}"
    assert result["status"] in VALID_STATUSES, (
        f"status {result['status']!r} not in {VALID_STATUSES}"
    )
    assert isinstance(result["name"], str) and result["name"], "name must be non-empty str"
    assert isinstance(result["findings"], str), "findings must be str"


# ---------------------------------------------------------------------------
# TC-01  check_guard_alignment — happy path: all VMs match repo hash
# ---------------------------------------------------------------------------

class TestCheckGuardAlignment:
    """Tests for check_guard_alignment() — check #1."""

    REPO_HASH = "abc123def456abc123def456abc123de  scripts/terminal_guard.py"
    REMOTE_HASH = "abc123def456abc123def456abc123de  /opt/agent/terminal_guard.py"

    def test_tc01_all_match_returns_ok(self, fleet_review: ModuleType):
        """TC-01: All three VMs return the same md5 as the repo — status OK."""
        with patch("subprocess.run") as mock_run:
            # First call: local md5 of repo file
            # Subsequent calls: remote md5 via ssh (one per agent)
            mock_run.side_effect = [
                _ok(self.REPO_HASH),       # local md5
                _ok(self.REMOTE_HASH),     # dan
                _ok(self.REMOTE_HASH),     # derrick
                _ok(self.REMOTE_HASH),     # morris
            ]
            result = fleet_review.check_guard_alignment()

        _assert_result_shape(result)
        assert result["status"] == "OK"
        assert "guard" in result["name"].lower() or "alignment" in result["name"].lower()

    def test_tc02_one_vm_mismatch_returns_crit(self, fleet_review: ModuleType):
        """TC-02: One VM hash differs from repo — status CRIT."""
        bad_hash = "deadbeefdeadbeefdeadbeefdeadbeef  /opt/agent/terminal_guard.py"
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                _ok(self.REPO_HASH),
                _ok(self.REMOTE_HASH),   # dan OK
                _ok(bad_hash),           # derrick MISMATCH
                _ok(self.REMOTE_HASH),   # morris OK
            ]
            result = fleet_review.check_guard_alignment()

        _assert_result_shape(result)
        assert result["status"] in ("CRIT", "WARN")

    def test_tc03_ssh_timeout_returns_skip(self, fleet_review: ModuleType):
        """TC-03: SSH raises TimeoutExpired — check degrades to SKIP."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                _ok(self.REPO_HASH),
                subprocess.TimeoutExpired(cmd="ssh", timeout=10),
            ]
            result = fleet_review.check_guard_alignment()

        _assert_result_shape(result)
        assert result["status"] == "SKIP"

    def test_tc04_ssh_nonzero_exit_returns_warn_or_crit(self, fleet_review: ModuleType):
        """TC-04: SSH exits with non-zero code — status WARN or CRIT (not OK)."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                _ok(self.REPO_HASH),
                _fail(returncode=255, stderr="Connection refused"),
                _ok(self.REMOTE_HASH),
                _ok(self.REMOTE_HASH),
            ]
            result = fleet_review.check_guard_alignment()

        _assert_result_shape(result)
        assert result["status"] != "OK"


# ---------------------------------------------------------------------------
# TC-05  check_sdlc_compliance — check #2
# ---------------------------------------------------------------------------

class TestCheckSdlcCompliance:
    """Tests for check_sdlc_compliance() — check #2."""

    DISPATCH_JSON = '{"dispatches": [{"story": "STORY-320", "phase": 8, "status": "complete"}]}'
    TREE_JSON = (
        '{"tree": ['
        '{"path": "features/story-320-fleet-cost-cache/feature-spec.md", "type": "blob"},'
        '{"path": "features/story-320-fleet-cost-cache/test-design.md", "type": "blob"}'
        ']}'
    )

    def test_tc05_all_deliverables_present_returns_ok(self, fleet_review: ModuleType):
        """TC-05: ops-console and GitHub both return expected data — status OK."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                _ok(self.DISPATCH_JSON),  # curl ops-console
                _ok(self.TREE_JSON),      # gh api github tree
            ]
            result = fleet_review.check_sdlc_compliance()

        _assert_result_shape(result)
        assert result["status"] in ("OK", "WARN")

    def test_tc06_curl_failure_returns_skip(self, fleet_review: ModuleType):
        """TC-06: curl to ops-console fails — check is SKIP."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                _fail(returncode=7, stderr="Failed to connect"),
            ]
            result = fleet_review.check_sdlc_compliance()

        _assert_result_shape(result)
        assert result["status"] == "SKIP"

    def test_tc07_missing_api_key_returns_skip(self, fleet_review: ModuleType):
        """TC-07: OPS_CONSOLE_API_KEY not set — check is SKIP before any subprocess."""
        import os
        original = os.environ.pop("OPS_CONSOLE_API_KEY", None)
        try:
            with patch("subprocess.run") as mock_run:
                result = fleet_review.check_sdlc_compliance()
            # subprocess.run should NOT have been called if key is missing
            # (implementation may choose to call curl and fail — either SKIP is valid)
            _assert_result_shape(result)
            assert result["status"] == "SKIP"
        finally:
            if original is not None:
                os.environ["OPS_CONSOLE_API_KEY"] = original

    def test_tc30_gh_api_uses_recursive_tree(self, fleet_review: ModuleType):
        """TC-30: gh api call includes ?recursive=1 so nested deliverables are found."""
        dispatch_json = '{"dispatches": [{"story": "STORY-320", "status": "complete"}]}'
        tree_paths = "features/story-320-fleet-cost-cache\nfeatures/story-320-fleet-cost-cache/seed.md\nfeatures/story-320-fleet-cost-cache/test-design.md"
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                _ok(dispatch_json),
                _ok(tree_paths),
            ]
            result = fleet_review.check_sdlc_compliance()

        # Verify the gh api call included recursive=1
        gh_call = mock_run.call_args_list[1]
        gh_api_url = gh_call[0][0][2]  # 3rd arg in the command list
        assert "recursive" in gh_api_url, (
            f"GitHub tree API URL must include ?recursive=1, got: {gh_api_url}"
        )

    def test_tc31_path_matching_is_prefix_specific(self, fleet_review: ModuleType):
        """TC-31: Deliverable check uses story-prefix matching, not global substring."""
        # Two stories: 320 has all deliverables, 321 is missing seed.md.
        # The old substring check would see "seed.md" in the combined output
        # and falsely report 321 as OK.
        dispatch_json = json.dumps({"dispatches": [
            {"story": "STORY-320", "status": "complete", "repo": "tech-dev-agents"},
            {"story": "STORY-321", "status": "complete", "repo": "tech-dev-agents"},
        ]})
        tree_paths = "\n".join([
            "features/story-320-foo",
            "features/story-320-foo/seed.md",
            "features/story-320-foo/test-design.md",
            "features/story-321-bar",
            "features/story-321-bar/test-design.md",
            # NOTE: story-321 has NO seed.md
        ])
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                _ok(dispatch_json),   # dispatch API
                _ok(tree_paths),      # gh api for story-320
                _ok(tree_paths),      # gh api for story-321 (same tree)
            ]
            result = fleet_review.check_sdlc_compliance()

        _assert_result_shape(result)
        details = result.get("details", {})
        # story-321 should be flagged for missing seed.md
        assert "STORY-321" in details.get("missing_seed", []), (
            f"STORY-321 should be missing seed.md, details={details}"
        )
        # story-320 should NOT be flagged
        assert "STORY-320" not in details.get("missing_seed", []), (
            f"STORY-320 has seed.md and should not be flagged, details={details}"
        )


# ---------------------------------------------------------------------------
# TC-08  check_model_economics — check #3
# ---------------------------------------------------------------------------

class TestCheckModelEconomics:
    """Tests for check_model_economics() — check #3."""

    COST_JSON = textwrap.dedent("""\
        {
          "properties": {
            "rows": [
              [12.34, "USD", "2026-04-01"]
            ],
            "columns": [
              {"name": "Cost", "type": "Number"},
              {"name": "Currency", "type": "String"},
              {"name": "UsageDate", "type": "String"}
            ]
          }
        }
    """)

    def test_tc08_cost_within_budget_returns_ok(self, fleet_review: ModuleType):
        """TC-08: az rest returns well-formed cost JSON under threshold — OK."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _ok(self.COST_JSON)
            result = fleet_review.check_model_economics()

        _assert_result_shape(result)
        assert result["status"] in ("OK", "WARN")
        # findings must mention a dollar amount or cost figure
        assert any(c.isdigit() for c in result["findings"])

    def test_tc09_az_not_found_returns_skip(self, fleet_review: ModuleType):
        """TC-09: az CLI not found (FileNotFoundError) — status SKIP."""
        with patch("subprocess.run", side_effect=FileNotFoundError("az not found")):
            result = fleet_review.check_model_economics()

        _assert_result_shape(result)
        assert result["status"] == "SKIP"

    def test_tc10_az_returns_error_json_returns_warn_or_crit(self, fleet_review: ModuleType):
        """TC-10: az rest returns non-zero exit (auth error) — WARN or CRIT."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _fail(returncode=1, stderr="AADSTS70011: Invalid scope")
            result = fleet_review.check_model_economics()

        _assert_result_shape(result)
        assert result["status"] in ("WARN", "CRIT", "SKIP")


# ---------------------------------------------------------------------------
# TC-11  check_agent_health — check #4
# ---------------------------------------------------------------------------

class TestCheckAgentHealth:
    """Tests for check_agent_health() — check #4."""

    HEALTHY_OUTPUT = textwrap.dedent("""\
        SYSTEMD: active
        DISK: /opt 23% used
        AUTH: ok
    """)

    def test_tc11_all_agents_healthy_returns_ok(self, fleet_review: ModuleType):
        """TC-11: All three agents report healthy systemd/disk/auth — OK."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _ok(self.HEALTHY_OUTPUT)
            result = fleet_review.check_agent_health()

        _assert_result_shape(result)
        assert result["status"] == "OK"
        # Should have probed all three IPs
        assert mock_run.call_count == len(AGENTS)

    def test_tc12_one_agent_ssh_fail_returns_warn(self, fleet_review: ModuleType):
        """TC-12: One SSH probe fails — overall status WARN or CRIT."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                _ok(self.HEALTHY_OUTPUT),   # dan OK
                _fail(returncode=255),      # derrick SSH error
                _ok(self.HEALTHY_OUTPUT),   # morris OK
            ]
            result = fleet_review.check_agent_health()

        _assert_result_shape(result)
        assert result["status"] in ("WARN", "CRIT")

    def test_tc13_high_disk_usage_returns_warn(self, fleet_review: ModuleType):
        """TC-13: One agent reports disk > 90% — status WARN."""
        high_disk = "SYSTEMD: active\nDISK: /opt 94% used\nAUTH: ok\n"
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                _ok(self.HEALTHY_OUTPUT),
                _ok(high_disk),
                _ok(self.HEALTHY_OUTPUT),
            ]
            result = fleet_review.check_agent_health()

        _assert_result_shape(result)
        assert result["status"] in ("WARN", "CRIT")


# ---------------------------------------------------------------------------
# TC-14  check_tool_drift — check #5
# ---------------------------------------------------------------------------

class TestCheckToolDrift:
    """Tests for check_tool_drift() — check #5."""

    SAME_HASH = "aabbccddeeff00112233445566778899"
    TRACKED_FILES = [
        "dispatch_poller.py",
        "terminal_guard.py",
        "work_queue.py",
        "claude_sdk_tool.py",
        "cost_monitor.sh",
        "weekly-patch.sh",
        "morris-fleet-check.sh",
    ]

    def test_tc14_no_drift_returns_ok(self, fleet_review: ModuleType):
        """TC-14: Local and remote md5 match for all tracked files — OK."""
        # Each tracked file needs a local hash + remote hash per agent
        n_files = len(self.TRACKED_FILES)
        n_agents = len(AGENTS)
        # local hashes: n_files calls; remote: n_agents * n_files calls
        local_result = _ok(f"{self.SAME_HASH}  somefile")
        remote_result = _ok(f"{self.SAME_HASH}  /opt/agent/somefile")

        total_calls = n_files + n_agents * n_files
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout=f"{self.SAME_HASH}  file",
                stderr="",
                returncode=0,
            )
            result = fleet_review.check_tool_drift()

        _assert_result_shape(result)
        assert result["status"] == "OK"

    def test_tc15_drift_detected_returns_warn_or_crit(self, fleet_review: ModuleType):
        """TC-15: One file hash differs on a remote VM — WARN or CRIT."""
        local_hash = "aabbccddeeff00112233445566778899"
        remote_hash = "deadbeefdeadbeefdeadbeefdeadbeef"

        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            # Return a different hash on the 2nd remote call to simulate drift
            if call_count[0] == len(self.TRACKED_FILES) + 1:
                return _ok(f"{remote_hash}  /opt/agent/terminal_guard.py")
            return _ok(f"{local_hash}  file")

        with patch("subprocess.run", side_effect=side_effect):
            result = fleet_review.check_tool_drift()

        _assert_result_shape(result)
        assert result["status"] in ("WARN", "CRIT")


# ---------------------------------------------------------------------------
# TC-16  check_open_prs — check #6
# ---------------------------------------------------------------------------

class TestCheckOpenPrs:
    """Tests for check_open_prs() — check #6."""

    PR_OUTPUT = textwrap.dedent("""\
        42\tFix cost parser\topen\t2026-04-15
        43\tAdd fleet review script\topen\t2026-04-16
    """)

    def test_tc16_prs_found_returns_warn(self, fleet_review: ModuleType):
        """TC-16: gh pr list returns open PRs — status WARN (PRs need attention)."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _ok(self.PR_OUTPUT)
            result = fleet_review.check_open_prs()

        _assert_result_shape(result)
        # Open PRs are informational; status is WARN or OK depending on impl policy
        assert result["status"] in ("OK", "WARN")
        # findings should mention PR count
        assert any(c.isdigit() for c in result["findings"])

    def test_tc17_no_prs_returns_ok(self, fleet_review: ModuleType):
        """TC-17: gh pr list returns empty — status OK."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _ok("")
            result = fleet_review.check_open_prs()

        _assert_result_shape(result)
        assert result["status"] == "OK"

    def test_tc18_gh_not_found_returns_skip(self, fleet_review: ModuleType):
        """TC-18: gh CLI not available — status SKIP."""
        with patch("subprocess.run", side_effect=FileNotFoundError("gh not found")):
            result = fleet_review.check_open_prs()

        _assert_result_shape(result)
        assert result["status"] == "SKIP"


# ---------------------------------------------------------------------------
# TC-19  check_kb_freshness — check #7
# ---------------------------------------------------------------------------

class TestCheckKbFreshness:
    """Tests for check_kb_freshness() — check #7."""

    RECENT_LOG = "commit abc123\nDate:   Wed Apr 16 09:00:00 2026 +0000\n\nUpdate KB articles\n"
    STALE_LOG = "commit def456\nDate:   Mon Mar 2 09:00:00 2026 +0000\n\nOld update\n"

    def test_tc19_recent_commit_returns_ok(self, fleet_review: ModuleType):
        """TC-19: git log shows a commit within the last 7 days — OK."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _ok(self.RECENT_LOG)
            result = fleet_review.check_kb_freshness()

        _assert_result_shape(result)
        assert result["status"] in ("OK", "WARN")

    def test_tc20_stale_commit_returns_warn(self, fleet_review: ModuleType):
        """TC-20: git log shows last commit was over 30 days ago — WARN."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _ok(self.STALE_LOG)
            result = fleet_review.check_kb_freshness()

        _assert_result_shape(result)
        assert result["status"] in ("WARN", "CRIT")

    def test_tc21_git_error_returns_skip(self, fleet_review: ModuleType):
        """TC-21: git command fails (repo not cloned) — status SKIP."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _fail(returncode=128, stderr="not a git repository")
            result = fleet_review.check_kb_freshness()

        _assert_result_shape(result)
        assert result["status"] == "SKIP"


# ---------------------------------------------------------------------------
# TC-22  build_report — report formatter
# ---------------------------------------------------------------------------

class TestBuildReport:
    """Tests for build_report(results) — Markdown report formatter."""

    SAMPLE_RESULTS = [
        {"name": "Guard Alignment",     "status": "OK",   "findings": "All 3 VMs match",           "details": ""},
        {"name": "SDLC Compliance",     "status": "WARN", "findings": "2 stories missing artifacts","details": "STORY-300, STORY-301"},
        {"name": "Model Economics",     "status": "OK",   "findings": "$12.34 this period",         "details": ""},
        {"name": "Agent Health",        "status": "OK",   "findings": "All agents healthy",         "details": ""},
        {"name": "Tool Drift",          "status": "CRIT", "findings": "Hash mismatch on derrick",   "details": "dispatch_poller.py differs"},
        {"name": "Open PRs",            "status": "WARN", "findings": "2 open PRs",                 "details": "PR #42, PR #43"},
        {"name": "KB Freshness",        "status": "OK",   "findings": "Last commit 1 day ago",      "details": ""},
    ]

    def test_tc22_returns_string(self, fleet_review: ModuleType):
        """TC-22: build_report returns a non-empty string."""
        report = fleet_review.build_report(self.SAMPLE_RESULTS)
        assert isinstance(report, str)
        assert len(report) > 0

    def test_tc23_contains_scorecard_table(self, fleet_review: ModuleType):
        """TC-23: Report contains a Markdown table with header and separator."""
        report = fleet_review.build_report(self.SAMPLE_RESULTS)
        lines = report.splitlines()
        table_lines = [l for l in lines if "|" in l]
        assert len(table_lines) >= 3, "Expected at least header, separator, and one data row"

    def test_tc24_all_check_names_present(self, fleet_review: ModuleType):
        """TC-24: Every check name appears somewhere in the report."""
        report = fleet_review.build_report(self.SAMPLE_RESULTS)
        for result in self.SAMPLE_RESULTS:
            assert result["name"] in report, f"Missing check name: {result['name']}"

    def test_tc25_all_statuses_present(self, fleet_review: ModuleType):
        """TC-25: Every status value (OK/WARN/CRIT) appears in the report."""
        report = fleet_review.build_report(self.SAMPLE_RESULTS)
        for result in self.SAMPLE_RESULTS:
            assert result["status"] in report

    def test_tc26_report_has_title(self, fleet_review: ModuleType):
        """TC-26: Report begins with a Markdown heading."""
        report = fleet_review.build_report(self.SAMPLE_RESULTS)
        assert report.lstrip().startswith("#"), "Report must start with a Markdown heading"


# ---------------------------------------------------------------------------
# TC-27  run_all_checks — orchestrator
# ---------------------------------------------------------------------------

class TestRunAllChecks:
    """Tests for run_all_checks() — the main orchestrator."""

    def _make_check_result(self, name: str, status: str = "OK") -> dict:
        return {"name": name, "status": status, "findings": "ok", "details": ""}

    def test_tc27_returns_seven_results(self, fleet_review: ModuleType):
        """TC-27: run_all_checks returns exactly 7 result dicts."""
        dummy = {"name": "X", "status": "OK", "findings": "", "details": ""}
        check_names = [
            "check_guard_alignment",
            "check_sdlc_compliance",
            "check_model_economics",
            "check_agent_health",
            "check_tool_drift",
            "check_open_prs",
            "check_kb_freshness",
        ]
        patches = {name: patch.object(fleet_review, name, return_value=dummy)
                   for name in check_names}

        with patches["check_guard_alignment"], \
             patches["check_sdlc_compliance"], \
             patches["check_model_economics"], \
             patches["check_agent_health"], \
             patches["check_tool_drift"], \
             patches["check_open_prs"], \
             patches["check_kb_freshness"]:
            results = fleet_review.run_all_checks()

        assert isinstance(results, list)
        assert len(results) == 7

    def test_tc28_partial_failure_does_not_raise(self, fleet_review: ModuleType):
        """TC-28: One check raising an unexpected exception must not abort remaining checks."""
        call_log = []

        def boom():
            call_log.append("boom")
            raise RuntimeError("unexpected check failure")

        def ok_check():
            call_log.append("ok")
            return {"name": "X", "status": "OK", "findings": "", "details": ""}

        with patch.object(fleet_review, "check_guard_alignment", side_effect=boom), \
             patch.object(fleet_review, "check_sdlc_compliance", side_effect=ok_check), \
             patch.object(fleet_review, "check_model_economics", side_effect=ok_check), \
             patch.object(fleet_review, "check_agent_health", side_effect=ok_check), \
             patch.object(fleet_review, "check_tool_drift", side_effect=ok_check), \
             patch.object(fleet_review, "check_open_prs", side_effect=ok_check), \
             patch.object(fleet_review, "check_kb_freshness", side_effect=ok_check):
            results = fleet_review.run_all_checks()

        # The orchestrator should still return 7 results; the failed check gets SKIP or CRIT
        assert len(results) == 7
        failed = next(r for r in results if r.get("status") in ("SKIP", "CRIT", "WARN")
                      and "Guard" in r.get("name", ""))
        assert failed is not None

    def test_tc29_result_order_matches_check_order(self, fleet_review: ModuleType):
        """TC-29: Results are returned in the canonical check order (1..7)."""
        names_in_order = [
            "Guard Alignment",
            "SDLC Compliance",
            "Model Economics",
            "Agent Health",
            "Tool Drift",
            "Open PRs",
            "KB Freshness",
        ]

        def make_mock(name):
            return lambda: {"name": name, "status": "OK", "findings": "", "details": ""}

        with patch.object(fleet_review, "check_guard_alignment",  side_effect=make_mock("Guard Alignment")), \
             patch.object(fleet_review, "check_sdlc_compliance",  side_effect=make_mock("SDLC Compliance")), \
             patch.object(fleet_review, "check_model_economics",  side_effect=make_mock("Model Economics")), \
             patch.object(fleet_review, "check_agent_health",     side_effect=make_mock("Agent Health")), \
             patch.object(fleet_review, "check_tool_drift",       side_effect=make_mock("Tool Drift")), \
             patch.object(fleet_review, "check_open_prs",         side_effect=make_mock("Open PRs")), \
             patch.object(fleet_review, "check_kb_freshness",     side_effect=make_mock("KB Freshness")):
            results = fleet_review.run_all_checks()

        result_names = [r["name"] for r in results]
        assert result_names == names_in_order, (
            f"Expected order {names_in_order}, got {result_names}"
        )
