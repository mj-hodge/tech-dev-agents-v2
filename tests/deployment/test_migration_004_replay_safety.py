"""Regression tests for 004_paused_status replay safety.

If deploy replays migrations on a DB that already has newer statuses
(`in_review`, `needs_info`), migration 004 must not fail when re-adding the
dispatch status CHECK constraint.
"""

from __future__ import annotations

from pathlib import Path
import re


MIGRATION_004 = Path("scripts/migrations/004_paused_status.sql")


def test_004_status_check_includes_future_statuses() -> None:
    """004 must allow statuses introduced by later migrations.

    Without this, re-running 004 on a live DB containing rows in `in_review`
    or `needs_info` fails with:
      check constraint "dispatch_items_status_check" ... is violated by some row
    """
    sql = MIGRATION_004.read_text(encoding="utf-8")

    check_expr = re.search(
        r"CHECK\s*\(\s*status\s+IN\s*\(([^)]+)\)\s*\)",
        sql,
        re.IGNORECASE | re.MULTILINE,
    )
    assert check_expr is not None, "004 must define dispatch_items_status_check"
    values = check_expr.group(1)

    assert "'paused'" in values, "004 must include paused status"
    assert "'in_review'" in values, (
        "004 replay must tolerate in_review rows introduced by later migrations"
    )
    assert "'needs_info'" in values, (
        "004 replay must tolerate needs_info rows introduced by later migrations"
    )
