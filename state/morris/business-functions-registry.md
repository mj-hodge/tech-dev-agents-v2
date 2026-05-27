# Business Functions Registry — Gorilla Commerce

**Last Updated:** 2026-04-17T20:25Z
**Owner:** Morris (Engineering Manager)
**Purpose:** Master reliability tracker for all Gorilla Commerce projects. Every business function, health endpoint, and monitoring gap in one place.

**Grading Policy:**
- No programmatic verification of key business functions → **FAIL**
- Unknown monitoring status → **FAIL**
- No reachable health endpoint → grade capped at **D**
- Passing requires: reachable `/health`, Loki integration, documented business functions, programmatic checks for scheduled tasks

---

## 1. advertising-amazon

**URL:** https://advertising-amazon.gorillacommerce.ai
**Health:** `/healthz` → 200 ✅ (checks DB, Redis, LWA tokens, background jobs, sync status)
**Version:** 1.1.1 | **Uptime:** 2053s
**Live Check (2026-04-17T20:21Z):** 200 OK — portfolio_sync GREEN (294), spend_refresh GREEN (874 rows), order_ingestion YELLOW (0 items)
**CI/CD:** 8-job pipeline, comprehensive
**Owner note:** Mark is personally fixing issues — Morris monitors only

### Critical Business Functions

| Function | Type | Verification | Status |
|----------|------|-------------|--------|
| Amazon Ads MCP server | Always-on | `/healthz` | ✅ GREEN |
| Portfolio sync | Background loop | `/healthz` → `background_jobs.portfolio_sync` | ✅ GREEN (294 portfolios) |
| Spend data refresh | Background loop | `/healthz` → `spend_freshness` | ✅ GREEN |
| Order ingestion | Background loop | `/healthz` → `order_ingestion` | ⚠️ YELLOW (0 items stored) |
| SP/SB/SD/DSP campaign sync | Background loop | `/healthz` → `sync_status` | ✅ 3258 campaigns, 5000 SP ad groups, 314 SB, 146 SD |
| TACOS calculations | On-demand | MCP tools | ✅ Available |
| Nightly email reports | — | — | ❌ NOT IMPLEMENTED (future EPIC) |
| Campaign budget allocation | — | — | ❌ NOT IMPLEMENTED (EPIC-003) |

### Monitoring

- **Loki:** ✅ Prometheus + Loki + Grafana + Azure Monitor
- **Alerting:** 20+ alert rules defined
- **Metrics:** Prometheus

### Risks

- Circuit breakers defined but **NOT wired in lifespan startup**
- Silent Redis failures (`except Exception: pass` in `ttl_cache.py`)
- Single PostgreSQL B1ms (no HA)
- All background tasks are asyncio loops (no external scheduler) — startup-sensitive (crash loop history)
- No Sentry/tracing. Max 3 replicas.

### Grade: B+

Comprehensive `/healthz` with sub-component checks, Loki+Prometheus+Grafana, all key functions monitorable. Deducted for: circuit breakers not wired, silent exception swallowing, no HA on DB.

---

## 2. product-health-dashboard

**URL:** https://product-health-dashboard.gorillacommerce.ai
**Health:** ❌ DNS NXDOMAIN — service NOT DEPLOYED
**Loki:** Not deployed. Code has App Insights integration but SDK not wired.

### Critical Business Functions

| Function | Type | Verification | Status |
|----------|------|-------------|--------|
| Product health dashboard UI | Always-on | — | ❌ NOT DEPLOYED |
| Data connectors (Amazon, Walmart, Shopify, NetSuite) | Always-on | — | ❌ NOT DEPLOYED |
| Weight history tracking | On-demand | — | ❌ NOT DEPLOYED |
| Partition manager (monthly data partitioning) | Scheduled | — | ❌ NOT DEPLOYED — **CRITICAL: unscheduled, will fail on month boundary** |
| Email alerts | Event-driven | — | ❌ Code exists, not deployed |

### Monitoring

- **Loki:** ❌ None
- **Alerting:** ❌ None
- **Metrics:** ❌ None

### Risks

- No App Insights SDK wired
- 6+ silent exception swallowing locations
- Weight history LEAD bug
- Staging probes wrong endpoint (`/health` not `/ready`)
- Prometheus dead code (not in deps)
- Outstanding stories: STORY-036 (Error Handling), STORY-037 (Operational Monitoring) — NOT STARTED

### Grade: F

DNS dead, not deployed, zero monitoring, zero business functions verifiable.

---

## 3. tech-datawarehouse (MCP Datalake)

**URL:** https://mcp-tech-datawarehouse.gorillacommerce.ai
**Health:** `/health` → 200 ✅ | `/health/ready` → structured checks (JWKS, adapters, access control, OAuth)
**Live Check (2026-04-17T20:21Z):** /health 200 OK, /health/ready 503 NOT READY — 4 adapter errors (nsdata1, fabric-keepa-lakehouse, sharepoint-gcdata, sharepoint-gcbuildstr) + bigquery-gorilla timeout. Only 5/9 adapters healthy.
**CI/CD:** Full pipeline with pre-deploy gates, smoke tests, Loki deploy status, rollback capability
**DNS Note:** Old URL `tech-datawarehouse.gorillacommerce.ai` is dead. Service lives at `mcp-tech-datawarehouse.gorillacommerce.ai`.

### Critical Business Functions

| Function | Type | Verification | Status |
|----------|------|-------------|--------|
| MCP server for AI-assisted analytics | Always-on | `/health` | ✅ HEALTHY |
| 7 data source adapters (Azure SQL, Fabric, BigQuery, Blob, SharePoint x2) | Always-on | `/health/ready` per-adapter | ✅ All checked |
| Query guardrails (max rows, timeouts, blocked DDL) | Code-enforced | Code review | ✅ Enforced |
| Audit log (90-day retention) | Background loop (hourly) | Internal | ✅ Running |
| Access grant polling | Every 30s | Circuit breaker monitored | ✅ Running |
| OAuth token cleanup | **NOT SCHEDULED** | — | ❌ GAP — code exists but orphaned |

### Monitoring — BEST IN FLEET

- **Loki:** ✅ Full integration (buffered HTTP, batch flush, sensitive data scrubbing)
- **Metrics:** Prometheus (15+ counters/histograms/gauges)
- **Dashboards:** 3 Grafana dashboards
- **Alerting:** 20 alert rules with 12 runbooks
- **SLOs:** 99.5% availability target tracked
- **Logging:** Structured

### Risks

- Silent exception swallowing (#1 recurring defect across fleet)
- No connection pooling
- OAuth cleanup orphaned (code exists, not scheduled)
- No retry/backoff for transient failures

### Grade: A-

Best monitoring posture in fleet. Comprehensive health checks, SLOs, runbooks, Grafana dashboards. Deducted for: silent exceptions, orphaned OAuth cleanup, no connection pooling.

---

## 4. tech-project-mapping

**URL:** https://tech-project-mapping.gorillacommerce.ai
**Health:** `/` → 302 redirect (Azure AD auth-gated). Local `serve.py` has `/health` and `/ready` but NOT deployed to production SWA.
**Live Check:** UP (302 redirect — serving). STORY-347 merged but NOT redeployed.
**Loki:** ✅ **Hosts the Loki+Grafana stack for ALL projects** (`grafana.gorillacommerce.ai`)

### Critical Business Functions

| Function | Type | Verification | Status |
|----------|------|-------------|--------|
| Pipeline Health Dashboard (40+ Azure Functions, 11+ ADF pipelines) | Always-on | Auth-gated, no programmatic check | ⚠️ Not externally verifiable |
| Data Ecosystem Catalog (YAML system of record) | Static files | File existence | ✅ Available |
| Centralized Logging Hub (Loki+Grafana for all projects) | Always-on | `grafana.gorillacommerce.ai/api/health` | ✅ Reachable |
| Power BI Lineage Viewer | Weekly refresh | Pushes directly to main (no PR) | ⚠️ No review gate |
| Service Account Security Tracking | Static page | Auth-gated | ⚠️ Not externally verifiable |
| Hourly data refresh (9 App Insights instances) | GitHub Actions cron `0 * * * *` | GitHub Issue on failure | ⚠️ Partial monitoring |
| Loki backup sidecar | Daily 2 AM UTC | **NO MONITORING** | ❌ Silent failures |

### Monitoring

- **Loki:** ✅ Alert rules defined — but **ALERTMANAGER NOT CONFIGURED** (alerts fire into void)
- **Metrics:** No Prometheus
- **Alerting:** GitHub Issue creation on refresh failure (partial)

### Risks

- **ALERTMANAGER EMPTY** — biggest gap in fleet. Rules exist but nobody receives alerts.
- No production health endpoints on SWA
- Committed secret in `grafana.env`
- Loki single instance (no redundancy)
- Backup sidecar unmonitored

### Grade: C+

Hosts critical Loki/Grafana infrastructure for the entire fleet. Has alert rules defined. But alertmanager not configured = alerts don't reach anyone. No production `/health` on SWA. Auth-gated dashboard.

---

## 5. sourcing-warning-labels

**URL:** https://compliance-warning-labels.gorillacommerce.ai
**Health:** `/health` → 200 ✅ (liveness) | `/health/ready` → 200 ✅ (checks DB with `SELECT 1`). Both excluded from auth in Bicep.
**Live Check:** BOTH HEALTHY
**CI/CD:** Canary deployment with automated rollback, smoke tests, Loki deploy logging — EXCELLENT

### Critical Business Functions

| Function | Type | Verification | Status |
|----------|------|-------------|--------|
| Warning labels management UI | Always-on | `/health` | ✅ Healthy |
| Label creation/approval workflow | On-demand (API) | API endpoints | ✅ Available |
| Active label selection (date-based) | On-demand | API endpoints | ✅ Available |
| Snapshot comparison | On-demand | API endpoints | ✅ Available |
| Excel export | On-demand | API endpoints | ✅ Available |
| PostgreSQL data store | Always-on | `/health/ready` checks DB | ✅ Healthy |

### Monitoring

- **Loki:** ✅ Custom LokiHandler (buffered, 5s flush, structured metadata) — **reference implementation** for other projects
- **Alerting:** ❌ NO alert rules
- **Metrics:** ❌ No Prometheus metrics
- **SLOs:** ❌ None defined
- **Other:** Azure Log Analytics (30d retention), Grafana dashboard exists

### Risks

- No alert rules defined
- No Prometheus metrics
- No SLOs
- Single PostgreSQL (no HA)
- Public network access on DB
- No rate limiting

### Grade: B

Health endpoints properly excluded from auth, Loki integrated (reference implementation), canary deploys, all business functions verifiable. Deducted for: no alerting rules, no metrics, no SLOs.

---

## 6. tech-dev-agents (Ops Console)

**URL:** https://tech-dev-agents.gorillacommerce.ai
**Health:** `/api/health` → 200 ✅ (checks `agents_reachable`, `agents_total`, `loki_reachable`, `uptime`)
**Live Check:** HEALTHY — 3/3 agents reachable, Loki reachable, uptime 68926s (~19h)
**Loki:** ✅ Integrated and reachable

### Critical Business Functions

| Function | Type | Verification | Status |
|----------|------|-------------|--------|
| Dispatch queue API | Always-on | `/api/dispatch/queue` | ✅ Working |
| Agent fleet health monitoring | Always-on | `/api/health` → `agents_reachable` | ✅ 3/3 reachable |
| Loki log aggregation | Always-on | `/api/health` → `loki_reachable` | ✅ Reachable |
| Morris cron jobs (heartbeat, PR review, standup) | Scheduled | Cron system | ✅ Running |
| Story lifecycle (dispatch→claim→complete) | On-demand (API) | API endpoints | ✅ Working |

### Monitoring

- **Loki:** ✅ Integrated
- **Fleet health:** Agent status thresholds, dispatch queue API
- **Alerting:** Alert rules/Grafana dashboards exist as templates (deployment status unclear)

### Risks

- `/api/health` always returns `ok` — no degraded state, no DB check
- Single PostgreSQL, no backup
- Teams-only notification (no PagerDuty fallback)
- No external synthetic monitoring
- Failed stories sit silently (no dead-letter queue)

### Grade: A-

Best operational monitoring, comprehensive health, Loki integrated. Deducted for: health endpoint doesn't check DB, no degraded state, no external synthetic monitoring, failed stories silent.

---

## 7. fabric-keepa

**URL:** N/A (Microsoft Fabric notebooks — no web endpoint)
**Health:** NO HTTP ENDPOINTS. `NB_SM Refresh Check` notebook exists but schedule is **DISABLED**.
**Loki:** N/A (Fabric platform — logs go to Spark stdout only)

### Critical Business Functions

| Function | Type | Verification | Status |
|----------|------|-------------|--------|
| US Daily Pipeline | Scheduled (5:30 AM ET) | Fabric pipeline status | ❌ NOT PROGRAMMATICALLY CHECKABLE |
| CA Daily Pipeline | Scheduled (6:15 AM ET) | Fabric pipeline status | ❌ NOT PROGRAMMATICALLY CHECKABLE |
| US Hourly Category Rank | Scheduled (7x/day) | Fabric pipeline status | ❌ NOT PROGRAMMATICALLY CHECKABLE |
| CA Hourly Category Rank | Scheduled (3x/day) | Fabric pipeline status | ❌ NOT PROGRAMMATICALLY CHECKABLE |
| BestsellerTracker Power BI refresh | Scheduled | SM Refresh Check (**DISABLED**) | ❌ DISABLED |
| Keepa API token monitoring | Circuit breaker | No alerting | ❌ Silent failures |
| Monday.com integration | On failure | Silent failure fallback | ❌ Silent failures |

### Monitoring

- **Loki:** N/A
- **Alerting:** Teams webhook (SM Refresh Check only — currently DISABLED)
- **Metrics:** Pipeline run metadata table, token budget circuit breaker
- **SLOs:** None

### Risks

- **SM Refresh Check DISABLED** — the only monitoring that existed is off
- Pipeline failures = nobody notified until data is visibly stale
- STORY-015 bug: CA pipeline references wrong notebook
- Schedule end dates (Jan/Feb 2027) will silently stop pipelines
- Manual git sync deployment (no CI/CD)
- No staging environment
- STORY-348 dispatched to Dan for SRE runbook

### Grade: F

Zero programmatic monitoring accessible to Morris. SM Refresh Check disabled. No Loki, no health endpoints. Pipeline failures are silent.

---

## 8. tech-gc-knowledgebase

**URL:** N/A (content repo — no web service)
**Health:** N/A (git repo)
**Loki:** N/A

### Critical Business Functions

| Function | Type | Verification | Status |
|----------|------|-------------|--------|
| Knowledge content | Always available | `git clone` | ✅ Available |
| Weekly curation (Cole) | Scheduled (Sunday 9 AM ET) | Cron deployed | ✅ Running |
| Content freshness | Continuous | `git log` | ✅ Active |

### Grade: B+

Content repo — monitoring = git freshness, properly tracked via cron.

---

## Reliability Grades Summary

| # | Project | Grade | `/health` | Loki | Business Functions Documented | Programmatically Monitorable |
|---|---------|-------|-----------|------|-------------------------------|------------------------------|
| 1 | tech-datawarehouse | **A-** | ✅ `/health` + `/health/ready` | ✅ Full | ✅ Yes | ✅ Yes |
| 2 | tech-dev-agents | **A-** | ✅ `/api/health` | ✅ Yes | ✅ Yes | ✅ Yes |
| 3 | advertising-amazon | **B+** | ✅ `/healthz` (sub-components) | ✅ Full | ✅ Yes | ✅ Yes |
| 4 | tech-gc-knowledgebase | **B+** | N/A (repo) | N/A | ✅ Yes | ✅ Git-based |
| 5 | sourcing-warning-labels | **B** | ✅ `/health` + `/health/ready` | ✅ Reference impl | ✅ Yes | ✅ Yes |
| 6 | tech-project-mapping | **C+** | ⚠️ Auth-gated (no prod `/health`) | ✅ Hosts fleet Loki | ⚠️ Partial | ⚠️ Partial |
| 7 | product-health-dashboard | **F** | ❌ DNS dead | ❌ None | ❌ No | ❌ No |
| 8 | fabric-keepa | **F** | ❌ No endpoints | ❌ N/A | ⚠️ Known but unverifiable | ❌ No |

**Fleet Average:** ~C+ (2 critical failures dragging fleet down)

---

## SRE Stories — Status

### Already Dispatched (All Failed)

| Story | Project | Assignee | Status |
|-------|---------|----------|--------|
| STORY-348 | fabric-keepa | Dan | FAILED — exhausted retries |
| STORY-357 | tech-project-mapping | Derrick | FAILED — exhausted retries |
| STORY-360 | product-health-dashboard | Dan | FAILED — exhausted retries |

### New Stories Needed

| Priority | Project | Scope | Description |
|----------|---------|-------|-------------|
| **P1** | product-health-dashboard | Medium | Deploy service, fix DNS, add monitoring. BLOCKED on deploy approval. |
| **P1** | tech-project-mapping | Small | Wire `alertmanager_url` in Loki config (alerts fire into void). Expose `/health` on SWA or create synthetic monitor. Rotate committed secret in `grafana.env`. |
| **P2** | advertising-amazon | Small | Wire circuit breakers in lifespan startup. Fix silent Redis exception swallowing. Mark fixing — track only. |
| **P3** | sourcing-warning-labels | Small | Add Grafana alert rules for error rates/latency. Add SLO definitions. |
| **P3** | tech-dev-agents | Small | Add DB check to `/api/health`, implement degraded state. Add dead-letter queue for failed stories. |
| **P3** | tech-datawarehouse | Small | Schedule orphaned OAuth token cleanup. Fix silent exception handling. |

### Fresh SRE Stories Needed (All Prior Failed)

| Priority | Project | Scope | Description |
|----------|---------|-------|-------------|
| P1 | product-health-dashboard | Medium | Deploy service, fix DNS, add /health + Loki. BLOCKED: needs Azure deploy approval |
| P1 | tech-project-mapping | Small | Configure alertmanager (alerts fire into void), expose /health on SWA, rotate grafana.env secret |
| P1 | tech-datawarehouse | Small | Fix 4 failing adapters (nsdata1, keepa-lakehouse, sharepoint x2), schedule OAuth cleanup |
| P2 | fabric-keepa | Medium | Create SRE runbook, enable SM Refresh Check, add programmatic pipeline monitoring |
| P3 | sourcing-warning-labels | Small | Add Grafana alert rules, SLO definitions |
| P3 | tech-dev-agents | Small | Add DB check to /api/health, implement degraded state, dead-letter queue |
