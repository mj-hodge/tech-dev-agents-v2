# STORY-228: Analysis — Graph Token Refresh

> Phase 4 | Scope: Small | Story: STORY-228

---

## Current State

The ops console relies on a static `OPS_GRAPH_API_TOKEN` environment variable for Microsoft Graph API authentication. This token is manually provisioned and injected via `.env`. When it expires (typically after 1 hour), Teams messaging silently fails until an operator manually refreshes the token.

### Current Token Flow

1. Operator obtains a Graph API bearer token manually (via Azure portal or CLI)
2. Token is placed in `deployment/ops-console/.env` as `OPS_GRAPH_API_TOKEN`
3. `TeamsClient` (v2) reads the static token and sends it in `Authorization` headers
4. On expiry, all Graph API calls return 401 — no recovery path exists

### Refresh Gaps Identified

| Gap | Impact | Severity |
|-----|--------|----------|
| No automatic token refresh | Teams messaging fails silently after ~1 hour | High |
| No 401 → refresh → retry loop wired up | v2 TeamsClient has the retry logic but no provider to call | High |
| Static token in `.env` is a secret rotation burden | Ops burden on every expiry | Medium |
| No MSAL integration in ops console | Agent VMs use M365 CLI but ops console has no equivalent | Medium |

## Evaluation: MSAL vs Manual Token Management

### Option A: MSAL ConfidentialClientApplication (Recommended)

- **Pros:** Built-in token caching with automatic expiry handling, well-tested library, standard OAuth2 client credentials flow, thread-safe
- **Cons:** Requires `msal` dependency, needs client secret in environment
- **Complexity:** Low — MSAL handles cache, expiry, and refresh internally

### Option B: Manual Token Management

- **Pros:** No external dependency, full control over refresh logic
- **Cons:** Must implement caching, expiry detection, and thread-safety manually; error-prone; duplicates what MSAL already does
- **Complexity:** Medium — significant boilerplate for a solved problem

### Decision

**MSAL (Option A)** — the library handles token lifecycle automatically. The v2 TeamsClient already accepts a `token_provider` async callable, so wiring MSAL in requires minimal code. The `GraphTokenProvider` class wraps MSAL's blocking `acquire_token_for_client` in `run_in_executor` for async compatibility.

## Impact Assessment

- **Files changed:** 3 (new `graph_token_provider.py`, updated `main.py` wiring, updated `docker-compose.yml`)
- **Risk:** Low — additive change with fallback to static token if MSAL credentials are not configured
- **Dependencies:** `msal` package added to requirements
