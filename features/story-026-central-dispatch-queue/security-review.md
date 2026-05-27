# Security Review: Central Dispatch Queue

> **Phase:** 6b — Security Review
> **Story:** STORY-026 — Central Dispatch Queue
> **Date:** 2026-04-08
> **Scope:** Medium
> **Reviewer:** Security Review Agent
> **Inputs:** `seed.md`, `feature-spec.md`, `auth.py`

---

## 1. Authentication & Authorization

### 1.1 Endpoint Protection

All dispatch endpoints are protected by `require_auth` via router-level dependency:

```python
router = APIRouter(dependencies=[Depends(require_auth)])
```

This matches the pattern used by every other route module (fleet, agents, alerts, context, messages). No endpoint is unprotected.

**Finding:** PASS. All five endpoints (POST enqueue, GET queue, GET next, POST claim, DELETE cancel) inherit the router-level auth guard.

### 1.2 Auth Mechanisms

Two auth paths exist, both enforced:

| Path | Mechanism | Used By |
|------|-----------|---------|
| Bearer JWT | Entra ID SSO (RS256, JWKS validation, audience + issuer checks, expiry) | Dashboard (Mark via browser) |
| API key | `X-API-Key` header, constant-time comparison via `validate_api_key` | Agent polling (Dan, Derrick) |

**Finding:** PASS. Both paths validate credentials before any dispatch logic runs.

### 1.3 Agent Identity Validation

The `ClaimRequest.agent_name` field is constrained to `^[a-zA-Z0-9_-]+$`, preventing injection via the agent name. However, there is no server-side verification that the `agent_name` in the claim body matches the authenticated identity. An agent authenticating as "dan" could claim with `agent_name: "derrick"`.

**Risk:** LOW. The fleet has 2 trusted agents on an internal network. Both use the same shared API key. Agent impersonation has no practical attack value in this environment since both agents are equally privileged.

**Recommendation (deferred):** If the fleet grows beyond trusted agents, bind `agent_name` to the authenticated API key or JWT subject claim to prevent identity spoofing.

### 1.4 Authorization Granularity

There is no role-based distinction between "enqueue" (Mark) and "poll/claim" (agents). Any authenticated caller can enqueue or claim.

**Risk:** LOW. Acceptable for 2-agent internal fleet. An agent could theoretically enqueue work for itself, but this is a workflow convenience, not a security hole.

**Recommendation (deferred):** Add role scoping if the system opens to non-admin users.

---

## 2. Input Validation

### 2.1 Field-Level Validation

All request models use Pydantic `Field` constraints:

| Field | Validation | Verdict |
|-------|-----------|---------|
| `story_id` | `^STORY-\d+$` regex | PASS — prevents path traversal and injection |
| `repo` | `min_length=1, max_length=200` | PASS |
| `scope` | `^(small|medium|large)$` enum regex | PASS |
| `prompt` | `min_length=1, max_length=5000` | PASS — bounded |
| `enqueued_by` | `min_length=1, max_length=100` | PASS |
| `agent_name` (claim) | `^[a-zA-Z0-9_-]+$` | PASS — alphanumeric only |

**Finding:** PASS. Pydantic enforces validation before route handlers execute. Malformed requests return 422 automatically.

### 2.2 Path Parameter Validation

The `story_id` in `/dispatch/claim/{story_id}` and `/dispatch/queue/{story_id}` is a bare `str` path parameter — FastAPI does not apply the Pydantic regex from `DispatchRequest` to path params automatically.

**Risk:** MEDIUM-LOW. A crafted `story_id` like `../../etc/passwd` would not cause file path traversal because `story_id` is only used as a dictionary key lookup against in-memory queue data (list comprehension match), never interpolated into file paths. However, the lack of validation means the 404 error message would echo arbitrary input back to the caller.

**Recommendation:** Add a `Path(pattern=r"^STORY-\d+$")` constraint to the `story_id` path parameter in `claim_story` and `cancel_story`, or validate with a regex guard at the top of each handler. This is a LOW-effort fix.

### 2.3 Prompt Injection

The `prompt` field (up to 5,000 chars) is stored verbatim in the JSON queue and later passed to `claude_sdk_tool.py` for execution. This is the prompt that drives the agent's SDLC work.

**Risk:** LOW. The only person who can enqueue prompts is an authenticated user (Mark). The prompt is consumed by the same agent system that already trusts user-provided prompts via Teams. There is no additional attack surface compared to the existing direct-dispatch flow.

**Finding:** ACCEPTABLE. The prompt field is not rendered as HTML (no XSS risk) and is not interpolated into shell commands (the SDK tool receives it as a structured argument). No additional sanitization is needed.

---

## 3. File System Security

### 3.1 Queue File Permissions

The queue file is created at `/opt/ops-console/dispatch-queue.json`. The `DispatchQueueService.__init__` calls `mkdir(parents=True, exist_ok=True)` on the parent directory.

**Risk:** LOW. The file inherits the process umask. If the FastAPI process runs as a dedicated service user (standard for systemd services), the file is readable/writable only by that user.

**Recommendation:** Explicitly set file permissions on creation. Add `os.chmod(tmp_path, 0o600)` before `os.replace()` in `_atomic_write` to ensure the queue file is not world-readable. This matters if the prompt field ever contains sensitive instructions.

### 3.2 Path Traversal via story_id

As noted in 2.2, the `story_id` is never used to construct file paths. The queue service uses a single hardcoded path (`self._path`). The `story_id` is only matched against in-memory list entries. No path traversal is possible through this vector.

**Finding:** PASS.

### 3.3 Atomic Write Safety

The `_atomic_write` method uses `tempfile.mkstemp` + `os.replace`, which is the correct pattern for atomic file updates on Linux. The temp file is created in the same directory as the target (same filesystem), ensuring `os.replace` is atomic.

Error handling cleans up the temp file on failure via `os.unlink` in the exception handler.

**Finding:** PASS.

### 3.4 Temp File Cleanup

If the process crashes between `mkstemp` and `os.replace`, orphan `.tmp` files may accumulate in `/opt/ops-console/`. This is a minor disk hygiene issue, not a security risk.

**Recommendation (LOW priority):** Add cleanup of stale `.tmp` files in the service `__init__` or via a periodic task.

---

## 4. Race Conditions

### 4.1 Double-Claim Prevention

The claim flow is: `load()` (shared lock) -> check pending list -> `pop()` + `append()` -> `save()` (exclusive lock). The load and save are separate lock acquisitions.

**Risk:** MEDIUM-LOW. Two agents could both `load()` the queue, both see `STORY-094` as pending, and both attempt to claim it. The `save()` exclusive lock serializes the writes, but the second agent's save would succeed because it loaded stale state — the second agent's `pop()` operates on its own copy of the list, and the save would overwrite the first agent's claim.

**Mitigating factors:**
- Only 2 agents poll every 60 seconds. The probability of simultaneous claim within the same second is very low.
- The 60s polling interval means claims are staggered by tens of seconds in practice.
- The stale claim recovery (5-minute timeout) acts as a safety net.

**Recommendation:** Wrap the load-check-modify-save sequence in a single exclusive lock acquisition (hold `LOCK_EX` from load through save) to make the claim operation truly atomic. This is a LOW-effort fix:

```python
def load_and_save(self, mutator_fn):
    """Load, mutate, and save under a single exclusive lock."""
    with open(self._path, "r+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            data = json.load(f)
            result = mutator_fn(data)
            f.seek(0)
            f.truncate()
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
    return result
```

### 4.2 Concurrent Enqueue + Claim

Similar TOCTOU risk as 4.1 — an enqueue and a claim happening simultaneously could lose one operation because both read before either writes. Same mitigation applies: single exclusive lock for all mutations.

**Risk:** LOW (same probability argument as 4.1).

### 4.3 Stale Claim Recovery Race

The recovery loop runs every 60 seconds and could race with an agent that is about to start work on a just-recovered story. The agent would have already received the claim confirmation but the story would be moved back to pending.

**Risk:** LOW. The 5-minute stale window is large enough that a healthy agent starts work within seconds of claiming.

---

## 5. Denial of Service

### 5.1 Queue Flooding

The queue has a hard cap of 50 pending items (`MAX_PENDING = 50`). Exceeding this returns 422. Combined with authentication requirements, an attacker would need valid credentials to flood the queue.

**Finding:** PASS. The cap is appropriate for the use case.

### 5.2 Large Payloads

The `prompt` field is capped at 5,000 characters. The `repo` field at 200 characters. Total maximum request body size is well under 10 KB. With 50 queue items, the JSON file maxes out at roughly 500 KB.

**Finding:** PASS. No risk of disk exhaustion or memory pressure.

### 5.3 Polling Load

Agents poll every 60 seconds. With 2 agents, this is 2 requests/minute — trivial load. Even if misconfigured to 1-second intervals, FastAPI handles this without issue.

**Finding:** PASS.

### 5.4 fcntl Lock Starvation

`fcntl.flock` with `LOCK_EX` blocks indefinitely. A stuck writer could block all readers and other writers.

**Risk:** LOW. The critical section (JSON serialize + write + fsync) completes in milliseconds for a <500 KB file. No network I/O or external calls within the lock.

---

## 6. Data Exposure

### 6.1 Prompt Field Sensitivity

The `prompt` field may contain SDLC instructions, story context, or references to internal systems. It is:
- Stored in plaintext in `dispatch-queue.json`
- Returned in full via `GET /dispatch/queue` and `GET /dispatch/next`
- Visible to any authenticated user

**Risk:** LOW. All authenticated users (Mark and the agents) are trusted internal actors who would see these prompts anyway. The file is on a VM accessible only via SSH.

**Recommendation (deferred):** If non-admin users ever gain dashboard access, redact or truncate the `prompt` field in list responses, returning full prompt only on claim.

### 6.2 Error Message Information Leakage

Error messages include the `story_id` value (e.g., "STORY-094 already claimed"). This is acceptable for authenticated internal APIs but should be reviewed if the API is ever exposed externally.

**Finding:** ACCEPTABLE for current threat model.

### 6.3 Log Exposure

The `recover_stale_claims` function logs `story_id` and `claimed_by` at WARNING level. The route handlers log at INFO level via standard FastAPI middleware. No prompt content is logged.

**Finding:** PASS.

---

## 7. Threat Model

| # | Threat | Likelihood | Impact | Mitigation | Status |
|---|--------|-----------|--------|------------|--------|
| T1 | Unauthenticated access to dispatch endpoints | Very Low | High | `require_auth` dependency on all routes; Entra ID JWT + API key validation | MITIGATED |
| T2 | Agent identity spoofing (claim as another agent) | Very Low | Low | `agent_name` regex validation; shared API key means all agents are equally trusted | ACCEPTED |
| T3 | Path traversal via story_id path parameter | Very Low | None | story_id is only used for in-memory list lookup, never in file paths | MITIGATED |
| T4 | Prompt injection / XSS via prompt field | Very Low | Low | Prompt is not rendered as HTML; consumed only by trusted agent SDK; Pydantic enforces max length | MITIGATED |
| T5 | Double-claim race condition | Low | Low | fcntl locks serialize writes; 60s poll interval makes collision unlikely; stale recovery as safety net | ACCEPTED (see recommendation in 4.1) |
| T6 | Queue flooding / DoS | Very Low | Low | Auth required; MAX_PENDING=50 cap; field length limits | MITIGATED |
| T7 | Queue file corruption | Very Low | Medium | Atomic write (mkstemp + os.replace); load() returns empty queue on parse error; fsync ensures durability | MITIGATED |
| T8 | Sensitive data in prompt field exposed | Low | Low | All API consumers are authenticated internal actors; file on SSH-only VM | ACCEPTED |
| T9 | Temp file accumulation on disk | Very Low | Very Low | Exception handler calls os.unlink; minor hygiene issue only | ACCEPTED |
| T10 | fcntl lock starvation | Very Low | Medium | Critical section is sub-millisecond; no blocking I/O under lock | ACCEPTED |

---

## 8. Summary of Recommendations

| # | Recommendation | Priority | Effort | Blocking? |
|---|---------------|----------|--------|-----------|
| R1 | Add `Path(pattern=r"^STORY-\d+$")` to `story_id` path parameters in claim and cancel routes | Low | 5 min | No |
| R2 | Set `os.chmod(tmp_path, 0o600)` in `_atomic_write` before `os.replace` | Low | 2 min | No |
| R3 | Use single exclusive lock for load-modify-save in claim/cancel operations | Low | 30 min | No |
| R4 | Bind `agent_name` to authenticated identity when fleet grows | Deferred | 1 hr | No |
| R5 | Redact prompt field in list responses if non-admin access is added | Deferred | 30 min | No |
| R6 | Clean up orphan `.tmp` files on service startup | Deferred | 15 min | No |

None of the recommendations are blocking for the current deployment. R1-R3 are low-effort hardening items that should be picked up during Phase 8 implementation.

---

## Verdict

**APPROVED WITH CONDITIONS**

The central dispatch queue has a small attack surface: internal network only, 2 trusted agents, all endpoints authenticated via Entra ID SSO or API key. Input validation is thorough via Pydantic models. File operations use correct atomic write patterns.

**Conditions for approval:**
1. **R1 (path parameter validation):** Add regex constraint to `story_id` path parameters during Phase 8 implementation. This is a 5-minute fix that closes the only input validation gap.
2. **R3 (atomic claim operation):** Strongly recommended to wrap load-modify-save in a single exclusive lock during Phase 8. While the race window is narrow at current scale, this is the correct pattern and prevents a class of bugs as the fleet grows.

R2 (file permissions) is recommended but not a condition — the current VM access controls are sufficient.
