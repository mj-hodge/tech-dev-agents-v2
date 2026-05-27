# STORY-774 — Dashboard Logout Must Clear ALL Auth State (Stop Forcing Manual Storage Wipe)

## Overview
| Field | Value |
|-------|-------|
| Mode | feature_update |
| Scope | small |
| Feature Name | Dashboard logout — full state wipe |
| Phase Path | 1 → 7 → 8 → Done |
| Repo | tech-dev-agents |
| Frontend | true |

## Problem Statement

Mark frequently has to manually clear localStorage/sessionStorage in browser DevTools to recover from dashboard errors. The Logout button doesn't actually clear all auth state — when MSAL `logoutPopup()` fails (popup blocked, network blip, race), state is left stale, causing the next login attempt to error.

**Code evidence (frontend/src/hooks/useAuth.ts:13-18):**
```typescript
const logout = async () => {
  clearApiKey();
  await instance.logoutPopup().catch(() => {
    window.location.href = '/login';
  });
};
```

`clearApiKey()` only removes ONE key (`ops_api_key`):
```typescript
// frontend/src/api/client.ts:18-20
export function clearApiKey(): void {
  localStorage.removeItem(API_KEY_STORAGE);
}
```

**Not cleared:**
- MSAL's own localStorage keys (`msal.*`, `xms_cc`, `<clientId>-...`)
- Any other app-specific localStorage entries (filters, view state)
- sessionStorage entirely
- TanStack Query / React Query cache (in memory)
- IndexedDB (if MSAL uses it)
- Cookies (if app sets any)

When `logoutPopup()` fails, MSAL state stays. Next login → MSAL sees stale account/token entries → errors.

## Target User / Use Case

**User:** Mark (and any future operator) using the ops-console dashboard.
**Today:** Logout often leaves app in broken state. Mark manually clears localStorage in DevTools to recover.
**After this story:** Logout button performs a complete client-side wipe BEFORE attempting MSAL logout. Even if `logoutPopup()` fails, the next login is clean.

## Success Criteria

1. **SC-1 — Comprehensive `wipeAuthState()` function** added to `frontend/src/api/client.ts`. Clears:
   - `localStorage` keys matching `^(msal\.|<clientId>|xms_|ops_)` plus any others starting with `dispatch-` / `dashboard-` / `agent-` / `auth-`
   - `sessionStorage.clear()` entirely
   - All `IndexedDB` databases prefixed with `msal-` (if any)
   - All cookies on the current origin (best-effort — `document.cookie` enumeration + `expires=Thu, 01 Jan 1970`)
   - TanStack Query cache: `queryClient.clear()` if present
2. **SC-2 — `logout()` calls `wipeAuthState()` BEFORE `logoutPopup()`.** Wipe happens unconditionally, then we attempt MSAL logout for clean server-side session, then redirect to `/login`. If MSAL logout fails, redirect happens anyway.
3. **SC-3 — Idempotent.** Calling `wipeAuthState()` twice is safe.
4. **SC-4 — Console-logged.** Each clear category logs `[LOGOUT] cleared <category>: <count>` so future debugging is fast.
5. **SC-5 — Navigation guard.** After logout, hard-redirect to `/login` (not soft router navigation) to ensure no stale React state survives.
6. **SC-6 — Test coverage.** Unit tests using `jsdom` localStorage/sessionStorage mocks. Playwright smoke test that logs in, clicks logout, attempts to navigate to a protected route → redirected to login.
7. **SC-7 — Zero regressions** on existing auth tests + happy-path login flow.

## Verification Plan

| SC | Command | Expected |
|----|---------|----------|
| SC-1 | `npm test -- src/api/client.test.ts` (or vitest) | All wipe-category tests PASS |
| SC-2 | Inspection: `logout()` body shows `wipeAuthState()` called BEFORE `logoutPopup()` | Code review |
| SC-3 | `wipeAuthState(); wipeAuthState();` doesn't throw | Test PASS |
| SC-5 | Playwright: login → click Logout → assert URL is `/login` AND `localStorage.length === 0` | PASS |
| SC-7 | `npm test && npm run test:e2e` | All GREEN |

## Test Criteria

- **Unit tests** with jsdom (existing pattern in `frontend/src/`):
  - Setup: pre-populate localStorage with `msal.foo=bar`, `xms_cc=baz`, `ops_api_key=qux`, `random_unrelated=keep_or_clear?`. Run `wipeAuthState()`. Assert auth-related keys gone, document explicitly whether unrelated keys are kept (default: clear all to be safe; document choice).
  - Setup: pre-populate sessionStorage. Run wipe. Assert empty.
  - Idempotency: call twice, no errors.
- **Playwright smoke** (frontend story → STORY-542 enforcement applies):
  - Test: log in, navigate, click Logout, assert URL=/login, assert localStorage empty after redirect.

## Validation

| Step | Command | Pass criterion |
|------|---------|----------------|
| 1 | `npm test -- src/api/client.test.ts src/hooks/useAuth.test.ts -v` | All ≥ 4 tests GREEN |
| 2 | `npm run test:e2e -- logout` | Playwright smoke green |
| 3 | After deploy: Mark logs in, clicks Logout, attempts to navigate back. Should redirect to /login cleanly. Then re-logs-in — no errors, no manual DevTools work. | Documented in PR body with screencap or steps |
| 4 | Negative case: simulate MSAL `logoutPopup()` rejection (mocked). Assert localStorage still cleared + redirect to /login still happens. | Documented |

## Acceptance Criteria

- [ ] AC-1: `wipeAuthState()` function added to `frontend/src/api/client.ts` with the SC-1 clearing scope.
- [ ] AC-2: `logout()` in `useAuth.ts` calls `wipeAuthState()` first (before `logoutPopup()`).
- [ ] AC-3: Idempotent (no errors on double-call).
- [ ] AC-4: Console-logs each cleared category with count: `[LOGOUT] cleared localStorage: 4 keys`, `[LOGOUT] cleared sessionStorage`, etc.
- [ ] AC-5: Hard navigation: `window.location.href = '/login'` (NOT `navigate('/login')`) after logout completes.
- [ ] AC-6: localStorage clear is conservative-default (clear all keys matching the auth prefixes; if a key is unprefixed and not a known dashboard key, it's left alone — unless we decide to nuke everything for simplicity, which is acceptable).
- [ ] AC-7: TanStack Query / React Query — if present in the project, call `queryClient.clear()` or equivalent. If not present, omit (don't add the dep).
- [ ] AC-8: All existing auth tests pass.
- [ ] AC-9: New Playwright smoke for logout flow lives at `e2e/auth/logout.spec.ts` (or matching project convention).
- [ ] AC-10: Logging — every logout invocation logs the cleared categories so future debugging is fast.
- [ ] AC-11: Error/logging AC — if any clear step throws (rare, e.g., privacy-mode browser), catch + console.warn + continue. Don't block the redirect.

## Constraints
| Constraint | Value |
|------------|-------|
| Budget | small — single function + integration call + ~5 tests |
| Timeline | URGENT — Mark hits this daily |
| Tech | TypeScript, MSAL-browser, jsdom, Playwright; no new deps |

## Performance Requirements
- `wipeAuthState()` execution: < 50ms typical.
- No noticeable delay on Logout button click.

## Security Constraints
- [ ] Wipe MUST clear all auth tokens — no leftover JWT, no leftover ID token, no leftover account.
- [ ] Logging MUST NOT include token values — only counts and category names.
- [ ] Cookie clearing is best-effort only (HttpOnly cookies invisible to JS — server-side logout would handle those, out of scope here).

## Operational Lifecycle
- **Configuration:** none.
- **Tuning:** none.
- **Monitoring:** browser console logs `[LOGOUT]` for each invocation. Loki doesn't see these (client-side), but operator can verify in DevTools.

## Boundaries

| Always Do | Ask First | Never Do |
|-----------|-----------|----------|
| Clear localStorage + sessionStorage + IndexedDB (best-effort) + cookies (best-effort) | Whether to also unregister service workers (probably overkill for v1) | Clear localStorage selectively in a way that misses MSAL keys |
| Hard-redirect to `/login` after wipe | Whether to also clear server-side session via API call (server logout already handled by MSAL) | Use soft router navigation (leaves React state in memory) |
| Catch + warn + continue on per-step errors | Whether to add a "are you sure?" confirmation modal (separate UX story if motivated) | Throw or block the logout flow if MSAL logout fails |
| Idempotent | Whether wipe should run on app boot too (probably no — would clear legitimate state on hard refresh) | Add new persistent storage to replace what we cleared |

## Files to Modify

- `frontend/src/api/client.ts` — add `wipeAuthState()` function + log helpers.
- `frontend/src/hooks/useAuth.ts` — call `wipeAuthState()` before `logoutPopup()`.
- `frontend/src/api/client.test.ts` — **new** (or extend existing), unit tests for `wipeAuthState()`.
- `frontend/src/hooks/useAuth.test.ts` — **new** (or extend), test that logout calls wipe.
- `e2e/auth/logout.spec.ts` — **new**, Playwright smoke.
- `features/story-774-dashboard-logout-clears-all-state/test-design.md` — Phase 7 deliverable.
- `.project`, `backlog.md` — tracking.

## Files to NOT Modify

- MSAL config (`msalConfig.ts` or equivalent) — this story is logout-side only.
- Backend logout / session endpoints — out of scope.
- Other auth flows (login, token refresh) — only logout.

## Done Looks Like

```
$ npm test -- src/api/client.test.ts src/hooks/useAuth.test.ts
PASS src/api/client.test.ts
  wipeAuthState
    ✓ clears all msal.* keys (15ms)
    ✓ clears ops_api_key (3ms)
    ✓ clears sessionStorage entirely (4ms)
    ✓ idempotent on double-call (2ms)
    ✓ logs each cleared category with count (5ms)
PASS src/hooks/useAuth.test.ts
  logout
    ✓ calls wipeAuthState before logoutPopup (8ms)
    ✓ redirects to /login when logoutPopup fails (12ms)
========== 7 passed ==========

$ npm run test:e2e -- logout
PASS e2e/auth/logout.spec.ts
  ✓ logs in, clicks Logout, lands on /login with empty localStorage (3.4s)

# Manual proof (Mark's experience):
1. Login OK
2. Click Logout
3. Console: [LOGOUT] cleared localStorage: 6 keys, cleared sessionStorage, cleared queryCache
4. Redirected to /login
5. Re-login — no errors, no DevTools work needed
```

## Escalation Contract

1. **Some MSAL keys persist after wipe** — investigate; MSAL may store some state in IndexedDB depending on browser. Add IndexedDB clear to the wipe.
2. **TanStack Query is not present in the project** — omit the queryClient.clear() call. Document the absence.
3. **HttpOnly cookies persist** — that's expected; document as a known limitation. Server-side logout handles those (separate concern).
4. **logoutPopup() throws synchronously** (unusual) — wipe still happened first, so just redirect.
5. **Mark reports the bug still recurs after this fix** — likely server-side session state. File STORY-775 follow-up.

**Default if no story-specific rule fires:** if more than 1 turn is spent guessing, write QUESTION.md and pause.

## Codebase Context

| Aspect | Details |
|--------|---------|
| Affected files | `frontend/src/api/client.ts`, `frontend/src/hooks/useAuth.ts` |
| Reference incident | Mark's daily friction: dashboard errors → manually clear DevTools storage to recover |
| Architecture | React + MSAL-browser + (likely) TanStack Query. localStorage = primary persistence. |
| Test pattern | jsdom mocks + Playwright smoke (per STORY-542 frontend enforcement) |

## Out of Scope

- Server-side session invalidation (separate concern; MSAL OAuth flow handles it normally).
- Token revocation API call (out of scope; logoutPopup handles).
- Re-authentication after logout — separate user journey.
- "Are you sure?" confirmation modal — separate UX story.

## Notes for Implementer

- Daily friction story for Mark — make this "just work" with no surprises.
- The `localStorage.clear()` nuclear option might be the simplest. Document the choice (nuclear is safer; selective is gentler on benign keys). If you go selective, test exhaustively.
- `clearApiKey()` should be DEPRECATED (or kept as alias) — `wipeAuthState()` includes it.
- Test the failure path: mock `logoutPopup()` to reject, assert wipe still happened.
- Console.log entries help future debugging when Mark inevitably hits a different auth bug — make them clear and grep-friendly.
