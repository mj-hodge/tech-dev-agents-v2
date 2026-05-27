# PR Tracker — Last Updated: 2026-05-20T12:55Z

Open: 16 | Reviewed: 16/16 (0 unreviewed) | Approved: 3 | Request Changes: 13

## Open PRs — advertising-amazon (9 open)
| PR | Title | Author | Size | Review | Action |
|----|-------|--------|------|--------|--------|
| #721 | STORY-1077: S3 enriched/ → notification_enrichment_log projector | labayatagorillacommerce | Large (+2175) | REQUEST_CHANGES (12:55Z) — M1-M2 | Pool timeout + CHANGELOG mismatch |
| #702 | chore(seeds): 3 follow-up seeds (STORY-1072 impl hidden) | markoreta-gc | Large (+2070) | REQUEST_CHANGES (19:10Z) — R1-R3 | Rewrite desc + batch commits + silent-ok |
| #686 | feat(STORY-1067): DB-backed recipient list + override flag | markoreta-gc | Medium (+258/-9) | REQUEST_CHANGES (05-18 23:10Z) — R1: async_session | Author verify — CONFLICTING |
| #671 | story-1069: move remaining 5 loops to ACA Jobs | agent-dan-gc | Medium (+993) | REQUEST_CHANGES (05-18 21:35Z) — R1: Unit Tests failing | Fix CI — BLOCKED |
| #670 | story-1069: BSR alert + achievability + SP bid cache to ACA Jobs | agent-dan-gc | Medium (+657) | REQUEST_CHANGES (05-18 21:35Z) — R1: Unit Tests failing | Fix CI — BLOCKED |
| #638 | fix(prod): non-blocking lifespan + lenient probes | markoreta-gc | Large (+2117) | REQUEST_CHANGES (05-18 15:10Z) — R1-R6 | Author fix — CONFLICTING |
| #621 | feat(fba): retire v1 FBA export | markoreta-gc | Large (+140/-10353) | REQUEST_CHANGES (05-18 13:10Z) — R1-R4 | Rebase + fix — CONFLICTING |
| #620 | feat(observability): trim silent-suppression | markoreta-gc | Medium (+140/-139) | REQUEST_CHANGES (05-18 13:10Z) — R1-R2 | Lint fix + rebase — CONFLICTING |
| #609 | ci(deploy-prod): bump health-check timeout | markoreta-gc | Small (+2/-2) | APPROVED ✅ | CONFLICTING + Unit Tests FAILING — needs rebase |

## Open PRs — tech-dev-agents (9 open)
| PR | Title | Author | Size | Review | Action |
|----|-------|--------|------|--------|--------|
| #352 | STORY-1002: load-canon skill + phase mods (v2) | agent-dan-gc | Large (+3213) | REQUEST_CHANGES (15:00Z) — R1-R2 | Rebase + CI — CONFLICTING |
| #350 | STORY-1010: ops-skill fallback + PR-link gate (v2) | agent-dan-gc | Medium (+958) | CHANGES_REQUESTED (13:19Z) | Fix required changes — UNSTABLE |
| #349 | STORY-1008: Morris canon-aware mods (v2) | agent-dan-gc | Large (+3227) | REQUEST_CHANGES (15:00Z) — R1-R7 | Split + fix — CONFLICTING |
| #347 | STORY-1007: Morris review-prs + merge v2-PR gates (v2) | agent-dan-gc | Large (+2150) | REQUEST_CHANGES (15:00Z) — R1-R5 | Fix entry points + split — CONFLICTING |
| #333 | STORY-917: needs_info guard + migration 060 | agent-dan-gc | Large (+3619) | CHANGES_REQUESTED — DIRTY | Stale since 05-14 |
| #326 | STORY-902: PR link backfill sweeper | agent-dan-gc | Small (+147) | CHANGES_REQUESTED — UNKNOWN | Stale since 05-08 |
| #321 | STORY-903: GitHub REST fallback | agent-dan-gc | Large (+6021) | CHANGES_REQUESTED — UNKNOWN | Stale since 05-06 |
| #316 | STORY-874: Queue Stability Sweeper | agent-dan-gc | Large (+2558) | CHANGES_REQUESTED — UNKNOWN | Stale since 05-05 |
| #303 | STORY-871: Dashboard History tab v2 | agent-dan-gc | Large (+3597) | CHANGES_REQUESTED — UNKNOWN | Stale since 05-04 |

**Cross-story scope mixing (systemic):** PRs #349, #347, and #352 all contain STORY-1012 work leaked across PR boundaries.

**Supersession chain:**
- #339 (STORY-1002 v1) → CLOSED, superseded by #352 (v2)
- #342 (STORY-1008 v1) → CLOSED, superseded by #349 (v2)
- #340 (STORY-1007 v1) → CLOSED, superseded by #347 (v2)
- #344 (STORY-1012 v1) → CLOSED, superseded by #351 (v2)
- #351 (STORY-1012 v2) → CLOSED (17:15Z) — empty diff, all changes already on main
- #341 (STORY-1003 v1) → CLOSED, #353 merged (v2)
- #345 (STORY-1019) → CLOSED, #346 merged
- #337 (STORY-1001 v1) → CLOSED, #348 merged (v2)
- #343 (STORY-1010 v1) → CLOSED, superseded by #350 (v2)

## Open PRs — gc-infra (2 open)
| PR | Title | Author | CI | Review | Action |
|----|-------|--------|-----|--------|--------|
| #99 | fix(polaris-rbac): drop CATALOG_LIST_NAMESPACES | roblescandres | No checks | CLEAN/MERGEABLE — no formal approval | Infra change — needs Mark's approval |
| #2 | Iceberg substrate stand-up | roblescandres | UNSTABLE | CHANGES_REQUESTED | Large infra PR — needs Mark's review |

## Open PRs — tech-gc-knowledgebase (1 open)
| PR | Title | Author | Size | Review | Action |
|----|-------|--------|------|--------|--------|
| #73 | STORY-1015: cross-repo index (full) | agent-dan-gc | Medium (+634) | APPROVED ✅ — DIRTY/CONFLICTING | Needs rebase |

## Open PRs — api-retail-target (1 open)
| PR | Title | Author | Size | Review | Action |
|----|-------|--------|------|--------|--------|
| #39 | STORY-008b: Catalog Client Staging Parity | jphillips-gc | Large (+6299/-149, 36 files) | REQUEST_CHANGES (23:42Z) — R1: report_bytes JSON serialization | CONFLICTING after #38 merge — needs rebase |

## Open PRs — gc-data-v2 (1 open)
| PR | Title | Author | Size | Review | Action |
|----|-------|--------|------|--------|--------|
| #59 | feat(scaffold): snapshot-replace materializer (Stub → Proven) | jduarte-buildstr | Medium (+456/−104) | REQUEST_CHANGES (12:55Z) — H1, M1-M2 | No tests (blocking) + EntityConfig.get() duck-typing |

## Other repos (0 open)
fabric-keepa: No open PRs
sourcing-warning-labels: No open PRs
tech-datawarehouse: No open PRs
tech-project-mapping: No open PRs
product-health-dashboard: No open PRs (#45 MERGED ✅ 05-19)

## Recently Merged (2026-05-20)
| PR | Repo | Merged | Title | By |
|----|------|--------|-------|-----|
| #38 | api-retail-target | 12:31Z | STORY-019b: Cancellations Client Staging Parity | jphillips-gc |
| #58 | gc-data-v2 | 12:52Z | docs(canon): add Pattern 6 — external-landed (v1.3.0) | Morris (auto) |

## Previously Merged (2026-05-19)
| PR | Repo | Merged | Title | By |
|----|------|--------|-------|-----|
| #713 | advertising-amazon | 21:33Z | fix(s3-projector): wrap blocking boto3 calls in asyncio.to_thread | Mark |
| #711 | advertising-amazon | 20:58Z | hotfix(STORY-1079): update critical-feature contract tests | Mark |
| #710 | advertising-amazon | 21:17Z | fix(ooda): score_snapshot + decide_bids thread pool | Mark (Morris approved) |
| #709 | advertising-amazon | 20:11Z | hotfix: handle SQLAlchemy RowMapping | Mark |
| #708 | advertising-amazon | 20:09Z | DRY-A step 7/7: migrate _send_session_email | Mark |
| #707 | advertising-amazon | 20:08Z | DRY-A step 5/7: migrate competitor_monitor_email | Mark |
| #706 | advertising-amazon | 20:19Z | fix(STORY-1079): spend cache freshness guard | Mark (Morris re-reviewed) |
| #705 | advertising-amazon | 20:08Z | DRY-A step 6/7: migrate bsr_alert_email | Mark |
| #704 | advertising-amazon | 20:08Z | DRY-A step 3/7: migrate campaign_alloc_email | Mark |
| #703 | advertising-amazon | 20:08Z | DRY-A step 4/7: migrate dry_run_email | Mark |
| #45 | product-health-dashboard | ~17:15Z | fix(auth): permanent Entra JWT audience fix | Mark |
| #35 | api-retail-target | 18:46Z | STORY-014c Phase 11 — staging guard hardening | Morris |
| #31 | api-nimbleway | 17:15Z | chore(pc-010): post-epic quality gates | Morris |
| #353 | tech-dev-agents | 13:32Z | STORY-1003: canon-backport + phase-9 + retro | Merged |

## Actions This Cycle (2026-05-20T12:55Z)
- ✅ **Re-reviewed PR #721** (advertising-amazon) — force-pushed (ddbd2e42). Full re-review: found MEDIUM-1 (pool timeout 10s→2s for Lambda budget) + MEDIUM-2 (CHANGELOG flag-gated claim vs unconditional code). Infra/tests solid. Posted REQUEST_CHANGES.
- ✅ **First review PR #59** (gc-data-v2) — snapshot-replace materializer Stub→Proven (+456/-104). Found HIGH-1 (no unit tests for 456-line impl), MEDIUM-1 (EntityConfig.get() duck-typing), MEDIUM-2 (identity_columns + _effective_from uniqueness edge case). Posted REQUEST_CHANGES.
- ℹ️ PR #38 (api-retail-target) merged by jphillips-gc at 12:31Z
- ℹ️ PR #39 (api-retail-target) now CONFLICTING after #38 merge — needs rebase
- ℹ️ No new commits on CHANGES_REQUESTED PRs in tech-dev-agents or remaining advertising-amazon PRs
- ℹ️ No new commits on CHANGES_REQUESTED PRs in advertising-amazon (#702, #686, #638, #621, #620) — no re-reviews needed
- ℹ️ advertising-amazon #609 (APPROVED) remains CONFLICTING + Unit Tests failing — needs rebase by Mark
- ℹ️ 5 stale CHANGES_REQUESTED PRs in tech-dev-agents (#303, #316, #321, #326, #333) — aging 5-16 days
- ℹ️ Main branch CI healthy on advertising-amazon (Deploy Prod + UAT green at 21:44Z)
