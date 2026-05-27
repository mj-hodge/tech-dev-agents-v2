# Day 6 Research Notes — Prompt Engineering for Code Agents
Date: 2026-05-18

## What was covered
- System prompt design (AGENTS.md, CLAUDE.md, .cursorrules) — what works, what hurts, quantified impact
- Task decomposition patterns (Structured CoT, Plan-then-Act, sub-agent decomposition)
- Context window management (Anthropic's 3 techniques, tool-specific approaches, context rot)
- Few-shot and example-driven prompts (TDP, CEDAR, canonical examples)
- Prompt templates and frameworks (RISEN, CO-STAR, 5-Component Pragmatic, Anthropic layered architecture)
- Anti-patterns and failure modes (hallucinated APIs, test fabrication/reward hacking, tool hallucination, context rot)
- Iterative refinement loops (LLMLOOP 5-stage, ReAct, correction prompt templates, iteration caps)
- Specialized patterns by task (refactoring, debugging, code review, migration, test generation)
- Real-world case studies (Stripe Minions, Shopify, Vercel, Replit)
- Evaluation and testing of prompts (SWE-bench, Promptfoo, regression suites)
- Repository context injection (2-bucket framework, metadata priority, git-as-context)
- GC-specific recommendations and implementation roadmap

## Key findings
1. Auto-generated AGENTS.md files REDUCE success rates in 5/8 settings (ETH Zurich 2025) — bad context is worse than none
2. Stripe's task spec format (XML-tagged: objective/scope/context/acceptance/constraints/style) maps directly to our seed.md — should adopt
3. Context files should be ~40 lines max, 6 core areas — our AGENTS.md at 1000+ lines is actively hurting agent performance
4. GPT-5 cheats on infeasible tests 76% of the time (ImpossibleBench) — specification isolation (hiding tests during implementation) drops this to near-zero
5. Improvement from iterative refinement diminishes after 2-3 cycles — should cap and switch strategy
6. Compressed 8KB docs index outperformed 40KB uncompressed (Vercel) — less is more for agent context
7. XML tags outperform markdown headings for Claude-family models (Anthropic + Replit confirm)

## Output
- wiki/research/prompt-engineering-code-agents.md (708 lines, 37KB)
- Committed to main: 9ec9092

## Implementation priorities for GC
1. XML-tagged seed.md template (high impact, low effort)
2. AGENTS.md audit + compression to ~40 lines (high impact, medium effort)
3. Prompt regression suite with Promptfoo (medium impact, medium effort)
4. Specification isolation in Phase 7→8 handoff (high impact, low effort)
5. Standardized Key Commands across repos (medium impact, low effort)

## Next
Day 7: Synthesis & Master Guide — compile all 6 days into actionable improvements
