# STORY-916 — Queue Health SLOs: Grafana Alerts for Dispatch v2

## Overview

| Field        | Value |
|--------------|-------|
| Story        | STORY-916 |
| Date         | 2026-05-12 |
| Scope        | Small |
| Frontend     | false |
| Phase Path   | 1 → 7 → 8 → Done |
| Branch       | `story-916/queue-health-slos-grafana-alerts` |
| Datasource   | Postgres (preferred); Loki acceptable after STORY-915 lands |

---

## Problem Statement

The dispatch v2 queue has three known failure modes that currently go undetected until a human notices an agent stuck in the UI or a story never progresses:

1. **Stuck claimed job** — a job enters `state = 'leased'` and the agent never submits or fails. Heartbeat may be alive but no forward progress. Today there is no alert; on-call discovers it manually via the dashboard.
2. **Unlinked in_review** — a job transitions to `state = 'in_review'` (submitted event) but `dispatch_jobs.pr_number` is never populated (GitHub API miss or STORY-902 backfill gap). Without a PR link, Morris cannot auto-merge, and the story silently stalls.
3. **Redispatch loop** — a `correlation_key` is enqueued more than three times within 24 hours, indicating an agent is repeatedly failing and being re-dispatched without root-cause resolution. Left unchecked, this burns tokens indefinitely.

No Grafana alert rules exist today for dispatch v2 queue health. This story adds three alert rules with Postgres-native queries, routes each to the existing alert channel, and provides operator runbooks in `data-remediations/`.

---

## Scope

**Small** — Three self-contained Grafana alert rules (one YAML provisioning file), three runbook markdown files, and a synthetic test suite. No schema migrations. No backend code changes.

---

## Alert Specifications

### Alert 1 — Stuck Claimed (PAGE)

| Field       | Value |
|-------------|-------|
| UID         | `dispatch-stuck-claimed` |
| Severity    | `critical` |
| Notification | PAGE (existing alert channel) |
| Fires when  | `COUNT(*) > 0` jobs with `state = 'leased'` AND `leased_at < now() - interval '30 minutes'` |
| Resolves    | all matching rows clear |

**Query (Postgres):**
```sql
SELECT COUNT(*) AS stuck_count
FROM dispatch_state_current
WHERE state = 'leased'
  AND leased_at < now() - INTERVAL '30 minutes';
```

### Alert 2 — Unlinked in_review (WARN)

| Field       | Value |
|-------------|-------|
| UID         | `dispatch-unlinked-in-review` |
| Severity    | `warning` |
| Notification | existing alert channel |
| Fires when  | `COUNT(*) > 0` jobs with `state = 'in_review'` AND `pr_number IS NULL` AND `updated_at < now() - interval '60 minutes'` |
| Resolves    | all matching rows clear |

**Query (Postgres):**
```sql
SELECT COUNT(*) AS unlinked_count
FROM dispatch_state_current dsc
JOIN dispatch_jobs dj ON dsc.job_id = dj.job_id
WHERE dsc.state = 'in_review'
  AND dj.pr_number IS NULL
  AND dsc.updated_at < now() - INTERVAL '60 minutes';
```

### Alert 3 — Redispatch Loop (WARN)

| Field       | Value |
|-------------|-------|
| UID         | `dispatch-redispatch-loop` |
| Severity    | `warning` |
| Notification | existing alert channel |
| Fires when  | any `correlation_key` has > 3 `enqueued` events in the last 24 hours |
| Resolves    | all hot-loop keys fall to ≤ 3 |

**Query (Postgres):**
```sql
SELECT COUNT(*) AS hot_loop_count
FROM (
  SELECT dj.correlation_key, COUNT(*) AS enqueue_count
  FROM dispatch_v2_events dve
  JOIN dispatch_jobs dj ON dve.job_id = dj.job_id
  WHERE dve.event_type = 'enqueued'
    AND dve.created_at > now() - INTERVAL '24 hours'
    AND dj.correlation_key IS NOT NULL
  GROUP BY dj.correlation_key
  HAVING COUNT(*) > 3
) hot_loops;
```

---

## Deliverables

| File | Purpose |
|------|---------|
| `deploy/grafana-alerts-dispatch-queue-health.yaml` | Grafana alert provisioning YAML — all 3 alert rules |
| `data-remediations/stuck-claimed.md` | Operator runbook for Alert 1 |
| `data-remediations/unlinked-in-review.md` | Operator runbook for Alert 2 |
| `data-remediations/redispatch-loop.md` | Operator runbook for Alert 3 |
| `tests/ops_console/test_queue_health_alerts.py` | Synthetic tests — structure validation + SQL threshold logic |

---

## Test Criteria

1. **TC-01 Alert YAML structure** — `deploy/grafana-alerts-dispatch-queue-health.yaml` loads without error; contains exactly 3 alert rules with UIDs `dispatch-stuck-claimed`, `dispatch-unlinked-in-review`, `dispatch-redispatch-loop`.
2. **TC-02 Stuck-claimed fires** — synthetic DB fixture with 1 row where `state='leased'` and `leased_at = now() - 35 min`; SQL returns `stuck_count >= 1`.
3. **TC-03 Stuck-claimed clear** — fixture with `leased_at = now() - 20 min`; SQL returns `stuck_count = 0`.
4. **TC-04 Unlinked-in-review fires** — fixture with `state='in_review'`, `pr_number IS NULL`, `updated_at = now() - 65 min`; SQL returns `unlinked_count >= 1`.
5. **TC-05 Unlinked-in-review clear (pr_number set)** — same fixture but `pr_number = 42`; SQL returns `unlinked_count = 0`.
6. **TC-06 Unlinked-in-review clear (too recent)** — fixture with `state='in_review'`, `pr_number IS NULL`, `updated_at = now() - 30 min`; SQL returns `unlinked_count = 0`.
7. **TC-07 Redispatch-loop fires** — fixture where `correlation_key='repo:foo|pr:1'` has 4 `enqueued` events in the last 24h; SQL returns `hot_loop_count >= 1`.
8. **TC-08 Redispatch-loop clear (≤ 3)** — same `correlation_key` with only 3 `enqueued` events; SQL returns `hot_loop_count = 0`.
9. **TC-09 Redispatch-loop ignores old events** — 5 `enqueued` events but all older than 24h; SQL returns `hot_loop_count = 0`.
10. **TC-10 Alert 1 severity is critical; Alerts 2 and 3 are warning** — validated from YAML structure.
11. **TC-11 Each alert rule references a runbook URL** — `annotations.runbook_url` present and non-empty in each rule.
12. **TC-12 Each alert rule names the correct contact point** — `notification_settings.receiver` matches the existing alert channel name (e.g. `"dispatch-ops"` or equivalent from env).

---

## Validation

After deploy:
1. Manually insert a row into `dispatch_state_current` with `state='leased'`, `leased_at = now() - 40 minutes`, verify Grafana fires "Dispatch: Stuck Claimed > 30m" PAGE alert within 2 evaluation cycles (2 min).
2. Confirm each alert's "Runbook URL" in the Grafana UI links to the correct `data-remediations/` file.
3. Delete the test row; confirm alert resolves within 1 evaluation cycle.

---

## Acceptance Diff

The PR for this story MUST include changes to these files. Phase 8 will
fail if any are missing from `git diff origin/main --name-only`:

- `deploy/grafana-alerts-dispatch-queue-health.yaml` must-contain `dispatch-stuck-claimed` must-contain `dispatch-unlinked-in-review` must-contain `dispatch-redispatch-loop` — all three alert rules in one provisioning file
- `data-remediations/stuck-claimed.md` must-contain `dispatch_state_current` — runbook for Alert 1
- `data-remediations/unlinked-in-review.md` must-contain `pr_number` — runbook for Alert 2
- `data-remediations/redispatch-loop.md` must-contain `correlation_key` — runbook for Alert 3
- `tests/ops_console/test_queue_health_alerts.py` must-contain `dispatch-stuck-claimed` must-contain `dispatch-unlinked-in-review` must-contain `dispatch-redispatch-loop` — synthetic tests for all 3 alerts

---

## Notes

- **STORY-915 dependency (optional):** If STORY-915 (Loki structured log datasource) has landed before Phase 8 executes, Phase 8 may add a Loki-backed variant of Alert 1 (lease heartbeat gap) as an addendum. This is non-blocking; Postgres queries cover all three alerts independently.
- Alert evaluation interval: `1m` for Alert 1 (critical), `5m` for Alerts 2 and 3 (warning).
- All alerts share folder `dispatch` in the Grafana provisioning hierarchy.
- Contact point name must be read from the existing `deploy/grafana-agent-dashboard.json` or ops config; do not hardcode a new channel name.
