# STORY-508: Morris Adaptation for STORY-507 Observability — Feature Spec

> Phase 6 | Scope: **Medium** | Date: 2026-04-21

---

## 1. Overview

Morris must stop its workaround behaviors (partial-PR recovery, manual retries, direct DB writes) and adopt the new event-driven observability that STORY-507 provides (Grafana alerts, structured logs, paused status, automatic partial-PR creation). This spec covers: band-aid removal, new alert-handler skill, priority API, cron cadence shift, baselines, weekly report, and dashboard panels.

---

## 2. API Changes

### 2.1 `POST /api/alerts/grafana` — Grafana Webhook Receiver (AC-6)

**Purpose**: Receives Grafana alert payloads, validates signature, formats as Teams adaptive card, sends to Morris's Teams channel, writes to alert-log.md.

**Request**:
```json
{
  "receiver": "ops-console",
  "status": "firing",
  "alerts": [
    {
      "status": "firing",
      "labels": {
        "alertname": "ClaimTimeoutsHigh",
        "agent": "dan",
        "repo": "tech-dev-agents",
        "story_id": "STORY-507"
      },
      "annotations": {
        "summary": "Claim timeouts > 3/min for 5m",
        "description": "..."
      },
      "startsAt": "2026-04-21T10:00:00Z",
      "endsAt": "0001-01-01T00:00:00Z",
      "generatorURL": "http://grafana:3000/..."
    }
  ],
  "commonLabels": { "severity": "warning" }
}
```

**Headers**: `X-Grafana-Signature: <HMAC-SHA256 of body using shared secret>`

**Response**: `200 OK` with `{ "accepted": <count>, "errors": [] }`

**Validation**:
1. Compute HMAC-SHA256 of raw request body using `GRAFANA_WEBHOOK_SECRET` env var
2. Compare with `X-Grafana-Signature` header using `hmac.compare_digest`
3. Reject with 401 if mismatch

**Side effects**:
1. For each alert in `alerts[]`:
   - Format as Teams adaptive card: alert name, severity, metric value, labels (agent, repo, story_id), since-timestamp
   - POST to Morris's Teams channel via Graph API (reuse existing `teams_service.send_channel_message()`)
   - Append to `/home/hermes/state/morris/alert-log.md`:
     ```
     ## 2026-04-21T10:00:00Z — ClaimTimeoutsHigh (firing)
     - Severity: warning
     - Agent: dan
     - Labels: {agent: dan, repo: tech-dev-agents, story_id: STORY-507}
     - Forwarded to Teams: yes
     ```

### 2.2 `GET /api/alerts/active` — Active Firing Alerts (AC-13)

**Purpose**: Returns currently firing Grafana alerts for the dashboard alert-status panel.

**Response**:
```json
{
  "alerts": [
    {
      "name": "ClaimTimeoutsHigh",
      "severity": "warning",
      "started_at": "2026-04-21T10:00:00Z",
      "labels": { "agent": "dan", "repo": "tech-dev-agents" },
      "annotations": { "summary": "Claim timeouts > 3/min for 5m" }
    }
  ],
  "count": 1,
  "fetched_at": "2026-04-21T10:05:00Z"
}
```

**Implementation**: Proxy to Grafana's `GET /api/v1/alerts` (or the Alertmanager API if separate). Cache for 30 seconds via TTL cache. If Grafana is unreachable, return empty list with `"error": "grafana_unreachable"` field.

**Config**: `GRAFANA_URL` and `GRAFANA_API_KEY` env vars.

### 2.3 `POST /api/dispatch/priority` — Set Story Priority (AC-14)

**Purpose**: Replace raw `UPDATE enqueued_at` DB hacks with a proper priority API.

**Request**:
```json
{
  "story_id": "STORY-510",
  "priority": 10
}
```

**Validation**:
- `story_id` must exist and be in `pending` or `paused` status (not claimed/completed/failed/cancelled)
- `priority` must be integer, range 0-100 (0 = default, higher = sooner)
- 404 if story not found in active statuses
- 422 if status is terminal or claimed (cannot reprioritize in-progress work)

**Response**:
```json
{
  "story_id": "STORY-510",
  "priority": 10,
  "previous_priority": 0,
  "status": "pending"
}
```

---

## 3. Database Changes

### 3.1 Migration: `006_dispatch_priority.sql` (AC-14)

```sql
BEGIN;

-- Add priority column (default 0 = normal priority, higher = sooner)
ALTER TABLE dispatch_items
    ADD COLUMN IF NOT EXISTS priority INTEGER DEFAULT 0;

-- Backfill: all existing rows get priority 0 (no-op since DEFAULT handles it,
-- but explicit for clarity)
-- UPDATE dispatch_items SET priority = 0 WHERE priority IS NULL;

COMMIT;
```

**Note**: PostgreSQL 11+ stores the DEFAULT in `pg_attribute` catalog — no table rewrite needed. This is a metadata-only change, safe under live traffic.

### 3.2 Query Change: `next_pending()` ORDER BY

**Before** (current):
```sql
SELECT * FROM dispatch_items
WHERE status IN ('pending', 'paused')
ORDER BY enqueued_at
LIMIT 1
```

**After**:
```sql
SELECT * FROM dispatch_items
WHERE status IN ('pending', 'paused')
ORDER BY priority DESC, enqueued_at ASC
LIMIT 1
```

Higher-priority stories are claimed first. Within same priority, FIFO by enqueue time.

### 3.3 Queue listing ORDER BY update

The `queue()` method also needs priority-aware ordering:

**Before**:
```sql
SELECT * FROM dispatch_items
WHERE status IN ('pending', 'claimed', 'in_review', 'paused')
ORDER BY enqueued_at
```

**After**:
```sql
SELECT * FROM dispatch_items
WHERE status IN ('pending', 'claimed', 'in_review', 'paused')
ORDER BY priority DESC, enqueued_at ASC
```

---

## 4. File-by-File Change Plan

### 4.1 `deployment/vm/skills/fleet-vigilance/SKILL.md` (AC-1, AC-3)

**Remove**:
- **Check 0d** ("Unpushed work + missing PRs"): The entire section that auto-commits stranded work, auto-pushes branches, and auto-creates PRs. STORY-507 AC-4 now creates partial PRs automatically on SIGTERM. Morris doing this manually creates duplicates.
  - Remove lines covering: `git add -A && git commit`, `git push origin HEAD`, `gh pr create --title 'STORY-XXX: <title>'`
  - Remove the CRIT/WARN severity classifications for uncommitted/unpushed work
- **Remediation A references to partial PRs**: Update the stuck-claim remediation to NOT create PRs — just clear the local queue and fail the stale claim. STORY-507's SIGTERM handler + partial-PR automation covers the rest.
- **Any `[RETRY 1/3]` loop references**: Remove manual retry dispatch logic. Add note: "Paused stories are automatically re-claimed via FIFO (STORY-507 AC-6). Do not manually re-dispatch."

**Add**:
- Top-of-file note: "Post-STORY-507: partial-PR creation, retry, and status transitions are handled by the phase runner and dispatch API. This skill focuses on monitoring, not remediation of those flows."
- In Check 4b (SDLC deliverable audit): Add note that Partial PRs (title contains "Partial") are NOT reviewed by Morris — they're managed by STORY-507 AC-4.

### 4.2 `deployment/vm/skills/pr-review/SKILL.md` (AC-2)

**Add** (at top of Step 1):
```markdown
### Exclusion: Partial PRs
Do NOT review, approve, request-changes, comment on, close, or create PRs
whose title contains "Partial". STORY-507 AC-4 manages partial-PR lifecycle
automatically. Morris touching these creates conflicts.
```

### 4.3 `deployment/vm/skills/alert-handler/SKILL.md` (AC-5, AC-7) — NEW

**Structure**:
```markdown
---
name: alert-handler
description: >
  Event-driven alert response. Receives Grafana alert names from Teams
  channel messages (forwarded by POST /api/alerts/grafana webhook).
  Matches alert name → playbook → executes remediation or escalation.
triggers:
  - alert fired
  - grafana alert
  - alert handler
---

# Alert Handler — Morris's Event-Driven Response

## How alerts arrive
Grafana fires → POST /api/alerts/grafana on ops-console → formatted as
Teams adaptive card → posted to Morris's fleet-health channel → Morris's
Teams bot reads it → this skill activates.

## Playbooks

### ClaimTimeoutsHigh (>3/min for 5m)
1. Health-check ops-console: `curl -s https://tech-dev-agents.gorillacommerce.ai/api/health`
2. Health-check agent-gateway: `curl -s https://tech-dev-agents.gorillacommerce.ai/api/gateway/health`
3. If both healthy → escalate to Mark: "Claim timeouts are high but infrastructure looks OK. Possible prompt issue or rate-limit cascade."
4. If either unhealthy → restart the unhealthy service, re-check in 2 min, DM Mark if still down.

### PartialPROpens (>0)
1. Do NOT auto-remediate — this means STORY-507 AC-4 fired and created a partial PR.
2. File a regression bug if partial PRs should have been zero post-507.
3. DM Mark: "Partial PR opened — AC-4 of 507 may have a regression. PR: [link]"

### Phase8P95High (>2x baseline)
1. Pull structured logs: `ssh $AGENT_IP "sudo journalctl -u dispatch-poller --since '2h ago' --output json"`
2. Parse Phase 8 session durations from structured log events (STORY-507 AC-10 schema)
3. Compare P95 against `metrics-baselines.md`
4. Post diff summary to Mark: "Phase 8 P95 is Xmin vs Ymin baseline. Top contributors: [stories]"

### PausedOver24h (paused >24h without re-claim)
1. Query: `curl -s -H "X-API-Key: $KEY" https://tech-dev-agents.gorillacommerce.ai/api/dispatch/queue?include_claimed=true`
2. Filter items where status=paused AND paused_at > 24h ago
3. For each: check agent's budget state via SSH
4. If budget exhausted → wait for reset, DM Mark with ETA
5. If budget OK → force unclaim: `POST /api/dispatch/reclaim/{story_id}` with priority bump via `POST /api/dispatch/priority`

### RateLimitDeferralSpike (all agents defer simultaneously)
1. Check all agents' rate-limit status: `cat /var/run/dispatch-poller-paused-until` on each VM
2. Collect reset ETAs
3. DM Mark: "All agents rate-limited. ETAs: Dan [time], Derrick [time], Daisy [time], Devon [time]"

## Audit logging (AC-7)
After every playbook execution, append to `/home/hermes/state/morris/alert-log.md`:
- Timestamp
- Alert name
- Playbook executed
- Action taken
- Outcome (resolved / escalated / failed)

## What NOT to do
- Don't create partial PRs (STORY-507 AC-4 owns that)
- Don't modify dispatch_items.status directly (use API)
- Don't retry stories manually (STORY-507 AC-6 auto-reclaims paused)
```

### 4.4 `deployment/vm/skills/weekly-fleet-report/SKILL.md` (AC-9) — NEW

**Structure**:
```markdown
---
name: weekly-fleet-report
description: >
  Friday summary — KPIs from the past week: partial-PR opens, completion
  rate, dispatch-to-merge P50, alert counts, flakey stories.
triggers:
  - weekly report
  - friday report
  - fleet report
---

# Weekly Fleet Report

## Schedule
Cron: `0 10 * * 5` (Friday 10:00 UTC / 5:00 AM ET)

## Data collection
1. Partial-PR opens this week: `gh pr list --search "Partial in:title created:>YYYY-MM-DD"`
2. Within-cycle completion rate: query dispatch history, count completed / (completed + failed + cancelled)
3. Medium-scope dispatch→merge P50: query dispatch history for medium scope, compute claim_at→completed_at durations
4. Alert counts: parse `/home/hermes/state/morris/alert-log.md` for entries this week, count new vs resolved
5. Top 3 flakey stories: query dispatch history, group by story_id, count dispatch cycles, sort desc

## Output
- DM Mark via Teams (executive summary, <=20 lines)
- Archive to `/home/hermes/state/morris/weekly-reports/YYYY-WW.md`

## KPI targets (from STORY-507 success measures)
- Partial-PR opens: 0
- Within-cycle completion rate: >=95%
- Medium dispatch→merge P50: <4h
```

### 4.5 `tech_dev_agents/ops_console/routes/alerts.py` (AC-6, AC-13)

**Add imports**:
```python
import hashlib
import hmac
import os
from datetime import datetime, timezone
from pathlib import Path
```

**Add `POST /api/alerts/grafana`**:
```python
@router.post("/alerts/grafana")
async def receive_grafana_alert(request: Request) -> dict:
    """Receive Grafana webhook alert, forward to Teams, log to audit file."""
    # 1. Validate HMAC signature
    secret = os.environ.get("GRAFANA_WEBHOOK_SECRET", "")
    body = await request.body()
    expected_sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    actual_sig = request.headers.get("X-Grafana-Signature", "")
    if not hmac.compare_digest(expected_sig, actual_sig):
        raise HTTPException(status_code=401, detail="Invalid signature")

    payload = await request.json()
    alerts = payload.get("alerts", [])
    accepted = 0
    errors = []

    for alert in alerts:
        try:
            # 2. Format and send to Teams
            # 3. Append to alert-log.md
            accepted += 1
        except Exception as exc:
            errors.append(str(exc))

    return {"accepted": accepted, "errors": errors}
```

**Add `GET /api/alerts/active`**:
```python
@router.get("/alerts/active")
async def get_active_alerts(request: Request) -> dict:
    """Proxy active firing alerts from Grafana."""
    # Proxy to Grafana API with TTL cache (30s)
    # Return: { alerts: [...], count: N, fetched_at: ISO }
```

### 4.6 `tech_dev_agents/ops_console/routes/dispatch.py` (AC-14)

**Add at end of file**:
```python
@router.post("/dispatch/priority")
async def set_priority(request: Request, body: PriorityRequest) -> PriorityResponse:
    """Set dispatch priority for a pending/paused story."""
    db = request.app.state.dispatch_db
    try:
        result = await db.set_priority(body.story_id, body.priority)
    except NotFoundError:
        raise HTTPException(404, f"Story {body.story_id} not found in active queue")
    except InvalidTransitionError as exc:
        raise HTTPException(422, str(exc))
    return PriorityResponse(**result)
```

### 4.7 `tech_dev_agents/ops_console/services/dispatch_db_service.py` (AC-14)

**Add method `set_priority()`**:
```python
async def set_priority(self, story_id: str, priority: int) -> dict[str, Any]:
    """Set priority on a pending or paused dispatch item."""
    async with self.pool.acquire() as conn:
        row = await conn.fetchrow(
            """UPDATE dispatch_items
               SET priority = $2, updated_at = now()
               WHERE story_id = $1 AND status IN ('pending', 'paused')
               RETURNING *""",
            story_id,
            priority,
        )
    if row is None:
        # Check if exists but wrong status
        exists = await self._find_active(story_id)
        if exists:
            raise InvalidTransitionError(
                f"Cannot set priority on {story_id} with status '{exists['status']}'"
            )
        raise NotFoundError(story_id)
    return self._row_to_dict(row)
```

**Modify `next_pending()` query**:
```sql
ORDER BY priority DESC, enqueued_at ASC
```

**Modify `queue()` query**:
```sql
ORDER BY priority DESC, enqueued_at ASC
```

### 4.8 `tech_dev_agents/ops_console/models/responses.py` (AC-14)

**Add models**:
```python
class PriorityRequest(BaseModel):
    story_id: str = Field(..., min_length=1, max_length=100)
    priority: int = Field(..., ge=0, le=100)

class PriorityResponse(BaseModel):
    story_id: str
    priority: int
    previous_priority: int
    status: str
```

**Add to `DispatchItem`**:
```python
priority: int = 0
```

### 4.9 `scripts/migrations/006_dispatch_priority.sql` (AC-14) — NEW

```sql
-- STORY-508: Add priority column to dispatch_items.
-- Higher priority = claimed sooner. Default 0 = normal FIFO.
-- Replaces the manual `UPDATE enqueued_at = '2020-01-01'` hack.
-- Safe under live traffic: PG 11+ stores DEFAULT in catalog, no rewrite.

BEGIN;

ALTER TABLE dispatch_items
    ADD COLUMN IF NOT EXISTS priority INTEGER DEFAULT 0;

COMMIT;
```

### 4.10 `deployment/vm/morris-fleet-check.sh` (AC-10)

**Add after existing header comment block (line ~19)**:
```bash
# STORY-508: Cadence reduced from */30 to 0 */2 (every 2 hours).
# Reactive alerting now handled by alert-handler skill via Grafana webhooks.
# This cron remains as a safety-net sweep — not the primary detection mechanism.
# Weekly sweep: 0 9 * * 1 (Mondays 9 AM UTC) for repo-drift detection — unchanged.
```

No logic changes to the script itself.

### 4.11 `deployment/vm/scripts/update-baselines.sh` (AC-8) — NEW

```bash
#!/usr/bin/env bash
# update-baselines.sh — Weekly cron to refresh metrics baselines.
# Cron: 0 12 * * 0 (Sunday noon UTC)
# Reads Prometheus metrics for the past 7 days, computes P50/P95, writes to
# /home/hermes/state/morris/metrics-baselines.md

BASELINES_FILE="/home/hermes/state/morris/metrics-baselines.md"
PROMETHEUS_URL="${PROMETHEUS_URL:-http://localhost:9090}"

# Query each STORY-507 metric for 7-day P50/P95
# Metric names TBD — must match AC-8 of STORY-507 exactly
# Template:
# curl -s "$PROMETHEUS_URL/api/v1/query?query=histogram_quantile(0.50, rate(dispatch_phase8_duration_seconds_bucket[7d]))"
# curl -s "$PROMETHEUS_URL/api/v1/query?query=histogram_quantile(0.95, rate(dispatch_phase8_duration_seconds_bucket[7d]))"

echo "# Metrics Baselines — Updated $(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$BASELINES_FILE"
echo "" >> "$BASELINES_FILE"
echo "## Phase 8 Duration" >> "$BASELINES_FILE"
echo "- P50: TBD" >> "$BASELINES_FILE"
echo "- P95: TBD" >> "$BASELINES_FILE"
echo "" >> "$BASELINES_FILE"
echo "## Claim Timeouts" >> "$BASELINES_FILE"
echo "- P50: TBD" >> "$BASELINES_FILE"
echo "- P95: TBD" >> "$BASELINES_FILE"
```

### 4.12 `frontend/src/components/DispatchQueue.tsx` (AC-12)

**Modify `statusBadge()` function**: Add explicit `paused` case:
```typescript
case 'paused':
  return { className: 'bg-purple-600/20 text-purple-400', label: 'paused' };
```

**Add paused filter/tab**: Add a "Paused" section or tab to the queue view that filters `status === 'paused'` items, showing: story_id, repo, scope, agent, paused_at, current_phase, time-since-paused.

### 4.13 `frontend/src/components/AlertStatusPanel.tsx` (AC-12) — NEW

**Purpose**: Display currently firing alerts from `GET /api/alerts/active`.

**Structure**:
```tsx
export default function AlertStatusPanel() {
  // Fetch from /api/alerts/active every 30s
  // Render: alert name, severity badge, started_at (time ago), labels
  // Empty state: "No active alerts" with green checkmark
}
```

### 4.14 `frontend/src/components/BudgetGauge.tsx` (AC-12) — NEW

**Purpose**: Per-agent Claude Code budget gauge. Reads from existing agent health data (STORY-507 AC-7 exposes budget field; STORY-496 already shows quota bar in AgentCard).

**Structure**: Circular or linear gauge showing percent_used with color coding (green <80%, yellow 80-90%, red >90%). Reuses data from `AgentCard.tsx`'s existing quota bar — this is a dashboard-level summary version.

### 4.15 `frontend/src/components/DashboardLayout.tsx` (AC-12)

**Modify**: Wire `AlertStatusPanel` and `BudgetGauge` into the dashboard grid. Place alert-status panel in the top-right (high visibility). Place budget gauges in the agent section.

### 4.16 `tests/test_audit_skills.py` (AC-4, AC-15) — NEW

```python
"""Audit test — scan skills and scripts for banned patterns.

AC-4: No direct DB writes to dispatch_items.status
AC-15: Regression guard for band-aid behaviors
"""

import subprocess
import pytest

SKILLS_DIR = "deployment/vm/skills"
SCRIPTS_DIR = "deployment/vm"

BANNED_PATTERNS = [
    ("UPDATE dispatch_items SET status", "Direct DB status writes banned — use API"),
    ("enqueued_at = '20", "Priority hack banned — use POST /api/dispatch/priority"),
    ("gh pr create.*Partial", "Partial PR creation banned — STORY-507 AC-4 owns this"),
]


@pytest.mark.parametrize("pattern,reason", BANNED_PATTERNS)
def test_no_banned_patterns_in_skills(pattern, reason):
    result = subprocess.run(
        ["grep", "-rn", pattern, SKILLS_DIR, SCRIPTS_DIR],
        capture_output=True, text=True,
    )
    assert result.stdout == "", f"Banned pattern found: {reason}\n{result.stdout}"
```

### 4.17 Fallback guard (AC-11)

**Implementation location**: `deployment/vm/skills/alert-handler/SKILL.md` — add a "Fallback Guard" section.

**Logic**:
- Check timestamp of last entry in `/home/hermes/state/morris/alert-log.md`
- Check timestamp of last fleet-health snapshot in `/home/hermes/state/morris/fleet-health.md`
- If alert-log last entry > 60min ago AND fleet-health last entry > 90min ago:
  - DM Mark: "Observability may be degraded — no Grafana webhooks in 60min, no cron in 90min. Falling back to 30-min polling."
  - Create a temporary cron override (or set a flag file `/var/run/morris-fallback-polling`)
- Auto-resume event-driven mode when either a Grafana webhook fires or a cron run succeeds

**Trigger**: Checked at the top of every alert-handler invocation AND at the top of every fleet-vigilance cron run.

---

## 5. Rollback Strategy

### Level 1: Feature-flag rollback (instant, no deploy)
- `GRAFANA_WEBHOOK_ENABLED=false` — disables `POST /api/alerts/grafana` (returns 503)
- `DISPATCH_PRIORITY_ENABLED=false` — disables `POST /api/dispatch/priority` (returns 503)
- Cron cadence: revert `/etc/cron.d/morris-fleet-check` from `0 */2` back to `*/30`

### Level 2: Skill rollback (deploy required)
- Revert `fleet-vigilance/SKILL.md` to pre-508 version (restores band-aid behaviors)
- Remove `alert-handler/SKILL.md` (Morris falls back to polling-only mode)
- Remove `weekly-fleet-report/SKILL.md` (Morris falls back to existing `weekly-fleet-review`)

### Level 3: Database rollback
- `priority` column: `ALTER TABLE dispatch_items DROP COLUMN IF EXISTS priority;`
- Revert `next_pending()` query to `ORDER BY enqueued_at ASC`
- No data loss — priority column only adds metadata; dropping it returns to pure FIFO

### Level 4: Full revert
- `git revert` the STORY-508 merge commit
- Redeploy ops-console
- Redeploy Morris VM skills via `push-code.sh morris`
- Restore cron cadence

**Critical**: Rollback does NOT require STORY-507 to be reverted. The two are decoupled — 507's infrastructure keeps working; Morris just falls back to polling mode instead of consuming alerts.

---

## 6. Testing Strategy Summary

| AC | Test Type | Description |
|----|-----------|-------------|
| 1-3 | Audit (grep) | Verify removed patterns don't exist in skill files |
| 4 | Audit (grep) | `UPDATE dispatch_items SET status` absent from all skills/scripts |
| 5 | Unit | Alert-handler playbook structure validation (each alert name has a playbook) |
| 6 | Integration | `POST /api/alerts/grafana` — valid signature accepted, invalid rejected, Teams message sent, alert-log appended |
| 7 | Integration | Alert-log.md written with correct format after webhook |
| 8 | Unit | `update-baselines.sh` produces valid baselines file |
| 9 | Unit | Weekly report skill produces valid markdown with all KPIs |
| 10 | Config | Cron file has `0 */2 * * *` cadence |
| 11 | Integration | Fallback guard activates when no webhook in 60min + no cron in 90min |
| 12 | Frontend | Dashboard renders paused column, alert-status panel, budget gauge |
| 13 | Integration | `GET /api/alerts/active` returns valid JSON matching schema |
| 14 | Integration | `POST /api/dispatch/priority` updates priority, `next_pending` respects priority ordering |
| 15 | Audit (grep) | All banned patterns absent from skills/scripts |

---

## 7. Implementation Order

1. **Migration + priority API** (AC-14) — foundation, no dependencies
2. **Alert routes** (AC-6, AC-13) — webhook receiver + active-alerts proxy
3. **Band-aid removal** (AC-1, AC-2, AC-3, AC-4) — skill file edits
4. **Alert-handler skill** (AC-5, AC-7) — new skill file
5. **Cron + fallback** (AC-10, AC-11) — cadence change + guard
6. **Baselines + weekly report** (AC-8, AC-9) — scripts + skill
7. **Dashboard panels** (AC-12) — frontend components
8. **Audit tests** (AC-15) — CI regression guard (run last to validate everything)
