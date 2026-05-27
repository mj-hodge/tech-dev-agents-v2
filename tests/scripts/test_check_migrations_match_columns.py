"""STORY-645 — CI migration invariant check script tests.

Verify that ``scripts/ci/check_migrations_match_columns.py`` correctly
detects PR diffs that reference new DB columns without a corresponding
migration file.

All tests are unit-level — no live DB, no real git. Diffs and migration
content are provided as strings or temp files.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_SCRIPT = REPO_ROOT / "scripts" / "ci" / "check_migrations_match_columns.py"
WORKFLOW_FILE = REPO_ROOT / ".github" / "workflows" / "migration-invariant.yml"


# --------------------------------------------------------------------------- #
# Import the check script module. It doesn't exist yet (RED state), so we
# use a lazy import inside each test class to get clear failure messages.
# --------------------------------------------------------------------------- #


def _import_check_module():
    """Import the CI check script as a module. Raises ImportError in RED state."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "check_migrations_match_columns",
        str(CI_SCRIPT),
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot find {CI_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- #
# Group B: CI Check Script — Column Detection
# --------------------------------------------------------------------------- #


class TestMissingMigrationExitsNonzero:
    """PR adds a column reference with no migration → fail."""

    def test_missing_migration_exits_nonzero(self, tmp_path: Path):
        """A PR diff that adds Field('new_col') in an ops_console file
        without a migration adding 'new_col' must cause the check to
        report that column as unsubstantiated and exit non-zero.
        """
        mod = _import_check_module()

        # Arrange — a diff adding a Pydantic field for a new column
        diff = textwrap.dedent("""\
            diff --git a/tech_dev_agents/ops_console/models.py b/tech_dev_agents/ops_console/models.py
            --- a/tech_dev_agents/ops_console/models.py
            +++ b/tech_dev_agents/ops_console/models.py
            @@ -10,0 +11,1 @@
            +    new_col: str | None = None
        """)

        migrations_dir = tmp_path / "migrations"
        migrations_dir.mkdir()
        # No migration file for new_col

        # Act
        result = mod.check_diff(diff, migrations_dir=migrations_dir)

        # Assert
        assert result["exit_code"] == 1
        assert "new_col" in result["missing"]


class TestMigrationPresentExitsZero:
    """PR adds both a column reference AND a migration → pass."""

    def test_migration_present_exits_zero(self, tmp_path: Path):
        """When the PR diff includes both a field reference and a
        migration that ADDs that column, the check must pass.
        """
        mod = _import_check_module()

        diff = textwrap.dedent("""\
            diff --git a/tech_dev_agents/ops_console/models.py b/tech_dev_agents/ops_console/models.py
            --- a/tech_dev_agents/ops_console/models.py
            +++ b/tech_dev_agents/ops_console/models.py
            @@ -10,0 +11,1 @@
            +    new_col: str | None = None
            diff --git a/scripts/migrations/011_add_new_col.sql b/scripts/migrations/011_add_new_col.sql
            new file mode 100644
            --- /dev/null
            +++ b/scripts/migrations/011_add_new_col.sql
            @@ -0,0 +1,2 @@
            +ALTER TABLE dispatch_items ADD COLUMN IF NOT EXISTS new_col TEXT;
        """)

        migrations_dir = tmp_path / "migrations"
        migrations_dir.mkdir()
        (migrations_dir / "011_add_new_col.sql").write_text(
            "ALTER TABLE dispatch_items ADD COLUMN IF NOT EXISTS new_col TEXT;\n"
        )

        result = mod.check_diff(diff, migrations_dir=migrations_dir)

        assert result["exit_code"] == 0
        assert not result.get("missing")


class TestExistingColumnExitsZero:
    """PR references a column already in the base schema → pass."""

    def test_existing_column_exits_zero(self, tmp_path: Path):
        """Referencing 'story_id' — a column established in
        001_dispatch_queue.sql — should not trigger a failure.
        """
        mod = _import_check_module()

        diff = textwrap.dedent("""\
            diff --git a/tech_dev_agents/ops_console/routes/dispatch.py b/tech_dev_agents/ops_console/routes/dispatch.py
            --- a/tech_dev_agents/ops_console/routes/dispatch.py
            +++ b/tech_dev_agents/ops_console/routes/dispatch.py
            @@ -20,0 +21,1 @@
            +    item_story_id = row["story_id"]
        """)

        # Provide real 001 migration content
        migrations_dir = tmp_path / "migrations"
        migrations_dir.mkdir()
        (migrations_dir / "001_dispatch_queue.sql").write_text(
            (REPO_ROOT / "scripts" / "migrations" / "001_dispatch_queue.sql").read_text()
        )

        result = mod.check_diff(diff, migrations_dir=migrations_dir)

        assert result["exit_code"] == 0


class TestOptOutCommentSkipsColumn:
    """migration-ci: ignore comment adjacent to a field → skip it."""

    def test_opt_out_comment_skips_column(self, tmp_path: Path):
        """A column-like reference with `# migration-ci: ignore` on the
        same or preceding line must be excluded from the check.
        """
        mod = _import_check_module()

        diff = textwrap.dedent("""\
            diff --git a/tech_dev_agents/ops_console/models.py b/tech_dev_agents/ops_console/models.py
            --- a/tech_dev_agents/ops_console/models.py
            +++ b/tech_dev_agents/ops_console/models.py
            @@ -10,0 +11,2 @@
            +    # migration-ci: ignore — this is an in-memory field, not a DB column
            +    color: str | None = None
        """)

        migrations_dir = tmp_path / "migrations"
        migrations_dir.mkdir()

        result = mod.check_diff(diff, migrations_dir=migrations_dir)

        assert result["exit_code"] == 0


class TestNonOpsConsoleFileIgnored:
    """Column-like refs in test files or scripts are not flagged."""

    def test_non_ops_console_file_ignored(self, tmp_path: Path):
        """Only files under tech_dev_agents/ops_console/ should be
        scanned. A reference to 'fake_col' in a test file must not
        trigger a failure.
        """
        mod = _import_check_module()

        diff = textwrap.dedent("""\
            diff --git a/tests/test_something.py b/tests/test_something.py
            --- a/tests/test_something.py
            +++ b/tests/test_something.py
            @@ -5,0 +6,1 @@
            +    fake_col: str = "not a real DB column"
        """)

        migrations_dir = tmp_path / "migrations"
        migrations_dir.mkdir()

        result = mod.check_diff(diff, migrations_dir=migrations_dir)

        assert result["exit_code"] == 0


class TestOutputVarianceTwoDiffs:
    """Two meaningfully different inputs produce different results."""

    def test_output_variance_two_diffs(self, tmp_path: Path):
        """A diff with a migration and a diff without a migration must
        produce different exit codes and different missing-column lists.
        """
        mod = _import_check_module()

        migrations_dir = tmp_path / "migrations"
        migrations_dir.mkdir()

        # Diff A: adds column with no migration → should fail
        diff_a = textwrap.dedent("""\
            diff --git a/tech_dev_agents/ops_console/models.py b/tech_dev_agents/ops_console/models.py
            --- a/tech_dev_agents/ops_console/models.py
            +++ b/tech_dev_agents/ops_console/models.py
            @@ -10,0 +11,1 @@
            +    missing_col: str | None = None
        """)

        # Diff B: adds column WITH migration → should pass
        diff_b = textwrap.dedent("""\
            diff --git a/tech_dev_agents/ops_console/models.py b/tech_dev_agents/ops_console/models.py
            --- a/tech_dev_agents/ops_console/models.py
            +++ b/tech_dev_agents/ops_console/models.py
            @@ -10,0 +11,1 @@
            +    missing_col: str | None = None
            diff --git a/scripts/migrations/011_missing_col.sql b/scripts/migrations/011_missing_col.sql
            new file mode 100644
            --- /dev/null
            +++ b/scripts/migrations/011_missing_col.sql
            @@ -0,0 +1 @@
            +ALTER TABLE dispatch_items ADD COLUMN IF NOT EXISTS missing_col TEXT;
        """)

        # Also write the migration file for diff_b
        migrations_dir_b = tmp_path / "migrations_b"
        migrations_dir_b.mkdir()
        (migrations_dir_b / "011_missing_col.sql").write_text(
            "ALTER TABLE dispatch_items ADD COLUMN IF NOT EXISTS missing_col TEXT;\n"
        )

        result_a = mod.check_diff(diff_a, migrations_dir=migrations_dir)
        result_b = mod.check_diff(diff_b, migrations_dir=migrations_dir_b)

        # Assert outputs are different
        assert result_a["exit_code"] != result_b["exit_code"], (
            "Two meaningfully different diffs must produce different exit codes"
        )
        assert result_a.get("missing") != result_b.get("missing"), (
            "Two meaningfully different diffs must produce different missing lists"
        )


# --------------------------------------------------------------------------- #
# Group C: CI Check Script — Schema Fixture / Column Extraction
# --------------------------------------------------------------------------- #


class TestExtractColumnsFromMigrations:
    """Parse CREATE TABLE + ALTER TABLE ADD COLUMN from migration SQL."""

    def test_extract_columns_from_migrations(self, tmp_path: Path):
        """Given migration files with CREATE TABLE and ALTER TABLE ADD
        COLUMN, the extractor must return the full set of column names.
        """
        mod = _import_check_module()

        migrations_dir = tmp_path / "migrations"
        migrations_dir.mkdir()
        (migrations_dir / "001_table.sql").write_text(textwrap.dedent("""\
            CREATE TABLE IF NOT EXISTS items (
                id BIGSERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
            );
        """))
        (migrations_dir / "002_add_col.sql").write_text(
            "ALTER TABLE items ADD COLUMN IF NOT EXISTS email TEXT;\n"
        )

        columns = mod.extract_columns_from_migrations(migrations_dir)

        assert "id" in columns
        assert "name" in columns
        assert "status" in columns
        assert "email" in columns


class TestExtractColumnsHandlesEmptyDir:
    """Empty migrations dir → empty set, no crash."""

    def test_extract_columns_handles_empty_dir(self, tmp_path: Path):
        mod = _import_check_module()

        empty_dir = tmp_path / "empty_migrations"
        empty_dir.mkdir()

        columns = mod.extract_columns_from_migrations(empty_dir)

        assert columns == set()


class TestExtractColumnsIgnoresNonSql:
    """Non-.sql files in the migrations directory are skipped."""

    def test_extract_columns_ignores_non_sql(self, tmp_path: Path):
        mod = _import_check_module()

        migrations_dir = tmp_path / "migrations"
        migrations_dir.mkdir()
        (migrations_dir / "README.md").write_text("# Migrations\n")
        (migrations_dir / "001_table.sql.bak").write_text(
            "CREATE TABLE IF NOT EXISTS items (id BIGSERIAL PRIMARY KEY);\n"
        )
        (migrations_dir / "001_table.sql").write_text(
            "CREATE TABLE IF NOT EXISTS items (id BIGSERIAL PRIMARY KEY, name TEXT);\n"
        )

        columns = mod.extract_columns_from_migrations(migrations_dir)

        # Only the .sql file's columns should be extracted
        assert "name" in columns
        assert "id" in columns


# --------------------------------------------------------------------------- #
# Group D: CI Workflow Structural
# --------------------------------------------------------------------------- #


class TestWorkflowFileExists:
    """The migration-invariant.yml workflow must exist."""

    def test_workflow_file_exists(self):
        assert WORKFLOW_FILE.exists(), (
            f"Expected CI workflow at {WORKFLOW_FILE}. "
            "Phase 8 must create .github/workflows/migration-invariant.yml"
        )


class TestWorkflowTriggersOnCorrectPaths:
    """Workflow must trigger on ops_console code and migration changes."""

    def test_workflow_triggers_on_correct_paths(self):
        assert WORKFLOW_FILE.exists(), (
            f"Workflow file does not exist yet: {WORKFLOW_FILE}"
        )
        content = WORKFLOW_FILE.read_text()

        assert "tech_dev_agents/**" in content, (
            "Workflow must trigger on changes to tech_dev_agents/**"
        )
        assert "scripts/migrations/**" in content, (
            "Workflow must trigger on changes to scripts/migrations/**"
        )
