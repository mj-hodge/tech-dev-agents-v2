# STORY-860 Phase 3 — Expansion (Architectural Approaches)

Three approaches for restoring poller-side orchestration in `dispatch_poller_v2.py`. Each is evaluated against the 13 ACs and 12 SCs from the seed.

---

## Approach A: Lift-and-Shift v1 Wholesale

### Description

Import `run_sdlc_phases()` from `sdlc_phase_runner.py` directly into the v2 `poll_loop()`, wrapping it with v2 lease/event plumbing. Minimal rewrite — the 723-line function runs as-is with adapter shims.

### Architecture

```
poll_loop() → claim_next() → _write_active_lease()
  ↓
v2_shim_wrapper(claim):
  - Map claim fields to run_sdlc_phases kwargs
  - Monkey-patch _emit_event() to go through transition_claim()
  - Monkey-patch _heartbeat_thread to use v2 send_heartbeat()
  - Call run_sdlc_phases(story_id=..., repo=..., scope=..., ...)
  - Map return tuple to v2 submitted/failed transition
  ↓
transition_claim() → _clear_active_lease()
```

### Files Modified

| File | Change |
|------|--------|
| `dispatch_poller_v2.py` | Add `_run_orchestrated()` wrapper that calls `run_sdlc_phases` with v2 adapters |
| `sdlc_phase_runner.py` | Add module-level hooks for event/heartbeat injection (or monkey-patch from v2) |
| `dispatch_failure_policy.py` | Add phase-scoped classes to POLICY_TABLE |

### Pros

- **Fastest to ship** — v1 orchestration is battle-tested across hundreds of stories.
- **Minimal new code** — adapter shim is ~100 lines; the 723-line runner is reused.
- **v1 quirks become features** — QUESTION.md handling, deliverable verification, ghost-completion guard all come for free.

### Cons

- **Massive coupling** — v2 poller imports v1 runner, creating a dependency that's hard to untangle later.
- **v1 baggage** — Teams notifications, v1 pause API, `.project` file updates, session resume (STORY-511) — all fire in v2 context where they don't belong. Must be disabled or no-op'd.
- **Monkey-patching fragility** — overriding `_emit_event`, `_heartbeat_thread`, `_ensure_branch` from outside the module is brittle; any v1 refactor breaks v2.
- **No phase events in v2 format** — v1 emits `phase_start`/`phase_complete` as Loki log lines, not v2 `dispatch_v2_events` rows. Adapter must intercept and re-emit.
- **Resume mechanism mismatch** — v1 uses deliverable-based skip; AC-7 requires event-based resume. Must bolt on event-resume on top of deliverable-resume.
- **SIGTERM conflict** — v1's `install_shutdown_handler()` installs a competing SIGTERM handler (sets `_shutdown_requested` event + calls v1 pause API). Must be suppressed or overridden.
- **Test surface is unclear** — testing the adapter shim without the v1 runner's internal state is hard; end-to-end only.

### AC Coverage

| AC | Covered | Notes |
|----|---------|-------|
| AC-1 (branch lifecycle) | Partial | v1 `_ensure_branch` runs but doesn't use `claim.metadata.branch` |
| AC-2 (default branch) | Yes | v1 `_resolve_default_branch` reused |
| AC-3 (rework threading) | Partial | v1 uses `rework_of` kwarg but doesn't emit `--rework-of` CLI args |
| AC-4 (phase events) | No | v1 emits Loki lines, not v2 events — adapter needed |
| AC-5 (phase-scoped failures) | No | v1 returns generic reason strings — adapter must map |
| AC-6 (SIGTERM) | Risk | Competing handler from v1; must suppress |
| AC-7 (resume) | Partial | Deliverable-based only; event-based must be bolted on |
| AC-8 (859 supersession) | Manual | Must manually route around 859's pre-step |
| AC-9-13 | Varies | |

### Effort Estimate

- Implementation: 2-3 days (adapter shim + suppression of v1 side-effects)
- Testing: 2 days (must test adapter behavior, not just v1 behavior)
- Risk budget: High (monkey-patching, competing handlers, unclear failure modes)

---

## Approach B: State-Machine Rewrite

### Description

Build a new `V2PhaseOrchestrator` class from scratch. No code shared with v1. The orchestrator is a state machine: states are phases, transitions are SDK results. State is persisted in `dispatch_v2_events`.

### Architecture

```
poll_loop() → claim_next() → _write_active_lease()
  ↓
V2PhaseOrchestrator(claim, session, headers):
  .resolve_phases()     → phase list from scope + seed Phase Path
  .resolve_branch()     → claim.metadata.branch > seed > story-id default
  .setup_workspace()    → git fetch, checkout, clean
  .determine_resume()   → query parent events → start phase
  .run()                → phase loop:
      for phase in phases[resume_idx:]:
          emit phase_started
          _run_sdk(claim, phase_prompt)
          emit phase_completed / phase_failed
          if failed: break
  .finalize()           → emit submitted / failed terminal event
  ↓
transition_claim() → _clear_active_lease()
```

### Module Layout

```
tech_dev_agents/orchestration/
  __init__.py
  v2_phase_runner.py      # V2PhaseOrchestrator class
  git_ops.py              # GitOps helper (fetch, checkout, clean, default-branch)
  branch_resolver.py      # Branch resolution (metadata > seed > default)
  phase_definitions.py    # PHASE_MAP, phase path parsing (lifted from v1)
```

### Files Modified

| File | Change |
|------|--------|
| `dispatch_poller_v2.py` | Replace `_run_sdk()` call with `V2PhaseOrchestrator(claim).run()` |
| `tech_dev_agents/orchestration/v2_phase_runner.py` | **New** — main orchestrator class |
| `tech_dev_agents/orchestration/git_ops.py` | **New** — git operations with typed exceptions |
| `tech_dev_agents/orchestration/branch_resolver.py` | **New** — structured branch resolution |
| `tech_dev_agents/orchestration/phase_definitions.py` | **New** — lifted from v1, cleaned |
| `dispatch_failure_policy.py` | Add phase-scoped classes to POLICY_TABLE |
| `scripts/migrations/059_phase_event_types.sql` | Optional index migration |

### Pros

- **Clean design** — purpose-built for v2 contract. No v1 baggage.
- **Testable in isolation** — each module (GitOps, BranchResolver, PhaseRunner) is independently testable with clear interfaces.
- **Phase events are native** — emitted via `transition_claim()` as first-class v2 events.
- **Resume is native** — event-history query is built into the orchestrator, not bolted on.
- **No SIGTERM conflict** — doesn't touch signal handlers; uses existing v2 handler.
- **Phase-scoped failure classes are native** — orchestrator knows the phase, sets the class directly.
- **Future-proof** — easy to extend with new phases, parallel phases, or DAG execution.

### Cons

- **Slower to ship** — estimated 4-5 days implementation + 2 days testing.
- **Loses v1 battle-testing** — new code means new bugs. v1's QUESTION.md handling, ghost-completion guard, acceptance diff gate, etc. are NOT ported (by design — they're separate concerns).
- **Risk of feature gaps** — v1 has 15+ post-phase checks; the rewrite only implements the 13 ACs. Any v1 behavior not covered by an AC is lost until a follow-up story.
- **More new code to maintain** — 4 new modules vs. 1 adapter shim.

### AC Coverage

| AC | Covered | Notes |
|----|---------|-------|
| AC-1 (branch lifecycle) | Yes | GitOps + BranchResolver, claim.metadata.branch primary |
| AC-2 (default branch) | Yes | GitOps._resolve_default_branch() lifted from v1 |
| AC-3 (rework threading) | Yes | BranchResolver uses claim.metadata, falls back to 859 regex |
| AC-4 (phase events) | Yes | Native v2 events via transition_claim() |
| AC-5 (phase-scoped failures) | Yes | Orchestrator sets class based on phase + SDK output |
| AC-6 (SIGTERM) | Yes | No new handler; existing v2 handler untouched |
| AC-7 (resume) | Yes | Event-history query for parent_job_id |
| AC-8 (859 supersession) | Yes | BranchResolver replaces regex with structured path |
| AC-9-13 | Yes | All covered by design |

### Effort Estimate

- Implementation: 4-5 days (4 new modules + poller integration)
- Testing: 2 days (unit + integration per module)
- Risk budget: Medium (new code, but well-tested and isolated)

---

## Approach C: Hybrid Wrapper (Recommended)

### Description

Build a thin `V2Orchestrator` wrapper that coordinates v2 lease/event plumbing around selective v1 function lifts. The wrapper owns the phase loop, event emission, resume logic, and SIGTERM coordination. Individual operations (branch checkout, default-branch detection, phase path parsing) are lifted from v1 as standalone functions in a new module — NOT imported from `sdlc_phase_runner.py`.

### Architecture

```
poll_loop() → claim_next() → _write_active_lease()
  ↓
_run_orchestrated(claim, session, headers):
  1. Start heartbeat thread (v2: send_heartbeat every 60s)
  2. resolve_branch(claim)           → lifted from v1 _ensure_branch, adapted
  3. setup_workspace(workspace, branch) → git fetch + checkout + clean
  4. determine_resume_phase(claim)    → query parent's phase events
  5. for phase in phases[resume_idx:]:
       if SIGTERM received: break
       emit phase_started via transition_claim()
       claim.current_phase = phase_num
       success, output = _run_sdk(claim, phase_prompt)
       emit phase_completed or phase_failed
       if not success: emit terminal failed; break
  6. if all complete: emit submitted
  7. Stop heartbeat thread
  ↓
_clear_active_lease()
```

### Key Design Decisions

1. **Lift functions, not the module.** Copy `_resolve_default_branch()`, `_ensure_branch()` logic, `PHASE_MAP`, `parse_seed_phase_path()` into a new file. Keep `sdlc_phase_runner.py` untouched (AC constraint).

2. **Phase loop in the poller.** The orchestration loop lives in `dispatch_poller_v2.py` itself (or a closely-coupled module). This avoids the indirection of a separate orchestrator class while keeping the code cohesive with the existing v2 poller.

3. **Branch resolution priority chain:**
   - `claim.metadata.branch` (structured, from Morris dispatch)
   - Seed `## Target Branch` override (parsed from seed.md)
   - `story-{num}/{story_id}` default (derived from story_id)
   - Prompt regex fallback (859 compat, narrow)

4. **Heartbeat thread:** New daemon thread using `send_heartbeat()`, started once per claim, stopped after terminal event. Updates `claim.current_phase` at each phase boundary.

5. **Phase events:** Emitted via `transition_claim()` with event_type `phase_started`/`phase_completed`/`phase_failed`. If the server rejects these (state machine validation), fall back to a direct `_post()` to a new `/event` endpoint — Phase 6 determines which.

6. **Resume:** Query `dispatch_v2_events` for the parent job's last phase event. Skip completed phases. Restart failed/started-but-not-completed phases.

7. **SIGTERM:** No new handler. Existing `_handle_sigterm()` terminates SDK + releases lease. Between phases, check `_sigterm_received` flag and exit the loop gracefully.

8. **SDK invocation:** Reuse existing `_run_sdk()` with modified prompt per phase. For rework: append `--rework-of` / `--target-pr` / `--base-branch` to cmd args.

### Module Layout

```
tech_dev_agents/orchestration/
  __init__.py
  v2_orchestrator.py       # _run_orchestrated() + helpers
  git_ops.py               # GitOps: fetch, checkout, clean, default-branch, branch-resolve
  phase_defs.py            # PHASE_MAP, parse_seed_phase_path (lifted from v1)
```

### Files Modified

| File | Change | Size |
|------|--------|------|
| `dispatch_poller_v2.py` | Replace bare `_run_sdk()` in poll_loop with `_run_orchestrated()` call; add heartbeat thread; extend `_ActiveClaim` with metadata fields | ~80 lines changed |
| `tech_dev_agents/orchestration/v2_orchestrator.py` | **New** — `_run_orchestrated()`, branch resolution, resume query, phase loop | ~300 lines |
| `tech_dev_agents/orchestration/git_ops.py` | **New** — git operations lifted from v1, typed exceptions | ~150 lines |
| `tech_dev_agents/orchestration/phase_defs.py` | **New** — phase definitions + path parsing lifted from v1 | ~100 lines |
| `dispatch_failure_policy.py` | Add phase-scoped classes to POLICY_TABLE | ~30 lines |
| `scripts/migrations/059_phase_event_types.sql` | Optional index on `(job_id, event_type, (event_data->>'phase'))` | ~10 lines |
| Tests (3 new files) | Full SDLC simulation, branch lifecycle, resume, 859 compat | ~400 lines |

### Pros

- **Right-sized** — new code only where v2 needs differ from v1. Reuses proven v1 logic for git operations and phase definitions.
- **No v1 coupling** — functions are copied (not imported), so v1 runner changes don't affect v2.
- **All ACs covered** — phase events, resume, SIGTERM, branch lifecycle, rework threading all addressed natively.
- **Testable** — git_ops and phase_defs are independently testable; orchestrator is testable with mocked SDK.
- **Minimal blast radius** — `dispatch_poller_v2.py` changes are additive (new function + call-site change in poll_loop); existing `_run_sdk`, `_failure_event_data`, `poll_loop` structure preserved.
- **Heartbeat fix included** — the heartbeat thread is a mandatory part of the wrapper, fixing the current starvation bug.

### Cons

- **Code duplication** — git_ops and phase_defs duplicate v1 code. Future v1 improvements don't propagate to v2 (but v1 is effectively frozen — no new features planned).
- **Three new modules** — more files to maintain than Approach A.
- **v1 post-phase checks not ported** — QUESTION.md handling, ghost-completion guard, acceptance diff gate, etc. are dropped. Must be re-added in follow-up stories if needed.

### AC Coverage

| AC | Covered | Notes |
|----|---------|-------|
| AC-1 (branch lifecycle) | Yes | git_ops.ensure_branch() with claim.metadata.branch |
| AC-2 (default branch) | Yes | git_ops.resolve_default_branch() lifted from v1 |
| AC-3 (rework threading) | Yes | Branch resolver uses claim.metadata; 859 regex as fallback |
| AC-4 (phase events) | Yes | transition_claim() with phase_started/completed/failed |
| AC-5 (phase-scoped failures) | Yes | Orchestrator sets class from phase context |
| AC-6 (SIGTERM) | Yes | Uses existing handler; checks _sigterm_received between phases |
| AC-7 (resume) | Yes | Event-history query for parent_job_id |
| AC-8 (859 supersession) | Yes | Structured resolver replaces regex; 859 tests ported |
| AC-9 (canary) | Yes | Canary set defined in Phase 6 |
| AC-10 (event_data.phase) | Yes | All phase events include phase field |
| AC-11 (heartbeat) | Yes | New heartbeat thread covers all phases |
| AC-12 (logging) | Yes | [ORCH] prefix at phase boundaries |
| AC-13 (backward compat) | Yes | Single-shot stories still work (1-phase path) |

### Effort Estimate

- Implementation: 3-4 days
- Testing: 1.5 days (modules are independently testable)
- Risk budget: Low-Medium (proven v1 logic in new context)

---

## Comparison Matrix

| Criterion | A (Lift Wholesale) | B (Full Rewrite) | C (Hybrid) |
|-----------|-------------------|-------------------|------------|
| **AC coverage** | 8/13 native, 5 need adapters | 13/13 | 13/13 |
| **Implementation effort** | 2-3 days | 4-5 days | 3-4 days |
| **Test effort** | 2 days (hard to isolate) | 2 days (clean isolation) | 1.5 days (good isolation) |
| **Blast radius** | High (monkey-patching v1) | Medium (all new code) | Low (additive to v2) |
| **SIGTERM risk** | High (competing handlers) | None | None |
| **Heartbeat fix** | Must be shim'd | Native | Native |
| **Resume mechanism** | Bolted on (event + deliverable) | Native (event only) | Native (event + deliverable fallback) |
| **Maintainability** | Low (adapter + v1 coupling) | High (clean modules) | Medium-High (some duplication) |
| **v1 feature coverage** | High (all v1 behaviors) | Low (ACs only) | Medium (ACs + key v1 patterns) |
| **Rollback safety** | Risky (v1 import path) | Safe (feature flag) | Safe (feature flag) |

---

## Recommendation

**Approach C (Hybrid Wrapper)** is the recommended path forward.

**Rationale:**
1. Covers all 13 ACs natively without adapter shims or monkey-patching.
2. Reuses proven v1 git logic without coupling to v1's module.
3. Fixes the heartbeat starvation bug as a mandatory part of the design.
4. Lowest blast radius — additive changes to `dispatch_poller_v2.py`.
5. Best effort-to-coverage ratio (3-4 days, 13/13 ACs).
6. Approach A's SIGTERM conflict and monkey-patching fragility are dealbreakers for a production poller.
7. Approach B's full rewrite is sound but slower than needed; the v1 git logic is well-tested and worth lifting.

Phase 4 (Analysis) should score all three approaches on the formal criteria and confirm this recommendation.
