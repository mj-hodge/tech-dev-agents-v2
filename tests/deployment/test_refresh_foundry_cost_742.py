"""STORY-742: Verify refresh_foundry_cost query filters for AI Foundry resources only.

The cron script must include a ResourceType dimension filter so that non-Foundry
costs (VMs, storage, networking) are excluded at the API level and don't inflate
the "other" bucket on the dashboard.
"""

from __future__ import annotations

import importlib
import json
import types
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers — import the cron script as a module
# ---------------------------------------------------------------------------

def _load_refresh_module():
    """Import refresh_foundry_cost.py as a module for testing."""
    import importlib.util
    import os

    script_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "deployment",
        "ops-console",
        "scripts",
        "refresh_foundry_cost.py",
    )
    script_path = os.path.normpath(script_path)
    spec = importlib.util.spec_from_file_location("refresh_foundry_cost", script_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def refresh_mod():
    return _load_refresh_module()


# ---------------------------------------------------------------------------
# TC-1: Query body includes resource-type filter
# ---------------------------------------------------------------------------


class TestQueryFilter:
    """Verify the Cost Management query body includes a Foundry resource filter."""

    @pytest.mark.asyncio
    async def test_query_body_contains_resource_type_filter(self, refresh_mod):
        """The query body must filter on a ResourceType dimension to restrict
        results to AI Foundry resources (Microsoft.MachineLearningServices)."""

        captured_body = {}

        async def _mock_post(url, json=None, headers=None, timeout=None):
            captured_body.update(json or {})
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"properties": {"rows": []}}
            return resp

        mock_client = AsyncMock()
        mock_client.post = _mock_post

        await refresh_mod._query_cost_management(
            mock_client, "fake-token", "fake-sub", "2026-04-21", "2026-04-28"
        )

        # The body must have a filter clause
        dataset = captured_body.get("dataset", {})
        assert "filter" in dataset, (
            "Cost Management query body must include a 'filter' in the dataset "
            "to restrict results to AI Foundry resources"
        )

        # Verify the filter targets the right dimension
        filter_clause = dataset["filter"]
        filter_json = json.dumps(filter_clause).lower()
        assert "resourcetype" in filter_json or "resource type" in filter_json, (
            "Filter must reference the ResourceType dimension"
        )
        assert "machinelearningservices" in filter_json, (
            "Filter must include Microsoft.MachineLearningServices provider"
        )


# ---------------------------------------------------------------------------
# TC-2: Aggregation with mixed resource types
# ---------------------------------------------------------------------------


class TestAggregation:
    """Verify _aggregate_by_date handles rows correctly."""

    def test_aggregation_with_foundry_rows(self, refresh_mod):
        """Foundry deployment ResourceIds are classified into correct buckets."""
        rows = [
            [10.50, 20260425, "/subscriptions/s/resourceGroups/rg/providers/Microsoft.MachineLearningServices/workspaces/ws/deployments/claude-opus-4-6"],
            [5.25, 20260425, "/subscriptions/s/resourceGroups/rg/providers/Microsoft.MachineLearningServices/workspaces/ws/deployments/claude-sonnet-4-5"],
            [2.10, 20260425, "/subscriptions/s/resourceGroups/rg/providers/Microsoft.MachineLearningServices/workspaces/ws/deployments/claude-haiku-3-5"],
        ]

        result = refresh_mod._aggregate_by_date(rows)
        assert len(result) == 1

        day = result[0]
        assert day["usage_date"] == date(2026, 4, 25)
        assert day["opus_usd"] == 10.50
        assert day["sonnet_usd"] == 5.25
        assert day["haiku_usd"] == 2.10
        assert day["other_usd"] == 0.0

    def test_aggregation_empty_rows(self, refresh_mod):
        """Empty input returns empty output with no errors."""
        result = refresh_mod._aggregate_by_date([])
        assert result == []

    def test_aggregation_unknown_model_goes_to_other(self, refresh_mod):
        """ResourceIds that don't match known models go to 'other'."""
        rows = [
            [3.00, 20260425, "/subscriptions/s/resourceGroups/rg/providers/Microsoft.MachineLearningServices/workspaces/ws/deployments/gpt-4o-mini"],
        ]

        result = refresh_mod._aggregate_by_date(rows)
        assert len(result) == 1
        assert result[0]["other_usd"] == 3.00
        assert result[0]["opus_usd"] == 0.0


# ---------------------------------------------------------------------------
# TC-3: classify_resource_id regression guard
# ---------------------------------------------------------------------------


class TestClassifyResourceId:
    """Regression guard for classify_resource_id()."""

    @pytest.mark.parametrize(
        "resource_id, expected",
        [
            ("/deployments/claude-opus-4-6-20260301", "opus"),
            ("/deployments/claude-sonnet-4-5-20260301", "sonnet"),
            ("/deployments/claude-haiku-3-5-20260301", "haiku"),
            ("/deployments/gpt-4o-mini", "other"),
            ("/providers/Microsoft.Compute/virtualMachines/agent-vm", "other"),
            ("", "other"),
        ],
    )
    def test_classification(self, refresh_mod, resource_id, expected):
        from tech_dev_agents.ops_console.services.foundry_cost_service import (
            classify_resource_id,
        )

        assert classify_resource_id(resource_id) == expected
