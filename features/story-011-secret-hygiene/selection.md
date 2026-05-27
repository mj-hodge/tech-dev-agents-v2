# Selection: Runtime Secret Hygiene & Config Exposure Audit (STORY-011)

> Phase 5 — Selection / Specification
> Date: 2026-03-31
> Scope: Small

---

## Selected Approach

Based on the Phase 4 analysis, the following approach is selected:

### Architecture
- **Single module**: `tech_dev_agents/secret_hygiene.py`
- **Pure logic + data**: No I/O except `os.environ` reads in `build_safe_env()`
- **Immutable data structures**: Frozen dataclasses for inventory and audit results; plain `__slots__` class for `SecretValue`
- **Opt-in utilities**: Existing modules are not modified; new code can adopt these utilities incrementally

### Data Model

| Class | Purpose | Immutable |
|-------|---------|-----------|
| `SecretValue` | Wraps a secret string; redacts in `__repr__`/`__str__` | Yes (`__slots__` + `__setattr__` override) |
| `SecretEntry` | Declares a secret's name, consumers, boundary, classification | Yes (frozen dataclass) |
| `SecretInventory` | Versioned collection of `SecretEntry` records | Yes (frozen dataclass) |
| `AuditResult` | Outcome of config exposure audit | Yes (frozen dataclass) |

### Key Functions

| Function | Signature | Purpose |
|----------|-----------|---------|
| `redact_secrets` | `(text: str, secrets: Iterable[str \| SecretValue]) -> str` | Replace all known secret values in text with `"***"` |
| `build_safe_env` | `(allowed_keys: Iterable[str] \| None = None, *, extra_keys: Iterable[str] \| None = None) -> dict[str, str]` | Return filtered copy of `os.environ` |
| `build_default_inventory` | `() -> SecretInventory` | Factory for the 6-secret tech-dev-agents inventory |
| `audit_config_exposure` | `(inventory: SecretInventory, loaded_secret_names: Iterable[str]) -> AuditResult` | Produce structured audit report |

### Constants

| Constant | Value | Purpose |
|----------|-------|---------|
| `REDACTED` | `"***"` | Consistent redaction placeholder across the codebase |
| `DEFAULT_SAFE_ENV_KEYS` | Tuple of ~12 non-secret variables | Default allowlist for `build_safe_env()` |

### Integration Pattern
Modules that want secret hygiene import from `secret_hygiene` and wrap values:
```python
from tech_dev_agents.secret_hygiene import SecretValue, redact_secrets

token = SecretValue(raw_token)
log.info("Using token %s", token)  # logs "***"
safe_text = redact_secrets(error_message, [token])
```

---

## Public API Summary

```python
# Classes
SecretValue(value: str)          # .expose() -> str, __repr__ = "SecretValue('***')"
SecretEntry(name, consuming_modules, exposure_boundary, classification)
SecretInventory(entries, version)
AuditResult(total_secrets, loaded_count, unloaded_secrets, exposure_summary, is_clean, audited_at)

# Functions
redact_secrets(text, secrets) -> str
build_safe_env(allowed_keys=None, *, extra_keys=None) -> dict[str, str]
build_default_inventory() -> SecretInventory
audit_config_exposure(inventory, loaded_secret_names) -> AuditResult
```

---

## Next Phase

**Phase 7 — Test Design**
