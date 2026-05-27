"""STORY-803 AC-8 / T-8: Grafana dashboard has 'needs_info loop guards (24h)' panel.

The Phase 8 implementation adds a panel to the ops Grafana dashboard that surfaces
the three failure-mode event types introduced by STORY-803:
  1. directive_bypass_attempted  (Bug 1 guard fires)
  2. question_commit_failed      (Bug 2 — git add fails)
  3. phase8_no_commits           (Bug 3 — Phase 8 silent exit)

This structural test parses the dashboard JSON and verifies the panel exists with
the expected title and at least three LogQL query targets — one per signal.

Test:
  T-8 (RED): parse deploy/grafana-agent-dashboard.json, assert a panel titled
             'needs_info loop guards (24h)' exists with 3 target queries.

RED: no such panel exists in the dashboard JSON yet. Phase 8 adds it.
"""
from __future__ import annotations

import json
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DASHBOARD_PATH = REPO_ROOT / "deploy" / "grafana-agent-dashboard.json"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_dashboard() -> dict:
    """Load the Grafana dashboard JSON from the deploy directory."""
    if not DASHBOARD_PATH.exists():
        pytest.fail(
            f"Dashboard JSON not found at {DASHBOARD_PATH}.\n"
            "Ensure deploy/grafana-agent-dashboard.json exists in the repo."
        )
    with open(DASHBOARD_PATH) as f:
        raw = json.load(f)
    # The file may be wrapped in {"dashboard": {...}} or be the dashboard directly
    if "dashboard" in raw and isinstance(raw["dashboard"], dict):
        return raw["dashboard"]
    return raw


def _find_panels(dashboard: dict, title_substring: str) -> list[dict]:
    """Find all panels whose title contains title_substring (case-insensitive)."""
    panels = dashboard.get("panels", [])
    matches = []
    for panel in panels:
        panel_title = panel.get("title", "")
        if title_substring.lower() in panel_title.lower():
            matches.append(panel)
        # Grafana supports nested panels in rows
        for sub in panel.get("panels", []):
            sub_title = sub.get("title", "")
            if title_substring.lower() in sub_title.lower():
                matches.append(sub)
    return matches


# ---------------------------------------------------------------------------
# T-8 (RED): Dashboard has the 'needs_info loop guards (24h)' panel
# ---------------------------------------------------------------------------


class TestGrafanaDashboardStory803Panel:
    """T-8 (STORY-803 AC-8): Grafana dashboard must contain a panel titled
    'needs_info loop guards (24h)' with three target queries (one per signal).

    RED: the panel does not exist in deploy/grafana-agent-dashboard.json yet.
    Phase 8 adds it alongside the three LogQL queries.
    """

    def test_grafana_dashboard_has_story_803_panel(self):
        """T-8 (RED): 'needs_info loop guards (24h)' panel must exist in
        deploy/grafana-agent-dashboard.json with at least 3 target queries.

        The panel surfaces three event-type signals (Bug 1, Bug 2, Bug 3):
          - directive_bypass_attempted
          - question_commit_failed
          - phase8_no_commits

        RED reason: panel does not yet exist in the dashboard JSON.
        Phase 8 must add the panel JSON block and three LogQL query targets.
        """
        dashboard = _load_dashboard()
        PANEL_TITLE = "needs_info loop guards (24h)"

        panels = _find_panels(dashboard, PANEL_TITLE)

        assert len(panels) >= 1, (
            f"No panel with title containing '{PANEL_TITLE}' found in "
            f"{DASHBOARD_PATH.relative_to(REPO_ROOT)}.\n\n"
            "STORY-803 AC-8 (Phase 8) must add this panel to the dashboard.\n"
            "Panel title must be exactly: 'needs_info loop guards (24h)'\n\n"
            "Required panel structure:\n"
            '  {\n'
            '    "title": "needs_info loop guards (24h)",\n'
            '    "type": "timeseries",\n'
            '    "targets": [\n'
            '      {"expr": "...directive_bypass_attempted...", "refId": "A"},\n'
            '      {"expr": "...question_commit_failed...", "refId": "B"},\n'
            '      {"expr": "...phase8_no_commits...", "refId": "C"}\n'
            '    ]\n'
            '  }\n\n'
            f"Available panel titles in dashboard: "
            f"{[p.get('title', '') for p in dashboard.get('panels', [])[:20]]}"
        )

        panel = panels[0]

        # Must have at least 3 targets (one per signal)
        targets = panel.get("targets", [])
        assert len(targets) >= 3, (
            f"Panel '{PANEL_TITLE}' must have at least 3 target queries "
            f"(directive_bypass_attempted, question_commit_failed, phase8_no_commits).\n"
            f"Found {len(targets)} target(s): {targets!r}\n\n"
            "Add three LogQL queries per spec §3.4:\n"
            "  A: count_over_time {{...}} |= 'directive_bypass_attempted' [24h]\n"
            "  B: count_over_time {{...}} |= 'question_commit_failed' [24h]\n"
            "  C: count_over_time {{...}} |= 'phase8_no_commits' [24h]"
        )

    def test_grafana_dashboard_story_803_panel_has_directive_bypass_query(self):
        """T-8 companion: at least one target query references 'directive_bypass_attempted'.

        RED: panel doesn't exist yet → no such target query.
        """
        dashboard = _load_dashboard()
        PANEL_TITLE = "needs_info loop guards (24h)"

        panels = _find_panels(dashboard, PANEL_TITLE)
        if not panels:
            pytest.fail(
                f"Panel '{PANEL_TITLE}' not found in dashboard — "
                "add the panel first (see test_grafana_dashboard_has_story_803_panel)."
            )

        panel = panels[0]
        targets = panel.get("targets", [])
        exprs = [t.get("expr", "") + t.get("query", "") for t in targets]

        has_directive_query = any(
            "directive_bypass_attempted" in expr for expr in exprs
        )
        assert has_directive_query, (
            f"Panel '{PANEL_TITLE}' must have a LogQL query referencing "
            f"'directive_bypass_attempted'.\n"
            f"Current target expressions: {exprs!r}\n\n"
            "Add LogQL target A per spec §3.4:\n"
            '  sum by (story_id) (count_over_time({app="ops-console"} '
            '|= "directive_bypass_attempted" [24h]))'
        )

    def test_grafana_dashboard_story_803_panel_has_question_commit_query(self):
        """T-8 companion: at least one target query references 'question_commit_failed'.

        RED: panel doesn't exist yet → no such target query.
        """
        dashboard = _load_dashboard()
        PANEL_TITLE = "needs_info loop guards (24h)"

        panels = _find_panels(dashboard, PANEL_TITLE)
        if not panels:
            pytest.fail(
                f"Panel '{PANEL_TITLE}' not found in dashboard — "
                "add the panel first."
            )

        panel = panels[0]
        targets = panel.get("targets", [])
        exprs = [t.get("expr", "") + t.get("query", "") for t in targets]

        has_commit_query = any(
            "question_commit_failed" in expr for expr in exprs
        )
        assert has_commit_query, (
            f"Panel '{PANEL_TITLE}' must have a LogQL query referencing "
            f"'question_commit_failed'.\n"
            f"Current target expressions: {exprs!r}\n\n"
            "Add LogQL target B per spec §3.4:\n"
            '  sum by (stage) (count_over_time({job=~"agent-.*"} '
            '|= "question_commit_failed" [24h]))'
        )

    def test_grafana_dashboard_story_803_panel_has_phase8_no_commits_query(self):
        """T-8 companion: at least one target query references 'phase8_no_commits'.

        RED: panel doesn't exist yet → no such target query.
        """
        dashboard = _load_dashboard()
        PANEL_TITLE = "needs_info loop guards (24h)"

        panels = _find_panels(dashboard, PANEL_TITLE)
        if not panels:
            pytest.fail(
                f"Panel '{PANEL_TITLE}' not found in dashboard — "
                "add the panel first."
            )

        panel = panels[0]
        targets = panel.get("targets", [])
        exprs = [t.get("expr", "") + t.get("query", "") for t in targets]

        has_phase8_query = any(
            "phase8_no_commits" in expr for expr in exprs
        )
        assert has_phase8_query, (
            f"Panel '{PANEL_TITLE}' must have a LogQL query referencing "
            f"'phase8_no_commits'.\n"
            f"Current target expressions: {exprs!r}\n\n"
            "Add LogQL target C per spec §3.4:\n"
            '  sum by (attempt) (count_over_time({job=~"agent-.*"} '
            '|= "phase8_no_commits" [24h]))'
        )
