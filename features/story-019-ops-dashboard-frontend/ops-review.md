# Ops Review — STORY-019: Ops Console Dashboard Frontend

**Phase:** 6d (Ops Review)
**Reviewer:** Ops Review Agent
**Date:** 2026-04-06
**Story:** STORY-019 — Ops Console Dashboard Frontend
**Advance Category:** Medium

---

## Deployment Architecture

The frontend is a React 18 SPA built with Vite and served as static files by the existing FastAPI process. There is no separate web server, no CDN, and no dedicated frontend container — the entire system is a single Python process.

```
Browser
  │
  ▼
FastAPI (tech-dev-agents.gorillacommerce.ai)
  ├── /api/*         → Python route handlers (live)
  ├── /assets/*      → StaticFiles → frontend/dist/assets/
  └── /{full_path}   → SPA catch-all → frontend/dist/index.html
```

**Build pipeline:**
1. `npm run build` in `frontend/` → Vite emits `frontend/dist/`
2. `frontend/dist/` is gitignored; must be built before or during deploy
3. FastAPI mounts `dist/` at startup via `mount_frontend()`; if the directory is absent, the mount is silently skipped and the SPA is simply unavailable (no crash)

**Cache-busting:** Vite content-hashes all JS and CSS chunks by default (e.g., `assets/index-a3f7c2b1.js`). The only file without a hash is `index.html`, which must not be cached aggressively. The current spec does not specify HTTP cache headers; see Recommendations.

**Development vs. production:** In dev, Vite proxies `/api` to `localhost:8000`. In production, no proxy layer exists — the browser calls the same origin directly. This is correct and operationally simple.

---

## Operational Concerns

| Area | Finding | Severity | Notes |
|------|---------|----------|-------|
| **Build presence at startup** | `mount_frontend()` silently no-ops if `frontend/dist/` is absent. SPA will 404 with no log message. | Low | Acceptable for an internal tool; add a warning log on skip |
| **API key in localStorage** | Key persists across sessions indefinitely. No TTL or rotation signal. | Low | Acceptable for internal use; operator controls key rotation via backend |
| **No frontend health endpoint** | The SPA itself has no `/health`. FastAPI's existing `/api/health` covers the process; the frontend is just static files and needs no separate check. | Informational | No action required |
| **HTTP cache headers** | Spec does not configure cache-control headers. `index.html` served without `Cache-Control: no-cache` may be stale in browsers after a redeploy. | Medium | Fix: add `Cache-Control: no-cache, no-store` for `index.html` in `serve_spa` |
| **No `sourcemap` in production** | `vite.config.ts` sets `sourcemap: false`. Stack traces from production errors will be minified and unreadable. | Low | Acceptable for internal ops tooling; enable if error tracking is added later |
| **30s polling — resource overhead** | Four queries polling every 30s: fleet, agents, alerts, agent-detail. At 4 tabs open = 16 requests/30s to FastAPI. With a single operator, this is negligible. | Low | No action needed at current scale |
| **window.confirm for destructive actions** | Restart/pause use `window.confirm`. Blocked by browser popups-blocked setting and visually inconsistent. | Low | Acceptable for MVP; replace with modal confirm in a future iteration |
| **No frontend error reporting** | Unhandled JS exceptions are silent unless browser DevTools is open. `window.onerror` / `window.onunhandledrejection` are not captured. | Medium | See Recommendations |
| **localStorage cleared mid-session** | If user or browser clears localStorage, all 401-redirect logic fires correctly (client.ts clears key and redirects to `/login`). This is a handled failure mode. | Informational | No action needed |
| **Mutation ordering** | `useAgentActions` invalidates `["agents"]` on success but not `["agent", name]`. The detail view may briefly show stale status after restart/pause. | Low | Add `["agent", name]` to `invalidateQueries` call in `useAgentActions` |

---

## Failure Modes

| Failure | User-Visible Behavior | Recovery |
|---------|-----------------------|----------|
| **Backend completely down** | All TanStack queries enter error state after 2 retries. Each component shows its own error UI with a "Retry" button. AlertBanner hides (no data). LoginPage shows "Connection failed". | Backend restart; TanStack auto-refetches on window focus or next poll interval |
| **Backend returns 401** | `client.ts` clears `localStorage("ops_api_key")` and hard-redirects to `/login`. All in-flight queries are effectively cancelled. | User re-enters API key |
| **Partial backend degradation** (one endpoint fails) | Only the affected component shows error state. Unaffected components continue to display data. | Component-level retry; no full-page reload needed |
| **Stale `index.html` after redeploy** | Browser serves cached `index.html` referencing old content-hashed asset filenames. Assets will 404; app breaks. | See Recommendations — set `Cache-Control: no-cache` on `index.html` |
| **localStorage cleared mid-session** | Next API call returns 401 (no key sent); `client.ts` redirects to `/login`. | User re-authenticates |
| **Vite build not run before deploy** | `mount_frontend()` skips silently. All non-API routes return 404. | Run `npm run build` in CI before deploying; check for `frontend/dist/index.html` in deploy gate |
| **Recharts renders with zero data points** | `CostChart` renders an empty `AreaChart`. Recharts handles this gracefully — no crash, just an empty chart area. | Normal state for new agents |
| **Long agent name or story title** | `AgentCard` truncates story to 40 chars; agent name is not truncated. Very long names may break card layout. | Low risk; CSS `overflow: hidden` or `truncate` class should be applied to agent name |

---

## Monitoring Gaps

| Gap | Impact | Suggested Fix |
|-----|--------|---------------|
| **No frontend error capture** | JS exceptions, failed fetches outside TanStack, and React render errors are invisible to ops. | Add a top-level `ErrorBoundary` component with fallback UI. Add `window.onerror` and `window.onunhandledrejection` handlers that log to the backend via a lightweight `POST /api/errors` endpoint, or to `console.error` at minimum. |
| **No deploy timestamp visible in UI** | Operator cannot tell which version of the frontend is running without inspecting the binary. | Embed `VITE_BUILD_TIMESTAMP` in the bundle via `vite.config.ts` `define`, and display it as a tooltip or footer label. |
| **No explicit 404 for missing build** | If `frontend/dist/` is absent, all non-API routes silently 404. No log, no alert. | Add a `logger.warning("Frontend dist not found; SPA not mounted")` in `mount_frontend()`. |
| **No latency tracking on polling** | Slow `/api/agents` responses will delay UI refresh without any visibility into it. | Acceptable gap for MVP; TanStack DevTools can be enabled in dev builds to inspect query timing. |

---

## Recommendations

### Required before deploy

1. **Set `Cache-Control: no-cache` on `index.html`.**
   Modify `serve_spa` to return `FileResponse(..., headers={"Cache-Control": "no-cache, no-store, must-revalidate"})` for the `index.html` response. Hashed assets (`/assets/*.js`, `/assets/*.css`) can be served with `Cache-Control: max-age=31536000, immutable` since Vite changes the filename on every build.

2. **Add a warning log in `mount_frontend()` when `dist/` is absent.**
   Replace the silent `return` with `logger.warning("Frontend dist dir not found at %s; SPA not mounted", FRONTEND_DIR)` so a missing build is visible in application logs.

3. **Fix query invalidation in `useAgentActions`.**
   After restart/pause success, also invalidate `["agent", name]` so the detail view reflects the new status immediately, not on the next 30s poll.

### Recommended (not blocking)

4. **Add an `ErrorBoundary` at the App root.** Catches React render errors and displays a degraded fallback UI instead of a blank screen. One component — low effort.

5. **Add `window.onerror` / `window.onunhandledrejection` handlers** that `console.error` with a structured prefix (e.g., `[OPS-CONSOLE ERROR]`) so errors appear clearly in browser console and any future log forwarding can pick them up.

6. **Embed a build timestamp or git SHA in the bundle** via `define: { __BUILD_TIME__: JSON.stringify(new Date().toISOString()) }` in `vite.config.ts`. Display it in the DashboardLayout footer for quick version identification.

### Deferred (post-MVP)

7. Replace `window.confirm` on restart/pause with a styled modal confirm dialog.
8. Apply CSS `truncate` to agent name in `AgentCard` to guard against layout overflow.
9. Consider a `/api/errors` endpoint for frontend error telemetry if the team wants post-deployment visibility into client-side failures.

---

## Rollback Procedure

The frontend has no database migrations or persistent state changes — it is a static asset bundle. Rollback is a redeploy:

1. Check out the previous git tag or commit.
2. Run `npm run build` in `frontend/`.
3. Redeploy the FastAPI process (or rsync the `dist/` directory to the server).
4. The previous SPA is live immediately.

FastAPI's `StaticFiles` mount reads from disk at request time, so replacing `frontend/dist/` while the process is running will serve the new files with no restart needed (on Linux, where inode semantics allow this). A process restart is not required for a frontend-only rollback.

---

## Verdict

**APPROVED — with two required conditions before deploy:**

1. `Cache-Control: no-cache` header on `index.html` responses (prevents stale app after redeploy)
2. Warning log in `mount_frontend()` when `dist/` directory is absent (ops visibility)

The third recommendation (fix `useAgentActions` query invalidation) is strongly recommended but does not block deploy — it is a minor UX inconsistency, not a correctness or reliability issue.

This is a static SPA served by an existing FastAPI process. There is no new infrastructure, no database, no background worker, and no separate service to monitor. The operational surface is intentionally minimal. The deploy, rollback, and failure modes are all well-understood and low-risk for an internal tool at current operator count.
