# STORY-020: Fix Teams Presence for Long-Running Agent Work

## Phase 1 — Concept & Seed

**Scope:** Small
**Phase Path:** 1 → 7 → 8 → Done
**Created:** 2026-04-07

---

## Problem Statement

Agents always show "Available" in Microsoft Teams even when actively running Claude Code SDK sessions. Team members (the engineering manager) cannot tell at a glance whether an agent is busy executing work or idle and available for new tasks. This creates confusion about agent utilization and makes it hard to know when to assign new work.

## Options Considered

### Option A — Patch Hermes Internals
Modify the core Hermes bot framework to automatically set presence on SDK invocation.
- **Pros:** Automatic, no agent-side config needed
- **Rejected:** Violates the constraint of not modifying Hermes internals; tight coupling

### Option B — Teams Bot Activity Handler
Use the Bot Framework's activity handler to infer busy state from message flow.
- **Pros:** No external script needed
- **Rejected:** Cannot reliably detect SDK session boundaries; activity handler sees messages, not execution state

### Option C — External Presence Manager Script (CHOSEN)
A self-contained `scripts/presence_manager.py` that agents call before/after SDK sessions.
- **Pros:** Zero coupling to Hermes; simple CLI interface; easy to test; agents already have GRAPH_ACCESS_TOKEN
- **Cons:** Requires agents to explicitly call the script (mitigated by AGENTS.md guidance)

## Chosen Approach — Option C

### Implementation Plan

1. **`scripts/presence_manager.py`** — Self-contained script with CLI interface:
   - `start [--story STORY-XXX]` — Sets presence to Busy via Microsoft Graph API
   - `stop` — Sets presence back to Available
   - Uses `PATCH /users/{userId}/presence/setPresence` endpoint
   - Sets `availability=Busy`, `activity=InACall`, `expirationDuration=PT10M`
   - Background refresh thread fires every 5 minutes to maintain Busy state during long sessions
   - 30-second debounce prevents presence flicker on rapid start/stop calls
   - Sets `statusMessage` to "Working on STORY-XXX" when story is known
   - Detects current story from `.project` file or `CURRENT_STORY` env var
   - Auth via `GRAPH_ACCESS_TOKEN` env var; graceful fallback (log warning, no crash) if missing

2. **AGENTS.md update** — Add guidance for agents:
   - Before SDK calls: `python3 scripts/presence_manager.py start --story STORY-XXX`
   - After session ends: `python3 scripts/presence_manager.py stop`

### Dependencies
- `requests` (already available in agent environments)
- No new pip dependencies required

## Success Criteria

| ID | Criterion | Verification |
|----|-----------|--------------|
| SC-1 | Agent shows Busy when SDK session is running | Unit test: start() calls Graph API with Busy |
| SC-2 | Agent shows Available when idle (within 30s of session end) | Unit test: stop() calls Graph API with Available |
| SC-3 | Busy state holds for full duration of long sessions (10+ min) | Unit test: refresh thread fires on schedule |
| SC-4 | Status message shows "Working on STORY-XXX" | Unit test: status message includes story name |
| SC-5 | No presence flicker on rapid messages (30s debounce) | Unit test: rapid calls are debounced |

## Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| Graph API token expired/missing | Presence not updated | Graceful fallback — log warning, never crash |
| Refresh thread not cleaned up | Stale Busy state | Graph API expiration (PT10M) is the safety net; stop() also kills thread |
| Agent forgets to call stop | Shows Busy when idle | 10-minute expiration on presence means auto-recovery |

## Out of Scope

- Modifying Hermes internals
- Automatic presence detection (future enhancement)
- Multi-agent presence coordination
