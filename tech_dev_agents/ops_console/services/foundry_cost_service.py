"""Fleet-wide Foundry cost by model deployment (STORY-576).

Reads cached daily cost data from Postgres (written by the 2h cron script).
Classifies Azure ResourceId strings into model buckets: opus/sonnet/haiku/other.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone
from typing import Any

import asyncpg
from pydantic import BaseModel

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Response Models
# ---------------------------------------------------------------------------


class DailyFoundryCostByModel(BaseModel):
    """Single day's Foundry cost split by model."""

    date: str  # YYYY-MM-DD
    opus_usd: float
    sonnet_usd: float
    haiku_usd: float
    other_usd: float
    total_usd: float  # computed: opus + sonnet + haiku + other


class FoundryCostResponse(BaseModel):
    """Response for GET /api/fleet/foundry-cost."""

    daily: list[DailyFoundryCostByModel]
    fetched_at: str | None  # ISO 8601 timestamp of last refresh
    cache_age_seconds: int  # seconds since last refresh


# ---------------------------------------------------------------------------
# Model Classification
# ---------------------------------------------------------------------------

# Patterns match Azure ResourceId segments like:
#   /providers/Microsoft.MachineLearningServices/.../deployments/claude-opus-4-6-...
#   /providers/Microsoft.MachineLearningServices/.../deployments/claude-sonnet-4-5-...
#   /providers/Microsoft.MachineLearningServices/.../deployments/claude-haiku-3-5-...

_MODEL_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"claude-opus", re.IGNORECASE), "opus"),
    (re.compile(r"claude-sonnet", re.IGNORECASE), "sonnet"),
    (re.compile(r"claude-haiku", re.IGNORECASE), "haiku"),
]


def classify_resource_id(resource_id: str) -> str:
    """Classify an Azure ResourceId into a model bucket.

    Returns one of: "opus", "sonnet", "haiku", "other".
    """
    for pattern, bucket in _MODEL_PATTERNS:
        if pattern.search(resource_id):
            return bucket
    return "other"


# ---------------------------------------------------------------------------
# Service Class
# ---------------------------------------------------------------------------


class FoundryCostService:
    """Read fleet-wide Foundry cost from Postgres cache.

    Write path (upsert_daily_costs) is called by the standalone cron script.
    Read path (get_daily_by_model) is called by the API route handler.
    """

    def __init__(self, db_pool: asyncpg.Pool):
        self._pool = db_pool

    async def get_daily_by_model(self, days: int = 7) -> FoundryCostResponse:
        """Read cached daily cost from Postgres.

        Returns up to ``days`` rows ordered by date ascending.
        """
        now = datetime.now(timezone.utc)

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT usage_date, opus_usd, sonnet_usd, haiku_usd, other_usd, fetched_at
                FROM foundry_cost_daily
                WHERE usage_date >= CURRENT_DATE - $1::int
                ORDER BY usage_date ASC
                """,
                days,
            )

        if not rows:
            return FoundryCostResponse(
                daily=[],
                fetched_at=None,
                cache_age_seconds=0,
            )

        latest_fetched = max(r["fetched_at"] for r in rows)
        cache_age = int((now - latest_fetched).total_seconds())

        daily = [
            DailyFoundryCostByModel(
                date=r["usage_date"].isoformat(),
                opus_usd=float(r["opus_usd"]),
                sonnet_usd=float(r["sonnet_usd"]),
                haiku_usd=float(r["haiku_usd"]),
                other_usd=float(r["other_usd"]),
                total_usd=float(
                    r["opus_usd"] + r["sonnet_usd"] + r["haiku_usd"] + r["other_usd"]
                ),
            )
            for r in rows
        ]

        return FoundryCostResponse(
            daily=daily,
            fetched_at=latest_fetched.isoformat(),
            cache_age_seconds=max(0, cache_age),
        )

    @staticmethod
    async def upsert_daily_costs(
        conn: asyncpg.Connection,
        rows: list[dict[str, Any]],
    ) -> int:
        """UPSERT classified cost rows into foundry_cost_daily.

        Each dict in ``rows`` must have keys:
          usage_date (date), opus_usd (float), sonnet_usd, haiku_usd, other_usd

        Returns the number of rows upserted.
        """
        if not rows:
            return 0

        count = 0
        for row in rows:
            await conn.execute(
                """
                INSERT INTO foundry_cost_daily
                    (usage_date, opus_usd, sonnet_usd, haiku_usd, other_usd, fetched_at)
                VALUES ($1, $2, $3, $4, $5, now())
                ON CONFLICT (usage_date) DO UPDATE SET
                    opus_usd   = EXCLUDED.opus_usd,
                    sonnet_usd = EXCLUDED.sonnet_usd,
                    haiku_usd  = EXCLUDED.haiku_usd,
                    other_usd  = EXCLUDED.other_usd,
                    fetched_at = EXCLUDED.fetched_at
                """,
                row["usage_date"],
                row["opus_usd"],
                row["sonnet_usd"],
                row["haiku_usd"],
                row["other_usd"],
            )
            count += 1
        return count
