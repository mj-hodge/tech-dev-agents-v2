# Security Review — STORY-019: Ops Console Dashboard Frontend

**Reviewer:** Security Review Agent
**Date:** 2026-04-06
**Story:** STORY-019 — Ops Console Dashboard Frontend
**Classification:** Internal ops tool, trusted network deployment

---

## Threat Model

### Asset Inventory

| Asset | Sensitivity | Notes |
|-------|-------------|-------|
| API key (`ops_api_key`) | High | Credential that authorizes all ops actions including restart/pause |
| Agent cost data | Medium | Internal spend figures — embarrassing if leaked externally, not regulated |
| Agent names / story names | Low | Internal operational detail; no PII |
| Restart/pause capability | High | Mutations that affect running workloads |
| Teams deep links | Low | Internal URLs; not a meaningful attack surface |

### Threat Actors

This is an **internal ops tool** served at `tech-dev-agents.gorillacommerce.ai`, accessed by:
- Mark (operator) and product managers on the corporate network / VPN
- No anonymous public access intended
- No customer-facing surface

**Realistic threats:**
1. **Insider misuse** — authorized user accidentally or intentionally disrupts agents
2. **Session hijack via XSS** — attacker injects script that steals the API key from localStorage
3. **Browser-based attacks** — malicious browser extension, CSRF if cookie auth is ever added, clickjacking
4. **Supply chain attack** — compromised npm dependency that exfiltrates key or injects malicious UI
5. **Credential theft** — API key exposed in browser history, logs, or dev tools

**Out of scope / low risk given internal deployment:**
- External attacker brute-forcing the API key (rate limiting is a backend concern)
- SQL injection / server-side injection (frontend does not touch the database)
- Nation-state / APT threats

---

## Findings

| # | Area | Severity | Finding | Mitigation | Status |
|---|------|----------|---------|------------|--------|
| F-01 | Auth | Medium | API key stored in `localStorage` is accessible to any JavaScript running on the same origin, including XSS payloads. If a third-party script (Recharts, any CDN) is ever loaded, it could read the key. | Store key in `sessionStorage` instead of `localStorage` (survives page refresh within tab, not cross-tab). Add a strict CSP (see F-10) to prevent inline script injection. Document the tradeoff in the README. | Condition |
| F-02 | Auth | Low | No API key rotation or expiry mechanism exists in the frontend. A leaked key remains valid indefinitely until manually revoked on the backend. | Document key rotation procedure; add an in-UI "Regenerate key" flow as a fast-follow (STORY-020 candidate). No blocking issue for this story since rotation is a backend responsibility. | Accepted |
| F-03 | Auth | Low | `isAuthenticated()` checks only for key presence in storage, not key validity. A stale/revoked key passes the `AuthGuard` until the first API call returns 401. | The existing 401 handler already clears the key and redirects to `/login`. This is acceptable — silent expiry is handled on first request. No change needed. | Accepted |
| F-04 | API Client | Low | `throw new Error(`API error: ${response.status} ${response.statusText}`)` may surface HTTP status text in error toasts or dev-tool console output visible to users with devtools open. For an internal tool this is acceptable but the message could be more generic in production. | Catch-all error messages shown in the UI should use a generic phrase ("Request failed"). Keep detailed messages in `console.error` only (behind a `DEBUG` flag or stripped by Vite in production). | Condition |
| F-05 | API Client | Low | The `request()` function spreads `options.headers` directly, allowing a caller to override `X-API-Key` or `Content-Type` via the `options` argument. No current callers exploit this, but it is an internal API footgun. | Type the `options` parameter more strictly; explicitly omit security-critical headers from the spread override path. Low-risk because this code is not attacker-controlled. | Recommended |
| F-06 | Input Validation | Medium | The agent name from the URL param (`/agents/:name`) is interpolated directly into the API path: `` client.get(`/api/agents/${name}`) ``. A crafted URL such as `/agents/../fleet` or `/agents/foo%2F..%2Ffleet` could produce unexpected API requests. | Validate `name` against a strict allow-list pattern (e.g., `/^[a-zA-Z0-9_-]{1,64}$/`) before using it in any API call or display context. Show a 404-style error for invalid names. | Condition |
| F-07 | Input Validation | Low | Alert filter dropdowns (agent, type, date range) perform client-side filtering only. Malformed data from the API could cause uncaught errors if `items` contains unexpected types. | Use TypeScript strict null checks and nullish coalescing consistently. The type definitions in `api.ts` are already well-typed; ensure runtime type guards or a schema-validation library (e.g., Zod) is used when parsing API responses. | Recommended |
| F-08 | Data Exposure | Low | Cost figures (`total_spend`, `cost_today`, `cost_history`) are visible in browser DevTools Network tab and React Query DevTools (if enabled in dev mode). For an internal tool accessed by authorized users this is expected and acceptable. | Ensure React Query DevTools are disabled in production builds (check the `import.meta.env.DEV` guard in `main.tsx`). No further action needed. | Condition |
| F-09 | Mutations | Low | Restart/pause actions use `window.confirm()` for the confirm dialog per spec. `window.confirm` is synchronous and blocks the JS thread; more importantly, it can be suppressed or auto-dismissed by browser extensions and some automation tools. | `window.confirm` is acceptable for an MVP internal tool. Replace with a modal dialog component in a fast-follow for better UX and to prevent automation suppression. No blocking issue. | Accepted |
| F-10 | Network / CSP | Medium | No Content Security Policy header is specified for the FastAPI static file responses. Without a CSP, any injected `<script>` tag (via XSS in agent names, story names, or activity descriptions rendered in the UI) executes in the same origin as the API key. | Add a `Content-Security-Policy` header to the FastAPI response for the SPA entry point: `default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'`. Recharts uses inline SVG — `'unsafe-inline'` for styles is typically required; test carefully. | Condition |
| F-11 | Network / HTTPS | Low | The feature spec does not explicitly require HTTPS for the production deployment. Sending `X-API-Key` over HTTP exposes the credential in transit. | Confirm that `tech-dev-agents.gorillacommerce.ai` is HTTPS-only with HSTS. This is a deployment / infrastructure concern (STORY-015), not a frontend code issue. | Dependency |
| F-12 | Network / CORS | Low | CORS is a backend concern; the frontend same-origin deployment means the browser does not enforce CORS for `/api/*` calls. If the backend's `CORSMiddleware` is overly permissive (`allow_origins=["*"]`), a malicious page on another origin could make credentialed requests — but only if cookies are used, which they are not here. | Verify backend CORS is restricted to the ops console origin. Not a frontend code change. | Dependency |
| F-13 | XSS — Rendered Data | Medium | Several components render server-supplied strings directly into JSX: `description` in `ActivityTimeline`, `message` in `AlertHistoryPanel`, `blocker_status` in `AgentContextPanel`. React's JSX escapes string content by default, so standard XSS is prevented. The exception is `dangerouslySetInnerHTML` — this MUST NOT be used anywhere in the implementation. | Explicitly ban `dangerouslySetInnerHTML` in the ESLint config (`no-danger` rule). Audit all components during code review (Phase 8b). | Condition |
| F-14 | XSS — Teams Link | Medium | `AgentContextPanel` renders a `teams_link` from API data as an `<a href={context.teams_link}>`. If the backend returns a `javascript:` or `data:` URI here, clicking the link executes arbitrary code. | Validate `teams_link` before rendering: only allow `https://` or `msteams://` schemes. Example: `const safeLink = /^(https?|msteams):\/\//.test(link) ? link : null`. Render no link if validation fails. | Condition |
| F-15 | Dependencies | Low | The npm dependency tree (React, TanStack Query, Recharts, Vite, Tailwind) introduces supply-chain risk. None of these packages have known critical CVEs as of the spec date, but this is a snapshot-in-time assessment. | Run `npm audit` in CI and block builds on high/critical severity findings. Pin dependencies to exact versions in `package-lock.json` (already enforced by lockfile). Schedule periodic Dependabot or Renovate updates. | Condition |
| F-16 | Clickjacking | Low | No `X-Frame-Options` or `frame-ancestors` CSP directive is specified. The dashboard could be embedded in a malicious iframe to trick an operator into clicking Restart/Pause. | Add `X-Frame-Options: DENY` and `frame-ancestors 'none'` to the CSP from F-10. Include this in the FastAPI `serve_spa` response headers. | Condition (bundled with F-10) |
| F-17 | Rate Limiting | Low | The frontend has no client-side rate limiting on the Restart/Pause mutation buttons beyond the "disabled while pending" state. A user can submit many restart actions in quick succession. | Backend should enforce per-agent action rate limits. The frontend "disabled while pending" guard is sufficient at the UI layer. No additional frontend change needed. | Accepted |

---

## Recommendations

### Must-fix before merge (Conditions)

1. **F-01 — sessionStorage for API key:** Change `localStorage` to `sessionStorage` in `api/client.ts`. This reduces the persistence window of the credential and limits cross-tab theft scenarios. The login flow (validate → store → redirect) still works identically.

2. **F-06 — Agent name validation:** Add a regex guard in `useAgent.ts` and `AgentDetailView.tsx` before constructing the API URL from the route param. Reject names that do not match `^[a-zA-Z0-9_-]{1,64}$`.

3. **F-10 / F-16 — CSP + X-Frame-Options headers:** Add security headers to the FastAPI `serve_spa` response. These can be added as a single middleware or directly in the `FileResponse` call. Example:
   ```python
   return FileResponse(
       str(FRONTEND_DIR / "index.html"),
       headers={
           "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'",
           "X-Frame-Options": "DENY",
           "X-Content-Type-Options": "nosniff",
       }
   )
   ```

4. **F-13 — Ban dangerouslySetInnerHTML:** Add `"react/no-danger": "error"` to `.eslintrc` or the ESLint flat config. Verify during Phase 8b code review that no component uses it.

5. **F-14 — Teams link scheme validation:** Implement scheme validation in `AgentContextPanel` before rendering the `<a>` tag.

6. **F-04 — Error message sanitization:** Ensure production error toasts show only generic user-facing messages. Detailed error info (status code, statusText) goes only to `console.error`.

7. **F-08 — React Query DevTools production guard:** Confirm `ReactQueryDevtools` is wrapped in `{import.meta.env.DEV && <ReactQueryDevtools />}` in `main.tsx`.

8. **F-15 — npm audit in CI:** Add `npm audit --audit-level=high` as a required CI step before build.

### Recommended fast-follows (not blocking)

- **F-02:** Design API key rotation UI (STORY-020 candidate)
- **F-05:** Tighten `request()` header typing to prevent accidental override of security headers
- **F-07:** Add Zod schema validation for API response parsing to catch shape drift at runtime
- **F-09:** Replace `window.confirm` with a proper modal dialog for restart/pause
- **F-11 / F-12:** Confirm HTTPS + CORS policy with the infra owner (STORY-015 dependency)

---

## Verdict

**APPROVED WITH CONDITIONS**

This is an internal ops tool on a trusted network accessed by a small number of authorized users. The overall security posture is reasonable for its threat model. No critical vulnerabilities were found in the design.

The following conditions MUST be resolved before the implementation is merged to main:

| # | Condition |
|---|-----------|
| C-1 | Switch `localStorage` to `sessionStorage` for the API key (F-01) |
| C-2 | Validate agent name URL param against `^[a-zA-Z0-9_-]{1,64}$` before API use (F-06) |
| C-3 | Add CSP, `X-Frame-Options: DENY`, and `X-Content-Type-Options: nosniff` headers to `serve_spa` (F-10, F-16) |
| C-4 | Ban `dangerouslySetInnerHTML` via ESLint rule; verify in Phase 8b code review (F-13) |
| C-5 | Validate `teams_link` scheme before rendering as an anchor (F-14) |
| C-6 | Generic error messages in production UI toasts; verbose messages to `console.error` only (F-04) |
| C-7 | Guard `ReactQueryDevtools` behind `import.meta.env.DEV` (F-08) |
| C-8 | Add `npm audit --audit-level=high` as a required CI step (F-15) |
