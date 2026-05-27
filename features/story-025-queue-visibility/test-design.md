# Test Design: STORY-025 Agent Queue Visibility in Dashboard

**Story:** STORY-025
**Phase:** 7 (Test Design)
**Date:** 2026-04-08
**Status:** RED (tests written, implementation pending)

---

## Test Matrix

| ID | Test | File | Covers |
|----|------|------|--------|
| T15 | enqueue emits [QUEUE] line | tests/test_work_queue.py | AC-1 |
| T16 | set_active emits [QUEUE] with active story | tests/test_work_queue.py | AC-1 |
| T17 | complete emits [QUEUE] showing cleared state | tests/test_work_queue.py | AC-1 |
| T18 | Multiple queued stories comma-separated | tests/test_work_queue.py | AC-1 |
| T19 | Side tasks don't trigger [QUEUE] log | tests/test_work_queue.py | AC-1 |
| T20 | _parse_queue_line parses full line | tests/ops_console/test_queue_loki.py | AC-2 |
| T21 | _parse_queue_line handles active=none | tests/ops_console/test_queue_loki.py | AC-2 |
| T22 | _parse_queue_line handles queued=none | tests/ops_console/test_queue_loki.py | AC-2 |
| T23 | get_agent_queue returns parsed Loki state | tests/ops_console/test_queue_loki.py | AC-2 |
| T24 | get_agent_queue returns None when no logs | tests/ops_console/test_queue_loki.py | AC-2 |
| T25 | get_agent_queue uses latest [QUEUE] line | tests/ops_console/test_queue_loki.py | AC-2 |
| T26 | Fleet endpoint populates queued_stories | tests/ops_console/test_queue_loki.py | AC-3 |

## Coverage

- **AC-1** (work_queue [QUEUE] log): T15-T19
- **AC-2** (Loki parsing): T20-T25
- **AC-3** (Fleet API wiring): T26
- **AC-4/AC-5** (Dashboard display): Frontend — out of scope for backend tests
