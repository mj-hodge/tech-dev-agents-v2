# Analysis — STORY-019: Ops Console Dashboard Frontend

## Approach Summary

The seed proposes a React 18 + TypeScript SPA built with Vite, using TanStack Query for server state, Recharts for charts, and Tailwind CSS for styling. The build artifact is served from FastAPI's `StaticFiles` mount, with a catch-all route returning `index.html` for client-side routing. Auth is API-key based via `localStorage`, validated against `GET /api/health` with an `X-API-Key` header. The backend API is defined but not yet live (depends on STORY-015).

---

## Technical Evaluation

### Stack Choices

| Concern | Choice | Verdict |
|---------|--------|---------|
| Build tooling | Vite + TypeScript | Excellent. Fast HMR, native ESM, first-class TS support. Standard for greenfield React. |
| Server state | TanStack Query v5 | Strong fit. Stale-while-revalidate at 30s `staleTime` gives polling without hammering the API. Background refetch on window focus is a bonus. |
| Charts | Recharts | Good fit for a single `AreaChart`. Recharts is React-native (no imperative D3 lifecycle), ships `ResponsiveContainer` out of the box. |
| Styling | Tailwind CSS | Appropriate for a utility-heavy internal tool. No design system overhead. Responsive breakpoints (`lg:`) cover the 1024px+ requirement. |
| Routing | React Router v6 | Industry standard. Nested routes map cleanly to `/ → /agents/:name`. |

### Architecture Fitness

- **Component decomposition** is well-scoped. Each component maps 1:1 to a UI concern; hook files map 1:1 to API endpoints. This avoids prop-drilling and keeps query cache keys predictable.
- **FastAPI SPA mount** is the standard pattern for co-locating a Python API with a React frontend. The catch-all route (`/{full_path:path}`) must be registered *after* all API routes to avoid shadowing them — seed code does this correctly.
- **Auth via `localStorage`** is adequate for an internal ops tool accessed over a trusted network. It is not suitable for public-facing apps, but that is not the use case here.
- **Polling strategy** — 30s `staleTime` means data is at most 30s stale under normal conditions. Window-focus refetch brings it current when the operator tabs back in. This is correct behaviour for a dashboard that does not need real-time fidelity.

---

## Risk Assessment

| Risk | Severity | Likelihood | Mitigation |
|------|----------|------------|------------|
| Backend not yet live (STORY-015 incomplete) | High | High | Add MSW (Mock Service Worker) as a dev-only dependency. Mock handlers mirror the API contract; swap to real endpoints by removing the MSW import in `main.tsx`. No conditional branching in production build. |
| API response shapes differ from TypeScript interfaces | Medium | Medium | TypeScript interfaces in `types/api.ts` act as an early-warning system at compile time. Add runtime field-presence checks in hooks (nullish coalescing on all fields) to avoid blank UI on shape drift. |
| Large `cost_history` arrays slowing Recharts render | Low | Low | Recharts `ResponsiveContainer` handles responsive sizing. For arrays >365 items, downsample to daily buckets client-side before passing to the chart. |
| `localStorage` API key leakage (XSS) | Low | Low | Internal tool behind a VPN/corp network. Acceptable risk. Document in security review (Phase 6b) if needed. |
| Bundle size | Low | Low | Recharts + TanStack Query + React Router totals ~180KB gzip. Well within acceptable range for an internal dashboard. Vite tree-shakes unused Recharts components automatically. |
| SPA catch-all shadowing API routes | Medium | Low | Register all `/api/*` routes before the catch-all. FastAPI route matching is first-match, so ordering matters. Seed code is correct but must be enforced in implementation. |

### MSW Strategy (Primary Mitigation)

```
frontend/
└── src/
    └── mocks/
        ├── handlers.ts   # MSW request handlers matching API contract
        └── browser.ts    # MSW service worker setup
```

Enable in development via `vite.config.ts` env check; exclude from production build with `if (import.meta.env.DEV)`. This allows full UI development and testing with zero backend dependency.

---

## Alternatives Considered

| Dimension | Chosen | Alternative | Reason Not Chosen |
|-----------|--------|-------------|-------------------|
| Framework | React 18 | Svelte / SvelteKit | React has broader internal knowledge, larger ecosystem for TanStack Query and Recharts. Svelte would require re-evaluating the entire query/charting stack. |
| Framework | React 18 | Vue 3 | Vue is viable but adds cognitive overhead for a team already working in React (existing component patterns). No meaningful advantage for this scope. |
| Charts | Recharts | Chart.js | Chart.js is imperative and requires `ref`-based integration in React. Recharts is declarative and idiomatic React — simpler to maintain. |
| Charts | Recharts | D3 | D3 is powerful but adds ~200KB and significant implementation complexity for a single AreaChart. Overkill. |
| CSS | Tailwind | CSS Modules | CSS Modules require per-file module files, which is more friction for an internal tool. Tailwind co-locates style with markup, faster to iterate. |
| CSS | Tailwind | styled-components | styled-components adds runtime CSS-in-JS overhead and a larger bundle. Tailwind compiles to static CSS at build time. |
| State (server) | TanStack Query | SWR | Both are good. TanStack Query v5 has a better devtools story and more granular cache invalidation — relevant for the restart/pause mutations needing optimistic updates. |
| Build | Vite | CRA (Create React App) | CRA is deprecated upstream. Vite is the current standard and significantly faster. |

---

## Business Value Analysis

| Stakeholder | Current State | With STORY-019 |
|-------------|---------------|----------------|
| Mark (operator) | SSH or raw curl to check agent health | Browser dashboard with fleet overview, cost charts, restart/pause buttons |
| Product managers | No visibility into story progress or blockers | Agent cards showing current story, phase, and blocked status |
| Engineering | Must field "what is agent X doing?" questions | Self-service dashboard eliminates interrupt-driven status checks |

**Value delivered:** Unblocks non-technical stakeholders from requiring engineering involvement for routine monitoring. The restart/pause capability reduces resolution time for stuck agents from "file a ticket → wait for engineer" to "click a button." Alert history panel surfaces anomalies before they escalate.

**Effort-to-value ratio:** High. The scope is bounded (one SPA, ~8 components, ~4 hooks), the API contract is defined, and the stack is standard. Expected implementation time is 2–3 days.

---

## Recommendation

**Proceed with the seed approach.** The stack is technically sound, the scope is well-bounded, and the business value is clear.

**Required addition:** Integrate MSW (Mock Service Worker) as a dev dependency before implementation begins. This is the critical enabler for making progress while STORY-015 backend is pending. Without it, all frontend development blocks on backend availability.

**One architectural note:** Ensure FastAPI route registration order places all `/api/*` routes before the SPA catch-all. Add a comment in the FastAPI app file to make this ordering explicit and prevent future regressions.

**Phase advance:** Proceed directly to Phase 6 (Design / Feature Spec). No expansion or selection phases are needed for this medium-scope story.
