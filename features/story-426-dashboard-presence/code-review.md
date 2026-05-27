# Code Review — STORY-426: Real-Time Agent Presence on Dashboard

> Phase 8b — Parallel Sub-Agent Review (architect · skeptic · simplifier · rule-reviewer · qa-preflight)
> Date: 2026-04-18
> Story: STORY-426
> Scope: Medium
> Verdict: **CHANGES REQUIRED** — 1 Critical + 4 High + 6 Medium findings must be resolved before merge

---

## Summary

The architecture is sound. State derivation (`_derive_state`) is correct, clean, and fully unit-tested.
Pydantic models are well-shaped. The React component is properly decomposed with loading skeleton, error
boundary, and dark-theme Tailwind classes. The route wiring (including router ordering in `main.py`) is
correct. Test coverage is broad — 30 test IDs across ~710 lines.

However, a **Critical** functional gap prevents the feature from shipping: `<PresencePanel />` is built
but never mounted anywhere in the component tree, making the entire dashboard widget unreachable. Beyond
that, four High defects introduce real production risk under load or network instability: a subprocess
resource leak on communicate() timeout, cache object aliasing, a cache stampede race, and silent
swallowing of SSH exit codes 1–254 with no diagnostic detail. Six Medium defects — including a broken
config setting, an unguarded app.state access, an accessibility regression, and missing type safety — are
strongly recommended fixes before merge.

---

## Findings by Severity

### Critical

---

#### C-1 · `<PresencePanel />` never mounted — primary deliverable is dark in production

**File:** `frontend/src/components/AgentGrid.tsx` (not changed)
**Spec ref:** Feature-spec §5.2 Step 9, AC8
**Disposition:** **FIX NOW**

`PresencePanel.tsx` is fully implemented and compiles cleanly. `usePresence.ts` polls correctly.
`GET /api/agents/presence` responds correctly. None of it matters: `AgentGrid.tsx` never imports
or renders `<PresencePanel />`. Neither does `DashboardLayout.tsx`, `App.tsx`, or any other component.

The feature-spec explicitly calls out Step 9 (mount in `AgentGrid`) as the final implementation step
and AC8 depends on it. The spec even acknowledged inline that it was "remaining Phase 8 work," but
Phase 8 was marked complete without closing it.

**Impact:** The entire user-visible deliverable of STORY-426 is absent from every browser session.
The backend SSH probing, caching, and API work all functions — it is simply never called from the UI.

**Fix:** In `frontend/src/components/AgentGrid.tsx`, import and render above the agent card grid:
```tsx
import { PresencePanel } from './PresencePanel';

// Inside the component return, after <DispatchQueue /> and before the agent card grid:
<PresencePanel />
```

---

### High

---

#### H-1 · Orphaned SSH subprocess on `communicate()` timeout — resource leak

**File:** `tech_dev_agents/ops_console/services/presence_service.py` — `_probe()`, lines ~82–97
**Disposition:** **FIX NOW**

```python
# Current code
try:
    proc = await asyncio.wait_for(
        asyncio.create_subprocess_exec(*ssh_cmd, ...),
        timeout=self._ssh_timeout + 1,
    )
    stdout_bytes, stderr_bytes = await asyncio.wait_for(
        proc.communicate(),
        timeout=self._ssh_timeout,   # ← can raise TimeoutError after proc is alive
    )
except (asyncio.TimeoutError, OSError) as exc:
    return AgentPresence(
        name=agent.name,
        state=PresenceState.OFFLINE,
        checked_at=now,
        detail=f"SSH probe failed: {exc}",
        # ← proc.kill() / proc.wait() never called
    )
```

When `asyncio.wait_for(proc.communicate(), ...)` times out, `proc` is already spawned but is never
killed or reaped. The OS `ssh` process continues running — blocking on network I/O, consuming a FD
pair (stdout + stderr pipe), a PID slot, and keeping the remote `sshd` session alive.

With 6 agents all timing out simultaneously (e.g., network partition), each 30-second poll cycle
deposits 6 zombie processes. Sustained over hours this exhausts file-descriptor limits (default 1024
on many Linux systems) and eventually the process table.

**Secondary issue:** The inner `wait_for` timeout (`ssh_timeout = 5s`) is *tighter* than the outer
(`ssh_timeout + 1 = 6s`). The inner timeout is almost always the one that fires. The outer is
effectively dead code, misleading readers into thinking there is protection during subprocess spawn.

**Fix:** Initialise `proc = None` before the `try`, kill and reap inside the handler:

```python
proc: asyncio.subprocess.Process | None = None
try:
    proc = await asyncio.wait_for(
        asyncio.create_subprocess_exec(*ssh_cmd, ...),
        timeout=self._ssh_timeout + 1,
    )
    stdout_bytes, stderr_bytes = await asyncio.wait_for(
        proc.communicate(),
        timeout=self._ssh_timeout,
    )
except (asyncio.TimeoutError, OSError) as exc:
    if proc is not None:
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
    return AgentPresence(
        name=agent.name,
        state=PresenceState.OFFLINE,
        checked_at=now,
        detail=f"SSH probe failed: {exc}",
    )
```

**Test gap (QA-preflight):** `T426-10` patches both `wait_for` calls simultaneously via
`asyncio.wait_for` global patch — the subprocess is never actually created in this test. The
`communicate()` timeout path (where `proc` exists but communicate stalls) is completely untested.
Add a test that mocks `create_subprocess_exec` to succeed and `proc.communicate()` to raise
`TimeoutError`, then asserts `proc.kill()` was called.

---

#### H-2 · Cache aliasing — stored Pydantic object mutated in place

**File:** `tech_dev_agents/ops_console/services/presence_service.py` — `get_all_presence()`, line ~37
**Disposition:** **FIX NOW**

```python
cached = self._cache.get("all_presence")
if cached is not None:
    cached.cached = True   # ← writes through to the live TTLCache entry
    return cached
```

`TTLCache.get("all_presence")` returns the exact Python object reference held by the cache store.
Mutating `cached.cached = True` therefore modifies the live cache entry in place. Any caller that
holds a previous reference to the returned response silently sees its `cached` flag flip. If any
downstream code (future middleware, a serialisation adapter, a test) calls `response.cached = False`
on the returned object, the stored entry is permanently corrupted for the remaining TTL.

Pydantic v2 models are mutable by default (`AgentPresenceListResponse` has no
`model_config = ConfigDict(frozen=True)`), so there is nothing preventing this.

**Fix:** Return a structural copy rather than mutating:
```python
if cached is not None:
    return cached.model_copy(update={"cached": True})
```

---

#### H-3 · Cache stampede — no concurrency guard on cache miss

**File:** `tech_dev_agents/ops_console/services/presence_service.py` — `get_all_presence()`, lines ~38–57
**Disposition:** **FIX NOW**

The cache-miss check and the subsequent `cache.set` have no lock between them. Two concurrent
`await svc.get_all_presence()` coroutines arriving on a cold cache both read `None`, both enter the
fan-out branch, and both independently fire `asyncio.gather(...)` against all 6 agents.

Python's event loop is single-threaded, but `asyncio.gather` suspends at every `await`, allowing
interleaved execution from multiple coroutines. With `refetchIntervalInBackground: true` in the
frontend hook, every open browser tab polls every 30 seconds regardless of visibility. Three
simultaneous users at cache expiry = 18 parallel SSH connections to each agent VM.

**Fix:** Add an `asyncio.Lock` with double-checked locking:

```python
# __init__
self._lock: asyncio.Lock = asyncio.Lock()

# get_all_presence
async with self._lock:
    cached = self._cache.get("all_presence")
    if cached is not None:
        return cached.model_copy(update={"cached": True})
    # ... fan-out and cache.set ...
```

**Test gap (QA-preflight):** No test asserts that concurrent calls result in exactly
`len(enabled_agents)` total probes. Add a test running
`asyncio.gather(svc.get_all_presence(), svc.get_all_presence())` and asserting
`mock_probe.call_count == len(enabled_agents)` (not double).

---

#### H-4 · SSH exit codes 1–254 silently fall through to `_parse_output` — diagnostic detail lost

**File:** `tech_dev_agents/ops_console/services/presence_service.py` — `_probe()`, lines ~99–108
**Disposition:** **FIX NOW**

```python
if proc.returncode == 255:
    # SSH connection failure
    ...
    return AgentPresence(..., state=OFFLINE, detail=stderr_text)

# Exit codes 1–254 fall through here with no detail
stdout = stdout_bytes.decode("utf-8", errors="replace")
return self._parse_output(agent.name, stdout, now)
```

SSH returns 255 for connection-level failures, but the remote shell can return any code 1–254:
exit 126 (remote command not executable), exit 127 (remote `/bin/sh` not found), exit 1
(`systemctl` not installed on a non-systemd VM). In all these cases `stdout` is empty or contains
error text; `_parse_output` silently derives `OFFLINE` with `detail=None`, discarding the diagnostic.
Operators see a red bubble with no explanation — indistinguishable from a genuine SSH timeout.

**Fix:** Branch explicitly on non-zero, non-255 codes:
```python
if proc.returncode == 255:
    stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()
    return AgentPresence(name=agent.name, state=PresenceState.OFFLINE,
                         checked_at=now, detail=stderr_text or "SSH connection failed")

if proc.returncode != 0:
    stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()
    return AgentPresence(name=agent.name, state=PresenceState.OFFLINE,
                         checked_at=now,
                         detail=f"Remote command exited {proc.returncode}: {stderr_text}")
```

**Test gap (QA-preflight):** No test covers exit codes 1–126. Add
`test_probe_nonzero_exit_code_returns_offline` with `returncode=1` and assert `detail` contains the
exit code and stderr.

---

### Medium — Should Fix Before Merge

---

#### M-1 · `ConnectTimeout=5` hardcoded — ignores `self._ssh_timeout` and config

**File:** `tech_dev_agents/ops_console/services/presence_service.py` — `_build_ssh_command()`, line ~115
**Disposition:** **FIX NOW** (single-character change)

```python
return [
    "ssh",
    "-p", port,
    "-o", "ConnectTimeout=5",   # ← hardcoded; self._ssh_timeout is ignored
    "-o", "StrictHostKeyChecking=no",
    ...
]
```

`OPS_PRESENCE_SSH_TIMEOUT` flows through `config.py` → `settings.presence_ssh_timeout` →
`self._ssh_timeout` → used by `asyncio.wait_for`. But the SSH binary's own TCP connect timer is
always `5` regardless. Changing the setting to `2` (fast-fail) or `15` (slow VPN) has zero effect
on how long SSH waits for a TCP SYN response.

Test `T426-17b` asserts the literal string `"ConnectTimeout=5"` and passes even when the bug is
active — it provides no regression protection.

**Fix:** `f"ConnectTimeout={self._ssh_timeout}"`

**Test fix:** Assert `f"ConnectTimeout={svc._ssh_timeout}"` (parameterised, not literal).

---

#### M-2 · `app.state.presence_service` unguarded in route handler

**File:** `tech_dev_agents/ops_console/routes/presence.py`, line 20
**Disposition:** **FIX NOW**

```python
async def get_presence(request: Request) -> AgentPresenceListResponse:
    svc = request.app.state.presence_service   # AttributeError if not set
    return await svc.get_all_presence()
```

If `presence_service` was never attached to `app.state` (startup exception, partial rollback,
test without full lifespan), this raises an unhandled `AttributeError`. FastAPI converts it to an
unformatted HTTP 500 with a Python traceback in the response body, leaking internal service
structure to the caller. Every other service access in the codebase uses a `getattr` guard + 503.

Also: `logger = logging.getLogger(__name__)` is imported and defined but never used.

**Fix:**
```python
# Remove unused logger or use it
svc = getattr(request.app.state, "presence_service", None)
if svc is None:
    raise HTTPException(status_code=503, detail="Presence service unavailable")
return await svc.get_all_presence()
```

**Test gap:** No test for the 503 path. Add `test_returns_503_when_presence_service_not_configured`.

---

#### M-3 · `title=""` on every non-offline bubble — accessibility regression

**File:** `frontend/src/components/PresencePanel.tsx` — `PresenceBubble`, line ~35
**Disposition:** **FIX NOW**

```tsx
<div className="..." title={agent.detail ?? ""}>
```

When `agent.detail` is `null` (which is always true for working/idle/rate_limited agents),
this renders `title=""` on every bubble. Screen readers (NVDA, JAWS, VoiceOver) announce a blank
tooltip on keyboard focus. Chrome and Firefox briefly flash an empty tooltip box on hover.
Axe / WAVE accessibility audit reports this as a violation.

**Fix:** Omit the attribute entirely when there is no detail:
```tsx
{...(agent.detail ? { title: agent.detail } : {})}
```

---

#### M-4 · `datetime.fromisoformat()` rejects `Z`-suffix on Python < 3.11

**File:** `tech_dev_agents/ops_console/services/presence_service.py` — `_parse_output()`, line ~137
**Disposition:** **DEFER** — verify Python floor; fix if < 3.11 is supported

```python
try:
    paused_until = datetime.fromisoformat(lines[1].strip())
except ValueError:
    paused_until = None   # silently treats parse failure as absent pause
```

`datetime.fromisoformat("2026-04-18T14:30:00Z")` raises `ValueError` on Python 3.9 and 3.10 (the
`Z` suffix was not accepted until CPython 3.11). Any agent VM that writes the pause flag with a
`Z`-suffix (common for Go or Node services) silently hits the `except` branch. The agent then
reports `IDLE` when it is actually `RATE_LIMITED` — the precise failure mode this feature exists to
surface.

**Fix if Python < 3.11 is supported:**
```python
ts_str = lines[1].strip().replace("Z", "+00:00")
paused_until = datetime.fromisoformat(ts_str)
```

**Test gap:** Add a test with a `Z`-suffix pause timestamp and assert `RATE_LIMITED` (not `IDLE`).

---

#### M-5 · `STATE_CONFIG` keyed as `Record<string, ...>` — TypeScript exhaustiveness lost

**File:** `frontend/src/components/PresencePanel.tsx`, top of file
**Disposition:** **FIX NOW**

```typescript
const STATE_CONFIG: Record<string, { dot: string; text: string; label: string }> = {
  working: { ... }, idle: { ... }, rate_limited: { ... }, offline: { ... },
};
```

Using `Record<string, ...>` removes TypeScript's compile-time verification that all four
`PresenceState` values are present. A future typo (`"workin"` instead of `"working"`) silently
returns `undefined` at runtime; the `?? STATE_CONFIG.offline` fallback masks the error as an
orange "Offline" bubble instead of a compile error.

**Fix:**
```typescript
import type { PresenceState } from "../types/api";
const STATE_CONFIG: Record<PresenceState, { dot: string; text: string; label: string }> = { ... };
```

TypeScript will now error at compile time if any state is misspelled or missing.

---

#### M-6 · `AgentRecord` has no `ssh_port` attribute — silent `getattr` fallback with no logging

**File:** `tech_dev_agents/ops_console/services/presence_service.py` — `_build_ssh_command()`, line ~113
**Disposition:** **ACCEPT** current default; add logging

```python
port = str(getattr(agent, "ssh_port", 443))
```

`AgentRecord` (a frozen dataclass in `agent_dashboard.py`) has `name`, `host`, `port`, `role`, and
`enabled` — no `ssh_port`. Every production probe therefore silently uses 443 via the `getattr`
fallback. This happens to be correct for the current deployment, but the silent fallback means there
is no ops signal when it is being used, and no type-system enforcement that the field exists.

**Fix (minimal):** Log when the fallback fires:
```python
ssh_port = getattr(agent, "ssh_port", None)
if ssh_port is None:
    logger.debug("Agent %s has no ssh_port; defaulting to 443", agent.name)
    ssh_port = 443
port = str(ssh_port)
```

**Preferred fix:** Add `ssh_port: int = 443` to `AgentRecord` and drop the `getattr`.

---

### Low — Noted, Deferred

| ID | File | Finding | Disposition |
|----|------|---------|-------------|
| L-1 | `presence_service.py` | `len(lines) > 0` after `strip().split("\n")` is always `True` — `[""]` is returned for empty input; guard is dead code and misleads readers | FIX trivially — remove guard |
| L-2 | `presence_service.py` | `_probe(agent)` and `_build_ssh_command(agent)` have no type annotation for `agent`; `agent_service` ctor param also untyped; mypy/pyright cannot validate call sites | DEFER — add `AgentService` / Protocol |
| L-3 | `presence_service.py` | `_parse_output` sets `detail=None` even for `rate_limited` — the parsed `paused_until` timestamp is available and would be useful in the tooltip | DEFER |
| L-4 | `presence_service.py` | `pgrep -f claude_sdk` is a substring match — any process with `claude_sdk` in its argv (debug scripts, log tails) is a false positive | ACCEPT — documented limitation for v1 |
| L-5 | `presence_service.py` | `StrictHostKeyChecking=no` has no inline rationale comment; new readers may flag it without knowing this is an internal-network-only deployment | FIX — one-line comment |
| L-6 | `usePresence.ts` | `staleTime: 30_000` and `refetchInterval: 30_000` are both hardcoded magic numbers — if one is changed without the other, TTL and poll interval fall out of sync | FIX — extract to named constant `PRESENCE_POLL_MS` |
| L-7 | `presence_service.py` | Module docstring `"""SSH-based agent presence probing service — STORY-426."""` — ticket references belong in git log, not source docstrings | FIX — remove ticket tag |
| L-8 | `test_presence_service.py` | `T426-11` patches `asyncio.create_subprocess_exec` without patching `wait_for`; the mock returns synchronously, bypassing the `wait_for` timeout logic it claims to test | DEFER — low risk given H-1 fix |

---

### Nitpick — Noted, Deferred

| ID | File | Finding |
|----|------|---------|
| N-1 | `models/responses.py` vs `types/api.ts` | Backend: `AgentPresenceListResponse`; frontend: `PresenceResponse` — naming drift across the stack |
| N-2 | `types/api.ts` | `AgentPresenceItem` (frontend) vs `AgentPresence` (backend) — inconsistent per-agent record name |
| N-3 | `PresencePanel.tsx` | `PresenceSkeleton` hardcodes `Array.from({ length: 6 })` — layout shift if fleet grows beyond 6 |
| N-4 | `PresencePanel.tsx` | `dot` and `text` in `STATE_CONFIG` always reference the same Tailwind color token; could be `color` with derived class names |
| N-5 | `presence_service.py` | `_parse_output` line-guarding can be simplified: `lines = (stdout.strip().split("\n") + ["", ""])[:3]` removes all `len(lines) > N` guards |
| N-6 | `models/responses.py` | `responses.py` is a growing file with no `__all__` export list; add one to make the API surface explicit |
| N-7 | `test_presence_service.py` | `_NOW = datetime.now(timezone.utc)` at module level; in an extremely slow CI run (> 30s) `_NOW` drifts; move to a `@pytest.fixture` |
| N-8 | `PresencePanel.test.tsx` | Color tests use `[class*="green"]` substring selectors — passes if _any_ parent class contains "green"; brittle under design-token rename |

---

## Test Coverage Assessment

| Area | Tests | Coverage | Gap |
|------|-------|---------|-----|
| `_derive_state()` — pure static | T426-01–05 | ✅ All 4 states, expiry boundary, poller-off override | — |
| `_parse_output()` | T426-06–09 | ✅ All states, malformed/short/empty stdout | `Z`-suffix timestamp (M-4) |
| `_probe()` | T426-10–12 | ⚠️ Both `wait_for` calls patched together — `communicate()` timeout path not isolated | H-1, H-4 |
| `get_all_presence()` | T426-13–16 | ⚠️ Cache hit/miss, exception coercion, disabled agents; no concurrency test | H-3 |
| `_build_ssh_command()` | T426-17 | ⚠️ Flags correct; `ConnectTimeout` asserts literal `"5"` — no regression guard for config | M-1 |
| Route integration | T426-18–22 | ✅ Auth (401/200), schema, all 4 states, cached flag, per-agent fields, detail propagation | 503 for absent service (M-2) |
| `PresencePanel` component | T426-23–30 | ✅ Agent names, state labels, color classes, skeleton, error, cached badge, detail tooltip | `title=""` absent (M-3), empty fleet |
| Concurrency / stampede | — | ❌ Not tested at all | H-3 |
| Subprocess cleanup | — | ❌ Not tested at all | H-1 |

---

## Error Handling Review

| Error Path | Handled? | Notes |
|------------|---------|-------|
| SSH TCP timeout / connection refused | ✅ | `asyncio.TimeoutError` / `OSError` → `OFFLINE` |
| SSH `returncode == 255` (SSH-level connection failure) | ✅ | Returns `OFFLINE` with stderr in `detail` |
| SSH `returncode` 1–254 (remote command error) | ❌ | **H-4** — falls through to `_parse_output`; `detail=None` |
| `asyncio.TimeoutError` during `communicate()` | ⚠️ | Returns `OFFLINE` but leaks subprocess — **H-1** |
| `OSError` (ssh binary missing or permission denied) | ✅ | Caught, returns `OFFLINE` |
| `_probe()` raises unexpected exception | ✅ | `return_exceptions=True` in `gather` → coerced to `OFFLINE` |
| `agent_service.get_registry()` raises | ❌ | Propagates as unformatted 500 — **L-2** |
| `app.state.presence_service` not set | ❌ | `AttributeError` → unformatted 500 — **M-2** |
| Malformed ISO-8601 pause flag | ✅ | `ValueError` caught; treated as absent |
| `Z`-suffix ISO timestamp on Python < 3.11 | ⚠️ | `ValueError` caught but produces wrong state (`IDLE` instead of `RATE_LIMITED`) — **M-4** |
| Empty agent registry (all disabled) | ✅ | Returns valid `{ agents: [] }` response |
| Frontend network error / 4xx / 5xx | ✅ | `isError` → `<PresenceError />` rendered |
| Frontend loading state | ✅ | `isLoading` → `<PresenceSkeleton />` rendered |

---

## Performance Concerns

| Concern | Severity | Notes |
|---------|---------|-------|
| Cache stampede on concurrent requests | **High** | **H-3** — no `asyncio.Lock`; N tabs × TTL expiry = N × 6 SSH connections; fix in H-3 |
| Subprocess resource leak under network failure | **High** | **H-1** — accumulates over time; exhausts FDs under sustained instability; fix in H-1 |
| Effective worst-case probe time > spec budget | Low | Inner `wait_for` = 5s; outer = 6s; total worst-case = 11s; AC6 budget = 10s; borderline |
| `refetchIntervalInBackground: true` fan-out | Acceptable | Keeps cache warm on tab visibility change; tradeoff is correct for this feature |
| Cache TTL vs poll alignment | Acceptable | Max staleness 60s (30s TTL + 30s poll); within spec |

---

## Code Quality Assessment

**What is well done:**
- `_derive_state` is a clean, pure static method — easy to test in isolation
- `_parse_output` is extracted from `_probe` — good testability boundary
- Router ordering comment in `main.py` is accurate and necessary
- `return_exceptions=True` in `asyncio.gather` ensures no single-agent failure crashes the fleet
- All exceptions degrade to `PresenceState.OFFLINE` — the endpoint cannot fail, only degrade
- React component decomposition is appropriate (Panel / Bubble / Skeleton / Error)
- Auth is correctly applied at router level, not per-handler

**Naming / style issues:**
- `agent_service`, `_probe(agent)`, `_build_ssh_command(agent)` — all untyped
- Module docstrings carry story ticket references (`— STORY-426`) — belongs in git log only
- `routes/presence.py` imports `logging` and defines `logger` but never uses it
- `AgentPresenceItem` (TS) vs `AgentPresence` (Python) — cross-stack naming drift
- `PresenceResponse` (TS) vs `AgentPresenceListResponse` (Python) — cross-stack naming drift

---

## Disposition Summary

| Finding | Severity | Disposition | Est. Effort |
|---------|---------|------------|-------------|
| **C-1: `PresencePanel` not mounted in `AgentGrid.tsx`** | Critical | **FIX NOW** | ~5 min |
| H-1: Orphaned subprocess on `communicate()` timeout | High | **FIX NOW** | ~15 min |
| H-2: Cache aliasing — `model_copy` not used | High | **FIX NOW** | ~5 min |
| H-3: Cache stampede — no `asyncio.Lock` | High | **FIX NOW** | ~15 min |
| H-4: SSH exit codes 1–254 unhandled; detail lost | High | **FIX NOW** | ~10 min |
| M-1: `ConnectTimeout` hardcoded, ignores config | Medium | **FIX NOW** | ~5 min |
| M-2: `app.state.presence_service` unguarded; unused `logger` | Medium | **FIX NOW** | ~5 min |
| M-3: `title=""` on non-offline bubbles — a11y regression | Medium | **FIX NOW** | ~5 min |
| M-4: `fromisoformat` Z-suffix (Python < 3.11) | Medium | DEFER — verify Python floor | ~10 min |
| M-5: `STATE_CONFIG: Record<string, ...>` loses TS exhaustiveness | Medium | **FIX NOW** | ~2 min |
| M-6: `getattr(agent, "ssh_port", 443)` — no logging, no field | Medium | FIX — add log | ~5 min |
| L-1 through L-8 | Low | DEFER / ACCEPT as noted | — |
| N-1 through N-8 | Nitpick | DEFER | — |

**Total estimated fix time for blocking items (C-1, H-1–H-4, M-1–M-3, M-5): ~67 minutes.**

---

## Verdict

**CHANGES REQUIRED**

The critical gap (C-1) is a 5-minute mount that makes the entire user-visible deliverable appear.
The four High defects (H-1–H-4) are localized to `_probe` and `get_all_presence` — no architectural
change required. All Medium fixes are small, targeted patches. Once C-1 through M-3 and M-5 are
addressed, this story is approvable. Low and Nitpick items are suitable for a post-merge backlog task.
