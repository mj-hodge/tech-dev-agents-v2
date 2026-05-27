# Test Design — STORY-508: Morris Adaptation for STORY-507 Observability

**Phase:** 7 — Test Design (RED state)
**Story:** STORY-508
**Date:** 2026-04-21
**Author:** Phase 7 automated test design

---

## Overview

STORY-508 delivers observability and operational hardening for Morris following STORY-507's
partial-PR lifecycle changes. Tests are written first in RED state; Phase 8 implementation
makes them GREEN.

### Test Philosophy

- **Band-aid removal tests (AC-1, AC-2):** Assert _absence_ of patterns that currently exist.
  These are RED because the bad patterns still exist in the codebase.
- **Regression guards (AC-3, AC-4, AC-15):** Assert absence of patterns that must never appear.
  These pass immediately (GREEN) if the patterns are absent, and serve as CI guards going forward.
- **New feature tests (AC-5 through AC-14):** Assert presence of files, content, routes, models,
  and database behavior that does not yet exist. All RED until Phase 8 ships.

### Test File Locations

| File | Covers ACs |
|------|-----------|
| `tests/test_audit_skills.py` | AC-1, AC-2, AC-3, AC-4, AC-15 |
| `tests/ops_console/test_508_priority_api.py` | AC-14 |
| `tests/ops_console/test_508_grafana_webhook.py` | AC-6, AC-7, AC-13 |
| `tests/test_508_skill_files.py` | AC-5, AC-7 (skill side), AC-8, AC-9, AC-10, AC-11 |
| `tests/test_508_frontend.py` | AC-12 |

---

## Test Matrix

| AC | Test ID | Test Type | Description | Expected RED Reason |
|----|---------|-----------|-------------|---------------------|
| AC-1 | T01-01 | File audit | fleet-vigilance/SKILL.md has no `git add -A && git commit` | Pattern currently present in Check 0d |
| AC-1 | T01-02 | File audit | fleet-vigilance/SKILL.md has no `git push origin HEAD` | Pattern currently present in Check 0d |
| AC-1 | T01-03 | File audit | fleet-vigilance/SKILL.md has no `gh pr create --title 'STORY-XXX'` | Pattern currently present in Check 0d |
| AC-1 | T01-04 | grep scan | No `partial.*preserve` or `Morris.*partial` in any skill | Should pass (GREEN guard) |
| AC-2 | T02-01 | File audit | review-prs/SKILL.md exists | File exists (GREEN) |
| AC-2 | T02-02 | File audit | review-prs/SKILL.md contains "Partial" | Exclusion not yet present — RED |
| AC-2 | T02-03 | File audit | review-prs/SKILL.md contains explicit "Do NOT" directive for Partial PRs | Exclusion not yet present — RED |
| AC-3 | T03-01 | grep scan | No `[RETRY N/N]` loop in any skill | Should pass (GREEN guard, pattern absent) |
| AC-4 | T04-01 | grep scan | No `UPDATE dispatch_items SET status` in skills/scripts | Should pass (GREEN guard, pattern absent) |
| AC-5 | T05-01 | File exists | `alert-handler/SKILL.md` exists | File not yet created — RED |
| AC-5 | T05-02 | Content | Contains "ClaimTimeoutsHigh" section | File not yet created — RED |
| AC-5 | T05-03 | Content | Contains "PartialPROpens" section | File not yet created — RED |
| AC-5 | T05-04 | Content | Contains "Phase8P95High" section | File not yet created — RED |
| AC-5 | T05-05 | Content | Contains "PausedOver24h" section | File not yet created — RED |
| AC-5 | T05-06 | Content | Contains "RateLimitDeferralSpike" section | File not yet created — RED |
| AC-5 | T05-07 | Content | Contains audit logging reference to alert-log.md | File not yet created — RED |
| AC-5 | T05-08 | YAML | Frontmatter `name: alert-handler` | File not yet created — RED |
| AC-6 | T06-01 | Route | POST /api/alerts/grafana with valid HMAC → 200 | Endpoint not yet created — RED (404) |
| AC-6 | T06-02 | Route | POST /api/alerts/grafana with invalid signature → 401 | Endpoint not yet created — RED (404) |
| AC-6 | T06-03 | Route | POST /api/alerts/grafana with empty alerts → 200 `{"accepted":0}` | Endpoint not yet created — RED (404) |
| AC-6 | T06-04 | Route | POST /api/alerts/grafana without secret configured still responds | Endpoint not yet created — RED (404) |
| AC-7 | T07-01 | Route+IO | After successful webhook, alert-log.md is created/appended | Endpoint not yet created — RED |
| AC-7 | T07-02 | Route+IO | Log entry contains timestamp, alert name, severity, agent label | Endpoint not yet created — RED |
| AC-7 | T07-03 | Skill | alert-handler/SKILL.md references `/home/hermes/state/morris/alert-log.md` | File not yet created — RED |
| AC-8 | T08-01 | File exists | `deployment/vm/scripts/update-baselines.sh` exists | File not yet created — RED |
| AC-8 | T08-02 | Executable | update-baselines.sh is executable | File not yet created — RED |
| AC-8 | T08-03 | Content | Contains PROMETHEUS_URL variable | File not yet created — RED |
| AC-8 | T08-04 | Content | References metrics-baselines.md | File not yet created — RED |
| AC-8 | T08-05 | Content | Contains P95/histogram_quantile concept | File not yet created — RED |
| AC-9 | T09-01 | File exists | `weekly-fleet-report/SKILL.md` exists | File not yet created — RED |
| AC-9 | T09-02 | Content | Contains "partial" PR KPI section | File not yet created — RED |
| AC-9 | T09-03 | Content | Contains "completion rate" KPI | File not yet created — RED |
| AC-9 | T09-04 | Content | Contains "P50" dispatch-to-merge metric | File not yet created — RED |
| AC-9 | T09-05 | Content | Contains alert count section | File not yet created — RED |
| AC-9 | T09-06 | Content | Contains cron schedule (`0 10 * * 5`) | File not yet created — RED |
| AC-9 | T09-07 | Content | Contains Teams DM delivery instruction | File not yet created — RED |
| AC-9 | T09-08 | Content | References weekly-reports/ archive path | File not yet created — RED |
| AC-10 | T10-01 | File audit | morris-fleet-check.sh contains comment about 2-hour cadence or STORY-508 | Comment not yet added — RED |
| AC-11 | T11-01 | Content | alert-handler/SKILL.md contains "fallback" section | File not yet created — RED |
| AC-11 | T11-02 | Content | alert-handler/SKILL.md references 60-minute webhook check | File not yet created — RED |
| AC-11 | T11-03 | Content | alert-handler/SKILL.md references 90-minute cron check | File not yet created — RED |
| AC-11 | T11-04 | Content | alert-handler/SKILL.md references polling mode fallback | File not yet created — RED |
| AC-11 | T11-05 | Content | alert-handler/SKILL.md says DM Mark on fallback | File not yet created — RED |
| AC-12 | T12-01 | File exists | `AlertStatusPanel.tsx` exists | File not yet created — RED |
| AC-12 | T12-02 | Content | AlertStatusPanel.tsx references `/api/alerts/active` | File not yet created — RED |
| AC-12 | T12-03 | Content | AlertStatusPanel.tsx has empty-state text | File not yet created — RED |
| AC-12 | T12-04 | File exists | `BudgetGauge.tsx` exists | File not yet created — RED |
| AC-12 | T12-05 | Content | BudgetGauge.tsx references percent_used or budget | File not yet created — RED |
| AC-12 | T12-06 | Content | DispatchQueue.tsx has `case 'paused':` in statusBadge | Currently absent — RED |
| AC-12 | T12-07 | Content | DispatchQueue.tsx paused case has distinct color (bg-purple) | Currently absent — RED |
| AC-12 | T12-08 | Content | DashboardLayout.tsx imports AlertStatusPanel | Import not yet present — RED |
| AC-12 | T12-09 | Content | DashboardLayout.tsx imports BudgetGauge | Import not yet present — RED |
| AC-13 | T13-01 | Route | GET /api/alerts/active → 200 with alerts/count/fetched_at | Endpoint not yet created — RED (404) |
| AC-13 | T13-02 | Route | Grafana unreachable → `{"alerts":[],"error":"grafana_unreachable"}` | Endpoint not yet created — RED (404) |
| AC-13 | T13-03 | Route | Each alert has name/severity/started_at/labels/annotations | Endpoint not yet created — RED (404) |
| AC-14 | T14-01 | Model | `PriorityRequest` importable from responses.py | Class not yet defined — RED (ImportError) |
| AC-14 | T14-02 | Model | `PriorityResponse` importable from responses.py | Class not yet defined — RED (ImportError) |
| AC-14 | T14-03 | Model | PriorityRequest has story_id (str) and priority (int, 0-100) | Class not yet defined — RED |
| AC-14 | T14-04 | Model | PriorityResponse has story_id, priority, previous_priority, status | Class not yet defined — RED |
| AC-14 | T14-05 | DB | DispatchItem has priority field defaulting to 0 | Field not yet added — RED |
| AC-14 | T14-06 | DB (PG) | set_priority() updates priority on pending item | Method not yet implemented — RED |
| AC-14 | T14-07 | DB (PG) | set_priority() raises NotFoundError for unknown story | Method not yet implemented — RED |
| AC-14 | T14-08 | DB (PG) | set_priority() raises InvalidTransitionError for claimed story | Method not yet implemented — RED |
| AC-14 | T14-09 | DB (PG) | next_pending() returns higher-priority item first | Method not yet modified — RED |
| AC-14 | T14-10 | DB (PG) | next_pending() FIFO for same priority | Method not yet modified — RED |
| AC-14 | T14-11 | Route (PG) | POST /api/dispatch/priority → 200 with PriorityResponse | Route not yet created — RED (404) |
| AC-14 | T14-12 | Route (PG) | POST /api/dispatch/priority unknown story → 404 | Route not yet created — RED |
| AC-14 | T14-13 | Route (PG) | POST /api/dispatch/priority claimed story → 422 | Route not yet created — RED |
| AC-14 | T14-14 | Route | POST /api/dispatch/priority priority=-1 → 422 | Route not yet created — RED |
| AC-14 | T14-15 | Route | POST /api/dispatch/priority priority=101 → 422 | Route not yet created — RED |
| AC-14 | T14-16 | Migration | `scripts/migrations/006_dispatch_priority.sql` exists | File not yet created — RED |
| AC-14 | T14-17 | Migration | 006 SQL contains ADD COLUMN priority | File not yet created — RED |
| AC-15 | T15-01 | grep scan | No `UPDATE dispatch_items SET status` in skills/scripts | Should pass (GREEN guard) |
| AC-15 | T15-02 | grep scan | No `enqueued_at = '20` priority hack in skills/scripts | Should pass (GREEN guard) |

---

## Prerequisites

### PostgreSQL

PG-dependent tests (T14-06 through T14-13) require:
- PostgreSQL accessible at `postgresql://ops_console:ops_console@localhost/ops_console_test`
- Migration `006_dispatch_priority.sql` applied before DB tests run

All PG-dependent tests carry `@pytest.mark.skipif(not _PG_AVAILABLE, reason="PostgreSQL not reachable")`.
Non-PG tests (model imports, skill file audits, frontend checks) have no PG dependency.

### External Services

Alert webhook tests (AC-6, AC-7) mock all Teams calls using `unittest.mock.patch`. No real
Teams or Grafana calls are made during testing.

---

## Coverage Summary by AC

| AC | Tests | Type | PG Required |
|----|-------|------|-------------|
| AC-1 | 4 | File audit + grep | No |
| AC-2 | 3 | File audit | No |
| AC-3 | 1 | grep | No |
| AC-4 | 1 | grep | No |
| AC-5 | 8 | File/content | No |
| AC-6 | 4 | Route (mock) | No |
| AC-7 | 3 | Route+file / skill content | No |
| AC-8 | 5 | File/content | No |
| AC-9 | 8 | File/content | No |
| AC-10 | 1 | File content | No |
| AC-11 | 5 | File/content | No |
| AC-12 | 9 | File/content | No |
| AC-13 | 3 | Route (mock) | No |
| AC-14 | 17 | Model/DB/Route/Migration | Partial (DB tests) |
| AC-15 | 2 | grep | No |
| **Total** | **74** | | |

---

## Notes

- AC-3, AC-4, AC-15 are regression guards: they are GREEN immediately if the forbidden patterns
  are absent, and turn RED the moment someone adds a banned pattern. They are included because
  CI should catch regressions before code review.
- AC-14 DB tests truncate `dispatch_items` between tests via the `db_pool` fixture; they share
  that fixture from the existing conftest pattern in `test_routes_dispatch.py`.
- The alert-log.md path in AC-7 tests (`/home/hermes/state/morris/alert-log.md`) is the
  production path. Tests use a `tmp_path`-backed override injected via app settings or monkeypatch.
