# STORY-020: Test Design — Teams Presence Fix

## Phase 7 — Test Design (RED State)

**Created:** 2026-04-07
**Test File:** `tests/test_presence_manager.py`
**Status:** RED (presence_manager.py does not exist yet)

---

## Test Strategy

All tests use `unittest.mock` to mock HTTP calls to the Microsoft Graph API. No real network calls are made. Time-dependent tests use `unittest.mock.patch` on `threading.Event` and `time.time` to control timing.

## Test Matrix

| ID | Test | Success Criteria | Technique |
|----|------|-----------------|-----------|
| T01 | `test_start_sets_busy` | SC-1 | Mock requests.post, verify Busy payload |
| T02 | `test_stop_sets_available` | SC-2 | Mock requests.post, verify Available payload |
| T03 | `test_refresh_thread_fires` | SC-3 | Mock threading, verify refresh calls |
| T04 | `test_debounce_prevents_rapid_calls` | SC-5 | Call start() twice within 30s, verify single API call |
| T05 | `test_graceful_fallback_no_token` | SC-1,SC-2 | Unset GRAPH_ACCESS_TOKEN, verify no crash + warning logged |
| T06 | `test_status_message_includes_story` | SC-4 | Pass --story, verify statusMessage in payload |
| T07 | `test_stop_cancels_refresh_thread` | SC-2,SC-3 | Call start() then stop(), verify thread stopped |
| T08 | `test_cli_start_command` | SC-1 | Invoke CLI with "start", verify start() called |
| T09 | `test_cli_stop_command` | SC-2 | Invoke CLI with "stop", verify stop() called |
| T10 | `test_detects_story_from_env` | SC-4 | Set CURRENT_STORY env var, verify detected |

## Coverage Goals

- 100% of public functions in presence_manager.py
- All 5 success criteria covered
- Edge cases: missing token, rapid calls, long sessions
