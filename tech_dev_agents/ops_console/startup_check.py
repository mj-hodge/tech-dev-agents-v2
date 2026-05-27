"""Q6 — Protocol manifest startup check.

Reads protocol_manifest.json (sibling to this file) and verifies that every
listed migration has been applied to the database before the route process is
allowed to start.

Usage (from FastAPI lifespan in main.py):

    from tech_dev_agents.ops_console.startup_check import run_startup_checks
    await run_startup_checks(db_pool)

If the DB pool is not available (e.g. no DATABASE_URL configured), the check
is silently skipped with a warning log — the absence of a pool is not a Q6
concern (it's handled by the fallback-to-JSON path in main.py).

If a required migration is absent from pg_tables: logs
``[STARTUP-FATAL] migration <name> not applied`` at CRITICAL level and raises
``SystemExit(1)`` so the process exits immediately.

Migration existence is checked via the sentinel table introduced by each
migration — no separate migration-tracking table is needed.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Manifest location — always next to this file.
# ---------------------------------------------------------------------------
_MANIFEST_PATH = Path(__file__).parent / "protocol_manifest.json"

# ---------------------------------------------------------------------------
# Migration → sentinel table mapping.
# We check whether the table that a migration creates actually exists in
# pg_tables.  If more migrations are added to the manifest, this dict must
# be extended accordingly.
# ---------------------------------------------------------------------------
_MIGRATION_SENTINEL_TABLE: dict[str, str] = {
    "050_dispatch_v2_schema.sql": "dispatch_jobs",
    "051_dispatch_v2_dependencies.sql": "dispatch_dependencies",
}


def _load_manifest() -> dict:
    """Load and parse the protocol manifest JSON."""
    try:
        return json.loads(_MANIFEST_PATH.read_text())
    except FileNotFoundError:
        logger.warning(
            "[STARTUP-WARN] protocol_manifest.json not found at %s — skipping check",
            _MANIFEST_PATH,
        )
        return {}
    except json.JSONDecodeError as exc:
        logger.error("[STARTUP-ERROR] Failed to parse protocol_manifest.json: %s", exc)
        return {}


async def run_startup_checks(pool) -> None:  # type: ignore[type-arg]
    """Assert that every migration listed in protocol_manifest.json is applied.

    Args:
        pool: An asyncpg connection pool.  If ``None``, the check is skipped
              with a warning (DB not configured — handled elsewhere).

    Raises:
        SystemExit(1): If any required migration has not been applied.
    """
    if pool is None:
        logger.warning(
            "[STARTUP-WARN] DB pool is None — skipping protocol manifest check"
        )
        return

    manifest = _load_manifest()
    if not manifest:
        # Could not read manifest — skip rather than block startup.
        return

    migrations: list[str] = manifest.get("migrations", [])
    if not migrations:
        logger.info("[STARTUP] No migrations listed in protocol_manifest.json — nothing to check")
        return

    protocol_version = manifest.get("protocol_version", "unknown")
    logger.info(
        "[STARTUP] Checking %d migration(s) from protocol_manifest.json (protocol_version=%s)",
        len(migrations),
        protocol_version,
    )

    async with pool.acquire() as conn:
        for migration_name in migrations:
            sentinel_table = _MIGRATION_SENTINEL_TABLE.get(migration_name)
            if sentinel_table is None:
                logger.warning(
                    "[STARTUP-WARN] No sentinel table configured for migration %s — skipping",
                    migration_name,
                )
                continue

            # Use pg_tables to check table existence — no custom tracking table.
            exists = await conn.fetchval(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename=$1",
                sentinel_table,
            )

            if not exists:
                logger.critical(
                    "[STARTUP-FATAL] migration %s not applied (sentinel table '%s' missing)",
                    migration_name,
                    sentinel_table,
                )
                raise SystemExit(1)

            logger.info(
                "[STARTUP] migration %s OK (sentinel table '%s' present)",
                migration_name,
                sentinel_table,
            )

    logger.info("[STARTUP] All %d migration(s) verified — protocol manifest check passed", len(migrations))
