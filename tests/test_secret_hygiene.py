"""Tests for STORY-011: Runtime Secret Hygiene & Config Exposure Audit.

Phase 7 test design — 19 tests covering all 9 acceptance criteria.
"""

from __future__ import annotations

import json
import os

import pytest

from tech_dev_agents.secret_hygiene import (
    AuditResult,
    SecretEntry,
    SecretInventory,
    SecretValue,
    audit_config_exposure,
    build_default_inventory,
    build_safe_env,
    redact_secrets,
    DEFAULT_SAFE_ENV_KEYS,
    REDACTED,
)


# ---------------------------------------------------------------------------
# Group 1 — SecretValue Wrapper (AC1, AC2)
# ---------------------------------------------------------------------------


class TestSecretValueReprStr:
    """T01-T03, T06: SecretValue redaction and immutability."""

    def test_secret_value_repr_is_redacted(self) -> None:
        """T01: repr() never exposes the actual value."""
        sv = SecretValue("hunter2")
        assert "hunter2" not in repr(sv)
        assert "***" in repr(sv)

    def test_secret_value_str_is_redacted(self) -> None:
        """T02: str() returns the redaction placeholder."""
        sv = SecretValue("super-secret-key-12345")
        assert str(sv) == REDACTED
        assert "super-secret" not in str(sv)

    def test_secret_value_expose_returns_raw(self) -> None:
        """T03: .expose() is the only way to get the raw value."""
        raw = "ghp_abc123XYZ"
        sv = SecretValue(raw)
        assert sv.expose() == raw

    def test_secret_value_immutable(self) -> None:
        """T06: Attribute assignment after construction raises AttributeError."""
        sv = SecretValue("value")
        with pytest.raises(AttributeError):
            sv._value = "new_value"  # type: ignore[misc]
        with pytest.raises(AttributeError):
            sv.anything = "nope"  # type: ignore[attr-defined]


class TestSecretValueEqualityHashing:
    """T04-T05: SecretValue equality and hashing."""

    def test_secret_value_equality(self) -> None:
        """T04: Equality is based on the underlying value."""
        assert SecretValue("abc") == SecretValue("abc")
        assert SecretValue("abc") != SecretValue("xyz")
        assert SecretValue("abc") != "abc"  # Not equal to raw strings

    def test_secret_value_hashable(self) -> None:
        """T05: Same value produces same hash; usable as dict key."""
        a = SecretValue("token-123")
        b = SecretValue("token-123")
        assert hash(a) == hash(b)
        # Can be used as dict key
        d = {a: "found"}
        assert d[b] == "found"


# ---------------------------------------------------------------------------
# Group 2 — Text Redaction (AC3)
# ---------------------------------------------------------------------------


class TestRedactSecrets:
    """T07-T10: Text redaction function."""

    def test_redact_single_secret(self) -> None:
        """T07: A single secret occurrence is masked."""
        text = "Connecting with token ghp_abc123 to GitHub"
        result = redact_secrets(text, ["ghp_abc123"])
        assert result == f"Connecting with token {REDACTED} to GitHub"
        assert "ghp_abc123" not in result

    def test_redact_multiple_secrets(self) -> None:
        """T08: Multiple different secrets are all masked."""
        text = "key=SECRET1 and password=SECRET2"
        result = redact_secrets(text, ["SECRET1", "SECRET2"])
        assert "SECRET1" not in result
        assert "SECRET2" not in result
        assert result.count(REDACTED) == 2

    def test_redact_no_match_returns_original(self) -> None:
        """T09: Text without any secret occurrences is unchanged."""
        text = "Nothing secret here"
        result = redact_secrets(text, ["not-present"])
        assert result == text

    def test_redact_accepts_secret_value_objects(self) -> None:
        """T10: SecretValue instances are accepted and their values extracted."""
        text = "Bearer my-token-xyz"
        result = redact_secrets(text, [SecretValue("my-token-xyz")])
        assert "my-token-xyz" not in result
        assert REDACTED in result


# ---------------------------------------------------------------------------
# Group 3 — Environment Allowlist (AC4)
# ---------------------------------------------------------------------------


class TestBuildSafeEnv:
    """T11-T13: Environment allowlist filtering."""

    def test_build_safe_env_filters_to_allowed_keys(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """T11: Only specified keys appear in the result."""
        monkeypatch.setenv("SAFE_VAR", "safe")
        monkeypatch.setenv("SECRET_VAR", "secret")
        result = build_safe_env(allowed_keys=["SAFE_VAR"])
        assert "SAFE_VAR" in result
        assert result["SAFE_VAR"] == "safe"
        assert "SECRET_VAR" not in result

    def test_build_safe_env_default_keys_excludes_secrets(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """T12: Default allowlist does not include secret-bearing variable names."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret")
        monkeypatch.setenv("PATH", "/usr/bin")
        result = build_safe_env()
        assert "ANTHROPIC_API_KEY" not in result
        assert "GITHUB_TOKEN" not in result
        # PATH should be in defaults
        assert "PATH" in result

    def test_build_safe_env_extra_keys_extends_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """T13: extra_keys parameter adds to the defaults."""
        monkeypatch.setenv("MY_CUSTOM_VAR", "custom")
        monkeypatch.setenv("PATH", "/usr/bin")
        result = build_safe_env(extra_keys=["MY_CUSTOM_VAR"])
        assert "MY_CUSTOM_VAR" in result
        assert "PATH" in result


# ---------------------------------------------------------------------------
# Group 4 — Secret Inventory (AC5, AC6)
# ---------------------------------------------------------------------------


class TestSecretInventory:
    """T14-T16: Inventory data structure and defaults."""

    def test_secret_entry_is_frozen(self) -> None:
        """T14: SecretEntry is a frozen dataclass."""
        entry = SecretEntry(
            name="test-secret",
            consuming_modules=("module_a",),
            exposure_boundary="env",
            classification="token",
        )
        with pytest.raises(AttributeError):
            entry.name = "changed"  # type: ignore[misc]

    def test_build_default_inventory_covers_all_required_secrets(self) -> None:
        """T15: Default inventory covers all 5 required secrets + Monday.com key."""
        from tech_dev_agents.runtime_identity import DEFAULT_REQUIRED_SECRETS

        inventory = build_default_inventory()
        inventory_names = {e.name for e in inventory.entries}

        for secret_name in DEFAULT_REQUIRED_SECRETS:
            assert secret_name in inventory_names, f"Missing: {secret_name}"

        # Monday.com key (not in DEFAULT_REQUIRED_SECRETS)
        assert "monday-api-key" in inventory_names

        # Total: 6 secrets
        assert len(inventory.entries) == 6

    def test_default_inventory_entries_have_valid_fields(self) -> None:
        """T16: Every entry has non-empty required fields."""
        inventory = build_default_inventory()
        for entry in inventory.entries:
            assert entry.name, "name must be non-empty"
            assert entry.consuming_modules, "consuming_modules must be non-empty"
            assert entry.exposure_boundary, "exposure_boundary must be non-empty"
            assert entry.classification, "classification must be non-empty"


# ---------------------------------------------------------------------------
# Group 5 — Config Exposure Audit (AC7, AC8)
# ---------------------------------------------------------------------------


class TestAuditConfigExposure:
    """T17-T19: Audit function and serialization."""

    def test_audit_all_loaded_returns_clean(self) -> None:
        """T17: All secrets loaded -> is_clean=True, no unloaded."""
        inventory = build_default_inventory()
        all_names = [e.name for e in inventory.entries]
        result = audit_config_exposure(inventory, all_names)
        assert result.is_clean is True
        assert result.unloaded_secrets == ()
        assert result.loaded_count == len(all_names)
        assert result.total_secrets == len(inventory.entries)

    def test_audit_missing_secret_returns_not_clean(self) -> None:
        """T18: Missing secret -> is_clean=False, unloaded_secrets populated."""
        inventory = build_default_inventory()
        # Load all except the first
        all_names = [e.name for e in inventory.entries]
        partial = all_names[1:]
        result = audit_config_exposure(inventory, partial)
        assert result.is_clean is False
        assert all_names[0] in result.unloaded_secrets

    def test_audit_result_to_audit_dict(self) -> None:
        """T19: to_audit_dict() returns JSON-serializable dict with expected keys."""
        inventory = build_default_inventory()
        all_names = [e.name for e in inventory.entries]
        result = audit_config_exposure(inventory, all_names)
        audit_dict = result.to_audit_dict()

        # Must be JSON-serializable
        serialized = json.dumps(audit_dict)
        assert isinstance(serialized, str)

        # Expected keys
        expected_keys = {
            "total_secrets",
            "loaded_count",
            "unloaded_secrets",
            "exposure_summary",
            "is_clean",
            "audited_at",
        }
        assert expected_keys <= set(audit_dict.keys())
