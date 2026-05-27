# STORY-515: Dashboard correctness — in_review column, state-sync audit, E2E tests

> Phase 1 | Scope: **Medium** | Created: 2026-04-22
> Advance: auto (automated dispatch path — 1 → 4+6 → 7 → 8+PR → Done)

---

## Problem Statement

Mark asked for three dashboard features across STORY-480, STORY-496, and STORY-510. All three shipped, all unit tests passed, the Dashboard Overhaul flag is flipped ON, frontend is rebuilt. And yet tonight (2026-04-22T00:00Z), on his third request, Mark still can't see any of the three:

1. **Which agent claimed which ticket** — DB had STORY-508 marked "completed by Devon" while Devon was actively writing test files for it. STORY-092 had `claimed_by=null` while Daisy had it in her local queue. State drift went undetected until Mark noticed.
2. **Which tickets are awaiting merge** — `grep -oE "in_review" /opt/ops-console/frontend/dist/assets/*.js` returns **ZERO matches**. The built frontend bundle has no code path for the `in_review` status despite the API returning the field, despite STORY-496 test-design.md listing it.
3. **Agent presence (idle / rate-limited / working)** — bundle has 6 `rate_limited` references so rendering should work, but the end-to-end path (agent self-reports rate-limited → ops-console reflects it → dashboard shows it) has never been tested on a live system.

All three are tests-didn't-catch-the-real-thing failures. Unit tests verify components render a mocked response; API tests verify endpoints return correct shapes. Nothing tests "given the real system in steady state, does Mark see what he expects."

---

## Target User

- **Mark** — needs to see fleet state at a glance in ≤2 seconds of looking at the dashboard. Currently has to ask Morris or query the DB directly.
- **Morris** — uses the dashboard as a sanity check before acting; needs it to reflect reality.
- **Future devs** — regression tests must catch dashboard/state drift before Mark does.

---

## Acceptance Criteria

### Frontend `in_review` implementation (AC-1/2)
1. **AC-1** — `DispatchQueue.tsx` (or whatever the current queue component is called) renders a dedicated section or tab for `in_review` items with columns: story_id, repo, agent that was claimed, review_started_at (relative — "15 min ago"), PR link.
2. **AC-2** — An item's transition from `in_progress` → `in_review` happens when its agent creates a PR. `sdlc_phase_runner.py`'s Phase 8 end (or PR-creation hook) POSTs `/api/dispatch/review/{story_id}` with the PR number. Server updates status to `in_review`, sets `review_started_at`. Feature-flag gated behind `OPS_DISPATCH_REVIEW_ENABLED` (already exists per STORY-496).

### State-sync reconciler (AC-3)
3. **AC-3** — New periodic reconciler (30-min cron or ops-console background task) that compares DB state to local queues on each agent VM. Mismatches logged + DM'd to Mark:
   - DB says `claimed_by=X` but agent X has no local queue entry → mark story as failed, release claim
   - DB says `completed` but no PR number AND agent has active SDK on same story_id → revert to `claimed` (story was falsely completed)
   - DB shows story as `pending` but an agent has it in local queue → re-claim on DB side
   - Config file/flag on the reconciler itself so it can be paused without disabling
   Audit log at `state/morris/reconciler-log.md`.

### Agent presence UI (AC-4/5)
4. **AC-4** — `AgentCard.tsx` clearly shows one of four states per agent: `Working on STORY-X — Phase Y`, `Idle`, `Rate-limited (resets HH:MM UTC)`, `Offline`. Test-visible text, not just color.
5. **AC-5** — Rate-limited agents show time-to-reset computed from `/var/run/dispatch-poller-paused-until` if available, fallback to "resets unknown". Daily-cap-exhausted agents show `Cap reached (resets 00:00 UTC)`.

### End-to-end tests (AC-6/7 — the real gap)
6. **AC-6** — New `tests/e2e/test_dashboard_reality.py` suite using `pytest` + `playwright` (or `requests` + HTML parsing if playwright is too heavy) that:
   - Spins up ops-console + mock agent state (DB rows + mock local queues)
   - Hits `https://<uat-host>/` and confirms the rendered HTML contains expected text:
     - "daisy" appears near "STORY-092" when daisy claimed it
     - "STORY-508" appears under "In Review" when its status is `in_review`
     - "Rate-limited" appears near dan/derrick when they have the pause flag
   - Runs in CI on every PR touching `frontend/src/components/*Card.tsx` or `routes/dispatch.py`
7. **AC-7** — Golden-state fixture: a JSON file `tests/fixtures/dashboard_golden_state.json` representing a realistic fleet state. The E2E test loads that state into postgres, renders the dashboard, asserts key text is present. Golden state updated when dashboard UI legitimately changes.

### Audit (AC-8)
8. **AC-8** — Grep-based regression guard: CI step that fails if the built bundle (`frontend/dist/assets/*.js`) is missing any of: `claimed_by`, `in_review`, `in_progress`, `paused`, `rate_limited`, `current_story`. Runs as part of Phase 11 pre-deploy gate.

### Documentation (AC-9)
9. **AC-9** — Update `.sdlc/software-development-guidance.md` to require E2E tests for any story adding a user-visible dashboard feature. "Unit tests pass" is insufficient evidence.

---

## Scope Classification

**Medium** — touches frontend (in_review UI + presence component), backend (reconciler, review endpoint wiring), tests (new E2E harness), and CI (regression grep). No DB schema changes beyond what STORY-507 already shipped.

Phase path: **1 → 4+6 → 7 → 8+PR → Done**.

---

## Technical Notes

### Why this is "Medium" not "Small"
The reconciler (AC-3) is the biggest piece — it's a new background task with its own state and alert channel. That plus an E2E test harness (AC-6) plus the bundle grep (AC-8) puts this past Small scope even if each individual piece is straightforward.

### State-sync root cause (today)
Multiple sources of truth for claim state without coordination:
- Central DB (ops-console postgres)
- Per-agent local queue (`/home/hermes/.hermes/work-queue.json`)
- Active SDK process on the agent VM
- Morris's view (the fleet-health snapshot)

When any of these diverge, the UI shows stale data. The reconciler (AC-3) treats the DB as canonical and corrects the others, with logging so we can see drift patterns.

### Why bundle-grep tests (AC-8) are valuable
Today's `in_review` gap would have been caught in 30 seconds by a CI step that grepped `frontend/dist/assets/*.js` for the expected feature strings. Cheap test, high catch rate for the "implementation missing but unit tests passed" failure mode.

### Keep E2E light
Playwright is heavy (~200MB install). If CI load becomes a concern, a lighter approach: serve the built bundle from a local static server, use `requests` to fetch, parse with BeautifulSoup. Doesn't catch JS-rendered content but catches the server-injected content + bundle references. Phase 4 should pick one.

### Dependencies

- STORY-507 (merged) — provides the paused/in_review status infrastructure
- STORY-510 (merged) — provides the quota endpoint (used in AgentCard)
- STORY-513 (in queue) — populates quota endpoint with real data
- STORY-514 (role-scoped API keys) — not blocking, parallel ok

---

## Out of Scope

- Redesigning the dashboard visual layout — this story is about correctness, not aesthetics
- Real-time push (WebSocket) updates — dashboard can continue polling; real-time is a separate story if needed
- Historical charts of agent state over time — only current-state rendering
- Mobile-responsive layout

---

## Success Measure

- **Mark never has to ask "why doesn't the dashboard show X" again.** Any drift between reality and dashboard is caught by the reconciler within 30 min and either auto-fixed or alerted.
- **CI catches missing frontend features** (AC-8 grep): if a dev adds a status enum value to the backend but forgets the UI, CI fails before merge.
- **E2E test catches rendering bugs** (AC-6): if a future refactor breaks the AgentCard's claimed-agent display, E2E fails before merge.
- **Zero unit-tests-pass-but-reality-fails incidents** for 30 days post-deploy.
