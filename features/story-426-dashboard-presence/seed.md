# Seed: Real-Time Agent Presence on Dashboard — PR #55 Remediation

> Phase 1 — Concept & Seed (Remediation Pass)
> Date: 2026-04-18
> Scope: Small
> Phase path: 1 → 7 → 8 → Done

---

## Problem Statement

STORY-426 implemented real-time agent presence on the ops console dashboard (SSH-based probing, 4-state model, React widget). The implementation passed internal code review (Phase 8b) and was submitted as PR #55. Morris's external review identified four must-fix issues: SSH credential exposure in process args, missing command injection validation, unbounded cache growth potential, and incomplete SDLC deliverables. These issues must be resolved before the PR can merge.

### Original Feature (Already Implemented)

The ops console fleet status page lacked visibility into agent operational state. STORY-426 added:
- `GET /api/agents/presence` endpoint with SSH fan-out probing
- Four-state model: `working`, `idle`, `rate_limited`, `offline`
- `PresenceService` with TTL-cached concurrent SSH probes
- React `PresencePanel` component with 30s auto-refresh
- 46 tests across unit, integration, and component layers

### What This Remediation Addresses

PR #55 review feedback from Morris (4 must-fix, 3 recommended):

1. **SSH credential exposure** — `PresenceService` could expose SSH credentials via command-line args visible in `/proc/{pid}/cmdline`. Must use SSH key auth exclusively or pass credentials via environment variable.
2. **Command injection in agent_name** — The `_build_ssh_command` method interpolates `agent.host` into SSH commands without validation. A malicious or corrupted registry entry could inject shell commands.
3. **Unbounded cache growth** — The `_cache` dict in `PresenceService` lacks TTL eviction enforcement or size bounds. Should use `functools.lru_cache` or add explicit eviction.
4. **SDLC deliverable gaps** — Medium-scope story requires `feature-spec.md`, `security-review.md`, `test-design.md`, `predeploy-gate.md`. Some exist but dispatch compliance requires `seed.md` and `test-design.md` at minimum.

## Target User

Operations engineers and the PR reviewer (Morris) who need confidence that the presence feature is secure and production-ready.

---

## Acceptance Criteria

- [ ] AC1: SSH probe uses key-based authentication exclusively — no passwords or credentials appear in process command-line arguments (`/proc/{pid}/cmdline` is clean)
- [ ] AC2: `agent.host` and `agent.name` values are validated before interpolation into SSH commands — reject any value containing shell metacharacters (`;`, `|`, `&`, `` ` ``, `$`, `(`, `)`, newlines)
- [ ] AC3: The presence cache has bounded growth — either use `functools.lru_cache` with `maxsize` or the existing `TTLCache` with explicit eviction on every access
- [ ] AC4: All existing tests continue to pass (no regressions)
- [ ] AC5: New tests cover the input validation (reject malicious hostnames) and cache eviction behavior
- [ ] AC6: `seed.md` and `test-design.md` exist in `features/story-426-dashboard-presence/`
- [ ] AC7: Orphaned SSH subprocess on timeout is killed and reaped (zombie process prevention)
- [ ] AC8: Cache aliasing fixed — cached response is not mutated in-place (`model_copy` instead of direct mutation)

---

## Technical Notes

### Affected Files

| File | Change |
|------|--------|
| `tech_dev_agents/ops_console/services/presence_service.py` | Fix SSH auth, add input validation, fix cache, kill orphaned processes, fix cache aliasing |
| `tests/ops_console/test_presence_service.py` | Add tests for validation rejects, cache eviction, zombie cleanup |
| `features/story-426-dashboard-presence/seed.md` | This file (remediation seed) |
| `features/story-426-dashboard-presence/test-design.md` | Update with remediation test cases |

### MUST-FIX 1: SSH Credential Exposure

**Current:** `_build_ssh_command` uses `BatchMode=yes` which prevents password prompts, but the design should explicitly enforce key-only auth.

**Fix:** Add `-o PasswordAuthentication=no` and `-o PreferredAuthentications=publickey` to the SSH command. These flags ensure SSH never attempts password auth, eliminating any possibility of credentials leaking to `/proc`. The ops console already has SSH keys configured for all agent VMs.

### MUST-FIX 2: Command Injection Prevention

**Current:** `agent.host` is interpolated into the SSH user@host argument and `agent.name` flows through unchecked.

**Fix:** Add a `_validate_ssh_target(value: str)` method that rejects any string containing shell metacharacters or whitespace. Call it on `agent.host` before building the SSH command. If validation fails, return `offline` with detail explaining the invalid hostname.

### MUST-FIX 3: Bounded Cache

**Current:** `TTLCache` is used with a single key `"all_presence"`. The cache is actually bounded (one entry only), but the reviewer's concern about growth is valid as a defensive practice.

**Fix:** Add `maxsize` parameter to `TTLCache` initialization or switch to `cachetools.TTLCache` with explicit `maxsize=1`. Add explicit stale-entry eviction on `get()`.

### MUST-FIX 4 (from Code Review): Zombie SSH Processes

**Current:** When `proc.communicate()` times out, the SSH process is never killed.

**Fix:** Wrap the timeout handler in a `finally` block that calls `proc.kill()` and `proc.wait()` to prevent zombie accumulation.

### MUST-FIX 5 (from Code Review): Cache Aliasing

**Current:** `cached.cached = True` mutates the stored cache object in-place.

**Fix:** Use `cached.model_copy(update={"cached": True})` to return a new object.

---

## Dependencies

- Existing SSH key infrastructure (ops console → agent VMs)
- Existing `TTLCache` implementation in `cache.py`
- Existing test infrastructure (pytest, pytest-asyncio)

## Out of Scope

- Rewriting the SSH probe approach (paramiko, etc.) — keep subprocess-based design
- Adding structured logging (recommended but not must-fix)
- Graceful SSH timeout degradation (recommended but not must-fix)
- Frontend build documentation (recommended but not must-fix)
- PresencePanel integration into AgentGrid (separate concern, tracked in C-1)
- Cache stampede prevention with asyncio.Lock (H-3 from code review — deferred)

---

## Scope Confirmation: Small

**Justification:** This is a focused remediation of 4-5 specific issues in a single service file, with corresponding test additions. No new endpoints, no new components, no architectural changes. All fixes are localized to `presence_service.py` and its test file. Estimated at ~50-80 lines of changes plus ~30 lines of new tests.

## Risk Assessment

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Input validation too restrictive (rejects valid hostnames) | Low | Allowlist approach: only reject dangerous chars, not restrict to specific patterns |
| Cache eviction changes cause flaky tests | Low | Test cache behavior explicitly with time mocking |
| SSH flag changes break connectivity | Low | `BatchMode=yes` already prevents password auth; new flags are additive reinforcement |

## Next Phase

Phase 7 — Test Design (remediation test cases)
