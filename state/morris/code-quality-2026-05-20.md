# Weekly Code Quality Report — 2026-05-20

**Audited by:** Morris (Claude Code subprocesses)
**Repos scanned:** 8
**Total codebase:** ~671,274 code lines across 18,525 files

## LOC Summary

| Repo | Files | Code Lines | Comment Lines | Top 3 Languages |
|------|------:|----------:|--------------:|-----------------|
| advertising-amazon | 13,732 | 361,883 | 736,572 | Python (307K), TSX (17K), YAML (11K) |
| tech-dev-agents | 1,918 | 159,656 | 111,592 | Python (84K), Diff (58K), Bash (6K) |
| product-health-dashboard | 1,157 | 62,021 | 82,653 | Python (37K), TSX (9K), Diff (6K) |
| tech-datawarehouse | 689 | 34,828 | 49,043 | Python (21K), YAML (6K), TypeScript (2K) |
| fabric-keepa | 356 | 28,218 | 16,875 | Python (15K), JSON (13K), YAML (475) |
| tech-project-mapping | 157 | 12,604 | 6,825 | HTML (4K), Bash (3K), YAML (2K) |
| sourcing-warning-labels | 97 | 9,870 | 4,765 | Python (3K), JavaScript (2K), JSON (1K) |
| tech-gc-knowledgebase | 419 | 2,194 | 38,136 | Python (826), YAML (809), Terraform (341) |

## Cross-Repo Findings Summary

### CRITICAL — Silent Exception Swallowing (All 8 Repos)

Every single repo has `except Exception: pass` or `except Exception: return {}` patterns that silently discard errors. This is the #1 systemic issue — it masks production failures and makes debugging near-impossible.

| Repo | Worst Offender | Impact |
|------|---------------|--------|
| advertising-amazon | `src/cache/ttl_cache.py:189` — Redis failures silently swallowed | Cache misses indistinguishable from errors |
| product-health-dashboard | `app/services/dashboard.py:201,664,702,778` — 4x identical `except: pass` on rollback | Session state corruption masked |
| tech-dev-agents | `deployment/hermes/dispatch_poller.py:1234,1559,1653` — rate-limit detection swallowed | Poller keeps slamming Claude Code while rate-limited |
| tech-datawarehouse | `src/core/loki.py:134` — Loki POST silently dropped | Observability data silently lost |
| tech-gc-knowledgebase | `features/story-331/.../ingest.py:337,427,473` — broad except catches everything | Programming bugs treated same as network errors |
| sourcing-warning-labels | `app/main.py:246` — PROIDI XML load fails silently | Lookups return empty with no indication |
| fabric-keepa | `src/core/loki.py:210` — identical silent Loki flush | Same pattern as tech-datawarehouse |
| tech-project-mapping | `serve.py:86` — bare Exception in tuple catches all errors | Programmer errors masked as "service down" |

### CRITICAL — Loki Client Duplication with Silent-Flush Bug (4 Repos)

The same `loki.py` with silent `_flush()` exception swallowing exists independently in 4 repos:
- `advertising-amazon/src/core/loki.py` (implied from tooling)
- `tech-datawarehouse/src/core/loki.py:134`
- `fabric-keepa/src/core/loki.py:210`
- `sourcing-warning-labels/app/loki.py:112`

All drop log batches silently on transport failure. Should be a shared library with proper retry/fallback.

### HIGH — Missing HTTP Timeouts on External Calls (5 Repos)

| Repo | Location | Risk |
|------|----------|------|
| tech-dev-agents | `monday.py:227` — no timeout on Monday.com API | Hangs freeze dispatch state transitions |
| tech-dev-agents | `dispatch_poller.py:329` — no timeout on subprocess | Single `ps` hang stalls entire poller |
| tech-datawarehouse | `src/auth/oauth_provider.py:354,626,876` — no timeout on Azure AD | OAuth callback hangs forever |
| product-health-dashboard | `app/connectors/rest_api.py:96` — no timeout, no retry | External connector hangs indefinitely |
| fabric-keepa | `src/pipelines/item_master_load.py:142` — Monday.com fetch no retry | 5xx silently zeros lead-ASIN feed |

### HIGH — Dependency Health Crisis (All 8 Repos)

| Repo | Manifest | Pinning Strategy | Lock File | Severity |
|------|----------|-----------------|-----------|----------|
| advertising-amazon | pyproject.toml | 27/29 deps unpinned (>=X.Y only) | None | HIGH |
| product-health-dashboard | pyproject.toml | Mixed; tenacity declared but unused | uv.lock | MEDIUM |
| tech-dev-agents | requirements.txt | All 9 pkgs floor-pin only; `requests` undeclared | None | HIGH |
| tech-datawarehouse | pyproject.toml | All major libs >= only; cryptography in dev group | uv.lock | MEDIUM |
| tech-gc-knowledgebase | None | No manifest at all | None | HIGH |
| sourcing-warning-labels | requirements.txt | Pins to non-existent versions (pandas 3.0.1) | None | CRITICAL |
| fabric-keepa | pyproject.toml | 4 unbounded deps; keepa properly pinned | uv.lock | LOW |
| tech-project-mapping | None | No manifest at all | None | HIGH |

### HIGH — Test Coverage Gaps (All 8 Repos)

Critical untested modules across repos:
- **tech-dev-agents:** v2_orchestrator.py (THE orchestration entrypoint), phase_defs.py (prompt templates), adversarial_review_parser.py (quality gates) — all zero tests
- **advertising-amazon:** system_health_digest.py, cockpit_aggregate.py, bsr_projection_service.py — customer-facing aggregators untested
- **product-health-dashboard:** endpoints.py (13KB connector API), weight_service.py (8.9KB), flat_file/_base.py (path traversal/SHA dedup)
- **tech-datawarehouse:** FabricSqlAdapter, BigQueryAdapter, SharePointAdapter — three adapters with zero tests
- **sourcing-warning-labels:** lookups.py (320 LOC Excel parsing), loki.py, all JS (3.7K LOC) — zero JS tests
- **tech-gc-knowledgebase:** Both ingest.py scripts — zero tests on classification logic
- **tech-project-mapping:** serve.py failure paths, parse-claude-export.py pure functions

### MEDIUM — Code Duplication Hotspots

| Repo | Pattern | Scale |
|------|---------|-------|
| advertising-amazon | `_get_user_id()` copy-pasted into 14 tool modules; `_build_headers()` 8x with drift | ~400 LOC |
| tech-dev-agents | 6 orphan scratch files (3,740 LOC literal dupe); TeamsAdapter 3x; load_config 3x | ~5,000 LOC |
| product-health-dashboard | Dashboard scoring helpers duplicated across L2/L3/main services | ~150 LOC |
| sourcing-warning-labels | `diffWordHtmlSides` + date helpers triplicated across 4 JS files | ~600 LOC |
| fabric-keepa | Retry/backoff loops 3x in keepa client; `load_settings()` 3x across pipelines | ~150 LOC |
| tech-datawarehouse | httpx.AsyncClient blocks 3x; parameter-placeholder converters 2x | ~100 LOC |

### MEDIUM — Hardcoded Production URLs as Defaults (6 Repos)

| Repo | Value | Location |
|------|-------|----------|
| advertising-amazon | `http://localhost:8001/8002` as prod defaults | `src/config/__init__.py:102,118` |
| product-health-dashboard | MCP prod URLs as fallback defaults | `app/connectors/endpoints.py:56-57` |
| tech-dev-agents | `_GH_ORG = "hpi-gorillacommerce"` in ~15 files | Multiple locations |
| tech-datawarehouse | Grafana fallback URL hardcoded | `src/teams_relay.py:47` |
| sourcing-warning-labels | CORS origin + Grafana dashboard URLs | `app/main.py:40,115-116` |
| tech-project-mapping | 6 service URLs hardcoded despite YAML source existing | `serve.py:41-48` |

### LOW — Missing Type Hints

Most repos have significant type hint gaps on public functions. Worst offenders:
- **tech-dev-agents:** dispatch.py has 22/22 public functions un-hinted (1,970-line core dispatch API)
- **advertising-amazon:** MCP tool helpers (`_get_db_factory`, `_to_float`) across tools/ directory
- **product-health-dashboard:** Generally well-typed; only ~20 gaps
- **sourcing-warning-labels:** Most FastAPI handlers lack return types

## Per-Repo Priority Actions

### advertising-amazon (361K LOC)
1. **[CRITICAL]** Extract `_tool_base.py` with shared auth/credential/header helpers — kills duplication across 14+ tool modules
2. **[HIGH]** Add dependency lockfile (uv.lock) — 27/29 deps floating
3. **[HIGH]** Add tests for system_health_digest.py, cockpit_aggregate.py

### product-health-dashboard (62K LOC)
1. **[HIGH]** Wire tenacity into rest_api.py/mcp_connector.py — declared but unused
2. **[HIGH]** Extract shared dashboard scoring helpers from L2/L3/main services
3. **[MEDIUM]** Fix duplicate dep declarations (httpx, pyyaml in both main and dev)

### tech-dev-agents (160K LOC)
1. **[CRITICAL]** Add `timeout=` to monday.py:227 and dispatch_poller.py:329 — prevents fleet stalls
2. **[HIGH]** Delete 6 orphan scratch files at repo root — 4,228 lines of dead code
3. **[HIGH]** Pin cryptography and PyJWT with upper bounds — auth-critical packages

### tech-datawarehouse (35K LOC)
1. **[HIGH]** Add timeouts to httpx.AsyncClient in oauth_provider.py — 3 callsites can hang forever
2. **[HIGH]** Move cryptography from dev to main deps
3. **[MEDIUM]** Add adapter integration tests for Fabric/BigQuery/SharePoint

### tech-gc-knowledgebase (2K LOC)
1. **[HIGH]** Fix silent ImportError fallbacks in ingest.py — missing deps corrupt extracted text
2. **[MEDIUM]** Consider moving Python scripts to scratch/ or tooling repo per CLAUDE.md policy
3. **[LOW]** Add dependency manifest if Python stays

### sourcing-warning-labels (10K LOC)
1. **[CRITICAL]** Fix requirements.txt — pins to non-existent package versions (pandas 3.0.1, uvicorn 0.42.0)
2. **[HIGH]** Extract public/shared.js — removes ~600 LOC duplication, centralizes June date fix
3. **[MEDIUM]** Add test coverage for lookups.py (320 LOC Excel parsing, zero tests)

### fabric-keepa (28K LOC)
1. **[HIGH]** Extract keepa-client retry helper — eliminates 3x duplication of retry/backoff loops
2. **[MEDIUM]** Fix Loki _flush() silent drops — add fallback logging
3. **[LOW]** Add upper caps to 4 unbounded deps (pandas, sqlalchemy, requests, psycopg2)

### tech-project-mapping (13K LOC)
1. **[HIGH]** Add requirements-dev.txt with pytest + PyYAML pins
2. **[MEDIUM]** Load RELIABILITY_SERVICES from catalog/projects.yaml instead of hardcoding
3. **[LOW]** Move test fixtures to conftest.py, delete dead _top_project function

## Org-Wide Recommendations

1. **Ban bare `except Exception: pass`** — Add ruff rule `BLE001` (blind-except) to all repos. Every repo has this anti-pattern. Replace with specific exception types + logging.

2. **Extract shared Loki client library** — 4 repos have independent loki.py with identical silent-flush bugs. Create a shared package with proper retry, backoff, and fallback logging.

3. **Standardize dependency management** — Adopt `pyproject.toml` + `uv.lock` everywhere. 3 repos have no manifest at all; 2 use requirements.txt with floor-pins only. sourcing-warning-labels pins to non-existent versions.

4. **Add HTTP timeout policy** — Every outbound HTTP call must have an explicit timeout. 5 repos have timeout-less calls on critical paths (Monday.com, Azure AD, external APIs). A single hung connection can freeze entire services.

5. **Mandate test coverage for new services** — Multiple repos have critical service modules (orchestrators, aggregators, adapters) with zero tests. Require test file creation as part of the SDLC Phase 7 gate.

6. **Centralize GH org and environment constants** — `_GH_ORG = "hpi-gorillacommerce"` appears in ~15 files in tech-dev-agents alone. Production URLs as defaults appear in 6 repos. Move to shared config.

---

*Report generated 2026-05-20 05:00 ET by Morris weekly code quality cron*
