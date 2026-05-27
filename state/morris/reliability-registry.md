# Reliability Registry — Last Updated: 2026-04-20T04:02Z

## Service Health Endpoints

| Service | URL | Expected | Actual (2026-04-20T04:02Z) | Status |
|---------|-----|----------|---------------------|--------|
| advertising-amazon | https://advertising-amazon.gorillacommerce.ai/healthz | 200 | 200 (v1.1.1, **DEGRADED** — portfolio_sync ✅GREEN, spend_refresh ✅GREEN, order_ingestion ⚠️YELLOW, uptime 210141s) | ⚠️ DEGRADED (stable) |
| tech-datawarehouse | https://mcp-tech-datawarehouse.gorillacommerce.ai/health | 200 | 200 | ✅ UP |
| tech-datawarehouse /ready | https://mcp-tech-datawarehouse.gorillacommerce.ai/health/ready | 200 | 503 | ⚠️ DEGRADED (adapter failures) |
| tech-dev-agents | https://tech-dev-agents.gorillacommerce.ai/api/health | 200 | 200 (v0.1.0, 3/3 agents, Loki reachable, uptime 135903s) | ✅ UP |
| sourcing-warning-labels | https://compliance-warning-labels.gorillacommerce.ai/health | 200 | 200 | ✅ UP |
| sourcing-warning-labels /ready | https://compliance-warning-labels.gorillacommerce.ai/health/ready | 200 | 200 | ✅ UP |
| tech-project-mapping | https://tech-project-mapping.gorillacommerce.ai/health | 200 | 200 (HTML — SWA auth wall, no dedicated health EP) | ⚠️ AUTH WALL |
| grafana | https://grafana.gorillacommerce.ai/api/health | 200 | 200 (v11.5.2) | ✅ UP |
| product-health-dashboard | https://product-health-dashboard.gorillacommerce.ai/health | 200 | 000 (DNS fail) | 🔴 DOWN (known) |
| fabric-keepa | No endpoint (ACI batch job) | N/A | N/A | PR #2 merged |

## Business-Critical Functions

| Function | Project | Schedule | How to Check | Status |
|----------|---------|----------|-------------|--------|
| Amazon Ad Reports Sync | advertising-amazon | Every 2h | App Insights + /healthz | ⚠️ Mark fixing |
| FBA Shipped Sales to Celigo | advertising-amazon | Every 2h async loop | Blob storage file timestamps | ⚠️ PR #95 filename incident (see decisions-log) |
| Walmart Order Ingestion | advertising-amazon | Every 30m | App Insights | ❓ UNKNOWN — need to verify |
| DPC (Daily Price Change) Emails | fabric-keepa | Daily (Timer Trigger) | No check mechanism yet | 🔴 FAIL — no monitoring |
| Product Health Dashboard Scheduler | product-health-dashboard | APScheduler (various) | No external check | 🔴 FAIL — in-memory lock, no health EP |
| Power BI Dataset Refreshes | tech-project-mapping monitors | 14 datasets, various | PBI Admin API via refresh-data.sh | ✅ Monitored (hourly) |
| ADF Pipeline Runs | tech-project-mapping monitors | Various nightly/hourly | Azure Management API | ✅ Monitored (hourly) |
| Azure Function Health | tech-project-mapping monitors | 9 App Insights instances | App Insights KQL | ✅ Monitored (hourly) |
| GitHub Actions Crons | tech-project-mapping monitors | Various | GitHub API | ✅ Monitored (hourly, 6 repos) |
| Nightly Emails (ecomm-newsletter) | Unknown — needs investigation | Nightly, before 7 AM ET | ❓ | 🔴 FAIL — not tracked |
| NetSuite SKU Sync | Unknown — needs investigation | Nightly | ❓ | 🔴 FAIL — not tracked |
| Loki Alert Delivery | tech-project-mapping (Loki stack) | Real-time | Alertmanager status | 🔴 FAIL — Alertmanager NOT connected, alerts fire to void |

## Per-Project Reliability Scorecard

| Project | Health EP | SRE Runbook | Key Functions Documented | Monitoring | Alert Delivery | Grade |
|---------|-----------|-------------|-------------------------|------------|----------------|-------|
| advertising-amazon | ✅ /healthz | ❓ Unknown | ❌ No | Partial (App Insights) | ❌ None | C |
| tech-datawarehouse | ✅ /health (DNS issue) | ❓ Unknown | ❌ No | Partial | ❌ None | D |
| tech-dev-agents | ✅ /api/health | ✅ Yes | ✅ Yes (ops-console) | ✅ Morris fleet-health | ✅ Teams | A |
| tech-project-mapping | ⚠️ Pending deploy | ❌ No | ❌ No | ✅ Self-monitors others | 🔴 Alertmanager broken | C |
| product-health-dashboard | 🔴 None → PR open | ❌ No | ❌ No | ❌ None | ❌ None | F |
| fabric-keepa | 🔴 None → PR open | 🔴 None → Story in progress | ❌ No | ❌ None | ❌ None | F |
| sourcing-warning-labels | ✅ /health + /ready | ❓ Unknown | ❓ Unknown | ❌ None (STORY-383 queued) | ❌ None | C |
| tech-gc-knowledgebase | N/A (static) | N/A | N/A | N/A | N/A | N/A |

## SRE Stories In Flight

| Story | Project | Scope | Status | Agent | PR |
|-------|---------|-------|--------|-------|-----|
| STORY-347 | tech-project-mapping | Small | ✅ Completed, PR #6 merged | Dan | Merged |
| STORY-348 | fabric-keepa | Small | ✅ Completed, PR #2 merged | Dan | Merged |
| STORY-349 | product-health-dashboard | Small | ✅ Completed, PR #19 closed/merged | Dan | Closed |
| STORY-351 | tech-project-mapping | Medium | 🟡 Alertmanager — PR #7 open, CI running | Derrick (STORY-357) | PR #7 |
| STORY-354 | advertising-amazon | Small | 🟡 PRs #92, #93, #103 open, all APPROVED but BLOCKED by branch protection | Derrick | PRs open |
| STORY-380 | tech-dev-agents | Medium | ➡️ Superseded by STORY-389 (queued) | — | — |
| STORY-384 | tech-dev-agents | Small | 🟡 Previously in progress — Dan now on STORY-388 | Dan | PR #50 |
| STORY-385 | tech-dev-agents | Small | 🟡 RETRY 1/3 pending in queue | — | PR #49 |
| STORY-386 | tech-dev-agents | Small | 🟡 RETRY 1/3 pending in queue | — | — |
| STORY-387 | sourcing-warning-labels | Small | 🟡 RETRY 1/3 pending in queue | — | — |
| STORY-388 | product-health-dashboard | Medium | 🟡 RETRY 1/3 pending in queue | — | — |
| STORY-389 | tech-dev-agents | Medium | ➡️ Superseded by STORY-397 | — | — |
| STORY-396 | product-health-dashboard | Medium | 🟡 RETRY 1/3 pending in queue | — | PR #20 |
| STORY-397 | tech-dev-agents | Medium | 🟡 RETRY 1/3 pending in queue | — | — |
| STORY-398 | advertising-amazon | Medium | 🟡 PR #106 open (keyword analytics) | — | PR #106 |
| STORY-399 | advertising-amazon | Medium | 🟡 Burnt — re-dispatched as STORY-411 | — | — |
| STORY-400 | advertising-amazon | Medium | 🟡 Burnt — re-dispatched as STORY-412 | — | — |
| STORY-401 | advertising-amazon | Medium | 🟡 Burnt — re-dispatched as STORY-413 | — | — |
| STORY-402 | advertising-amazon | Medium | 🟡 Burnt — re-dispatched as STORY-414 | — | — |
| STORY-410 | tech-dev-agents | Medium | ⏳ Pending [RETRY 2/3] — dashboard presence | — | PR #53 |
| STORY-411 | advertising-amazon | Medium | ⏳ Pending [RETRY 2/3] — keyword analytics | — | PR #109 |
| STORY-412 | advertising-amazon | Medium | ⏳ Pending [RETRY 2/3] — product targeting | — | — |
| STORY-413 | advertising-amazon | Medium | 🟢 Active — Dan Phase 7 + Derrick Phase 4 (DUPLICATE) | Dan+Derrick | PR #108 |
| STORY-414 | advertising-amazon | Medium | ⏳ Pending [RETRY 2/3] — analytics dashboard | — | — |
| STORY-266 | advertising-amazon | Small | ⏳ Pending (P0 from Mark) — budget verification fix | — | — |

## SRE Stories Needed (Not Yet Created)

| Priority | Project | Gap | Proposed Story |
|----------|---------|-----|---------------|
| P0 | tech-project-mapping | Alertmanager not connected — alerts fire to void | STORY-350: Connect Alertmanager for Loki alerts |
| P0 | tech-project-mapping | No live health probing of other services | STORY-351: Reliability Center — live health dashboard |
| P1 | tech-project-mapping | No scheduled task SLA tracking | Part of STORY-351 |
| P1 | advertising-amazon | No SRE runbook, key functions not documented | STORY-352: advertising-amazon SRE audit |
| P1 | tech-datawarehouse | DNS failing, no SRE runbook | STORY-353: tech-datawarehouse SRE audit |
| P2 | sourcing-warning-labels | Health EP returns 401, no SRE docs | STORY-354: sourcing-warning-labels SRE audit |
| P2 | tech-project-mapping | No Fabric pipeline visibility | Part of STORY-022 (existing backlog) |

## What Morris Checks Today vs What's Missing

### Morris Checks (via crons + manual)
- Agent VM health (SSH: CPU, disk, memory, SDK processes)
- Dispatch queue (API: pending/claimed)
- Open PRs (GitHub API)
- Service endpoints (curl: /healthz, /health)

### Missing from Morris's Checks
- ❌ Business-critical function execution (did the nightly email run?)
- ❌ Data freshness (is advertising data < 4h old?)
- ❌ Scheduled task verification (did every Timer Trigger fire on time?)
- ❌ Loki alert status (are any alerts currently firing?)
- ❌ PBI refresh failures (are all 14 datasets refreshing?)
- ❌ ADF pipeline status (did all nightly pipelines complete?)
- ❌ Blob storage file arrival times (is data landing as expected?)

These are all available via tech-project-mapping's data.json — Morris should be checking this hourly.

## Loki Integration Status — Last Updated: 2026-04-17

| Project | Loki Integrated | Grafana Dashboard | Push Endpoint | Action |
|---------|:-:|:-:|---|---|
| sourcing-warning-labels | ✅ Yes | ✅ Yes | app/loki.py LokiHandler | Reference implementation |
| tech-dev-agents | ⚠️ Tests only | ✅ Yes (hermes-gateway) | tests only | SRE: Add production Loki client |
| advertising-amazon | ❌ No | ❌ No | Python logging only | SRE: Add LokiHandler (STORY being specced) |
| product-health-dashboard | ❌ No | ❌ No | Python logging only | SRE: Add LokiHandler |
| tech-datawarehouse | ❌ No | ❌ No | Python logging only | SRE: Add LokiHandler |
| fabric-keepa | ❌ No | ❌ No | Fabric notebooks | SRE: Bridge to Loki or accept Fabric monitoring |

## Grafana/Loki Access — RESOLVED 2026-04-17
- Morris can now query Loki via https://grafana.gorillacommerce.ai/loki/api/v1/
- Labels endpoint: /loki/api/v1/labels
- Query endpoint: /loki/api/v1/query_range
- Label values: /loki/api/v1/label/{label}/values
- Rules: /loki/api/v1/rules
- Auth: Loki path is unauthenticated through Caddy reverse proxy
- ISSUE: Alert rules directory doesn't exist on Loki server — gorilla-alerts.yaml is in the repo but NOT deployed

## Loki Live Status — 2026-04-20T02:39Z

### Projects Actively Logging
| Project | Environments | Status |
|---------|-------------|--------|
| advertising-amazon | prod, uat | ⚠️ Active — 1 error (FBA UTF-8), 90 warnings (Amazon Reporting API 400s — invalid reportTypeId/groupBy, duplicate reports, known) |
| sourcing-warning-labels | prod | ⚠️ Silent 4h+ — /health returns 200, but zero info logs (scheduled functions may not be running) |
| tech-datawarehouse | — | ⚠️ 10 warnings in last 6h (adapter health check failures — fabric-keepa-lakehouse, sharepoint Azure AD token) |
| tech-dev-agents | dev | ✅ Clean — no errors |

### Projects NOT Logging to Loki
| Project | Status |
|---------|--------|
| product-health-dashboard | ❌ No Loki integration |
| fabric-keepa | ❌ No Loki integration |
| tech-project-mapping | ❌ No Loki integration |

### Error/Warning Breakdown (last 6h as of 02:39Z)

**Errors: 1** ✅ (stable)
- advertising-amazon: 1 — FBA inventory ingestion UTF-8 decode error (`'utf-8' codec can't decode byte 0xae in position 13752`)

**Warnings: 100**
- advertising-amazon: 90 — Amazon Reporting API 400s (invalid reportTypeId, invalid groupBy values, duplicate report 425s) — known
- tech-datawarehouse: 10 — adapter health check failures (fabric-keepa-lakehouse, sharepoint Azure AD token)

### Key Issue: Alert Rules NOT Deployed
gorilla-alerts.yaml exists in repo loki/rules/ but the Loki server reports NO rules configured. The rules directory was never mounted/deployed to the ACI instance.

## SRE Story Queue — 2026-04-17

### P0 — Blocking Morris Monitoring

| Story | Repo | Scope | Description | Status |
|-------|------|-------|-------------|--------|
| STORY-350 | tech-project-mapping | Medium | Grafana service account + Morris Loki query access | Ready to dispatch |
| STORY-351 | tech-project-mapping | Medium | Connect Alertmanager to Loki ruler so alerts actually fire | Ready to dispatch |

### P1 — Loki Integration (per-project)

| Story | Repo | Scope | Description | Status |
|-------|------|-------|-------------|--------|
| STORY-259 | advertising-amazon | Medium | SRE: Add LokiHandler, health EP, Loki integration, monitoring | In progress — Derrick (Phase 7 Test Design) |
| STORY-353 | tech-datawarehouse | Small | Add LokiHandler, health endpoint, LOKI_URL config | Ready to dispatch |
| STORY-354 | product-health-dashboard | Small | Add LokiHandler, health endpoint, LOKI_URL config | Blocked — DNS/PR#19 first |
| STORY-355 | tech-dev-agents | Small | Move Loki client from tests to production src/ | Ready to dispatch |

### P2 — Business Function Monitoring

| Story | Repo | Scope | Description | Status |
|-------|------|-------|-------------|--------|
| STORY-356 | tech-project-mapping | Medium | Add /api/status unauthenticated endpoint for business function monitoring | Ready to dispatch |
| STORY-357 | advertising-amazon | Small | Document all scheduled functions, expected schedules, verification | Being specced by Mark |

### Critical Gap: Morris is BLIND to Application Errors
Without Grafana API access (STORY-350), Morris cannot:
- Query Loki for error/critical logs
- Detect silent failures (scheduled tasks that didn't run)
- Correlate health endpoint status with application-level errors
- Verify business functions executed successfully

This makes STORY-350 the highest priority SRE item.
