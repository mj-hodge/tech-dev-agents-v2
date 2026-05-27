# Retro: Agent Dispatch Trust Gap (2026-05-04)

## Problem Statement
Execution quality is not primarily a model-capability problem; it is a dispatch-package integrity and context-packaging problem. Agents are frequently asked to execute without enough negative constraints, verification scripts, and first-run operational guidance, resulting in avoidable PR-review loops.

## Root Causes
1. Design packages emphasize positive architecture but omit explicit anti-patterns.
2. Validation is described, but executable verification scripts are not mandated.
3. RED tests are treated as optional in some dispatches, making completion subjective.
4. No standardized first-30-minute absorption runbook for dispatched stories.
5. Cross-project memory (incident gotchas) is not auto-inlined into dispatch context.
6. Surprise learnings are deferred to epic retros instead of captured per story.
7. Model-tier boundaries are policy-only; rationale is under-documented.

## Required Framework Changes

### 1) Phase 6: Add "Do Not Do" Section (Mandatory)
Each design package must include a `do-not-do.md` (or section in feature-spec) with:
- concrete anti-patterns
- incident/memory references for each anti-pattern
- impact if violated

Template fields:
- `Anti-pattern`
- `Why it's wrong`
- `Evidence (incident/research/memory)`
- `Detection check`

### 2) Phase 6: Verification Scripts as Deliverables (Mandatory for medium/large)
Design must include executable validation commands/scripts, e.g.:
- `scripts/ams_recon.py`
- expected assertions
- failure handling path

Gate rule: story cannot leave Phase 6 without at least one executable post-change verification command.

### 3) Phase 7 RED Tests: Non-Negotiable for Agent Dispatch
Dispatch blocker:
- no RED tests, no dispatch.

Gate rule:
- Phase 7 artifacts must include test file paths + failing test evidence.

### 4) Dispatch Package: "First 30 Minutes" Runbook
Required section in dispatch payload:
1. Read specific research/design sections.
2. Run verification baseline scripts.
3. Confirm reuse map and known failure modes.
4. Record any mismatch before coding.

### 5) Auto-inline Relevant Memory by Domain Tags
Dispatch tooling should attach memory snippets by tags:
- infra/azure
- queue/review-loop
- amazon-ads-auth
- migration-safety

Gate rule:
- dispatch payload includes `memory_context` block for tagged stories.

### 6) PR-Time "What Surprised Us" Appendix
Each dispatched story PR must include:
- surprises
- mitigations
- framework deltas proposed

### 7) Model Policy Rationale in Framework Docs
Document why model boundaries exist:
- high-judgment synthesis phases vs focused implementation phases
- expected failure modes if boundary is ignored

## Immediate Ops Actions (Queue Integrity)
1. Enforce `submitted` only when `pr_number` exists.
2. Route successful/no-PR output to `needs_info` (`missing_pr_linkage`).
3. Alert when `in_review_missing_pr > 0` for 2 consecutive polls.
4. Post-deploy gate: verify `/api/dispatch/v2/metrics`, `/review-outcome`, `/review-link-pr` are present and functional.

## Success Metrics
- `in_review_missing_pr` stays at 0 during business hours.
- PR review turnaround drops (fewer redispatch loops).
- Reduced "phase_runner_crash" retries caused by missing dispatch context.
- Fewer manual operator interventions per day.

