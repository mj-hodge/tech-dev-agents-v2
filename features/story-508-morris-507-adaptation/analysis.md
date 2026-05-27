# STORY-508: Morris Adaptation for STORY-507 Observability — Analysis

> Phase 4 | Scope: **Medium** | Date: 2026-04-21

---

## Affected Files

### Skills (modification — band-aid removal)
| File | Change Type | ACs |
|------|------------|-----|
| `deployment/vm/skills/fleet-vigilance/SKILL.md` | Modify — remove partial-PR recovery sections (Remediation A references to "Partial" PRs, auto-commit/push of stranded work) | AC-1 |
| `deployment/vm/skills/pr-review/SKILL.md` | Modify — remove partial-PR detection/auto-close steps (currently no explicit "Partial" handling found — verify at impl time) | AC-2 |

### Skills (new)
| File | Change Type | ACs |
|------|------------|-----|
| `deployment/vm/skills/alert-handler/SKILL.md` | New — per-alert playbooks for each AC-9 rule from STORY-507 | AC-5, AC-7 |
| `deployment/vm/skills/weekly-fleet-report/SKILL.md` | New — Friday summary generator with KPIs | AC-9 |

### Ops-console backend (new routes + modifications)
| File | Change Type | ACs |
|------|------------|-----|
| `tech_dev_agents/ops_console/routes/alerts.py` | Modify — add `POST /api/alerts/grafana` webhook receiver, `GET /api/alerts/active` | AC-6, AC-13 |
| `tech_dev_agents/ops_console/routes/dispatch.py` | Modify — add `POST /api/dispatch/priority` endpoint, update `next_pending` ORDER BY | AC-14 |
| `tech_dev_agents/ops_console/services/dispatch_db_service.py` | Modify — add `set_priority()` method, update `next_pending` query to `ORDER BY priority DESC, enqueued_at ASC` | AC-14 |
| `tech_dev_agents/ops_console/models/responses.py` | Modify — add `PriorityRequest`/`PriorityResponse` models, add `priority` field to `DispatchItem` | AC-14 |

### Frontend
| File | Change Type | ACs |
|------|------------|-----|
| `frontend/src/components/DispatchQueue.tsx` | Modify — add paused tab/column alongside pending/claimed/completed | AC-12 |
| `frontend/src/components/AlertStatusPanel.tsx` | New — live firing-alerts panel fed by `GET /api/alerts/active` | AC-12 |
| `frontend/src/components/BudgetGauge.tsx` | New — per-agent Claude Code budget gauge | AC-12 |
| `frontend/src/components/DashboardLayout.tsx` | Modify — wire in new panels | AC-12 |

### Database migrations
| File | Change Type | ACs |
|------|------------|-----|
| `scripts/migrations/006_dispatch_priority.sql` | New — add `priority INT DEFAULT 0` column, backfill existing rows | AC-14 |

### Cron / scripts
| File | Change Type | ACs |
|------|------------|-----|
| `deployment/vm/morris-fleet-check.sh` | Modify — add header comment explaining cadence reduction | AC-10 |
| `/etc/cron.d/morris-fleet-check` (on Morris VM) | Modify — change `*/30 * * * *` → `0 */2 * * *` | AC-10 |
| `deployment/vm/scripts/update-baselines.sh` | New — weekly cron to refresh metrics baselines | AC-8 |

### State files (created at runtime, not in repo)
| File | Change Type | ACs |
|------|------------|-----|
| `/home/hermes/state/morris/alert-log.md` | New — audit trail for fired alerts + Morris responses | AC-7 |
| `/home/hermes/state/morris/metrics-baselines.md` | New — rolling P50/P95 per STORY-507 metric | AC-8 |
| `/home/hermes/state/morris/weekly-reports/YYYY-WW.md` | New — weekly report archive | AC-9 |

### CI / audit
| File | Change Type | ACs |
|------|------------|-----|
| `.github/workflows/audit-skills.yml` OR `tests/test_audit_skills.py` | New — CI step scanning for banned patterns | AC-15 |

---

## Current Behavior

### Morris fleet-vigilance (polling model)
- Runs every 30 minutes via cron (`*/30 * * * *` → `morris-fleet-check.sh`)
- Stage 1 (bash): collects queue state, per-agent SSH probes, ghost-completion audit, failed stories, open PRs (~5s)
- Stage 2 (SDK): passes JSON blob to `claude -p` with fleet-vigilance skill for reasoning (~5 turns, ~10 budget)
- Cost: ~480 turns/day at current cadence
- **Band-aids present**: Check 0d auto-commits/pushes stranded work and creates PRs for branches without them; Remediation A handles stuck claims by clearing local work queues and re-enqueueing

### Morris PR review
- `pr-review/SKILL.md` defines a generic Claude-based PR review flow
- No explicit "Partial PR" handling found in the current skill file — the band-aid behavior described in the seed (detecting partial-PR titles, auto-closing stranded work PRs) may be in Morris's memory/instructions rather than in the skill file itself
- PR review skill does not filter by PR title pattern

### Dispatch queue
- Status transitions managed by `DispatchDBService` — all through async methods, no raw SQL in routes
- `next_pending()` query: `ORDER BY enqueued_at ASC` (pure FIFO, no priority)
- No `priority` column exists on `dispatch_items`
- `paused` status already supported (STORY-507 migration 004 applied)
- Priority hacks today: Mark manually runs `UPDATE enqueued_at = '2020-01-01'` via `az vm run-command` to bump items

### Alerting
- `GET /alerts` endpoint exists — returns health/Loki/cost alerts from `alert_service`
- No Grafana webhook receiver
- No active-alerts proxy endpoint
- No real-time alert routing to Teams

### Frontend dashboard
- `DispatchQueue.tsx`: tabs for Queue/History; `paused` renders as gray badge via default switch case
- No dedicated Paused section/tab
- No alert-status panel
- No per-agent budget gauge
- `AgentCard.tsx` already shows quota bar (percent_used, reset_in_minutes from STORY-496)

---

## Proposed Approach

### Phase 1: Band-aid removal (AC-1 through AC-4)
1. **fleet-vigilance/SKILL.md**: Remove Check 0d "auto-commit + auto-push + auto-PR" behavior for stranded work (this is the "partial PR recovery" playbook). Add a note that AC-4 of STORY-507 handles partial-PR creation automatically.
2. **pr-review/SKILL.md**: Add explicit exclusion — "Do NOT review, comment on, close, or create PRs whose title contains 'Partial'." This prevents Morris from interfering with STORY-507's automated partial-PR flow.
3. **fleet-vigilance/SKILL.md**: Remove any `[RETRY 1/3]` manual retry logic references (AC-3). Add note that STORY-507 AC-6 handles paused→re-claim automatically.
4. **Audit test** (AC-4, AC-15): Create `tests/test_audit_skills.py` that greps all skills/scripts for banned patterns: `UPDATE dispatch_items SET status`, `enqueued_at = '20`, `gh pr create.*Partial`.

### Phase 2: New alert-handler skill (AC-5, AC-6, AC-7)
1. Create `deployment/vm/skills/alert-handler/SKILL.md` with playbooks keyed by Grafana alert name.
2. Add `POST /api/alerts/grafana` route on ops-console: validate shared-secret signature, format as Teams adaptive card, send via Graph API, write to alert-log.md.
3. Alert-handler skill reads incoming Teams messages, matches alert name → playbook, executes.

### Phase 3: Priority API + migration (AC-14)
1. Migration `006_dispatch_priority.sql`: `ALTER TABLE dispatch_items ADD COLUMN priority INT DEFAULT 0`.
2. `POST /api/dispatch/priority` endpoint: updates priority for a story.
3. Update `next_pending()` query to `ORDER BY priority DESC, enqueued_at ASC`.

### Phase 4: Cron cadence + fallback (AC-10, AC-11)
1. Update cron from `*/30` to `0 */2` on Morris VM.
2. Add fallback guard logic to alert-handler: if no webhook fires in 60min AND no cron in 90min → revert to 30-min polling + DM Mark.

### Phase 5: Baselines + weekly report (AC-8, AC-9)
1. Create `update-baselines.sh` cron script.
2. Create `weekly-fleet-report/SKILL.md` with KPI template.

### Phase 6: Dashboard panels (AC-12, AC-13)
1. Add `GET /api/alerts/active` route proxying Grafana alert API.
2. Add Paused tab/column to `DispatchQueue.tsx`.
3. Create `AlertStatusPanel.tsx` and `BudgetGauge.tsx` components.

---

## Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| STORY-507 contract drift — metric names, alert names, or structured log schema differ from what seed assumes | High | Phase 8 must verify 507's actual shipped schema before coding alert-handler playbooks. Add contract assertions to tests. |
| Grafana webhook delivery reliability — if Grafana→ops-console HTTP fails, alerts are silently dropped | Medium | AC-11 fallback guard catches this: no-webhook-in-60min triggers polling mode. Also: alert-log.md provides audit trail for missed alerts. |
| Band-aid removal too aggressive — fleet-vigilance Check 0d (auto-commit stranded work) may still be needed if STORY-507's SIGTERM handler doesn't cover all edge cases | Medium | Keep the Check 0d logic but gate it behind a flag: only fire if `paused` status is NOT present (i.e., old-mode fallback). Remove entirely after 2-week soak. |
| Priority column migration on production DB — adding a column with DEFAULT to a table with active traffic | Low | `ADD COLUMN ... DEFAULT 0` is non-blocking in PostgreSQL 11+ (the default is stored in pg_attribute, not written to every row). Safe for live traffic. |
| Alert-handler skill complexity — 5 distinct playbooks each with different remediation logic | Medium | Each playbook is a separate markdown section with clear trigger → action mapping. Test individually. |
| Cron cadence reduction — going from 30min to 2h means 90-minute blind spot if alerting fails | Medium | AC-11 fallback guard specifically addresses this: auto-reverts to 30min polling if observability degrades. |

---

## Dependencies

### Hard dependencies (must be merged before STORY-508 starts Phase 8)
- **STORY-507 PR #72** — provides: `paused` status enum, Prometheus metrics, Grafana alert rules, structured log events, Claude Code budget field, SIGTERM handler, per-file commits, partial-PR automation

### Soft dependencies (nice-to-have but not blocking)
- **Grafana instance configured** — AC-6 webhook receiver needs a Grafana instance sending alerts. If Grafana isn't set up yet, the webhook route can be built and tested with mock payloads.
- **Teams Graph API channel** — AC-6 needs Morris's Teams channel ID for posting adaptive cards. Already exists (fleet-health digests go there today).

### No dependency on
- STORY-440 (small scope, unrelated)
- STORY-510 (quota endpoint hardening — parallel, no overlap)
