---
name: alert-handler
version: "1.0"
story: STORY-508
triggers:
  - grafana alert
  - alert received
  - webhook alert
  - firing alert
  - alert webhook
description: >
  Handles Grafana alertmanager webhook events.  Each incoming alert is matched
  to a named playbook below.  Every action is logged to the audit log.
  Do NOT auto-remediate beyond what each playbook explicitly authorises.
---

# Alert Handler Skill

Morris receives Grafana alerts via `POST /api/alerts/grafana`.  This skill
defines the playbook for each named alert rule.

## Audit Log

**Every** alert received MUST be appended to the audit log:

```
/home/hermes/state/morris/alert-log.md
```

Format per entry:
```
- received_at: <ISO-8601>  alertname: <name>  severity: <sev>  agent: <agent>  action: <action taken>
```

Write the entry **before** performing any action so the log is complete even
if the action fails.

---

## Playbooks

### ClaimTimeoutsHigh

**Trigger:** Grafana alert `ClaimTimeoutsHigh` fires when the rate of
claim-timeout events on any agent spikes above the configured threshold.

**Action:**
1. Log to `/home/hermes/state/morris/alert-log.md`.
2. Query `GET /api/dispatch/queue` to identify claims older than 30 minutes.
3. Compose a concise Teams DM to Mark:
   - Which agent(s) are timing out
   - Count of stale claims
   - Whether auto-unclaim was performed
4. For each stale claim (>30 min): POST `/api/dispatch/fail/{story_id}` with
   `exit_code: null` to release the claim so another agent can pick it up.
5. Do NOT restart agents autonomously — DM Mark and wait for instructions.

---

### PartialPROpens

**Trigger:** Grafana alert `PartialPROpens` fires when the count of open
"Partial" PRs in any repo spikes above the threshold.

**Action:**
1. Log to `/home/hermes/state/morris/alert-log.md`.
2. **Observe only** — do NOT auto-close, auto-merge, or auto-remediate partial PRs.
   STORY-507 AC-4 manages the partial-PR lifecycle entirely.
3. DM Mark in Teams with the count of open partial PRs and a link to the PRs list.
4. Never take any git or GitHub action on partial PRs.

> **CRITICAL:** This playbook MUST NOT auto-close or auto-merge partial PRs.
> Never. The partial-PR lifecycle is owned by STORY-507 AC-4.
> Morris's role here is: log, DM Mark, and observe.

---

### Phase8P95High

**Trigger:** Grafana alert `Phase8P95High` fires when the P95 execution latency
for Phase 8 tasks exceeds the baseline threshold.

**Action:**
1. Log to `/home/hermes/state/morris/alert-log.md`.
2. Pull structured logs from Loki for Phase 8 stories in the past 2 hours:
   - Query for slow stories (phase_duration > P95 threshold)
   - Identify which agent(s) and story IDs are slowest
3. Analyse bottleneck:
   - Is it a specific agent? A specific story type? A resource constraint?
4. DM Mark in Teams with:
   - Current P95 vs. baseline
   - Top 3 slowest stories with durations
   - Suspected bottleneck and recommendation
5. No auto-remediation — analysis and DM only.

---

### PausedOver24h

**Trigger:** Grafana alert `PausedOver24h` fires when a story has been in
`paused` status for more than 24 hours.

**Action:**
1. Log to `/home/hermes/state/morris/alert-log.md`.
2. Identify the story from the alert labels.
3. Force unclaim via API: `POST /api/dispatch/fail/{story_id}` to release it
   back into the queue as `failed`, then re-enqueue if appropriate.
   Alternatively, if the story is still valid: use `POST /api/dispatch/reclaim`
   to reset the claim and allow an agent to resume.
4. Bump priority to 90: `POST /api/dispatch/priority` with `{"story_id": ..., "priority": 90}`.
5. DM Mark: "Story {story_id} has been paused for >24h — reset and priority bumped to 90."

---

### RateLimitDeferralSpike

**Trigger:** Grafana alert `RateLimitDeferralSpike` fires when the rate of
rate-limit deferrals in the dispatch poller spikes above the baseline.

**Action:**
1. Log to `/home/hermes/state/morris/alert-log.md`.
2. Query the queue for the current distribution of claimed stories by agent.
3. DM Mark in Teams with:
   - Agent breakdown (which agents are hitting limits most)
   - Rate of deferrals per agent
   - Recommendation: consider pausing low-priority stories (priority=0) to
     relieve pressure on the Anthropic API
4. If rate is sustained (>10 deferrals/min for >15 min): consider pausing
   `priority=0` stories via `POST /api/dispatch/pause/{story_id}` for each
   pending low-priority item.  DM Mark before taking this action.

---

## Fallback Guard

If **both** alert sources go silent, Morris must detect the gap and self-heal.

**Conditions triggering fallback:**
- No Grafana webhook has arrived in the past **60 minutes** (60-min webhook silence threshold)
- AND no fleet-check cron has fired in the past **90 minutes** (90-min cron silence threshold)

**Fallback procedure:**
1. DM Mark in Teams: _"Alert system silent for >60 min — switching to manual
   fleet polling mode.  Webhook: {last_received}.  Cron: {last_fired}."_
2. Enter polling mode: manually query `GET /api/dispatch/queue` and per-agent
   SSH health probes (same checks as fleet-vigilance) once every 30 minutes
   until either the webhook or cron resumes.
3. Log each polling cycle to `/home/hermes/state/morris/alert-log.md` with
   `action: fallback-poll`.
4. Exit polling mode once a Grafana webhook or fleet-check cron fires again.
   DM Mark: _"Alert system resumed — exiting fallback polling mode."_

---

## General Rules

- Every playbook MUST write a log entry to `/home/hermes/state/morris/alert-log.md`
  before taking any action.
- DM Mark for all CRIT-severity and any alert where auto-remediation was performed.
- Never commit, push, or modify source code from an alert handler.
- Never run `git add`, `git commit`, or `git push`.
