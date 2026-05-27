# Test Design: Real-Time Agent Presence on Dashboard

> Phase 7 — Test Design (updated: PR #55 Remediation Pass)
> Original Date: 2026-04-18 | Remediation Date: 2026-04-18
> Story: STORY-426
> Scope: Small (remediation) / Medium (original)
> State: RED ✗ — 16 remediation tests failing until Phase 8 implements fixes

---

## 1. Coverage Summary

| Layer | File | Test IDs | Tests |
|-------|------|----------|-------|
| Unit — service (original) | `tests/ops_console/test_presence_service.py` | T426-01–17 | 28 |
| Unit — service (remediation) | `tests/ops_console/test_presence_service.py` | T426-R01–R06 | 16 |
| Integration — route | `tests/ops_console/test_presence_route.py` | T426-18–22 | 12 |
| Component — frontend | `frontend/src/__tests__/PresencePanel.test.tsx` | T426-23–30 | 12 |
| **Total** | | | **68** |

**Coverage target:** All four state derivations, SSH output parsing edge cases, SSH timeout/error handling, concurrent probe fan-out, cache hit/miss, API schema validation, authentication enforcement, and all four UI states.

---

## 2. Acceptance Criteria Traceability

### 2.1 Original Feature ACs (Phase 8 — all GREEN)

| AC | Test IDs | Description |
|----|----------|-------------|
| AC1 (`GET /api/agents/presence`) | T426-18, T426-19b | Endpoint returns 200 with JSON |
| AC2 (agent entry schema) | T426-20, T426-21 | `name`, `state`, `checked_at`, `detail` present |
| AC3 (three SSH signals) | T426-17c | SSH command combines systemctl + pause flag + pgrep |
| AC4 (state derivation logic) | T426-01–05 | All four states + expiry logic |
| AC5 (SSH 5s timeout → offline) | T426-10 | `asyncio.TimeoutError` → offline with detail |
| AC6 (concurrent probe) | T426-14 | `asyncio.gather` probes all enabled agents |
| AC7 (auth required) | T426-19 | 401 without credentials |
| AC8 (UI color coding) | T426-25a–d | Green/yellow/gray/red Tailwind classes per state |
| AC9 (30s auto-refresh) | Not unit-testable — covered by `refetchInterval: 30_000` in hook spec |
| AC10 (test coverage) | All T426 IDs | Entire test suite |

### 2.2 Remediation ACs (PR #55 must-fix — RED until Phase 8)

| AC | Test IDs | Description |
|----|----------|-------------|
| AC1-R (SSH key-only auth) | T426-R01a, T426-R01b | `PasswordAuthentication=no` + `PreferredAuthentications=publickey` in SSH command |
| AC2-R (command injection prevention) | T426-R02a–c, T426-R03 | `_validate_ssh_target` rejects shell metacharacters; `_probe` guards host before exec |
| AC3-R (bounded cache) | T426-R06 | Cache has `maxsize` attribute (≥ 1) preventing unbounded growth |
| AC4-R (no regressions) | T426-01–17 (all 28 GREEN) | All existing tests continue to pass |
| AC5-R (validation + eviction tests) | T426-R02, T426-R06 | New tests for input validation and cache bounds |
| AC7-R (zombie prevention) | T426-R05 | `proc.kill()` + `proc.wait()` called on communicate timeout |
| AC8-R (cache aliasing fix) | T426-R04 | `model_copy` used; stored cache entry not mutated |

---

## 3. Test Cases

### 3.0 Remediation Test Cases (T426-R01–R06) — RED until Phase 8 fixes applied

These tests target the five must-fix issues from Morris's PR #55 review.

#### T426-R01: SSH Key-Only Auth (AC1-R)

| ID | Assertion | Fails Because |
|----|-----------|--------------|
| T426-R01a | `_build_ssh_command` output contains `PasswordAuthentication=no` | Flag absent in current implementation |
| T426-R01b | `_build_ssh_command` output contains `PreferredAuthentications=publickey` | Flag absent in current implementation |

**Fix:** Add `-o PasswordAuthentication=no -o PreferredAuthentications=publickey` to `_build_ssh_command`.

#### T426-R02: Input Validation — `_validate_ssh_target` (AC2-R)

| ID | Input | Expected | Fails Because |
|----|-------|----------|--------------|
| T426-R02a | `"10.0.0.1"` | no exception | Method does not exist (`AttributeError`) |
| T426-R02b | `"agent-01.internal.example.com"` | no exception | Method does not exist |
| T426-R02c (×8) | `"host; rm -rf /"`, `"host\|…"`, `"host&…"`, `` "host`…" ``, `"host$(…)"`, `"host(…)"`, `"host\n…"`, `"host …"` | `ValueError` | Method does not exist |

**Fix:** Add `_validate_ssh_target(value: str)` that rejects strings containing `;|&\`$()` newlines, or spaces via `ValueError`.

#### T426-R03: Host Validation in `_probe` (AC2-R)

| ID | Condition | Expected | Fails Because |
|----|-----------|----------|--------------|
| T426-R03 | `agent.host = "10.0.0.1; bad-command"` | returns `offline`, no subprocess spawned | `_probe` does not call `_validate_ssh_target` before building SSH command |

#### T426-R04: Cache Aliasing Prevention (AC8-R)

| ID | Condition | Expected | Fails Because |
|----|-----------|----------|--------------|
| T426-R04 | Second call hits cache | `stored.cached is False`, `second is not stored`, `second.cached is True` | Current code mutates `cached.cached = True` in-place — stored object becomes `cached=True` |

**Fix:** Replace `cached.cached = True; return cached` with `return cached.model_copy(update={"cached": True})`.

#### T426-R05: Zombie Process Prevention (AC7-R)

| ID | Condition | Expected | Fails Because |
|----|-----------|----------|--------------|
| T426-R05 | `communicate()` raises `TimeoutError` | `proc.kill()` called once, `proc.wait()` called once, result is `offline` | Current `except` block returns immediately without killing proc |

**Fix:** Wrap `communicate()` timeout in a `finally` block calling `proc.kill(); await proc.wait()`.

#### T426-R06: Bounded Cache (AC3-R)

| ID | Assertion | Fails Because |
|----|-----------|--------------|
| T426-R06 | `hasattr(svc._cache, "maxsize")` and `svc._cache.maxsize >= 1` | `TTLCache` has no `maxsize` attribute |

**Fix:** Add `self.maxsize = maxsize` to `TTLCache.__init__` (with default), or switch to `cachetools.TTLCache(maxsize=1, ttl=N)`.

---

### 3.1 Unit Tests — `PresenceService._derive_state()` (T426-01–05)

Pure static method — no I/O, no mocking. Tests cover all four states and the edge case where the pause flag has already expired.

| ID | Condition | Expected State |
|----|-----------|---------------|
| T426-01 | `poller_active=False`, any other values | `offline` |
| T426-02 | `poller_active=True`, `paused_until` in future, `sdk_running=False` | `rate_limited` |
| T426-03 | `poller_active=True`, `paused_until=None`, `sdk_running=True` | `working` |
| T426-04 | `poller_active=True`, `paused_until=None`, `sdk_running=False` | `idle` |
| T426-05 | `poller_active=True`, `paused_until` in past, `sdk_running=False` | `idle` (expired flag → no pause) |

### 3.2 Unit Tests — `PresenceService._parse_output()` (T426-06–09)

Verifies SSH stdout parsing across valid, empty, and malformed inputs. No subprocesses are spawned.

| ID | Input (stdout) | Expected State |
|----|---------------|---------------|
| T426-06 | `"active\n\nrunning\n"` (active poller, no pause, SDK running) | `working` |
| T426-06b | `"active\n\n\n"` (active poller, no pause, no SDK) | `idle` |
| T426-06c | `"active\n<future ISO-8601>\n\n"` | `rate_limited` |
| T426-07 | `"active\n<past ISO-8601>\n\n"` (expired pause flag) | `idle` |
| T426-08 | `"active\nnot-a-date\n\n"` (malformed timestamp) | `idle` or `working` — no crash |
| T426-09 | `"inactive"` (single line, poller not active) | `offline` |
| T426-09b | `""` (completely empty output) | `offline` |

### 3.3 Unit Tests — `PresenceService._probe()` (T426-10–12)

Patches `asyncio.create_subprocess_exec` and `asyncio.wait_for` to simulate SSH subprocess outcomes without network.

| ID | Condition | Expected |
|----|-----------|----------|
| T426-10 | `asyncio.TimeoutError` raised | `offline`, `detail` contains "SSH probe failed" |
| T426-10b | `OSError` raised (ssh binary missing) | `offline`, `detail` populated |
| T426-11 | SSH returncode 255 (connection failure) | `offline`, `detail` contains stderr text |
| T426-12 | returncode 0, stdout `"active\n\nrunning\n"` | `working` |
| T426-12b | returncode 0, stdout `"active\n\n\n"` | `idle` |

### 3.4 Unit Tests — `PresenceService.get_all_presence()` (T426-13–16)

Tests fleet fan-out, cache behaviour, exception coercion, and disabled-agent filtering.

| ID | Condition | Expected |
|----|-----------|----------|
| T426-13 | Cache pre-warmed with response | `_probe` not called, `cached=True` returned |
| T426-13b | Cold cache — probe succeeds | Result stored in cache after probe |
| T426-14 | Two enabled agents, cold cache | `_probe` called exactly twice |
| T426-15 | `_probe` raises `RuntimeError` | Agent coerced to `offline`, no exception propagated |
| T426-15b | Mixed: one `working`, one raises `OSError` | `[working, offline]` — no crash |
| T426-16 | One enabled, one disabled agent | Disabled agent absent from results |

### 3.5 Unit Tests — `PresenceService._build_ssh_command()` (T426-17)

Verifies SSH command structure without executing any subprocess.

| ID | Condition | Expected |
|----|-----------|----------|
| T426-17 | Default service config | Command includes `BatchMode=yes`, `StrictHostKeyChecking=no`, `LogLevel=ERROR`, `ConnectTimeout=5` |
| T426-17b | Agent with `ssh_port=443` | Port `443` appears in command |
| T426-17c | Default poller service name | Remote cmd contains `systemctl`, `dispatch-poller`, `dispatch-poller-paused-until`, `claude_sdk` |
| T426-17d | Custom `poller_service="my-custom-poller"` | Custom name appears in remote cmd |
| T426-17e | `AgentRecord` without `ssh_port` attribute | Falls back to `443` via `getattr` |

### 3.6 Integration Tests — `GET /api/agents/presence` (T426-18–22)

Uses real FastAPI app via `httpx.AsyncClient` (ASGI transport). `PresenceService` is replaced by a `MagicMock` with an `AsyncMock` for `get_all_presence`, injected via `inject_mock_services`.

| ID | Condition | Expected |
|----|-----------|----------|
| T426-18 | Authenticated GET | HTTP 200, `application/json` content-type |
| T426-19 | No `X-API-Key` header | HTTP 401 |
| T426-19b | Valid `X-API-Key` | HTTP 200 |
| T426-19c | Wrong `X-API-Key` | HTTP 401 |
| T426-20 | Default mock response | `agents`, `cached`, `checked_at` present in body |
| T426-20b | Three-agent mock response | All three agent names in body |
| T426-20c | `cached=True` mock response | `cached: true` in JSON |
| T426-21 | Two-agent response | Each agent has `name`, `state`, `checked_at`, `detail` |
| T426-21b | Agent with `detail=null` | `detail` field is `null` in JSON |
| T426-21c | Offline agent with `detail` string | `detail` value preserved in JSON |
| T426-22 | Four-agent response with all states | Returned states == `{"working","idle","rate_limited","offline"}` |

### 3.7 Component Tests — `PresencePanel` (T426-23–30)

Uses Vitest + React Testing Library. The `usePresence` hook is fully mocked so tests are network-isolated.

| ID | Condition | Expected |
|----|-----------|----------|
| T426-23 | Four agents in data | All four agent names rendered |
| T426-24 | All four states | Labels "Working", "Idle", "Rate Limited", "Offline" present |
| T426-25 | `state="working"` | Element with `green` Tailwind class present |
| T426-25b | `state="offline"` | Element with `red` Tailwind class present |
| T426-25c | `state="rate_limited"` | Element with `yellow` Tailwind class present |
| T426-25d | `state="idle"` | Element with `gray` Tailwind class present |
| T426-26 | `isLoading=true` | Skeleton with `animate-pulse` rendered |
| T426-27 | `isError=true` | "Failed to load presence" text rendered |
| T426-28 | `cached=true` | "cached" indicator text rendered |
| T426-28b | `cached=false` | "cached" text NOT rendered |
| T426-29 | Offline agent with `detail` string | Bubble wrapper has `title="SSH timed out"` attribute |
| T426-30 | Any data state | "Agent Presence" heading rendered |

---

## 4. Test File Inventory

| File | Status | Purpose |
|------|--------|---------|
| `tests/ops_console/test_presence_service.py` | T426-01–17: ✓ GREEN (28 tests) / T426-R01–R06: ✗ RED (16 tests) | Unit: original feature + remediation security fixes |
| `tests/ops_console/test_presence_route.py` | ✓ GREEN | Integration: endpoint schema, auth, state enum coverage |
| `frontend/src/__tests__/PresencePanel.test.tsx` | ✓ GREEN | Component: all UI states, loading, error, cached badge, tooltip |

**RED state evidence (remediation tests — 16 failing):**

| Failure | Root Cause |
|---------|-----------|
| T426-R01a/b | `PasswordAuthentication=no` / `PreferredAuthentications=publickey` absent from `_build_ssh_command` |
| T426-R02a/b/c (×10) | `AttributeError: 'PresenceService' object has no attribute '_validate_ssh_target'` |
| T426-R03 | Probe executes without host validation; `create_subprocess_exec` is not blocked |
| T426-R04 | `stored.cached is False` fails — stored object was mutated via `cached.cached = True` |
| T426-R05 | `kill` not called — `except` block returns OFFLINE without cleaning up the subprocess |
| T426-R06 | `hasattr(svc._cache, "maxsize")` is `False` — `TTLCache` has no `maxsize` |

---

## 5. Test Execution Commands

```bash
# Backend unit + integration tests
python3 -m pytest tests/ops_console/test_presence_service.py tests/ops_console/test_presence_route.py -v

# All ops_console tests (regression guard)
python3 -m pytest tests/ops_console/ -v

# Frontend component tests
npm --prefix frontend test run -- --reporter=verbose src/__tests__/PresencePanel.test.tsx
```

---

## 6. What Is NOT Tested

| Concern | Reason |
|---------|--------|
| 30-second auto-refresh cycle | Cannot unit-test `refetchInterval` without real timers + network; covered by the hook spec and E2E observation |
| Actual SSH connectivity to agent VMs | Requires live infrastructure; SSH subprocess is mocked in all tests |
| `presence_push.py` (STORY-304) | Separate concern — already tested |
| Router registration order (`presence.router` before `agents.router`) | Structural constraint; validated by `test_presence_route.py` succeeding (if route were misregistered, 404 would break T426-18) |
| TTLCache time-based expiry | Covered by `test_cache.py` (existing); unit tests seed/clear the cache directly |

---

## 7. Phase 8 Implementation Gate

All 68 tests MUST be GREEN before Phase 8 remediation is considered complete. No test may be skipped or modified to pass — the tests define the contract. If implementation diverges from this design, the tests (not the implementation) are the source of truth.

**Remediation implementation checklist (Phase 8 targets):**

| Task | File | AC |
|------|------|----|
| Add `-o PasswordAuthentication=no -o PreferredAuthentications=publickey` | `presence_service.py` | AC1-R |
| Add `_validate_ssh_target(value: str)` method with metacharacter rejection | `presence_service.py` | AC2-R |
| Call `_validate_ssh_target(agent.host)` in `_probe` before SSH exec | `presence_service.py` | AC2-R |
| Wrap communicate timeout in `finally: proc.kill(); await proc.wait()` | `presence_service.py` | AC7-R |
| Replace `cached.cached = True` with `cached.model_copy(update={"cached": True})` | `presence_service.py` | AC8-R |
| Add `maxsize` parameter to `TTLCache.__init__` and expose as attribute | `cache.py` | AC3-R |
