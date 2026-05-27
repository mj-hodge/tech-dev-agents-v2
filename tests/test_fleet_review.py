#!/usr/bin/env python3
"""Tests for scripts/fleet_review.py — STORY-324.

All external calls (SSH, az CLI, gh CLI) are mocked.  Tests exercise
parsing, classification, and report-formatting logic.
"""

from __future__ import annotations

import json
import subprocess
import textwrap
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Import helpers — the script lives in scripts/, not a package
# ---------------------------------------------------------------------------

import importlib
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"


@pytest.fixture(autouse=True)
def _add_scripts_to_path():
    """Ensure scripts/ is importable."""
    sys.path.insert(0, str(SCRIPTS_DIR))
    yield
    sys.path.remove(str(SCRIPTS_DIR))


def _import_fleet_review():
    """Import (or reimport) the fleet_review module."""
    if "fleet_review" in sys.modules:
        del sys.modules["fleet_review"]
    return importlib.import_module("fleet_review")


# ---------------------------------------------------------------------------
# Fixtures — realistic subprocess outputs
# ---------------------------------------------------------------------------

REPO_GUARD_MD5 = "abc123def456"
AGENT_IPS = {
    "dan": "20.228.224.243",
    "derrick": "20.121.210.186",
    "morris": "20.246.36.143",
}


def _make_ssh_health_output(
    sdk=1, poller="active", gateway="active", auth="true", disk="42%"
):
    return (
        f"sdk={sdk}\n"
        f"poller={poller}\n"
        f"gateway={gateway}\n"
        f"auth={auth}\n"
        f"disk={disk}\n"
    )


def _make_md5_output(md5: str, path: str = "/opt/agent/terminal_guard.py"):
    return f"{md5}  {path}\n"


# ---------------------------------------------------------------------------
# Tests: report structure
# ---------------------------------------------------------------------------


class TestReportStructure:
    """Full report must contain all 7 check sections."""

    @patch("subprocess.run")
    def test_full_report_contains_all_sections(self, mock_run):
        """Running all checks produces a report with 7 scorecard rows."""
        fr = _import_fleet_review()

        # Generic mock — all checks will get empty/error responses
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="timeout"
        )

        report = fr.run_all_checks()
        assert "# Fleet Review" in report
        assert "## Scorecard" in report
        # All 7 check names should appear
        for name in [
            "Guard alignment",
            "SDLC compliance",
            "Model economics",
            "Agent health",
            "Tool drift",
            "Open PRs",
            "KB freshness",
        ]:
            assert name in report

    @patch("subprocess.run")
    def test_scorecard_is_markdown_table(self, mock_run):
        """Scorecard must be a valid markdown table with header + separator."""
        fr = _import_fleet_review()
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr=""
        )
        report = fr.run_all_checks()
        lines = report.split("\n")
        scorecard_start = None
        for i, line in enumerate(lines):
            if line.strip().startswith("| Check"):
                scorecard_start = i
                break
        assert scorecard_start is not None, "Scorecard table not found"
        # Next line should be separator
        assert lines[scorecard_start + 1].strip().startswith("|---")

    @patch("subprocess.run")
    def test_executive_summary_present(self, mock_run):
        """Report includes an executive summary section."""
        fr = _import_fleet_review()
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr=""
        )
        report = fr.run_all_checks()
        assert "## Executive Summary" in report


# ---------------------------------------------------------------------------
# Tests: guard alignment (Check 1)
# ---------------------------------------------------------------------------


class TestGuardAlignment:
    """Check 1: MD5 of terminal_guard.py on VMs vs repo."""

    @patch("subprocess.run")
    def test_all_guards_match_returns_ok(self, mock_run):
        fr = _import_fleet_review()
        md5 = "abc123"

        def side_effect(cmd, **kw):
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
            # Local md5sum
            if "md5sum" in cmd_str and "ssh" not in cmd_str:
                return MagicMock(returncode=0, stdout=f"{md5}  deployment/vm/terminal_guard.py\n", stderr="")
            # SSH md5sum
            if "ssh" in cmd_str and "md5sum" in cmd_str:
                return MagicMock(returncode=0, stdout=f"{md5}  /opt/agent/terminal_guard.py\n", stderr="")
            return MagicMock(returncode=1, stdout="", stderr="")

        mock_run.side_effect = side_effect
        result = fr.check_guard_alignment()
        assert result["status"] == "OK"

    @patch("subprocess.run")
    def test_guard_mismatch_returns_crit(self, mock_run):
        fr = _import_fleet_review()

        def side_effect(cmd, **kw):
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
            if "md5sum" in cmd_str and "ssh" not in cmd_str:
                return MagicMock(returncode=0, stdout="abc123  deployment/vm/terminal_guard.py\n", stderr="")
            if "ssh" in cmd_str and "md5sum" in cmd_str:
                return MagicMock(returncode=0, stdout="DIFFERENT  /opt/agent/terminal_guard.py\n", stderr="")
            return MagicMock(returncode=1, stdout="", stderr="")

        mock_run.side_effect = side_effect
        result = fr.check_guard_alignment()
        assert result["status"] == "CRIT"

    @patch("subprocess.run")
    def test_ssh_unreachable_returns_crit(self, mock_run):
        fr = _import_fleet_review()

        def side_effect(cmd, **kw):
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
            if "md5sum" in cmd_str and "ssh" not in cmd_str:
                return MagicMock(returncode=0, stdout="abc123  file\n", stderr="")
            if "ssh" in cmd_str:
                raise subprocess.TimeoutExpired(cmd, 10)
            return MagicMock(returncode=1, stdout="", stderr="")

        mock_run.side_effect = side_effect
        result = fr.check_guard_alignment()
        assert result["status"] == "CRIT"


# ---------------------------------------------------------------------------
# Tests: SDLC compliance (Check 2)
# ---------------------------------------------------------------------------


class TestSDLCCompliance:
    """Check 2: deliverable presence for completed stories."""

    @patch("subprocess.run")
    def test_all_stories_compliant_returns_ok(self, mock_run):
        fr = _import_fleet_review()

        gh_tree = json.dumps([
            {"path": "features/story-100-foo/seed.md"},
            {"path": "features/story-100-foo/test-design.md"},
        ])

        def side_effect(cmd, **kw):
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
            # Dispatch history (curl to dispatch API)
            if "curl" in cmd_str and "dispatch" in cmd_str:
                return MagicMock(
                    returncode=0,
                    stdout=json.dumps([
                        {"story_id": "STORY-100", "repo": "tech-dev-agents", "scope": "small"}
                    ]),
                    stderr="",
                )
            # GitHub folder lookup (contains "startswith" in --jq)
            if "gh" in cmd_str and "contents/features" in cmd_str and "startswith" in cmd_str:
                return MagicMock(returncode=0, stdout="story-100-foo", stderr="")
            # GitHub file listing (contains story folder name, uses .[].name)
            if "gh" in cmd_str and "story-100-foo" in cmd_str:
                return MagicMock(returncode=0, stdout="seed.md\ntest-design.md\n", stderr="")
            if "gh" in cmd_str:
                return MagicMock(returncode=0, stdout="", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = side_effect
        result = fr.check_sdlc_compliance()
        assert result["status"] == "OK"

    @patch("subprocess.run")
    def test_missing_seed_returns_crit(self, mock_run):
        fr = _import_fleet_review()

        def side_effect(cmd, **kw):
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
            if "curl" in cmd_str and "dispatch" in cmd_str:
                return MagicMock(
                    returncode=0,
                    stdout=json.dumps([
                        {"story_id": "STORY-100", "repo": "tech-dev-agents", "scope": "small"}
                    ]),
                    stderr="",
                )
            if "gh" in cmd_str and "contents" in cmd_str:
                return MagicMock(returncode=0, stdout="[]", stderr="")
            if "gh" in cmd_str:
                return MagicMock(returncode=0, stdout="", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = side_effect
        result = fr.check_sdlc_compliance()
        assert result["status"] == "CRIT"


# ---------------------------------------------------------------------------
# Tests: model economics (Check 3)
# ---------------------------------------------------------------------------


class TestModelEconomics:
    """Check 3: Azure cost query."""

    @patch("subprocess.run")
    def test_low_cost_returns_ok(self, mock_run):
        fr = _import_fleet_review()
        cost_response = {
            "properties": {
                "rows": [
                    [50.00, "Claude Sonnet", "USD"],
                    [30.00, "Claude Opus", "USD"],
                ]
            }
        }

        def side_effect(cmd, **kw):
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
            if "az" in cmd_str and "rest" in cmd_str:
                return MagicMock(
                    returncode=0, stdout=json.dumps(cost_response), stderr=""
                )
            return MagicMock(returncode=1, stdout="", stderr="")

        mock_run.side_effect = side_effect
        result = fr.check_model_economics()
        assert result["status"] == "OK"

    @patch("subprocess.run")
    def test_high_opus_cost_returns_crit(self, mock_run):
        fr = _import_fleet_review()
        cost_response = {
            "properties": {
                "rows": [
                    [50.00, "Claude Sonnet", "USD"],
                    [250.00, "Claude Opus", "USD"],
                ]
            }
        }

        def side_effect(cmd, **kw):
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
            if "az" in cmd_str:
                return MagicMock(
                    returncode=0, stdout=json.dumps(cost_response), stderr=""
                )
            return MagicMock(returncode=1, stdout="", stderr="")

        mock_run.side_effect = side_effect
        result = fr.check_model_economics()
        assert result["status"] == "CRIT"

    @patch("subprocess.run")
    def test_az_cli_failure_returns_skip(self, mock_run):
        fr = _import_fleet_review()

        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="az: command not found"
        )
        result = fr.check_model_economics()
        assert result["status"] == "SKIP"


# ---------------------------------------------------------------------------
# Tests: agent health (Check 4)
# ---------------------------------------------------------------------------


class TestAgentHealth:
    """Check 4: SSH probes."""

    @patch("subprocess.run")
    def test_all_healthy_returns_ok(self, mock_run):
        fr = _import_fleet_review()
        healthy = _make_ssh_health_output()

        def side_effect(cmd, **kw):
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
            if "ssh" in cmd_str:
                return MagicMock(returncode=0, stdout=healthy, stderr="")
            return MagicMock(returncode=1, stdout="", stderr="")

        mock_run.side_effect = side_effect
        result = fr.check_agent_health()
        assert result["status"] == "OK"

    @patch("subprocess.run")
    def test_agent_unreachable_returns_crit(self, mock_run):
        fr = _import_fleet_review()

        def side_effect(cmd, **kw):
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
            if "ssh" in cmd_str:
                raise subprocess.TimeoutExpired(cmd, 10)
            return MagicMock(returncode=1, stdout="", stderr="")

        mock_run.side_effect = side_effect
        result = fr.check_agent_health()
        assert result["status"] == "CRIT"

    @patch("subprocess.run")
    def test_high_disk_returns_warn(self, mock_run):
        fr = _import_fleet_review()
        high_disk = _make_ssh_health_output(disk="92%")

        def side_effect(cmd, **kw):
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
            if "ssh" in cmd_str:
                return MagicMock(returncode=0, stdout=high_disk, stderr="")
            return MagicMock(returncode=1, stdout="", stderr="")

        mock_run.side_effect = side_effect
        result = fr.check_agent_health()
        assert result["status"] == "WARN"


# ---------------------------------------------------------------------------
# Tests: tool drift (Check 5)
# ---------------------------------------------------------------------------


class TestToolDrift:
    """Check 5: deployed files vs repo source."""

    @patch("subprocess.run")
    def test_all_files_match_returns_ok(self, mock_run):
        fr = _import_fleet_review()
        md5 = "abc123"

        def side_effect(cmd, **kw):
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
            if "md5sum" in cmd_str:
                return MagicMock(returncode=0, stdout=f"{md5}  somefile\n", stderr="")
            return MagicMock(returncode=1, stdout="", stderr="")

        mock_run.side_effect = side_effect
        result = fr.check_tool_drift()
        assert result["status"] == "OK"

    @patch("subprocess.run")
    def test_critical_file_stale_returns_crit(self, mock_run):
        fr = _import_fleet_review()

        call_count = {"n": 0}

        def side_effect(cmd, **kw):
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
            if "md5sum" in cmd_str and "ssh" not in cmd_str:
                return MagicMock(returncode=0, stdout="repo_hash  somefile\n", stderr="")
            if "ssh" in cmd_str and "md5sum" in cmd_str:
                call_count["n"] += 1
                # First critical file is stale
                return MagicMock(returncode=0, stdout="stale_hash  somefile\n", stderr="")
            return MagicMock(returncode=1, stdout="", stderr="")

        mock_run.side_effect = side_effect
        result = fr.check_tool_drift()
        assert result["status"] == "CRIT"


# ---------------------------------------------------------------------------
# Tests: open PRs (Check 6)
# ---------------------------------------------------------------------------


class TestOpenPRs:
    """Check 6: open PRs across repos (informational)."""

    @patch("subprocess.run")
    def test_prs_listed_returns_ok(self, mock_run):
        fr = _import_fleet_review()
        pr_json = json.dumps([
            {"number": 42, "title": "Fix thing", "author": {"login": "dan"}, "createdAt": "2026-04-10T00:00:00Z"}
        ])

        def side_effect(cmd, **kw):
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
            if "gh" in cmd_str and "pr" in cmd_str:
                return MagicMock(returncode=0, stdout=pr_json, stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = side_effect
        result = fr.check_open_prs()
        assert result["status"] == "OK"

    @patch("subprocess.run")
    def test_gh_failure_returns_skip(self, mock_run):
        fr = _import_fleet_review()
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="gh: command not found"
        )
        result = fr.check_open_prs()
        assert result["status"] == "SKIP"


# ---------------------------------------------------------------------------
# Tests: KB freshness (Check 7)
# ---------------------------------------------------------------------------


class TestKBFreshness:
    """Check 7: knowledge-base commit recency."""

    @patch("subprocess.run")
    def test_recent_commits_returns_ok(self, mock_run):
        fr = _import_fleet_review()

        def side_effect(cmd, **kw):
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
            if "git" in cmd_str and "log" in cmd_str:
                return MagicMock(returncode=0, stdout="5\n", stderr="")
            if "find" in cmd_str:
                return MagicMock(returncode=0, stdout="", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = side_effect
        result = fr.check_kb_freshness()
        assert result["status"] == "OK"

    @patch("subprocess.run")
    def test_zero_commits_returns_warn(self, mock_run):
        fr = _import_fleet_review()

        def side_effect(cmd, **kw):
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
            if "git" in cmd_str and "log" in cmd_str:
                # Empty output = no commits (git log --oneline returns nothing)
                return MagicMock(returncode=0, stdout="", stderr="")
            if "find" in cmd_str:
                return MagicMock(returncode=0, stdout="old-doc.md\n", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = side_effect
        result = fr.check_kb_freshness()
        assert result["status"] == "WARN"


# ---------------------------------------------------------------------------
# Tests: error resilience
# ---------------------------------------------------------------------------


class TestErrorResilience:
    """Failed checks must not crash the full report."""

    @patch("subprocess.run")
    def test_all_checks_fail_still_produces_report(self, mock_run):
        """Even if every subprocess fails, a report is produced."""
        fr = _import_fleet_review()
        mock_run.side_effect = subprocess.TimeoutExpired("cmd", 10)
        report = fr.run_all_checks()
        assert "# Fleet Review" in report
        assert "Scorecard" in report

    @patch("subprocess.run")
    def test_single_check_exception_doesnt_block_others(self, mock_run):
        """A check that raises still allows the rest to run."""
        fr = _import_fleet_review()

        call_count = {"n": 0}

        def side_effect(cmd, **kw):
            call_count["n"] += 1
            if call_count["n"] <= 3:
                raise OSError("network down")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = side_effect
        report = fr.run_all_checks()
        # Should still have all 7 sections
        for name in ["Guard alignment", "SDLC compliance", "Model economics",
                      "Agent health", "Tool drift", "Open PRs", "KB freshness"]:
            assert name in report


# ---------------------------------------------------------------------------
# Tests: CLI interface
# ---------------------------------------------------------------------------


class TestCLI:
    """Command-line argument handling."""

    @patch("subprocess.run")
    def test_single_check_flag(self, mock_run):
        fr = _import_fleet_review()
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")
        # The parse_args + run_check function should handle --check
        args = fr.parse_args(["--check", "guard"])
        assert args.check == "guard"

    def test_valid_check_names(self):
        fr = _import_fleet_review()
        valid = fr.VALID_CHECKS
        assert "guard" in valid
        assert "sdlc" in valid
        assert "economics" in valid
        assert "health" in valid
        assert "drift" in valid
        assert "prs" in valid
        assert "kb" in valid
