# Analysis: Runtime Secret Hygiene & Config Exposure Audit (STORY-011)

> Phase 4 — Analysis
> Date: 2026-03-31
> Scope: Small
> Depends on: STORY-001, STORY-008

---

## Decision 1: SecretValue Implementation Strategy

### Context
The project uses frozen dataclasses for all data structures (STORY-009, STORY-010). However, `dataclasses.asdict()` recursively exposes all fields — including a `_value` field — which defeats the purpose of a secret wrapper.

### Analysis
Three options:
1. **Frozen dataclass** — Consistent but `asdict()` leaks the value. We would need to override serialization everywhere.
2. **Plain class with `__slots__`** — Full control over `__repr__`, `__str__`, `__eq__`, `__hash__`. Not a dataclass, so `asdict()` simply doesn't apply. Can still be immutable via `__setattr__` override.
3. **Plain class without slots** — Simpler but slightly less memory-efficient.

Option 2 is the best fit: it provides immutability (via `__setattr__` override after init), prevents `asdict()` leakage, and is memory-efficient.

### Decision
**Plain class with `__slots__` and `__setattr__` override.** `SecretValue` is NOT a dataclass. It implements `__repr__`, `__str__`, `__eq__`, `__hash__` explicitly. The `expose()` method is the only way to access the raw value.

---

## Decision 2: Text Redaction Approach

### Context
`redact_secrets()` needs to replace known secret values in arbitrary text (log messages, error strings, tracebacks).

### Analysis
- **Simple string replacement** (`str.replace()`) — Predictable, fast, no regex overhead. Handles exact matches. Longest-first replacement order prevents partial-match issues (e.g., a short secret being a substring of a longer one).
- **Regex-based** — Could use word boundaries or pattern matching, but secrets are arbitrary strings (UUIDs, base64 tokens, connection strings with special characters). Escaping these for regex is error-prone and slower.

### Decision
**Simple string replacement, longest-first.** Sort secret values by length descending before replacing. This prevents a shorter secret from masking part of a longer one before the longer one can be matched.

---

## Decision 3: Environment Allowlist Defaults

### Context
`claude_runner.py` currently passes `os.environ.copy()` to `subprocess.run()`. A safe env builder should provide a sensible default set of non-secret environment variables.

### Analysis
Standard safe variables for subprocess invocation:
- `PATH` — Required for command resolution
- `HOME` — Required by many tools
- `USER`, `LOGNAME` — User identity
- `LANG`, `LC_ALL`, `LC_CTYPE` — Locale/encoding
- `TERM` — Terminal type (for CLI tools)
- `SHELL` — Shell identification
- `TZ` — Timezone
- `TMPDIR`, `TEMP`, `TMP` — Temp directory paths
- `REPO_PATH` — Used by `claude_runner._resolve_options()`

This list should be the default; callers can extend it.

### Decision
**Default allowlist of ~12 non-secret variables.** `build_safe_env()` accepts an optional `extra_keys` parameter for extending. The function returns only variables that are actually present in `os.environ` (no KeyError if a key is absent).

---

## Decision 4: Inventory Scope

### Context
`runtime_identity.DEFAULT_REQUIRED_SECRETS` lists 5 secrets. The Monday.com API key is passed directly to `MondayClient.__init__()` and is not in that tuple.

### Analysis
The inventory should be **complete** — it documents all secrets the platform uses, regardless of which loading mechanism manages them. This makes the audit function useful for compliance.

Secrets to include:
1. `anthropic-api-key` — consumed by `claude_runner` (subprocess env)
2. `bot-app-id` — consumed by `webhook_auth`, `teams_bot` (configuration)
3. `bot-app-password` — consumed by `teams_bot` (HTTP auth)
4. `github-token` — consumed by `git_workflow` (clone URL, HTTP header)
5. `appinsights-connection-string` — consumed by `loki_logging` (configuration)
6. `monday-api-key` — consumed by `monday` (HTTP header)

### Decision
**6 secrets in the default inventory.** Each entry maps the secret to its consuming modules and exposure boundary.

---

## Integration Points

| Module | Current Secret Handling | Hygiene Opportunity |
|--------|------------------------|-------------------|
| `runtime_identity.py` | `load_required_secrets()`, `build_log_safe_metadata()` — names-only logging | Already safe; inventory documents it |
| `git_workflow.py` | `mask_authenticated_url()` — single-purpose masking | `redact_secrets()` generalizes this pattern |
| `monday.py` | API key in `Authorization` header, stored as `self.api_key` | Inventory documents the HTTP-header boundary |
| `claude_runner.py` | `os.environ.copy()` in `subprocess.run()` | `build_safe_env()` replaces the full copy |
| `webhook_auth.py` | Token strings in validation — no logging of raw tokens | Already safe; audit confirms |
| `loki_logging.py` | Reads `LOKI_URL` from env | Non-secret config; safe |

---

## Next Phase

**Phase 5 — Selection/Specification**
