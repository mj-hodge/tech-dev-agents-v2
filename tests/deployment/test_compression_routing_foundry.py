"""Tests for compression routing to Foundry — hermes-config.yaml validation.

STORY-556: Fleet Reliability Test Backfill — AC-2
Incident 2026-04-18: Context compression was using default Anthropic API instead
of Azure Foundry endpoint, causing auth failures mid-session.

These tests parse the actual YAML config files and assert compression is enabled,
thresholds are valid, and the VM config routes through Azure Foundry.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
VM_CONFIG = REPO_ROOT / "deployment" / "vm" / "hermes-config.yaml"
HERMES_CONFIG = REPO_ROOT / "deployment" / "hermes" / "hermes-config.yaml"


def _load_yaml(path: Path) -> dict:
    """Load a YAML file and return parsed dict."""
    with open(path) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCompressionRoutingVM:
    """AC-2: VM config (deployment/vm/hermes-config.yaml) compression settings."""

    def test_vm_config_exists(self):
        """The VM hermes-config.yaml must exist."""
        assert VM_CONFIG.exists(), f"VM config not found at {VM_CONFIG}"

    def test_vm_compression_enabled(self):
        """compression.enabled must be true in VM config."""
        cfg = _load_yaml(VM_CONFIG)
        assert cfg.get("compression", {}).get("enabled") is True, (
            "compression.enabled must be true in VM config"
        )

    def test_vm_compression_threshold_valid(self):
        """compression.threshold must be between 0.0 and 1.0 inclusive."""
        cfg = _load_yaml(VM_CONFIG)
        threshold = cfg.get("compression", {}).get("threshold")
        assert threshold is not None, "compression.threshold must be set"
        assert 0.0 <= threshold <= 1.0, (
            f"compression.threshold={threshold} out of valid range [0.0, 1.0]"
        )

    def test_vm_compression_summary_model_set(self):
        """compression.summary_model must be set in VM config."""
        cfg = _load_yaml(VM_CONFIG)
        model = cfg.get("compression", {}).get("summary_model")
        assert model is not None and model.strip(), (
            "compression.summary_model must be set in VM config (routes through Foundry)"
        )

    def test_vm_compression_summary_provider_is_anthropic(self):
        """compression.summary_provider must be 'anthropic' in VM config."""
        cfg = _load_yaml(VM_CONFIG)
        provider = cfg.get("compression", {}).get("summary_provider")
        assert provider == "anthropic", (
            f"compression.summary_provider={provider!r}, expected 'anthropic' "
            "(routed through Azure Foundry)"
        )

    def test_vm_base_url_is_foundry(self):
        """model.base_url must contain cognitiveservices.azure.com in VM config."""
        cfg = _load_yaml(VM_CONFIG)
        base_url = cfg.get("model", {}).get("base_url", "")
        assert "cognitiveservices.azure.com" in base_url, (
            f"model.base_url={base_url!r} does not point at Azure Foundry"
        )


class TestCompressionRoutingHermes:
    """AC-2: Hermes config (deployment/hermes/hermes-config.yaml) compression settings."""

    def test_hermes_config_exists(self):
        """The Hermes hermes-config.yaml must exist."""
        assert HERMES_CONFIG.exists(), f"Hermes config not found at {HERMES_CONFIG}"

    def test_hermes_compression_enabled(self):
        """compression.enabled must be true in Hermes config."""
        cfg = _load_yaml(HERMES_CONFIG)
        assert cfg.get("compression", {}).get("enabled") is True, (
            "compression.enabled must be true in Hermes config"
        )

    def test_hermes_compression_threshold_valid(self):
        """compression.threshold must be between 0.0 and 1.0 inclusive."""
        cfg = _load_yaml(HERMES_CONFIG)
        threshold = cfg.get("compression", {}).get("threshold")
        assert threshold is not None, "compression.threshold must be set"
        assert 0.0 <= threshold <= 1.0, (
            f"compression.threshold={threshold} out of valid range [0.0, 1.0]"
        )
