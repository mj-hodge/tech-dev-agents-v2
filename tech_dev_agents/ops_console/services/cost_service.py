"""Cost service — combine SDK costs (Loki) and Azure Foundry costs."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from tech_dev_agents.ops_console.cache import TTLCache

logger = logging.getLogger(__name__)
from tech_dev_agents.ops_console.models.responses import (
    CostBreakdownResponse,
    CostToday,
    DailyCost,
    DataFreshness,
)
from tech_dev_agents.ops_console.services.azure_cost_client import AzureCostClient
from tech_dev_agents.ops_console.services.loki_client import LokiClient


class CostService:
    """Combine SDK costs (from Loki) and Azure Foundry costs into unified views."""

    def __init__(
        self,
        loki: LokiClient,
        azure: AzureCostClient | None = None,
        cost_cache_ttl: int = 300,
        fleet_cache_ttl: int = 60,
    ):
        self._loki = loki
        self._azure = azure
        self._cache = TTLCache(cost_cache_ttl)
        self._fleet_cache = TTLCache(fleet_cache_ttl)

    async def get_today_cost(self, agent_name: str) -> CostToday:
        """Get today's cost for a single agent.

        SDK cost from Loki [COST_SUMMARY] or [DONE] lines.
        Azure cost from Azure Cost API (may be stale).
        Caches for 5min.
        """
        cached = self._cache.get(f"today:{agent_name}")
        if cached is not None:
            return cached

        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        today_end = now.isoformat()

        # SDK costs from Loki
        sdk_cost = 0.0
        sdk_sessions = 0
        sdk_turns = 0
        try:
            summaries = await self._loki.query_cost_summaries(
                agent_name, today_start, today_end
            )
            if summaries:
                for s in summaries:
                    sdk_cost += s.get("cost", 0.0)
                    sdk_sessions += s.get("sessions", 0)
                    sdk_turns += s.get("turns", 0)
            else:
                done_lines = await self._loki.query_done_lines(
                    agent_name, today_start, today_end
                )
                for d in done_lines:
                    sdk_cost += d.get("cost", 0.0)
                    sdk_turns += d.get("turns", 0)
                    sdk_sessions += 1
        except Exception:
            logger.warning("Failed to fetch SDK costs for agent %s", agent_name, exc_info=True)

        # Azure costs — split into Foundry vs OpenAI (STORY-038)
        azure_cost = 0.0
        foundry_cost = 0.0
        openai_cost = 0.0
        # STORY-736: Replace silent-zero fallback with explicit cost_status
        foundry_cost_status: str = "unavailable"
        if self._azure:
            try:
                today_str = now.strftime("%Y-%m-%d")
                azure_daily = await self._azure.get_agent_daily_costs(
                    agent_name, today_str, today_str
                )
                azure_cost = sum(d.azure_cost_usd for d in azure_daily)
                foundry_cost = sum(d.foundry_cost_usd for d in azure_daily)
                openai_cost = sum(d.openai_cost_usd for d in azure_daily)
                # Distinguish "no rows" from "real cost returned"
                if azure_daily and foundry_cost > 0:
                    foundry_cost_status = "ok"
                elif now.hour >= 8 and (not azure_daily or foundry_cost == 0):
                    # STORY-736 SC-7: Azure Cost Management has 24-48h reporting
                    # lag. If it's past 08:00 UTC and today still shows $0, the
                    # data is likely stale rather than genuinely zero.
                    foundry_cost_status = "stale"
                else:
                    foundry_cost_status = "no_usage"
            except Exception:
                logger.warning("Failed to fetch Azure costs for agent %s", agent_name, exc_info=True)
                foundry_cost_status = "unavailable"

        result = CostToday(
            sdk_cost_usd=round(sdk_cost, 2),
            azure_cost_usd=round(azure_cost, 2),
            foundry_cost_usd=round(foundry_cost, 2),
            openai_cost_usd=round(openai_cost, 2),
            total_cost_usd=round(sdk_cost + azure_cost, 2),
            sdk_sessions=sdk_sessions,
            sdk_turns=sdk_turns,
            foundry_cost_status=foundry_cost_status,
        )
        self._cache.set(f"today:{agent_name}", result)
        return result

    async def get_cost_breakdown(
        self,
        agent_name: str,
        days: int = 7,
        granularity: str = "daily",
    ) -> CostBreakdownResponse:
        """Get historical cost breakdown.

        SDK from Loki, Azure from Cost API. Caches for 15min.
        """
        cache_key = f"breakdown:{agent_name}:{days}:{granularity}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        now = datetime.now(timezone.utc)
        period_end = now.strftime("%Y-%m-%d")
        period_start_dt = now - timedelta(days=days)
        period_start = period_start_dt.strftime("%Y-%m-%d")

        # SDK costs from Loki
        sdk_by_date: dict[str, dict[str, Any]] = {}
        try:
            summaries = await self._loki.query_cost_summaries(
                agent_name,
                period_start_dt.isoformat(),
                now.isoformat(),
            )
            for s in summaries:
                date = s.get("date", "unknown")
                if date not in sdk_by_date:
                    sdk_by_date[date] = {"cost": 0.0, "sessions": 0, "turns": 0}
                sdk_by_date[date]["cost"] += s.get("cost", 0.0)
                sdk_by_date[date]["sessions"] += s.get("sessions", 0)
                sdk_by_date[date]["turns"] += s.get("turns", 0)
            if not summaries:
                done_lines = await self._loki.query_done_lines(
                    agent_name,
                    period_start_dt.isoformat(),
                    now.isoformat(),
                )
                for d in done_lines:
                    ts_ns = d.get("timestamp_ns", "0")
                    try:
                        date_str = datetime.fromtimestamp(
                            int(ts_ns) / 1e9, tz=timezone.utc
                        ).strftime("%Y-%m-%d")
                    except (ValueError, OSError):
                        date_str = now.strftime("%Y-%m-%d")
                    if date_str not in sdk_by_date:
                        sdk_by_date[date_str] = {"cost": 0.0, "sessions": 0, "turns": 0}
                    sdk_by_date[date_str]["cost"] += d.get("cost", 0.0)
                    sdk_by_date[date_str]["turns"] += d.get("turns", 0)
                    sdk_by_date[date_str]["sessions"] += 1
        except Exception:
            logger.warning("Failed to fetch SDK cost breakdown for agent %s", agent_name, exc_info=True)

        # Azure costs (split into Foundry vs OpenAI)
        azure_by_date: dict[str, float] = {}
        foundry_by_date: dict[str, float] = {}
        openai_by_date: dict[str, float] = {}
        azure_as_of: str | None = None
        if self._azure:
            try:
                azure_daily = await self._azure.get_agent_daily_costs(
                    agent_name, period_start, period_end
                )
                for d in azure_daily:
                    azure_by_date[d.date] = azure_by_date.get(d.date, 0.0) + d.azure_cost_usd
                    foundry_by_date[d.date] = foundry_by_date.get(d.date, 0.0) + d.foundry_cost_usd
                    openai_by_date[d.date] = openai_by_date.get(d.date, 0.0) + d.openai_cost_usd
                azure_as_of = now.isoformat()
            except Exception:
                logger.warning("Failed to fetch Azure cost breakdown for agent %s", agent_name, exc_info=True)

        # Build daily entries
        daily_entries: list[DailyCost] = []
        for i in range(days):
            date_dt = period_start_dt + timedelta(days=i)
            date_str = date_dt.strftime("%Y-%m-%d")
            sdk_data = sdk_by_date.get(date_str, {"cost": 0.0, "sessions": 0, "turns": 0})
            azure_amt = azure_by_date.get(date_str, 0.0)
            foundry_amt = foundry_by_date.get(date_str, 0.0)
            openai_amt = openai_by_date.get(date_str, 0.0)
            sdk_amt = sdk_data["cost"]
            daily_entries.append(
                DailyCost(
                    date=date_str,
                    sdk_cost_usd=round(sdk_amt, 2),
                    azure_cost_usd=round(azure_amt, 2),
                    foundry_cost_usd=round(foundry_amt, 2),
                    openai_cost_usd=round(openai_amt, 2),
                    total_cost_usd=round(sdk_amt + azure_amt, 2),
                    sdk_sessions=sdk_data["sessions"],
                    sdk_turns=sdk_data["turns"],
                )
            )

        # Weekly aggregation if requested
        if granularity == "weekly":
            daily_entries = self._aggregate_weekly(daily_entries)

        total_sdk = sum(d.sdk_cost_usd for d in daily_entries)
        total_azure = sum(d.azure_cost_usd for d in daily_entries)

        result = CostBreakdownResponse(
            agent_name=agent_name,
            period_start=period_start,
            period_end=period_end,
            granularity=granularity,
            total_sdk_cost_usd=round(total_sdk, 2),
            total_azure_cost_usd=round(total_azure, 2),
            total_cost_usd=round(total_sdk + total_azure, 2),
            daily=daily_entries,
            data_freshness=DataFreshness(
                sdk_as_of=now.isoformat(),
                azure_as_of=azure_as_of,
                azure_is_estimated=self._azure is not None,
            ),
        )
        self._cache.set(cache_key, result)
        return result

    async def get_fleet_daily_spend(self, agent_names: list[str]) -> float:
        """Sum today's Azure Foundry cost across all agents.

        Returns Azure-only spend (the real operational cost).  SDK costs
        (covered by Team subscription) are excluded from the headline number.
        Caches using fleet_cache_ttl.
        """
        cached = self._fleet_cache.get("fleet_spend")
        if cached is not None:
            return cached

        async def _safe_cost(name: str) -> float:
            try:
                cost = await self.get_today_cost(name)
                return cost.azure_cost_usd
            except Exception:
                logger.warning("Failed to fetch cost for agent %s in fleet spend", name, exc_info=True)
                return 0.0

        logger.debug("Fetching fleet daily spend for %d agents", len(agent_names))
        costs = await asyncio.gather(*[_safe_cost(n) for n in agent_names])
        total = round(sum(costs), 2)
        self._fleet_cache.set("fleet_spend", total)
        return total

    async def get_fleet_monthly_spend(self, agent_names: list[str]) -> float:
        """Sum last 30 days Azure Foundry cost across all agents.

        Returns Azure-only spend (the real operational cost).  SDK costs
        (covered by Team subscription) are excluded from the headline number.
        Caches using fleet_cache_ttl.
        """
        cached = self._fleet_cache.get("fleet_monthly_spend")
        if cached is not None:
            return cached

        async def _safe_breakdown(name: str) -> float:
            try:
                breakdown = await self.get_cost_breakdown(name, days=30)
                return sum(d.azure_cost_usd for d in breakdown.daily)
            except Exception:
                logger.warning("Failed to fetch 30d cost for agent %s", name, exc_info=True)
                return 0.0

        logger.debug("Fetching fleet monthly spend for %d agents", len(agent_names))
        costs = await asyncio.gather(*[_safe_breakdown(n) for n in agent_names])
        total = round(sum(costs), 2)
        self._fleet_cache.set("fleet_monthly_spend", total)
        return total

    @staticmethod
    def _aggregate_weekly(daily: list[DailyCost]) -> list[DailyCost]:
        """Aggregate daily entries into weekly buckets."""
        if not daily:
            return []

        weeks: list[DailyCost] = []
        chunk_size = 7
        for i in range(0, len(daily), chunk_size):
            chunk = daily[i : i + chunk_size]
            weeks.append(
                DailyCost(
                    date=chunk[0].date,
                    sdk_cost_usd=round(sum(d.sdk_cost_usd for d in chunk), 2),
                    azure_cost_usd=round(sum(d.azure_cost_usd for d in chunk), 2),
                    foundry_cost_usd=round(sum(d.foundry_cost_usd for d in chunk), 2),
                    openai_cost_usd=round(sum(d.openai_cost_usd for d in chunk), 2),
                    total_cost_usd=round(sum(d.total_cost_usd for d in chunk), 2),
                    sdk_sessions=sum(d.sdk_sessions for d in chunk),
                    sdk_turns=sum(d.sdk_turns for d in chunk),
                )
            )
        return weeks
