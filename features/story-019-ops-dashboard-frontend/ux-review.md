# UX Review — STORY-019: Ops Console Dashboard Frontend

**Phase:** 6c — UX Review
**Reviewer:** Claude Sonnet (Phase 6c agent)
**Date:** 2026-04-06
**Story:** STORY-019 — Ops Console Dashboard Frontend
**Spec reviewed:** `features/story-019-ops-dashboard-frontend/feature-spec.md`

---

## User Personas Recap

| Persona | Role | Device | Browser | Primary Goal |
|---------|------|--------|---------|--------------|
| Mark | Operator | Desktop (1440px+) | Chrome | At-a-glance fleet health; restart/pause stuck agents without SSH |
| Product Managers | Stakeholders | Desktop (1280px+) | Chrome/Edge | See which stories agents are working on, what phase, what is blocked |

**Design directive:** This is an internal ops tool, NOT a consumer app. Optimize for information density and task speed. Aesthetics are secondary to scanability and reliability.

---

## Flow Analysis

### Happy Path: Mark's Morning Check

```
/login
  └─► Enter API key → "Connect"
        └─► GET /api/health (200)
              └─► / (Dashboard)
                    ├─ FleetOverviewBar: spend, agents, health — 3-second glance
                    ├─ AgentGrid: all agent cards with status badges
                    └─ AlertBanner (if active alerts)
                          └─► /alerts (AlertHistoryPanel)

/agents/:name (Agent Detail)
  ├─ CostChart (30-day area chart)
  ├─ AgentContextPanel (Teams link, blocker badge)
  ├─ [Restart] [Pause] with confirm dialog
  └─ ActivityTimeline
        └─► ← Back to Dashboard → /
```

**Assessment:** The core three-click loop (Dashboard → Agent → Action → Back) is clean. No dead ends found. The AuthGuard redirects cleanly in both directions (unauthenticated → login; post-login → `/`). The logout button is always accessible in the header.

### Identified Navigation Gaps

1. **No breadcrumb on `/alerts`** — The AlertHistoryPanel route (`/alerts`) has no explicit "Back to Dashboard" link specified in the spec. The browser back button works, but an internal link is safer, especially after a page refresh that would lose history.
2. **Agent name links in AlertHistoryPanel** — The spec mentions each alert row includes an "agent name (link to agent detail)" — this closes the loop from alerts to agent detail. Good.
3. **No 404 / unknown agent route handling** — If `/agents/:name` is called with a name the API does not recognize, the `useAgent` hook will throw an API error. The error state component handles this, but the spec does not explicitly define the message. Needs a copy decision.

---

## Findings

| # | Area | Severity | Finding | Recommendation |
|---|------|----------|---------|----------------|
| F-01 | Information hierarchy | Low | FleetOverviewBar places "Total Spend" as the first card. For Mark, agent health (error count) may be a higher-priority first glance than cumulative spend. | Consider ordering: Health Score → Active Agents → Active Stories → Total Spend. Spend is important but reactive; health is actionable. |
| F-02 | Navigation | Medium | `/alerts` page has no explicit "Back to Dashboard" link in the spec. After a hard refresh, the browser history is gone and the user must manually navigate to `/`. | Add a "← Dashboard" link to `DashboardLayout` nav or as a header breadcrumb on `/alerts`. |
| F-03 | Status communication | Low | The four-color scheme (green/yellow/red/gray) maps to active/idle/error/offline. The "idle" state (yellow) could be ambiguous — yellow typically signals "warning" in ops tooling, but idle agents are normal, not problematic. | Keep the colors as-is (they match conventional ops dashboards) but add a tooltip on the badge explaining the status. "Idle — no active story" vs "Error — last run failed" reduces misreads. |
| F-04 | Error states | Low | The spec defines "Retry" buttons for component-level network errors, but does not specify the error message copy. Vague errors ("Something went wrong") frustrate operators. | Standardize error message template: "{Component} failed to load. {Reason if available}. [Retry]" — e.g., "Agent list failed to load. Network error. [Retry]" |
| F-05 | Action safety | Low | Restart/Pause use `window.confirm()` for confirmation. `window.confirm` is visually inconsistent across browsers, cannot be styled, and is blocked in some enterprise Chrome/Edge configurations (e.g., popups disabled via policy). | Replace with an inline modal or a destructive button pattern (click once to arm → second click to confirm within 5 seconds, then auto-disarm). Given this is a medium-scope story, a simple Tailwind modal is the right fix. |
| F-06 | Action safety | Low | After a successful Restart/Pause, the spec shows a "success toast" but does not specify the toast duration, position, or dismissal. If the toast auto-dismisses before Mark reads it, he may be unsure the action took effect. | Toast duration: 4 seconds, top-right position, with an explicit "✓ Agent restarted" message. Ensure the agent card status badge also updates (query invalidation already handles this). |
| F-07 | Information density | Low | AgentCard has 6 fields: name, status badge, current story (40 chars), phase, cost today, last activity. This is the right amount for ops users — not excessive. No change needed. | No action needed. Density is appropriate for the target audience. |
| F-08 | Accessibility | Medium | `StatusBadge` uses `bg-green-500` / `text-green-400` on a dark background. Tailwind's `green-400` on dark gray (#374151 or similar) approaches the 3:1 contrast ratio minimum for UI components, but may fail the 4.5:1 WCAG AA text standard for label text. | Upgrade label text from `green-400` to `green-300` (or `green-200`) for higher contrast. Same pattern for yellow-400 → yellow-300 and red-400 → red-300. The colored dot does not need to meet text contrast since it is decorative (the label carries the meaning). |
| F-09 | Accessibility | Medium | AlertBanner and action buttons are not explicitly given `aria-label` attributes in the spec. AlertBanner renders a count string ("⚠ N active alerts") which is sufficient for screen readers, but the [Restart] / [Pause] buttons need agent context: "Restart agent-name" not just "Restart". | Add `aria-label={`Restart ${agentName}`}` and `aria-label={`Pause ${agentName}`}` to action buttons in `AgentDetailView`. |
| F-10 | Accessibility | Low | CostChart uses Recharts without an accessible text alternative. Screen readers cannot interpret SVG charts by default. | Add a visually-hidden `<caption>` or `aria-label` to the chart container: "30-day cost history for {agentName}. Highest: $X on {date}. Current: $Y." Recharts supports a `title` prop on ResponsiveContainer. |
| F-11 | Accessibility | Low | Keyboard navigation for AgentCard: The card is a clickable `<div>` navigating to `/agents/:name`. Divs are not keyboard-focusable by default. | Render AgentCard as a `<button>` or `<a href="/agents/:name">` so it receives focus and responds to Enter/Space. This also improves screen reader UX (announces as a link/button, not a generic region). |
| F-12 | Responsive design | Low | The 1024px+ breakpoint is well-matched for desktop operators. The `<1024px` fallback stacks to single-column, which is acceptable since this is not a mobile use case. The spec does not specify what happens at very large viewports (2560px+ / 4K). | Add a `max-w-screen-2xl mx-auto` container on `DashboardLayout` to prevent the grid from stretching uncomfortably on ultrawide monitors. This is a single Tailwind class addition. |
| F-13 | Loading states | Low | AgentGrid shows 3 skeleton cards during loading. But FleetOverviewBar, AgentDetailView, and AlertHistoryPanel do not have explicit skeleton/loading states defined in the spec. If those components load slowly, the user sees a blank area. | Define loading state for FleetOverviewBar (4 skeleton KPI cards), AgentDetailView header (name + badge placeholder), and AlertHistoryPanel (3 skeleton rows). |
| F-14 | Empty states | Low | The spec defines "No agents registered" for the empty agent list, but does not define the empty state for AlertHistoryPanel when filters yield no results (vs. genuinely no alerts). | Distinguish: "No alerts match your filters" (with a "Clear filters" button) vs. "No alerts recorded in this period." |
| F-15 | Login UX | Low | LoginPage submits to `GET /api/health` for key validation, which is correct. However, on failed validation the error message "Invalid API key" or "Connection failed" does not differentiate between a wrong key vs. the backend being down. This matters for Mark: if the backend is down, he should not assume his key is wrong. | Use "Invalid API key" for 401, and "Backend unreachable — check server status" for network errors / 5xx. |

---

## Accessibility Audit

| Check | Status | Notes |
|-------|--------|-------|
| Color contrast — StatusBadge label text | Needs fix | `*-400` text on dark bg may fail 4.5:1 AA; upgrade to `*-300` (F-08) |
| Color contrast — KPI cards | Pass | White/light text on dark gray cards is typically 7:1+ |
| Color contrast — AlertBanner | Pass | Yellow/red backgrounds with dark text are high-contrast |
| Keyboard navigation — AgentCard | Needs fix | Render as `<a>` not `<div>` (F-11) |
| Keyboard navigation — action buttons | Pass | Native `<button>` elements are keyboard accessible by default |
| Keyboard navigation — modals | Needs design | If `window.confirm` is replaced (F-05), ensure focus trap and Escape-to-dismiss |
| Screen reader — StatusBadge | Pass | Colored dot is decorative; text label carries meaning |
| Screen reader — CostChart | Needs fix | Add accessible text alternative (F-10) |
| Screen reader — action buttons | Needs fix | Add `aria-label` with agent name context (F-09) |
| Screen reader — AlertBanner | Pass | Text content "⚠ N active alerts" is readable |
| Focus indicators | Not specified | Ensure Tailwind `focus:ring` classes are applied to all interactive elements; Tailwind v3 removes default browser focus outlines for custom-styled elements |
| Reduced motion | Not specified | Skeleton pulse animation should respect `prefers-reduced-motion: reduce` — add `motion-safe:animate-pulse` Tailwind class |

**WCAG Level targeted:** AA (appropriate for internal ops tooling)

---

## Recommendations

### Non-Blocking (must be addressed before or during Phase 8)

1. **R-01** — Replace `window.confirm` with an inline Tailwind modal for Restart/Pause (F-05). Priority: high — enterprise Chrome may block native dialogs.
2. **R-02** — Upgrade StatusBadge label text colors from `*-400` to `*-300` for WCAG AA compliance (F-08).
3. **R-03** — Render AgentCard as `<a href>` to enable keyboard focus and screen reader announcement as a link (F-11).
4. **R-04** — Add `aria-label` with agent name to Restart/Pause buttons (F-09).
5. **R-05** — Add "← Dashboard" link to `/alerts` route to prevent navigation dead-end after hard refresh (F-02).

### Recommended Improvements (implement if time permits in Phase 8)

6. **R-06** — Reorder FleetOverviewBar cards: Health Score first, Spend last (F-01).
7. **R-07** — Add tooltips to StatusBadge explaining the status meaning (F-03).
8. **R-08** — Standardize error message copy: "{Component} failed to load. {Reason}. [Retry]" (F-04).
9. **R-09** — Specify toast duration (4s) and position (top-right) for action success feedback (F-06).
10. **R-10** — Add skeleton loading states to FleetOverviewBar and AgentDetailView header (F-13).
11. **R-11** — Distinguish "no results for filter" vs. "no alerts exist" in AlertHistoryPanel (F-14).
12. **R-12** — Differentiate "Invalid API key" (401) from "Backend unreachable" (network/5xx) on LoginPage (F-15).
13. **R-13** — Add `max-w-screen-2xl mx-auto` container to prevent layout stretch on ultrawide monitors (F-12).
14. **R-14** — Add accessible text alternative to CostChart (R-10 in accessibility audit).
15. **R-15** — Apply `motion-safe:animate-pulse` to skeleton cards for reduced-motion accessibility.

---

## Verdict

**APPROVED with recommendations.**

The overall UX design is well-suited to its purpose. The information hierarchy is sound for desktop operators: the FleetOverviewBar gives fleet-wide context, the AgentGrid provides immediate per-agent status, and the detail view provides the depth needed for diagnosis and remediation. The 30-second polling cadence with stale-while-revalidate is appropriate for an ops dashboard — fast enough to catch issues without hammering the backend.

The 6-field AgentCard strikes the right balance for this audience. Information density is a feature, not a bug, for Mark and product managers. No trimming needed.

The main concerns are (1) `window.confirm` reliability in enterprise Chrome and (2) WCAG AA color contrast on StatusBadge text — both are straightforward to fix in Phase 8 and do not require spec changes.

**Blocking items before Phase 7/8 begins:** None. All findings are non-blocking. Recommendations R-01 through R-05 should be treated as implementation requirements in Phase 8, not post-launch polish.
