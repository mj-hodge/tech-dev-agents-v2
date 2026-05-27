"""STORY-508 AC-12: Frontend component existence and content tests.

Phase 7 — RED state.

Covers:
- AlertStatusPanel.tsx: new component displaying active Grafana alerts
- BudgetGauge.tsx: new component displaying token budget/usage
- DispatchQueue.tsx: must add 'paused' case to statusBadge() (STORY-507 follow-up)
- DashboardLayout.tsx: must import both new components

RED reasons:
- AlertStatusPanel.tsx does not yet exist
- BudgetGauge.tsx does not yet exist
- DispatchQueue.tsx has no 'paused' case in statusBadge
- DashboardLayout.tsx does not import AlertStatusPanel or BudgetGauge

All tests pass after Phase 8 creates/updates these files.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
FRONTEND_COMPONENTS = REPO_ROOT / "frontend" / "src" / "components"


# ---------------------------------------------------------------------------
# AC-12: AlertStatusPanel.tsx
# ---------------------------------------------------------------------------


class TestAlertStatusPanel:
    """AC-12: AlertStatusPanel.tsx must be created with correct content."""

    COMPONENT = FRONTEND_COMPONENTS / "AlertStatusPanel.tsx"

    def test_alert_status_panel_exists(self):
        """AC-12: AlertStatusPanel.tsx must be created in frontend/src/components/.

        RED: File does not yet exist.
        GREEN after: Create frontend/src/components/AlertStatusPanel.tsx.

        This component displays active Grafana alerts from GET /api/alerts/active.
        It shows alert name, severity, and a 'No active alerts' empty state.
        """
        assert self.COMPONENT.exists(), (
            f"AlertStatusPanel.tsx not found at {self.COMPONENT}. "
            "AC-12 requires creating this React component. "
            "It should call GET /api/alerts/active and render a list of active alerts."
        )

    def test_alert_status_panel_references_active_alerts_api(self):
        """AC-12: AlertStatusPanel.tsx must reference /api/alerts/active endpoint.

        RED: File does not yet exist.
        GREEN after: Add a fetch/useEffect/hook call to /api/alerts/active.
        """
        if not self.COMPONENT.exists():
            pytest.skip("AlertStatusPanel.tsx not yet created")
        content = self.COMPONENT.read_text()
        assert "/api/alerts/active" in content, (
            "AlertStatusPanel.tsx does not reference '/api/alerts/active'. "
            "AC-12 requires the component to fetch from GET /api/alerts/active "
            "to display current Grafana alerts."
        )

    def test_alert_status_panel_has_empty_state(self):
        """AC-12: AlertStatusPanel.tsx must have a 'No active alerts' empty state.

        RED: File does not yet exist.
        """
        if not self.COMPONENT.exists():
            pytest.skip("AlertStatusPanel.tsx not yet created")
        content = self.COMPONENT.read_text()
        has_empty_state = (
            "No active alerts" in content
            or "no active alerts" in content.lower()
            or "no alerts" in content.lower()
            or "All clear" in content
        )
        assert has_empty_state, (
            "AlertStatusPanel.tsx has no empty state message. "
            "AC-12 requires displaying 'No active alerts' (or equivalent) when count=0."
        )

    def test_alert_status_panel_is_exported(self):
        """AC-12: AlertStatusPanel must be exported so DashboardLayout can import it."""
        if not self.COMPONENT.exists():
            pytest.skip("AlertStatusPanel.tsx not yet created")
        content = self.COMPONENT.read_text()
        has_export = "export function AlertStatusPanel" in content or "export const AlertStatusPanel" in content
        assert has_export, (
            "AlertStatusPanel is not exported from AlertStatusPanel.tsx. "
            "Add: export function AlertStatusPanel() { ... }"
        )


# ---------------------------------------------------------------------------
# AC-12: BudgetGauge.tsx
# ---------------------------------------------------------------------------


class TestBudgetGauge:
    """AC-12: BudgetGauge.tsx must be created with correct content."""

    COMPONENT = FRONTEND_COMPONENTS / "BudgetGauge.tsx"

    def test_budget_gauge_exists(self):
        """AC-12: BudgetGauge.tsx must be created in frontend/src/components/.

        RED: File does not yet exist.
        GREEN after: Create frontend/src/components/BudgetGauge.tsx.

        This component displays a token budget gauge showing percent_used from QuotaInfo.
        """
        assert self.COMPONENT.exists(), (
            f"BudgetGauge.tsx not found at {self.COMPONENT}. "
            "AC-12 requires creating this React component. "
            "It should display a visual gauge of token budget consumption."
        )

    def test_budget_gauge_references_budget_or_percent_used(self):
        """AC-12: BudgetGauge.tsx must reference percent_used or budget.

        RED: File does not yet exist.
        GREEN after: The component receives percent_used (from QuotaInfo) as a prop.
        """
        if not self.COMPONENT.exists():
            pytest.skip("BudgetGauge.tsx not yet created")
        content = self.COMPONENT.read_text()
        has_budget_ref = (
            "percent_used" in content
            or "percentUsed" in content
            or "budget" in content.lower()
        )
        assert has_budget_ref, (
            "BudgetGauge.tsx does not reference 'percent_used', 'percentUsed', or 'budget'. "
            "AC-12 requires the component to display budget consumption. "
            "It should accept a percent_used prop from QuotaInfo."
        )

    def test_budget_gauge_is_exported(self):
        """AC-12: BudgetGauge must be exported."""
        if not self.COMPONENT.exists():
            pytest.skip("BudgetGauge.tsx not yet created")
        content = self.COMPONENT.read_text()
        has_export = "export function BudgetGauge" in content or "export const BudgetGauge" in content
        assert has_export, (
            "BudgetGauge is not exported from BudgetGauge.tsx. "
            "Add: export function BudgetGauge({ ... }: BudgetGaugeProps) { ... }"
        )


# ---------------------------------------------------------------------------
# AC-12: DispatchQueue.tsx — 'paused' status badge
# ---------------------------------------------------------------------------


class TestDispatchQueuePausedStatus:
    """AC-12: DispatchQueue.tsx must handle 'paused' status in statusBadge().

    STORY-507 added the 'paused' status to DispatchItem, but the frontend
    statusBadge() function still has no 'paused' case — it falls through to
    the default grey badge.
    """

    COMPONENT = FRONTEND_COMPONENTS / "DispatchQueue.tsx"

    def test_dispatch_queue_exists(self):
        """Prerequisite: DispatchQueue.tsx must exist (it already does)."""
        assert self.COMPONENT.exists(), (
            f"DispatchQueue.tsx not found at {self.COMPONENT}. "
            "This is a pre-existing file — it should not have been deleted."
        )

    def test_dispatch_queue_has_paused_case_in_status_badge(self):
        """AC-12: statusBadge() in DispatchQueue.tsx must have an explicit 'paused' case.

        RED: Currently statusBadge() only handles: pending, claimed, completed, cancelled.
        The 'paused' status (added by STORY-507) falls through to the grey default.

        GREEN after: Add:
          case 'paused':
            return { className: 'bg-purple-600/20 text-purple-400', label: 'paused' };
        """
        if not self.COMPONENT.exists():
            pytest.skip("DispatchQueue.tsx not found")
        content = self.COMPONENT.read_text()
        assert "case 'paused':" in content, (
            "DispatchQueue.tsx statusBadge() has no 'paused' case. "
            "AC-12 requires adding:\n"
            "  case 'paused':\n"
            "    return { className: 'bg-purple-600/20 text-purple-400', label: 'paused' };\n"
            "STORY-507 added the paused status but the frontend was not updated."
        )

    def test_dispatch_queue_paused_has_distinct_color(self):
        """AC-12: The 'paused' badge must have a distinct color (purple per spec).

        Using a distinct color prevents paused stories from being confused with
        pending (yellow), claimed (blue), completed (green), or cancelled (red).

        RED: 'paused' case does not yet exist.
        """
        if not self.COMPONENT.exists():
            pytest.skip("DispatchQueue.tsx not found")
        content = self.COMPONENT.read_text()
        if "case 'paused':" not in content:
            pytest.fail(
                "DispatchQueue.tsx has no 'paused' case in statusBadge(). "
                "Add the 'paused' case with bg-purple styling before testing color."
            )
        # Find the paused case and check the styling within a reasonable window
        paused_idx = content.find("case 'paused':")
        window = content[paused_idx : paused_idx + 200]
        has_purple = "purple" in window or "violet" in window
        assert has_purple, (
            "The 'paused' case in statusBadge() does not use purple/violet styling. "
            "AC-12 spec requires: className: 'bg-purple-600/20 text-purple-400'. "
            f"Found near 'case paused':\n{window}"
        )

    def test_dispatch_queue_paused_label(self):
        """AC-12: The 'paused' badge label must say 'paused'."""
        if not self.COMPONENT.exists():
            pytest.skip("DispatchQueue.tsx not found")
        content = self.COMPONENT.read_text()
        if "case 'paused':" not in content:
            pytest.skip("'paused' case not yet added")
        paused_idx = content.find("case 'paused':")
        window = content[paused_idx : paused_idx + 200]
        assert "label: 'paused'" in window or 'label: "paused"' in window, (
            "The 'paused' statusBadge case must set label: 'paused'. "
            f"Found near 'case paused':\n{window}"
        )


# ---------------------------------------------------------------------------
# AC-12: DashboardLayout.tsx — imports AlertStatusPanel and BudgetGauge
# ---------------------------------------------------------------------------


class TestDashboardLayoutImports:
    """AC-12: DashboardLayout.tsx must import AlertStatusPanel and BudgetGauge."""

    COMPONENT = FRONTEND_COMPONENTS / "DashboardLayout.tsx"

    def test_dashboard_layout_exists(self):
        """Prerequisite: DashboardLayout.tsx must exist (it already does)."""
        assert self.COMPONENT.exists(), (
            f"DashboardLayout.tsx not found at {self.COMPONENT}. "
            "This is a pre-existing file — it should not have been deleted."
        )

    def test_dashboard_layout_imports_alert_status_panel(self):
        """AC-12: DashboardLayout.tsx must import AlertStatusPanel.

        RED: AlertStatusPanel does not yet exist, and DashboardLayout has no such import.
        GREEN after: Add import + usage of AlertStatusPanel in DashboardLayout.
        """
        if not self.COMPONENT.exists():
            pytest.skip("DashboardLayout.tsx not found")
        content = self.COMPONENT.read_text()
        assert "AlertStatusPanel" in content, (
            "DashboardLayout.tsx does not import or reference AlertStatusPanel. "
            "AC-12 requires adding:\n"
            "  import { AlertStatusPanel } from './AlertStatusPanel';\n"
            "and including <AlertStatusPanel /> in the layout."
        )

    def test_dashboard_layout_imports_budget_gauge(self):
        """AC-12: DashboardLayout.tsx must import BudgetGauge.

        RED: BudgetGauge does not yet exist, and DashboardLayout has no such import.
        GREEN after: Add import + usage of BudgetGauge in DashboardLayout.
        """
        if not self.COMPONENT.exists():
            pytest.skip("DashboardLayout.tsx not found")
        content = self.COMPONENT.read_text()
        assert "BudgetGauge" in content, (
            "DashboardLayout.tsx does not import or reference BudgetGauge. "
            "AC-12 requires adding:\n"
            "  import { BudgetGauge } from './BudgetGauge';\n"
            "and including <BudgetGauge /> in the layout."
        )

    def test_dashboard_layout_alert_panel_is_not_just_commented(self):
        """AC-12: AlertStatusPanel must be an active import, not a comment."""
        if not self.COMPONENT.exists():
            pytest.skip("DashboardLayout.tsx not found")
        content = self.COMPONENT.read_text()
        lines_with_alert = [
            line for line in content.splitlines()
            if "AlertStatusPanel" in line
        ]
        active_lines = [
            line for line in lines_with_alert
            if not line.strip().startswith("//") and not line.strip().startswith("*")
        ]
        assert len(active_lines) > 0, (
            "AlertStatusPanel is only referenced in comments in DashboardLayout.tsx. "
            "The import must be an active (uncommented) import statement."
        )

    def test_dashboard_layout_budget_gauge_is_not_just_commented(self):
        """AC-12: BudgetGauge must be an active import, not a comment."""
        if not self.COMPONENT.exists():
            pytest.skip("DashboardLayout.tsx not found")
        content = self.COMPONENT.read_text()
        lines_with_gauge = [
            line for line in content.splitlines()
            if "BudgetGauge" in line
        ]
        active_lines = [
            line for line in lines_with_gauge
            if not line.strip().startswith("//") and not line.strip().startswith("*")
        ]
        assert len(active_lines) > 0, (
            "BudgetGauge is only referenced in comments in DashboardLayout.tsx. "
            "The import must be an active (uncommented) import statement."
        )
