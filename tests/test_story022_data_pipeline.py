"""Tests for STORY-022: Ops Dashboard Data Pipeline Fix.

Phase 7 test design — tests covering [DONE] line regex fix (AC-2),
agent detail cost enrichment (AC-3), and systemd timer existence (AC-2).

These tests are RED until Phase 8 implementation.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# AC-2: [DONE] line regex — field-order agnostic parsing
# ---------------------------------------------------------------------------


class TestParseDoneLineFieldOrder:
    """Verify _parse_done_line handles any field order in [DONE] lines.

    The actual SDK output format is:
        [DONE] turns=1 tools=0 cost=$16.59 duration=30s stop=end error=False
    But the old regex expected cost= before turns=.
    """

    def test_parse_done_line_turns_before_cost(self) -> None:
        """Real SDK output: turns= appears before cost=. Must parse correctly."""
        from tech_dev_agents.ops_console.services.loki_client import _parse_done_line

        line = "[DONE] turns=42 tools=5 cost=$16.59 duration=30s stop=end error=False"
        result = _parse_done_line(line)
        assert result is not None, "Should parse [DONE] line with turns before cost"
        assert result["cost"] == 16.59
        assert result["turns"] == 42

    def test_parse_done_line_cost_before_turns(self) -> None:
        """Backwards compat: cost= before turns= still works."""
        from tech_dev_agents.ops_console.services.loki_client import _parse_done_line

        line = "[DONE] cost=$10.00 turns=20 duration=15s"
        result = _parse_done_line(line)
        assert result is not None, "Should parse [DONE] line with cost before turns"
        assert result["cost"] == 10.00
        assert result["turns"] == 20

    def test_parse_done_line_skips_auth_failure(self) -> None:
        """Lines with cost=$? (auth failure) should be skipped."""
        from tech_dev_agents.ops_console.services.loki_client import _parse_done_line

        line = "[DONE] turns=1 tools=0 cost=$? duration=0s stop=stop_sequence error=True"
        result = _parse_done_line(line)
        assert result is None, "Should skip [DONE] lines with cost=$? (auth failure)"

    def test_parse_done_line_missing_turns_defaults_zero(self) -> None:
        """If turns= is missing, default to 0."""
        from tech_dev_agents.ops_console.services.loki_client import _parse_done_line

        line = "[DONE] cost=$5.50 duration=10s"
        result = _parse_done_line(line)
        assert result is not None, "Should parse line with cost but no turns"
        assert result["cost"] == 5.50
        assert result["turns"] == 0

    def test_parse_done_line_with_claude_code_prefix(self) -> None:
        """Real log lines have [Claude Code] prefix from claude_sdk_tool.py."""
        from tech_dev_agents.ops_console.services.loki_client import _parse_done_line

        line = "[Claude Code] [DONE] turns=10 tools=3 cost=$8.25 duration=45s stop=end error=False"
        result = _parse_done_line(line)
        assert result is not None, "Should parse [DONE] with [Claude Code] prefix"
        assert result["cost"] == 8.25
        assert result["turns"] == 10

    def test_parse_done_line_no_dollar_sign(self) -> None:
        """Handle cost values without $ prefix."""
        from tech_dev_agents.ops_console.services.loki_client import _parse_done_line

        line = "[DONE] turns=5 cost=3.50 duration=10s"
        result = _parse_done_line(line)
        assert result is not None, "Should parse cost without $ prefix"
        assert result["cost"] == 3.50


# ---------------------------------------------------------------------------
# AC-2: Cost collector systemd timer
# ---------------------------------------------------------------------------


class TestCostCollectorSystemdTimer:
    """Verify systemd timer and service files exist for cost collection."""

    def test_cost_collector_systemd_timer_exists(self) -> None:
        """Timer unit file must exist in deployment/vm/."""
        timer_path = Path(__file__).parent.parent / "deployment" / "vm" / "cost-collector.timer"
        assert timer_path.exists(), f"Missing systemd timer: {timer_path}"

    def test_cost_collector_systemd_service_exists(self) -> None:
        """Service unit file must exist in deployment/vm/."""
        service_path = Path(__file__).parent.parent / "deployment" / "vm" / "cost-collector.service"
        assert service_path.exists(), f"Missing systemd service: {service_path}"

    def test_cost_collector_timer_has_persistent(self) -> None:
        """Timer must have Persistent=true for catch-up on missed runs."""
        timer_path = Path(__file__).parent.parent / "deployment" / "vm" / "cost-collector.timer"
        if timer_path.exists():
            content = timer_path.read_text()
            assert "Persistent=true" in content, "Timer must have Persistent=true"


# ---------------------------------------------------------------------------
# AC-3: Agent detail cost enrichment (cost_7d, cost_30d)
# ---------------------------------------------------------------------------


class TestAgentDetailCostEnrichment:
    """Verify AgentDetailResponse includes cost_7d and cost_30d fields."""

    def test_agent_detail_response_has_cost_fields(self) -> None:
        """AgentDetailResponse model must have cost_7d and cost_30d fields."""
        from tech_dev_agents.ops_console.models.responses import AgentDetailResponse

        fields = AgentDetailResponse.model_fields
        assert "cost_7d" in fields, "AgentDetailResponse must have cost_7d field"
        assert "cost_30d" in fields, "AgentDetailResponse must have cost_30d field"

    def test_agent_detail_cost_defaults_to_zero(self) -> None:
        """cost_7d and cost_30d should default to 0.0."""
        from tech_dev_agents.ops_console.models.responses import (
            AgentDetailResponse,
            AgentStatusEnum,
            CostToday,
        )

        response = AgentDetailResponse(
            name="test",
            status=AgentStatusEnum.ONLINE,
            role="developer",
            enabled=True,
            host="localhost",
            port=8080,
            last_activity=None,
            uptime_seconds=0,
            active_sessions=0,
            error_count=0,
            checked_at="2026-04-07T00:00:00Z",
            current_story=None,
            cost_today=CostToday(
                sdk_cost_usd=0.0,
                azure_cost_usd=0.0,
                total_cost_usd=0.0,
                sdk_sessions=0,
                sdk_turns=0,
            ),
            recent_activity=[],
        )
        assert response.cost_7d == 0.0
        assert response.cost_30d == 0.0
