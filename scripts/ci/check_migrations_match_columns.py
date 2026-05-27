#!/usr/bin/env python3
"""STORY-645 — CI migration invariant: detect column references without migrations.

Scans a git diff for new column-like references in ``tech_dev_agents/ops_console/``
Python files and verifies that each referenced column either:

1. Already exists in a migration file under ``scripts/migrations/``, or
2. Is added by a new migration file in the same PR diff.

Exit 0 if all references are substantiated. Exit 1 if any are missing.

Usage (CLI)::

    python3 scripts/ci/check_migrations_match_columns.py <base_ref> <head_ref>

The script can also be imported and called programmatically via
``check_diff()`` and ``extract_columns_from_migrations()``.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Column extraction from migration SQL files
# ---------------------------------------------------------------------------

# Matches column definitions inside CREATE TABLE (...)
_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?\w+\s*\((.+?)\);",
    re.IGNORECASE | re.DOTALL,
)

# Matches ALTER TABLE ... ADD COLUMN [IF NOT EXISTS] <col_name>
_ALTER_ADD_COL_RE = re.compile(
    r"ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)",
    re.IGNORECASE,
)

# Tokens that are SQL keywords, not column names, inside CREATE TABLE bodies
_SQL_KEYWORDS = frozenset({
    "primary", "key", "not", "null", "default", "check", "in",
    "unique", "index", "constraint", "references", "on", "delete",
    "cascade", "set", "true", "false", "create", "table", "if",
    "exists", "where", "and", "or", "serial", "bigserial", "text",
    "integer", "boolean", "timestamptz", "timestamp", "varchar",
    "bigint", "smallint", "json", "jsonb", "float", "double",
    "precision", "numeric", "date", "time", "interval", "now",
})


def extract_columns_from_migrations(migrations_dir: Path) -> set[str]:
    """Parse all ``*.sql`` files in *migrations_dir* and return the set of
    column names defined via ``CREATE TABLE`` or ``ALTER TABLE ADD COLUMN``.
    """
    columns: set[str] = set()

    if not migrations_dir.is_dir():
        return columns

    for sql_file in sorted(migrations_dir.iterdir()):
        if sql_file.suffix != ".sql":
            continue

        content = sql_file.read_text()

        # --- CREATE TABLE columns ---
        for m in _CREATE_TABLE_RE.finditer(content):
            body = m.group(1)
            # Split on commas first (handles single-line CREATE TABLE),
            # then on newlines for multi-line definitions.
            fragments: list[str] = []
            for part in body.split(","):
                for subline in part.split("\n"):
                    subline = subline.strip()
                    if subline:
                        fragments.append(subline)

            for frag in fragments:
                # Skip CHECK / CONSTRAINT / pure-keyword lines
                first_token = frag.split()[0].lower() if frag.split() else ""
                if first_token in _SQL_KEYWORDS or first_token.startswith("--"):
                    continue
                # The first token on a column-definition line is the column name
                col_name = re.match(r"(\w+)", frag)
                if col_name:
                    name = col_name.group(1).lower()
                    if name not in _SQL_KEYWORDS:
                        columns.add(name)

        # --- ALTER TABLE ADD COLUMN ---
        for m in _ALTER_ADD_COL_RE.finditer(content):
            columns.add(m.group(1).lower())

    return columns


# ---------------------------------------------------------------------------
# Diff parsing — detect column-like references in ops_console Python files
# ---------------------------------------------------------------------------

# Matches Pydantic-style field declarations: ``name: type``
_FIELD_DECL_RE = re.compile(r"^\+\s{4}(\w+)\s*:\s*(?:str|int|float|bool|datetime|date|Optional)", re.MULTILINE)

# Matches row["col"] or row.get("col") or row['col']
_ROW_ACCESS_RE = re.compile(r"""row\s*(?:\[|\.get\s*\()\s*["'](\w+)["']""")

# Matches Field("col_name", ...) — SQLAlchemy / Pydantic Field
_FIELD_CALL_RE = re.compile(r"""Field\s*\(\s*["'](\w+)["']""")

_MIGRATION_CI_IGNORE = re.compile(r"#\s*migration-ci:\s*ignore", re.IGNORECASE)

# Only scan files under tech_dev_agents/ops_console/
_OPS_CONSOLE_PATH_RE = re.compile(r"^diff --git a/(tech_dev_agents/ops_console/\S+\.py)")


def _extract_columns_from_diff_migrations(diff_text: str) -> set[str]:
    """Extract column names from migration files that appear as new in the diff."""
    columns: set[str] = set()

    in_migration = False
    for line in diff_text.splitlines():
        if line.startswith("diff --git"):
            in_migration = bool(re.search(r"scripts/migrations/\S+\.sql", line))
            continue
        if in_migration and line.startswith("+") and not line.startswith("+++"):
            for m in _ALTER_ADD_COL_RE.finditer(line):
                columns.add(m.group(1).lower())
            # Also catch CREATE TABLE columns in new migration files
            # (handled line-by-line — simplified but sufficient for the heuristic)
            col_match = re.match(r"\+\s{4,}(\w+)\s+\w+", line)
            if col_match:
                name = col_match.group(1).lower()
                if name not in _SQL_KEYWORDS:
                    columns.add(name)

    return columns


def _extract_referenced_columns(diff_text: str) -> set[str]:
    """Find column-like references in added lines of ops_console Python files."""
    referenced: set[str] = set()

    in_ops_console = False
    prev_line_ignored = False

    for line in diff_text.splitlines():
        if line.startswith("diff --git"):
            in_ops_console = bool(_OPS_CONSOLE_PATH_RE.match(line))
            prev_line_ignored = False
            continue

        if not in_ops_console:
            continue

        if not line.startswith("+") or line.startswith("+++"):
            # Track ignore comments on context lines too
            if _MIGRATION_CI_IGNORE.search(line):
                prev_line_ignored = True
            else:
                prev_line_ignored = False
            continue

        # Check for ignore comment on this line or preceding line
        if _MIGRATION_CI_IGNORE.search(line):
            prev_line_ignored = True
            continue

        if prev_line_ignored:
            prev_line_ignored = False
            continue

        # Extract column-like references from added lines
        for m in _FIELD_DECL_RE.finditer(line):
            referenced.add(m.group(1).lower())

        for m in _ROW_ACCESS_RE.finditer(line):
            referenced.add(m.group(1).lower())

        for m in _FIELD_CALL_RE.finditer(line):
            referenced.add(m.group(1).lower())

        prev_line_ignored = False

    return referenced


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def check_diff(
    diff_text: str,
    *,
    migrations_dir: Path | None = None,
) -> dict[str, Any]:
    """Check a unified diff for unsubstantiated column references.

    Returns a dict with:
    - ``exit_code``: 0 (pass) or 1 (fail)
    - ``missing``: list of column names without a migration
    - ``referenced``: set of all detected column references
    - ``known``: set of columns found in existing + diff migrations
    """
    if migrations_dir is None:
        migrations_dir = Path("scripts/migrations")

    # Columns already defined in existing migrations on disk
    known_columns = extract_columns_from_migrations(migrations_dir)

    # Columns added by new migration files in the PR diff
    diff_migration_columns = _extract_columns_from_diff_migrations(diff_text)
    known_columns |= diff_migration_columns

    # Columns referenced in ops_console Python code in the diff
    referenced = _extract_referenced_columns(diff_text)

    # Find unsubstantiated references
    missing = sorted(referenced - known_columns)

    return {
        "exit_code": 1 if missing else 0,
        "missing": missing,
        "referenced": referenced,
        "known": known_columns,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> int:
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <base_ref> <head_ref>", file=sys.stderr)
        return 2

    base_ref = sys.argv[1]
    head_ref = sys.argv[2]

    # Get the diff between base and head
    result = subprocess.run(
        ["git", "diff", f"{base_ref}...{head_ref}"],
        capture_output=True,
        text=True,
        check=True,
    )

    diff_text = result.stdout
    repo_root = Path(
        subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    )

    check_result = check_diff(
        diff_text,
        migrations_dir=repo_root / "scripts" / "migrations",
    )

    if check_result["exit_code"] == 0:
        print("Migration invariant check PASSED.")
        if check_result["referenced"]:
            print(f"  Columns referenced: {', '.join(sorted(check_result['referenced']))}")
        return 0

    print("Migration invariant check FAILED.", file=sys.stderr)
    print(f"  Missing migrations for columns: {', '.join(check_result['missing'])}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
