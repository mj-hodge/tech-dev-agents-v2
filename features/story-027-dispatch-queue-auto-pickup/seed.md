# Seed: Dispatch Queue Auto-Pickup

**Story:** STORY-027
**Date:** 2026-04-09
**Scope:** Small
**Phase Path:** 1 → 7 → 8 → Done

---

## Problem Statement

STORY-026 built the central dispatch queue: API endpoints, service layer, frontend dashboard, and tests. However, agents cannot yet **automatically pick up** queued stories. The polling loop that lets idle agents claim work was specced (STORY-026 feature-spec.md §3) but not implemented. Additionally, the stale claim recovery background task is not wired into the app lifespan, and the `/dispatch` skill doesn't exist yet to route queue-vs-direct dispatching.

Without these three pieces, Mark still has to manually trigger agents to claim work from the queue, which defeats the purpose of the central dispatch queue.

## Desired Outcome

1. **Agent polling loop** — Each agent VM polls `GET /api/dispatch/next` every 60 seconds when idle. When a story is returned, the agent claims it via `POST /api/dispatch/claim/{story_id}`, adds it to the local work queue, and starts `claude_sdk_tool.py`.
2. **Stale claim recovery** — A background `asyncio` task in the ops console runs `recover_stale_claims()` every 60 seconds, returning abandoned claims to the pending queue.
3. **Dispatch skill** — `/dispatch STORY-XXX --repo foo --scope medium` enqueues to the central queue. `/dispatch STORY-XXX --agent dan --repo foo` sends directly to the named agent (existing SSH behavior).

After this story, Mark's workflow becomes: run `/dispatch STORY-XXX`, story enters the queue, next idle agent picks it up automatically.

## Users

- **Mark** — dispatches work via `/dispatch` skill, no longer needs to know which agent is idle
- **Agents (Dan, Derrick)** — poll the queue when idle, auto-claim and execute stories

## Success Criteria

| ID | Criterion | Verification |
|----|-----------|-------------|
| AC-1 | Agent polling loop polls `/api/dispatch/next` every 60s when idle | Unit test: polling thread calls endpoint at correct interval |
| AC-2 | Idle detection returns true when no `claude_sdk_tool.py` process is running and local work queue is empty | Unit test: `is_agent_idle()` with mocked subprocess |
| AC-3 | Agent claims story and starts `claude_sdk_tool.py` with correct prompt and workdir | Integration test: mock API + mock subprocess |
| AC-4 | 409 on claim (race condition) triggers retry of `/dispatch/next` | Unit test: polling loop retries on 409 |
| AC-5 | Polling skips cycle when agent is busy (SDK process running) | Unit test: `is_agent_idle()` returns False when SDK running |
| AC-6 | Stale claim recovery background task runs every 60s in ops console | Unit test: asyncio task calls `recover_stale_claims()` |
| AC-7 | Stale claims (>5min) return to pending queue | Already tested (T30-T32 in STORY-026) |
| AC-8 | Recovery task cancels cleanly on shutdown | Unit test: CancelledError handled |
| AC-9 | `/dispatch STORY-XXX --repo foo` (no --agent) enqueues to central queue via API | Integration test: skill calls POST /api/dispatch |
| AC-10 | `/dispatch STORY-XXX --agent dan --repo foo` dispatches directly to agent | Integration test: skill uses SSH direct dispatch |
| AC-11 | `/dispatch` without required args shows usage help | Unit test: missing args returns help text |
| AC-12 | Polling loop logs each cycle to stdout (Promtail → Loki) | Verify `[DISPATCH]` log lines in test output |

## Architecture

### Agent Polling Loop (on agent VM)

Integrates into `deployment/hermes/health_server.py` as a background daemon thread. Runs alongside the existing HTTP health server.

```
Every 60 seconds:
  1. is_agent_idle()?  → No: skip, log "[DISPATCH] busy, skipping"
  2. GET /api/dispatch/next  → 204: skip, log "[DISPATCH] queue empty"
  3. Got story → POST /api/dispatch/claim/{story_id}
  4. 200: Add to local WorkQueue, start claude_sdk_tool.py
  5. 409: Another agent claimed it → retry step 2
```

**Idle detection:**
- `pgrep -f claude_sdk_tool.py` returns non-zero (no SDK process running)
- Local WorkQueue has no active item (`resume()` returns None)

**Environment variables:**
| Variable | Default | Description |
|----------|---------|-------------|
| `OPS_CONSOLE_URL` | (required) | Base URL of ops console API |
| `OPS_CONSOLE_API_KEY` | (required) | API key for auth |
| `DISPATCH_POLL_INTERVAL` | `60` | Seconds between polls |
| `AGENT_NAME` | (required) | Agent name for claim requests |
| `AGENT_WORKSPACE` | `/home/hermes/workspace` | Default workdir for SDK |

### Stale Claim Recovery (on ops console)

Wire `_stale_claim_recovery_loop()` into `main.py` lifespan:
- `asyncio.create_task()` before `yield`
- `task.cancel()` after `yield`
- Logs recovered stories via `logger.info()`

### Dispatch Skill

New skill at `.sdlc/skills/dispatch/SKILL.md`:
- Parses args: `story_id` (required), `--repo` (required), `--scope` (default: small), `--agent` (optional), `--prompt` (optional)
- No `--agent`: POST to `/api/dispatch` (central queue)
- With `--agent`: SSH direct dispatch to named agent (existing behavior from ops console)

## Files to Create/Modify

| File | Action | Est. Lines |
|------|--------|-----------|
| `deployment/hermes/dispatch_poller.py` | **Create** — polling loop module | ~120 |
| `deployment/hermes/health_server.py` | **Modify** — start polling thread on boot | ~10 |
| `tech_dev_agents/ops_console/main.py` | **Modify** — wire stale recovery bg task | ~15 |
| `.sdlc/skills/dispatch/SKILL.md` | **Create** — dispatch skill | ~60 |
| `tests/deployment/test_dispatch_poller.py` | **Create** — polling loop tests | ~200 |
| `tests/ops_console/test_stale_recovery_task.py` | **Create** — bg task tests | ~80 |

**Total estimated new/modified lines:** ~485

## Scope Classification: Small

- Three well-defined, isolated changes (polling loop, bg task, skill file)
- No API/DB schema changes — all endpoints already exist
- Feature spec from STORY-026 §3-4 provides exact design
- Existing test infrastructure covers the API layer; new tests cover agent-side logic only

## Phase Path

`1 → 7 → 8 → Done`

## Risks

| Risk | Mitigation |
|------|-----------|
| Polling loop crashes and stops picking up work | Wrap in try/except with logging; health server stays up regardless |
| Race condition: two agents claim same story | API returns 409; polling loop retries with next story |
| Agent idle detection false positive (SDK crashed but process lingers) | Stale claim recovery returns story to queue after 5min |
| `fcntl` not available on agent VM | Already used by dispatch_service.py in STORY-026; VMs are Linux |

## Dependencies

- **STORY-026** (Central Dispatch Queue) — Complete. All API endpoints, service, models, and tests are merged to main.
- **STORY-025** (Agent Queue Visibility) — Complete. Local work queue (`scripts/work_queue.py`) and Loki logging are merged.
