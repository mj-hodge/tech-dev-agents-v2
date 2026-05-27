# Weekly Retrospective: 2026-03-27 to 2026-04-01

## Context
| Field | Value |
|-------|-------|
| Project | tech-dev-agents (Autonomous Dev Agent v1) |
| Period | 2026-03-27 to 2026-04-01 (5 working days) |
| Stories Completed | 12 (STORY-001 through STORY-011, STORY-016 backend) |
| Scope Breakdown | 3 Small, 7 Medium, 1 Large, 1 Epic (seed only) |
| Workers | 2 agents (Dan, Derrick) + 1 human (Mark) |
| Key Events | Epic v1 stories completed, v2 stories started/reverted/consolidated, STORY-016 full SDLC, SDK enforcement incident, VM provisioning |

## Metrics

| Metric | Value |
|--------|-------|
| Total stories completed | 12 |
| Total tests (GREEN) | 341 |
| Total SDLC phases executed | ~120 (11 stories x ~10 phases + STORY-016 x 12 phases) |
| Code review fix loops | 0 of 12 stories required fix loops |
| Total code review findings | ~148 (0 critical, 0 high, 1 medium, ~8 low, rest informational) |
| Average tests per story | 28.4 |
| Stories approved first pass | 12/12 (100%) |
| Reverted commits | 3 (STORY-012, 013, 014 — then re-applied with fixes) |
| Production incidents | 1 (SDK bypass / native coding) |
| Git commits this week | 45+ |

---

## What Went Well

### 1. Extraordinary velocity — 12 stories in 5 days
The v1 epic (STORY-001 through STORY-011) went from test design through implementation, code review, pre-deploy gate, refinement, and ops reports in a single sustained push. Every story passed code review on the first attempt. **Evidence:** 11 stories completed on 2026-03-31 alone, each with full phase deliverables.

### 2. Zero code review fix loops
All 12 code reviews came back APPROVED with no blocking findings. The worst was 1 medium (an uncommitted enum serialization fix in STORY-016) and a handful of low/informational items. **Root cause of success:** Phase 7 test design was thorough enough that Phase 8 implementations were well-constrained. Frozen dataclasses, dependency injection, and protocol interfaces were used consistently.

### 3. SDLC process produced consistent deliverables
Every story has its full artifact chain: seed, test-design, code-review, predeploy-gate, refinement-report, site-reliability. The `features/<story>/` structure is clean and navigable. Phase gates caught issues before they propagated.

### 4. Revert discipline on v2 stories was correct
When STORY-012/013/014 had non-blocking code review findings, the team reverted cleanly and re-applied with fixes rather than patching incrementally. This prevented downstream integration debt. **Evidence:** 3 clean reverts, 3 clean re-applications, then consolidated into STORY-016.

### 5. Deployment hardening from real incidents
The SDK bypass incident (Dan coding natively through Hermes) led to a comprehensive 6-layer safeguard system deployed same-day. Terminal guard, SDK health checks, cost anomaly alerts, config lockdown, and SOUL.md enforcement. This turned a production issue into a durable operational improvement.

### 6. Memory system captured deployment learnings in real time
VM provisioning lessons, SDK enforcement rules, and fleet state were all captured as memory records during the incidents, making them available for future conversations and new agent bootstrapping.

---

## What Went Wrong

### 1. V2 story scope churn — 3 stories reverted and consolidated
STORY-012 (Cost Dashboard), STORY-013 (Monday Integration), and STORY-014 (Management Dashboard) were individually specced, implemented, reviewed, reverted, re-applied with fixes, then ultimately absorbed into STORY-016 (Agent Operations Console). **Impact:** ~6 hours of wasted implementation + review cycles on work that was superseded. **Root cause:** The individual dashboard stories were specced before the team realized a unified console was needed. The consolidation decision came after implementation, not before.

### 2. SDK bypass incident — agent coding natively, burning tokens undetected
Dan was using Hermes terminal tools (cat, patch, code_execution) instead of Claude Code SDK, consuming Azure Opus tokens without SDK session tracking. **Impact:** Unknown token spend, no audit trail, agent operating outside safety boundaries. **Root cause:** SDK installation had a Python path mismatch (`/opt/hermes-agent/venv/lib/` wrong version), no enforcement layer existed, and no cost anomaly detection was in place.

### 3. No epic-level planning for v2
The v2 stories (STORY-012 through STORY-016) were added incrementally to the backlog without an epic decomposition pass. This led to overlapping scope (three separate dashboard stories that should have been one console story) and unnecessary churn. **Root cause:** Skipped Phase 1 epic decomposition for v2 work — went straight to individual story seeds.

### 4. VM provisioning was ad-hoc
Derrick's VM setup required multiple fix iterations: Docker via snap (wrong), Python path mismatches, port routing confusion, missing workspace directories. **Root cause:** No runbook or provisioning checklist existed. Each fix was discovered through trial and error. The checklist was only codified in memory *after* the incidents.

### 5. Phases 9 and 10 skipped on STORY-016
Refinement and Operations phases were skipped for STORY-016 ("post-deploy, not needed for story completion"). While pragmatic, this means the largest story of the week has no gap analysis or operational readiness review. For a production console that will be deployed to a VM, this is a gap.

---

## Recurring Patterns

| Pattern | Occurrences | Stories | Root Phase |
|---------|-------------|---------|------------|
| Clean first-pass code reviews | 12/12 stories | All | Phase 7 (good test design) |
| Frozen dataclasses + DI + protocols | 11/12 stories | 001-011 | Phase 6 (design patterns) |
| Scope consolidation after implementation | 1 epic-level | 012-016 | Phase 1 (missing epic decomp) |
| VM/deployment issues discovered in prod | 5+ incidents | Deployment | Phase 10 (no runbook) |
| SDK/tooling assumptions untested | 1 critical | Dan deployment | Phase 11 (pre-deploy gate) |

---

## Phase-by-Phase Analysis

### Phase 1 (Seed)
**Finding:** V1 epic decomposition was excellent (8 stories, clear dependency graph, proper parallelism). V2 had no epic decomposition — stories were added one at a time, leading to scope overlap and consolidation churn.
- **Proposed change:** When adding 3+ related stories to a backlog, require a mini-epic decomposition pass (even for "small" additions) to check for overlap and shared infrastructure.

### Phase 2-5 (Research through Selection)
**Finding:** These phases worked well for STORY-016 (the one Large story that went through full SDLC). For v1 stories, they were appropriately scoped to Medium and skipped research/expansion per the scope path. No issues.

### Phase 6 (Design)
**Finding:** The consistent use of frozen dataclasses, dependency injection, and protocol interfaces across all 11 v1 stories contributed directly to the zero-fix-loop code review results. This pattern should be explicitly codified.
- **Proposed change:** Add "Recommended Implementation Patterns" section to Phase 6 agent: frozen dataclasses for value objects, protocol interfaces for dependencies, explicit `__all__` exports.

### Phase 6b (Security Review)
**Finding:** STORY-016 security review caught 12 items (SEC-01 through SEC-12). 8 were must-have for MVP. All were addressed in Phase 8. The security review process is working.

### Phase 6d (Ops Review)
**Finding:** Ops reviews were done for STORY-016 but not for the deployment/VM work itself. The SDK bypass incident could have been caught by an ops review of the agent deployment architecture.
- **Proposed change:** Require an ops review (even lightweight) for any deployment or infrastructure change, not just feature stories.

### Phase 7 (Test Design)
**Finding:** This phase is the single biggest contributor to code quality. Stories with thorough test designs (STORY-006: 26 tests, STORY-016: 72 tests) had zero issues in review. The discipline of writing tests first constrained implementations to be correct.
- **Proposed change:** None needed — this phase is working as designed. Continue current approach.

### Phase 8 (Implementation)
**Finding:** Implementations were clean and focused. The model policy (Sonnet for Phase 8, Opus orchestrates) kept costs reasonable. One issue: STORY-016 had an uncommitted enum serialization fix discovered in 8b — the implementation should have caught this with a final integration test pass.
- **Proposed change:** Add a "smoke test" gate at end of Phase 8: run the full test suite one final time and commit any last fixes *before* entering code review.

### Phase 8b (Code Review)
**Finding:** Reviews were thorough but surfaced only low/informational items. This suggests Phase 7 and 8 are doing the heavy lifting. The 8b agent personas (architect, skeptic, simplifier, rule-reviewer) are a good multi-perspective model.

### Phase 9 & 10 (Refinement & Operations)
**Finding:** Skipped for STORY-016 despite being the largest story of the week. V1 stories all completed these phases. The inconsistency is a risk — STORY-016 is headed for production deployment without operational readiness review.
- **Proposed change:** Phase 9/10 skip should require explicit justification in `.project` with a follow-up task created. Never skip silently.

### Phase 11 (Pre-Deploy Gate)
**Finding:** Pre-deploy gates passed for all stories. However, the gate doesn't cover operational deployment concerns (VM provisioning, service management, monitoring setup). The SDK bypass incident happened *after* the code passed pre-deploy.
- **Proposed change:** Add a "deployment environment" section to pre-deploy gate: verify target environment exists, service management is configured, monitoring is connected, and SDK/tooling dependencies are installed and functional.

---

## Deployment & Operations Findings (Non-Phase)

### D-001: VM Provisioning Needs a Runbook
**Evidence:** Derrick's VM setup required 5+ fix iterations (Docker snap vs apt, Python paths, port routing, workspace dirs, Graph subscription cleanup).
**Action:** Create a VM provisioning runbook in `deployment/vm/` with a step-by-step checklist. The memory system captured the lessons — now codify them as an executable checklist.

### D-002: Agent SDK Enforcement Needs Pre-Deployment Verification
**Evidence:** Dan's SDK was broken (Python path mismatch) and no verification existed. Agent operated natively for unknown duration.
**Action:** Add SDK verification to the VM provisioning checklist AND to the agent health check (already done — verify it persists across updates).

### D-003: Cost Anomaly Detection Should Be Default
**Evidence:** SDK bypass burned Azure tokens with no alerting until manual discovery.
**Action:** Ensure cost anomaly alerting is part of the standard agent deployment, not added reactively after incidents.

---

## Status Tracker

| ID | Finding | Category | Severity | Action | Target | Status |
|----|---------|----------|----------|--------|--------|--------|
| F-001 | V2 stories lacked epic decomposition, causing scope churn | Phase 1 | High | Add mini-epic decomposition gate for 3+ related stories | agents/phase-1-seed.md | IMPLEMENTED (f0388fa) |
| F-002 | Frozen dataclass + DI + protocol pattern drove zero-fix-loop reviews | Phase 6 | Medium | Codify as "Recommended Implementation Patterns" in design agent | agents/phase-6-design.md | IMPLEMENTED (f0388fa) |
| F-003 | Ops review not triggered for deployment/infra work | Phase 6d | High | Extend ops review trigger to deployment changes, not just features | agents/phase-6d-ops-review.md | IMPLEMENTED (f0388fa) |
| F-004 | Uncommitted fix discovered in 8b — missing final smoke test | Phase 8 | Medium | Add final test suite run + commit gate at end of Phase 8 | agents/phase-8-implementation.md | IMPLEMENTED (f0388fa) |
| F-005 | Phase 9/10 silently skipped for largest story | Guidance | High | Require explicit skip justification + follow-up task | software-development-guidance.md | IMPLEMENTED (f0388fa) |
| F-006 | Pre-deploy gate doesn't verify deployment environment | Phase 11 | High | Add deployment environment verification section | agents/phase-11-predeploy-gate.md | IMPLEMENTED (f0388fa) |
| F-007 | VM provisioning was ad-hoc, required 5+ fix iterations | Operations | High | Create VM provisioning runbook | deployment/vm/runbook.md (project) | PENDING (project-level) |
| F-008 | SDK enforcement not verified pre-deployment | Operations | Critical | Add SDK verification to provisioning checklist + health check | deployment/vm/runbook.md (project) | PENDING (project-level) |
| F-009 | Cost anomaly detection added reactively | Operations | High | Make cost alerting part of standard agent deployment | deployment/vm/runbook.md (project) | PENDING (project-level) |
| F-010 | Phase 7 test design is the #1 quality driver — protect it | Phase 7 | Low | No change — document as validated pattern | — | VALIDATED |
| F-011 | Multi-perspective code review (architect/skeptic/simplifier) working well | Phase 8b | Low | No change — document as validated pattern | — | VALIDATED |
