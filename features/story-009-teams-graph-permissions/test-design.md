# Test Design: Teams Least-Privilege Graph Permissions (STORY-009)

> Phase 7 — Test Design
> Date: 2026-03-31
> Scope: Small
> Story path: 1 → 4 → 5 → 7 → 8 → 8b → 11 → [9,10] → Done

---

## Test Strategy

**Approach:** Pure unit tests with no mocking needed — the module is entirely in-memory data structures and validation logic. No network, no filesystem, no subprocess calls.

**Framework:** `pytest` (consistent with all other story test suites).

**Module under test:** `tech_dev_agents.graph_permissions`

**Coverage target:** All 7 acceptance criteria from the seed, with emphasis on security validation correctness (missing scopes, excess scopes, audit output).

**State:** These tests start in RED state until Phase 8 implements the module.

---

## Test Groups

### Group 1 — Permission Manifest Construction

| # | Test | Description | AC |
|---|------|-------------|-----|
| T01 | `test_graph_permission_is_frozen_and_immutable` | Verify `GraphPermission` dataclass is frozen — attribute assignment raises `FrozenInstanceError`. | AC6 |
| T02 | `test_build_teams_bot_manifest_returns_minimal_scopes` | Factory returns a manifest containing only Teams messaging scopes; no mail, calendar, file, or directory scopes. | AC2 |
| T03 | `test_manifest_permissions_are_tuples_not_lists` | Manifest `permissions` field is a tuple (immutable), not a list. | AC6 |

### Group 2 — Scope Validation (Happy Path)

| # | Test | Description | AC |
|---|------|-------------|-----|
| T04 | `test_validate_exact_required_scopes_passes` | Granted scopes exactly match required scopes — `is_valid` is True, no missing, no excess. | AC3 |
| T05 | `test_validate_required_plus_optional_scopes_passes` | Granted scopes include all required + some optional — `is_valid` is True. | AC3 |

### Group 3 — Missing Required Scope Detection

| # | Test | Description | AC |
|---|------|-------------|-----|
| T06 | `test_missing_required_scope_detected` | One required scope missing — `is_valid` is False, `missing_required` contains the missing scope. | AC3 |
| T07 | `test_all_required_scopes_missing` | No required scopes granted — all required scopes appear in `missing_required`. | AC3 |

### Group 4 — Over-Privilege Detection

| # | Test | Description | AC |
|---|------|-------------|-----|
| T08 | `test_excess_scope_detected` | Granted scopes include `Mail.Read` (not in manifest) — `excess_scopes` contains it, `is_valid` is False. | AC4 |
| T09 | `test_excess_and_missing_both_detected` | Missing a required scope AND has an excess scope — both fields populated, `is_valid` is False. | AC3, AC4 |

### Group 5 — Audit Output Serialization

| # | Test | Description | AC |
|---|------|-------------|-----|
| T10 | `test_validation_result_to_audit_dict_contains_all_fields` | `to_audit_dict()` returns a dict with: granted, required, optional, missing, excess, is_valid, manifest_version. | AC5 |

### Group 6 — Manifest Content Validation

| # | Test | Description | AC |
|---|------|-------------|-----|
| T11 | `test_every_manifest_permission_has_justification` | Every `GraphPermission` in the default manifest has a non-empty `justification` string. | AC1 |

---

## Acceptance Criteria → Test Mapping

| AC | Tests |
|----|-------|
| AC1: Permission manifest | T01, T02, T03, T11 |
| AC2: Minimal scope set | T02 |
| AC3: Startup validation | T04, T05, T06, T07, T09 |
| AC4: Over-privilege detection | T08, T09 |
| AC5: Audit-friendly output | T10 |
| AC6: Immutable manifest | T01, T03 |
| AC7: Unit tests | All (T01-T11) |
