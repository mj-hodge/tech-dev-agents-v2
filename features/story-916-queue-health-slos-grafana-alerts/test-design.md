# Test Design — STORY-916: Queue Health SLOs + Grafana Alerts

## Phase 7 Summary

**Scope:** Medium  
**Phase path:** 1 → 7 → 8 → Done  
**Test file:** `tests/observability/test_queue_health_alerts.py`  
**State at phase end:** RED (alert YAML group + runbook files not yet written)

---

## Deliverables Being Tested

| Deliverable | Path | Purpose |
|---|---|---|
| Alert YAML group | `deployment/observability/grafana-alerts.yml` (additive group) | Three Grafana alert rules (Postgres datasource) |
| Runbook 1 | `data-remediations/queue-stuck-claimed-runbook.md` | Remediation guide for stuck claimed rows |
| Runbook 2 | `data-remediations/queue-unlinked-in-review-runbook.md` | Remediation guide for unlinked in_review rows |
| Runbook 3 | `data-remediations/queue-redispatch-loop-runbook.md` | Remediation guide for redispatch loop |

---

## Test Groups

### Group A — Alert YAML Structure (3 tests)
Verify the `queue_health_slos` group is present in `grafana-alerts.yml` with correct alert
UIDs, severity labels, and evaluation intervals. Tests load the YAML and assert schema
constraints without touching Grafana.

| ID | Name | Type | Expected State |
|----|------|------|----------------|
| A1 | `test_alert_yaml_has_queue_health_group` | unit / YAML parse | RED — group absent |
| A2 | `test_alert_severities_and_intervals` | unit / YAML assertion | RED — group absent |
| A3 | `test_alert_payloads_no_sensitive_data` | unit / content scan | RED — group absent |

### Group B — SQL Query Correctness (6 tests)
Each alert rule embeds a Postgres SQL expression. These tests extract the SQL from the
YAML and execute it against an in-memory SQLite database seeded with synthetic rows,
verifying the predicate fires / clears as specified in the acceptance criteria.

SQLite schema mirrors the three Postgres tables:
- `dispatch_state_current(job_id TEXT, state TEXT, updated_at TIMESTAMP)`
- `dispatch_jobs(job_id TEXT, pr_number INTEGER, correlation_key TEXT)`
- `dispatch_v2_events(id INTEGER, correlation_key TEXT, event_type TEXT, created_at TIMESTAMP)`

| ID | Name | Seed | Expected |
|----|------|------|----------|
| B1 | `test_alert1_positive_stuck_claimed_31min` | claimed row, updated_at = now−31m | query returns ≥ 1 row |
| B2 | `test_alert1_negative_claimed_fresh` | claimed row, updated_at = now−1m | query returns 0 rows |
| B3 | `test_alert2_positive_unlinked_in_review_61min` | in_review row, pr_number NULL, updated_at = now−61m | query returns ≥ 1 row |
| B4 | `test_alert2_cleared_after_pr_backfill` | in_review row, pr_number backfilled | query returns 0 rows |
| B5 | `test_alert3_positive_redispatch_4_events` | 4 redispatched events, same correlation_key, last 24h | query returns ≥ 1 row |
| B6 | `test_alert3_boundary_3_events_no_fire` | 3 redispatched events, same key, last 24h | query returns 0 rows |

### Group C — Notification Payload (1 test)
Verifies alert annotations in the YAML contain all required fields: alert name, count
expression, Grafana panel link, and runbook link.

| ID | Name | Expected State |
|----|------|----------------|
| C1 | `test_alert_annotations_contain_required_fields` | RED — group absent |

### Group D — Runbook Content (3 tests)
Parse each runbook markdown file and assert structural requirements: a `## Quick triage`
section exists and contains at least one fenced SQL block.

| ID | Name | Expected State |
|----|------|----------------|
| D1 | `test_stuck_claimed_runbook_has_quick_triage_sql` | RED — file absent |
| D2 | `test_unlinked_in_review_runbook_has_quick_triage_sql` | RED — file absent |
| D3 | `test_redispatch_loop_runbook_has_quick_triage_sql` | RED — file absent |

### Group E — Alert Additive-Only Guard (1 test)
Confirms the existing `dispatch_sdlc_alerts` group is preserved verbatim after Phase 8
changes — no existing rule UIDs are removed or modified.

| ID | Name | Expected State |
|----|------|----------------|
| E1 | `test_existing_alert_rules_unchanged` | RED — queue_health group absent causes parse divergence |

---

## Total: 14 tests (all RED at phase end)

---

## Fixture Strategy

```
conftest.py (tests/observability/)
  ├─ alert_yaml()          → parsed dict from grafana-alerts.yml
  ├─ queue_health_group()  → the 'queue_health_slos' group dict (KeyError → RED)
  └─ db()                  → SQLite in-memory conn with dispatch schema seeded empty
```

SQL extraction strategy for Group B: each alert rule stores a `rawSql` field (Postgres
datasource model). Tests parse `alert_yaml → groups[queue_health_slos] → rules[N] →
data[0] → model → rawSql` and adapt it for SQLite (strip `interval '...'` syntax, replace
with equivalent datetime arithmetic).

---

## Acceptance Criteria Coverage

| AC | Test IDs |
|----|----------|
| Three alert rules deployed | A1, A2 |
| Routes to existing channel | A2 (labels.notification_channel) |
| Runbooks with Quick triage + SQL | D1, D2, D3 |
| Synthetic: Alert 1 fires / clears | B1, B2 |
| Synthetic: Alert 2 fires / clears | B3, B4 |
| Synthetic: Alert 3 fires / clears | B5, B6 |
| Payload: job_id + story_id only (no secrets) | A3 |
| Payload: name, count, panel link, runbook link | C1 |
| Existing rules untouched | E1 |

---

## Constraints Reflected in Tests

- **Postgres datasource** — SQL extracted from `model.rawSql` (Postgres panel model), not LogQL
- **No Prometheus/AlertManager** — A2 asserts `datasourceUid` is NOT `prometheus`
- **No full row contents in payload** — A3 scans annotation templates for prohibited fields
  (`state`, `updated_at`, `prompt`, `agent_identity`)
- **Additive only** — E1 diffs existing UIDs before/after simulated merge
