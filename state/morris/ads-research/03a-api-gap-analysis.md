# Amazon Ads API v3 — Capability Gap Analysis
**Generated:** 2026-04-23 | **Phase 3a: Internal Codebase vs API Capabilities**

## 1. What's Already Integrated

### 1.1 Campaign & Ad Group Management
| Entity | List | Get | Create | Update | Archive |
|--------|------|-----|--------|--------|---------|
| SP Campaigns | ✅ | ✅ | ✅ | ✅ | via state |
| SP Ad Groups | ✅ | ✅ | ✅ | ✅ | via state |
| SP Keywords | ✅ | ✅ | ✅ | ✅ (bid) | ✅ |
| SP Product Ads | ✅ | ✅ | ✅ | ✅ | via state |
| SP Targets (product/contextual) | ✅ | ✅ | ❌ | ✅ | ✅ |
| SP Negative Keywords | ✅ | — | ✅ | — | ✅ |
| SP Negative Targets | ✅ | — | ✅ | — | ✅ |
| SP Campaign Neg Keywords | ✅ | — | — | — | — |
| SB Campaigns (v4) | ✅ | ✅ | ✅ | ✅ | via state |
| SB Ad Groups (v4) | ✅ | ✅ | ✅ | ✅ | via state |
| SB Keywords | ✅ | — | — | — | — |
| SB Neg Keywords | ✅ | — | — | — | — |
| SB Targets | ✅ | — | — | — | — |
| SD Campaigns | ✅ | ✅ | ✅ | ✅ | via state |
| SD Ad Groups | ✅ | ✅ | ✅ | ✅ | via state |
| SD Targets | ✅ | — | — | — | — |
| SD Negative Targets | ✅ | — | — | — | — |
| Portfolios | ✅ | ✅ | — | ✅ (budget) | — |

### 1.2 Bid Management
| Endpoint | Status |
|----------|--------|
| SP keyword bid update | ✅ |
| SP target bid update | ✅ |
| SP ad group default bid | ✅ |
| SP placement bid adjustments | ✅ |
| SB ad group bid | ✅ |
| SD ad group bid | ✅ |
| SP bid recommendations | ✅ |
| SP target bid recommendations | ✅ |
| SP budget recommendations | ✅ |

### 1.3 Reporting (Unified v3 `/reporting/reports`)
| Report Type ID | Metrics Requested | Time Unit |
|----------------|-------------------|-----------|
| `spCampaigns` | spend, sales7d, impressions, clicks | DAILY |
| `sbCampaigns` | cost, sales, impressions, clicks | DAILY |
| `sdCampaigns` | cost, sales, impressions, clicks | DAILY |
| `spTargeting` | spend, sales7d, impressions, clicks, targeting, matchType | DAILY |
| Custom (via `sp_request_report`) | impressions, clicks, cost, purchases1d/7d/14d/30d | SUMMARY |
| DSP reports | impressions, clicks, cost, totalCost | SUMMARY |

### 1.4 Budget Management
| Endpoint | Status |
|----------|--------|
| Portfolio budget cap | ✅ |
| SP campaign budget rules CRUD | ✅ |
| SP campaign budget recommendations | ✅ |

### 1.5 Other APIs Integrated
- Attribution API — tag creation, publisher list, report generation
- Brand Metrics API — NTB, halo effect, brand studies
- Audiences API — list/create/update/delete, browse segments
- DSP — order + line item CRUD, async reporting
- AMC — workflow + execution CRUD (infrastructure present, no optimization workflows defined)
- Stores API — pages and store management
- Creative Assets API — upload/status
- Insights API — present but depth unknown
- Admin API — account/invitation/role management
- SP-API — order metrics (TACOS), order sync, FBA inventory/fees, catalog, listings

---

## 2. Available but NOT Integrated

### 2.1 Critical Missing Report Types

#### `spSearchTerm` — **#1 Missing Capability**
Without this: the system cannot identify which actual customer search queries are converting (to harvest as exact/phrase keywords) and which are wasting spend (to add as negatives). **Foundation of SP optimization.**

Columns available: campaignId, adGroupId, keywordId, targeting, searchTerm, matchType, impressions, clicks, cost, CTR, purchases7d/14d, sales7d/14d, ROAS14d, ACOS14d

#### `spPurchasedProduct` — ASIN-Level Attribution
Shows which ASINs were actually purchased after ad click — including cross-ASIN halo.

#### `spAdvertisedProduct` — ASIN Ad Performance
Per-ASIN performance within campaigns. Critical for pausing underperformers and scaling winners.

#### `sbSearchTerm` / `sbPurchasedProduct` — SB equivalents
Not requested anywhere in the codebase.

#### `sdTargeting` with audience/context breakdowns
SD performance by audience segment — needed to optimize SD bids by audience value.

### 2.2 Missing Derived Metrics in Current Reports
| Metric | Column Name | Why It Matters |
|--------|-------------|----------------|
| ACOS (7d/14d) | `advertisingCostOfSales7d/14d` | Direct ACOS target comparison |
| ROAS (14d) | `returnOnAdSpend14d` | Revenue efficiency |
| Click-through rate | `clickThroughRate` | Ad quality signal |
| Cost per click | `costPerClick` | Actual CPC vs bid |
| Cost per acquisition | `costPerConversion14d` | Unit economics |
| New-to-brand purchases | `newToBrandPurchases14d` | Customer acquisition value |
| New-to-brand sales | `newToBrandSales14d` | NTB contribution |
| Top-of-search IS | `topOfSearchImpressionShare` | Competitive position |

### 2.3 Missing Write Endpoints
- **SP Create Target** (`POST /sp/targets`) — blocks auto→manual expansion
- **SB Keyword Write Operations** — no create/update/archive
- **SB Target Write Operations** — read-only
- **SD Target Write Operations** — read-only
- **SD Bid Recommendations** — not integrated
- **SB Target Recommendations** — not integrated

### 2.4 Missing Budget Management
- SB budget rules (`/sb/v4/campaigns/{id}/budgetRules`) — not integrated
- SD budget rules — not integrated
- Campaign-level budget pacing data (`budgetUsagePercent`) — not fetched
- Portfolio budget utilization (`inBudget`/`budgetPolicyStatus`) — not tracked

### 2.5 Dayparting / Scheduling
**Amazon does not provide hourly dayparting for SP or SB in the standard Ads API.** SP Budget Rules support `DAYOFWEEK` predicates at day granularity. True dayparting requires external scheduler (agentic automation pattern).

### 2.6 Change History API (`/history/`) — Not Integrated
Full audit log of all changes to campaigns, ad groups, keywords, targets. Critical for preventing optimization conflicts and change attribution.

### 2.7 AMC — Zero Optimization Workflows
Infrastructure fully built. No SQL workflows defined. AMC enables: multi-touch attribution, path-to-conversion, audience overlap, incrementality measurement, custom attribution windows.

---

## 3. Rate Limits
| API Category | Requests/sec | Notes |
|---|---|---|
| SP/SB/SD List endpoints | ~2 req/s per profile | Token bucket, bursts allowed |
| SP/SB/SD Write endpoints | ~1 req/s per profile | Stricter for mutations |
| Reporting submit | ~1 req/s per profile | Report creation |
| Reporting poll | ~2 req/s | Status checks |
| Recommendation endpoints | ~1 req/s per profile | Low limit |
| Profile-level cap | ~50 req/s total | Across all endpoints |
| Concurrent pending reports | ~100 per profile | Async queue |

---

## 4. Gap-to-Leverage Ranking

### Tier 1 — High Impact, Low Effort
1. **Search Term Reports** (`spSearchTerm`) — ~2 hours, unlocks negative keyword mining + query harvesting
2. **Richer Report Columns** (ACOS, ROAS, NTB, CTR) — ~1 hour
3. **SP Target Create** — ~3 hours, enables auto→manual expansion
4. **Purchased Product Report** — ~2 hours, cross-ASIN attribution
5. **Budget Pacing Data** — ~2 hours, mid-day budget cap detection

### Tier 2 — High Impact, Medium Effort
6. **Change History API** — ~1 day, prevents optimization conflicts
7. **SB Keyword Write Operations** — ~1 day
8. **Impression Share Columns** — ~2 hours, competitive positioning
9. **SB Budget Rules** — ~4 hours
10. **SD/SB Target Bid Recommendations** — ~4 hours each

### Tier 3 — Transformational, Higher Effort
11. **AMC Optimization Workflows** — ~1 week
12. **Negative Keyword Mining Pipeline** — ~1 week, 10-15% efficiency gain
13. **Auto→Manual Keyword Harvesting** — ~1 week
14. **ASIN-Level Optimization** — ~3 days

---

## 5. Coverage Summary
| Capability Area | Current Coverage | Key Gap |
|---|---|---|
| SP bid management | ~85% | Missing: create target; SB/SD keyword write |
| SP reporting metrics | ~40% | Missing: search term, purchased product, ACOS/ROAS columns |
| SB bid management | ~30% | Missing: keyword/target write operations |
| SD bid management | ~40% | Missing: target write, bid recommendations |
| Budget automation | ~60% | Missing: SB/SD budget rules, pacing data |
| Recommendations | ~70% | Missing: SB/SD target recommendations |
| Attribution depth | ~35% | Missing: search term attribution, AMC workflows |
| Change tracking | 0% | History API entirely absent |
| Competitive data | 5% | Impression share not collected |
| Dayparting | N/A | Not a native API capability for SP/SB |

**#1 Priority:** Enable `spSearchTerm` reports (~10 lines of code) — unlocks negative keyword mining and keyword harvesting, the highest-leverage ongoing optimizations.
