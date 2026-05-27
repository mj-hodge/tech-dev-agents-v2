---
name: weekly-fleet-report
version: "1.0"
story: STORY-508
triggers:
  - weekly report
  - fleet report
  - friday report
  - weekly fleet report
schedule: "0 10 * * 5"
description: >
  Generates the weekly fleet health report every Friday at 10:00 UTC.
  Compiles KPIs from Prometheus, Loki, and the dispatch queue, then
  delivers the report as a Teams DM to Mark and archives it locally.
---

# Weekly Fleet Report Skill

**Schedule:** Every Friday at 10:00 UTC (`0 10 * * 5`)

Morris generates this report automatically.  It covers the past 7 days
(Friday 00:00 UTC to Thursday 23:59 UTC).

---

## KPIs to Collect and Report

### 1. Partial PR Count

- Query GitHub across all repos for PRs with "Partial" in the title opened
  during the reporting week.
- Report: total partial PRs opened, broken down by repo and agent.

### 2. Completion Rate

- Query `dispatch_items` for stories dispatched during the week.
- **completion rate** = completed / total dispatched (×100%)
- Include counts: dispatched, completed, failed, cancelled, still-in-progress.

### 3. Dispatch-to-Merge P50 (median latency)

- For each story completed during the week, compute:
  `merge_time - enqueued_at` (using PR merge timestamp from GitHub if available,
  or `completed_at` as proxy).
- Report the **P50** (median) dispatch-to-merge duration in hours.
- Also report P95 for reference.

### 4. Alert Counts by Severity

- Query Prometheus / Grafana for the total number of alerts fired during the
  reporting week, grouped by severity (critical, warning, info).
- Include total count and a breakdown by alert name (top 5 by frequency).

### 5. Top 3 Flakey Stories

- Identify stories that failed more than once during the week (or were
  paused > 24h).
- Report the top 3 by failure/pause count, with story IDs and failure reasons
  if available from Loki logs.

---

## Report Format

Deliver as a Markdown-formatted Teams direct message to Mark.

```
📊 Weekly Fleet Report — {YYYY-MM-DD} to {YYYY-MM-DD}

**Partial PRs opened:** {N} (by repo: {repo: N, ...})

**Completion rate:** {N}% ({completed}/{dispatched} stories)
  - Failed: {N}  |  Cancelled: {N}  |  In-progress: {N}

**Dispatch → Merge latency:**
  - P50: {Xh Ym}
  - P95: {Xh Ym}

**Alerts fired this week:** {total}
  - Critical: {N}  |  Warning: {N}  |  Info: {N}
  - Top alerts: {AlertName: N, ...}

**Flakey stories (top 3):**
  - {STORY-NNN}: {N} failures — {reason}
  - ...

Report archived to /home/hermes/state/morris/weekly-reports/{YYYY-MM-DD}.md
```

---

## Delivery and Archive

1. **Teams DM:** Send the report to Mark via Teams (Microsoft Graph API).
2. **Archive:** Save the full report to:
   ```
   /home/hermes/state/morris/weekly-reports/{YYYY-MM-DD}.md
   ```
   where the date is the Friday of the reporting week.

---

## Data Sources

| KPI | Source |
|-----|--------|
| Partial PR count | GitHub API (`gh pr list --search "Partial in:title"`) |
| Completion rate | `dispatch_items` table via `GET /api/dispatch/history` |
| P50 latency | `dispatch_items.completed_at - enqueued_at` (proxy) |
| Alert counts | Prometheus / Grafana API or `GET /api/alerts?since=7d` |
| Flakey stories | `dispatch_items` (status=failed, count by story_id) |

---

## Error Handling

- If any data source is unavailable, include `⚠️ {source} unavailable` in the
  relevant section and continue with what is available.
- Always deliver the report even if some KPIs are missing.
- Log errors to `/home/hermes/state/morris/alert-log.md`.
