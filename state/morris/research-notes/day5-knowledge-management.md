# Day 5 Research Notes — Knowledge Management for Agent Teams
Date: 2026-05-17

## What was covered
- Knowledge taxonomy for agent teams (architectural decisions, operational patterns, task context, ephemeral learnings)
- Industry landscape: Claude Code skills, GitHub Copilot KBs, Cursor rules, Factory AI knowledge graph, CrewAI memory
- Knowledge flow patterns: post-completion extraction, layered context loading, cross-agent learning loops
- Architecture Decision Records (ADRs) optimized for agent consumption
- State management patterns and the two-location problem
- Cross-repo context challenges
- Implementation roadmap (3 phases)

## Key findings
1. GC is Tier 2 for Morris but Tier 1 for implementation agents — biggest gap is agent-to-agent knowledge transfer
2. Post-completion knowledge extraction is the highest-leverage quick win (~2 min per story, 45% repeat failure reduction)
3. `.claude/rules/` with path-scoped rules would give implementation agents auto-loaded context without changing dispatch
4. The two-location state file problem should be resolved by consolidating to a single git-tracked location
5. ADRs per repo would capture tribal knowledge that agents currently rediscover every session

## Output
- wiki/research/knowledge-management-agent-teams.md (604 lines, 36KB)
- Committed to main: 9c94069

## Next
Day 6: Prompt Engineering for Code Agents
