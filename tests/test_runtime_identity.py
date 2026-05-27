from __future__ import annotations

import json

import pytest

from tech_dev_agents.runtime_identity import (
    DEFAULT_REQUIRED_SECRETS,
    DEFAULT_HEALTH_PATH,
    DEFAULT_WORKSPACE_PATH,
    HealthContract,
    MissingSecretError,
    RuntimeIdentityContract,
    RuntimeSecurityContext,
    SecretLoadError,
    SecretValidationError,
    build_health_payload,
    build_log_safe_metadata,
    build_runtime_contract,
    load_required_secrets,
    validate_required_secrets,
)


def test_runtime_contract_uses_non_root_user_and_hardens_container() -> None:
    contract = build_runtime_contract()

    assert isinstance(contract, RuntimeIdentityContract)
    assert contract.user != "root"
    assert contract.group != "root"
    assert contract.user == "agent"
    assert contract.group == "agent"
    assert contract.workspace_path == DEFAULT_WORKSPACE_PATH
    assert contract.health_path == DEFAULT_HEALTH_PATH
    assert contract.port == 3978
    assert isinstance(contract.security_context, RuntimeSecurityContext)
    assert contract.security_context.run_as_non_root is True
    assert contract.security_context.allow_privilege_escalation is False
    assert contract.security_context.read_only_root_filesystem is True
    assert "ALL" in contract.security_context.capabilities_drop


def test_load_required_secrets_reads_each_required_secret() -> None:
    seen: list[str] = []

    values = {
        "anthropic-api-key": "anthropic-value",
        "bot-app-id": "app-id-value",
        "bot-app-password": "app-password-value",
        "github-token": "github-token-value",
        "appinsights-connection-string": "insights-value",
    }

    def fetch_secret(name: str) -> str:
        seen.append(name)
        return values[name]

    loaded = load_required_secrets(fetch_secret)

    assert tuple(seen) == DEFAULT_REQUIRED_SECRETS
    assert loaded == values


def test_load_required_secrets_wraps_provider_errors() -> None:
    def fetch_secret(_: str) -> str:
        raise RuntimeError("kv unavailable")

    with pytest.raises(SecretLoadError):
        load_required_secrets(fetch_secret)


def test_validate_required_secrets_rejects_missing_or_blank_values() -> None:
    with pytest.raises(MissingSecretError):
        validate_required_secrets(
            {
                "anthropic-api-key": "anthropic-value",
                "bot-app-id": "app-id-value",
                "bot-app-password": "app-password-value",
                "github-token": "github-token-value",
            }
        )

    with pytest.raises(MissingSecretError):
        validate_required_secrets(
            {
                "anthropic-api-key": "anthropic-value",
                "bot-app-id": "app-id-value",
                "bot-app-password": "app-password-value",
                "github-token": "github-token-value",
                "appinsights-connection-string": "   ",
            }
        )

    with pytest.raises(SecretValidationError):
        validate_required_secrets({"anthropic-api-key": "anthropic-value"})


def test_log_safe_metadata_excludes_secret_values() -> None:
    contract = build_runtime_contract()
    loaded_secrets = {
        "anthropic-api-key": "sk-ant-test-123",
        "bot-app-id": "app-id-value",
        "bot-app-password": "super-secret-password",
        "github-token": "github-token-value",
        "appinsights-connection-string": "InstrumentationKey=secret",
    }

    metadata = build_log_safe_metadata(contract, loaded_secrets)
    rendered = json.dumps(metadata, sort_keys=True)

    for secret_value in loaded_secrets.values():
        assert secret_value not in rendered

    assert metadata["runtime"]["user"] == "agent"
    assert metadata["runtime"]["health_path"] == DEFAULT_HEALTH_PATH
    assert metadata["secrets"]["count"] == len(DEFAULT_REQUIRED_SECRETS)
    assert metadata["secrets"]["loaded"] is True


def test_health_check_contract_reflects_secret_load_state() -> None:
    contract = build_runtime_contract()

    not_ready = build_health_payload(
        contract,
        secrets_loaded=False,
        missing_secrets=("anthropic-api-key", "github-token"),
    )
    ready = build_health_payload(contract, secrets_loaded=True)

    assert not_ready.ready is False
    assert not_ready.status == "unhealthy"
    assert not_ready.health_path == DEFAULT_HEALTH_PATH
    assert "anthropic-api-key" in not_ready.missing_secrets
    assert ready.ready is True
    assert ready.status == "healthy"
    assert ready.health_path == DEFAULT_HEALTH_PATH
    assert ready.missing_secrets == ()


# ---------------------------------------------------------------------------
# Phase 8 — additional coverage tests (gaps identified in Phase 7 review)
# ---------------------------------------------------------------------------


def test_load_required_secrets_rejects_none_from_provider() -> None:
    """load_required_secrets raises MissingSecretError when provider returns None."""

    def fetch_secret(name: str) -> str | None:
        if name == "github-token":
            return None
        return f"value-for-{name}"

    with pytest.raises(MissingSecretError, match="github-token"):
        load_required_secrets(fetch_secret)


def test_load_required_secrets_rejects_blank_from_provider() -> None:
    """load_required_secrets raises MissingSecretError when provider returns blank."""

    def fetch_secret(name: str) -> str | None:
        if name == "bot-app-password":
            return "   "
        return f"value-for-{name}"

    with pytest.raises(MissingSecretError, match="bot-app-password"):
        load_required_secrets(fetch_secret)


def test_validate_required_secrets_rejects_explicit_none_value() -> None:
    """validate_required_secrets rejects a dict entry with value=None."""
    secrets = {
        "anthropic-api-key": "val",
        "bot-app-id": "val",
        "bot-app-password": None,
        "github-token": "val",
        "appinsights-connection-string": "val",
    }
    with pytest.raises(MissingSecretError, match="bot-app-password"):
        validate_required_secrets(secrets)


def test_log_safe_metadata_contains_secret_names_not_values() -> None:
    """Metadata 'names' field lists secret names, never their values."""
    contract = build_runtime_contract()
    loaded_secrets = {
        "anthropic-api-key": "sk-ant-secret",
        "bot-app-id": "id-secret",
        "bot-app-password": "pw-secret",
        "github-token": "ghp-secret",
        "appinsights-connection-string": "InstrKey=secret",
    }

    metadata = build_log_safe_metadata(contract, loaded_secrets)
    names = metadata["secrets"]["names"]

    # All expected secret names are present
    assert set(names) == set(loaded_secrets.keys())
    # Names are sorted
    assert list(names) == sorted(loaded_secrets.keys())
    # No value leaked into the names tuple
    for val in loaded_secrets.values():
        assert val not in names


def test_health_payload_sorts_missing_secrets() -> None:
    """build_health_payload returns missing_secrets in sorted order."""
    contract = build_runtime_contract()
    payload = build_health_payload(
        contract,
        secrets_loaded=False,
        missing_secrets=("github-token", "anthropic-api-key", "bot-app-id"),
    )

    assert isinstance(payload, HealthContract)
    assert payload.missing_secrets == (
        "anthropic-api-key",
        "bot-app-id",
        "github-token",
    )
    assert payload.ready is False
    assert payload.status == "unhealthy"


def test_load_required_secrets_with_custom_secret_names() -> None:
    """load_required_secrets works with a custom required_secret_names tuple."""
    custom_names = ("secret-a", "secret-b")
    values = {"secret-a": "val-a", "secret-b": "val-b"}

    def fetch_secret(name: str) -> str:
        return values[name]

    loaded = load_required_secrets(fetch_secret, required_secret_names=custom_names)
    assert loaded == values


def test_validate_required_secrets_surfaces_all_missing_names() -> None:
    """When multiple secrets are missing, all names appear in the error."""
    with pytest.raises(SecretValidationError, match="anthropic-api-key") as exc_info:
        validate_required_secrets(
            {"bot-app-id": "val"},
            required_secret_names=("anthropic-api-key", "bot-app-id", "github-token"),
        )
    # Both missing names should appear
    msg = str(exc_info.value)
    assert "anthropic-api-key" in msg
    assert "github-token" in msg


def test_build_log_safe_metadata_with_empty_secrets() -> None:
    """Metadata reports loaded=False and count when no secrets are loaded."""
    contract = build_runtime_contract()
    metadata = build_log_safe_metadata(contract, {})

    assert metadata["secrets"]["loaded"] is False
    assert metadata["secrets"]["count"] == len(DEFAULT_REQUIRED_SECRETS)
    assert metadata["secrets"]["names"] == ()


@pytest.mark.smoke
def test_smoke_runtime_identity_contract() -> None:
    """Smoke test: core runtime identity contract builds and is consistent."""
    contract = build_runtime_contract()
    assert contract.user == "agent"
    assert contract.port == 3978

    # Health payload round-trips
    healthy = build_health_payload(contract, secrets_loaded=True)
    assert healthy.ready is True
    assert healthy.health_path == contract.health_path
