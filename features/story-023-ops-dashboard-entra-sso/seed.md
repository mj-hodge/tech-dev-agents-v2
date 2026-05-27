# Seed: Ops Dashboard Entra ID SSO

**Story:** STORY-023
**Date:** 2026-04-08
**Scope:** Small
**Phase Path:** 1 → 7 → 8 → Done
**Assignee:** Derrick

---

## Problem Statement

The ops dashboard at `tech-dev-agents.gorillacommerce.ai` uses a shared API key for authentication. This means:
- No per-user identity — can't tell who's logged in
- Key shared in plaintext — anyone with the key has full access
- No group-based access control — can't restrict to Technology team
- Agents (Dan, Derrick) can't authenticate via their Entra ID accounts

A "Technology Agents" Entra ID group was just created (ID: `04284f3f-51db-46f4-a5d8-3d1bd17efb7c`) with both bot accounts. The dashboard needs Entra ID SSO so this group (and Mark) can log in.

## Target User

- Mark (engineering manager) — primary dashboard user
- Bot Dan and Bot Derrick — agent accounts for automated access
- Future team members added to the Technology Agents group

## Reference Pattern

Product Health Dashboard already implements Entra ID SSO:
- App registration: `109cc94b-11c5-4f64-856b-4497d3997f95`
- Redirect URIs: `https://product-health-dashboard.gorillacommerce.ai/auth/callback` + `http://localhost:5173/auth/callback`
- Implicit grant: ID tokens + access tokens enabled
- Follow this same pattern for the ops dashboard.

## Current State

**Frontend** (`frontend/src/`):
- `LoginPage.tsx` — password field form, calls `setApiKey()`, probes `/api/health`
- `client.ts` — stores key in `localStorage` as `ops_api_key`, sends `X-API-Key` header
- No MSAL, no auth dependencies in `package.json`

**Backend** (`tech_dev_agents/ops_console/`):
- `auth.py` — `require_api_key` dependency compares `X-API-Key` header against `settings.ops_console_api_key`
- No JWT validation, no identity provider integration

## Acceptance Criteria

| ID | Criterion | Measurable |
|----|-----------|------------|
| AC-1 | Entra ID app registration created for ops dashboard | App registration exists with correct redirect URIs (`https://tech-dev-agents.gorillacommerce.ai/auth/callback` + `http://localhost:5173/auth/callback`) |
| AC-2 | Frontend uses MSAL for login | `@azure/msal-browser` installed, `LoginPage.tsx` uses `loginRedirect()` or `loginPopup()`, bearer token sent instead of API key |
| AC-3 | Backend validates JWT bearer tokens | `auth.py` validates `Authorization: Bearer <token>` against Microsoft JWKS, checks `iss`, `aud`, `exp` claims |
| AC-4 | Technology Agents group has access | Members of group `04284f3f-51db-46f4-a5d8-3d1bd17efb7c` can log in and access the dashboard |
| AC-5 | Mark can log in | Mark's Entra account (`moreta@gorillacommerce.co`) can authenticate |
| AC-6 | API key auth removed or deprecated | `X-API-Key` auth path removed from `auth.py` (or kept as fallback for MCP tools only) |
| AC-7 | Health endpoint remains unauthenticated | `GET /api/health` returns 200 without auth (needed for monitoring) |

## Out of Scope

- Role-based access control (RBAC) within the dashboard — all authenticated users have full access
- MCP tool auth migration — MCP tools can continue using API key (separate auth path)
- Refresh token rotation — MSAL handles this automatically
- Custom claims or app roles — group membership check is sufficient

## Dependencies

- Entra ID group "Technology Agents" — DONE (ID: `04284f3f-51db-46f4-a5d8-3d1bd17efb7c`)
- Tenant ID — same as existing apps (check `az account show`)
- App registration — Derrick can create via seed instructions, or Mark provisions manually

## Technical Notes

### Entra ID Setup (Mark may need to do this)
```bash
# Create app registration
az ad app create --display-name "Ops Dashboard" \
  --web-redirect-uris "https://tech-dev-agents.gorillacommerce.ai/auth/callback" "http://localhost:5173/auth/callback" \
  --enable-id-token-issuance true \
  --enable-access-token-issuance true

# Note the appId — needed for frontend MSAL config and backend token validation
```

### Frontend Changes
- Add deps: `@azure/msal-browser`, `@azure/msal-react`
- New: `src/auth/msalConfig.ts` — MSAL configuration (clientId, authority, redirectUri)
- Modify: `LoginPage.tsx` — replace password form with MSAL login button
- Modify: `client.ts` — replace `X-API-Key` with `Authorization: Bearer` from MSAL token cache
- Modify: `App.tsx` — wrap in `MsalProvider`

### Backend Changes
- Add dep: `PyJWT[crypto]` or `python-jose[cryptography]`
- Modify: `auth.py` — add `require_bearer_token` dependency that validates JWT against Microsoft JWKS
- Modify: `config.py` — add `entra_tenant_id`, `entra_client_id` settings
- Keep: `require_api_key` as secondary auth for MCP tools (check `Authorization: Bearer` first, fall back to `X-API-Key`)

### Files to Change
| File | Action |
|------|--------|
| `frontend/package.json` | Add MSAL deps |
| `frontend/src/auth/msalConfig.ts` | New — MSAL config |
| `frontend/src/components/LoginPage.tsx` | Replace with MSAL flow |
| `frontend/src/api/client.ts` | Bearer token instead of API key |
| `frontend/src/App.tsx` | Wrap in MsalProvider |
| `frontend/src/hooks/useAuth.ts` | Replace with MSAL account check |
| `tech_dev_agents/ops_console/auth.py` | Add JWT validation |
| `tech_dev_agents/ops_console/config.py` | Add Entra settings |
| `requirements.txt` / `pyproject.toml` | Add PyJWT |
