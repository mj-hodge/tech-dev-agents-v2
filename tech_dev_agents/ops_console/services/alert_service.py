"""Alert service — merge cost, health, and Loki anomaly alerts."""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from tech_dev_agents.ops_console.cache import TTLCache

logger = logging.getLogger(__name__)
from tech_dev_agents.ops_console.models.responses import AlertItem, AlertListResponse
from tech_dev_agents.ops_console.services.loki_client import LokiClient


class AlertService:
    """Merge alerts from cost thresholds, health alerts, and Loki anomaly logs."""

    def __init__(
        self,
        loki: LokiClient,
        agent_service: Any,
        cost_service: Any,
        alert_cache_ttl: int = 60,
    ):
        self._loki = loki
        self._agent_service = agent_service
        self._cost_service = cost_service
        self._cache = TTLCache(alert_cache_ttl)

    async def get_alerts(
        self,
        agent: str | None = None,
        alert_type: str | None = None,
        active: bool | None = None,
        since: str | None = None,
        limit: int = 100,
    ) -> AlertListResponse:
        """Merge alerts from three sources, sorted by timestamp descending.

        Sources:
        1. Health alerts (agent offline/stuck/high_errors)
        2. Loki anomaly alerts ([COST_ANOMALY], [SDK_HEALTH], [TERMINAL_GUARD])
        3. Cost threshold alerts (from cost dashboard)
        """
        now = datetime.now(timezone.utc)
        since_dt = self._parse_since(since, now)
        since_str = since_dt.isoformat()

        all_alerts: list[AlertItem] = []

        # 1. Health-based alerts
        try:
            health_alerts = await self._get_health_alerts()
            all_alerts.extend(health_alerts)
        except Exception:
            logger.warning("Failed to fetch health alerts", exc_info=True)

        # 2. Loki anomaly alerts
        try:
            loki_alerts = await self._get_loki_alerts(since_str, now.isoformat())
            all_alerts.extend(loki_alerts)
        except Exception:
            logger.warning("Failed to fetch Loki alerts", exc_info=True)

        # Apply filters
        if agent:
            all_alerts = [a for a in all_alerts if a.agent_name == agent]
        if alert_type:
            all_alerts = [a for a in all_alerts if a.type == alert_type]
        if active is not None:
            all_alerts = [a for a in all_alerts if a.active == active]

        # Sort by timestamp descending
        all_alerts.sort(key=lambda a: a.triggered_at, reverse=True)

        # Apply limit
        all_alerts = all_alerts[:limit]

        active_count = sum(1 for a in all_alerts if a.active)

        return AlertListResponse(
            alerts=all_alerts,
            total=len(all_alerts),
            active_count=active_count,
            fetched_at=now.isoformat(),
        )

    async def get_active_anomalies(self) -> list[AlertItem]:
        """Get active cost anomaly alerts for the AlertBanner component.

        Loki query: [COST_ANOMALY] in last 15 min. Caches for 60s.
        """
        cached = self._cache.get("active_anomalies")
        if cached is not None:
            return cached

        now = datetime.now(timezone.utc)
        start = (now - timedelta(minutes=15)).isoformat()

        try:
            anomalies = await self._loki.query_anomalies(None, start, now.isoformat())
            alerts = [
                AlertItem(
                    id=_make_alert_id(a.get("line", "")),
                    agent_name=a.get("agent_name", "unknown"),
                    type="cost_anomaly",
                    severity="high",
                    message=a.get("line", "Cost anomaly detected"),
                    active=True,
                    triggered_at=a.get("timestamp", now.isoformat()),
                    resolved_at=None,
                    source="loki",
                )
                for a in anomalies
            ]
        except Exception:
            logger.warning("Failed to fetch active anomalies from Loki", exc_info=True)
            alerts = []

        self._cache.set("active_anomalies", alerts)
        return alerts

    async def _get_health_alerts(self) -> list[AlertItem]:
        """Generate alerts from agent health snapshots."""
        from tech_dev_agents.agent_dashboard import evaluate_health_alerts

        snapshots = await self._agent_service.get_all_health()
        health_alerts = evaluate_health_alerts(
            snapshots=snapshots,
            stuck_threshold_minutes=30,
            error_threshold=10,
            cooldown_keys=set(),
        )

        result: list[AlertItem] = []
        for da in health_alerts:
            result.append(
                AlertItem(
                    id=_make_alert_id(da.cooldown_key),
                    agent_name=da.agent_name,
                    type=da.alert_type,
                    severity=da.severity,
                    message=da.message,
                    active=True,
                    triggered_at=da.triggered_at,
                    resolved_at=None,
                    source="health",
                )
            )
        return result

    async def _get_loki_alerts(self, start: str, end: str) -> list[AlertItem]:
        """Gather Loki-sourced alerts (anomalies, SDK health, terminal guard)."""
        alerts: list[AlertItem] = []

        # Cost anomalies
        try:
            anomalies = await self._loki.query_anomalies(None, start, end)
            for a in anomalies:
                alerts.append(
                    AlertItem(
                        id=_make_alert_id(a.get("line", "")),
                        agent_name=a.get("agent_name", "unknown"),
                        type="cost_anomaly",
                        severity="high",
                        message=a.get("line", "Cost anomaly detected"),
                        active=a.get("active", True),
                        triggered_at=a.get("timestamp", ""),
                        resolved_at=None,
                        source="loki",
                    )
                )
        except Exception:
            logger.warning("Failed to query Loki cost anomalies", exc_info=True)

        # SDK health alerts
        try:
            sdk_health = await self._loki.query_sdk_health(None, start, end)
            for s in sdk_health:
                alerts.append(
                    AlertItem(
                        id=_make_alert_id(s.get("line", "")),
                        agent_name=s.get("labels", {}).get("agent", "unknown"),
                        type="sdk_health",
                        severity="medium",
                        message=s.get("line", "SDK health issue"),
                        active=True,
                        triggered_at=s.get("timestamp", ""),
                        resolved_at=None,
                        source="loki",
                    )
                )
        except Exception:
            logger.warning("Failed to query Loki SDK health alerts", exc_info=True)

        # Terminal guard alerts
        try:
            guard = await self._loki.query_terminal_guard(None, start, end)
            for g in guard:
                alerts.append(
                    AlertItem(
                        id=_make_alert_id(g.get("line", "")),
                        agent_name=g.get("labels", {}).get("agent", "unknown"),
                        type="terminal_guard",
                        severity="high",
                        message=g.get("line", "Terminal guard denial"),
                        active=True,
                        triggered_at=g.get("timestamp", ""),
                        resolved_at=None,
                        source="loki",
                    )
                )
        except Exception:
            logger.warning("Failed to query Loki terminal guard alerts", exc_info=True)

        # Cost alerts ([COST_ALERT] daily threshold breach from daily_cost_alert.sh)
        try:
            cost_alerts = await self._loki.query_cost_alerts(None, start, end)
            for c in cost_alerts:
                alerts.append(
                    AlertItem(
                        id=_make_alert_id(c.get("line", "")),
                        agent_name=c.get("agent_name", "fleet"),
                        type="cost_alert",
                        severity="high",
                        message=c.get("line", "Foundry daily cost alert"),
                        active=True,
                        triggered_at=c.get("timestamp", ""),
                        resolved_at=None,
                        source="loki",
                    )
                )
        except Exception:
            logger.warning("Failed to query Loki cost alerts", exc_info=True)

        return alerts

    @staticmethod
    def _parse_since(since: str | None, now: datetime) -> datetime:
        """Parse a 'since' parameter into a datetime.

        Supports relative ('24h', '7d') or ISO format strings.
        """
        if not since:
            return now - timedelta(hours=24)
        if since.endswith("h"):
            hours = int(since[:-1])
            return now - timedelta(hours=hours)
        if since.endswith("d"):
            days = int(since[:-1])
            return now - timedelta(days=days)
        try:
            return datetime.fromisoformat(since)
        except ValueError:
            return now - timedelta(hours=24)


def _make_alert_id(content: str) -> str:
    """Generate a deterministic alert ID from content."""
    return f"alert_{hashlib.md5(content.encode()).hexdigest()[:12]}"
