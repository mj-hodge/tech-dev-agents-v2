# Test Design: Runtime Secret Hygiene & Config Exposure Audit (STORY-011)

> Phase 7 — Test Design
> Date: 2026-03-31
> Scope: Small
> Story path: 1 → 4 → 5 → 7 → 8 → 8b → 11 → [9,10] → Done

---

## Test Strategy

**Approach:** Pure unit tests. `SecretValue`, `SecretEntry`, `SecretInventory`, and `AuditResult` are in-memory data structures. `redact_secrets()` is a pure function. `build_safe_env()` reads `os.environ` — tests patch it with `monkeypatch`.

**Framework:** `pytest` (consistent with all other story test suites).

**Module under test:** `tech_dev_agents.secret_hygiene`

**Coverage target:** All 9 acceptance criteria from the seed.

**State:** RED until Phase 8 implements the module.

---

## Test Groups

### Group 1 — SecretValue Wrapper (AC1, AC2)

| # | Test | Description | AC |
|---|------|-------------|-----|
| T01 | `test_secret_value_repr_is_redacted` | `repr(SecretValue("hunter2"))` returns `"SecretValue('***')"`, NOT the actual value. | AC1 |
| T02 | `test_secret_value_str_is_redacted` | `str(SecretValue("hunter2"))` returns `"***"`. | AC1 |
| T03 | `test_secret_value_expose_returns_raw` | `SecretValue("hunter2").expose()` returns `"hunter2"`. | AC1 |
| T04 | `test_secret_value_equality` | `SecretValue("a") == SecretValue("a")` is True; `SecretValue("a") == SecretValue("b")` is False. | AC2 |
| T05 | `test_secret_value_hashable` | Two `SecretValue` instances with same value have equal hashes; usable as dict keys. | AC2 |
| T06 | `test_secret_value_immutable` | Setting an attribute on a `SecretValue` after construction raises `AttributeError`. | AC1 |

### Group 2 — Text Redaction (AC3)

| # | Test | Description | AC |
|---|------|-------------|-----|
| T07 | `test_redact_single_secret` | One secret in text is replaced with `"***"`. | AC3 |
| T08 | `test_redact_multiple_secrets` | Two different secrets in text are both replaced. | AC3 |
| T09 | `test_redact_no_match_returns_original` | Text with no secret occurrences returns unchanged. | AC3 |
| T10 | `test_redact_accepts_secret_value_objects` | `redact_secrets(text, [SecretValue("x")])` works — extracts via `.expose()`. | AC3 |

### Group 3 — Environment Allowlist (AC4)

| # | Test | Description | AC |
|---|------|-------------|-----|
| T11 | `test_build_safe_env_filters_to_allowed_keys` | Only specified keys from env are included in result. | AC4 |
| T12 | `test_build_safe_env_default_keys_excludes_secrets` | Default allowlist does not include known secret variable names. | AC4 |
| T13 | `test_build_safe_env_extra_keys_extends_defaults` | `extra_keys` parameter adds keys beyond the defaults. | AC4 |

### Group 4 — Secret Inventory (AC5, AC6)

| # | Test | Description | AC |
|---|------|-------------|-----|
| T14 | `test_secret_entry_is_frozen` | `SecretEntry` attribute assignment raises `FrozenInstanceError`. | AC5 |
| T15 | `test_build_default_inventory_covers_all_required_secrets` | Default inventory includes entries for all 5 `DEFAULT_REQUIRED_SECRETS` plus Monday.com API key. | AC6 |
| T16 | `test_default_inventory_entries_have_valid_fields` | Every entry has non-empty name, consuming_modules, exposure_boundary, classification. | AC6 |

### Group 5 — Config Exposure Audit (AC7, AC8)

| # | Test | Description | AC |
|---|------|-------------|-----|
| T17 | `test_audit_all_loaded_returns_clean` | When all inventory secrets are loaded, `is_clean` is True, `unloaded_secrets` is empty. | AC7 |
| T18 | `test_audit_missing_secret_returns_not_clean` | When one secret is not loaded, `is_clean` is False, `unloaded_secrets` contains it. | AC7 |
| T19 | `test_audit_result_to_audit_dict` | `to_audit_dict()` returns a JSON-serializable dict with all expected keys. | AC8 |

---

## AC Coverage Matrix

| AC | Tests |
|----|-------|
| AC1: SecretValue wrapper | T01, T02, T03, T06 |
| AC2: Equality and hashing | T04, T05 |
| AC3: Text redaction | T07, T08, T09, T10 |
| AC4: Environment allowlist | T11, T12, T13 |
| AC5: Secret inventory manifest | T14 |
| AC6: Default inventory | T15, T16 |
| AC7: Config exposure audit | T17, T18 |
| AC8: Audit serialization | T19 |
| AC9: Unit tests | All (T01-T19) |

---

## Next Phase

**Phase 8 — Implementation** (write tests in RED state, then implement module to GREEN)
