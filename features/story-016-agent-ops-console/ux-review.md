# UX Review: Agent Operations Console

> Phase 6c — UX Review
> Story: STORY-016
> Date: 2026-04-01
> Scope: Large
> Reviewer: UX Persona

---

## Review Scope

This review evaluates the user experience design of the Agent Operations Console across accessibility, responsiveness, loading states, error states, information hierarchy, interaction patterns, and visual design consistency. The target user is a solo engineering manager (Mark) monitoring 2-N autonomous dev agents from a desktop browser.

---

## Findings Summary

| ID | Category | Severity | Status | Summary |
|----|----------|----------|--------|---------|
| UX-01 | Information Hierarchy | Low | Mitigate | Fleet overview should anchor the user's mental model |
| UX-02 | Loading States | Medium | Mitigate | All data-fetching components need skeleton/spinner states |
| UX-03 | Error States | Medium | Mitigate | Partial failures need graceful degradation, not blank screens |
| UX-04 | Stale Data Indicators | Medium | Mitigate | Users must know when data is stale vs. live |
| UX-05 | Accessibility | Medium | Mitigate | Color-coded statuses need non-color alternatives |
| UX-06 | Responsiveness | Low | Accept | Desktop-first is correct; basic tablet support is sufficient |
| UX-07 | Destructive Actions | Medium | Mitigate | Restart/pause need confirmation and clear feedback |
| UX-08 | Navigation | Low | Mitigate | Need clear back-navigation from agent detail |
| UX-09 | Alert Banner | Low | Verified | Prominent, auto-dismissing anomaly warning is well-designed |
| UX-10 | Cost Chart | Low | Mitigate | Need clear labeling of estimated vs. actual Azure costs |
| UX-11 | Dark Mode | Low | Verified | Dark theme is appropriate for ops dashboard |
| UX-12 | Empty States | Low | Mitigate | Handle zero-agent and no-alert scenarios |

---

## Detailed Findings

### UX-01: Information Hierarchy and Mental Model

**Design:** The dashboard opens with FleetOverview (stat cards) followed by AgentGrid (agent cards).

**Assessment:** This is the correct hierarchy. The user's primary question is "is everything OK?" (fleet health), followed by "what's each agent doing?" (agent cards), followed by "show me the details" (agent detail).

**Recommendation:** Reinforce the hierarchy with visual weight:
1. FleetOverview stat cards: larger font, bold numbers, subtle background color
2. Fleet health score: circular gauge or large percentage with color
3. AgentGrid: cards should be scannable in <2 seconds — name, status badge, and cost should be the most prominent elements
4. Consider a "fleet status bar" that uses a single color to indicate overall health (green bar = all good, yellow = degraded, red = critical)

---

### UX-02: Loading States (Medium — Mitigate)

**Issue:** The feature spec describes data fetching via TanStack Query with polling intervals but does not specify loading states for initial page load or data refresh.

**Risk:** Users see blank areas or layout shifts while data loads. This creates a perception of unreliability.

**Mitigation (Required):**

1. **Initial load:** Show skeleton placeholders that match the layout of the real content:
   - FleetOverview: 4 gray skeleton cards with pulsing animation
   - AgentGrid: N skeleton agent cards (N from last known agent count, default 2)
   - CostChart: Skeleton bar chart area

2. **Background refresh:** Use TanStack Query's `keepPreviousData: true` to show stale data while fetching fresh data. No spinners during background refresh — just update data silently.

3. **Slow response (>3s):** Show a subtle "Refreshing..." indicator in the footer or header, not a blocking spinner.

4. **Component-level loading pattern:**
   ```tsx
   function AgentGrid() {
     const { data, isLoading, isError } = useAgents();

     if (isLoading) return <AgentGridSkeleton />;
     if (isError) return <ErrorMessage message="Failed to load agents" />;
     if (data.agents.length === 0) return <EmptyState message="No agents registered" />;

     return <div className="grid ...">{data.agents.map(a => <AgentCard key={a.name} agent={a} />)}</div>;
   }
   ```

---

### UX-03: Error States (Medium — Mitigate)

**Issue:** The feature spec lists error HTTP codes but does not fully specify the frontend error UX for partial failures.

**Scenarios and required UX:**

| Scenario | What the user sees | What to show |
|----------|-------------------|-------------|
| API returns 502 (one VM down) | One agent card shows stale data | Agent card with "Last seen 5m ago" badge, status shows "unknown" with gray badge |
| API returns 502 (Loki down) | Cost data unavailable | Cost chart shows "Cost data temporarily unavailable" with last-known values grayed out |
| API returns 502 (Monday.com down) | Story info missing | Agent card shows "Story data unavailable" instead of story name |
| Network error (total) | Nothing loads | Full-page "Connection lost. Retrying in Xs..." with countdown |
| 401 (key expired/invalid) | Authentication failed | Redirect to login page with "Session expired" message |

**Key principle:** Never show a blank screen. Always show the last-known data with a staleness indicator, or a clear error message with a retry button.

---

### UX-04: Stale Data Indicators (Medium — Mitigate)

**Issue:** Multiple data sources have different freshness guarantees:
- Agent health: 30s cache, near-real-time
- SDK costs: 5min cache, near-real-time via Loki
- Azure costs: 24-48h delay, estimated for same-day

**Risk:** The user assumes all numbers are live. When Azure costs are 2 days behind, the total cost display is misleading.

**Mitigation (Required):**

1. **Global "Last updated" timestamp** in the footer:
   ```
   Last updated: 12:00:05 PM · Refreshing every 30s
   ```

2. **Per-data-source freshness badges:**
   - SDK costs: Show `Live` badge (green dot) when data is <5min old
   - Azure costs: Show `Estimated` badge (yellow dot) for same-day data, `As of Mar 30` for delayed data
   - Agent status: Show `X ago` relative timestamp next to status badge

3. **Cost chart annotation:** On the CostChart component, the most recent day's Azure bar should have a dashed border or "est." label to indicate estimated data.

4. **Fleet daily spend:** If Azure data is stale, show: `$24.50 (SDK) + ~$2.00 (Azure est.)`

---

### UX-05: Accessibility (Medium — Mitigate)

**Issue:** Status badges rely on color coding (green/yellow/orange/red) to convey agent status.

**Risk:** Color-blind users cannot distinguish between statuses. Approximately 8% of males have some form of color vision deficiency.

**Mitigation (Required):**

1. **Text labels always visible** — The status badge must include text, not just color:
   ```
   [● Online]  [● Idle]  [● Stuck]  [● Offline]
   ```

2. **Icon differentiation** — Each status gets a unique icon in addition to color:
   - Online: filled circle ●
   - Idle: half-circle ◑
   - Stuck: warning triangle ⚠
   - Offline: X in circle ⊗

3. **ARIA attributes:**
   - Status badges: `role="status"` with `aria-label="Agent dan is online"`
   - Alert banner: `role="alert"` for screen reader announcement
   - Stat cards: `aria-label` with full description ("Daily spend: $24.50")
   - Interactive controls: `aria-label` on buttons ("Restart agent dan")

4. **Keyboard navigation:**
   - All agent cards focusable via Tab
   - Enter/Space to open agent detail
   - Escape to close modals
   - Focus trap in confirmation modals

5. **Color contrast:** Ensure all text meets WCAG 2.1 AA (4.5:1 contrast ratio) against the dark background. Tailwind's default dark palette (`gray-900` bg, `white`/`gray-100` text) meets this requirement.

---

### UX-06: Responsiveness (Low — Accept)

**Design:** Desktop-first with basic tablet support. No mobile layout specified.

**Assessment:** Correct for an ops dashboard. Mark uses this from his desktop. Mobile is out of scope per seed.

**Recommendations (nice-to-have):**
1. Agent card grid: `grid-cols-2` on tablet, `grid-cols-1` on very narrow viewports
2. FleetOverview stat cards: wrap to 2x2 grid on tablet
3. CostChart: reduce height on smaller viewports
4. Minimum supported width: 768px (tablet landscape)

---

### UX-07: Destructive Actions (Medium — Mitigate)

**Issue:** Restart and pause are potentially disruptive operations. The feature spec mentions a confirmation modal but doesn't specify the UX details.

**Mitigation (Required):**

1. **Confirmation modal for restart:**
   ```
   ┌────────────────────────────────────────────┐
   │  ⚠ Restart Agent "dan"?                    │
   │                                             │
   │  This will interrupt any active session.    │
   │  The agent will restart in ~30 seconds.     │
   │                                             │
   │  Reason: [________________________]         │
   │                                             │
   │  [ ] Force restart (skip graceful shutdown) │
   │                                             │
   │           [Cancel]  [Restart Agent]         │
   └────────────────────────────────────────────┘
   ```

2. **Button states:**
   - Default: Red "Restart" button, yellow "Pause" button
   - During operation: Button shows spinner, text changes to "Restarting...", button disabled
   - Success: Green checkmark toast "Agent dan restarted successfully"
   - Failure: Red error toast "Failed to restart agent dan: <reason>"

3. **Pause/Resume toggle:**
   - Paused agent: card shows muted/grayed styling with "PAUSED" overlay
   - Resume button appears when paused (green color)

4. **Undo consideration:** Restart is not undoable, but pause is (resume). The UI should make this distinction clear.

---

### UX-08: Navigation (Low — Mitigate)

**Recommendation:**

1. **Breadcrumb navigation** on agent detail page:
   ```
   Dashboard > dan
   ```

2. **Back button** in agent detail header (← Back to fleet)

3. **Navigation tabs** in the main layout:
   ```
   [Dashboard]  [Alerts]
   ```

4. **Agent card click** navigates to detail page (already specified). Add hover state with border color change for affordance.

5. **URL-driven state:** React Router paths (`/`, `/agents/:name`, `/alerts`) enable browser back/forward navigation and bookmarking.

---

### UX-09: Alert Banner (Low — Verified)

**Design:** Prominent top-of-page red banner for active cost anomalies, auto-dismissing when resolved.

**Assessment:** Well-designed. The banner is the correct pattern for critical alerts that demand immediate attention.

**Enhancement:**
- Include agent name and brief description in banner: `⚠ Cost Anomaly: Agent "dan" detected coding without Claude Code SDK — $45.00 in 2 hours`
- Banner should have a "Dismiss" button (hides until next poll) and a "View Details" link (navigates to alerts page)
- Multiple active anomalies: show count badge `⚠ 2 active anomalies` with expandable list

---

### UX-10: Cost Chart Clarity (Low — Mitigate)

**Recommendation:**

1. **Legend:** Clear legend showing "SDK (Claude Code)" in blue and "Azure AI Foundry" in orange
2. **Tooltip:** On hover, show exact values: `Mar 25: SDK $12.00 | Azure $2.50 | Total $14.50`
3. **Date range selector:** Pill buttons `[7d] [14d] [30d]` above the chart
4. **Estimated data marker:** Most recent Azure bar with dashed outline or hatching pattern
5. **Y-axis:** Dollar values with "$" prefix
6. **Zero state:** If no cost data for the period, show "No cost data available for this period" instead of empty chart

---

### UX-11: Dark Mode (Low — Verified)

**Design:** Dark theme (`gray-900` background, light text) consistent with ops dashboard conventions.

**Assessment:** Appropriate. Ops dashboards are typically viewed on dedicated monitors, where dark mode reduces eye strain and draws attention to colored status indicators.

**Color palette confirmation:**
- Background: `bg-gray-900` (#111827)
- Card background: `bg-gray-800` (#1F2937)
- Text primary: `text-white` (#FFFFFF)
- Text secondary: `text-gray-400` (#9CA3AF)
- Online: `text-green-400` (#4ADE80)
- Idle: `text-yellow-400` (#FACC15)
- Stuck: `text-orange-400` (#FB923C)
- Offline: `text-red-400` (#F87171)
- Accent (links, active): `text-blue-400` (#60A5FA)

---

### UX-12: Empty States (Low — Mitigate)

**Scenarios:**

1. **No agents registered:** Show illustration + "No agents registered. Add agents to agent-registry.json to get started."
2. **No alerts:** Show "No alerts in the last 24 hours" with a green checkmark icon
3. **No activity:** Agent detail timeline shows "No recent activity recorded"
4. **No cost data:** Cost chart shows "No cost data available" with explanation of potential causes
5. **Agent detail for offline agent:** Show last-known data with prominent "Agent offline since X" banner at top of detail page

---

## User Journey: Primary Flow

### 1. Morning Check (Daily Routine)

```
User opens ops.gorillacommerce.ai
    │
    ├── FleetOverview loads: "$24.50 spent | 2/2 agents online | 3 stories | 95% health"
    │   └── Glance takes 2 seconds → "Everything looks fine"
    │
    ├── AlertBanner: (not shown — no active anomalies)
    │
    ├── AgentGrid:
    │   ├── Dan: [● Online] STORY-016 Phase 8 | $12.47 today
    │   └── Derrick: [◑ Idle] No active story | $12.03 today
    │       └── Click on Derrick → why is he idle?
    │
    └── AgentDetail (Derrick):
        ├── Status: Idle since 6:00 AM
        ├── Last activity: Completed STORY-015 Phase 10 at 5:45 AM
        ├── Cost: $12.03 today (SDK $10.00, Azure $2.03)
        └── Action: No action needed — Derrick finished his story
```

### 2. Anomaly Response (Alert Flow)

```
User opens console → AlertBanner shows:
    "⚠ Cost Anomaly: Agent 'dan' detected coding without SDK — $45.00 in 2 hours"
    │
    ├── Click "View Details" → /alerts page
    │   └── Alert table shows: [COST_ANOMALY] dan | High | Active | 10:15 AM
    │
    ├── Click agent name → /agents/dan
    │   ├── CostChart: Spike visible in today's bar
    │   ├── Controls: Click [Restart Agent]
    │   │   ├── Modal: "Restart Agent 'dan'? This will interrupt active session."
    │   │   ├── Enter reason: "Coding without SDK detected"
    │   │   └── Click [Restart Agent] → spinner → "Restarted successfully"
    │   └── Status updates: stuck → offline → online (over ~30 seconds)
```

---

## Interaction Specifications

### Agent Card Hover/Focus

```css
/* Default */
border: 1px solid rgb(55, 65, 81);    /* gray-700 */
/* Hover */
border: 1px solid rgb(59, 130, 246);  /* blue-500 */
cursor: pointer;
/* Focus (keyboard) */
outline: 2px solid rgb(59, 130, 246);
outline-offset: 2px;
```

### Toast Notifications

- **Position:** Bottom-right corner, stacked
- **Duration:** Success: 3 seconds, Error: 5 seconds (with dismiss button)
- **Animation:** Slide in from right, fade out
- **Content:** Icon (checkmark/X) + message + dismiss button

### Confirmation Modal

- **Backdrop:** Semi-transparent dark overlay
- **Position:** Centered vertically and horizontally
- **Focus trap:** Tab cycles within modal only
- **Escape key:** Closes modal (equivalent to Cancel)
- **Animation:** Fade in backdrop, scale-up modal

---

## Performance Targets

| Metric | Target | Rationale |
|--------|--------|-----------|
| First Contentful Paint | <1.5s | SPA bundle is ~50KB gzipped |
| Time to Interactive | <3s | Initial API calls complete |
| Fleet overview visible | <3s | Single API call to /api/fleet |
| Agent cards visible | <3s | Single API call to /api/agents |
| Cost chart render | <5s | Loki query + chart render |
| Restart response | <5s | Direct POST to agent VM (SC-5) |

---

## Verdict

**APPROVED with conditions.** The information hierarchy and visual design are appropriate for the target user. The following must be implemented:

1. Loading skeleton states for all data-fetching components (UX-02)
2. Graceful partial failure handling — never blank screens (UX-03)
3. Stale data indicators, especially for Azure cost delays (UX-04)
4. Accessible status badges with text labels and icons (UX-05)
5. Restart confirmation modal with reason field (UX-07)
6. Empty state handling for all zero-data scenarios (UX-12)
