# STORY-541 — Agent Row Display Contract: Status, Quota, Busy

**Scope:** small
**Repo:** tech-dev-agents
**Target Branch:** main

## Context

STORY-513 shipped the quota backend and a percentage-bar UI in `AgentCard.tsx`. The bar shows `"23% · resets in 3h 12m"` with a green/yellow/red gradient. That matched what 513's seed asked for ("renders populated data"), but it doesn't match what Mark actually wants on the dashboard. He wants a per-agent row that reads like a status line from `top` — explicit numeric remaining quota, role label, real activity state. Today the dashboard says "Dan online, busy" when Dan's poller is dead and he can't claim a thing.

Three problems compound:

1. **Quota display** — a percentage bar tells you *how far through the window* but not *how much you have left*. Mark needs the absolute number ("47K tokens left, resets 4h 12m") to make routing decisions in real time.

2. **Status field is one-dimensional** — `online | idle | working | rate_limited | offline` collapses three independent signals (VM reachable, poller running, has active claim) into one value. Today: Dan's VM is reachable → `status=online` even though his `dispatch-poller.service` is dead. The dashboard lies about whether he can do work.

3. **`busy` is computed from the wrong source** — `busy = active_sessions > 0` derives from a health probe that counts SSH sessions and stale process handles. Devon shows `busy=true` because his last SDK invocation was recent, not because anything is currently running. The single source of truth for "doing work" is the dispatch table — `WHERE claimed_by=<agent> AND status='claimed'` — and that's never consulted.

The same "narrow test passes, contract fails" pattern that hit STORY-513 (mocked Loki, never verified real agents emit) and STORY-538 (wrong path in seed's Acceptance Diff) is at work here too. The fix is to lock the user-visible format in the seed so tests can pixel-check against it, not just verify "fields are rendered."

## Locked Display Contract

This is the exact text-level rendering each agent row MUST produce. The Phase 7 tests assert against this verbatim. If the spec needs to change later, it changes in this seed first, not in code.

```
Dan      [Developer]  ●online   ⏱ 47K tokens left · resets 4h 12m   📋 STORY-541 Phase 7
Derrick  [Developer]  ●idle     ⏱ 92K tokens left · resets 4h 12m   —
Daisy    [Developer]  ●working  ⏱ 38K tokens left · resets 4h 12m   📋 STORY-540 Phase 8
Devon    [Developer]  ●paused   ⏱ —                                  📋 STORY-539 (paused until 19:00 UTC)
Morris   [Manager]    ●online   ⏱ 14K tokens left · resets 2h 47m   —
Hermes   [Developer]  ●unreachable                                   —
```

Layout columns (left to right): name, role badge, status dot + label, quota line, current claim line.

| Status | Dot color | Meaning | Source of truth |
|--------|-----------|---------|-----------------|
| `online` | green | VM reachable + poller running + nothing claimed | `health.reachable && poller_active && !has_claim` |
| `idle` | green | VM reachable + poller running + nothing claimed (alias for online — pick one) | (use `idle` when no claim, drop `online`) |
| `working` | green | VM reachable + poller running + has claim + currently mid-phase | `has_claim && phase_in_progress` |
| `paused` | yellow | poller is alive but `paused_until` is in the future (rate-limit etc) | `paused_until > now()` |
| `stopped` | red | VM reachable but `dispatch-poller.service` is `inactive` or `failed` | poller probe |
| `unreachable` | gray | VM probe fails (SSH timeout, no response in 60s) | health probe |

Quota line:
- `⏱ 47K tokens left · resets 4h 12m` when `quota.remaining_tokens` is non-null
- `⏱ — ` when `quota.source == "no_data"` or `quota.remaining_tokens` is null
- `K` suffix for thousands (47K), `M` for millions (1.2M), exact integer below 1000
- `resets Xh Ym` for the time-to-reset; round minutes to nearest

Current claim line:
- `📋 STORY-XXX Phase N` when the agent has a `claimed` row in `dispatch_items`
- `📋 STORY-XXX (paused until HH:MM UTC)` when the row is claimed but agent is paused
- `—` (em-dash) when the agent has no claim

## Acceptance Diff

The implementation MUST add or modify these files with the must-contain tokens below.

- `frontend/src/components/AgentCard.tsx` — must-contain `tokensRemaining`, must-contain `formatTokens`, must-contain `📋`, must-contain `⏱`, must-**not**-contain `quota-bar-fill` (the old percentage bar is replaced)
- `frontend/src/components/AgentCard.tsx` — also must-contain a status-dot component reference (`StatusDot` or `statusDotColor`) covering all six states above
- `frontend/src/types/api.ts` — must-contain `tokens_remaining`, must-contain `'paused' | 'stopped' | 'unreachable'` in the AgentStatus union (additions to whatever already exists)
- `tech_dev_agents/ops_console/routes/_status.py` — must-contain `paused`, must-contain `stopped`, must-contain `unreachable` (new states added to `_STATUS_MAP`); must-contain a function or branch that derives status from `(reachable, poller_active, paused_until, has_claim)` not just `health.status`
- `tech_dev_agents/ops_console/routes/agents.py` — must-contain `claimed_by=` in a query (busy derived from dispatch table), must-**not**-contain `busy=bool((health.active_sessions if health else 0) > 0)` (the old computation is gone)
- `tech_dev_agents/ops_console/services/dispatch_db_service.py` — must-contain `async def has_active_claim` or equivalent helper for the agents route to call
- `tech_dev_agents/ops_console/services/loki_client.py` — must-contain `remaining_tokens` (the field already exists in `QuotaInfo`; this story makes sure it's populated, not always None)
- `frontend/src/__tests__/AgentCard.contract.test.tsx` — NEW. must-contain `def test_renders_locked_contract_for_each_status` (or the JS equivalent), must-contain at least one inline expected string `"⏱ 47K tokens left"`, must-contain at least one inline expected string `"📋 STORY"`, must-contain `"●paused"`
- `tests/ops_console/test_agent_status_taxonomy.py` — NEW. must-contain `def test_status_paused_when_paused_until_in_future`, must-contain `def test_status_stopped_when_poller_inactive`, must-contain `def test_status_working_when_has_claim_and_mid_phase`, must-contain `def test_busy_from_dispatch_table_not_health_probe`
- `e2e/dashboard-agent-row.spec.ts` — NEW Playwright spec, MANDATORY (frontend per `.sdlc/skills/phase-7/SKILL.md` line 58). must-contain `@smoke`, must-contain `'●working'`, must-contain `'⏱'`, must-contain `'📋 STORY'`, must-contain `'●paused'`, must-contain `'●stopped'`, must-contain `'●unreachable'`, must-contain `toHaveScreenshot`
- `e2e/screenshots/` — directory with at minimum `dashboard-all-states.png` and one per-row crop per state (`row-idle.png`, `row-working.png`, `row-paused.png`, `row-stopped.png`, `row-unreachable.png`). Screenshots are committed; they're the visual contract record.
- `.github/workflows/test.yml` — must-contain `playwright`, so CI runs the smoke specs and gates merges on them. Without this, the visual contract isn't actually enforced.

## Test Criteria

Every assertion below must have a pytest-level test (or vitest/jest for frontend) that fails RED before implementation and passes GREEN after.

1. **Display contract — text-level rendering**
   - For each of the 6 status values, `<AgentCard>` rendered with a fixture matching the contract above produces output that contains the expected status label (`●working`, `●paused`, etc) and quota line. Tests assert against substrings from the locked contract — not "the component rendered without errors."
   - The token formatter: `formatTokens(47_000)` → `"47K"`, `formatTokens(1_234_567)` → `"1.2M"`, `formatTokens(842)` → `"842"`, `formatTokens(null)` → `"—"`. Three explicit unit tests.
   - The reset-time formatter (already exists for percentage UI): keep the existing implementation, just verify it's still called. `formatResetTime(252)` → `"4h 12m"`.

2. **Status taxonomy — backend derivation**
   - Given `(reachable=true, poller_active=true, paused_until=null, has_claim=false)` → status is `idle`.
   - Given `(reachable=true, poller_active=true, paused_until=null, has_claim=true, phase_in_progress=true)` → status is `working`.
   - Given `(reachable=true, poller_active=true, paused_until=now()+3600, has_claim=true)` → status is `paused`.
   - Given `(reachable=true, poller_active=false)` → status is `stopped`. (No matter what else is true.)
   - Given `(reachable=false)` → status is `unreachable`. (Highest precedence.)
   - The `online` legacy state may map to `idle` for backward compat in the API layer, but new code emits the six-state taxonomy.

3. **`busy` derived from dispatch state**
   - `busy = exists(dispatch_items WHERE claimed_by=<name> AND status='claimed')`.
   - Test: a fixture with no claimed rows for an agent → `busy=false` even if `health.active_sessions > 0`.
   - Test: a fixture with a claimed row → `busy=true` even if `health.active_sessions == 0`.
   - The old computation `busy=bool((health.active_sessions if health else 0) > 0)` MUST NOT appear in the codebase anywhere.

4. **Quota line populates correctly**
   - When `quota.remaining_tokens` is a positive int → render `"⏱ 47K tokens left · resets 4h 12m"` (with the actual number).
   - When `quota.source == "no_data"` → render `"⏱ —"` exactly. No fake number, no "0 tokens left" (which would imply we know they're at zero).
   - When `quota.remaining_tokens == 0` AND source is `loki` → render `"⏱ 0 tokens · resets …"` (the actual zero state, distinguishable from no-data).
   - The Loki aggregator's `remaining_tokens` field MUST be populated when `[USAGE]` lines are present in the time window. If it's silently null when data exists, that's a backend bug to fix here.

5. **End-to-end against the live dashboard**
   - Run the frontend test suite (vitest) → all snapshots match the locked contract.
   - Run the backend tests → status taxonomy + busy derivation are correct against fixture DB rows.
   - The integration loop: dispatch a story, claim it from daisy → daisy's row in `/api/agents` shows `status=working`, `busy=true`, `current_story=STORY-XXX`, `current_phase=N`. Pause daisy via the new STORY-538 release primitive → row shows `status=paused`, claim line says `(paused until HH:MM UTC)`.

## Validation

**MANDATORY: this is frontend work, so Playwright is REQUIRED per the SDLC** (see `.sdlc/skills/phase-7/SKILL.md` line 58 — frontend test code lives in `e2e/` as Playwright specs; `.sdlc/agents/phase-10-operations.md` mandates `npx playwright test --grep @smoke` post-deploy). Vitest/jest unit tests for the components are not sufficient. The agent MUST produce a Playwright spec that drives a real browser, screenshots the rendered dashboard, and asserts against the locked contract.

### Playwright spec requirements (Phase 7)

Add `e2e/dashboard-agent-row.spec.ts` with `@smoke` tag. The spec MUST:

1. Boot a headless browser, navigate to the dashboard with seeded fixture data covering all six status states (the test harness mocks `/api/agents` to return one row per state).
2. For each state, assert via `page.getByText(...)` that the rendered row contains the exact strings from the Locked Display Contract — `"⏱ 47K tokens left · resets 4h 12m"`, `"●working"`, `"📋 STORY-540 Phase 8"`, etc.
3. Take a full-page screenshot to `e2e/screenshots/dashboard-all-states.png` and a per-row crop for each state to `e2e/screenshots/row-<state>.png`. Commit the screenshots — they're the visual contract record.
4. Use `expect(page).toHaveScreenshot()` with a baseline so future regressions surface as image diff failures, not just text diff failures.

Phase 8 MUST run `npx playwright test --grep @smoke` and confirm GREEN before marking complete. The CI workflow (`.github/workflows/test.yml`) gets a new job that runs Playwright against a built frontend bundle — without this, the test never gates.

### Manual validation steps (after deploy)

1. Run `npx playwright test --grep @smoke` against the deployed dashboard URL. All assertions GREEN, screenshots match baselines.
2. Open `https://tech-dev-agents.gorillacommerce.ai` in a browser. Each agent row matches the layout in the Locked Display Contract section. Mark eyeballs the rendered output and confirms it reads like the mockup.
3. From a terminal, stop dan's poller manually: `ssh dan "sudo systemctl stop dispatch-poller"`. Within 60 seconds, dan's row flips from `●idle` to `●stopped`. The dot color changes from green to red.
4. Trigger a real claim: dispatch a small story to daisy. Within 30 seconds her row shows `●working` with the story id and phase. When she completes and the claim clears, it returns to `●idle`.
5. Force a 429 path on devon: write `/var/run/dispatch-poller-paused-until` with a future timestamp. Within 60 seconds his row shows `●paused` with the paused-until time in the claim line.
6. Disconnect derrick's VM (or simulate by killing his SSH listener for 60s). His row flips to `●unreachable` with a gray dot. Restoring connectivity returns him to the appropriate state.

If any of these don't render exactly as the contract says, the implementation is incomplete and the story does not ship — even if all unit tests and Playwright specs pass.

## Implementation Notes for the SDK Agent

- The frontend test file should use vitest's `expect(screen.getByText("⏱ 47K tokens left · resets 4h 12m")).toBeInTheDocument()` (or equivalent) so the assertion is on user-visible text, not implementation details. If the renderer uses different layout components internally, tests should still pass as long as the rendered text matches the contract.
- Status precedence order matters when computing on the backend: `unreachable > stopped > paused > working > idle`. Higher-precedence states win even if a lower one is also "true." Document this in the `_status.py` docstring.
- For the new `has_active_claim` DB helper: just `SELECT 1 FROM dispatch_items WHERE claimed_by = $1 AND status = 'claimed' LIMIT 1`. Don't add fancy caching; this runs once per agent per `/api/agents` call (5 agents × every 30s polling = trivial load).
- The poller-active probe needs to be cheap. Options: (a) an HTTP endpoint on each VM that returns 200 if poller is up — but that needs another deployable, (b) check via the existing health probe that already SSHes — add `systemctl is-active dispatch-poller` to the same call. Option (b) is what STORY-507 already does for some checks; reuse that path.
- DO NOT touch the existing percentage-bar code in a "fix it later" way. Delete it. The new contract replaces it. Half-done UI changes are exactly what got us here.

Phase 7 writes the tests (RED). Phase 8 implements (GREEN). Phase 8 also runs `cd frontend && npm run build && tar czf /tmp/dist.tar.gz dist/ && scp` etc to deploy the frontend bundle to the ops-console VM, since the Dockerfile doesn't build the frontend. (See the existing STORY-538 / STORY-535 history for the deploy pattern.)
