"""Runtime Secret Hygiene & Config Exposure Audit for STORY-011.

This module provides utilities for preventing secret leakage at runtime:

- SecretValue: wraps a secret string and redacts it in repr/str contexts
- redact_secrets(): replaces known secret values in arbitrary text
- build_safe_env(): filters os.environ to a safe allowlist for subprocess use
- SecretEntry/SecretInventory: declarative inventory of platform secrets
- audit_config_exposure(): produces a structured audit of secret coverage

Key design decisions (see analysis.md):
- SecretValue is a plain __slots__ class (not a dataclass) to prevent asdict() leakage
- Text redaction uses simple string replacement, longest-first ordering
- Environment allowlist defaults to ~12 non-secret standard variables
- Default inventory covers all 6 secrets used by the tech-dev-agents platform
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REDACTED = "***"
"""Consistent redaction placeholder used across the codebase."""

DEFAULT_SAFE_ENV_KEYS: tuple[str, ...] = (
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TERM",
    "SHELL",
    "TZ",
    "TMPDIR",
    "TEMP",
    "TMP",
    "REPO_PATH",
)
"""Default non-secret environment variables safe for subprocess invocation."""


# ---------------------------------------------------------------------------
# SecretValue
# ---------------------------------------------------------------------------


class SecretValue:
    """Wraps a secret string and redacts it in repr/str contexts.

    The raw value is only accessible via the explicit `expose()` method.
    This class is immutable after construction: attribute assignment raises
    AttributeError.

    Usage::

        token = SecretValue("ghp_abc123")
        print(token)        # prints "***"
        repr(token)         # "SecretValue('***')"
        token.expose()      # "ghp_abc123"
    """

    __slots__ = ("_value", "_hash")

    def __init__(self, value: str) -> None:
        object.__setattr__(self, "_value", value)
        object.__setattr__(self, "_hash", hash(value))

    def expose(self) -> str:
        """Return the raw secret value. Use with care."""
        return self._value

    def __repr__(self) -> str:
        return f"SecretValue('{REDACTED}')"

    def __str__(self) -> str:
        return REDACTED

    def __eq__(self, other: object) -> bool:
        if isinstance(other, SecretValue):
            return self._value == other._value
        return NotImplemented

    def __hash__(self) -> int:
        return self._hash

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError(
            f"'{type(self).__name__}' object does not support attribute assignment"
        )


# ---------------------------------------------------------------------------
# Text redaction
# ---------------------------------------------------------------------------


def redact_secrets(
    text: str,
    secrets: Iterable[str | SecretValue],
) -> str:
    """Replace all known secret values in text with the redaction placeholder.

    Secrets are replaced longest-first to prevent partial-match issues
    (e.g., a short secret being a substring of a longer one).

    Args:
        text: The string to scan for secrets.
        secrets: Known secret values (strings or SecretValue instances).

    Returns:
        A new string with all secret occurrences replaced by ``REDACTED``.
    """
    # Extract raw values, handling both str and SecretValue
    raw_values: list[str] = []
    for secret in secrets:
        if isinstance(secret, SecretValue):
            raw_values.append(secret.expose())
        else:
            raw_values.append(str(secret))

    # Sort longest-first to prevent partial-match issues
    raw_values.sort(key=len, reverse=True)

    result = text
    for value in raw_values:
        if value:  # Skip empty strings
            result = result.replace(value, REDACTED)
    return result


# ---------------------------------------------------------------------------
# Environment allowlist
# ---------------------------------------------------------------------------


def build_safe_env(
    allowed_keys: Iterable[str] | None = None,
    *,
    extra_keys: Iterable[str] | None = None,
) -> dict[str, str]:
    """Return a filtered copy of os.environ containing only allowed keys.

    Prevents secret bleed into subprocess invocations by allowlisting
    environment variables.

    Args:
        allowed_keys: Explicit set of keys to include. If None, uses
            ``DEFAULT_SAFE_ENV_KEYS``.
        extra_keys: Additional keys to include on top of allowed_keys.

    Returns:
        Dict of environment variables filtered to the allowed set.
        Missing keys are silently skipped.
    """
    keys: set[str] = set(allowed_keys) if allowed_keys is not None else set(DEFAULT_SAFE_ENV_KEYS)
    if extra_keys is not None:
        keys.update(extra_keys)

    return {key: os.environ[key] for key in keys if key in os.environ}


# ---------------------------------------------------------------------------
# Secret inventory
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SecretEntry:
    """Declares a secret's metadata for inventory and audit purposes.

    Attributes:
        name: The secret identifier (e.g., "anthropic-api-key").
        consuming_modules: Tuple of module names that use this secret.
        exposure_boundary: How the secret leaves the process boundary
            ("env", "http-header", "clone-url", "subprocess", "log", "config").
        classification: Type of secret ("credential", "token", "connection-string").
    """

    name: str
    consuming_modules: tuple[str, ...]
    exposure_boundary: str
    classification: str


@dataclass(frozen=True)
class SecretInventory:
    """Versioned, immutable collection of SecretEntry records.

    Attributes:
        entries: Tuple of SecretEntry records.
        version: Manifest version for audit trail.
    """

    entries: tuple[SecretEntry, ...]
    version: str

    @property
    def secret_names(self) -> frozenset[str]:
        """Return the set of all secret names in the inventory."""
        return frozenset(e.name for e in self.entries)


_DEFAULT_INVENTORY_ENTRIES: tuple[SecretEntry, ...] = (
    SecretEntry(
        name="anthropic-api-key",
        consuming_modules=("claude_runner",),
        exposure_boundary="subprocess",
        classification="token",
    ),
    SecretEntry(
        name="bot-app-id",
        consuming_modules=("webhook_auth", "teams_bot"),
        exposure_boundary="config",
        classification="credential",
    ),
    SecretEntry(
        name="bot-app-password",
        consuming_modules=("teams_bot",),
        exposure_boundary="http-header",
        classification="credential",
    ),
    SecretEntry(
        name="github-token",
        consuming_modules=("git_workflow",),
        exposure_boundary="clone-url",
        classification="token",
    ),
    SecretEntry(
        name="appinsights-connection-string",
        consuming_modules=("loki_logging",),
        exposure_boundary="config",
        classification="connection-string",
    ),
    SecretEntry(
        name="monday-api-key",
        consuming_modules=("monday",),
        exposure_boundary="http-header",
        classification="token",
    ),
)


def build_default_inventory() -> SecretInventory:
    """Factory for the default tech-dev-agents secret inventory.

    Returns an inventory covering all 6 secrets used by the platform:
    the 5 required secrets from runtime_identity.DEFAULT_REQUIRED_SECRETS
    plus the Monday.com API key.
    """
    return SecretInventory(
        entries=_DEFAULT_INVENTORY_ENTRIES,
        version="1.0",
    )


# ---------------------------------------------------------------------------
# Config exposure audit
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuditResult:
    """Outcome of a config exposure audit.

    Attributes:
        total_secrets: Number of secrets in the inventory.
        loaded_count: Number of secrets actually loaded at runtime.
        unloaded_secrets: Names of secrets not loaded.
        exposure_summary: Per-secret boundary and module info.
        is_clean: True if all inventory secrets are loaded (full coverage).
        audited_at: ISO 8601 timestamp of the audit.
    """

    total_secrets: int
    loaded_count: int
    unloaded_secrets: tuple[str, ...]
    exposure_summary: tuple[dict[str, Any], ...]
    is_clean: bool
    audited_at: str

    def to_audit_dict(self) -> dict[str, Any]:
        """Serialize to a dict suitable for structured logging.

        All values are JSON-serializable.
        """
        return {
            "total_secrets": self.total_secrets,
            "loaded_count": self.loaded_count,
            "unloaded_secrets": list(self.unloaded_secrets),
            "exposure_summary": list(self.exposure_summary),
            "is_clean": self.is_clean,
            "audited_at": self.audited_at,
        }


def audit_config_exposure(
    inventory: SecretInventory,
    loaded_secret_names: Iterable[str],
) -> AuditResult:
    """Produce a structured audit of secret coverage.

    Compares loaded secrets against the inventory to identify gaps.

    Args:
        inventory: The secret inventory to audit against.
        loaded_secret_names: Names of secrets that are actually loaded.

    Returns:
        AuditResult with coverage analysis and per-secret exposure summary.
    """
    loaded_set = frozenset(loaded_secret_names)
    inventory_names = inventory.secret_names

    unloaded = tuple(sorted(inventory_names - loaded_set))

    exposure_summary: list[dict[str, Any]] = []
    for entry in inventory.entries:
        exposure_summary.append({
            "name": entry.name,
            "loaded": entry.name in loaded_set,
            "consuming_modules": list(entry.consuming_modules),
            "exposure_boundary": entry.exposure_boundary,
            "classification": entry.classification,
        })

    now = datetime.now(timezone.utc).isoformat()

    return AuditResult(
        total_secrets=len(inventory.entries),
        loaded_count=len(loaded_set & inventory_names),
        unloaded_secrets=unloaded,
        exposure_summary=tuple(exposure_summary),
        is_clean=len(unloaded) == 0,
        audited_at=now,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "AuditResult",
    "DEFAULT_SAFE_ENV_KEYS",
    "REDACTED",
    "SecretEntry",
    "SecretInventory",
    "SecretValue",
    "audit_config_exposure",
    "build_default_inventory",
    "build_safe_env",
    "redact_secrets",
]
