# EPIC — Dashboard Overwatch Redesign

## Overview
| Field | Value |
|-------|-------|
| Mode | feature_update |
| Scope | Large (epic — decomposes into 6 stories: STORY-803 through STORY-808) |
| Feature Name | Dashboard Overwatch Redesign — three-zone layout + Foundry CostMonitor |
| Phase Path | Epic: 1 → decompose → per-story SDLC → Done |
| Repo | tech-dev-agents |
| Frontend | true |
| Driver | Mark, 2026-05-02 |

## Problem Statement

Today's top of dashboard (`DashboardLayout.tsx` lines 51-62) renders four blocks of mostly informational widgets — `AlertBanner` → `FleetOverviewBar` → `AlertStatusPanel` + `BudgetGauge` → `FoundryCostPanel` — with no clear action surface and Foundry cost sandwiched mid-page rather than headlined. Mark, 2026-05-02: *"the current dashboard's top widgets are not useful and not really giving me the information i need to know. Foundry cost is potential huge issue and needs to be monitored."* Foundry overrun is concretely catastrophic per STORY-802 ($702/7d, 99.94% Opus until the migration). The dashboard cannot treat cost as a decoration; it must be a control surface. The top of the dashboard must answer in priority order: (1) what needs Mark's decision now, (2) are we within Foundry budget today, (3) is anything actively breaking, (4) what's the queue doing.

## Scope

**Large (epic).** Decomposes into 6 stories. STORY-803 through STORY-806 ship without queue v2 dependencies; STORY-807 and STORY-808 are gated on Q1/Q3/Q7/Q8/Q9 from `features/epic-queue-v2/`. Total ~8 dev days. Each substory is independently shippable to main.

## Acceptance Diff

_None — spec-only epic seed. Per-story acceptance diffs land in each substory's seed.md when dispatched._

## Today's Layout (the thing being replaced)
1. `AlertBanner` (header, conditional)
2. `FleetOverviewBar` (full width)
3. `AlertStatusPanel` + `BudgetGauge` (side-by-side)
4. `FoundryCostPanel` (full width)
5. routed content (queue, agents, etc.)

Four blocks of mostly **informational** widgets. No clear action surface. Foundry cost is sandwiched mid-page rather than headlined. Fleet status is shown but not in a way that distinguishes "5 healthy idle" from "5 burning tokens uselessly." The dispatch queue lives several scrolls down.

What Mark needs from the top of the dashboard, in order of priority:
1. **What needs my decision RIGHT NOW** — the action queue.
2. **Are we within Foundry budget today** — and projected end-of-day.
3. **Is anything actively breaking** — SLO and incidents.
4. **What's the queue doing** — lane counts, stuck stories, clusters.
5. **Are agents working or idle** — fleet status (currently dominant; should be smaller).

## Target State

Three-zone layout, top-of-page only. Everything below stays.

### Zone 1 — Status Strip (5 tiles, single row, always visible)

Each tile is a single 1-line metric with a color (green/yellow/red) and one number. Click drills in.

| Tile | Content | Red trigger |
|------|---------|-------------|
| **Action Queue** | `N` items needing Mark | N > 0 |
| **Foundry Cost Today** | `$X.XX / $20` + projected end-of-day | projected > $20 OR actual > $15 |
| **Queue SLO** | 4-of-4 green, or named breach (e.g., "head-of-line 12m") | any breach |
| **Queue Depth** | `pending: X` `attention: Y` (with Y bold if >0) | attention > 0 |
| **Fleet** | `X working, Y idle, Z offline` | any offline |

Compact. Reads in 3 seconds. Replaces today's `FleetOverviewBar` + `AlertStatusPanel` + `BudgetGauge`. Zero filler.

### Zone 2 — Action Queue (the heart of the dashboard)

A ranked, click-to-act list of things only Mark can resolve. Each row is one decision with a one-click action.

**Row format:** `[urgency tag] [STORY-X / context] [action button] [dismiss/snooze]`

Action types Mark sees here:
1. **Answer needs_info** — `STORY-X paused 3h asking <question summary>` → button: `Answer`
2. **Resolve cluster** — `3 similar questions clustered, answer 1 to resume all` → button: `Resolve Cluster`
3. **Approve PR merge** — `PR #N approved by Morris, awaiting Mark` → button: `Merge` (advertising-amazon only — others auto-merge)
4. **Approve apprenticeship rule** — `Candidate rule from 4 prior decisions` → button: `Approve` / `Reject` / `View Inputs`
5. **Promote knowledge page** — `Morris flagged page from STORY-X for human:judgment review` → button: `Approve` / `Edit` / `Reject`
6. **Stuck story decision** — `STORY-X in attention 7d, class=adversarial_block` → button: `Cancel` / `Rescope` / `Force-resume`
7. **Cost anomaly** — `failure_class sdk_died_silent burning $7/h, 3× baseline` → button: `Pause class` / `Investigate`
8. **Failure-class spike** — `Class X up 5× baseline last 24h` → button: `Investigate`
9. **Knowledge curation digest** — weekly: `4 auto:mechanical promoted, 6 human:judgment pending` → button: `Review`

**Sort order:** urgency (red), then age. Red = SLO breach, cost ceiling, or 7d+ stuck. Yellow = budgetable but actionable. Default behavior: snoozed items hide for 4h.

**Empty state:** "No action items. Mark touch rate this week: X." With Q9 stage indicator (Stage 0/1/2/3/4).

This is the **single most important widget** on the dashboard. It directly addresses "actions I need to take."

### Zone 3 — Queue at a Glance

A compact, dense view of every active story across lanes. Replaces today's deep-scroll DispatchQueue as the at-a-glance.

**Layout:** lane columns (Pending | In Progress | In Review | Human Q | Attention), each a small stack of story cards. Each card: STORY-X, repo, age, agent (if claimed), failure_class chip (if applicable), cluster pill (if clustered). Stuck stories highlighted red.

Click a card to drill into the existing detail view. The full DispatchQueue (with filters, history, etc.) stays but is below the fold.

## Foundry Cost — Dedicated View (the cost panel reimagined)

Replace `FoundryCostPanel` with a structured `CostMonitorPanel`. Foundry overrun is potentially catastrophic per STORY-802 ($702 in 7 days, 99.94% Opus). Cost can't be a decoration; it has to be a control surface.

**Top:** **today's spend / ceiling** as the headline. `$14.32 / $20` with a horizontal progress bar that turns yellow at 75%, red at 90%. Plus a *projected end-of-day* estimate based on current burn rate (`projected: $19.10`). If projection exceeds ceiling → RED + dispatch-pause-recommended pill.

**Burn rate sparkline:** last 12 hours by hour. Spot the hockey stick before it hits $20.

**Per-class breakdown:** which failure classes are spending. `sdk_died_silent: $4.20 (29%)` etc. Only classes with retry budget show up here (rate_limited stories cost $0 to release; only retried classes burn). One-click to "pause this class" if it spikes — wires to the Q3 policy table to set `retryable=FALSE` temporarily.

**Per-agent breakdown:** which agent is the heaviest spender today and what they're working on. Useful when a single stuck agent dominates spend.

**Last 7 days trend:** daily totals against the $20 ceiling (per-day; the $20 is a daily budget). Shows if we're trending up or stable.

**Per-model breakdown:** post-STORY-802, this is the alignment check. Bars: Opus / Sonnet / Haiku. Today's Foundry telemetry should show Sonnet/Haiku dominant. If Opus crosses 50% of spend → alert (regression on STORY-802).

**Alert thresholds documented inline:** "Alerts at 75% ceiling (Teams DM)", "Auto-pause dispatch at 100% ceiling" — visible so Mark knows what's automated.

## What Disappears

- `FleetOverviewBar` as a full strip — collapsed into one Status Strip tile.
- `AlertBanner` — folded into the Action Queue (alerts that need action become rows; passive alerts move to a small notification bell in the header).
- `AlertStatusPanel` mid-page — folded into Action Queue + Status Strip.
- `BudgetGauge` standalone — folded into the Foundry Cost tile + the dedicated cost view.
- Foundry cost shown twice (sandwich position) — single dedicated section, properly designed.

## What Stays Below the Fold

Don't touch: `DispatchQueue` (full table), `AgentGrid` (per-agent cards), `WorkHistoryPanel`, `AgentDetailView`, the `/work-history` and `/alerts` routes. Those are drill-in views, not at-a-glance, and they're fine.

## Data Dependencies

Most of the new widgets need data the v2 epic introduces. The redesign can't fully ship until those land:

| Widget | Data source | Available when |
|--------|-------------|----------------|
| Action Queue: Answer needs_info | Existing `dispatch_items` + STORY-738 modal | Today |
| Action Queue: Resolve cluster | STORY-773 cluster output + Q7+Q8 cluster propagation | Q7 + Q8 |
| Action Queue: Approve PR merge | Existing PR review feed | Today |
| Action Queue: Approve rule | `dispatch_rules` table | Q9 |
| Action Queue: Promote knowledge | `knowledge_ingest_queue` | Q7 |
| Action Queue: Stuck story | `dispatch_state_current.lane='attention_queue'` + age | Q1 + Q3 |
| Action Queue: Cost anomaly | `dispatch_failure_policy` × cost telemetry | Q3 + cost guard |
| Status Strip: Queue SLO | Phase 0 metrics | Phase 0 |
| Status Strip: Foundry cost projected | New `/api/foundry/projection` | New endpoint, ~0.5 day |
| Cost panel: per-class breakdown | Cost guard task + Q3 policy | Q3 + cost guard |
| Cost panel: per-model breakdown | Existing Foundry billing API | Today |

**What can ship now (not gated by v2):**
- Status Strip skeleton (with placeholder counts where data isn't available yet).
- Action Queue with the 4 action types available today (needs_info, PR merge, knowledge curation digest, alerts).
- Foundry Cost dedicated view with projected end-of-day, per-model, last 7d trend.

**What waits for v2:**
- Cluster resolve action (Q7+Q8).
- Approve rule action (Q9).
- Stuck-story attention surfacing (depends on Q1's `dispatch_state_current` lanes).
- Per-class cost breakdown (depends on Q3's policy table).

## Decomposition

| Story | Title | Scope | Effort | Gating |
|-------|-------|-------|--------|--------|
| STORY-803 | Status Strip — replace `FleetOverviewBar` + `BudgetGauge` + `AlertStatusPanel` with 5-tile compact strip | small | 1 day | None |
| STORY-804 | Action Queue v1 — needs_info + PR merge + knowledge digest + passive alerts | medium | 2 days | None |
| STORY-805 | Foundry CostMonitorPanel — projected EOD, per-model, last 7d, alert thresholds | medium | 1.5 days | New `GET /api/foundry/projection` endpoint |
| STORY-806 | Queue at a Glance — lane columns above the fold | small | 1 day | None |
| STORY-807 | Action Queue v2 extensions — cluster resolve, rule approval, stuck-story decision, cost anomaly | medium | 1.5 days | After epic-queue-v2 Q1, Q3, Q7, Q8, Q9 |
| STORY-808 | Cost panel v2 — per-failure-class breakdown, one-click pause-class | small | 1 day | After epic-queue-v2 Q3 |

**Total:** ~8 dev days. STORY-803 through STORY-806 (~5.5 days) ship now; STORY-807 and STORY-808 ship as v2 stories land.

**Sequencing:** STORY-803 first (it's the smallest and unblocks the layout shell). Then STORY-804, STORY-806 in parallel. STORY-805 in parallel after `/api/foundry/projection` endpoint exists. STORY-807, STORY-808 hold until epic-queue-v2 lands those primitives.

## Success Criteria

- **SC-1:** Mark can identify "the next thing I need to do" in <5 sec by glancing at the dashboard.
- **SC-2:** Foundry cost trajectory for today is visible without scrolling. Projected EOD vs $20 ceiling is the headline.
- **SC-3:** Action Queue surfaces every Mark-decision type listed in the runbook's escalation matrix.
- **SC-4:** Dashboard load shows actionable info (Action Queue ≥1 item) >80% of the time during business hours, OR explicitly displays "Mark touch rate: X this week" and Q9 stage on empty state.
- **SC-5:** Per-class cost breakdown lets Mark identify a single runaway failure class in <10 sec when burn rate spikes.
- **SC-6:** Touch-rate metric displayed on dashboard so Mark sees Stage progression toward declining cadence.

## Non-Goals

- Doesn't redesign drill-in views (`/work-history`, `/alerts`, `/agents/X`). Those work fine.
- Doesn't replace `DispatchQueue` table — that's for filtered exploration. The new "Queue at a Glance" is for at-a-glance, not replacement.
- Doesn't add a chat interface to the dashboard. (If Mark wants chat, that's Teams.)
- Doesn't add knowledge layer search UI. That's a separate need.

## Open Questions

- **Snooze duration on action items:** 4h default OK? Configurable per item type?
- **Auto-pause dispatch at 100% Foundry ceiling:** ship as automatic or as a one-click recommendation? Recommend: one-click for now, automatic after Q9 promotes that decision pattern.
- **Mobile view:** does Mark check the dashboard on phone? If yes, Action Queue must be the first thing rendered on narrow screens.

## Test Criteria

Each substory ships its own pytest + Playwright tests. Epic-level test criteria (verified once after STORY-803 through STORY-806 merge):

1. **Status Strip renders with all 5 tiles** in a single row above the fold; load time <500ms; tiles colorable green/yellow/red based on threshold logic. Playwright: `tests/frontend/e2e/dashboard-status-strip.spec.ts` asserts presence of all 5 tiles by `data-testid` and asserts color states under fixture data.
2. **Action Queue surfaces ≥1 row** when the system has actionable items, and shows the touch-rate empty state when no items exist. Playwright fixture: enqueue a needs_info → assert one row with `Answer` button → click → modal opens.
3. **Foundry CostMonitorPanel headline shows today's spend / $20** with horizontal progress bar; projected EOD calculated from current burn rate; per-model breakdown renders (Opus / Sonnet / Haiku bars). Pytest backend: `GET /api/foundry/projection` returns `{today, projected_eod, ceiling, per_model: {opus, sonnet, haiku}, last_7d: [...]}`. Playwright frontend: progress bar fill matches `today / ceiling` ratio.
4. **Opus-share alert fires** when per-model breakdown shows Opus >50% of today's spend (per STORY-802 alignment check). Pytest unit test on the alert-threshold logic.
5. **Queue at a Glance lane columns** render counts matching `dispatch_queue` source for: pending, in_progress, in_review, human_queue, attention_queue. Playwright: each column header shows correct count under fixture.
6. **Removed widgets are gone:** `FleetOverviewBar`, standalone `BudgetGauge`, mid-page `AlertStatusPanel`, sandwich-position `FoundryCostPanel` no longer appear in `DashboardLayout.tsx`. Test: import-graph snapshot test asserts these aren't imported by `DashboardLayout`.
7. **Zero regressions** in below-the-fold views (`DispatchQueue`, `AgentGrid`, `WorkHistoryPanel`, `/work-history`, `/alerts`, `/agents/X`). Existing Playwright + pytest suites pass unchanged.
8. **Status Strip + Action Queue + Cost Panel render under empty state** (no agents, no queue items, no alerts) without crashing. Playwright fixture with empty backend.
9. **Action Queue dismiss/snooze persists** across page reload (localStorage or backend, decision in STORY-804); 4h default snooze.

Per-substory test design (RED tests, Phase 7) is responsible for finer-grained coverage of each component.

## Validation

After STORY-803 through STORY-806 merge to main:

| Step | Command | Pass criterion |
|------|---------|----------------|
| 1 | `cd frontend && pnpm test` | All frontend unit tests pass; zero regressions vs. main |
| 2 | `cd frontend && pnpm playwright test tests/e2e/dashboard-*.spec.ts` | All new dashboard e2e tests pass |
| 3 | `pytest tests/ -x --ignore=tests/e2e -q` | Backend regression suite green |
| 4 | Manual smoke: load dashboard at `https://ops.<domain>` as Mark; observe Status Strip (5 tiles), Action Queue (≥1 item or empty-state with touch rate), Queue at a Glance (lane columns), CostMonitorPanel (today's spend / $20 + projected EOD) | All four zones render in correct order, no console errors, page paints in <1s |
| 5 | Manual smoke: trigger a needs_info on a test story → confirm Action Queue surfaces row with `Answer` button → click → modal opens → answer → queue row clears | E2E flow works |
| 6 | Manual smoke: review CostMonitorPanel against `docs/azure-foundry-billing.md` reported spend for the day | Numbers agree within 5% |

After STORY-807 and STORY-808 land (post-v2):

| Step | Command | Pass criterion |
|------|---------|----------------|
| 7 | Trigger a needs_info cluster (3 similar questions) → confirm Action Queue shows `Resolve Cluster` button → click → answer once → all 3 stories resume | Cluster auto-resume visible in UI |
| 8 | Approve a candidate apprenticeship rule from the dashboard | Rule appears in `dispatch_rules` with `approved_at` set |
| 9 | Trigger a `sdk_died_silent` cost spike → confirm CostMonitorPanel surfaces per-class row → click `Pause class` → confirm `dispatch_failure_policy.retryable=FALSE` | One-click cost mitigation works |

## Codebase Context

- **Affected files (epic-level):** `frontend/src/components/DashboardLayout.tsx` (orchestrator), `frontend/src/components/{FleetOverviewBar,BudgetGauge,AlertStatusPanel,FoundryCostPanel}.tsx` (to be removed or gutted), new `frontend/src/components/{StatusStrip,ActionQueue,CostMonitorPanel,QueueAtAGlance}.tsx`, new backend route `tech_dev_agents/ops_console/routes/foundry.py` (or extension to existing) for projection endpoint.
- **Related components:** `DispatchQueue.tsx` (stays, drill-in target), `AgentGrid.tsx` (stays, drill-in target), `AlertBanner.tsx` (stays in header).
- **Current behavior:** four widget-blocks at top, Foundry cost mid-page, no action surface.
- **Desired change:** three-zone layout (Status Strip / Action Queue / Queue at a Glance) + dedicated CostMonitorPanel.
- **Test coverage:** new components require unit tests + Playwright e2e per substory. Existing tests must continue to pass.
- **Architecture constraints:** must not increase first-paint time. Action Queue must be lazy-fetched (not block first paint). CostMonitorPanel may use existing Foundry billing API but adds projection endpoint.

## Follow-ups (out of scope for this epic, captured for later)

- Knowledge layer search UI (separate from epic-queue-v2 Q7's read-side; this would be a write-side curation UI for Mark).
- Mobile-optimized layout if Mark uses dashboard on phone.
- Drag-to-reorder Action Queue rows.
- Custom thresholds per-tile (e.g., per-user yellow/red triggers).
