# Advertising-Amazon: Codebase Architecture Analysis
**Generated:** 2026-04-23 | **Phase 1 of Ads Research**

## 1. Guidance File Flow

### Format
Excel workbook authored by the media-buying team, two formats:
- **`.xlsb`** (primary — SharePoint-published binary) parsed via `pyxlsb`
- **`.xlsx`** (browser-upload fallback) parsed via `openpyxl`

Key columns (from `src/api/budget_guidance.py`):
- **F** = portfolio name
- **G** = parent ASIN
- **CJ** = current portfolio daily budget
- **CK** = total recommended weekly budget
- **CL–CR** (7 cols) = per-day suggested budgets (Tue → Mon)
- **CS** = change percentage

Header row is auto-detected by scanning first 10 rows for the label `"Portfolio"` / `"Parent"`.

### Three Ingest Paths
1. **Browser upload** — `POST /bg/upload` (multipart). Writes file to `/app/docs/`, then through parse+save pipeline.
2. **SharePoint auto-download** — `POST /bg/download-guidance` or startup via `SharePointDownloadService`. Uses Microsoft Graph API with client-credentials OAuth; SHA256 hash change-detection prevents redundant parses.
3. **Startup hydration** — `hydrate_guidance_on_startup()` rehydrates `guidance_state` into memory so the compare endpoint is immediately available after restart.

### Processing Pipeline
```
upload/download → parse_guidance_file_(xlsx|xlsb)
               → _match_portfolios (name normalization via bg_name_matching.py)
               → _save_guidance_to_db_v2 (guidance_files + guidance_state tables)
               → hydrate in-memory map
               → available via /bg/compare, /bg/load, budget cron
```

Name normalization (`src/services/bg_name_matching.py`) strips `2026_` / `2026-` year prefixes from Amazon portfolio names before matching.

### Storage
- **Filesystem**: `/app/docs/guidance_latest.xlsb`
- **`guidance_files`** table (migration 006): per-upload history with JSONB `parsed_rows`, SHA256 hash, `is_active` flag. New upload deactivates prior rows.
- **`guidance_state`** table (migration 013): per-`profile_id` upsert holding the currently live guidance.

---

## 2. Budget Update Pipeline

Daily portfolio-budget run, orchestrated at **06:00 America/New_York** by `_budget_cron_loop()` in `src/api/budget_guidance.py`.

### Entry Point
`BudgetUpdateService.run_update_session()` in `src/services/budget_update_service.py` (1648 lines).

### Phases
1. **Refresh guidance** — optional SharePoint download + parse if file changed
2. **`_fetch_portfolios`** — from Amazon (entity cache → ads client)
3. **`_fetch_campaign_rollups`** — cache-first; falls back to `sp_tools` pagination, then `spend_metadata`
4. **`_fetch_previous_day_spend`** — Redis/DB spend cache
5. **`_build_plan`** — join guidance row → portfolio → current budget → target budget
6. **`_enrich_plan_with_spend`** — attach yesterday's spend for the report
7. **Safety gates** (hard-coded, not env-overridable):
   - `_DEFAULT_MAX_PCT = 0.5` — reject single-portfolio change > 50%
   - `_DEFAULT_MAX_TOTAL = $10,000` — require explicit UI confirmation above this total-delta
   - `_DEFAULT_HARD_CAP = $100,000` — absolute circuit-breaker, cannot be overridden
   - `_HARD_CAP_FLOOR = $1,000` — per-portfolio cap floor
8. **Batch write** — `portfolio_tools.portfolio_update_budget_batch` (50 portfolios per `PUT /portfolios` call)
9. **5-second propagation sleep**, then per-portfolio **verify** against Amazon
10. **`_write_report`** — openpyxl BEFORE/AFTER xlsx file
11. **`_send_session_email`** — ACS HTML email with grouped status table
12. **Optional cascade** — if `CAMPAIGN_ALLOCATION_ENABLED`, call `cascade_campaign_budgets` per verified portfolio write
13. **Record session row** in `budget_update_sessions`

### Write Path Detail
`portfolio_tools.portfolio_update_budget_batch`:
- Write-guard check (read-only mode block)
- Ownership check (profile claims portfolio)
- Rate limiter
- `PUT /portfolios` with `{"portfolios": [...]}` payload
- Cache invalidation (`api_entity_cache` + Redis)
- Audit-log entry per portfolio

---

## 3. Data Sources

| Source | What's Pulled |
|--------|--------------|
| **Amazon Ads API v3** | Portfolios, SP/SB/SD campaigns/ad groups/keywords, budget rules, bidding strategy, reports |
| **Amazon SP-API** | Orders flat-file TSV (PII-stripped), FBA inventory, FBA storage fees, FBA shipped-items |
| **Datalake MCP** (port 8002) | Historical order lookups, `dim_sku` for parent-ASIN mapping |
| **Redis** | 30-min TTL spend cache |
| **Postgres** | `spend_report_cache`, `spend_metadata`, `campaign_spend_daily`, `bid_daily` aggregates |
| **SharePoint** | Guidance workbook (xlsb) via Microsoft Graph |

---

## 4. Current Automation Level

### Fully Automatic (wired in `src/server.py` lifespan)

| Loop | Interval | Purpose |
|------|----------|---------|
| `_budget_cron_loop` | Daily 06:00 ET | Portfolio-budget updates |
| `start_ads_sync_loop` | 1800 s (30 min) | Entity cache refresh from Amazon Ads API |
| `start_attributed_orders_loop` | 1800 s | Attributed orders pull |
| `start_report_ingestion_loop` | 1800 s | SP-API orders flat-file |
| `_fba_ingest_then_export_loop` | 7200 s (2 hr) | FBA shipped |
| `_fba_inventory_ingest_loop` | 14400 s (4 hr) | FBA inventory |
| `_fba_storage_ingest_loop` | 86400 s (24 hr) | FBA storage fees |

### Human-in-the-Loop / Manual
- **Read-only mode is the default** on every process start. Requires explicit dashboard toggle.
- **Guidance file upload** — browser upload or user-triggered SharePoint pull.
- **Large-delta confirmation** — any plan total > `$10,000` requires explicit UI confirm.
- **Hard cap** — `$100,000` cannot be bypassed in code.
- **Campaign cascade** — gated behind `CAMPAIGN_ALLOCATION_ENABLED` feature flag.

---

## 5. API Integrations

### Amazon Ads v3 (`src/amazon/ads_client.py`)
- Auth: LWA refresh-token grant → `https://api.amazon.com/auth/o2/token`; tokens Fernet-encrypted in `oauth_tokens` table
- Headers: `Amazon-Advertising-API-ClientId`, `Amazon-Advertising-API-Scope` (profile id)

| Operation | Endpoint |
|-----------|---------|
| List portfolios | `POST /portfolios/list` |
| Update portfolio budgets | `PUT /portfolios` |
| SP campaigns/ad groups/keywords | `GET /sp/campaigns`, `/sp/adGroups`, `/sp/keywords` |
| SB campaigns | `GET /sb/v4/campaigns` |
| SD campaigns | `GET /sd/campaigns` |
| Budget rules | `GET /sp/campaigns/{id}/budgetRules` |
| Bidding strategy | `GET /sp/campaigns/{id}/bidding/strategy` |
| Reports | `POST /reporting/reports` |
| Profiles | `GET /v2/profiles` |

### Amazon SP-API (`src/ingestion/report_ingestion.py`)
- `_create_report` → `_poll_report` (15 s interval, 600 s max) → `_download_report` (gunzip)
- Report types: `GET_FLAT_FILE_ALL_ORDERS_DATA_BY_ORDER_DATE_GENERAL`, FBA inventory/storage/shipped

### Microsoft Graph (`src/services/sharepoint_download.py`)
- Client-credentials flow for SharePoint guidance download
- SHA256 change-detection prevents redundant parses

### Azure Communication Services
- Email transport for budget-update reports and dry-run notifications

---

## 6. Database Schema

Managed by Alembic (`alembic/versions/`).

### Core Tables (001_initial)
- `users`, `oauth_tokens` (Fernet-encrypted), `user_profiles`, `audit_log`, `api_usage`

### Guidance Tables (006, 013)
- **`guidance_files`** — JSONB `parsed_rows`, `parent_to_asins`, SHA256 hash, `is_active`
- **`guidance_state`** — per-`profile_id` upsert of current active guidance

### Session Tables (009)
- **`budget_write_sessions`** — `preview_id`, JSONB `items`, status enum

### Consolidation Tables (023)
- **`api_entity_cache`** — PK `(profile_id, operation, params_hash)`, JSONB `data`, `expires_at` TTL
- **`spend_metadata`** — campaign_parent_lookup + campaign_budgets cache
- **`budget_update_sessions`** — per-run record of the 6 AM cron
- **`budget_guidance_daily`** — flattened daily guidance
- **`spend_daily`**, **`campaign_spend_daily`**, **`bid_daily`** — aggregated spend roll-ups
- **`campaign_allocations`** — per-campaign `alloc_pct` under a parent
- **`campaign_allocation_sessions`**, **`campaign_allocation_history`**
- **`portfolio_budgets`** — snapshot used by cascade writer
- **`campaign_name_matches`** — normalized-name match history
- **`fba_inventory`**, **`fba_storage_fees`**

### Config Tables
- **`app_config`** — generic key/value (settings, report-ingestion run metadata)
- **`spend_report_cache`** — guarded UPSERT (won't overwrite with smaller `row_count`)

---

## 7. Email / Reporting Pipeline

### Daily Budget-Update Email
Sent by `BudgetUpdateService._send_session_email`:
- Transport: Azure Communication Services
- Format: HTML — grouped status table (Written / Verified / Failed / Skipped)
- Attachment: xlsx BEFORE/AFTER report
- Content: summary counts + totals, per-portfolio rows (old budget, new budget, delta, guidance row, yesterday's spend), cascade results if enabled

### Campaign-Allocation Dry-Run Email
- Fire-and-forget background thread
- CSV attachment of planned campaign-budget changes
- Default recipient: `moreta@gorillacommerce.co`
- Gated off in non-prod via `LOKI_ENV`

---

## 8. Entity Cache

Two-Layer Design: DB (`api_entity_cache`) + in-memory `TtlCache`

### Sync Cycle
`src/ingestion/ads_data_sync.py`:
- 30-min loop fetching portfolios + SP/SB/SD campaigns + SP ad-groups in parallel (`asyncio.gather`)
- Startup bootstrap via `hydrate_cache_from_db()`
- Invalidation after writes via `delete_entity_by_operation`

---

## 9. Key Services Map

| File | Lines | Responsibility |
|------|-------|---------------|
| `src/api/budget_guidance.py` | 5627 | All `/bg/*` REST endpoints + cron loop orchestration |
| `src/server.py` | ~6000 | FastMCP app, lifespan, middleware, all background loop startup |
| `src/amazon/ads_client.py` | 1510 | Amazon Ads v3 HTTP client, LWA token management |
| `src/services/budget_update_service.py` | 1648 | Main 6 AM portfolio budget run orchestrator |
| `src/services/campaign_allocation_writer.py` | 754 | Per-campaign cascade budget writer |
| `src/services/entity_cache_service.py` | — | Two-layer entity cache |
| `src/services/spend_cache_service.py` | — | Redis + spend_report_cache |
| `src/services/sharepoint_download.py` | — | Microsoft Graph xlsb fetch |
| `src/services/settings_resolver.py` | — | DB > env > default config |
| `src/services/bg_name_matching.py` | — | Portfolio name normalization |
| `src/services/campaign_allocation_service.py` | — | Campaign-Output sheet parser |
| `src/tools/portfolio_tools.py` | — | Portfolio batch write with safety |
| `src/ingestion/ads_data_sync.py` | — | Entity cache refresh loop |
| `src/ingestion/report_ingestion.py` | — | SP-API flat-file report pipeline |

---

## 10. Configuration

### Resolution Order: DB > env > default
- DB lookup: `SELECT value FROM app_config WHERE key = :key LIMIT 1`
- Falls through to env on DB failure
- Values validated via `SettingDefinition.validation` lambdas

### Safety Constants (intentionally not env-overridable)
- `_DEFAULT_MAX_PCT = 0.5` — max 50% change per portfolio
- `_DEFAULT_MAX_TOTAL = $10,000` — UI confirmation required above
- `_DEFAULT_HARD_CAP = $100,000` — absolute circuit-breaker
- `_HARD_CAP_FLOOR = $1,000` — per-portfolio cap floor

---

## Key Observations for Agentic Evolution

### What's Already Good
1. **Safety gates** are well-designed — hard-coded circuit-breakers, read-only default, ownership checks
2. **Two-layer caching** reduces API calls
3. **Audit logging** exists on every write
4. **Entity cache** provides a solid data foundation

### Current Limitations for Agentic Use
1. **One-shot daily execution** — only runs at 6 AM, no intra-day adjustments
2. **Guidance-driven only** — all budget decisions come from a human-authored Excel file
3. **No performance feedback loop** — system doesn't learn from spend vs revenue outcomes
4. **No bid optimization** — only portfolio/campaign budget levels, not keyword/ad-group bids
5. **No A/B testing** — no framework for testing different strategies
6. **No alerting on anomalies** — no detection of spend spikes, ROAS drops, etc.
7. **Campaign cascade is nascent** — behind a feature flag, allocation percentages are static

### Biggest Gaps for an Agentic System
1. **Decision engine** — needs a layer that interprets performance data and generates budget/bid plans
2. **Feedback loop** — need to track what changes were made → what outcomes occurred → learn
3. **Intra-day responsiveness** — some campaigns may need mid-day budget increases if performing well
4. **Keyword/bid management** — the highest-leverage optimization is at the keyword bid level
5. **Goal framework** — need to define target ACOS/ROAS/revenue goals per portfolio/campaign
