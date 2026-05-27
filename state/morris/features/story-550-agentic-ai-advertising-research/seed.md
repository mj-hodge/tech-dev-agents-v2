# STORY-550 — Agentic AI Advertising Research

## Goal
Research agentic AI architectures applied to advertising and ad-tech, producing a comprehensive brief for the Gorilla Commerce engineering team to inform platform design decisions.

## Scope
- **In scope**: Agentic architectures (OODA loops, reward signals, guardrails), real-world case studies (Amazon, Google, Spotify, NBCUniversal), failure modes and safety patterns, multi-agent system design, human-in-the-loop patterns, trust calibration models
- **Out of scope**: Implementation code, API integration, prototype development

## Deliverables
- `ads-research/09-agentic-ai-advertising.md` — Full research document with executive summary, 6 detailed sections, comparison tables, dated citations, and actionable recommendations

## Key Findings (Preview)
- Amazon, Google, Spotify all shipped agentic ad features in 2025-2026
- Spotify's multi-agent planner reduced campaign creation from 15-30 min → 5-10 sec
- Documented budget disasters range $23K-$52K per incident; 94% fall into 5 failure modes
- Bounded-autonomy with graduated trust calibration is the recommended architecture pattern
- IAB Tech Lab is standardizing agent-to-agent ad buying protocols

## Risk
- Low: research-only story with no production code changes
