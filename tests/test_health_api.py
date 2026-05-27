"""Tests for STORY-015: Health API (SC-4, SC-5).

Phase 7 test design — 8 tests covering health and restart endpoints.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tech_dev_agents.health_api import (
    AuthError,
    build_health_response,
    build_restart_response,
    validate_api_key,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc_iso(minutes_ago: int = 0) -> str:
    dt = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return dt.isoformat()


# ---------------------------------------------------------------------------
# SC-4: Health endpoint
# ---------------------------------------------------------------------------


class TestBuildHealthResponse:
    """Tests for build_health_response."""

    def test_returns_correct_structure(self) -> None:
        """Health response contains all required fields."""
        resp = build_health_response(
            agent_name="dan",
            last_activity=_utc_iso(1),
            uptime_seconds=3600,
            active_sessions=2,
            error_count=0,
        )
        assert resp["agent_name"] == "dan"
        assert resp["uptime_seconds"] == 3600
        assert resp["active_sessions"] == 2
        assert resp["error_count"] == 0
        assert "checked_at" in resp
        assert "status" in resp

    def test_includes_version_field(self) -> None:
        """Health response includes a version string."""
        resp = build_health_response(
            agent_name="dan",
            last_activity=_utc_iso(1),
            uptime_seconds=100,
            active_sessions=0,
            error_count=0,
        )
        assert "version" in resp
        assert isinstance(resp["version"], str)
        assert resp["version"]  # non-empty

    def test_derives_status_from_last_activity(self) -> None:
        """Status is derived from last_activity timestamp."""
        # Recent activity -> online
        resp = build_health_response(
            agent_name="dan",
            last_activity=_utc_iso(1),
            uptime_seconds=100,
            active_sessions=1,
            error_count=0,
        )
        assert resp["status"] == "online"

        # No activity -> offline
        resp_offline = build_health_response(
            agent_name="dan",
            last_activity=None,
            uptime_seconds=0,
            active_sessions=0,
            error_count=0,
        )
        assert resp_offline["status"] == "offline"


# ---------------------------------------------------------------------------
# SC-5: Restart endpoint + auth
# ---------------------------------------------------------------------------


class TestValidateApiKey:
    """Tests for API key validation."""

    def test_accepts_valid_key(self) -> None:
        """Valid API key passes validation."""
        assert validate_api_key("secret-123", "secret-123") is True

    def test_rejects_missing_key(self) -> None:
        """Missing API key raises AuthError."""
        with pytest.raises(AuthError, match="Missing"):
            validate_api_key(None, "secret-123")

    def test_rejects_wrong_key(self) -> None:
        """Wrong API key raises AuthError."""
        with pytest.raises(AuthError, match="Invalid"):
            validate_api_key("wrong-key", "secret-123")


class TestBuildRestartResponse:
    """Tests for build_restart_response."""

    def test_returns_success_result(self) -> None:
        """Successful restart returns success=True."""
        resp = build_restart_response(
            agent_name="dan",
            reason="stuck session",
            requested_by="mark",
            restart_fn=lambda name, force: None,  # no-op
        )
        assert resp["success"] is True
        assert resp["agent_name"] == "dan"
        assert "completed_at" in resp

    def test_returns_failure_on_error(self) -> None:
        """Failed restart returns success=False with message."""

        def failing_restart(name: str, force: bool) -> None:
            raise RuntimeError("systemctl failed")

        resp = build_restart_response(
            agent_name="dan",
            reason="stuck",
            requested_by="mark",
            restart_fn=failing_restart,
        )
        assert resp["success"] is False
        assert "systemctl failed" in resp["message"]
