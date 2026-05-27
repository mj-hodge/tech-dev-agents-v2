# Site Reliability: STORY-009 Teams Least-Privilege Graph Permissions

> Phase 10 — Site Reliability / Operations
> Date: 2026-03-31
> Story: STORY-009
> Scope: Small

## Operational Profile

| Attribute | Value |
|-----------|-------|
| Runtime model | Library module consumed at container startup and by webhook auth (STORY-010) |
| State storage | None — stateless; manifest is a constant, validation is pure |
| External dependencies | None — no network, no Azure SDK, no filesystem |
| Concurrency model | Thread-safe by design (all data is immutable/frozen) |
| Expected invocation frequency | Once at startup (manifest build + validation), optionally per-request for webhook auth |

## Failure Modes

| Failure | Detection | Impact | Recovery |
|---------|-----------|--------|----------|
| Token has missing required scopes | `validate_token_scopes()` returns `is_valid=False`, `missing_required` populated | Bot cannot send messages via Graph API | Fix app registration in Azure AD; re-consent; restart container |
| Token has excess scopes | `validate_token_scopes()` returns `is_valid=False`, `excess_scopes` populated | Security risk — over-privileged token | Remove excess permissions from app registration; rotate token |
| Manifest out of sync with Azure AD | Startup validation flags mismatch | False positive/negative in validation | Update manifest version + permissions; redeploy |
| New Teams API requires additional scope | Validation flags it as excess if not in manifest | New feature blocked until manifest updated | Add scope to manifest with justification; bump version |

## Restart Behavior

### Startup Sequence

1. Import `graph_permissions` module (no initialization needed)
2. Call `build_teams_bot_manifest()` to get the declared policy
3. Extract granted scopes from token claims (done by caller, not this module)
4. Call `validate_token_scopes(granted, manifest)` to validate
5. Log `result.to_audit_dict()` for compliance trail
6. Caller decides whether to abort on validation failure

### Idempotency Guarantees

- `build_teams_bot_manifest()` returns the same manifest on every call (constant data)
- `validate_token_scopes()` is a pure function — same inputs always produce same output
- No side effects, no state mutation, no I/O

## Monitoring

| Signal | Source | Alert Threshold |
|--------|--------|----------------|
| Permission validation failure at startup | Structured log from `to_audit_dict()` | Any `is_valid: false` at startup |
| Excess scopes detected | `excess_scopes` field in audit log | Any non-empty excess_scopes |
| Missing required scopes | `missing_required` field in audit log | Any non-empty missing_required |

## Runbook

### Over-privileged token detected

1. Check audit log for `excess_scopes` field
2. Go to Azure AD > App Registrations > [bot app] > API Permissions
3. Remove the excess permissions
4. Re-consent (admin consent if application permissions)
5. Restart container; verify `is_valid: true` in startup log

### Missing required scope

1. Check audit log for `missing_required` field
2. Go to Azure AD > App Registrations > [bot app] > API Permissions
3. Add the missing scope
4. Grant admin consent
5. Restart container; verify `is_valid: true` in startup log
