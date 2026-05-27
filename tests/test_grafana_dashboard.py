"""Tests for STORY-015: Grafana Dashboard JSON (SC-1, SC-6).

Phase 7 test design — 3 tests covering dashboard structure and restart links.
"""

from __future__ import annotations

import json
import os

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DASHBOARD_PATH = os.path.join(
    os.path.dirname(__file__), "..", "deploy", "grafana-agent-dashboard.json"
)


def _load_dashboard() -> dict:
    """Load and parse the Grafana dashboard JSON."""
    with open(_DASHBOARD_PATH, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# SC-1: Grafana dashboard deployed
# ---------------------------------------------------------------------------


class TestGrafanaDashboard:
    """Tests for Grafana dashboard JSON structure."""

    def test_dashboard_is_valid_json(self) -> None:
        """Dashboard file is valid JSON."""
        data = _load_dashboard()
        assert "dashboard" in data
        assert "panels" in data["dashboard"]

    def test_has_required_panels(self) -> None:
        """Dashboard has cost, sessions, and status panels."""
        data = _load_dashboard()
        panels = data["dashboard"]["panels"]
        titles = [p["title"] for p in panels]

        assert "Cost per Agent per Day" in titles
        assert "Sessions per Agent" in titles
        assert "Agent Status" in titles

    def test_has_restart_link_panel(self) -> None:
        """Dashboard has a restart link panel with URL template (SC-6)."""
        data = _load_dashboard()
        panels = data["dashboard"]["panels"]

        restart_panels = [p for p in panels if "restart" in p.get("title", "").lower()]
        assert len(restart_panels) >= 1

        restart_panel = restart_panels[0]
        # Check for restart URL in links or content
        has_restart_url = False
        if "links" in restart_panel:
            for link in restart_panel["links"]:
                if "/api/restart" in link.get("url", ""):
                    has_restart_url = True
                    break
        if "options" in restart_panel:
            content = restart_panel["options"].get("content", "")
            if "/api/restart" in content:
                has_restart_url = True

        assert has_restart_url, "Restart panel must contain /api/restart URL"
