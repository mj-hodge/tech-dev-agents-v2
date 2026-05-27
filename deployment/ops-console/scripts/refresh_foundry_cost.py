#!/usr/bin/env python3
"""Refresh fleet-wide Foundry cost cache in Postgres (STORY-576).

Queries the Azure Cost Management API for the last 8 days of daily cost
grouped by ResourceId, classifies each deployment into opus/sonnet/haiku/other,
and UPSERTs the aggregated rows into the ``foundry_cost_daily`` table.

Schedule: ``0 */2 * * *`` (every 2 hours via cron).

Environment variables:
    DATABASE_URL                          — Postgres connection string
    OPS_AZURE_SUBSCRIPTION_ID             — Azure subscription ID
    OPS_AZURE_MANAGED_IDENTITY_CLIENT_ID  — (optional) managed identity client ID
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

import asyncpg
import httpx

# Add project root to path so we can import the service module
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from tech_dev_agents.ops_console.services.foundry_cost_service import (
    FoundryCostService,
    classify_resource_id,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("refresh_foundry_cost")


def _get_azure_token(subscription_id: str) -> str:
    """Acquire a bearer token via DefaultAzureCredential.

    This is a synchronous call — wrapped in asyncio.to_thread by the caller.
    """
    from azure.identity import DefaultAzureCredential

    kwargs: dict[str, str] = {}
    client_id = os.environ.get("OPS_AZURE_MANAGED_IDENTITY_CLIENT_ID")
    if client_id:
        kwargs["managed_identity_client_id"] = client_id

    credential = DefaultAzureCredential(**kwargs)
    token = credential.get_token("https://management.azure.com/.default")
    return token.token


async def _query_cost_management(
    http_client: httpx.AsyncClient,
    token: str,
    subscription_id: str,
    start_date: str,
    end_date: str,
) -> list[list]:
    """POST to Azure Cost Management Query API and return raw rows.

    Each row is [cost, YYYYMMDD, resourceId, currency].
    """
    url = (
        f"https://management.azure.com/subscriptions/{subscription_id}"
        "/providers/Microsoft.CostManagement/query"
        "?api-version=2023-11-01"
    )
    body = {
        "type": "ActualCost",
        "timeframe": "Custom",
        "timePeriod": {"from": start_date, "to": end_date},
        "dataset": {
            "granularity": "Daily",
            "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}},
            "grouping": [{"type": "Dimension", "name": "ResourceId"}],
            # 2026-05-01: Foundry billing flows through Azure Marketplace SaaS,
            # not microsoft.machinelearningservices/workspaces. Verified via
            # Cost Management Query API: 7-day Foundry spend is $702 under
            # microsoft.saas; $0 under machinelearningservices. The earlier
            # filter silently dropped 100% of Foundry rows → dashboard $0.
            # See docs/azure-foundry-billing.md.
            "filter": {
                "dimensions": {
                    "name": "MeterCategory",
                    "operator": "In",
                    "values": ["SaaS"],
                },
            },
        },
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    resp = await http_client.post(url, json=body, headers=headers, timeout=60.0)
    if resp.status_code != 200:
        logger.error("Cost Management API returned %d: %s", resp.status_code, resp.text[:500])
        raise RuntimeError(f"Cost Management API error: {resp.status_code}")

    data = resp.json()
    return data.get("properties", {}).get("rows", [])


def _aggregate_by_date(
    rows: list[list],
) -> list[dict]:
    """Aggregate raw Cost Management rows into per-date model buckets.

    Input rows: [cost, YYYYMMDD, resourceId, currency]
    Output: list of dicts with keys: usage_date, opus_usd, sonnet_usd, haiku_usd, other_usd
    """
    buckets: dict[date, dict[str, float]] = defaultdict(
        lambda: {"opus": 0.0, "sonnet": 0.0, "haiku": 0.0, "other": 0.0}
    )

    for row in rows:
        if len(row) < 3:
            continue
        cost = float(row[0])
        date_num = str(row[1])  # YYYYMMDD
        resource_id = str(row[2])

        try:
            usage_date = date(int(date_num[:4]), int(date_num[4:6]), int(date_num[6:8]))
        except (ValueError, IndexError):
            logger.warning("Skipping row with invalid date: %s", date_num)
            continue

        model = classify_resource_id(resource_id)
        buckets[usage_date][model] += cost

    result = []
    for usage_date in sorted(buckets.keys()):
        b = buckets[usage_date]
        result.append({
            "usage_date": usage_date,
            "opus_usd": round(b["opus"], 2),
            "sonnet_usd": round(b["sonnet"], 2),
            "haiku_usd": round(b["haiku"], 2),
            "other_usd": round(b["other"], 2),
        })
    return result


async def main() -> None:
    """Main refresh routine."""
    subscription_id = os.environ.get("OPS_AZURE_SUBSCRIPTION_ID")
    database_url = os.environ.get("DATABASE_URL")

    if not subscription_id:
        logger.error("OPS_AZURE_SUBSCRIPTION_ID is not set")
        sys.exit(1)
    if not database_url:
        logger.error("DATABASE_URL is not set")
        sys.exit(1)

    logger.info("Starting Foundry cost refresh")

    # 1. Acquire Azure token (sync call wrapped in thread)
    try:
        token = await asyncio.to_thread(_get_azure_token, subscription_id)
    except Exception:
        logger.exception("Failed to acquire Azure credential")
        sys.exit(1)

    # 2. Query Cost Management API (last 8 days to catch late-arriving rows)
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=8)).strftime("%Y-%m-%dT00:00:00Z")
    end = now.strftime("%Y-%m-%dT23:59:59Z")

    async with httpx.AsyncClient() as http_client:
        try:
            raw_rows = await _query_cost_management(
                http_client, token, subscription_id, start, end
            )
        except Exception:
            logger.exception("Failed to query Cost Management API")
            sys.exit(1)

    logger.info("Received %d raw cost rows from Azure", len(raw_rows))

    # 3. Classify and aggregate by date
    aggregated = _aggregate_by_date(raw_rows)
    logger.info("Aggregated into %d daily rows", len(aggregated))

    if not aggregated:
        logger.warning("No cost data to upsert — exiting")
        return

    # 4. Connect to Postgres and UPSERT
    try:
        conn = await asyncpg.connect(database_url)
    except Exception:
        logger.exception("Failed to connect to Postgres")
        sys.exit(1)

    try:
        count = await FoundryCostService.upsert_daily_costs(conn, aggregated)
        total_usd = sum(
            r["opus_usd"] + r["sonnet_usd"] + r["haiku_usd"] + r["other_usd"]
            for r in aggregated
        )
        logger.info(
            "Refresh complete: %d rows upserted, $%.2f total across %d days",
            count, total_usd, len(aggregated),
        )
    except Exception:
        logger.exception("Failed to upsert cost data")
        sys.exit(1)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
