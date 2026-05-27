# STORY-229: Analysis — Bot-to-Bot Teams Messaging via App Token

**Phase:** 4 (Analysis)
**Date:** 2026-04-15
**Scope:** Medium

## Approach Evaluation

There is effectively **one viable approach** here — the codebase already has a proven MSAL client-credentials implementation in `deployment/hermes/teams.py`. The only question is how to apply it.

### Option A: In-Place Refactor (SELECTED)

Modify `deployment/vm/teams_m365_deployed.py` directly, replacing M365 CLI token code with MSAL while preserving the adapter's structure, polling logic, presence management, and Hermes integration.

| Criterion | Score | Notes |
|-----------|-------|-------|
| Technical soundness | 9/10 | Proven pattern; MSAL already in requirements.txt |
| Risk | Low | M365 CLI code is isolated to 3 functions; rest of adapter is Graph API calls |
| Effort | Small-Medium | ~6 functions to modify, ~1 new test file |
| Backward compatibility | High | Same env vars as hermes/teams.py (TEAMS_CLIENT_ID, etc.) already documented |

**Changes required:**

1. **Remove:** `_get_m365_token()`, `M365_CMD` constant, `m365` subprocess in `__init__`, `m365` subprocess in `check_teams_requirements()`
2. **Add:** `import msal`, `_build_msal_app()`, MSAL-based `_get_token()` (copy pattern from hermes/teams.py)
3. **Replace:** All `/me/` paths with `/users/{self._bot_user_id}/`
4. **Update:** `_refresh_token()` to use MSAL instead of subprocess
5. **Update:** `check_teams_requirements()` to validate env vars + MSAL import
6. **Update:** `__init__` to remove M365 CLI status check, add MSAL app lazy init

### Option B: Extract Shared Auth Module (REJECTED)

Create a shared `deployment/common/msal_auth.py` used by both `hermes/teams.py` and `vm/teams_m365_deployed.py`.

| Criterion | Score | Notes |
|-----------|-------|-------|
| Technical soundness | 8/10 | DRY, but the two adapters have different token needs (Graph + BF vs Graph only) |
| Risk | Medium | Refactoring hermes/teams.py is out of scope; touching working production code |
| Effort | Medium-Large | Two files to modify + new shared module + cross-cutting tests |

**Rejected because:** The adapters have divergent token needs. Hermes needs both Graph and Bot Framework tokens; the VM adapter only needs Graph. Forcing a shared abstraction adds coupling without meaningful benefit. If a third adapter appears, this can be revisited.

## Technical Analysis

### Graph API `/me/` → `/users/{id}/` Migration

The `/me/` shorthand resolves to the authenticated user. With app-only tokens, there is no authenticated user — the token represents the application itself. All `/me/` calls must become explicit `/users/{bot_user_id}/` calls.

**Affected endpoints in `teams_m365_deployed.py`:**

| Current (delegated) | New (app-only) | Method |
|---------------------|----------------|--------|
| `/me/chats?$top=50` | `/users/{id}/chats?$top=50` | `_get_chats()` |
| `/me/chats/{chatId}/messages?$top=5&$orderby=...` | `/users/{id}/chats/{chatId}/messages?$top=5&$orderby=...` | `_get_messages()` |
| `/me/chats/{chatId}/messages` | `/users/{id}/chats/{chatId}/messages` | `send()` |
| `/me/presence/setPresence` | `/users/{id}/presence/setPresence` | `_set_presence()` |

**Important:** The `$orderby=createdDateTime desc` query parameter works with both delegated and app-only tokens on `/users/{id}/chats/{chatId}/messages`. No change needed there.

### MSAL Token Caching

MSAL's `ConfidentialClientApplication` has a built-in in-memory token cache. When `acquire_token_for_client()` is called and a valid cached token exists, MSAL returns it instantly without hitting Azure AD. This means:

- The 30-minute background refresh loop can be **removed** — MSAL handles caching internally.
- The `_token_expires` field becomes the MSAL-reported `expires_in` minus a 60s buffer (matching hermes/teams.py).
- The `_get_headers()` method can check expiry and call MSAL only when needed.

### Presence API with App Permissions

`/users/{id}/presence/setPresence` with application permissions requires `Presence.ReadWrite.All`. This is a privileged permission but the bot already has `Chat.ReadWrite.All` (equally privileged), so admin consent is already required. Adding one more application permission in the same consent flow is trivial.

**Fallback:** The existing graceful-degradation pattern (warn + no-crash) is retained for presence failures.

### Test Strategy

New file: `tests/deployment/test_teams_m365_deployed.py`

| Test | What it verifies |
|------|-----------------|
| T1: MSAL token acquisition | `_get_token()` calls `acquire_token_for_client`, caches result |
| T2: Token refresh on expiry | Second call after expiry fetches new token |
| T3: Token refresh on 401 | `_graph_get` retry triggers `_get_token()` re-acquisition |
| T4: MSAL error handling | `acquire_token_for_client` returning error dict raises RuntimeError |
| T5: `/users/{id}/` URL construction | All Graph calls use explicit user path, not `/me/` |
| T6: `check_teams_requirements` pass | All env vars set + msal importable → empty list |
| T7: `check_teams_requirements` fail | Missing env var → descriptive error in list |
| T8: Presence set | `_set_presence()` calls correct `/users/{id}/` endpoint |
| T9: Build MSAL app idempotent | Multiple calls to `_build_msal_app()` create only one instance |
| T10: Connect flow | `connect()` acquires token, initializes chats, starts loops |

## Decision

**Proceed with Option A (in-place refactor).** The scope is well-defined, the pattern is proven, and the risk is low. No architectural changes needed — this is a clean auth-mechanism swap.
