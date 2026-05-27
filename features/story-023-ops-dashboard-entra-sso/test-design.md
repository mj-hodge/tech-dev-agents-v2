# Test Design: Ops Dashboard Entra ID SSO

**Story:** STORY-023
**Phase:** 7 (Test Design)
**Date:** 2026-04-08
**Scope:** Small

---

## Test Strategy

Replace API key authentication with Entra ID SSO (OAuth 2.0 / OIDC). Tests cover:
1. **Backend JWT validation** — new `require_bearer_token` dependency in `auth.py`
2. **Backend dual-auth** — Bearer token preferred, API key fallback for MCP tools
3. **Backend config** — new Entra settings in `config.py`
4. **Frontend MSAL integration** — LoginPage uses MSAL redirect, client sends Bearer token

### Approach

- Backend tests use mocked JWKS/JWT tokens (no real Entra calls)
- Frontend tests mock `@azure/msal-browser` and `@azure/msal-react`
- Existing API key tests remain (T51-T55) to verify backward compat for MCP tools
- New tests numbered T80-T95

---

## Backend Tests

### File: `tests/ops_console/test_entra_auth.py`

| ID | Test | Acceptance Criteria |
|----|------|-------------------|
| T80 | Valid Entra JWT bearer token returns 200 | AC-3 |
| T81 | Expired JWT returns 401 | AC-3 |
| T82 | JWT with wrong audience returns 401 | AC-3 |
| T83 | JWT with wrong issuer returns 401 | AC-3 |
| T84 | Missing Authorization header returns 401 | AC-3 |
| T85 | Malformed bearer token returns 401 | AC-3 |
| T86 | API key still works as fallback (MCP tools) | AC-6 |
| T87 | Health endpoint remains unauthenticated | AC-7 |
| T88 | Config has entra_tenant_id and entra_client_id settings | AC-1 |
| T89 | JWT with valid group claim (Technology Agents) succeeds | AC-4 |

### File: `tests/ops_console/test_entra_config.py`

| ID | Test | Acceptance Criteria |
|----|------|-------------------|
| T88 | Settings include entra_tenant_id field | AC-1 |
| T88b | Settings include entra_client_id field | AC-1 |

---

## Frontend Tests

### File: `frontend/src/__tests__/MsalLoginPage.test.tsx`

| ID | Test | Acceptance Criteria |
|----|------|-------------------|
| T90 | LoginPage renders "Sign in with Microsoft" button | AC-2 |
| T91 | Clicking sign-in triggers MSAL loginRedirect | AC-2 |
| T92 | After auth, client sends Authorization: Bearer header | AC-2 |
| T93 | App is wrapped in MsalProvider | AC-2 |
| T94 | Unauthenticated user sees login page (AuthGuard with MSAL) | AC-2 |
| T95 | Authenticated user can access dashboard routes | AC-2 |

---

## Test Counts

| Suite | Count |
|-------|-------|
| Backend (test_entra_auth.py) | 10 |
| Backend (test_entra_config.py) | 2 |
| Frontend (MsalLoginPage.test.tsx) | 6 |
| **Total new tests** | **18** |

---

## RED State

All 18 tests are expected to FAIL because:
- `auth.py` has no JWT validation yet
- `config.py` has no Entra settings yet
- Frontend has no MSAL dependencies or configuration
- LoginPage still uses API key form
