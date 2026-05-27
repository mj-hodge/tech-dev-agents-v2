# Objectives & Backlog — Last Updated: 2026-04-21T16:30Z

## Team Objectives

### 🆕 HIGH — Knowledgebase → SDLC Integration
- **Status:** PROPOSED to Mark (Apr 19) — awaiting decision on approach
- **Context:** Mark asked how agents should use the KB. Morris found zero references from CLAUDE.md/AGENTS.md. 50 wiki pages + 50 sources unused.
- **Three approaches:** (1) AGENTS.md directive, (2) Per-repo CLAUDE.md, (3) SDLC phase framework integration
- **Action when agents return:** Spec story and dispatch
- **Impact:** Agents will build features informed by existing context instead of starting from scratch

### Primary — SDLC Remediation Across All Repos
- **Status:** Active — SDLC remediation stories dispatched across all repos
- **Queue:** STORY-316 (Dan, advertising-amazon), STORY-313/314/315/317 pending
- **Pattern:** Retroactive SDLC deliverables for all merged PRs missing them
- **Gate:** GITLEAKS_LICENSE still needed for CI on advertising-amazon PRs

### Secondary — EPICs 004/005/006 (advertising-amazon)
- **Status:** Q&A session started on 2026-04-15 with Nik (ad ops)
- **Business Answers Captured:**
  - ACoS: Calculate breakeven ACoS, then apply a down-multiplier. Varies by campaign type & product margin. Higher margin → higher ACoS tolerance.
  - Bids: Amazon's bid recommendations used as sanity check, not the source of truth.
  - Bid strategy: Different bids per campaign type — separate into distinct campaigns; campaign logic analysis dictates spend allocation.
  - Format: New artifact; lowest friction = Mark talking through logic conversationally (option B chosen)
- **Next:** Continue Q&A to reach 80% DECIDED gate before sub-story dispatch

### Tertiary — Product Health Dashboard
- **Status:** STORY-035 (RBAC) has been churning — 14 failures overnight, Mark cancelled STORY-036-039
- **STORY-314:** Pending retry for SDLC remediation on PR #12
- **STORY-317:** Pending retroactive SDLC for merged stories (057-061) missing deliverables
- **Systemic issue:** Product-health-dashboard stories fail repeatedly — likely missing deps or CI config
- **⚠️ OPEN QUESTION from Mark:** Did Morris merge all tickets for product-health-dashboard and evaluate for SDLC consistency? (Morris was checking — incomplete, needs follow-up)

### 🔴 IMMEDIATE — Overnight PR Review Protocol
- **Overnight PR Review Protocol** [P1]: Establish automated overnight PR monitoring so Morris catches merges like STORY-480 (PR #69) that happen outside business hours. Mark expects proactive notification when PRs are merged without review or require manual deployment steps.

### 🔴 IMMEDIATE — Dispatch API Completion Guard (NEW)
- **Status:** GAP IDENTIFIED — needs story spec
- **Issue:** Dispatch API accepts re-dispatch of already-merged stories. STORY-228/229/230 were re-dispatched Apr 21 despite being merged Apr 14-15.
- **Also:** No guard against duplicate agent work — Daisy re-worked STORY-024 that Dan already had PR #17 for.
- **Action:** Spec a story to add pre-dispatch checks: (1) query GitHub for merged PRs matching story ID, (2) reject if already completed, (3) check for existing open PRs/active claims before dispatching.
- **Priority:** P1 — prevents wasted agent tokens and double-work

### 🔴 IMMEDIATE — tech-dev-agents CI/CD + Post-Merge Notification
- **Status:** GAP IDENTIFIED — needs story spec
- **Issue:** No CI/CD pipeline. Deployment is manual. Mark merged PR #69 (STORY-480) and wasn't notified about a deployment step.
- **Action:** Spec a story for either CI/CD pipeline or at minimum post-merge webhook/notification for deployment action.

### 🔴 IMMEDIATE — Reliability Center (tech-project-mapping)
- **Status:** STORY-347 MERGED but needs redeploy (still returning 302 auth redirect)
- **Note:** Rate-limited until Apr 23 — no new dispatch possible
- **Scope:** Single page in tech-project-mapping that consolidates: Loki logs, all metrics, critical business functions view
- **Action:** Morris to spec the ticket now. Analyzing tech-project-mapping current capabilities for reliability features.
- **Must include:** Monitoring guidance file (docs/monitoring-guidance.md) documenting the canonical pattern: health EP, Loki integration, severity labels, critical event logging, SRE runbook
- **Context:** Follows fleet health findings — advertising-amazon DOWN (Mark personally fixing), other services OK but no unified reliability view exists
- **STORY-347:** MERGED but needs redeploy (still returning 302 auth redirect)
- **Business Functions Registry:** ✅ COMPLETE — business-functions-registry.md created with comprehensive audit findings, all reliability grades (A- to F), and key functions per project. Fleet average: ~C+
- **Reliability Grades Assigned (15:48 UTC):** tech-datawarehouse A-, tech-dev-agents A-, advertising-amazon B+ (RECOVERED), tech-gc-knowledgebase B+, sourcing-warning-labels B, tech-project-mapping C+, product-health-dashboard F, fabric-keepa F
- **SRE stories dispatched:** STORY-347 (merged), STORY-348/349/357/360 (all failed retries). Fresh re-dispatch: STORY-381 (tech-pm alertmanager P1), STORY-382 (keepa SRE P1), STORY-383 (sourcing alerts P2)
- **Mark reviewing on Monday:** Items 1 (tech-dw adapters 4/9 failing) and 3 (PHD deployment DNS dead). Monday 1 PM ET reminder set.
- **Token burn lesson:** Dispatch audit work to agents instead of running directly in Opus.

### 🔴 IMMEDIATE — tech-datawarehouse Incident
- **Status:** Incident created per Mark's directive
- **Issue:** DNS resolution failure — hostname cannot be resolved at all
- **Action:** Create incident, add status endpoint story (STORY-353 queued), check Loki logs, ensure app sends critical issues to Loki
- **Context:** Discovered during endpoint health audit 2026-04-17 ~11:30 AM ET

### 🔴 IMMEDIATE — PHD PR #19 Dockerfile Fix
- **Status:** Dispatch pending — CI failure root cause identified
- **Issue:** Dockerfile.frontend COPY path mismatch causing CI build failure on PR #19 (STORY-349)
- **Action:** Dispatch fix story to agent when rate limit resets

### 🔴 IMMEDIATE — Curator (Cole) Activation
- **Status:** Active — Cole running first curation cycle
- **Step 1:** DONE (PR #46 context — Cole running)
- **Step 2:** DONE — Sunday 9 AM ET cron deployed
- **Step 3:** IN PROGRESS — Cole running, curation plan complete, PR being built (21 promotions, 8 merges, 9 skips)
- **Context:** PR #39 merged the skill but it has never run. PR #46 has formatters but no actual send capability.

### 🔴 IMMEDIATE — Advertising-Amazon Monitoring Story
- **Status:** Story being specced for advertising outage (crons not firing)
- **Requirement:** MUST follow the monitoring pattern being established — health EP, Loki integration, cron health verification, alert delivery for cron failures
- **Context:** Crons stopped firing silently with no alerting. Mark is personally fixing the immediate cron issue. STORY-259 dispatched to Derrick for SRE monitoring.

### Tech-Dev-Agents Project — Goal (Per Mark)
- **Plan:**
  1. Update Morris structure to use Claude Code SDK first — make it repeatable for future agents
  2. Update dashboard to show Azure Foundry consumption for overage monitoring
- **Goal:** Build toward a full agentic development team. Need to scale beyond 2 agents eventually.
- **Azure cost tracking:** Creds are live (ops-console-cost-reader SP). Budget target: <$50/day ideal. Warning severity for alerts, not critical.
- **Mark's ask:** Morris should make suggestions on how to achieve the scaling goals — this should be in the project summary

### Knowledgebase (tech-gc-knowledgebase)
- **Structure:** `sources/` (raw landings) + `wiki/` (curated) — layered structure set by Mark
- **Status:** 58 raw source files, 71 wiki pages, index (159 lines). No ingest since Apr 15.
- **Mark's directive:** Pull latest daily. Mark has made structure changes (raw vs curated).

### Operational
- CI/CD monitoring — detect and surface failures automatically
- Fleet health — agents processing, no stale queues
- Story lifecycle tracking — zero stories falling through cracks
- **SDLC enforcement is CRITICAL** — Morris's primary ongoing job
- **Dispatch reliability stories to agents** — Morris is a manager, NOT a developer. When reliability gaps are found, create stories and dispatch to Dan/Derrick. Never implement reliability features directly. (Mark mandate 2026-04-17)
- **Update state files during active sessions** — Not just via crons. Morris must write state updates as events happen during conversations. (Mark mandate 2026-04-17)
- **Reliability registry** — Maintain a project-level reliability registry from comprehensive repo audits. Every project must have health endpoints, key function tracking, and monitoring. Building now via dispatched stories (347-349).
- **Codex adversarial reviews on all PRs** — Every PR review must include a second pass via Codex (GPT-5). code-review.md (Morris) + security-review.md (Codex). Only approve if both pass. Codex is also the fallback when Claude Code rate-limited. (Mark mandate 2026-04-17)
- **Monday.com board alignment** — Daily sync Monday boards with backlog.md and .project files. Mark flagged recurring desync. Added to daily standup routine. (Mark mandate 2026-04-17)

## KPIs
| Metric | Target |
|--------|--------|
| Story throughput | ≥ 3 stories merged/week per agent |
| PR merge time | < 24h Small, < 48h Medium/Large |
| CI pass rate | 100% on new PRs |
| Stories falling through | 0 |
| Fleet uptime | 99%+ |

---

## Backlog (Priority Order)

### 🔴 P1 — Active Dispatch Queue
| Story | Repo | Agent | Status |
|-------|------|-------|--------|
| STORY-373 | advertising-amazon | Dan (Phase 8) + Derrick (Phase 4) | In Progress — both agents working |
| STORY-374-378 | advertising-amazon | — | Retrying (EPIC-006 ad-amazon) |
| STORY-380 | — | — | Pending — real agent status |
| STORY-381 | tech-project-mapping | — | Pending — SRE alertmanager (P1) |
| STORY-382 | fabric-keepa | — | Pending — SRE monitoring & runbook (P1) |
| STORY-383 | sourcing-warning-labels | — | Pending — SRE alert rules & SLOs (P2) |

### 🟡 P2 — Open PRs (21 total across 5 repos)
- **advertising-amazon:** 12 PRs (most blocked by gitleaks CI). PR #79 is CI-green candidate for merge.
- **tech-dev-agents:** 5 PRs — all have Request Changes for SDLC gaps
- **product-health-dashboard:** 2 PRs — both Large, CI failing
- **tech-gc-knowledgebase:** 2 PRs — Large, merge conflicts
- **sourcing-warning-labels:** 1 PR — Medium, human PR (harisgc)

---

## Research — Ongoing Daily (Morris's Job, NOT Dispatched)

PRIMARY FOCUS: Agentic development — are we using the best strategies, patterns, and tools?
This is a multi-day rolling investigation, not a one-shot article. Find things → deep-dive next day → evaluate → bring findings to Mark.

**Mark has said this MULTIPLE TIMES:** "I want to be sure we're using the best strategies, patterns and tools for our agentic development. We've been figuring a lot as we go and not looking to see what's out there." This is NOT optional or low-priority — it's Morris's ongoing daily responsibility.

### Agentic Dev Research (Active — Multi-Day)

| Day | Focus | Status |
|-----|-------|--------|
| Day 1 | Landscape scan — what multi-agent frameworks exist (CrewAI, AutoGen, LangGraph, Swarm, OpenHands, SWE-Agent, Devin, Factory) | ✅ Complete — day1-landscape.md (330 lines, 20+ tools evaluated). Key: LangGraph durable execution is #1 missing capability |
| Day 2 | Deep-dive top 3 frameworks — Claude Code Agent Teams, LangGraph, CrewAI | ✅ Complete — day2-deep-dive.md (897 lines). Verdict: Enable Agent Teams now (free), prototype LangGraph PoC (2-4 weeks), skip CrewAI migration |
| Day 3 | Orchestration patterns — centralized dispatch vs event-driven vs hierarchical. Rate limit handling. | ✅ Complete — day3-orchestration-patterns.md (700+ lines). Verdict: Keep centralized queue, add rate limit defense (P0), model fallback chain, PG NOTIFY event side-channel |
| Day 4 | Missing tools — MCP servers, vector memory, observability (LangSmith, Braintrust), sandboxing | Pending |
| Day 5 | Gap analysis — our setup vs best practices. Prioritized recommendations. | Pending |

### 🆕 Claude Quota/Usage Visibility (Mark Request, Apr 20)
- **Status:** ✅ RESEARCH COMPLETE — cost comparison done, API key switch decision pending with Mark
- **Mark's Core Concern:** Dan and Derrick hit quota walls and can't work. No way to see remaining quota programmatically. Admin API only shows dollar usage, NOT Max subscription quota status.
- **Key Correction (Mark):** Each person has their own independent quota on Claude Max (regular/premium license). NOT shared per org as initially reported.
- **Admin API Findings:**
  1. **Usage Report:** `GET /v1/organizations/usage_report/messages` — token-level data. Requires Admin API Key (`sk-ant-admin...`).
  2. **Cost Report:** `GET /v1/organizations/cost_report` — USD cents. Requires Admin API Key.
  3. **Neither endpoint shows quota remaining** — only consumption data.
- **API Key vs Max Subscription (Mark-directed research):**
  - Per-session API cost: ~$1.10 (Sonnet, 80% cache hit, 20 turns)
  - Light use (<12 sessions/day/agent): API cheaper than Max 5x ($100/seat)
  - Medium+ use (>12 sessions/day/agent): Max subscriptions cheaper
  - API advantage: rate limit headers on every call = full quota visibility
  - Max advantage: cheaper at medium-heavy use, simpler billing
- **⚠️ BLOCKERS:** (1) No Admin API key on any VM — Mark must create one. (2) No decision on API vs Max switch — Mark weighing cost vs visibility tradeoff.
- **Next steps:** (a) When admin key provided: build usage monitoring script. (b) Mark to decide: stay Max (cheaper, no visibility) or switch to API (more expensive, full visibility). (c) Possible hybrid: API for agents, Max for humans.
- **Priority:** HIGH — directly impacts agent uptime. Dan/Derrick blocked 3+ days when they hit limits.

### Secondary Research Topics (Queue)

| # | Topic | Why |
|---|-------|-----|
| 1 | MCP ecosystem & emerging patterns | Spec evolving — new transports, auth, registries |
| 2 | LLM cost optimization | Prompt caching, model routing, token reduction |
| 3 | AI code review tools landscape | CodeRabbit, Codium, Qodo — cost/quality tradeoffs |
| 4 | Azure Container Apps vs ACI | Container Apps offers scaling, Dapr, revisions |
| 5 | Structured logging (Python/FastAPI) | Standardize log format, correlation IDs for Loki |

---

## SDLC Enforcement — Morris's Responsibilities

Morris is the orchestrator/dispatcher. Agents execute phases; Morris ensures the framework is followed before, during, and after.

### Phase Paths by Scope (Non-Negotiable)

| Scope | Path |
|-------|------|
| Trivial | 8 → Done |
| Small | 1 → 7 → 8 → Done |
| Medium | 1 → 4 → 6 → [6b, 6c, 6d] → 7 → 8 → 8b → 11 → Done |
| Large/New | 1 → 2 → 3 → 4 → 5 → 6 → [6b, 6c, 6d] → 7 → 8 → 8b → 11 → [9, 10] → Done |
| Epic | 1 → decompose → per-story SDLC → [E2E gate] → Retrospective → Done |

Brackets `[A, B, C]` = parallel phases. Skipping phases is not allowed.

### Gate Phases (Hard Stop — Explicit Approval Required)

| Phase | Gate | What to Check Before Approving |
|-------|------|-------------------------------|
| 1 (Seed) | Scope correct, ACs complete, risk identified | `features/story-XXX-slug/seed.md` exists and is thorough |
| 8 (Implementation) | Tests GREEN, code matches spec, no regressions | All tests pass, `git diff` reviewed, deliverables present |
| 11 (Pre-Deploy) | All 11 checks pass, `predeploy-gate.md` shows PASS | Report exists, no FAIL/BLOCKED items, sign-off section present |

### Confirm Phases (Ask "Proceed?" — Wait for Yes/No)
Phases 2, 3, 4, 5, 6, 7, 9, 10 — agent must ask before advancing.

### Auto-Advance Phases
Phases 6b, 6c, 8b — proceed immediately after completion.

### Every Phase Must Update (Atomic — All Four Together)
1. `.project` — Phase Routing table (Completed Phases, Current Phase, Current Status, Next Phase, Last Updated)
2. `backlog.md` — Story status, acceptance criteria progress
3. `development-tasks.md` — Task statuses
4. Task tracker (Asana/Monday.com) — Move card + post phase summary comment

### Dispatch Checklist (Run before dispatching ANY story)

- [ ] **Seed exists?** — `features/story-XXX-slug/seed.md` present? If scope requires it (Small+) and missing → run Phase 1 first
- [ ] **SDLC submodule?** — Repo has `.sdlc` submodule pointing to sdlc-framework? If not → flag to Mark
- [ ] **Tracking docs exist?** — Repo has `.project`, `backlog.md`, `development-tasks.md`? If not → flag; cannot dispatch without them
- [ ] **Scope determined?** — Seed identified scope as Trivial/Small/Medium/Large? This determines the phase path
- [ ] **Phase path in .project?** — `.project` Phase Routing has correct Scope Path for the determined scope?
- [ ] **Dispatch prompt complete?** — Includes: story ID, scope, phase path, SDLC compliance requirements, pointer to `features/story-XXX-slug/seed.md`

### PR Review SDLC Checklist

| Scope | Required Deliverables in `features/story-XXX-slug/` |
|-------|-----------------------------------------------------|
| Small | `seed.md`, `test-design.md` |
| Medium | `seed.md`, `analysis.md`, `feature-spec.md`, `security-review.md`, `ux-review.md`, `ops-review.md`, `test-design.md`, `code-review.md`, `predeploy-gate.md` |
| Large | All Medium + `research.md`, `expansion.md`, `selection.md`, `specification.md`, `architecture.md`, `api-design.md`, `database-schema.md`, `implementation-plan.md`, `refinement-report.md`, `site-reliability.md` |

### Fleet-Vigilance SDLC Checks (Every 15 min via heartbeat)
- Phase deliverables on disk match claimed progress in `.project`
- No stories stuck >2 hours without phase advancement
- Cross-repo consistency of tracking docs
- Zero-tolerance: no direct DB writes, no external API calls from tests, no agent-triggered deploys, no secrets in code
