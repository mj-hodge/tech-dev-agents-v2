# Analysis: Teams Least-Privilege Graph Permissions (STORY-009)

> Phase 4 — Analysis
> Date: 2026-03-31
> Scope: Small
> Depends on: STORY-001, STORY-002

---

## Decision 1: Application vs Delegated Permission Model

### Context
The bot runs as a daemon service inside a container (STORY-001) with no interactive user sign-in. Microsoft Graph distinguishes between:
- **Application permissions** — granted to the app itself, consented by a tenant admin. The app acts as itself.
- **Delegated permissions** — granted on behalf of a signed-in user. Requires user authentication flow.

### Analysis
Since the bot is a background service that sends proactive messages without a user session, application permissions are the correct model. However, the manifest data structure should support both `permission_type` values ("application" and "delegated") because:
1. Future stories may add user-context features (e.g., reading a specific user's Teams status).
2. The manifest is a validation tool — it should be able to describe what the app *actually has*, regardless of type.

### Decision
**Support both types in the data model; default manifest uses application-only.** The `permission_type` field is a string enum ("application" | "delegated") on each entry. The default Teams bot manifest declares only application permissions.

---

## Decision 2: Validation Strictness

### Context
The seed proposes a report-only validator: it flags missing and excess scopes but doesn't enforce rejection. The question is whether configurable strictness is needed.

### Analysis
- **Report-only** is sufficient for v1. The validator returns a structured result; the caller (startup code, health check, or STORY-010 webhook handler) decides the enforcement policy.
- Adding strictness modes (warn/fail) to the validator would couple policy decisions to the library, violating separation of concerns.
- The validation result already contains `is_valid` (all required present, no excess) and per-scope detail, which gives callers everything they need.

### Decision
**Report-only model.** The validator returns a `PermissionValidationResult` with `is_valid: bool`, `missing_required`, `excess_scopes`, etc. No strictness parameter — the caller enforces.

---

## Decision 3: Manifest Versioning

### Context
Should the manifest carry a version number for audit trails?

### Analysis
- A version field costs nothing and provides traceability in structured logs.
- When permissions change between releases, the version makes it clear which policy was active.
- Keep it simple: a string version (e.g., "1.0") plus an optional description.

### Decision
**Include `version` and `description` fields on the manifest.** Both are strings, set at construction time.

---

## Decision 4: Manifest Structure

### Recommended data model

```
GraphPermission (frozen dataclass):
    scope_name: str              # e.g., "ChannelMessage.Send"
    permission_type: str         # "application" | "delegated"
    required: bool               # True = must be present for bot to function
    justification: str           # Human-readable reason for needing this scope

GraphPermissionManifest (frozen dataclass):
    version: str                 # e.g., "1.0"
    description: str             # e.g., "Teams Bot Messaging — minimal scopes"
    permissions: tuple[GraphPermission, ...]  # Immutable sequence

PermissionValidationResult (frozen dataclass):
    is_valid: bool               # True if all required present AND no excess
    granted_scopes: frozenset[str]
    required_scopes: frozenset[str]
    optional_scopes: frozenset[str]
    missing_required: frozenset[str]
    excess_scopes: frozenset[str]
    manifest_version: str

    def to_audit_dict() -> dict   # Structured logging output
```

### Integration Points

| Component | How it uses this module |
|-----------|----------------------|
| Container startup (STORY-001) | Calls `validate_token_scopes(granted, manifest)` at boot; logs audit dict; decides whether to abort |
| Teams Bot (STORY-002) | No direct dependency — the bot doesn't call Graph APIs itself in v1 |
| Webhook Auth (STORY-010) | May use the manifest to verify that incoming tokens don't grant excess scopes |
| Secret Hygiene (STORY-011) | May include the audit dict in compliance reports |

---

## Recommended Approach Summary

1. Pure Python module: `tech_dev_agents/graph_permissions.py`
2. Frozen dataclasses for `GraphPermission`, `GraphPermissionManifest`, `PermissionValidationResult`
3. Factory function `build_teams_bot_manifest()` returns the default minimal manifest
4. Validator function `validate_token_scopes(granted_scopes, manifest)` returns `PermissionValidationResult`
5. `to_audit_dict()` method on the result for structured logging
6. No network calls, no Azure SDK dependencies, no JWT parsing
7. Tests in `tests/test_graph_permissions.py`
