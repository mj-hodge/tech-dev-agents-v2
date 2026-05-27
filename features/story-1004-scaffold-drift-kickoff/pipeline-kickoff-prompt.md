# Pipeline Kickoff — Phase 1 Seed Template

> **Usage:** This template is loaded by the `/pipeline-kickoff` skill when starting a
> new data pipeline story. Fill in each section. Sections marked `[REQUIRED]` must be
> completed before Phase 7 begins.

---

## Pipeline: {{pipeline_name}}

**Source system:** {{source_system}}  
**Initiated:** {{today}}  
**Story:** STORY-{{story_id}}  
**Scope:** small / medium / large _(delete as appropriate)_

---

## 1. Source System [REQUIRED]

Describe the upstream system this pipeline reads from.

| Field | Value |
|-------|-------|
| Source name | {{source_system}} |
| Source type | e.g. ecommerce / ERP / CRM / analytics / custom |
| Connection method | e.g. REST API / SFTP / direct DB / webhook |
| Auth mechanism | e.g. OAuth2, API key, service account |
| Catalog entry | `tech-project-mapping/catalog/data-sources.yaml` — `name: {{source_system}}` |
| Rate limits / quotas | e.g. 1000 req/min, 10k rows/call |
| Data format | e.g. JSON, CSV, Parquet, AVRO |

**Source contact / owner:** _(team or person responsible for upstream system)_

---

## 2. Target Tables [REQUIRED]

List the destination tables or datasets this pipeline writes to.

| Table / Dataset | Schema | Write mode | Notes |
|----------------|--------|-----------|-------|
| `raw.{{source_system}}_orders` | BigQuery / Postgres / etc. | APPEND / UPSERT / OVERWRITE | e.g. partitioned by date |
| `staging.{{source_system}}_orders_clean` | — | OVERWRITE | transform layer |

**Target data warehouse / DB:** _(e.g. BigQuery project `gc-datawarehouse-prod`)_

---

## 3. Schedule [REQUIRED]

Define the execution cadence.

| Field | Value |
|-------|-------|
| Frequency | e.g. hourly / daily at 02:00 UTC / event-driven |
| Cron expression | e.g. `0 2 * * *` |
| Trigger | e.g. time-based / upstream pipeline complete / manual |
| Backfill required? | Yes / No — if yes, date range: |
| Timezone | UTC (preferred) |

---

## 4. Latency [REQUIRED]

Define the acceptable data freshness requirements.

| Field | Value |
|-------|-------|
| SLA | e.g. data available within 2h of source close |
| Max acceptable lag | e.g. 4h before alerting |
| Real-time requirement? | No / Yes — if yes, describe streaming approach |
| Recovery SLA | e.g. catch up within 1 backfill run if pipeline misses a window |

---

## 5. Dependencies

List upstream pipelines, jobs, or data assets this pipeline depends on.

| Dependency | Type | Required before run? | Owner |
|-----------|------|---------------------|-------|
| e.g. `pipeline-auth-tokens-refresh` | upstream pipeline | Yes | data-platform |
| e.g. `raw.product_catalog` | data asset | No (enrichment only) | catalog-team |

**Downstream consumers** (who reads what this pipeline writes):

- e.g. `reporting.daily_revenue_summary` depends on `staging.shopify_orders_clean`
- e.g. BI dashboard "Sales Overview" reads `raw.shopify_orders`

---

## 6. Test Strategy [REQUIRED]

Define the testing approach for Phase 7.

| Test type | What it covers | Priority |
|-----------|---------------|---------|
| Schema contract | Output columns match expected types / nullability | HIGH |
| Row count validation | Non-zero rows written; count within expected range | HIGH |
| Idempotency | Re-running pipeline produces same result (no duplicates) | HIGH |
| Backfill correctness | Historical data loads match source | MEDIUM |
| Latency smoke test | Pipeline completes within SLA window | MEDIUM |
| Failure / retry | Partial failure doesn't corrupt target; retry picks up from checkpoint | HIGH |

**Test data approach:** _(e.g. mock API responses, fixture CSV files, staging environment)_

---

## 7. Acceptance Criteria

_Phase 8 is not complete until all items below pass._

- [ ] AC-1: Pipeline runs end-to-end in staging environment without errors
- [ ] AC-2: Target tables populated with correct row count (±5% of expected)
- [ ] AC-3: Schema matches `target_tables` definition above
- [ ] AC-4: Idempotency test passes (re-run produces same row count, no duplicates)
- [ ] AC-5: Source catalog entry for `{{source_system}}` confirmed registered
- [ ] AC-6: Alerting configured for pipeline failure / SLA breach
- [ ] AC-7: Backfill tested for at least 7 days of historical data
- [ ] AC-8: All Phase 7 tests GREEN

---

## 8. Out of Scope

List items explicitly excluded from this pipeline story to avoid scope creep.

- _(e.g. real-time streaming — covered by separate STORY-N)_
- _(e.g. data quality scoring — add after initial pipeline is stable)_

---

## 9. Open Questions

| Question | Owner | Status |
|----------|-------|--------|
| e.g. Does `{{source_system}}` API support incremental pulls or full snapshot only? | data-platform | Open |
| e.g. What is the retention policy on the target raw table? | data-governance | Open |

---

_Template version: 1.0 — STORY-1004. Update in `.sdlc/templates/pipeline-kickoff-prompt.md`._
