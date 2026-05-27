# Seed: Agent Queue Visibility in Dashboard

**Story:** STORY-025
**Date:** 2026-04-08
**Scope:** Small
**Phase Path:** 1 → 7 → 8 → Done
**Assignee:** Dan

---

## Problem Statement

The dashboard has a "Queued Stories" KPI card and per-agent `queued_stories` field, but they always show 0. The work queue (`~/.hermes/work-queue.json`) on each agent VM has real data but the ops console can't read it — no SSH access, no HTTP endpoint, no Loki integration.

## Solution

Agents log their queue state to stdout (picked up by Promtail → Loki). The ops console queries Loki for the latest queue state per agent.

### Agent side: Log queue state after every change

In `scripts/work_queue.py`, after every `enqueue()`, `set_active()`, and `complete()` call, emit a structured log line:

```
[QUEUE] active=STORY-024 queued=STORY-093,STORY-094 total=2
```

If no active story: `active=none`. If no queued stories: `queued=none total=0`.

The `claude_sdk_tool.py` already logs `[START]` and `[DONE]` lines that Promtail picks up. Use the same `log()` function or just `print()` to stdout.

### Ops console side: Query Loki for queue state

In `loki_client.py`, add `get_agent_queue(agent_name)` that queries:
```
{agent="dan"} |~ "\\[QUEUE\\]" | last 1h
```
Parse the most recent `[QUEUE]` line to extract active and queued story IDs.

Wire into `routes/fleet.py` to populate `queued_stories` on each agent in the fleet response.

## Acceptance Criteria

| ID | Criterion |
|----|-----------|
| AC-1 | `work_queue.py` emits `[QUEUE]` log line after enqueue/set_active/complete |
| AC-2 | `loki_client.py` has `get_agent_queue()` method that parses `[QUEUE]` lines |
| AC-3 | Fleet API returns `queued_stories` populated from Loki data |
| AC-4 | Dashboard "Queued Stories" KPI shows correct count |
| AC-5 | Agent cards show "N queued" badge when stories are queued |

## Files to Change

| File | Action |
|------|--------|
| `scripts/work_queue.py` | Add `_log_queue_state()` called after mutations |
| `tech_dev_agents/ops_console/services/loki_client.py` | Add `get_agent_queue()` |
| `tech_dev_agents/ops_console/routes/fleet.py` | Wire queue data into agent summaries |
| `tests/test_work_queue.py` | Test `[QUEUE]` log output |
| `tests/ops_console/test_queue_loki.py` | Test Loki queue parsing |

## Out of Scope
- Real-time WebSocket updates
- Queue management from the dashboard (enqueue/dequeue)
- Priority ordering (FIFO only)
