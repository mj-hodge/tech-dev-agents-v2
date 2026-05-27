# STORY-508: Morris Adaptation for STORY-507 Observability

> Phase 1 | Scope: **Medium** | Created: 2026-04-21
> **Blocked by STORY-507** — must merge before this can start (depends on paused status enum, AC-8 metrics, AC-9 alerts, AC-10 structured logs)
> Advance: auto (automated dispatch path — 1 → 4+6 → 7 → 8+PR → Done)

---

## Problem Statement

STORY-507 ships new infrastructure (paused status, Prometheus metrics, Grafana alerts, structured log events, pre-claim budget checks, automatic partial-PR elimination). None of that is useful unless Morris — the fleet manager bot — is updated to (a) react to the new signals, (b) stop the band-aid behaviors it learned to cope with the absence of those signals, and (c) enforce correctness guardrails so the new honest data doesn't immediately get re-contaminated.

Today, Morris's survival playbook is almost entirely workarounds:

- Opens "Partial" PRs manually when Phase 8 times out (code has no SIGTERM handler)
- Force-marks dispatch rows as `completed` even when work is abandoned (no `paused` status exists)
- Writes free-text "RESUME — do not start over" prompts (phase runner has no resume logic)
- Opens re-do story IDs (STORY-505 as "redo of 446") — duplicating work because paused→re-claim is not a thing
- Polls fleet state every 30 min because there's no alerting

After STORY-507 merges, every one of those behaviors becomes wrong:

- AC-4 opens partial state automatically — Morris opening a second "Partial" PR creates duplicates
- AC-5 introduces `paused` — Morris still writing `completed` contaminates the very metric we're building to detect regressions
- AC-1/2 handle resume at the phase-runner level — Morris's prompt text never reaches that layer
- AC-6 makes paused→re-claim automatic — Morris re-dispatching under a new ID fragments the story history
- AC-9 alerts fire in real time — 30-min polling sees most events 29 min late

This story fixes Morris, turns off the band-aids, and adds the reactive alert-handler skill that actually consumes what 507 produces.

---

## Target User

- **Morris** — fleet manager bot. His behavior changes here.
- **Mark** — downstream consumer of Morris's Teams DMs and weekly reports. After 508, alerts arrive in seconds instead of ~30 min, and Morris's messages carry structured alert context instead of re-fetched snapshots.
- **STORY-507** — its metrics and alerts are the contract this story consumes. Most ACs here reference 507 ACs by ID.

---

## Acceptance Criteria

### Behavior removal (band-aids → off)
1. **AC-1** — Morris's `fleet-vigilance` skill has the "partial PR recovery" playbook **removed** (search for `Partial` / `preserve work` / `Morris opens` and delete those sections). Regression test: `grep -rn 'partial.*preserve\|Morris.*partial' deployment/vm/skills/` returns no matches in playbook steps.
2. **AC-2** — Morris's `review-prs` skill has the "partial-PR detection" and "stranded work auto-close" steps removed. Morris must not open, close, or comment on PRs whose title contains `Partial`; AC-4 of STORY-507 owns them now.
3. **AC-3** — Morris's `dispatch` skill drops manual `[RETRY 1/3]` retry loop logic; relies on AC-6 of STORY-507 for automatic re-claim of paused stories.
4. **AC-4** — Any direct DB write to `dispatch_items.status` by Morris is **removed**. A regression test scans Morris's skills/scripts for `UPDATE dispatch_items SET status` and fails if found. All state transitions go through the API (which 507 enforces correctly).

### New skill: `alert-handler`
5. **AC-5** — `deployment/vm/skills/alert-handler/SKILL.md` exists with per-alert playbooks for each AC-9 rule from STORY-507:
   - `ClaimTimeouts > 3/min for 5m` → health-check ops-console and agent-gateway, escalate Mark if both healthy
   - `PartialPRopens > 0` → file a regression bug (AC-4 of 507 broke); do NOT auto-remediate
   - `Phase 8 P95 > 2× baseline` → pull AC-10 structured logs for last N sessions, diff against `metrics-baselines.md`, post summary to Mark
   - `Paused > 24h without re-claim` → check all agents' budget state, force unclaim if stuck, re-enqueue with priority bump via `POST /dispatch/priority`
   - `RateLimitDeferrals spike` (all agents defer simultaneously) → Teams DM Mark with reset ETAs
6. **AC-6** — A Grafana webhook → Teams bridge (one new route on ops-console, path `POST /api/alerts/grafana`) forwards alert payloads to Morris's Teams channel as structured messages. Message carries alert name, severity, metric value, labels (agent, repo, story_id), since-timestamp. Morris's alert-handler skill matches on alert name and executes the matching playbook.
7. **AC-7** — Every fired alert and Morris's response is logged to `/home/hermes/state/morris/alert-log.md` with timestamp, alert name, action taken, outcome. Audit trail for correctness review.

### Baseline + reporting
8. **AC-8** — `metrics-baselines.md` exists in Morris's state dir with rolling P50/P95 per 507 metric, refreshed weekly by a new cron (`update-baselines.sh`). Without this, "2× baseline" alerts have no reference. Initial baseline seed from the first 7 days of post-507 data.
9. **AC-9** — A new weekly report skill (`weekly-fleet-report`) generates a Friday summary:
   - Partial-PR opens count (target per 507 success measure: 0)
   - Within-cycle completion rate (target: ≥95%)
   - Medium-scope dispatch → merge P50 (target: <4h)
   - New vs. resolved alert counts
   - Top 3 flakey stories (most dispatch cycles)
   Posted to Mark via Teams DM; archived as `state/morris/weekly-reports/YYYY-WW.md`.

### Cron cadence shift
10. **AC-10** — `morris-fleet-check.sh` cron cadence drops from `*/30 * * * *` → `0 */2 * * *` (every 2 hours). Reactive behavior moves to alert-handler (real time). Weekly sweep remains `0 9 * * 1` for repo-drift detection. No change to `morris-fleet-check.sh` logic except a new top-of-file comment explaining the cadence reduction.
11. **AC-11** — Fallback guard: if Morris's alert-handler sees no Grafana webhook fires in 60 min AND his cron hasn't run in 90 min, he falls back to 30-min polling and DMs Mark "observability may be degraded — falling back to polling mode." Auto-resumes event-driven mode once a Grafana fire or cron run succeeds.

### Ops-console dashboard additions
12. **AC-12** — Dashboard gains three panels:
    - `paused` column/tab alongside pending/claimed/completed in queue view (consumes AC-5 of 507)
    - Alert-status panel (live: currently firing alerts, since when, severity) fed by `GET /api/alerts/active`
    - Per-agent Claude Code budget gauge (AC-7 of 507 exposes; this surfaces)
13. **AC-13** — `GET /api/alerts/active` endpoint proxies from Grafana's alert API. Returns JSON array of firing alerts with fields: name, severity, started_at, labels, annotations. Used by panel above.

### Correctness guardrails
14. **AC-14** — New ops-console endpoint `POST /api/dispatch/priority` with `{story_id, priority}` body. Updates a new `priority: int` column on `dispatch_items` (default 0, higher = sooner). `/dispatch/next` ORDER BY changes to `priority DESC, enqueued_at ASC`. Today Mark and I use raw `UPDATE enqueued_at = '2020-01-01'` hacks via `az vm run-command`; AC-14 gives us the proper API. Migration + backfill to set `priority=0` on all existing rows.
15. **AC-15** — Audit test: a CI step scans all skills and scripts for:
   - `UPDATE dispatch_items SET status` (banned — status goes through API)
   - `enqueued_at = '20` (banned — priority goes through `/api/dispatch/priority`)
   - `gh pr create.*Partial` (banned — AC-4 of 507 owns partial PRs)
   Fails CI if any match. Prevents regressions.

---

## Scope Classification

**Medium** — four subsystems touched, but each is a moderate change:
- `deployment/vm/skills/` (alert-handler skill new, fleet-vigilance + review-prs + dispatch pruned)
- `tech_dev_agents/ops_console/routes/` (alerts.py new, dispatch.py gets priority endpoint)
- `frontend/src/components/` (3 panels: paused column, alert-status, budget gauge)
- `scripts/migrations/` (priority column + backfill)
- `/etc/cron.d/morris-fleet-check` (cadence flip — trivial)

No brand-new subsystems. No significant test surface beyond the audit tests.

Phase path (automated dispatch): **1 → 4+6 → 7 → 8+PR → Done**.

Expected duration: ~1 day focused work.

---

## Technical Notes

### Dependency on STORY-507

This story cannot start until 507 is merged. Specifically, it consumes:
- `dispatch_items.status = 'paused'` (AC-5 of 507)
- Prometheus metric names (AC-8 of 507) — alert-handler plays book entries reference them
- Grafana alert names (AC-9 of 507) — alert-handler dispatch keys on these
- Structured log event schema (AC-10 of 507) — Phase-8-P95 playbook parses these
- Claude Code budget field (AC-7 of 507) — budget gauge renders from it

Phase 4 of this story should verify all five contracts match what 507 actually shipped (schema drift check).

### Why alert-handler is a NEW skill, not an extension of fleet-vigilance

`fleet-vigilance` is polling-mode-shaped: collect snapshot → reason over delta → act. `alert-handler` is event-shaped: receive event → match by alert name → execute playbook. Different control flow, different state, different failure modes. Mixing them into one skill would mean one skill file hitting both modes with conditionals throughout — harder to test, harder for Morris to follow. Keep them separate; both persist.

### Webhook delivery (AC-6) — routing

Grafana fires alerts to a single webhook URL. We add `POST /api/alerts/grafana` on ops-console. It:
1. Validates signature (shared secret in Grafana + ops-console config)
2. Formats payload as a Teams-compatible adaptive card
3. Sends via Graph API to Morris's team channel (same channel Morris already uses for fleet-health digests)
4. Also writes to `alert-log.md` (so log exists even if Morris's Teams bot misses the message)

Morris's Teams-bot reader already exists for DMs from Mark; this piggybacks on it.

### Audit tests (AC-15) — why CI, not a test file

Typical pytest can't scan skill markdown for banned patterns reliably. Add this as a CI workflow step or a pre-commit hook in `.sdlc/`. Cheap to implement (one grep command per rule), catches the worst regression classes (Morris reverting to band-aid behavior under pressure).

### What stays unchanged

- Morris's core identity (`project_dan_identity.md` memory — Morris is manager, never implements)
- `review-prs` auto-approval logic for small PRs
- Morris's GitHub token, Teams credentials, VM setup
- The morris-fleet-check.sh script itself except for the cadence and a header comment

Nothing in this story changes *what Morris is*. It changes *what Morris reacts to and how fast*.

---

## Out of Scope

- Full rewrite of Morris's skills architecture — only the specific skills named in ACs
- Grafana dashboard JSON / alert rule definitions — those belong in STORY-507 AC-9
- Prometheus scraping config / retention policy — also STORY-507 infrastructure
- Teams adaptive-card design polish — functional delivery is enough; visual refinement can be follow-up
- Metric aggregation / roll-up across agents — baselines are per-agent; cross-agent views deferred
- Historical replay: re-running alerts against past structured logs to "test" playbooks — nice to have, not required
- Replacement for `morris-fleet-check.sh` entirely — it still runs (2h cadence) as a safety net. Rewriting it as event-driven is a separate story when we're confident in alerting coverage.

---

## Success Measure (check 2 weeks post-deploy)

- **Alert median time-to-Teams-DM**: <30s (from current ~29m worst case)
- **Morris turn-budget spent on band-aid behaviors** (partial-PR opens, manual retries, status force-writes): **0** (from current ~30%)
- **Weekly report generated and delivered**: 1 per Friday
- **Audit-test CI failures in skills/scripts**: 0 (means regression guard is effective)
- **Dispatch queue rows with `priority > 0` created via API**: ≥1 per week (means `POST /api/dispatch/priority` is used instead of DB hacks)

If Morris's fleet-health log still shows partial-PR-recovery actions 2 weeks after this ships, AC-1/2/3 weren't enforced hard enough; file follow-up.
