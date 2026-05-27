# Work Queue Integration Hooks

These hooks should be added to the agent orchestrator (SOUL.md or equivalent session bootstrap):

## Session Start Hook
```python
from work_queue import WorkQueue

wq = WorkQueue()
active = wq.resume()
if active:
    # Announce resumed state to Mark via Teams
    print(f"Resuming {active['story_id']} at phase {active['phase']}")
    # send_teams_message(f"🔄 Resuming {active['story_id']} at phase {active['phase']}")
```

## Story Start Hook
```python
wq = WorkQueue()
wq.enqueue(story_id, phase=phase, scope=scope, source="mark")
wq.set_active(story_id, phase=phase)
```

## Story Complete Hook
```python
wq = WorkQueue()
wq.complete(story_id)
```

## Side Task Guard
Side tasks (SIDE-*, MAINT-*, HOTFIX-*) are automatically excluded — `enqueue()` returns False for them.
