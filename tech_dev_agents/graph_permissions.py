"""Teams Least-Privilege Graph Permissions for STORY-009.

This module provides a declarative permission policy for Microsoft Graph API
scopes used by the Teams bot. It validates that runtime tokens carry exactly
the required scopes and flags over-privileged tokens.

Key components:
- GraphPermission: single permission entry with scope, type, required flag, justification
- GraphPermissionManifest: versioned, immutable collection of permissions
- PermissionValidationResult: outcome of comparing granted scopes to the manifest
- build_teams_bot_manifest(): factory for the default minimal Teams bot scopes
- validate_token_scopes(): validates granted scopes against a manifest
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class GraphPermission:
    """A single Microsoft Graph permission entry.

    Attributes:
        scope_name: The Graph permission scope (e.g., "ChannelMessage.Send").
        permission_type: "application" or "delegated".
        required: True if the bot cannot function without this scope.
        justification: Human-readable reason for needing this scope.
    """

    scope_name: str
    permission_type: str  # "application" | "delegated"
    required: bool
    justification: str


@dataclass(frozen=True)
class GraphPermissionManifest:
    """Versioned, immutable collection of Graph permissions.

    Attributes:
        version: Manifest version for audit trail (e.g., "1.0").
        description: Human-readable description of this permission set.
        permissions: Immutable tuple of GraphPermission entries.
    """

    version: str
    description: str
    permissions: tuple[GraphPermission, ...]

    @property
    def required_scope_names(self) -> frozenset[str]:
        """Return the set of scope names marked as required."""
        return frozenset(p.scope_name for p in self.permissions if p.required)

    @property
    def optional_scope_names(self) -> frozenset[str]:
        """Return the set of scope names marked as optional."""
        return frozenset(p.scope_name for p in self.permissions if not p.required)

    @property
    def all_scope_names(self) -> frozenset[str]:
        """Return the set of all scope names in the manifest."""
        return frozenset(p.scope_name for p in self.permissions)


@dataclass(frozen=True)
class PermissionValidationResult:
    """Outcome of validating granted token scopes against a permission manifest.

    Attributes:
        is_valid: True if all required scopes are present AND no excess scopes.
        granted_scopes: The scopes that were granted in the token.
        required_scopes: The scopes marked required in the manifest.
        optional_scopes: The scopes marked optional in the manifest.
        missing_required: Required scopes not found in granted scopes.
        excess_scopes: Granted scopes not found in the manifest.
        manifest_version: Version string from the manifest used for validation.
    """

    is_valid: bool
    granted_scopes: frozenset[str]
    required_scopes: frozenset[str]
    optional_scopes: frozenset[str]
    missing_required: frozenset[str]
    excess_scopes: frozenset[str]
    manifest_version: str

    def to_audit_dict(self) -> dict[str, Any]:
        """Serialize to a dict suitable for structured logging.

        Sets are converted to sorted lists for JSON compatibility.
        """
        return {
            "is_valid": self.is_valid,
            "granted_scopes": sorted(self.granted_scopes),
            "required_scopes": sorted(self.required_scopes),
            "optional_scopes": sorted(self.optional_scopes),
            "missing_required": sorted(self.missing_required),
            "excess_scopes": sorted(self.excess_scopes),
            "manifest_version": self.manifest_version,
        }


_TEAMS_BOT_PERMISSIONS: tuple[GraphPermission, ...] = (
    GraphPermission(
        scope_name="ChannelMessage.Send",
        permission_type="application",
        required=True,
        justification="Send messages to Teams channels",
    ),
    GraphPermission(
        scope_name="ChatMessage.Send",
        permission_type="application",
        required=True,
        justification="Send chat messages in 1:1 and group chats",
    ),
    GraphPermission(
        scope_name="TeamsActivity.Send",
        permission_type="application",
        required=True,
        justification="Send activity feed notifications for proactive messaging",
    ),
    GraphPermission(
        scope_name="ChannelMessage.Read.All",
        permission_type="application",
        required=False,
        justification="Read channel messages for approval flow context",
    ),
)


def build_teams_bot_manifest() -> GraphPermissionManifest:
    """Factory for the default minimal Teams bot permission manifest.

    Returns a manifest containing only the scopes needed for Teams bot
    messaging — no mail, calendar, file, or directory scopes.
    """
    return GraphPermissionManifest(
        version="1.0",
        description="Teams Bot Messaging — minimal least-privilege scopes",
        permissions=_TEAMS_BOT_PERMISSIONS,
    )


def validate_token_scopes(
    granted_scopes: set[str],
    manifest: GraphPermissionManifest,
) -> PermissionValidationResult:
    """Validate granted token scopes against a permission manifest.

    Args:
        granted_scopes: Set of scope names from the token's claims.
        manifest: The permission manifest to validate against.

    Returns:
        PermissionValidationResult with missing/excess scope analysis.
    """
    granted = frozenset(granted_scopes)
    required = manifest.required_scope_names
    optional = manifest.optional_scope_names
    all_manifest = manifest.all_scope_names

    missing_required = required - granted
    excess_scopes = granted - all_manifest

    is_valid = len(missing_required) == 0 and len(excess_scopes) == 0

    return PermissionValidationResult(
        is_valid=is_valid,
        granted_scopes=granted,
        required_scopes=required,
        optional_scopes=optional,
        missing_required=missing_required,
        excess_scopes=excess_scopes,
        manifest_version=manifest.version,
    )


__all__ = [
    "GraphPermission",
    "GraphPermissionManifest",
    "PermissionValidationResult",
    "build_teams_bot_manifest",
    "validate_token_scopes",
]
