"""Agent registry management, health poll fan-out, restart/pause forwarding."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

from tech_dev_agents.agent_dashboard import (
    AgentHealthSnapshot,
    AgentNotFoundError,
    AgentRecord,
    RestartResult,
    build_agent_record,
    build_health_snapshot,
    build_restart_command,
    build_restart_result,
    validate_restart,
)
from tech_dev_agents.cost_dashboard import AgentActivityStatus
from tech_dev_agents.ops_console.cache import TTLCache
from tech_dev_agents.ops_console.models.responses import AgentStatusEnum


class AgentService:
    """Agent registry management, health polling, and control operations."""

    def __init__(
        self,
        registry_path: str,
        http_client: httpx.AsyncClient,
        agent_api_key: str,
        health_cache_ttl: int = 30,
        loki_client: Any | None = None,
    ):
        self._registry_path = registry_path
        self._http = http_client
        self._agent_api_key = agent_api_key
        self._cache = TTLCache(health_cache_ttl)
        self._registry: list[AgentRecord] | None = None
        self._registry_mtime: float = 0.0
        self._loki = loki_client

    def get_registry(self) -> list[AgentRecord]:
        """Return all agents from registry. Reloads if file changed (mtime check)."""
        try:
            mtime = os.path.getmtime(self._registry_path)
        except OSError:
            mtime = 0.0

        if self._registry is None or mtime != self._registry_mtime:
            self._registry = self._load_registry()
            self._registry_mtime = mtime

        return self._registry

    def get_agent(self, name: str) -> AgentRecord:
        """Return a single agent by name. Raises AgentNotFoundError if not found."""
        logger.debug("Looking up agent: %s", name)
        for agent in self.get_registry():
            if agent.name == name:
                return agent
        raise AgentNotFoundError(f"Agent '{name}' not found in registry")

    async def get_all_health(self) -> list[AgentHealthSnapshot]:
        """Fan-out GET /health to all enabled agent VMs.

        Uses asyncio.gather with 5s timeout per VM.
        Returns AgentHealthSnapshot per agent (OFFLINE for unreachable).
        Caches for 30s.
        """
        cached = self._cache.get("all_health")
        if cached is not None:
            return cached

        agents = self.get_registry()
        enabled = [a for a in agents if a.enabled]

        tasks = [self._poll_agent_health(agent) for agent in enabled]
        snapshots = await asyncio.gather(*tasks)

        # Add OFFLINE entries for disabled agents
        disabled = [
            build_health_snapshot(
                agent_name=a.name,
                last_activity=None,
                uptime_seconds=0,
                active_sessions=0,
                error_count=0,
            )
            for a in agents
            if not a.enabled
        ]

        result = list(snapshots) + disabled
        self._cache.set("all_health", result)
        return result

    async def get_agent_health(self, name: str) -> AgentHealthSnapshot:
        """Get health for a single agent. Returns from cache if fresh."""
        cached = self._cache.get(f"health:{name}")
        if cached is not None:
            return cached

        agent = self.get_agent(name)
        snapshot = await self._poll_agent_health(agent)
        self._cache.set(f"health:{name}", snapshot)
        return snapshot

    async def restart_agent(
        self, name: str, reason: str, force: bool = False
    ) -> RestartResult:
        """POST restart to agent VM's health API.

        Validates preconditions before sending the restart command.
        """
        agent = self.get_agent(name)
        if not agent.enabled:
            raise ValueError(f"Agent '{name}' is disabled. Enable before restarting.")

        health = await self.get_agent_health(name)

        command = build_restart_command(
            agent_name=name,
            reason=reason,
            requested_by="ops-console",
            force=force,
        )

        # Validate restart preconditions
        registry = self.get_registry()
        command = validate_restart(command, registry, health)

        # Send restart to VM
        url = f"http://{agent.host}:{agent.port}/restart"
        headers = {"X-API-Key": self._agent_api_key}
        try:
            resp = await self._http.post(
                url,
                json={"reason": reason, "force": force},
                headers=headers,
                timeout=10.0,
            )
            success = resp.status_code == 200
            message = "Restart initiated successfully" if success else f"Restart failed: {resp.text}"
        except httpx.HTTPError:
            logger.warning("Failed to reach agent '%s' at %s:%s for restart", name, agent.host, agent.port, exc_info=True)
            success = False
            message = f"Failed to reach agent '{name}' at {agent.host}:{agent.port}"

        previous_status = health.status if hasattr(health, "status") else AgentActivityStatus.OFFLINE
        return build_restart_result(
            agent_name=name,
            success=success,
            message=message,
            previous_status=previous_status,
            new_status=AgentActivityStatus.ONLINE if success else None,
        )

    async def pause_agent(
        self, name: str, action: str, reason: str | None = None
    ) -> dict[str, Any]:
        """POST pause/resume to agent VM."""
        agent = self.get_agent(name)
        url = f"http://{agent.host}:{agent.port}/pause"
        headers = {"X-API-Key": self._agent_api_key}
        body: dict[str, Any] = {"action": action}
        if reason:
            body["reason"] = reason

        try:
            resp = await self._http.post(url, json=body, headers=headers, timeout=10.0)
            success = resp.status_code == 200
            message = f"Agent {action}d successfully" if success else f"{action.title()} failed: {resp.text}"
        except httpx.HTTPError:
            logger.warning("Failed to reach agent '%s' at %s:%s for %s", name, agent.host, agent.port, action, exc_info=True)
            success = False
            message = f"Failed to reach agent '{name}' at {agent.host}:{agent.port}"

        return {
            "agent_name": name,
            "action": action,
            "success": success,
            "message": message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    async def _poll_agent_health(self, agent: AgentRecord) -> AgentHealthSnapshot:
        """Determine agent health from Loki log activity (preferred) or HTTP fallback.

        When a loki_client is available, queries the most recent log entry
        to derive online/idle/offline status.  Falls back to HTTP /health
        polling only when no Loki client is configured.
        """
        # Loki-based health (preferred path — STORY-024)
        if self._loki is not None:
            try:
                loki_name = agent.loki_label or agent.name
                last_activity = await self._loki.get_last_activity(loki_name)
                # Telemetry resilience: if Loki's generic "last activity" is stale
                # but dispatch logs show the poller is actively running, refresh
                # last_activity to now so active queue-empty agents are not shown
                # as stuck.
                if self._is_stale(last_activity):
                    try:
                        dispatch = await self._loki.query_dispatch_state(loki_name)
                    except Exception:
                        logger.warning(
                            "Dispatch-state fallback failed for %s; keeping stale Loki activity",
                            agent.name,
                            exc_info=True,
                        )
                    else:
                        if getattr(dispatch, "status", "unknown") in {
                            "idle",
                            "working",
                            "rate_limited",
                        }:
                            last_activity = datetime.now(timezone.utc).isoformat()
                return build_health_snapshot(
                    agent_name=agent.name,
                    last_activity=last_activity,
                    uptime_seconds=0,
                    active_sessions=1 if last_activity else 0,
                    error_count=0,
                )
            except Exception:
                logger.warning(
                    "Loki health poll failed for agent %s, marking OFFLINE",
                    agent.name,
                    exc_info=True,
                )
                return build_health_snapshot(
                    agent_name=agent.name,
                    last_activity=None,
                    uptime_seconds=0,
                    active_sessions=0,
                    error_count=0,
                )

        # HTTP fallback (legacy path)
        url = f"http://{agent.host}:{agent.port}/health"
        headers = {"X-API-Key": self._agent_api_key}
        try:
            resp = await self._http.get(
                url, headers=headers, timeout=httpx.Timeout(5.0)
            )
            if resp.status_code == 200:
                data = resp.json()
                return build_health_snapshot(
                    agent_name=agent.name,
                    last_activity=data.get("last_activity") or datetime.now(timezone.utc).isoformat(),
                    uptime_seconds=data.get("uptime_seconds", 1),
                    active_sessions=data.get("active_sessions", 0),
                    error_count=data.get("error_count", 0),
                )
        except httpx.HTTPError:
            logger.warning("Health poll failed for agent %s at %s", agent.name, url, exc_info=True)

        # Return OFFLINE snapshot on failure
        return build_health_snapshot(
            agent_name=agent.name,
            last_activity=None,
            uptime_seconds=0,
            active_sessions=0,
            error_count=0,
        )

    @staticmethod
    def _is_stale(last_activity: str | None, threshold_minutes: int = 60) -> bool:
        """Return True when activity timestamp is older than threshold_minutes."""
        if not last_activity:
            return False
        try:
            parsed = datetime.fromisoformat(last_activity)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return False
        return parsed < (datetime.now(timezone.utc) - timedelta(minutes=threshold_minutes))

    def _load_registry(self) -> list[AgentRecord]:
        """Load agent registry from JSON file."""
        try:
            with open(self._registry_path) as f:
                raw = json.load(f)
                data = raw.get("agents", raw) if isinstance(raw, dict) else raw
        except (OSError, json.JSONDecodeError):
            return []

        agents = []
        for entry in data:
            agents.append(
                build_agent_record(
                    name=entry["name"],
                    host=entry.get("host", entry.get("ip", "localhost")),
                    port=entry.get("port", entry.get("health_port", 8080)),
                    role=entry.get("role", "developer"),
                    enabled=entry.get("enabled", True),
                    loki_label=entry.get("loki_label"),
                )
            )
        return agents
