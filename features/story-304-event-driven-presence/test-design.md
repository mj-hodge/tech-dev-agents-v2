# STORY-304: Test Design

**Phase:** 7 (Test Design)
**Scope:** Small
**Target:** RED state — all tests written, all failing (prior to Phase 8 implementation)

## Test Matrix

| ID | Test | AC | File |
|----|------|----|------|
| T01 | Valid presence payload passes validation | AC-1 | `test_presence_endpoint.py` |
| T02 | POST /internal/presence requires X-API-Key matching OPS_CONSOLE_API_KEY | AC-1 | `test_presence_endpoint.py` |
| T03 | Invalid JSON body returns 400 | AC-1 | `test_presence_endpoint.py` |
| T04 | Missing required fields in payload returns 400 | AC-1 | `test_presence_endpoint.py` |
| T05 | Valid presence request enqueues update via _enqueue_presence | AC-1 | `test_presence_endpoint.py` |
| T06 | push_presence sends Busy/InACall on claim event | AC-2 | `test_presence_push.py` |
| T07 | push_presence sends Available/Available on complete event | AC-3 | `test_presence_push.py` |
| T08 | push_presence sends Available/Available on fail event | AC-3 | `test_presence_push.py` |
| T09 | push_presence retries with backoff on ConnectionError, fails silently | AC-6 | `test_presence_push.py` |
| T10 | push_presence retries on 5xx, no retry on 4xx (except 401) | AC-6 | `test_presence_push.py` |
| T11 | URL resolution from agent registry (host:port) | AC-2 | `test_presence_push.py` |
| T12 | OPS_CONSOLE_API_KEY included in X-API-Key header | AC-1 | `test_presence_push.py` |
| T13 | push_presence fails silently when agent not in registry | AC-6 | `test_presence_push.py` |
| T14 | Morris heartbeat: sdk_count > 0 returns Busy | AC-5 | `test_morris_presence.py` |
| T15 | Morris heartbeat: unread_inbox > 0 returns Busy | AC-5 | `test_morris_presence.py` |
| T16 | Morris heartbeat: both active returns Busy | AC-5 | `test_morris_presence.py` |
| T17 | Morris heartbeat: both zero returns Available | AC-5 | `test_morris_presence.py` |
| T18 | Morris heartbeat: negative values treated as zero (Available) | AC-5 | `test_morris_presence.py` |
| T19 | _presence_monitor_loop removed from TeamsAdapter | AC-4 | `test_teams_m365_cleanup.py` |
| T20 | _is_busy not referenced in TeamsAdapter source | AC-4 | `test_teams_m365_cleanup.py` |
| T21 | _presence_task not created in connect() | AC-4 | `test_teams_m365_cleanup.py` |
| T22 | _set_presence method still exists (used by presence endpoint) | AC-4 | `test_teams_m365_cleanup.py` |
| T23 | claim_story dispatch route triggers push_presence(Busy) | AC-2 | `test_dispatch_presence_integration.py` |
| T24 | complete_story dispatch route triggers push_presence(Available) | AC-3 | `test_dispatch_presence_integration.py` |
| T25 | fail_story dispatch route triggers push_presence(Available) | AC-3 | `test_dispatch_presence_integration.py` |
| T26 | cancel_story does NOT trigger presence push | AC-3 | `test_dispatch_presence_integration.py` |
| T27 | Presence push failure does not break dispatch operations | AC-6 | `test_dispatch_presence_integration.py` |

## AC Coverage

| AC | Tests | Coverage |
|----|-------|----------|
| AC-1: POST /internal/presence endpoint with API key auth | T01-T05, T12 | Endpoint validation, auth, happy path |
| AC-2: Dispatch claim pushes Busy | T06, T11, T23 | Push service + dispatch integration |
| AC-3: Dispatch complete/fail/cancel pushes Available | T07, T08, T24-T26 | All terminal transitions |
| AC-4: _presence_monitor_loop and _is_busy removed | T19-T22 | Source inspection + structural checks |
| AC-5: Morris heartbeat presence logic | T14-T18 | All combinations of sdk_count/unread_inbox |
| AC-6: Backward compat — silent failure | T09, T10, T13, T27 | Retry, backoff, fault isolation |

## Test Strategy

- **Mock Graph API calls** — no real Microsoft auth; presence updates are verified via mock assertions
- **Mock HTTP calls to agent gateways** — no real agent VMs; `httpx`/`aiohttp` clients are patched
- **Async mock fixtures** for dispatch DB service (`AsyncMock`)
- **Source inspection** for removal verification (AC-4) — `hasattr` checks and source file grep
- **Fault injection** — ConnectionError, 5xx responses to verify silent failure (AC-6)
- **No external API calls** — all tests are fully isolated (per External API Write Safety policy)

## Test Files

| File | AC | Tests |
|------|-----|-------|
| `tests/story_304/test_presence_endpoint.py` | AC-1 | 5 tests — endpoint handler validation, auth, request processing |
| `tests/story_304/test_presence_push.py` | AC-2, AC-6 | 8 tests — ops-console push service, retry, backoff, silent failure |
| `tests/story_304/test_morris_presence.py` | AC-5 | 5 tests — heartbeat-driven presence computation |
| `tests/story_304/test_teams_m365_cleanup.py` | AC-4 | 4 tests — removal of old polling code |
| `tests/story_304/test_dispatch_presence_integration.py` | AC-2, AC-3, AC-6 | 5 tests — dispatch route → presence push integration |
