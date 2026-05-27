# Seed: Central Dispatch Queue

**Story:** STORY-026
**Date:** 2026-04-08
**Scope:** Medium
**Phase Path:** 1 → 4 → 6 → [6b, 6c, 6d] → 7 → 8 → 8b → 11 → Done

---

## Problem Statement

Mark dispatches work to specific agents via SSH or Teams, requiring him to know who's idle. When dispatching faster than agents can pick up, work is lost or collides. Team members are assigned a single agent and message them directly, which works — but Mark needs a way to throw work into a pool and let the next available agent pick it up.

## Desired Outcome

Mark runs `/dispatch STORY-XXX` without specifying an agent. The story enters a central queue. The first idle agent picks it up automatically. Direct agent communication (team member → their assigned agent via Teams) continues unchanged.

## Users

- **Mark** — dispatches unassigned work to the pool, sees queue in dashboard
- **Team members** — continue messaging their assigned agent directly (no change)
- **Agents (Dan, Derrick)** — pull from central queue when idle, prioritize direct messages

## Architecture

### Central Queue (on ops console VM)
- `/opt/ops-console/dispatch-queue.json` — FIFO list of unassigned stories
- New API endpoints on the ops console:
  - `POST /api/dispatch` — add story to central queue (Mark's dispatch skill calls this)
  - `GET /api/dispatch/queue` — list pending stories
  - `DELETE /api/dispatch/queue/{story_id}` — cancel a queued story

### Agent Polling
- Agents poll `GET /api/dispatch/next` every 60s when idle
- Response: next story from the queue (or 204 No Content if empty)
- Agent calls `POST /api/dispatch/claim/{story_id}` to claim it (prevents double-pickup)
- Claimed story is removed from central queue, added to agent's local work queue

### Priority Rules
1. **Direct Teams message** — highest priority, interrupts idle polling
2. **Central queue** — picked up only when agent is idle (no active session, no direct message pending)
3. **Local queue** — agent's own queued stories (from earlier dispatch)

### Dispatch Skill Update
- `/dispatch STORY-XXX` without agent name → `POST /api/dispatch` (central queue)
- `/dispatch STORY-XXX --agent derrick` → direct dispatch to Derrick (current behavior)
- `/dispatch STORY-XXX --agent dan` → direct dispatch to Dan (current behavior)

### Dashboard Integration
- New "Dispatch Queue" section on Fleet page showing pending unassigned stories
- Each item shows: story ID, repo, scope, enqueued time, and "waiting for agent" badge

## Acceptance Criteria

| ID | Criterion |
|----|-----------|
| AC-1 | `POST /api/dispatch` adds a story to the central queue with story_id, repo, scope, prompt, enqueued_at |
| AC-2 | `GET /api/dispatch/queue` returns the current queue (FIFO order) |
| AC-3 | `GET /api/dispatch/next` returns the oldest unclaimed story (or 204 if empty) |
| AC-4 | `POST /api/dispatch/claim/{story_id}` marks story as claimed by the requesting agent, removes from queue |
| AC-5 | Agents poll `/api/dispatch/next` every 60s when idle and auto-start claimed stories |
| AC-6 | Direct Teams messages to a specific agent still work unchanged |
| AC-7 | Dashboard shows the central dispatch queue with pending/claimed status |
| AC-8 | `/dispatch` skill sends to central queue when no `--agent` flag is specified |
| AC-9 | Double-claim is prevented — second agent to claim gets 409 Conflict |
| AC-10 | Claimed stories that aren't started within 5 minutes return to the queue (stale claim recovery) |

## Out of Scope (v1)
- Agent skill/capability matching (any agent can pick up any story)
- Priority ordering (FIFO only, no urgency levels)
- Load balancing (first idle agent wins, no cost-based routing)
- Cross-team visibility (team members don't see the central queue)
- Retry/failure handling (if an agent crashes mid-story, manual re-dispatch)

## Dependencies
- STORY-025 (queue visibility) — agents need local queue + [QUEUE] Loki logging
- STORY-021 (work queue) — already merged, provides local queue primitives
- Ops console API — new endpoints added to existing FastAPI app

## Technical Notes

### Agent Idle Detection
An agent is "idle" when:
- No active `claude_sdk_tool.py` process running
- No pending Teams message being processed
- Local work queue is empty (no queued stories)

The polling loop runs in the Hermes gateway as a background task.

### Queue File Format
```json
{
  "pending": [
    {
      "story_id": "STORY-094",
      "repo": "advertising-amazon",
      "scope": "small",
      "prompt": "Start Phase 7 for STORY-094...",
      "enqueued_at": "2026-04-08T20:00:00Z",
      "enqueued_by": "mark"
    }
  ],
  "claimed": [
    {
      "story_id": "STORY-095",
      "repo": "product-health-dashboard",
      "claimed_by": "dan",
      "claimed_at": "2026-04-08T20:05:00Z"
    }
  ],
  "last_updated": "2026-04-08T20:05:00Z"
}
```

### Files to Create/Change
| File | Action |
|------|--------|
| `tech_dev_agents/ops_console/routes/dispatch.py` | New — dispatch queue API endpoints |
| `tech_dev_agents/ops_console/models/responses.py` | Add DispatchItem, DispatchQueue models |
| `tech_dev_agents/ops_console/main.py` | Register dispatch router |
| `deployment/vm/skills/dispatch/SKILL.md` | Update to support `--agent` flag vs central queue |
| `frontend/src/components/DispatchQueue.tsx` | New — dashboard queue display |
| `frontend/src/App.tsx` | Add dispatch queue route |
| Agent-side: Hermes gateway polling loop | New — poll /api/dispatch/next when idle |
