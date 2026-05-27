# Seed

## Overview
| Field | Value |
|-------|-------|
| Mode | feature_update |
| Scope | medium |
| Feature Name | Agent Monday.com Integration |
| Story ID | TBD |

## Problem Statement

Agents complete SDLC phases and stories but don't update Monday.com. This means:
- Monday.com boards don't reflect actual progress
- Mark has to manually update boards or check git logs
- No visibility for stakeholders who use Monday.com
- Updates show as Mark (his API token) instead of the agent

## Target User / Use Case

- **Mark (operator)** — wants Monday.com boards auto-updated as agents work
- **Stakeholders** — need Monday.com as source of truth for project status
- **Agents** — need to read Monday.com for story assignments and priorities

## Success Criteria

- [ ] SC-1: Each agent has its own Monday.com user account and API token
- [ ] SC-2: Agent updates Monday.com at each phase transition (status change + summary comment)
- [ ] SC-3: Updates show as the agent's name (Bot Dan, Bot Sarah, etc.) not Mark
- [ ] SC-4: Agent reads Monday.com for story assignments (Ready → In Progress)
- [ ] SC-5: Agent moves stories through: Ready → In Progress → Review → Done
- [ ] SC-6: Agent adds structured comments at each phase with: phase name, deliverables, decisions, duration

## Technical Approach

### Per-Agent Monday.com Setup
1. Create Monday.com user per agent (tech-agent-dan@gorillacommerce.co, etc.)
2. Generate API token per agent from their Monday.com developer settings
3. Configure MCP server with agent's own token in `~/.claude/settings.json`
4. Hermes SOUL.md instructs agent to update Monday at phase transitions

### SOUL.md Updates
Add to every agent's SOUL.md:
```
## Monday.com (update at every phase transition)
After completing each SDLC phase:
1. Move the story to the appropriate status column
2. Add a comment summarizing: what was done, decisions made, deliverables produced
3. Check Monday.com before starting work to confirm story assignment
```

### Board Structure (from SDLC framework trackers/monday.md)
| Column | Who Moves Here |
|--------|----------------|
| Backlog | Phase 1 agent |
| Ready | Repository owner |
| In Progress | Agent at Phase 8 start |
| Review | Agent at story completion |
| UAT | Repository owner after merge |
| Done | Repository owner after production release |

### Monday.com MCP Tools Used
- `search` — find stories/boards
- `get_board_info` — read board structure
- `get_board_items_page` — list stories with status
- `change_item_column_values` — move stories between columns
- `create_update` — add phase completion comments
- `get_user_context` — verify agent identity

### Comment Template
```
**Phase {N} ({name}) Complete**
- **Deliverables:** {list of files produced}
- **Decisions:** {key choices made}
- **Duration:** {time taken}
- **Next:** Phase {N+1} ({next_name})
```
