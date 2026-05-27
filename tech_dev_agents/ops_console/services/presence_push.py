"""Presence push service — pushes presence state to agent gateways.

STORY-304: Event-Driven Teams Presence
Ops-console calls push_presence() after dispatch claim/complete/fail
to set the agent's Teams presence via the agent gateway endpoint.

Retry + backoff design:
- Max retries: 3
- Base delay: 0.5s, backoff factor: 2x (0.5s, 1s, 2s)
- Retry on ConnectionError or 5xx
- No retry on 4xx (except 401)
- After max retries: log error, return (no raise) — AC-6 silent failure
"""

from __future__ import annotations

import asyncio
import logging
import os

import httpx

logger = logging.getLogger(__name__)

PRESENCE_PATH = "/internal/presence"
MAX_RETRIES = 3
BASE_DELAY = 0.5  # seconds
BACKOFF_FACTOR = 2


def _resolve_agent_gateway_url(agent_name: str, agent_service) -> str | None:
    """Resolve the gateway URL for an agent from the registry.

    Returns the full URL (http://{host}:{port}/internal/presence)
    or None if the agent is not found.
    """
    try:
        agent = agent_service.get_agent(agent_name)
        return f"http://{agent.host}:{agent.port}{PRESENCE_PATH}"
    except Exception:
        logger.warning("Agent '%s' not found in registry — skipping presence push", agent_name)
        return None


async def push_presence(
    *,
    agent_name: str,
    availability: str,
    activity: str,
    agent_service,
    http_client: httpx.AsyncClient,
) -> None:
    """Push presence state to an agent's gateway endpoint.

    Uses exponential backoff on transient failures. Fails silently
    after max retries (AC-6: backward compat).

    Args:
        agent_name: Name of the agent (e.g., "dan")
        availability: Teams presence availability ("Busy" or "Available")
        activity: Teams presence activity ("InACall" or "Available")
        agent_service: AgentService instance for registry lookups
        http_client: httpx.AsyncClient for HTTP calls
    """
    url = _resolve_agent_gateway_url(agent_name, agent_service)
    if url is None:
        return

    api_key = os.environ.get("OPS_CONSOLE_API_KEY", "")
    headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
    payload = {"availability": availability, "activity": activity}

    delay = BASE_DELAY
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = await http_client.post(url, json=payload, headers=headers, timeout=5.0)

            if resp.status_code == 200:
                logger.info(
                    "Pushed presence %s/%s to %s",
                    availability, activity, agent_name,
                )
                return

            # 5xx — retry
            if resp.status_code >= 500:
                logger.warning(
                    "Presence push to %s returned %d (attempt %d/%d)",
                    agent_name, resp.status_code, attempt, MAX_RETRIES,
                )
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(delay)
                    delay *= BACKOFF_FACTOR
                continue

            # 4xx (not 401) — don't retry, log and return
            if 400 <= resp.status_code < 500:
                logger.warning(
                    "Presence push to %s returned %d: %s — not retrying",
                    agent_name, resp.status_code, resp.text[:200],
                )
                return

        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, OSError) as exc:
            logger.warning(
                "Presence push to %s failed (attempt %d/%d): %s",
                agent_name, attempt, MAX_RETRIES, exc,
            )
            if attempt < MAX_RETRIES:
                await asyncio.sleep(delay)
                delay *= BACKOFF_FACTOR

    logger.error(
        "Presence push to %s exhausted %d retries — giving up (AC-6 silent failure)",
        agent_name, MAX_RETRIES,
    )
