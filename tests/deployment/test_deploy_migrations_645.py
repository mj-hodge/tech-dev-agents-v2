"""STORY-645 — Deploy script migration integration tests.

Verify that deploy.sh wires the migration auto-apply step correctly:
ordering, failure handling, and graceful skip when no migrations exist.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_SH = REPO_ROOT / "deployment" / "ops-console" / "deploy.sh"


@pytest.fixture(scope="module")
def deploy_sh_text() -> str:
    return DEPLOY_SH.read_text()


# --------------------------------------------------------------------------- #
# Group A: Deploy Script Migration Integration
# --------------------------------------------------------------------------- #


class TestMigrationsRunBeforeHealthCheck:
    """Migration step must execute BEFORE the health check curl."""

    def test_migrations_run_before_health_check(self, deploy_sh_text: str):
        """The migration loop (psql) must appear in the script text before
        the curl health-check. If migrations run after the health check,
        the container may serve traffic against an un-migrated schema.
        """
        # Find positions of the migration psql block and the health curl
        migration_pos = deploy_sh_text.find("psql -U ops_console -d ops_console")
        health_pos = deploy_sh_text.find("curl -sf")

        assert migration_pos != -1, (
            "deploy.sh must contain a psql invocation against ops_console"
        )
        assert health_pos != -1, (
            "deploy.sh must contain a curl health check"
        )
        assert migration_pos < health_pos, (
            "Migrations must run BEFORE the health check. "
            f"psql found at char {migration_pos}, curl at {health_pos}."
        )


class TestMigrationFailureAbortsDeploy:
    """A failing migration must abort the deploy — code and schema must
    never drift.
    """

    def test_migration_failure_aborts_deploy(self, deploy_sh_text: str):
        """The psql invocation must use ON_ERROR_STOP=1 or be followed
        by `|| exit 1` (or equivalent) so a failing migration halts the
        deploy before the container restarts.
        """
        # Look for ON_ERROR_STOP=1 in the psql command
        has_on_error_stop = "ON_ERROR_STOP=1" in deploy_sh_text

        # Or look for || exit 1 pattern near the psql command
        has_exit_on_fail = bool(re.search(
            r"psql[^\n]*\n[^\n]*\|\|\s*\{[^\}]*exit\s+1",
            deploy_sh_text,
            re.DOTALL,
        ))

        # Also accept || { log ... ; exit 1; } form
        has_exit_on_fail_inline = bool(re.search(
            r"psql.*\|\|.*exit\s+1",
            deploy_sh_text,
            re.DOTALL,
        ))

        assert has_on_error_stop or has_exit_on_fail or has_exit_on_fail_inline, (
            "deploy.sh must abort on migration failure — use "
            "ON_ERROR_STOP=1 or `|| exit 1` after the psql command."
        )


class TestMigrationsSortedNumerically:
    """Migrations must run in filename order (001, 002, ..., 010)."""

    def test_migrations_sorted_numerically(self, deploy_sh_text: str):
        """The migration loop must sort files by name so that 001 runs
        before 002, and 009 before 010. Using unsorted glob risks
        running a dependent migration before its prerequisite.
        """
        # Look for sort command (sort -z for null-delimited, or sort alone)
        has_sort = bool(re.search(r"\bsort\b", deploy_sh_text))

        # Or a for loop with sorted glob (bash globs are sorted by default)
        has_sorted_glob = bool(re.search(
            r'for\s+\w+\s+in\s+["\$]*\{?MIGRATIONS_DIR\}?[/\*]*\.sql',
            deploy_sh_text,
        ))

        assert has_sort or has_sorted_glob, (
            "deploy.sh must sort migration files numerically. "
            "Use `sort` or rely on sorted glob expansion."
        )


class TestNoMigrationsDirSkipsGracefully:
    """When no migrations directory exists, deploy should continue."""

    def test_no_migrations_dir_skips_gracefully(self, deploy_sh_text: str):
        """If the migrations directory doesn't exist (e.g. a deploy
        package without schema changes), deploy.sh must skip the
        migration step gracefully — not exit non-zero.
        """
        # Look for a conditional check on the migrations directory
        has_dir_check = bool(re.search(
            r'if\s+\[\s+-d\s+.*MIGRATIONS_DIR',
            deploy_sh_text,
        ))

        # And a corresponding else/skip log
        has_skip_log = bool(re.search(
            r'(skip|no\s+migration)',
            deploy_sh_text,
            re.IGNORECASE,
        ))

        assert has_dir_check, (
            "deploy.sh must check if MIGRATIONS_DIR exists before "
            "attempting to apply migrations."
        )
        assert has_skip_log, (
            "deploy.sh must log a skip message when no migrations "
            "directory is found."
        )
