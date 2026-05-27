"""Tests for Entra ID configuration settings — T88."""

from __future__ import annotations

import pytest


class TestEntraConfig:
    """T88: Entra ID settings in ops console config."""

    def test_settings_has_entra_tenant_id(self):
        """T88: Settings class accepts entra_tenant_id field."""
        from tech_dev_agents.ops_console.config import Settings

        settings = Settings(
            ops_console_api_key="test-key",
            loki_api_key="test-loki-key",
            agent_api_key="test-agent-key",
            entra_tenant_id="1060148b-e4f2-4e64-880e-b8b05958e6fe",
            entra_client_id="746105b5-1e11-4e75-9fd3-a27a355229fb",
        )
        assert settings.entra_tenant_id == "1060148b-e4f2-4e64-880e-b8b05958e6fe"

    def test_settings_has_entra_client_id(self):
        """T88b: Settings class accepts entra_client_id field."""
        from tech_dev_agents.ops_console.config import Settings

        settings = Settings(
            ops_console_api_key="test-key",
            loki_api_key="test-loki-key",
            agent_api_key="test-agent-key",
            entra_tenant_id="1060148b-e4f2-4e64-880e-b8b05958e6fe",
            entra_client_id="746105b5-1e11-4e75-9fd3-a27a355229fb",
        )
        assert settings.entra_client_id == "746105b5-1e11-4e75-9fd3-a27a355229fb"
