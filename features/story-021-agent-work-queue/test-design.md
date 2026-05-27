# STORY-021: Test Design — Persistent Agent Work Queue

## Test Strategy

Unit tests for the `WorkQueue` class in `scripts/work_queue.py`. All tests use `tmp_path` fixture to avoid touching real `~/.hermes/` directory. Tests verify JSON persistence, FIFO ordering, resume-on-restart, side-task exclusion, and atomic writes.

## Test Matrix

| ID | Test | Success Criteria | Description |
|----|------|-----------------|-------------|
| T01 | test_enqueue_creates_file | SC-1 | Enqueue a story, verify JSON file is created with correct schema |
| T02 | test_set_active_marks_story | SC-1 | set_active() updates the active field in queue file |
| T03 | test_complete_removes_story | SC-1, SC-3 | complete() removes story from active and queue |
| T04 | test_resume_returns_active | SC-2 | resume() returns the active story after "restart" (new instance) |
| T05 | test_resume_returns_none_when_empty | SC-2 | resume() returns None when no active story |
| T06 | test_fifo_ordering | SC-3 | Multiple enqueues maintain FIFO order |
| T07 | test_list_returns_all_items | SC-5 | list() returns active + queued items |
| T08 | test_side_task_excluded | SC-6 | is_side_task() returns True for side tasks, enqueue skips them |
| T09 | test_atomic_write | SC-1 | File is written atomically (tmp + rename pattern) |
| T10 | test_corrupt_file_recovery | SC-2 | Corrupt JSON file doesn't crash resume(), returns None |
| T11 | test_enqueue_then_set_active | SC-1, SC-3 | Enqueue + set_active workflow works end-to-end |
| T12 | test_complete_promotes_next | SC-3 | Completing active story promotes next queued story |
| T13 | test_cli_list | SC-5 | CLI `list` subcommand outputs queue contents |
| T14 | test_cli_resume | SC-2 | CLI `resume` subcommand outputs active story |

## RED State

Phase 7 delivers `tests/test_work_queue.py` with all 14 tests. The implementation in `scripts/work_queue.py` is a stub (class exists, methods raise `NotImplementedError`). All tests FAIL (RED).

## File Locations

- Tests: `tests/test_work_queue.py`
- Stub: `scripts/work_queue.py`
