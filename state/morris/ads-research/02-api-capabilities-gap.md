# Amazon Advertising API v3 — Capability Gap Analysis
**Generated:** 2026-04-23 | **Phase 3 of Ads Research**

## 1. What's Already Integrated

### Campaign & Ad Group Management
| Entity | List | Get | Create | Update | Archive |
|--------|------|-----|--------|--------|---------|
| SP Campaigns | ✅ | ✅ | ✅ | ✅ | via state |
| SP Ad Groups | ✅ | ✅ | ✅ | ✅ | via state |
| SP Keywords | ✅ | ✅ | ✅ | ✅ (bid) | ✅ |
| SP Product Ads | ✅ | ✅ | ✅ | ✅ | via state |
| SP Targets | ✅ | ✅ | ❌ | ✅ | ✅ |
| SP Negative Keywords | ✅ | — | ✅ | — | ✅ |
| SP Negative Targets | ✅ | — | ✅ | — | ✅ |
| SB Campaigns (v4) | ✅ | ✅ | ✅ | ✅ | via state |
| SB Ad Groups (v4) | ✅ | ✅ | ✅ | ✅ | via state |
| SB Keywords | ✅ | — | — | — | — |
| SB Targets | ✅ | — | — | — | — |
| SD Campaigns | ✅ | ✅ | ✅ | ✅ | via state |
| SD Ad Groups | ✅ | ✅ | ✅ | ✅ | via state |
| SD Targets | ✅ | — | — | — | — |
| Portfolios | ✅ | ✅ | — | ✅ (budget) | — |

### Bid Management
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

### Reporting (v3 Unified)
| Report Type | Metrics | Time Unit |
|-------------|---------|-----------|
| `spCampaigns` | spend, sales7d, impressions, clicks | DAILY |
| `sbCampaigns` | cost, sales, impressions, clicks | DAILY |
| `sdCampaigns` | cost, sales, impressions, clicks | DAILY |
| `spTargeting` | spend, sales7d, impressions, clicks, targeting, matchType | DAILY |

### Also Integrated
- Attribution API — tag creation, publisher list, report generation
- Brand Metrics API — new-to-brand, halo effect
- Audiences API — full CRUD
- DSP — order + line item CRUD, async reporting
- AMC — workflow/execution CRUD (infrastructure only, no optimization workflows)
- Stores API, Creative Assets API, Insights API, Admin API
- SP-API — order metrics (TACOS), order sync, FBA inventory/fees

---

## 2. Critical Missing Capabilities

### 2.1 Missing Report Types (Highest Priority)

#### `spSearchTerm` — **#1 Missing Capability**
Without this: system cannot identify which customer search queries convert (to harvest as exact/phrase keywords) or waste spend (to add as negatives). **Foundation of SP optimization.**

#### `spPurchasedProduct` — ASIN-Level Attribution
Shows which ASINs were purchased after ad click, including cross-ASIN halo. Essential for true ROAS at ASIN level.

#### `spAdvertisedProduct` — ASIN Ad Performance
Per-ASIN performance within campaigns. Critical for pausing underperformers and scaling winners.

#### `sbSearchTerm` / `sbPurchasedProduct`
Same concepts for Sponsored Brands — not requested anywhere.

### 2.2 Missing Report Columns (Already Available)
| Metric | Column | Why It Matters |
|--------|--------|----------------|
| ACOS (14d) | `advertisingCostOfSales14d` | Direct target comparison |
| ROAS (14d) | `returnOnAdSpend14d` | Revenue efficiency |
| CTR | `clickThroughRate` | Ad quality signal |
| CPC | `costPerClick` | Actual vs bid |
| CPA | `costPerConversion14d` | Unit economics |
| NTB purchases | `newToBrandPurchases14d` | Customer acquisition |
| Top-of-search IS | `topOfSearchImpressionShare` | Competitive position |
| Units sold | `unitsSoldClicks14d` | Volume metric |

### 2.3 Missing Write Endpoints
- **SP Create Target** (`POST /sp/targets`) — blocks auto→manual expansion
- **SB Keyword Write** — SB keyword bids can't be adjusted programmatically
- **SB/SD Target Write** — read-only currently
- **SD Bid Recommendations** — not integrated
- **SB Target Recommendations** — not integrated

### 2.4 Missing Budget Capabilities
- SB budget rules (SP has full CRUD, SB does not)
- SD budget rules
- Campaign-level budget pacing (`budgetUsagePercent` — returned by API, not stored)
- Portfolio `inBudget` / `budgetPolicyStatus` — not tracked

### 2.5 Missing Infrastructure
- **Change History API** (`GET /history/campaigns`) — 0% integrated. Critical for preventing optimization conflicts.
- **Impression Share columns** — competitive positioning signals not collected
- **AMC optimization workflows** — infrastructure exists, zero SQL workflows defined

### 2.6 Dayparting
Amazon does NOT provide hourly dayparting for SP/SB. Workaround: external scheduler that pauses/enables campaigns at target hours (agentic pattern).

---

## 3. Rate Limits
| Category | Rate | Notes |
|----------|------|-------|
| List endpoints | ~2 req/s per profile | Token bucket |
| Write endpoints | ~1 req/s per profile | Stricter |
| Report submit | ~1 req/s per profile | Async queue |
| Recommendations | ~1 req/s per profile | Low limit |
| Profile-level total | ~50 req/s | All endpoints |
| Concurrent reports | ~100 per profile | Polling 1-30 min |

Codebase has PostgreSQL-backed rate limiter — architecture is correct.

---

## 4. Attribution Data Landscape
| Source | Provides | Status |
|--------|----------|--------|
| `spTargeting` report | Keyword→ROAS, CPC, conversion | ✅ (limited columns) |
| `spSearchTerm` report | Query→purchase attribution | ❌ Not integrated |
| `spPurchasedProduct` | Advertised→purchased ASIN | ❌ Not integrated |
| `spAdvertisedProduct` | Per-ASIN performance | ❌ Not integrated |
| Attribution API | Off-Amazon channel attribution | ✅ Infrastructure |
| SP-API orders | Total sales (TACOS) | ✅ |
| AMC | Cross-channel multi-touch | ✅ Infra, ❌ No workflows |
| Brand Metrics | NTB, brand halo | ✅ |

**14-day window is industry standard** for optimization decisions.

---

## 5. Gap-to-Leverage Ranking

### Tier 1 — High Impact, Low Effort
1. **Search Term Reports** (`spSearchTerm`) — ~2 hours, unlocks neg keyword mining + harvesting
2. **Richer Report Columns** (ACOS, ROAS, NTB, CTR) — ~1 hour
3. **SP Target Create** (`POST /sp/targets`) — ~3 hours, enables ASIN targeting
4. **Purchased Product Report** — ~2 hours, cross-ASIN attribution
5. **Budget Pacing Data** — ~2 hours, mid-day budget decisions

### Tier 2 — High Impact, Medium Effort
6. **Change History API** — ~1 day, prevents optimization conflicts
7. **SB Keyword Write** — ~1 day, SB bid optimization
8. **Impression Share Columns** — ~2 hours, competitive signals
9. **SB Budget Rules** — ~4 hours
10. **SD/SB Bid Recommendations** — ~4 hours each

### Tier 3 — Transformational, Higher Effort
11. **AMC Optimization Workflows** — ~1 week, true multi-touch ROAS
12. **Negative Keyword Mining Pipeline** — ~1 week, 10-15% efficiency gain
13. **Auto→Manual Keyword Harvesting** — ~1 week, compounds over time
14. **ASIN-Level Optimization** — ~3 days, pause losers/scale winners

---

## 6. Coverage Summary
| Capability | Current | Key Gap |
|-----------|---------|---------|
| SP bid management | ~85% | Missing: create target |
| SP reporting | ~40% | Missing: search term, ACOS/ROAS columns |
| SB bid management | ~30% | Missing: keyword/target write |
| SD bid management | ~40% | Missing: target write, bid recs |
| Budget automation | ~60% | Missing: SB/SD rules, pacing |
| Recommendations | ~70% | Missing: SB/SD target recs |
| Attribution depth | ~35% | Missing: search term, AMC |
| Change tracking | 0% | History API absent |
| Competitive data | 5% | Impression share not collected |

**#1 ROI action:** Enable `spSearchTerm` reports (~10 lines of config) → unlocks negative keyword mining + keyword harvesting = majority of ongoing SP efficiency gains.
