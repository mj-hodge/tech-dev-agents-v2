# Day 4 — Risk Reduction & Guardrails for AI Coding Agents

**Date:** 2026-05-16
**Researcher:** Morris
**Focus:** Validation gates, rollback strategies, safety guardrails for autonomous coding agents

---

## Summary

Comprehensive research on how to protect against agent-generated code failures. Covers:
- 5-layer validation gate hierarchy (sandboxing → scope enforcement → static analysis → semantic validation → human review)
- Agent failure taxonomy (8 failure modes ranked by frequency × severity)
- Rollback strategies (git revert, feature flags, migration rollback, snapshot restore)
- Runtime circuit breakers for agent-generated production code
- Tool permission models across platforms (Claude Code, Devin, Factory AI, Codex, Cursor)
- Graduated autonomy model (L0-L3, inspired by self-driving levels)
- GC-specific gap analysis with prioritized 10-item action plan

## Key Findings

1. GC's SDLC pipeline is strong at Layer 4-5 (semantic validation, human review) but has gaps at Layers 1-2 (scope enforcement, automated static analysis)
2. Biggest risk: database migrations — no auto-rollback generation, no migration linting (squawk recommended)
3. Quick wins: squawk CI step, standardized ruff+bandit, diff-size circuit breaker in acceptance gate
4. Silent wrong code is the most dangerous failure mode — passes all syntactic checks, fails in production
5. Graduated autonomy (shadow → suggest → auto-with-review → full-auto) should be formalized as fleet grows

## Output

Published to: `tech-gc-knowledgebase/wiki/research/agentic-dev-risk-reduction-guardrails.md` (34KB, ~570 lines)

## Stories to Dispatch

1. Add squawk migration linting to CI (all repos with Alembic)
2. Standardize ruff + bandit static analysis across all repos
3. Add diff-size check to dispatch acceptance gate
4. Add scope_guard section to seed.md template
