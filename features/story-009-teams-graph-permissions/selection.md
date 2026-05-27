# Selection: Teams Least-Privilege Graph Permissions (STORY-009)

> Phase 5 — Selection / Specification
> Date: 2026-03-31
> Scope: Small

---

## Selected Approach

Based on the Phase 4 analysis, the following approach is selected:

### Architecture
- **Single module**: `tech_dev_agents/graph_permissions.py`
- **Pure data + validation**: No I/O, no network, no Azure SDK dependency
- **Frozen dataclasses**: All structures immutable after construction
- **Report-only validation**: Returns structured result; caller enforces policy

### Data Model

| Class | Purpose | Immutable |
|-------|---------|-----------|
| `GraphPermission` | Single permission entry with scope, type, required flag, justification | Yes (frozen dataclass) |
| `GraphPermissionManifest` | Versioned collection of permissions | Yes (frozen dataclass, tuple of permissions) |
| `PermissionValidationResult` | Outcome of validating granted scopes against manifest | Yes (frozen dataclass, frozensets) |

### Key Functions

| Function | Signature | Purpose |
|----------|-----------|---------|
| `build_teams_bot_manifest()` | `() -> GraphPermissionManifest` | Factory for the default minimal Teams bot permission set |
| `validate_token_scopes()` | `(granted: set[str], manifest: GraphPermissionManifest) -> PermissionValidationResult` | Compare granted scopes against manifest; report missing/excess |

### Default Manifest Scopes

| Scope | Type | Required | Justification |
|-------|------|----------|---------------|
| `ChannelMessage.Send` | application | Yes | Send messages to Teams channels |
| `ChatMessage.Send` | application | Yes | Send 1:1 and group chat messages |
| `TeamsActivity.Send` | application | Yes | Send activity feed notifications for proactive messaging |
| `ChannelMessage.Read.All` | application | No | Read channel messages for approval flow context |

### Validation Logic

1. Extract required and optional scope names from manifest
2. Compare against granted scopes
3. `missing_required` = required scopes not in granted
4. `excess_scopes` = granted scopes not in manifest (required or optional)
5. `is_valid` = no missing required AND no excess scopes

### Test File
- `tests/test_graph_permissions.py` — 8-10 tests covering all acceptance criteria

### Rationale for Selection
- Matches the project's established pattern (pure Python modules with frozen dataclasses, protocol interfaces, and comprehensive unit tests)
- No external dependencies beyond stdlib
- Consistent with STORY-001 (runtime_identity.py), STORY-002 (teams_bot.py) module patterns
- Audit dict output mirrors the `build_log_safe_metadata()` pattern from STORY-001
