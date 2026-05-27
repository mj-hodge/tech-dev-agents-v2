"""Runtime identity contract for STORY-001.

This module is intentionally stubbed during Phase 7 so the tests can prove
the contract before Phase 8 replaces the stub with the actual implementation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Callable, Mapping


NON_ROOT_RUNTIME_USER = "agent"
NON_ROOT_RUNTIME_GROUP = "agent"
DEFAULT_WORKSPACE_PATH = "/workspace"
DEFAULT_HEALTH_PATH = "/api/health"

DEFAULT_REQUIRED_SECRETS = (
    "anthropic-api-key",
    "bot-app-id",
    "bot-app-password",
    "github-token",
    "appinsights-connection-string",
)


class RuntimeIdentityError(Exception):
    """Base error for runtime identity contract failures."""


class SecretLoadError(RuntimeIdentityError):
    """Raised when a secret cannot be loaded from the provider."""


class MissingSecretError(SecretLoadError):
    """Raised when a required secret is missing or blank."""


class SecretValidationError(RuntimeIdentityError):
    """Raised when a required secret set is incomplete or invalid."""


@dataclass(frozen=True, slots=True)
class RuntimeSecurityContext:
    run_as_non_root: bool
    allow_privilege_escalation: bool
    read_only_root_filesystem: bool
    capabilities_drop: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class RuntimeIdentityContract:
    user: str
    group: str
    workspace_path: str
    health_path: str
    port: int
    security_context: RuntimeSecurityContext


@dataclass(frozen=True, slots=True)
class HealthContract:
    ready: bool
    status: str
    health_path: str
    missing_secrets: tuple[str, ...] = field(default_factory=tuple)


def build_runtime_contract() -> RuntimeIdentityContract:
    return RuntimeIdentityContract(
        user=NON_ROOT_RUNTIME_USER,
        group=NON_ROOT_RUNTIME_GROUP,
        workspace_path=DEFAULT_WORKSPACE_PATH,
        health_path=DEFAULT_HEALTH_PATH,
        port=3978,
        security_context=RuntimeSecurityContext(
            run_as_non_root=True,
            allow_privilege_escalation=False,
            read_only_root_filesystem=True,
            capabilities_drop=("ALL",),
        ),
    )


def load_required_secrets(
    fetch_secret: Callable[[str], str | None],
    required_secret_names: tuple[str, ...] = DEFAULT_REQUIRED_SECRETS,
) -> dict[str, str]:
    loaded: dict[str, str] = {}
    provider_errors: list[Exception] = []

    for secret_name in required_secret_names:
        try:
            value = fetch_secret(secret_name)
        except Exception as exc:  # pragma: no cover - provider path exercised in tests
            provider_errors.append(exc)
            continue

        if value is None or not str(value).strip():
            raise MissingSecretError(f"Required secret '{secret_name}' is missing or blank.")

        loaded[secret_name] = str(value)

    if provider_errors:
        raise SecretLoadError("One or more secrets could not be loaded.") from provider_errors[0]

    return validate_required_secrets(loaded, required_secret_names=required_secret_names)


def validate_required_secrets(
    secrets: Mapping[str, str | None],
    required_secret_names: tuple[str, ...] = DEFAULT_REQUIRED_SECRETS,
) -> dict[str, str]:
    normalized: dict[str, str] = {}
    missing: list[str] = []
    blank: list[str] = []

    for secret_name in required_secret_names:
        if secret_name not in secrets:
            missing.append(secret_name)
            continue

        value = secrets[secret_name]
        if value is None or not str(value).strip():
            blank.append(secret_name)
            continue

        normalized[secret_name] = str(value)

    if blank:
        raise MissingSecretError(
            "Required secrets cannot be blank: " + ", ".join(sorted(blank))
        )

    if missing:
        if len(missing) == 1:
            raise MissingSecretError(f"Required secret '{missing[0]}' is missing.")
        raise SecretValidationError(
            "Required secrets are missing: " + ", ".join(sorted(missing))
        )

    return normalized


def build_log_safe_metadata(
    runtime_contract: RuntimeIdentityContract,
    loaded_secrets: Mapping[str, str],
) -> dict[str, object]:
    return {
        "runtime": {
            "user": runtime_contract.user,
            "group": runtime_contract.group,
            "workspace_path": runtime_contract.workspace_path,
            "health_path": runtime_contract.health_path,
            "port": runtime_contract.port,
            "security_context": asdict(runtime_contract.security_context),
        },
        "secrets": {
            "loaded": bool(loaded_secrets),
            "count": len(DEFAULT_REQUIRED_SECRETS),
            "names": tuple(sorted(loaded_secrets.keys())),
        },
    }


def build_health_payload(
    runtime_contract: RuntimeIdentityContract,
    *,
    secrets_loaded: bool,
    missing_secrets: tuple[str, ...] = (),
) -> HealthContract:
    ready = bool(secrets_loaded) and not missing_secrets
    return HealthContract(
        ready=ready,
        status="healthy" if ready else "unhealthy",
        health_path=runtime_contract.health_path,
        missing_secrets=tuple(sorted(missing_secrets)),
    )
