"""Tests for STORY-009: Teams Least-Privilege Graph Permissions.

Phase 7 — Test Design (RED state until Phase 8 implementation).
"""

import pytest

from tech_dev_agents.graph_permissions import (
    GraphPermission,
    GraphPermissionManifest,
    PermissionValidationResult,
    build_teams_bot_manifest,
    validate_token_scopes,
)


# ---------------------------------------------------------------------------
# Group 1 — Permission Manifest Construction
# ---------------------------------------------------------------------------


def test_graph_permission_is_frozen_and_immutable():
    """T01: GraphPermission dataclass is frozen — attribute assignment raises."""
    perm = GraphPermission(
        scope_name="ChannelMessage.Send",
        permission_type="application",
        required=True,
        justification="Send messages to Teams channels",
    )
    with pytest.raises(AttributeError):
        perm.scope_name = "Mail.Read"  # type: ignore[misc]


def test_build_teams_bot_manifest_returns_minimal_scopes():
    """T02: Factory returns only Teams messaging scopes — no mail/calendar/file/directory."""
    manifest = build_teams_bot_manifest()
    scope_names = {p.scope_name for p in manifest.permissions}

    # Must have Teams messaging scopes
    assert "ChannelMessage.Send" in scope_names
    assert "ChatMessage.Send" in scope_names
    assert "TeamsActivity.Send" in scope_names

    # Must NOT have broad or unrelated scopes
    forbidden_prefixes = ("Mail.", "Calendar.", "Files.", "Directory.", "User.Read.All")
    for scope in scope_names:
        for prefix in forbidden_prefixes:
            assert not scope.startswith(prefix), f"Manifest contains forbidden scope: {scope}"


def test_manifest_permissions_are_tuples_not_lists():
    """T03: Manifest permissions field is a tuple (immutable), not a list."""
    manifest = build_teams_bot_manifest()
    assert isinstance(manifest.permissions, tuple)


# ---------------------------------------------------------------------------
# Group 2 — Scope Validation (Happy Path)
# ---------------------------------------------------------------------------


def test_validate_exact_required_scopes_passes():
    """T04: Granted scopes exactly match required — is_valid True, no missing, no excess."""
    manifest = build_teams_bot_manifest()
    required_scopes = {p.scope_name for p in manifest.permissions if p.required}

    result = validate_token_scopes(required_scopes, manifest)

    assert result.is_valid is True
    assert len(result.missing_required) == 0
    assert len(result.excess_scopes) == 0


def test_validate_required_plus_optional_scopes_passes():
    """T05: Granted includes all required + optional — is_valid True."""
    manifest = build_teams_bot_manifest()
    all_scopes = {p.scope_name for p in manifest.permissions}

    result = validate_token_scopes(all_scopes, manifest)

    assert result.is_valid is True
    assert len(result.missing_required) == 0
    assert len(result.excess_scopes) == 0


# ---------------------------------------------------------------------------
# Group 3 — Missing Required Scope Detection
# ---------------------------------------------------------------------------


def test_missing_required_scope_detected():
    """T06: One required scope missing — is_valid False, missing_required populated."""
    manifest = build_teams_bot_manifest()
    required_scopes = {p.scope_name for p in manifest.permissions if p.required}

    # Remove one required scope
    partial = required_scopes.copy()
    removed = partial.pop()

    result = validate_token_scopes(partial, manifest)

    assert result.is_valid is False
    assert removed in result.missing_required


def test_all_required_scopes_missing():
    """T07: No required scopes granted — all appear in missing_required."""
    manifest = build_teams_bot_manifest()
    required_scopes = {p.scope_name for p in manifest.permissions if p.required}

    result = validate_token_scopes(set(), manifest)

    assert result.is_valid is False
    assert result.missing_required == frozenset(required_scopes)


# ---------------------------------------------------------------------------
# Group 4 — Over-Privilege Detection
# ---------------------------------------------------------------------------


def test_excess_scope_detected():
    """T08: Granted includes Mail.Read (not in manifest) — excess_scopes populated."""
    manifest = build_teams_bot_manifest()
    all_manifest_scopes = {p.scope_name for p in manifest.permissions}
    granted = all_manifest_scopes | {"Mail.Read"}

    result = validate_token_scopes(granted, manifest)

    assert result.is_valid is False
    assert "Mail.Read" in result.excess_scopes


def test_excess_and_missing_both_detected():
    """T09: Missing a required scope AND has an excess scope — both fields populated."""
    manifest = build_teams_bot_manifest()
    required_scopes = {p.scope_name for p in manifest.permissions if p.required}

    # Grant only some required scopes + an excess scope
    partial = set()
    removed_required = set()
    for i, scope in enumerate(sorted(required_scopes)):
        if i == 0:
            removed_required.add(scope)
        else:
            partial.add(scope)
    partial.add("Directory.Read.All")

    result = validate_token_scopes(partial, manifest)

    assert result.is_valid is False
    assert len(result.missing_required) > 0
    assert "Directory.Read.All" in result.excess_scopes


# ---------------------------------------------------------------------------
# Group 5 — Audit Output Serialization
# ---------------------------------------------------------------------------


def test_validation_result_to_audit_dict_contains_all_fields():
    """T10: to_audit_dict() returns dict with all required audit fields."""
    manifest = build_teams_bot_manifest()
    all_scopes = {p.scope_name for p in manifest.permissions}

    result = validate_token_scopes(all_scopes, manifest)
    audit = result.to_audit_dict()

    assert isinstance(audit, dict)
    expected_keys = {
        "is_valid",
        "granted_scopes",
        "required_scopes",
        "optional_scopes",
        "missing_required",
        "excess_scopes",
        "manifest_version",
    }
    assert expected_keys.issubset(set(audit.keys()))
    # All set fields should be serialized as sorted lists for JSON compatibility
    assert isinstance(audit["granted_scopes"], list)
    assert isinstance(audit["missing_required"], list)


# ---------------------------------------------------------------------------
# Group 6 — Manifest Content Validation
# ---------------------------------------------------------------------------


def test_every_manifest_permission_has_justification():
    """T11: Every permission in the default manifest has a non-empty justification."""
    manifest = build_teams_bot_manifest()
    for perm in manifest.permissions:
        assert perm.justification.strip(), (
            f"Permission {perm.scope_name} has empty justification"
        )
