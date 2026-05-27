# Seed: Runtime Secret Hygiene & Config Exposure Audit (STORY-011)

> Phase 1 — Concept & Seed
> Date: 2026-03-31
> Scope: Small
> Phase path: 1 → 4 → 5 → 7 → 8 → 8b → 11 → [9,10] → Done
> Depends on: STORY-001 (Container Runtime & Identity), STORY-008 (Git Workflow)

---

## Problem Statement

The Autonomous Dev Agent platform manages multiple high-value secrets at runtime: an Anthropic API key, a Microsoft bot app password, a GitHub token, an App Insights connection string, and a Monday.com API key. These secrets flow through several modules — `runtime_identity.py` loads and validates them, `git_workflow.py` embeds the GitHub token in clone URLs, `monday.py` sends the API key in HTTP headers, `claude_runner.py` passes environment variables to subprocesses, and `loki_logging.py` reads config from environment.

Without a centralized secret hygiene layer:

1. **Log leakage** — A secret value could appear in a log message, error traceback, exception repr, or structured audit dict. `git_workflow.py` already has `mask_authenticated_url()` but this is an ad-hoc, single-module solution. No codebase-wide redaction contract exists.
2. **Repr/str exposure** — Dataclasses holding secret values expose them in `__repr__` and `__str__`. A frozen dataclass with a secret field will print the raw value in debug output and tracebacks.
3. **Environment bleed** — `claude_runner.py` passes `os.environ.copy()` to subprocess calls, which may include secrets not needed by the child process. There is no allowlisting of environment variables for subprocess invocation.
4. **Config audit gap** — There is no single inventory of which modules consume which secrets, what format they expect, and whether they ever pass those secrets to external boundaries (HTTP headers, subprocess env, clone URLs, log output).
5. **Existing `build_log_safe_metadata()` is limited** — The function in `runtime_identity.py` exposes secret *names* (safe) but the pattern is not enforced across modules. Nothing prevents a new module from accidentally logging raw values.

This story introduces a **Secret Hygiene module** that:
- Provides a `SecretValue` wrapper type that redacts its content in `__repr__`, `__str__`, and serialization contexts.
- Offers a `redact_secrets()` function that scans arbitrary text for known secret patterns and masks them.
- Provides a `build_safe_env()` function that allowlists environment variables for subprocess invocation.
- Defines a `SecretInventory` manifest mapping secret names to their consuming modules, exposure boundaries, and classification.
- Exposes a `audit_config_exposure()` function producing a structured report of which secrets are loaded, which modules consume them, and whether redaction coverage exists.

---

## Acceptance Criteria

- [ ] **AC1: SecretValue wrapper** — A `SecretValue` class wraps a string secret and redacts it in `__repr__()` and `__str__()`, returning `"***"` instead of the actual value. The real value is accessible only via an explicit `.expose()` method.
- [ ] **AC2: SecretValue equality and hashing** — `SecretValue` instances support equality comparison (comparing the underlying value) and are hashable, so they can be used as dict values or in sets without accidentally exposing the value.
- [ ] **AC3: Text redaction** — A `redact_secrets(text, secrets)` function accepts a string and a collection of known secret values, returning a new string where every occurrence of each secret is replaced with `"***"`.
- [ ] **AC4: Environment allowlist** — A `build_safe_env(allowed_keys)` function returns a copy of `os.environ` filtered to only the specified keys, preventing secret bleed into subprocess invocations.
- [ ] **AC5: Secret inventory manifest** — A `SecretInventory` frozen dataclass holds a tuple of `SecretEntry` records, each declaring: secret name, consuming modules, exposure boundary (env, http-header, clone-url, subprocess, log), and classification (credential, token, connection-string).
- [ ] **AC6: Default inventory** — A factory function `build_default_inventory()` returns the inventory for the tech-dev-agents platform, covering all 5 required secrets from `runtime_identity.DEFAULT_REQUIRED_SECRETS` plus the Monday.com API key.
- [ ] **AC7: Config exposure audit** — A function `audit_config_exposure(inventory, loaded_secrets)` returns an `AuditResult` frozen dataclass containing: total secrets, loaded count, unloaded secrets, per-secret exposure boundary summary, and an `is_clean: bool` indicating whether all loaded secrets have redaction coverage.
- [ ] **AC8: Audit serialization** — `AuditResult` provides a `to_audit_dict()` method producing a JSON-serializable dict suitable for structured logging.
- [ ] **AC9: Unit tests** — Cover: SecretValue repr/str redaction, SecretValue equality/hashing, text redaction (single, multiple, overlapping), safe env filtering, inventory construction, default inventory completeness, audit happy path, audit with missing secrets.

---

## Functional Scope

| Capability | In Scope |
|-----------|----------|
| SecretValue wrapper with redaction | Yes |
| Text redaction for arbitrary strings | Yes |
| Environment variable allowlist for subprocess calls | Yes |
| Secret inventory manifest | Yes |
| Default inventory for tech-dev-agents | Yes |
| Config exposure audit with serialization | Yes |
| Automatic log handler integration | No — future story |
| Runtime monkey-patching of existing modules | No |
| Encryption at rest | No — infrastructure concern |
| Key rotation | No — operational concern |
| Vault integration (Azure Key Vault, etc.) | No — infrastructure concern |

---

## Constraints

| Constraint | Detail |
|-----------|--------|
| **No external deps** | Module uses only Python stdlib |
| **No runtime patching** | Provides utilities; does not modify existing modules at import time |
| **Frozen dataclasses** | All data structures are frozen following the project pattern |
| **No actual secrets in tests** | Tests use synthetic placeholder values |
| **Backward compatible** | Existing modules continue to work; this module provides opt-in utilities |

---

## Out of Scope

- Modifying existing modules to use `SecretValue` (future integration story)
- Azure Key Vault or any external secret store
- Secret rotation or expiration tracking
- Encryption of secrets in memory
- Log handler that automatically redacts (future story)
- Network-level secret scanning (e.g., GitHub secret scanning)

---

## Dependencies

| Dependency | Direction | Detail |
|-----------|-----------|--------|
| STORY-001: Container Runtime & Identity | Upstream | Defines `DEFAULT_REQUIRED_SECRETS` and `load_required_secrets()` — this module provides complementary hygiene |
| STORY-008: Git Workflow | Upstream | Has `mask_authenticated_url()` — this module generalizes that pattern |
| STORY-010: Webhook Auth Hardening | Sibling | May consume redaction utilities for auth audit logging |
| STORY-009: Graph Permissions | Sibling | May consume audit output for compliance reporting |

---

## Data Model (Conceptual)

```
SecretValue:
    _value: str           # Internal, never exposed in repr/str
    expose() -> str       # Explicit access to raw value
    __repr__() -> str     # Returns "SecretValue('***')"
    __str__() -> str      # Returns "***"
    __eq__, __hash__      # Based on underlying value

SecretEntry (frozen dataclass):
    name: str                    # e.g., "anthropic-api-key"
    consuming_modules: tuple[str, ...]  # e.g., ("claude_runner",)
    exposure_boundary: str       # "env" | "http-header" | "clone-url" | "subprocess" | "log"
    classification: str          # "credential" | "token" | "connection-string"

SecretInventory (frozen dataclass):
    entries: tuple[SecretEntry, ...]
    version: str                 # Manifest version for audit trail

AuditResult (frozen dataclass):
    total_secrets: int
    loaded_count: int
    unloaded_secrets: tuple[str, ...]
    exposure_summary: tuple[dict, ...]  # Per-secret boundary info
    is_clean: bool               # True if all loaded secrets have coverage
    audited_at: str              # ISO 8601 timestamp

    to_audit_dict() -> dict      # JSON-serializable output
```

---

## Key Design Decisions for Phase 4

1. **SecretValue immutability:** Should `SecretValue` be a frozen dataclass, a `__slots__` class, or a plain class with `__repr__`/`__str__` overrides? Frozen dataclass gives consistency with project patterns but makes the internal value accessible via `dataclasses.asdict()` which defeats the purpose.
2. **Redaction strategy:** Should `redact_secrets()` use simple string replacement or regex-based pattern matching? Regex could catch partial matches (e.g., a token appearing as a URL query parameter), but simple replacement is more predictable.
3. **Inventory scope:** Should the inventory include secrets that are not in `DEFAULT_REQUIRED_SECRETS` (e.g., Monday.com API key which is passed directly to `MondayClient`)? Yes — completeness is more valuable than strict alignment.

---

## Risks

| Risk | Mitigation |
|------|-----------|
| Secret values could still leak via `dataclasses.asdict()` on wrapping structures | Do not use frozen dataclass for SecretValue; use a plain class with explicit serialization |
| Text redaction may miss partial matches | Document that `redact_secrets()` does exact string replacement; callers must supply the full secret values |
| Environment allowlist may miss needed variables (e.g., PATH) | Document standard safe variables; provide sensible defaults |

---

## Next Phase

**Phase 4 — Analysis**

Evaluate: SecretValue implementation strategy (class design), text redaction approach, environment allowlist defaults, inventory scope and integration points.
