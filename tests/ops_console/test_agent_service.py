"""Tests for AgentService — T01-T08."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from tech_dev_agents.agent_dashboard import (
    AgentNotFoundError,
    build_agent_record,
    build_health_snapshot,
)
from tech_dev_agents.cost_dashboard import AgentActivityStatus
from tech_dev_agents.ops_console.services.agent_service import AgentService


def _make_registry_file(tmp_path, agents):
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(agents))
    return str(path)


def _make_vm_health_response(status=200, data=None):
    """Build a mock httpx Response for a VM health endpoint."""
    if data is None:
        data = {
            "last_activity": datetime.now(timezone.utc).isoformat(),
            "uptime_seconds": 86400,
            "active_sessions": 1,
            "error_count": 0,
        }
    resp = httpx.Response(status, json=data)
    return resp


class TestAgentServiceHealth:
    """T01-T04: Health polling and registry."""

    @pytest.mark.asyncio
    async def test_fan_out_health_all_online(self, tmp_path):
        """T01: get_all_health with 2 reachable VMs returns 2 ONLINE snapshots."""
        agents = [
            {"name": "dan", "host": "10.0.1.10", "port": 8080, "role": "developer", "enabled": True},
            {"name": "derrick", "host": "10.0.1.11", "port": 8080, "role": "developer", "enabled": True},
        ]
        reg_path = _make_registry_file(tmp_path, agents)

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.get.return_value = _make_vm_health_response()

        service = AgentService(reg_path, mock_http, "test-key")
        snapshots = await service.get_all_health()

        assert len(snapshots) == 2
        statuses = {s.agent_name: s.status for s in snapshots}
        assert statuses["dan"] == AgentActivityStatus.ONLINE
        assert statuses["derrick"] == AgentActivityStatus.ONLINE

    @pytest.mark.asyncio
    async def test_fan_out_health_one_unreachable(self, tmp_path):
        """T02: get_all_health with 1 unreachable VM returns OFFLINE for that agent, ONLINE for the other."""
        agents = [
            {"name": "dan", "host": "10.0.1.10", "port": 8080, "role": "developer", "enabled": True},
            {"name": "derrick", "host": "10.0.1.11", "port": 8080, "role": "developer", "enabled": True},
        ]
        reg_path = _make_registry_file(tmp_path, agents)

        call_count = 0

        async def mock_get(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if "10.0.1.11" in url:
                raise httpx.ConnectError("Connection refused")
            return _make_vm_health_response()

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.get = mock_get

        service = AgentService(reg_path, mock_http, "test-key")
        snapshots = await service.get_all_health()

        assert len(snapshots) == 2
        status_map = {s.agent_name: s.status for s in snapshots}
        assert status_map["dan"] == AgentActivityStatus.ONLINE
        assert status_map["derrick"] == AgentActivityStatus.OFFLINE

    @pytest.mark.asyncio
    async def test_fan_out_health_timeout(self, tmp_path):
        """T03: get_all_health with httpx timeout marks agent OFFLINE (not exception)."""
        agents = [
            {"name": "dan", "host": "10.0.1.10", "port": 8080, "role": "developer", "enabled": True},
        ]
        reg_path = _make_registry_file(tmp_path, agents)

        async def mock_get(url, **kwargs):
            raise httpx.ReadTimeout("Read timed out")

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.get = mock_get

        service = AgentService(reg_path, mock_http, "test-key")
        snapshots = await service.get_all_health()

        assert len(snapshots) == 1
        assert snapshots[0].status == AgentActivityStatus.OFFLINE

    @pytest.mark.asyncio
    async def test_get_agent_not_found(self, tmp_path):
        """T04: get_agent with unknown name raises AgentNotFoundError."""
        agents = [
            {"name": "dan", "host": "10.0.1.10", "port": 8080, "role": "developer", "enabled": True},
        ]
        reg_path = _make_registry_file(tmp_path, agents)

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        service = AgentService(reg_path, mock_http, "test-key")

        with pytest.raises(AgentNotFoundError):
            service.get_agent("unknown")

    @pytest.mark.asyncio
    async def test_loki_stale_activity_uses_dispatch_idle_fallback(self, tmp_path):
        """When Loki activity is stale but dispatch is active, treat agent as online."""
        agents = [
            {"name": "daisy", "host": "10.0.1.12", "port": 8080, "role": "developer", "enabled": True},
        ]
        reg_path = _make_registry_file(tmp_path, agents)

        stale_iso = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        mock_loki = MagicMock()
        mock_loki.get_last_activity = AsyncMock(return_value=stale_iso)
        mock_loki.query_dispatch_state = AsyncMock(return_value=MagicMock(status="idle"))

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        service = AgentService(reg_path, mock_http, "test-key", loki_client=mock_loki)

        snapshots = await service.get_all_health()

        assert len(snapshots) == 1
        assert snapshots[0].agent_name == "daisy"
        assert snapshots[0].status in (AgentActivityStatus.ONLINE, AgentActivityStatus.IDLE)

    @pytest.mark.asyncio
    async def test_loki_stale_activity_without_dispatch_signal_stays_stuck(self, tmp_path):
        """Without dispatch signal, stale Loki activity should remain stale-derived."""
        agents = [
            {"name": "devon", "host": "10.0.1.13", "port": 8080, "role": "developer", "enabled": True},
        ]
        reg_path = _make_registry_file(tmp_path, agents)

        stale_iso = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        mock_loki = MagicMock()
        mock_loki.get_last_activity = AsyncMock(return_value=stale_iso)
        mock_loki.query_dispatch_state = AsyncMock(return_value=MagicMock(status="unknown"))

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        service = AgentService(reg_path, mock_http, "test-key", loki_client=mock_loki)

        snapshots = await service.get_all_health()

        assert len(snapshots) == 1
        assert snapshots[0].agent_name == "devon"
        assert snapshots[0].status == AgentActivityStatus.STUCK


class TestAgentServiceControls:
    """T05-T08: Restart and pause operations."""

    @pytest.mark.asyncio
    async def test_restart_success(self, tmp_path):
        """T05: restart_agent on ONLINE agent sends POST to VM and returns RestartResult with success=True."""
        agents = [
            {"name": "dan", "host": "10.0.1.10", "port": 8080, "role": "developer", "enabled": True},
        ]
        reg_path = _make_registry_file(tmp_path, agents)

        now_iso = datetime.now(timezone.utc).isoformat()

        async def mock_get(url, **kwargs):
            return _make_vm_health_response()

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.get = mock_get
        mock_http.post.return_value = httpx.Response(200, json={"success": True})

        service = AgentService(reg_path, mock_http, "test-key")
        result = await service.restart_agent("dan", "Agent stuck", force=False)

        assert result.success is True
        assert result.agent_name == "dan"
        assert "successfully" in result.message

    @pytest.mark.asyncio
    async def test_restart_disabled_agent_rejected(self, tmp_path):
        """T06: restart_agent on disabled agent raises ValidationError without calling VM."""
        agents = [
            {"name": "dan", "host": "10.0.1.10", "port": 8080, "role": "developer", "enabled": False},
        ]
        reg_path = _make_registry_file(tmp_path, agents)

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        service = AgentService(reg_path, mock_http, "test-key")

        with pytest.raises(ValueError, match="disabled"):
            await service.restart_agent("dan", "test reason")

        # Should NOT have called the VM
        mock_http.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_pause_agent_success(self, tmp_path):
        """T07: pause_agent with action='pause' sends POST and returns success."""
        agents = [
            {"name": "dan", "host": "10.0.1.10", "port": 8080, "role": "developer", "enabled": True},
        ]
        reg_path = _make_registry_file(tmp_path, agents)

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.post.return_value = httpx.Response(200, json={"success": True})

        service = AgentService(reg_path, mock_http, "test-key")
        result = await service.pause_agent("dan", "pause", "Reducing spend")

        assert result["success"] is True
        assert result["action"] == "pause"
        assert result["agent_name"] == "dan"

    @pytest.mark.asyncio
    async def test_resume_agent_success(self, tmp_path):
        """T08: pause_agent with action='resume' sends POST and returns success."""
        agents = [
            {"name": "dan", "host": "10.0.1.10", "port": 8080, "role": "developer", "enabled": True},
        ]
        reg_path = _make_registry_file(tmp_path, agents)

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.post.return_value = httpx.Response(200, json={"success": True})

        service = AgentService(reg_path, mock_http, "test-key")
        result = await service.pause_agent("dan", "resume")

        assert result["success"] is True
        assert result["action"] == "resume"
