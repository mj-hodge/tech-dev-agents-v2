"""STORY-541: Agent status taxonomy — derive status from multi-signal inputs.

Tests the new derive_agent_status() function and the busy-from-dispatch-table
contract. RED state: these tests MUST fail before Phase 8 implementation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Group A: Status derivation from (reachable, poller_active, paused_until,
#          has_claim, phase_in_progress)
# ---------------------------------------------------------------------------


class TestStatusDerivation:
    """Verify derive_agent_status() maps multi-signal inputs to the 6-state taxonomy.

    Precedence: unreachable > stopped > paused > working > idle.
    """

    def test_status_idle_when_reachable_poller_active_no_claim(self):
        """VM reachable + poller running + no claim → idle."""
        from tech_dev_agents.ops_console.routes._status import derive_agent_status

        result = derive_agent_status(
            reachable=True,
            poller_active=True,
            paused_until=None,
            has_claim=False,
            phase_in_progress=False,
        )
        assert result == "idle"

    def test_status_working_when_has_claim_and_mid_phase(self):
        """VM reachable + poller running + claimed story + phase running → working."""
        from tech_dev_agents.ops_console.routes._status import derive_agent_status

        result = derive_agent_status(
            reachable=True,
            poller_active=True,
            paused_until=None,
            has_claim=True,
            phase_in_progress=True,
        )
        assert result == "working"

    def test_status_paused_when_paused_until_in_future(self):
        """Poller alive but paused_until is in the future → paused."""
        from tech_dev_agents.ops_console.routes._status import derive_agent_status

        future = datetime.now(timezone.utc) + timedelta(hours=1)
        result = derive_agent_status(
            reachable=True,
            poller_active=True,
            paused_until=future,
            has_claim=True,
            phase_in_progress=False,
        )
        assert result == "paused"

    def test_status_stopped_when_poller_inactive(self):
        """VM reachable but poller is dead → stopped."""
        from tech_dev_agents.ops_console.routes._status import derive_agent_status

        result = derive_agent_status(
            reachable=True,
            poller_active=False,
            paused_until=None,
            has_claim=False,
            phase_in_progress=False,
        )
        assert result == "stopped"

    def test_status_unreachable_when_not_reachable(self):
        """VM probe fails → unreachable (highest precedence)."""
        from tech_dev_agents.ops_console.routes._status import derive_agent_status

        result = derive_agent_status(
            reachable=False,
            poller_active=True,
            paused_until=None,
            has_claim=True,
            phase_in_progress=True,
        )
        assert result == "unreachable"

    def test_status_precedence_unreachable_beats_stopped(self):
        """When both unreachable and poller inactive, unreachable wins."""
        from tech_dev_agents.ops_console.routes._status import derive_agent_status

        result = derive_agent_status(
            reachable=False,
            poller_active=False,
            paused_until=None,
            has_claim=False,
            phase_in_progress=False,
        )
        assert result == "unreachable"

    def test_status_precedence_stopped_beats_paused(self):
        """When poller inactive and paused_until is set, stopped wins."""
        from tech_dev_agents.ops_console.routes._status import derive_agent_status

        future = datetime.now(timezone.utc) + timedelta(hours=1)
        result = derive_agent_status(
            reachable=True,
            poller_active=False,
            paused_until=future,
            has_claim=True,
            phase_in_progress=False,
        )
        assert result == "stopped"

    def test_status_paused_until_in_past_is_idle(self):
        """Expired paused_until treated as not-paused → idle if no claim."""
        from tech_dev_agents.ops_console.routes._status import derive_agent_status

        past = datetime.now(timezone.utc) - timedelta(minutes=1)
        result = derive_agent_status(
            reachable=True,
            poller_active=True,
            paused_until=past,
            has_claim=False,
            phase_in_progress=False,
        )
        assert result == "idle"


# ---------------------------------------------------------------------------
# Group B: busy derived from dispatch table, not health probe
# ---------------------------------------------------------------------------


class TestBusyFromDispatchTable:
    """Verify busy is computed from dispatch_items claimed rows, not health.active_sessions."""

    def test_busy_from_dispatch_table_not_health_probe(self):
        """Agent with active_sessions > 0 but no claimed dispatch row → busy=False.

        The old computation `busy=bool((health.active_sessions if health else 0) > 0)`
        must NOT determine busy status.
        """
        from tech_dev_agents.ops_console.services.dispatch_db_service import (
            DispatchDBService,
        )

        # has_active_claim() should exist and return False for this agent
        service = DispatchDBService.__new__(DispatchDBService)
        # This will fail until has_active_claim is implemented
        assert hasattr(service, "has_active_claim"), (
            "DispatchDBService must have a has_active_claim method"
        )

    def test_busy_true_when_claimed_row_exists(self):
        """Agent with a claimed dispatch row → busy=True even if active_sessions == 0.

        busy = exists(dispatch_items WHERE claimed_by=<name> AND status='claimed')
        """
        from tech_dev_agents.ops_console.services.dispatch_db_service import (
            DispatchDBService,
        )

        service = DispatchDBService.__new__(DispatchDBService)
        assert hasattr(service, "has_active_claim"), (
            "DispatchDBService must have a has_active_claim method"
        )

    @pytest.mark.asyncio
    async def test_has_active_claim_returns_false_when_no_rows(self):
        """has_active_claim('derrick') → False when no claimed rows exist."""
        from tech_dev_agents.ops_console.services.dispatch_db_service import (
            DispatchDBService,
        )

        # Mock the DB connection to return no rows
        mock_pool = AsyncMock()
        mock_pool.fetchval = AsyncMock(return_value=None)
        service = DispatchDBService(pool=mock_pool)

        result = await service.has_active_claim("derrick")
        assert result is False

    @pytest.mark.asyncio
    async def test_has_active_claim_returns_true_when_claimed(self):
        """has_active_claim('daisy') → True when a claimed row exists."""
        from tech_dev_agents.ops_console.services.dispatch_db_service import (
            DispatchDBService,
        )

        # Mock the DB connection to return a row
        mock_pool = AsyncMock()
        mock_pool.fetchval = AsyncMock(return_value=1)
        service = DispatchDBService(pool=mock_pool)

        result = await service.has_active_claim("daisy")
        assert result is True
