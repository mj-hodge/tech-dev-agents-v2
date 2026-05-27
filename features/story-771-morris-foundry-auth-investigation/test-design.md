# STORY-771 — Test Design: Morris Foundry Auth Investigation

## Scope & Coverage

| Field | Value |
|-------|-------|
| Scope | Small |
| Coverage Target | 50% (critical paths) |
| Test Framework | pytest |
| Test Location | `tests/deployment/test_check_foundry_auth_771.py` |

## Context

This story is primarily diagnostic + ops. The testable code deliverable is `deployment/morris/scripts/check_foundry_auth.py` — a health check script that:
1. Probes Azure SP credential expiry via MS Graph API
2. Returns OK / WARN / CRIT status based on days-until-expiry
3. Logs failures with credential name + runbook pointer (AC-7)
4. Sends a Teams alert when credential is expiring or expired (AC-4)

## Test Categories

### Unit Tests — Credential Expiry Logic (7 tests)

| Test | What It Verifies |
|------|------------------|
| `test_check_valid_credential_returns_ok` | SP credential with >7 days until expiry → status OK, rc=0 |
| `test_check_expiring_soon_returns_warn` | SP credential expiring in 5 days → status WARN, rc=1 |
| `test_check_expired_credential_returns_crit` | SP credential already expired → status CRIT, rc=2 |
| `test_check_no_credentials_returns_crit` | Graph returns empty passwordCredentials → CRIT |
| `test_check_multiple_credentials_uses_latest_expiry` | Multiple creds → uses the one with the latest expiry date |
| `test_output_varies_with_expiry_date` | Two different expiry dates produce different status + log output (output-variance gate) |
| `test_days_until_expiry_computation` | Pure function: correct day count from expiry datetime |

### Unit Tests — Error Handling & Logging (5 tests)

| Test | What It Verifies |
|------|------------------|
| `test_missing_env_vars_returns_error` | Missing OPS_AZURE_* env vars → clean error, rc=1, no crash |
| `test_graph_api_failure_logs_credential_name` | Graph API 401/500 → log includes credential name (AC-7) |
| `test_graph_api_failure_logs_runbook_pointer` | Graph API failure → log includes runbook path (AC-7) |
| `test_graph_api_timeout_handled_gracefully` | Network timeout → clean error, not unhandled exception |
| `test_graph_api_malformed_response_handled` | API returns unexpected JSON shape → clean error |

### Unit Tests — Alert Delivery (3 tests)

| Test | What It Verifies |
|------|------------------|
| `test_sends_alert_when_expiring` | WARN/CRIT status → Teams DM sent via m365 CLI |
| `test_no_alert_when_ok` | OK status → no Teams DM call |
| `test_alert_failure_does_not_crash_check` | Teams send fails → check still completes, logs the failure |

## Total: 15 tests across 3 categories

## API Mock Verification

| Mock Pattern | Actual Endpoint |
|-------------|-----------------|
| `urllib.request.urlopen` (Graph token) | `https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token` |
| `urllib.request.urlopen` (Graph query) | `https://graph.microsoft.com/v1.0/applications/{appId}` |
| `subprocess.run` (m365 CLI) | `/usr/bin/m365 teams chat message send` |

All mocks target the external boundary (urllib, subprocess). Internal logic is tested directly.

## LLM Error-Prone Area Coverage

| Category | Tests |
|----------|-------|
| Boundary conditions | `test_check_expiring_soon_returns_warn` (7-day boundary), `test_days_until_expiry_computation` |
| Edge cases | `test_check_no_credentials_returns_crit`, `test_missing_env_vars_returns_error` |
| Output format | `test_output_varies_with_expiry_date` |
| Security | `test_graph_api_failure_logs_credential_name` (no secret leakage — only name, not value) |
| Error handling | `test_graph_api_timeout_handled_gracefully`, `test_graph_api_malformed_response_handled` |

## Defensive Gate Coverage

| Gate | Covered By |
|------|-----------|
| Gate 1 (Null/None) | `test_missing_env_vars_returns_error`, `test_check_no_credentials_returns_crit`, `test_graph_api_malformed_response_handled` |
| Gate 2b (External API Degradation) | `test_graph_api_failure_logs_credential_name`, `test_graph_api_timeout_handled_gracefully`, `test_graph_api_malformed_response_handled` |
| Gate 10 (Error Observability) | `test_graph_api_failure_logs_credential_name`, `test_graph_api_failure_logs_runbook_pointer` |
| Output-Variance | `test_output_varies_with_expiry_date` |

## RED State Rationale

All tests will FAIL because `deployment/morris/scripts/check_foundry_auth.py` does not yet exist. The test file imports the script as a module (same pattern as `test_refresh_foundry_cost_742.py`). Import will succeed via a stub, but function calls will raise NotImplementedError or return unexpected values.
