# STORY-021: Persistent Agent Work Queue

## Problem
When an agent restarts (container recycle, crash, manual restart), it loses track of what story and phase it was working on. Mark has to re-send the assignment via Teams, creating friction and wasted cycles.

## Solution
A persistent work queue stored at `~/.hermes/work-queue.json` that:
1. Tracks the active story + phase
2. Maintains a FIFO queue of pending stories
3. Survives restarts — on startup, the agent reads the queue and resumes
4. Excludes side tasks from queue pollution

## Success Criteria
- SC-1: Persist active story + phase to ~/.hermes/work-queue.json, survives restarts
- SC-2: On restart, resume active story from queue file without needing new Teams message
- SC-3: Queued stories tracked in order (FIFO)
- SC-4: Message Mark on restart with resumed state
- SC-5: Queue visible via /fleet or API
- SC-6: Side tasks do not pollute the queue

## Scope
Small — standalone Python module with tests.

## Implementation
- `scripts/work_queue.py` — WorkQueue class
- `tests/test_work_queue.py` — full test suite
- SOUL.md hooks for session start/story lifecycle
