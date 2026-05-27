# Seed

## Overview
| Field | Value |
|-------|-------|
| Mode | feature_update |
| Scope | medium |
| Feature Name | Agent Cost & Usage Dashboard |
| Story ID | TBD |

## Problem Statement

No visibility into per-agent costs or work output. The operator (Mark) cannot see:
- How much each agent costs per day/week on Azure Foundry (Opus per-token)
- How much Claude Code usage each agent consumes (team sub, reported via SDK)
- How many stories/phases each agent completes
- Whether an agent is stuck, idle, or productive

Currently requires manual SSH and log grep to see any of this.

## Target User / Use Case

- **Mark (operator)** — needs a single dashboard to monitor all agents' cost, usage, and productivity
- **Future team leads** — will need per-agent cost attribution for budgeting

## Success Criteria

- [ ] SC-1: Grafana dashboard showing per-agent daily/weekly cost (Azure Foundry)
- [ ] SC-2: Grafana dashboard showing per-agent Claude Code usage (sessions, turns, cost from SDK logs)
- [ ] SC-3: Story completion tracker — count of stories/phases completed per agent per day
- [ ] SC-4: Agent health panel — online/offline/stuck status, last activity time
- [ ] SC-5: Alert when daily agent cost exceeds threshold (configurable)

## Technical Approach

### Data Sources
1. **Azure Cost Management API** — query per-resource costs, filter by AI Services resource
2. **Loki logs** — `[DONE] cost=$X turns=Y duration=Zs` from SDK tool
3. **Loki logs** — `[START]`, `[DONE]`, commit messages for story tracking
4. **Hermes gateway journal** — `Inbound`, `Sent`, presence changes for activity

### Implementation
- Grafana dashboard with Loki data source for SDK usage
- Azure Monitor data source for Foundry costs (or a cron that pushes cost data to Loki)
- LogQL queries for aggregation:
  - `sum by (agent) (count_over_time({job="claude-code"} |~ "DONE" [24h]))`
  - `sum by (agent) (extract_metric({job="claude-code"} |~ "cost=") [24h])`
- Prometheus for agent health metrics (optional, can use Loki for now)

### Panels
1. **Cost Overview** — stacked bar: Azure Foundry + Claude Code per agent per day
2. **Session Activity** — time series: SDK sessions per hour per agent
3. **Story Progress** — table: stories started/completed per agent this week
4. **Agent Status** — stat panels: online/busy/offline per agent
5. **Alerts** — daily cost threshold per agent
