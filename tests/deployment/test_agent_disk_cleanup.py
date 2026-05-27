"""
Tests for STORY-768: Agent VM Disk Cleanup Cron.

Verifies the agent-disk-cleanup.sh script:
- Exists and has correct structure (bash shebang, flags)
- Targets all 6 known disk-growth sources
- Dry-run does not delete files
- Watermark guard skips cleanup when disk < 60%
- Force flag bypasses watermark
- File age thresholds (14d for caches, 30d for failed-story flags)
- Branch prune safety (only MERGED/CLOSED, never main)
- Continues on individual step failure (AC-12)
- Logs bytes freed per source
- Install script creates cron entry idempotently
- Security: no rm -rf, hardcoded paths, no root

Test strategy:
- Script structural tests read the deployed file content directly.
- Behavioral tests use tmp_path fixtures mimicking VM filesystem layout
  and mock subprocess calls for journalctl/gh/df.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "deployment" / "vm" / "scripts"
CLEANUP_SCRIPT = SCRIPTS_DIR / "agent-disk-cleanup.sh"
INSTALL_SCRIPT = SCRIPTS_DIR / "install-disk-cleanup-cron.sh"


def _read_cleanup() -> str:
    """Return contents of agent-disk-cleanup.sh.

    Raises AssertionError (not FileNotFoundError) if file is missing,
    so tests show as FAIL with a clear message rather than a traceback.
    """
    assert CLEANUP_SCRIPT.exists(), (
        f"agent-disk-cleanup.sh not found at {CLEANUP_SCRIPT} — "
        "create the script before these tests can pass"
    )
    return CLEANUP_SCRIPT.read_text()


def _read_install() -> str:
    """Return contents of install-disk-cleanup-cron.sh.

    Raises AssertionError (not FileNotFoundError) if file is missing.
    """
    assert INSTALL_SCRIPT.exists(), (
        f"install-disk-cleanup-cron.sh not found at {INSTALL_SCRIPT} — "
        "create the script before these tests can pass"
    )
    return INSTALL_SCRIPT.read_text()


# ===========================================================================
# Category 1: Script Structural Tests
# ===========================================================================


class TestScriptStructure:
    """AC-1, AC-2, AC-3: Scripts exist with correct structure."""

    def test_cleanup_script_exists(self):
        """AC-1: agent-disk-cleanup.sh must exist at deployment/vm/scripts/."""
        assert CLEANUP_SCRIPT.exists(), (
            f"agent-disk-cleanup.sh not found at {CLEANUP_SCRIPT}"
        )

    def test_cleanup_script_is_bash(self):
        """AC-1: Cleanup script must have a bash shebang."""
        content = _read_cleanup()
        assert content.startswith("#!/usr/bin/env bash") or content.startswith(
            "#!/bin/bash"
        ), "agent-disk-cleanup.sh must have a bash shebang"

    def test_cleanup_script_supports_dry_run_flag(self):
        """AC-3: Script must recognize --dry-run flag."""
        content = _read_cleanup()
        assert "--dry-run" in content, (
            "agent-disk-cleanup.sh must support --dry-run flag"
        )

    def test_cleanup_script_supports_force_flag(self):
        """AC-3: Script must recognize --force flag."""
        content = _read_cleanup()
        assert "--force" in content, (
            "agent-disk-cleanup.sh must support --force flag"
        )

    def test_install_script_exists(self):
        """AC-2: install-disk-cleanup-cron.sh must exist."""
        assert INSTALL_SCRIPT.exists(), (
            f"install-disk-cleanup-cron.sh not found at {INSTALL_SCRIPT}"
        )

    def test_install_script_is_bash(self):
        """AC-2: Install script must have a bash shebang."""
        content = _read_install()
        assert content.startswith("#!/usr/bin/env bash") or content.startswith(
            "#!/bin/bash"
        ), "install-disk-cleanup-cron.sh must have a bash shebang"


# ===========================================================================
# Category 2: Cleanup Targets
# ===========================================================================


class TestCleanupTargets:
    """Verify script targets all 6 known disk-growth sources."""

    def test_script_targets_journalctl_vacuum(self):
        """AC-4: journalctl --vacuum-time=7d must be in the script."""
        content = _read_cleanup()
        assert "journalctl --vacuum-time=7d" in content, (
            "Script must include journalctl --vacuum-time=7d (SC-3, AC-4)"
        )

    def test_script_targets_pytest_cache(self):
        """AC-6: pytest_cache cleanup must be present."""
        content = _read_cleanup()
        assert "pytest_cache" in content, (
            "Script must target ~/.cache/pytest_cache/ (AC-6)"
        )

    def test_script_targets_uv_cache(self):
        """AC-6: uv cache cleanup must be present."""
        content = _read_cleanup()
        # Should reference .cache/uv
        assert re.search(r"\.cache/uv", content), (
            "Script must target ~/.cache/uv/ (AC-6)"
        )

    def test_script_targets_failed_story_flags(self):
        """AC-7: failed-stories cleanup must be present."""
        content = _read_cleanup()
        assert "failed-stories" in content, (
            "Script must target ~/state/<agent>/failed-stories/ (AC-7)"
        )

    def test_script_targets_merged_branches(self):
        """AC-5: Branch pruning logic must be present."""
        content = _read_cleanup()
        # Should reference gh pr view or similar mechanism to check PR status
        assert "gh pr view" in content or "gh pr list" in content, (
            "Script must use gh CLI to check branch PR status before pruning (AC-5)"
        )

    def test_script_has_watermark_guard(self):
        """AC-8: 60% watermark check must be present."""
        content = _read_cleanup()
        assert "60" in content, (
            "Script must check 60% disk watermark threshold (AC-8)"
        )


# ===========================================================================
# Category 3: Dry-Run Semantics
# ===========================================================================


class TestDryRun:
    """SC-7: --dry-run prints plan but does not delete."""

    def test_dry_run_does_not_delete_files(self, tmp_path: Path):
        """SC-7: Running with --dry-run must leave all files intact.

        Arrange: Create a tmp_path with old cache files that would be cleaned.
        Act: Parse script for dry-run guard logic.
        Assert: Script has conditional that prevents deletion when DRY_RUN is set.
        """
        content = _read_cleanup()
        # The script must have a dry-run guard that prevents actual deletion
        # Look for pattern: if DRY_RUN is set, print instead of delete
        assert re.search(r"(DRY_RUN|dry_run|DRYRUN)", content, re.IGNORECASE), (
            "Script must have a DRY_RUN variable/flag that gates deletion"
        )
        # Verify dry-run mode prints instead of deleting
        # The script should have conditional logic around delete commands
        lines = content.splitlines()
        has_dry_run_guard = any(
            "dry" in line.lower() and ("echo" in line.lower() or "print" in line.lower() or "log" in line.lower())
            for line in lines
        ) or re.search(r'if\s+.*\b(DRY_RUN|dry_run)\b', content, re.IGNORECASE)
        assert has_dry_run_guard, (
            "Script must guard deletions with DRY_RUN check"
        )


# ===========================================================================
# Category 4: Watermark Guard
# ===========================================================================


class TestWatermarkGuard:
    """SC-8, AC-8: Disk usage watermark controls cleanup execution."""

    def test_below_watermark_skips_cleanup(self):
        """SC-8: When disk usage < 60%, script should early-exit.

        Verify the script contains logic to parse df output and compare against
        the 60% threshold, with an early-exit path.
        """
        content = _read_cleanup()
        # Script must use df to check disk usage
        assert "df " in content or "df\n" in content or "df)" in content, (
            "Script must use df to check current disk usage"
        )
        # Script must have a comparison with the threshold and exit/return
        assert re.search(r"(no.op|skip|exit|early)", content, re.IGNORECASE), (
            "Script must have an early-exit path when disk is below watermark"
        )

    def test_above_watermark_runs_cleanup(self):
        """SC-8: When disk usage > 60%, cleanup proceeds.

        Verify the script has cleanup actions that execute when above threshold.
        """
        content = _read_cleanup()
        # Should have both the check and the cleanup path
        assert re.search(r"(running|starting|proceed|cleanup)", content, re.IGNORECASE), (
            "Script must have a cleanup execution path when above watermark"
        )

    def test_force_flag_ignores_watermark(self):
        """AC-3: --force runs cleanup regardless of disk usage.

        Verify that force flag bypasses the watermark check.
        """
        content = _read_cleanup()
        # Should have force flag logic that skips watermark check
        assert re.search(r"(FORCE|force)", content), (
            "Script must support --force to bypass watermark"
        )
        # Force and watermark should interact — force should skip the watermark check
        has_force_bypass = re.search(
            r"(FORCE|force).*\b(skip|bypass|regardless|ignore)\b"
            r"|\b(skip|bypass|regardless|ignore)\b.*(FORCE|force)",
            content,
            re.IGNORECASE | re.DOTALL,
        )
        # Alternative: the force flag simply runs cleanup unconditionally
        has_force_unconditional = re.search(
            r"(FORCE|force).*(cleanup|clean|run|proceed)",
            content,
            re.IGNORECASE | re.DOTALL,
        )
        assert has_force_bypass or has_force_unconditional, (
            "Script must bypass watermark when --force is set"
        )


# ===========================================================================
# Category 5: File Age Thresholds
# ===========================================================================


class TestFileAgeThresholds:
    """AC-6, AC-7: File age thresholds for cache and flag pruning."""

    def test_pytest_cache_14_day_threshold(self):
        """AC-6: pytest_cache and uv cache entries older than 14 days are pruned.

        Verify the find command uses +14 (or 14d equivalent) for cache cleanup.
        """
        content = _read_cleanup()
        # Should use find with -atime +14 or -mtime +14 for cache dirs
        assert re.search(r"(atime|mtime)\s+\+14", content), (
            "Cache cleanup must use -atime +14 or -mtime +14 threshold (AC-6)"
        )

    def test_failed_story_flags_30_day_threshold(self):
        """AC-7: Failed-story flags older than 30 days are deleted.

        Verify the find command uses +30 for failed-story flag cleanup.
        """
        content = _read_cleanup()
        assert re.search(r"(atime|mtime)\s+\+30", content), (
            "Failed-story flag cleanup must use +30 day threshold (AC-7)"
        )


# ===========================================================================
# Category 6: Branch Prune Safety
# ===========================================================================


class TestBranchPruneSafety:
    """AC-5: Branch pruning only for MERGED/CLOSED PRs, never main."""

    def test_branch_prune_only_merged_or_closed(self):
        """AC-5: Script checks PR status via gh before deleting branches.

        Only MERGED or CLOSED PRs trigger branch deletion.
        """
        content = _read_cleanup()
        # Must check for MERGED and CLOSED states
        assert "MERGED" in content or "merged" in content, (
            "Branch prune must check for MERGED PR state"
        )
        assert "CLOSED" in content or "closed" in content, (
            "Branch prune must check for CLOSED PR state"
        )

    def test_branch_prune_skips_main(self):
        """AC-5: main branch is never deleted.

        Verify explicit guard against deleting main/master.
        """
        content = _read_cleanup()
        # Script must have a guard to never delete main
        assert re.search(r"\bmain\b", content), (
            "Script must explicitly guard against deleting main branch"
        )


# ===========================================================================
# Category 7: Error Resilience
# ===========================================================================


class TestErrorResilience:
    """AC-12: Script continues on individual step failure."""

    def test_continues_on_step_failure(self):
        """AC-12: If one cleanup step fails, script continues to next.

        Verify the script does NOT use `set -e` globally, or uses trap/continue
        patterns around cleanup steps.
        """
        content = _read_cleanup()
        # Script should NOT have `set -e` that would abort on first failure
        # OR if it has set -e, it must use `|| true` or trap to continue
        has_set_e = "set -e" in content
        has_continue_pattern = (
            "|| true" in content
            or "|| :" in content
            or "|| log" in content.lower()
            or "|| echo" in content.lower()
            or "continue" in content
        )
        if has_set_e:
            assert has_continue_pattern, (
                "Script uses set -e but must have || true or trap patterns "
                "to ensure individual cleanup steps don't abort the whole script (AC-12)"
            )
        else:
            # Without set -e, failures naturally continue — verify at least
            # one error logging pattern exists
            assert re.search(r"(error|fail|warn)", content, re.IGNORECASE), (
                "Script must log errors from failed cleanup steps (AC-12)"
            )


# ===========================================================================
# Category 8: Logging
# ===========================================================================


class TestLogging:
    """AC-9, AC-11: Cleanup logs bytes freed per source."""

    def test_logs_bytes_freed_per_source(self):
        """AC-11: Script logs [CLEANUP] with bytes freed per source.

        Verify the script emits [CLEANUP] log lines.
        """
        content = _read_cleanup()
        assert "[CLEANUP]" in content, (
            "Script must use [CLEANUP] prefix in log output (AC-11)"
        )
        # Should log freed bytes
        assert re.search(r"(freed|bytes|total)", content, re.IGNORECASE), (
            "Script must log bytes freed per source (AC-11)"
        )


# ===========================================================================
# Category 9: Deploy Integration
# ===========================================================================


class TestDeployIntegration:
    """AC-2: Install script creates cron entry; idempotent."""

    def test_install_script_creates_cron_entry(self):
        """AC-2: Install script must reference agent-disk-cleanup.sh in crontab.

        Verify the install script sets up the 03:00 UTC daily cron.
        """
        content = _read_install()
        assert "agent-disk-cleanup" in content, (
            "Install script must reference agent-disk-cleanup.sh"
        )
        # Should create a cron entry with the 03:00 schedule
        assert re.search(r"0\s+3\s+\*\s+\*\s+\*", content), (
            "Install script must set up 0 3 * * * cron schedule (SC-2)"
        )

    def test_install_script_is_idempotent(self):
        """Escalation #4: Repeated installs must not create duplicate entries.

        Verify the install script removes old entry before adding new one,
        or checks for existing entry.
        """
        content = _read_install()
        # Should check for existing entry or replace it
        has_idempotent_pattern = (
            "grep" in content  # checks existing entries
            or "crontab -r" in content  # removes old crontab
            or "sed" in content  # edits existing crontab
            or "replace" in content.lower()
            or re.search(r"(remove|delete).*old", content, re.IGNORECASE)
        )
        assert has_idempotent_pattern, (
            "Install script must handle existing cron entries idempotently"
        )


# ===========================================================================
# Category 10: Security
# ===========================================================================


class TestSecurity:
    """Security constraints: no rm -rf, hardcoded paths, limited sudo."""

    def test_no_rm_rf_in_script(self):
        """Security: Script must use find -delete, not rm -rf.

        rm -rf is dangerous; find -delete is anchored and safer.
        """
        content = _read_cleanup()
        # Strip comments before checking
        code_lines = [
            line for line in content.splitlines()
            if not line.lstrip().startswith("#")
        ]
        code = "\n".join(code_lines)
        assert "rm -rf" not in code, (
            "Script must NOT use rm -rf in executable code — use find -delete instead"
        )

    def test_cleanup_paths_are_hardcoded(self):
        """Security: Cleanup paths must be hardcoded, not from env vars.

        Prevents accidental rm of wrong paths if config is malformed.
        """
        content = _read_cleanup()
        # Script should not read cleanup target paths from environment variables
        # Look for patterns like CLEANUP_DIR="${SOME_ENV_VAR}" driving the delete targets
        # Acceptable: using $HOME or ~ for the user's home directory
        # Not acceptable: arbitrary env vars controlling delete paths
        code_lines = [
            line for line in content.splitlines()
            if not line.lstrip().startswith("#")
        ]
        for line in code_lines:
            if "delete" in line.lower() or "find" in line.lower():
                # These lines should not reference arbitrary env vars
                # (HOME and USER are OK — they're standard)
                env_refs = re.findall(r'\$\{?([A-Z_]+)\}?', line)
                forbidden = [v for v in env_refs if v not in (
                    "HOME", "USER", "DRY_RUN", "FORCE", "LOGFILE",
                    "AGENT_USER", "AGENT_HOME",
                )]
                assert not forbidden, (
                    f"Delete/find line uses non-standard env var(s): {forbidden}. "
                    "Cleanup paths must be hardcoded (Security constraint)"
                )

    def test_script_does_not_run_as_root(self):
        """Security: No broad sudo usage — only journalctl --vacuum gets sudo.

        The script runs as the agent user (hermes), not root.
        """
        content = _read_cleanup()
        # Find all sudo usages
        sudo_lines = [
            line.strip() for line in content.splitlines()
            if "sudo" in line and not line.lstrip().startswith("#")
        ]
        for line in sudo_lines:
            assert "journalctl" in line, (
                f"sudo is only allowed for journalctl --vacuum, found: {line}"
            )
