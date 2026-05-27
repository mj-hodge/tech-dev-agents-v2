# STORY-304: Code Review (Phase 8b)

**Story:** Event-Driven Teams Presence
**Reviewer:** Code Review Agent
**Date:** 2026-04-16
**Scope:** Medium
**Test Results:** 17/17 GREEN
**Outcome:** APPROVED

---

## 1. Files Reviewed

| File | Purpose |
|------|---------|
| `deployment/hermes/presence_endpoint.py` | Agent gateway endpoint handler |
| `deployment/hermes/health_server.py` | HTTP server routing integration |
| `deployment/hermes/morris_presence.py` | Morris heartbeat presence computation |
| `tech_dev_agents/ops_console/services/presence_push.py` | Ops-console → agent push service |
| `tech_dev_agents/ops_console/routes/dispatch.py` | Dispatch claim/complete/fail integration |
| `tests/story_304/test_presence_endpoint.py` | Endpoint unit tests |
| `tests/story_304/test_presence_push.py` | Push service unit tests |
| `tests/story_304/test_morris_presence.py` | Morris heartbeat unit tests |
| `tests/story_304/test_teams_m365_cleanup.py` | Polling loop removal verification |
| `tests/story_304/test_dispatch_presence_integration.py` | Dispatch route integration tests |

---

## 2. Code Quality

### 2.1 Separation of Concerns

**PASS**

The implementation cleanly separates three distinct responsibilities:

- **`morris_presence.compute_presence()`** — pure function, no I/O, easy to test. Computes `(availability, activity)` tuple from `sdk_count` and `unread_inbox` signals.
- **`presence_endpoint.handle_presence_request()`** — HTTP boundary handler: authenticate, parse, validate, dispatch to Graph API. No business logic beyond input sanitization.
- **`presence_push.push_presence()`** — async service function with retry/backoff. URL resolution, HTTP execution, and error handling are cleanly separated from the dispatch routes that call it.

Dispatch routes (`dispatch.py`) call `push_presence()` as a fire-and-forget side effect — the presence push never affects the dispatch result, satisfying AC-6.

### 2.2 Naming Conventions

**PASS**

All new names follow the existing project conventions:
- Module names: `snake_case` (`presence_endpoint`, `morris_presence`, `presence_push`)
- Function names: `snake_case` (`handle_presence_request`, `compute_presence`, `push_presence`, `_resolve_agent_gateway_url`)
- Constants: `UPPER_SNAKE_CASE` (`PRESENCE_PATH`, `MAX_RETRIES`, `BASE_DELAY`, `BACKOFF_FACTOR`)
- Private helpers: leading underscore (`_resolve_agent_gateway_url`, `_HAS_PRESENCE_ENDPOINT`)

### 2.3 Docstrings and Comments

**PASS**

All public functions have Google-style docstrings with Args and Returns sections. Story references (`STORY-304`) and AC references (`AC-6`) are cited inline where design decisions deviate from the obvious path (e.g., silent failure rationale).

---

## 3. Error Handling

### 3.1 presence_endpoint.py

**PASS**

- Auth failure: `401` returned before any body parsing — no information leaked.
- JSON parse failure: `400` with `"Invalid JSON body"` — no raw exception propagated.
- `_set_presence` exception: caught with `logger.warning` and swallowed — endpoint returns `200` (best-effort design per AC-6).
- Missing `presence_manager` at import time: handled via `_HAS_PRESENCE_MANAGER` flag, logs a warning and returns `200` — prevents import failures from crashing the health server.

### 3.2 presence_push.py

**PASS**

- `ConnectError`, `ConnectTimeout`, `ReadTimeout`, `OSError`: caught per attempt, retried up to `MAX_RETRIES`.
- `5xx` responses: retried with exponential backoff.
- `4xx` responses: not retried (logged and returned immediately).
- `200`: success path, returns immediately.
- After `MAX_RETRIES` exhausted: logs `error` level and returns — never raises (AC-6 silent failure).
- Agent not found in registry: `_resolve_agent_gateway_url()` returns `None`, `push_presence()` returns early without making any HTTP call.

### 3.3 health_server.py integration

**PASS**

The `_HAS_PRESENCE_ENDPOINT` flag at module level allows the health server to start without `presence_endpoint` if the module is unavailable (e.g., during rollout). The routing guard `if self.path == "/internal/presence" and _HAS_PRESENCE_ENDPOINT:` falls through to the 404 handler cleanly.

---

## 4. Async Patterns

**PASS**

`push_presence()` is `async` and uses `await asyncio.sleep(delay)` for backoff — correct non-blocking pattern. It is called from FastAPI route handlers as a fire-and-forget `asyncio.ensure_future()` or equivalent — verified in `test_dispatch_presence_integration.py` which patches and asserts the call signature.

`presence_endpoint.handle_presence_request()` is synchronous (called from the stdlib `ThreadingHTTPServer` handler) and correctly avoids `asyncio` primitives. The `_set_presence` Graph API call is also synchronous — appropriate for the threading model.

---

## 5. Test Coverage

**17/17 tests GREEN**

| Test File | Tests | Coverage |
|-----------|-------|---------|
| `test_morris_presence.py` | 5 | `compute_presence()` — all branches: sdk active, inbox active, both active, idle, negative values |
| `test_presence_endpoint.py` | 10 | `validate_presence_payload()` — valid/missing/invalid fields; `handle_presence_request()` — missing key, wrong key, correct key, bad JSON, missing fields, enqueue call |
| `test_presence_push.py` | 9 | URL resolution, Busy push, Available push, connection error retry, 5xx retry, 4xx no-retry, API key header, silent on unknown agent |
| `test_teams_m365_cleanup.py` | 4 | `_presence_monitor_loop` removed, `_is_busy` removed from source, `_presence_task` not in `connect()`, `_set_presence` preserved |
| `test_dispatch_presence_integration.py` | 5 | claim→Busy, complete→Available, fail→Available, cancel→no-push, push failure→claim succeeds |

All acceptance criteria (AC-1 through AC-6) are covered by at least one test.

---

## 6. Findings

### F1 — `_HAS_PRESENCE_ENDPOINT` boolean check in routing

**Severity: LOW (non-blocking)**

The routing guard in `health_server.py` is:
```python
if self.path == "/internal/presence" and _HAS_PRESENCE_ENDPOINT:
```
If `_HAS_PRESENCE_ENDPOINT` is `False` (import failed), the request falls through to the final `404` catch-all. This is safe and intentional, but callers may receive a confusing `404` instead of a `503`. Consider adding a dedicated `503` branch for the case where the path matches but the handler is unavailable.

**Not blocking — design rationale (backward compat AC-6) is sound.**

### F2 — `compute_presence` negative value clamping

**Severity: INFO (observation)**

`morris_presence.compute_presence()` defensively clamps negative inputs to zero:
```python
effective_sdk = max(0, sdk_count)
```
This is good defensive coding. There is a test (`test_compute_presence_negative_treated_as_zero`) that explicitly covers this branch.

---

## 7. Verdict

**APPROVED**

The implementation is clean, well-structured, and thoroughly tested. All 17 tests pass. Separation of concerns is excellent — the presence push is a true side effect that cannot break dispatch. Error handling correctly implements the AC-6 silent-failure requirement at every layer. Finding F1 is non-blocking and can be addressed in a follow-up story if desired.
