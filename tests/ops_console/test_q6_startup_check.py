"""Q6 — AC2: startup_check.py fails with [STARTUP-FATAL] when a manifest migration
is missing from the DB.

RED until tech_dev_agents/ops_console/startup_check.py is implemented.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

MANIFEST = {
    "protocol_version": "2.0",
    "min_worker_version": "2.0",
    "migrations": [
        "050_dispatch_v2_schema.sql",
        "051_dispatch_v2_dependencies.sql",
    ],
}

# Sentinel tables expected per migration (mirrors startup_check.py logic)
MIGRATION_TABLES = {
    "050_dispatch_v2_schema.sql": "dispatch_jobs",
    "051_dispatch_v2_dependencies.sql": "dispatch_dependencies",
}


@pytest.fixture()
def mock_pool_all_present():
    """asyncpg pool where all sentinel tables exist."""
    conn = AsyncMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)
    # fetchval returns a truthy row for every sentinel table
    conn.fetchval = AsyncMock(return_value="dispatch_jobs")

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=conn)
    return pool


@pytest.fixture()
def mock_pool_missing_050():
    """asyncpg pool where 050 migration table is absent."""
    conn = AsyncMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)

    async def fetchval_side_effect(query, *args):
        # Return None when asked about dispatch_jobs (from migration 050)
        if "dispatch_jobs" in query or (args and "dispatch_jobs" in str(args)):
            return None
        return "dispatch_dependencies"

    conn.fetchval = AsyncMock(side_effect=fetchval_side_effect)

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=conn)
    return pool


@pytest.fixture()
def mock_pool_missing_051():
    """asyncpg pool where 051 migration table is absent."""
    conn = AsyncMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)

    async def fetchval_side_effect(query, *args):
        if "dispatch_dependencies" in query or (args and "dispatch_dependencies" in str(args)):
            return None
        return "dispatch_jobs"

    conn.fetchval = AsyncMock(side_effect=fetchval_side_effect)

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=conn)
    return pool


def test_startup_check_module_importable() -> None:
    """RED: startup_check module must exist."""
    try:
        import tech_dev_agents.ops_console.startup_check  # noqa: F401
    except ImportError as exc:
        pytest.fail(
            f"[RED] Cannot import startup_check: {exc}. "
            "Implement tech_dev_agents/ops_console/startup_check.py"
        )


@pytest.mark.asyncio
async def test_startup_check_passes_all_present(mock_pool_all_present) -> None:
    """AC2: run_startup_checks returns normally when all migrations are applied."""
    from tech_dev_agents.ops_console.startup_check import run_startup_checks

    # Should not raise
    await run_startup_checks(mock_pool_all_present)


@pytest.mark.asyncio
async def test_startup_check_fails_missing_050(mock_pool_missing_050, caplog) -> None:
    """AC2: run_startup_checks raises SystemExit when migration 050 is missing."""
    from tech_dev_agents.ops_console.startup_check import run_startup_checks

    with caplog.at_level(logging.CRITICAL):
        with pytest.raises(SystemExit) as exc_info:
            await run_startup_checks(mock_pool_missing_050)

    assert exc_info.value.code != 0, "SystemExit code must be non-zero"
    assert any(
        "[STARTUP-FATAL]" in record.message for record in caplog.records
    ), f"Expected [STARTUP-FATAL] in log. Got: {[r.message for r in caplog.records]}"


@pytest.mark.asyncio
async def test_startup_check_fails_missing_051(mock_pool_missing_051, caplog) -> None:
    """AC2: run_startup_checks raises SystemExit when migration 051 is missing."""
    from tech_dev_agents.ops_console.startup_check import run_startup_checks

    with caplog.at_level(logging.CRITICAL):
        with pytest.raises(SystemExit) as exc_info:
            await run_startup_checks(mock_pool_missing_051)

    assert exc_info.value.code != 0
    assert any(
        "[STARTUP-FATAL]" in record.message for record in caplog.records
    )


@pytest.mark.asyncio
async def test_startup_check_exits_nonzero(mock_pool_missing_050) -> None:
    """AC2: SystemExit code is 1 (not 0) on missing migration."""
    from tech_dev_agents.ops_console.startup_check import run_startup_checks

    with pytest.raises(SystemExit) as exc_info:
        await run_startup_checks(mock_pool_missing_050)

    assert exc_info.value.code == 1, (
        f"Expected SystemExit(1), got SystemExit({exc_info.value.code})"
    )


@pytest.mark.asyncio
async def test_startup_check_log_names_missing_migration(mock_pool_missing_051, caplog) -> None:
    """AC2: [STARTUP-FATAL] log message must name the unapplied migration."""
    from tech_dev_agents.ops_console.startup_check import run_startup_checks

    with caplog.at_level(logging.CRITICAL):
        with pytest.raises(SystemExit):
            await run_startup_checks(mock_pool_missing_051)

    fatal_messages = [r.message for r in caplog.records if "[STARTUP-FATAL]" in r.message]
    assert any(
        "051" in msg or "dispatch_v2_dependencies" in msg or "dispatch_dependencies" in msg
        for msg in fatal_messages
    ), f"Fatal log must name the missing migration. Got: {fatal_messages}"


def test_startup_check_reads_manifest_from_package_dir() -> None:
    """AC2: startup_check must ship a manifest alongside it and read it correctly."""
    manifest_path = (
        Path(__file__).parent.parent.parent
        / "tech_dev_agents"
        / "ops_console"
        / "protocol_manifest.json"
    )
    assert manifest_path.exists(), (
        "[RED] protocol_manifest.json not found at "
        f"{manifest_path} — implement Q6 manifest file"
    )
    data = json.loads(manifest_path.read_text())
    assert "migrations" in data
    assert "050_dispatch_v2_schema.sql" in data["migrations"]
    assert "051_dispatch_v2_dependencies.sql" in data["migrations"]
    assert data.get("protocol_version") == "2.0"
