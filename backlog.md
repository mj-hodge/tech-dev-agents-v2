# Backlog

## Epic: Autonomous Dev Agent (v1)

| ID | Story | Scope | Delivery Phase | Status | Assignee | Branch |
|----|-------|-------|---------------|--------|----------|--------|
| STORY-001 | Container Runtime & Identity | Medium | P1: Foundation | Done (15/15 GREEN) | — | — |
| STORY-002 | Teams Bot Foundation | Medium | P2: Core | Done (6/6 GREEN) | — | — |
| STORY-003 | Claude Code Runner | Medium | P2: Core | Done (12/12 GREEN) | — | — |
| STORY-004 | Monday.com Integration | Small | P1: Foundation | Done (26/26 GREEN) | — | — |
| STORY-005 | Persona System | Medium | P1: Foundation | Done (14/14 GREEN) | — | — |
| STORY-006 | SDLC Execution Engine | Large | P3: Integration | Done (26/26 GREEN) | — | — |
| STORY-007 | Approval Flow | Medium | P4: End-to-End | Done (12/12 GREEN) | — | — |
| STORY-008 | Git Workflow | Small | P3: Integration | Done (15/15 GREEN) | — | — |
| STORY-009 | Teams Least-Privilege Graph Permissions | Small | P2: Core | Done (11/11 GREEN) | — | — |
| STORY-010 | Teams Webhook Auth Hardening & Replay Protection | Medium | P2: Core | Done (16/16 GREEN) | — | — |
| STORY-011 | Runtime Secret Hygiene & Config Exposure Audit | Small | P1: Foundation | Done (19/19 GREEN) | — | — |

## Epic: Agent Platform (v2)

| ID | Story | Scope | Status | Assignee |
|----|-------|-------|--------|----------|
| STORY-012 | Agent Cost & Usage Dashboard | Medium | Phase 8b Complete (21/21 GREEN) | — |
| STORY-013 | Agent Monday.com Integration | Medium | Ready | — |
| STORY-014 | Agent Management Dashboard | Medium | Ready | — |

| STORY-016 | Agent Operations Console + MCP + Teams Bridge | Large | Done (430 tests GREEN, v2 complete: SC-1 through SC-13) | — |
| STORY-018 | Ops Console Dedicated VM | Small | Done | Dan |
| STORY-019 | Ops Console Dashboard Frontend | Medium | Done (59/59 GREEN, merged) | Derrick |
| STORY-022 | Ops Dashboard Data Pipeline Fix | Medium | Done (39/39 GREEN) | Derrick |
| STORY-023 | Ops Dashboard Entra ID SSO | Small | Seed Complete | Derrick |
| STORY-426 | Real-Time Agent Presence on Dashboard | Medium | Phase 4 Complete | — |
| STORY-507 | Resume-Aware Phase Runner + Dispatch Observability | Large | Phase 1 Complete (seed) — dispatched priority-top | queue |
| STORY-508 | Morris Adaptation for 507 Observability (alert-handler, remove band-aids, priority API, audit guards) | Medium | Phase 1 Complete (seed) — BLOCKED by 507 | queue (hold) |
| STORY-510 | Harden /api/agents/{name}/quota endpoint (ccusage fallback + weekly trend) | Medium | Done — PR #73 merged | — |
| STORY-511 | Per-phase SDK session redesign — close the 4x agent/human speed gap | Large | Phase 1 Complete (seed) — HOLD: do not dispatch until Dan/Derrick unrated (2026-04-23T19:00Z) | HOLD — token-heavy |
| STORY-512 | push-code.sh safety: don't restart pollers mid-SDK | Small | Phase 1 Complete (seed) — dispatched | queue |
| STORY-513 | Quota endpoint data layer: install ccusage on ops-console | Small | Phase 7 Complete (test-design.md + 15 RED tests) — ready for Phase 8 | queue |
| STORY-523 | Dispatch enqueue: reject prompts whose STORY-N disagrees with story_id (prevents phase-1 SDK waste — STORY-518/520 on 2026-04-22) | Small | Phase 8 Complete — 9/9 tests GREEN, PR open | Devon |
| STORY-531 | Composite (story_id, repo) unique key on dispatch_items | Medium | Phase 8 Complete — 394 tests GREEN, PR pending | Devon |
| STORY-543 | Loki-derived P90 token quota baseline — replace hardcoded 200K ceiling | Small | Phase 1 Complete (seed) — in queue | queue |
| STORY-549 | Unified Agent Presence — Dashboard ↔ Teams (kill event-only push, add 60s fleet→Graph sync) | Medium | Phase 1 Complete (seed) — queue | queue |
| STORY-628 | Presence gather partial-failure resilience (rework STORY-549 PR #99) + ops runbook Presence.ReadWrite.All | Small | Phase 8 Complete — 18/18 tests GREEN, PR pending | daisy |
| STORY-639 | Cancel endpoint accepts claimed/in_review/needs_info/paused source states | Small | Phase 8 Complete — 13/13 tests GREEN, PR #139 open | Devon |
| STORY-550 | Retro-scope dispatch support — allow /retro skill to be enqueued via the dispatch queue | Small | Phase 1 Complete (seed) — queue | queue |
| STORY-100 | Dispatch Queue Smoke Tests — 8 @pytest.mark.smoke tests for dispatch queue API | Small | Phase 8 Complete — 8/8 GREEN, PR #260 merged | Hermes |
| STORY-556 | Fleet Reliability Test Backfill — regression tests for 6 fleet bug patterns | Small | Phase 8 Complete — 40/40 GREEN | Hermes |

Seeds: `features/agent-cost-dashboard/seed.md`, `features/agent-monday-integration/seed.md`, `features/agent-management-dashboard/seed.md`, `features/story-015-deploy-agent-dashboards/seed.md`, `features/story-016-agent-ops-console/seed.md`, `features/story-018-ops-console-vm/seed.md`
Monday board: https://gorillacommerce.monday.com/boards/18405631030

## Collaborative Work with Mark — work-with-me, no urgency

> Items here are NOT for autonomous dispatch. Morris and Mark work through them together as time allows. Get to them when there's a quiet moment, ask Mark before starting any of them.

| ID | Story | Repo | Status | Notes |
|----|-------|------|--------|-------|
| STORY-306 | First Cole curation run | `tech-gc-knowledgebase` | Pending — runs immediately once 303-305 are done | Cole's first sweep. Mark answers initial question batch (in Teams). Establishes the cadence. This is the only truly collaborative item — 303/304/305 are normal dispatch work. |

## Cole-the-Curator Setup (dispatchable dev work)

> These are normal stories that agents pick up from the queue. They're prep work to enable the Cole curator. STORY-306 (first run) happens after these complete and is the only Mark+Morris working session.

| ID | Story | Repo | Status | Depends on |
|----|-------|------|--------|-----------|
| STORY-303 | Migrate tech-gc-knowledgebase to scratch/wiki/sources schema | `tech-gc-knowledgebase` | Seed Complete | none |
| STORY-304 | Add Cole the Curator skill to Morris's persona | `tech-dev-agents` | Seed Complete | none (independent of 303) |
| STORY-305 | Curator Teams Q&A delivery + weekly Sunday cron | `tech-dev-agents` | Seed Complete | STORY-304 (needs the skill defined) |

## Active Stories

| ID | Story | Status | Assignee | Branch |
|----|-------|--------|----------|--------|
| STORY-803 | Fix needs_info loop: override bypass + git-add bug + Phase-8-no-commits self-pause | Phase 8 Complete — PR #257 merged 2026-05-11 | devon | main |
| STORY-772 | Phase Router Must Respect Seed's Declared Phase Path | Phase 8 Complete — 20/20 tests GREEN. _extract_phase_path (list[str|int], preserves 6b/8b), dispatch_poller.parse_seed_phase_path (file-path variant, STORY-725), AC-8 log format. PR pending. | devon | main |
| STORY-767 | Fleet-Vigilance Blind-Spot Closures (VM Reachability, Stuck Deploy, Code-Drift, NULL failure_reason, Zombie Heartbeat, No-Seed Dispatch) | Phase 7 Complete — test-design.md + 15 RED tests (Checks 9–14). STORY-766 not yet shipped → numbering offset documented. Ready for Phase 8. | devon | story-767-fleet-vigilance-blind-spots |
| STORY-763 | Phase-Progress Watchdog — kill zombie heartbeats when SDK dies silently | Phase 8 Complete — 12/12 tests GREEN. _heartbeat_thread enhanced with watchdog (PID liveness + progress stall). _run_phase_sdk Popen path updates last_output_ts[0] per stdout line. PR pending. | devon | story-763-phase-progress-watchdog |
| STORY-759 | Default-branch detection in `_ensure_branch` — fix hardcoded "main" that breaks master-default repos (api-retail-target) | Phase 7 Complete — test-design.md + 10 tests (9 RED, 1 regression guard). Ready for Phase 8. | queue | story-759-dispatch-poller-branch-sync |
| STORY-885 | Review Context Bundler — daily KB digest bundle for Morris PR reviews via `--append-system-prompt-file` | Phase 7 Complete — test-design.md + 4/4 tests GREEN (smoke, header format, budget, idempotency). PR #309 rework in progress. | Derrick | story-885/review-context-bundler |
| EPIC-Queue-v2 | Atomic claim-next + lease + event log + bidirectional v1↔v2 mirrors + Morris cron migration | **DEPLOYED 2026-05-03** — fleet on DISPATCH_PROTOCOL=v2, 4 agents processing. See features/epic-queue-v2/cutover-postmortem-2026-05-03.md. | — | — |
| STORY-860 | Restore Poller-Side Orchestration in dispatch_poller_v2.py — branch lifecycle, phase events, resume-from-phase-N, rework threading, SIGTERM contract | Phase 8 Complete — 14/14 GREEN, all CI PASS, PR #299 open. Feature-flagged DISPATCH_V2_ORCHESTRATION=1. Canary STORY-839/844/845/847/854 post-merge. | — | story-860/restore-poller-orchestration |
| EPIC-Dashboard-Overwatch | Dashboard rework — 3-zone layout (Status Strip / Action Queue / Queue at a Glance) + Foundry CostMonitorPanel | Phase 1 Complete (epic seed) — STORY-803..STORY-808 HOLD until v2 soaks 7 days | — | — |
| STORY-803 | Status Strip — replace FleetOverviewBar + BudgetGauge + AlertStatusPanel with 5-tile compact strip | HOLD — wait for v2 soak | — | — |
| STORY-804 | Action Queue v1 — needs_info + PR merge + knowledge digest + passive alerts | HOLD — wait for v2 soak | — | — |
| STORY-805 | Foundry CostMonitorPanel — projected EOD, per-model alert (Opus >50%), 7d trend | HOLD — wait for v2 soak | — | — |
| STORY-806 | Queue at a Glance — lane columns above the fold | HOLD — wait for v2 soak | — | — |
| STORY-807 | Action Queue v2 — cluster resolve, rule approval, stuck-story decision, cost anomaly | HOLD — gated on Q1/Q3/Q7/Q8/Q9 | — | — |
| STORY-808 | Cost panel v2 — per-failure-class breakdown + one-click pause-class | HOLD — gated on Q3 | — | — |
| STORY-1004 | scaffold-drift-check + pipeline-kickoff skills (Wave 2) — pre-Phase-6 scaffold gate + data pipeline source validation | Phase 8 Complete — 16/16 tests GREEN, PR open | Devon | story-1004/scaffold-drift-kickoff |
| STORY-902 | PR Link Backfill Sweeper — 5-min loop backfills dispatch_jobs.pr_number on in_review rows via GitHub REST API branch lookup | Phase 8 Complete — 8/8 GREEN | Devon | story-902/pr-link-backfill |
| STORY-871 | Dashboard History tab reads from v2 terminal lane — new GET /api/dispatch/v2/history endpoint + frontend hook URL swap. Resolves QV2-FU-1. EPIC-Queue-v2.1. | Phase 1 Complete (Seed) — gate, awaiting approval | Dan (suggested) | — |
| STORY-874 | Queue Stability Sweeper — authenticated manager-only endpoint: rejects stale in_review rows missing pr_number back to work_queue; triages attention_queue by failure_class; dry-run mode; audit log; 3 metrics counters. Medium scope. Path: 1 → 4 → 6 → 7 → 8 → Done. | Phase 4 Complete (Analysis) | Morris | story-874/queue-stability-sweeper |
| STORY-886 | Outcome Watcher — cron service that classifies `pending` findings in findings-ledger.jsonl as validated/false_positive/false_negative/unresolved. Watchlists rebuilt per author after each run. Medium scope. Path: 1 → 6 → 7 → 8 → Done. | Phase 8 Complete — 33/33 GREEN, cron registration pending | Morris | story-886/outcome-watcher |
| STORY-874 | Queue Stability Sweeper — authenticated manager-only endpoint: rejects stale in_review rows missing pr_number back to work_queue; triages attention_queue by failure_class; dry-run mode; audit log; 3 metrics counters. Medium scope. Path: 1 → 4 → 6 → 7 → 8 → Done. | Phase 8 complete — 27/27 tests GREEN. PR #316 open, awaiting merge. | Morris | story-874/queue-stability-sweeper |
| STORY-902 | PR Link Backfill Sweeper — 5-min background task that backfills `dispatch_jobs.pr_number` on `in_review` rows where it's NULL. Infers branch from story_id (STORY-N → story-N/), queries GitHub REST API, updates pr_number. Pairs with STORY-901 merge sweeper. Small scope. Path: 1 → 7 → 8 → Done. | Phase 8 Complete — 13/13 tests GREEN. PR open. | Morris | story-902-pr-link-backfill |
| STORY-885 | review-context-bundler — skill + Python script that builds ~/state/morris/review-context.md (≤120K chars) from wiki digest, SDLC matrix, runbooks, incidents, knowledge gaps. Loaded by review-prs via --append-system-prompt-file. Daily cron 06:00 UTC. Small scope. Path: 1 → 7 → 8 → Done. | Phase 8 Complete — PR #309 open | Morris | story-885/review-context-bundler |
| STORY-886 | Outcome Watcher — cron service that classifies `pending` findings in findings-ledger.jsonl as validated/false_positive/false_negative/unresolved. Watchlists rebuilt per author after each run. Medium scope. Path: 1 → 6 → 7 → 8 → Done. | Phase 8 Complete — 33/33 GREEN, cron registration pending | Morris | story-886/outcome-watcher |
| STORY-887 | Curator KB-Gap Backflow — wire Cole's curator skill to read knowledge-gaps.jsonl and turn recurring KB-GAP clusters (≥threshold occurrences) into wiki page proposals; mark gaps resolved after wiki PR merges. Small scope. Path: 1 → 6 → 7 → 8 → Done. | Phase 8 Complete — 50/50 tests GREEN | Morris | story-887-curator-gap-backflow |
| STORY-897 | Queue Stability Sweeper v2 — PR-state-based orphan cancellation (gh pr view for merged/closed PRs), phantom-repo sanity check, audit log queue-sweeps.jsonl, ops_console manual-trigger route, ID-reuse gate on dispatch_v2 enqueue (DISPATCH_V2_ID_REUSE_GATE flag), */15 cron on Morris VM. Medium scope. Path: 1 → 4 → 6 → 7 → 8 → Done. | Phase 1 Complete (Seed) | Morris | story-897-queue-stability-sweeper-v2 |
| STORY-903 | Deterministic PR Detection in Dispatch Poller v2 — replace fragile `PR #N` regex with regex→gh REST fallback chain; adds `_lookup_pr_by_branch` using `urllib.request` + GITHUB_TOKEN to query `GET /repos/.../pulls?head={org}:{branch}`. Small scope. Path: 1 → 7 → 8 → Done. | Phase 8 Complete — 21/21 tests GREEN, PR pending | Morris | story-903/poller-pr-detection |
| STORY-914 | In-Review PR-Link DB Invariant — migration 057 emits requeued events for existing violators, then installs BEFORE INSERT/UPDATE trigger on dispatch_state_current that rejects state='in_review' when dispatch_jobs.pr_number IS NULL. Defense-in-depth behind the service-layer guard. Medium scope. Path: 1 → 7 → 8 → Done. | Ready | — | story-914-in-review-pr-link-db-invariant |
| STORY-915 | Promtail Onboarding for Manager Fleet — add Promtail scrape configs for hermes (Dan/Derrick/Daisy/Devon dispatch-poller), ops_console, and morris-orchestrator so Loki receives logs from the agent fleet's manager layer. Closes the long-standing `promtail_critical` gap. Medium scope. Path: 1 → 7 → 8 → Done. | **Phase 8 Complete — PR #329 open. 69/69 lint GREEN. Daisy (configs + deploy script) + Morris (test-design.md + lint suite + verify-deploy.sh + LABELS.md).** | Daisy + Morris | story-915-promtail-onboarding-manager-fleet |
| STORY-916 | Queue Health SLOs + Grafana Alerts — three predicates (stuck claimed >30m, unlinked in_review >60m, redispatch loop >3 in 24h) wired to existing alert channel with runbooks in data-remediations/. Surfaces residual queue leaks within minutes instead of days. Medium scope. Path: 1 → 7 → 8 → Done. | Ready | — | story-916-queue-health-slos-grafana-alerts |
| STORY-917 | needs_info question_text guardrail — service-layer guard in dispatch_v2_service.transition() requires non-empty question_text or needs_info_path on needs_info transitions, mirroring the 2026-05-12 submitted-PR-link guard. Migration 058 cancels existing zombie needs_info rows then installs a BEFORE INSERT/UPDATE trigger on dispatch_state_current as defense-in-depth. Closes leak #7 observed during the 2026-05-12 cleanup (12+ zombie needs_info rows cancelled). Medium scope. Path: 1 → 7 → 8 → Done. | Ready | — | story-917-needs-info-question-text-guard |
| STORY-918 | v1→v2 pr_number sync — fix the trigger that should mirror dispatch_items.pr_number into dispatch_jobs.pr_number. Migration 059 installs/replaces the sync trigger (additive, COALESCE-guarded) and one-shot backfills any stranded rows. Closes the silent v1→v2 sync gap that left 26 in_review rows pr_number=NULL for up to 10.8 days (cancelled 2026-05-12). Medium scope. Path: 1 → 7 → 8 → Done. | Ready | — | story-918-v1-v2-pr-number-sync |
| STORY-919 | PR-link backfill handles rework jobs — extend pr_link_backfill_sweeper with a fallback regex parser that extracts PR number from row.title/prompt when branch-name lookup fails (covers Morris's "PR #N rework" / "Rebase PR #N" dispatches). One verify call to GitHub gates the link to prevent cross-repo corruption. Small scope. Path: 1 → 7 → 8 → Done. | Ready | — | story-919-pr-link-backfill-rework-jobs |
| STORY-920 | claude_sdk_tool.py → Agent SDK migration with caching + per-task model tiering — replace the `claude -p` subprocess wrapper with direct Anthropic Python SDK calls; add aggressive prompt caching on system prompts + tool definitions; add per-phase model selection in canonical-state.yaml so Haiku absorbs classifier/triage/summarization while Sonnet stays on Phase 7/8/8b code work; confidence-based fallback to Sonnet when Haiku is uncertain; per-story cost rollup + Grafana dashboards; canary rollout on Dan first with USE_LEGACY_CLAUDE_SDK_TOOL=1 rollback lever. Closes the cost surface that STORY-802 started ad-hoc, and gets us onto the canonical SDK ahead of the 2026-06-15 Agent SDK credit policy change. Large scope. Path: 1 → 2 → 6 → 7 → 8 → 8b → Done. | Ready | — | story-920-claude-sdk-tool-agent-sdk-migration |

## Epic: Cross-Repo Canon Alignment (STORY-1000)

> Bring sdlc-framework, tech-dev-agents (Morris + queue), gc-data-v2, tech-gc-knowledgebase, and data-pipelines-runbooks into a single canon-first, drift-checked, gate-enforced system. Acceptance: all 4 incident classes from the 2026-05-04..05-18 audit are catchable by the new gates.

| ID | Story | Scope | Stream | Status | Branch |
|----|-------|-------|--------|--------|--------|
| STORY-1000 | Cross-Repo Canon Alignment — Epic seed + coordination | Epic | — | Reserved — seed pending | story-1000/canon-alignment-epic |
| STORY-1001 | SDLC `spec` + `phase-1` + `new-project`: build_type classifier + pipeline canon auto-load | Medium | A | Reserved | story-1001/build-type-classifier |
| STORY-1002 | NEW skill `load-canon` + phase-6/7/10 pipeline-aware modifications | Medium | A | PR Open (rework v2) — 30/30 GREEN | story-1002/load-canon-phase-mods-v2 |
| STORY-1003 | NEW skill `canon-backport` + `retro` companion proposal + phase-9 3-question gate | Medium | A | Reserved | story-1003/canon-backport-retro |
| STORY-1004 | NEW skills `scaffold-drift-check` + `pipeline-kickoff` | Small | A | Reserved | story-1004/scaffold-drift-kickoff |
| STORY-1005 | NEW Morris skill `canon-check` — byte-diff v2 PRs against gc-data-v2/pipeline-template **(P0)** | Medium | B | Reserved | story-1005/morris-canon-check |
| STORY-1006 | NEW Morris skill `pre-dispatch-validate` — BLOCKING seed-completeness gate **(P0)** | Medium | B | Reserved | story-1006/morris-pre-dispatch-validate |
| STORY-1007 | Morris `review-prs` + `merge` v2-PR gating (3-question template + canon-drift green) | Medium | B | Reserved | story-1007/morris-review-merge-v2-gates |
| STORY-1008 | Morris `start-story` + `answer-needs-info` + `canon-backport` canon-aware mods | Medium | B | Reserved | story-1008/morris-canon-aware-mods |
| STORY-1009 | Queue: `validate_dispatch_seed()` API gate + POST /api/dispatch/v2/rework endpoint | Medium | C | Reserved | story-1009/queue-rework-and-validate |
| STORY-1010 | Queue: ops-skill fallback registry + PR-link assertion at merge gate | Medium | C | Reserved | story-1010/queue-fallback-prlink |
| STORY-1011 | Queue: sdlc-framework version tagging + downstream canon-drift CI | Medium | C | **In PR #336** | story-1011/sdlc-framework-versioning |
| STORY-1012 | Queue: complete-story 3-question gate + new-behavior assertion gate + canon-version pin in PR descriptions | Medium | C | Reserved | story-1012/story-close-and-coverage-gates |
| STORY-1013 | gc-data-v2: scaffold versioning (v1.0/v1.1 tags) + CHANGELOG.canon.md + sources/TEMPLATE.md + structural-completeness CI | Medium | D | Reserved | story-1013/gc-data-v2-versioning-template |
| STORY-1014 | gc-data-v2: fix S7 telemetry scrubber/exporter inversion + P0-alert mechanism for 🔴 Active platform-gaps findings | Medium | D | Reserved | story-1014/s7-security-and-alerts |
| STORY-1015 | Retire data-pipelines-runbooks → move into gc-data-v2/platform/runbooks/ + cross-repo index in tech-gc-knowledgebase | Small | D | Reserved | story-1015/retire-runbooks-and-index |
| STORY-1016 | tech-gc-knowledgebase: freshness frontmatter + CI stale-page check + weekly Morris scan + complete-story auto-PR loop | Medium | D | Reserved | story-1016/kb-freshness-and-autoflow |
| STORY-1019 | build-artifact.sh: bundle full import closure + pre-deploy import gate (follow-up to 2026-05-18 outage) | Medium | C | Ready | story-1019/build-artifact-bundle-imports |

| QV2-FU-1 | Build /api/dispatch/v2/history endpoint (orchestrator_loop's history call still hits v1) | **Spec'd as STORY-871** — close after STORY-871 merges; orchestrator_loop migration is a sub-follow-up | — | — |
| QV2-FU-2 | Drop v1↔v2 mirror triggers after 7-day clean Morris cron run | Tomorrow follow-up — schedule retro check | — | — |
| QV2-FU-3 | /fleet skill: optionally surface v2 lane data per agent | Tomorrow follow-up — feature add | — | — |
| QV2-FU-4 | Watcher self-observability — watcher_health table + Morris liveness check | Tomorrow follow-up (decisions.md gap) | — | — |
| STORY-760 | No Hardcoded Default-Branch Contract Test | Phase 7 Complete — test-design.md + 10 tests (4 PASS, 5 FAIL, 1 XFAIL strict); ready for Phase 8 | Hermes | story-760/story-760 |
| STORY-741 | Fleet Reliability Guardrails — --model CLI fix, stale-log immunity, rc=2 classification, retry classification, E2E failure-chain tests | Phase 1 Complete (seed) | queue | — |
| STORY-740 | Dispatch State-Machine Contract Consolidation — single source of truth + CI drift test | Phase 1 Complete (seed) — dispatching | queue | — |
| STORY-739 | Dashboard Foundation + Agentic Tier — new Agentic tab + Foundation metrics on homepage | Phase 1 Complete (seed) | — | — |
| STORY-738 | Needs Info Response UI — operator reads QUESTION.md and submits answer from dashboard | Phase 1 Complete (seed) | — | — |
| STORY-737 | Fleet active/queued story counts — source from dispatch queue, not Monday.com + Loki | Phase 1 Complete (seed) | — | — |
| STORY-736 | Fix Foundry cost display — $0 shown for all agents instead of real Azure spend | Phase 1 Complete (seed) | — | — |
| STORY-735 | Dashboard quota display — per-agent accuracy + clarity (fix identical-looking quotas) | Phase 1 Complete (seed) | — | — |
| STORY-734 | Morris Operating Modes — Quota-Aware + Overnight Scheduling | Dispatched (2026-04-26) — pending in queue | queue | — |
| STORY-731 | Deploy systemd unit files alongside code in `push-code.sh` (or new `push-units` action) — STORY-730 changed `hermes-log-sync.service` (turned `/tmp/hermes-combined.log` into a symlink) without a matching `dispatch-poller.service` update; on next poller restart, systemd's `StandardOutput=append:` refused to follow the symlink and dan's poller crashlooped on 2026-04-26. Hand-fixed the canonical unit on all 4 dev VMs same day. Deploy automation must own unit-file drift the same way it owns Python-file drift. | Filed 2026-04-26 — needs seed | — | — |
| STORY-542 | Framework-Level Playwright Enforcement for Frontend Stories | Phase 7 Complete — test-design.md written, 22 tests RED (17 FAIL + 3 FAIL + 1 SKIP across 2 files), ready for Phase 8 | Devon | story-542-sdlc-playwright-enforcement |
| STORY-507 | Resume-Aware Phase Runner + Dispatch Observability | Phase 7 Complete (RED Tests) | Hermes | story-507/resume-aware-phase-runner |
| STORY-440 | Integrate project_file.py into Phase Runner | Phase 1 Complete (Seed) | Hermes | story-440/story-440 |
| STORY-020 | Fix Teams Presence for Long-Running Agent Work | Done (11/11 GREEN) | — | main |
| STORY-027 | Dispatch Queue Auto-Pickup | Seed Complete | Dan | — |
| STORY-028 | Dispatch Queue Database Persistence & History | Done (64/64 GREEN) | — | story-028/dispatch-queue-database |
| STORY-223 | Dispatch Queue Reliability Hardening (Leases/Reconciler/Alerts) | Seed Complete, Dispatched | — | — |
| EPIC-004  | Foundry Cost Observability & Morris SDK-First | Seed Complete (epic) | — | — |
| STORY-224 | Morris SDK-First Enforcement (child of EPIC-004) | Seed Complete | Dan | — |
| STORY-225 | Grafana Cost-per-Agent — Foundry-Only (child of EPIC-004) | Seed Complete | Dan | — |
| STORY-226 | Foundry Daily Limits Post-Baseline (child of EPIC-004) | Scheduled ~2026-04-23 | — | — |
| STORY-227 | User-Assigned Managed Identity for Azure Auth (child of EPIC-004) | Claimed by Dan | Dan (Morris coordinates) | — |
| STORY-322 | Cole the Curator skill definition | PR #39 — review fixes in progress (25/25 GREEN) | — | story-322/cole-curator-skill |
| STORY-480 | Dashboard Overhaul (Foundry costs, quota %, presence, work detail) | Phase 8 Complete (108+16 GREEN, PR open) | — | story-480/story-480 |
| STORY-887 | Curator wiki-backflow — KB-GAP findings → wiki proposals | Phase 8 Complete (13/13 GREEN). curator_backflow.py + curator SKILL.md Steps 1b/1c. PR open. | Mark | story-887/curator-wiki-backflow |
| STORY-900 | Close v1↔v2 cancel desync — operator/cancel route + v1 propagation | Phase 8 Complete (11/11 GREEN). POST /api/dispatch/v2/operator/cancel + v1 propagation. PR open. | Mark | story-900/cancel-propagation |
| STORY-901 | PR-merge → v2 completed transition | Phase 8 Complete (5/5 GREEN). dispatch_pr_merge_sweeper + _pr_merge_sweeper_tick in self_healing.py; registered in main.py lifespan. PR open. | Mark | story-901/pr-merge-completes-v2 |
| STORY-916 | Queue Health SLOs: Grafana alerts for dispatch v2 (stuck claimed PAGE, unlinked in_review WARN, redispatch loop WARN) | **Phase 8 Complete** — 14/14 tests GREEN. queue_health_slos group in grafana-alerts.yml + 3 runbooks. PR #330 open. | Morris | story-916/queue-health-slos-grafana-alerts |

## Completed Stories

| ID | Story | Completed | Branch |
|----|-------|-----------|--------|
| STORY-001 | Container Runtime & Identity | 2026-03-31 | — |
| STORY-002 | Teams Bot Foundation | 2026-03-31 | — |
| STORY-003 | Claude Code Runner | 2026-03-31 | — |
| STORY-004 | Monday.com Integration | 2026-03-26 | — |
| STORY-005 | Persona System | 2026-03-31 | — |
| STORY-006 | SDLC Execution Engine | 2026-03-31 | — |
| STORY-007 | Approval Flow | 2026-03-31 | — |
| STORY-008 | Git Workflow | 2026-03-31 | — |
| STORY-009 | Teams Least-Privilege Graph Permissions | 2026-03-31 | — |
| STORY-010 | Teams Webhook Auth Hardening & Replay Protection | 2026-03-31 | — |
| STORY-011 | Runtime Secret Hygiene & Config Exposure Audit | 2026-03-31 | — |
| STORY-016 | Agent Operations Console (v1 backend) | 2026-04-01 | — |
