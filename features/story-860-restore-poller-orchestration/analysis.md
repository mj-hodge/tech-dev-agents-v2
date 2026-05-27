# STORY-860 Phase 4 — Analysis

## Scoring Framework

Each approach is scored 1-5 on six criteria (equal weight). Total = 30 max.

| Score | Meaning |
|-------|---------|
| 5 | Excellent — no concerns |
| 4 | Good — minor concerns, manageable |
| 3 | Adequate — notable concerns, workarounds needed |
| 2 | Weak — significant concerns, high risk |
| 1 | Unacceptable — dealbreaker issues |

---

## Criterion 1: Technical Soundness

*Does the approach correctly satisfy all 13 ACs without architectural contradictions?*

### Approach A (Lift Wholesale): 2/5

- AC-4 (phase events) requires adapter shim to intercept v1's `_emit_event()` and re-emit as v2 `transition_claim()`. This is a semantic mismatch — v1 events are fire-and-forget Loki lines; v2 events are state-machine transitions. Adapter must handle the case where `transition_claim()` returns 409 (stale lease) mid-phase, which v1 never encounters.
- AC-6 (SIGTERM) is the critical gap. v1's `install_shutdown_handler()` (line 2888) installs a handler that calls v1's `/api/dispatch/pause/{story_id}`. v2's `_handle_sigterm()` calls `release_claim()`. Running both creates a race: which handler fires? Which API call wins? Suppressing v1's handler means `_shutdown_requested` is never set, so `_run_phase_sdk()` never checks it. The workaround (monkey-patch v1's shutdown handler to set `_sigterm_received` instead) is fragile.
- AC-7 (resume) requires bolting event-based resume onto v1's deliverable-based resume. Two resume mechanisms running in parallel creates ambiguity: what if events say "resume from phase 4" but deliverables say "phase 4 already done"? Must define precedence.

### Approach B (Full Rewrite): 5/5

- All 13 ACs are addressed natively in the design. No adapter shims, no competing mechanisms.
- Event emission, resume, SIGTERM, branch lifecycle are all built for v2 contract from the ground up.
- Risk: new code may have bugs, but the architecture is sound.

### Approach C (Hybrid): 5/5

- All 13 ACs addressed natively. Git operations are proven v1 logic; orchestration loop is purpose-built for v2.
- Resume uses event-history primary + deliverable secondary (consistent precedence: events win).
- SIGTERM uses existing v2 handler; orchestrator checks `_sigterm_received` between phases.
- No architectural contradictions.

---

## Criterion 2: Blast Radius

*How much existing code is touched? How likely are regressions in unrelated paths?*

### Approach A: 2/5

- Imports `sdlc_phase_runner.py` functions at runtime, creating a transitive dependency on all its imports (`project_file`, Teams notification helpers, v1 dispatch API helpers).
- Monkey-patching v1 module-level state (`_shutdown_requested`, `_emit_event`) affects any other code that imports `sdlc_phase_runner.py` in the same process.
- If v1 poller and v2 poller ever coexist (during rollback), the monkey-patches could leak across.

### Approach B: 4/5

- All new modules — no existing code touched except `dispatch_poller_v2.py`'s `poll_loop()`.
- `dispatch_failure_policy.py` gets new POLICY_TABLE entries (additive, no existing behavior changed).
- Risk: `dispatch_poller_v2.py` changes are concentrated in `poll_loop()` — a single function that's the entire v2 control flow. Must be careful with the diff.

### Approach C: 4/5

- Same as B for new modules.
- `dispatch_poller_v2.py` changes are slightly smaller than B (reuses existing `_run_sdk()` rather than replacing it).
- `_ActiveClaim` extended with new fields (backward-compatible: all new fields have defaults).
- `sdlc_phase_runner.py` untouched (constraint honored).

---

## Criterion 3: Test Surface

*How testable is the approach? Can each component be tested in isolation?*

### Approach A: 2/5

- The adapter shim wraps v1's `run_sdlc_phases()`, which is a 723-line function with 15+ internal concerns. Testing the shim without running the full function requires mocking most of v1's internals — effectively testing the mocks, not the behavior.
- Integration tests must cover the shim + v1 function + v2 lease plumbing — a large surface.
- v1's internal functions (`_ensure_branch`, `_run_phase_sdk`, `_check_for_questions`) are not designed for external testing; they use module-level state.

### Approach B: 5/5

- Each module (GitOps, BranchResolver, PhaseRunner) has a clear interface and can be unit-tested independently.
- `V2PhaseOrchestrator` can be tested with a mocked SDK and mocked `transition_claim()`.
- GitOps can be tested with real git fixtures (tmpdir + git init).
- Phase definitions are pure data — trivially testable.

### Approach C: 4/5

- GitOps and phase_defs modules are independently testable (same as B).
- `_run_orchestrated()` is testable with mocked SDK + mocked API calls.
- Slightly less clean than B because the orchestrator function lives closer to the poller (may need to mock more poller internals).
- Deliverable-based fallback resume adds a test path that doesn't exist in B.

---

## Criterion 4: Deploy Risk

*What's the rollback story? What happens if the approach ships with a bug?*

### Approach A: 2/5

- Rollback = remove the v1 import path from v2 poller. But if the monkey-patches leaked into v1's state, the v1 fallback may also be affected.
- If the adapter shim has a bug in SIGTERM handling, the failure mode is orphaned leases + orphaned SDK processes — the exact problem STORY-857 fixed.
- Deploy via `push-code.sh` triggers SIGTERM on all running pollers. If the new code has a SIGTERM bug, the deploy itself causes the failure.

### Approach B: 4/5

- Rollback = revert commit, push. v2 poller falls back to single-shot `_run_sdk()` (pre-860 behavior). No v1 contamination.
- Feature flag possible: check an env var to enable/disable orchestration.
- Risk: if the new orchestrator has a heartbeat bug, leases may expire. But the failure mode is clean — expired leases are swept, stories are requeued.

### Approach C: 4/5

- Same rollback as B.
- Feature flag possible: `DISPATCH_V2_ORCHESTRATION=1` env var to gate `_run_orchestrated()` vs bare `_run_sdk()`.
- Heartbeat thread is new code, but it's a simple daemon thread calling an existing function (`send_heartbeat()`). Failure mode: heartbeat doesn't fire → lease expires → job requeued. Not catastrophic.

---

## Criterion 5: Effort / Timeline

*How long to implement + test to GREEN?*

### Approach A: 3/5

- Implementation: 2-3 days (adapter shim is conceptually simple but debugging the monkey-patching is time-consuming).
- Testing: 2 days (integration-heavy; isolation is poor).
- Total: 4-5 days.
- Hidden cost: debugging competing SIGTERM handlers in a production-like environment.

### Approach B: 3/5

- Implementation: 4-5 days (4 new modules from scratch).
- Testing: 2 days (excellent isolation offsets the volume).
- Total: 6-7 days.
- Exceeds the seed's "3-5 days" timeline target.

### Approach C: 4/5

- Implementation: 3-4 days (lift proven v1 logic; write new orchestration loop).
- Testing: 1.5 days (good isolation; reusable fixtures).
- Total: 4.5-5.5 days.
- Within the seed's "3-5 days" target (optimistic) or slightly over.

---

## Criterion 6: Maintainability

*How easy is it to extend, debug, and evolve after initial ship?*

### Approach A: 1/5

- Two codepaths (v1 runner + v2 adapter) must be maintained in sync. Any v1 change potentially breaks v2.
- Monkey-patching is invisible to code search — `git grep _emit_event` finds the v1 definition but not the v2 override.
- New developers must understand both v1 and v2 contracts to debug issues.
- Technical debt accumulates: every new v1 feature (QUESTION.md changes, new gates, etc.) requires a corresponding adapter update.

### Approach B: 5/5

- Clean modules with clear interfaces. Each module's responsibility is obvious.
- No coupling to v1. v1 can be deprecated/deleted independently.
- Standard Python patterns (classes, typed exceptions, dependency injection).

### Approach C: 4/5

- Lifted v1 functions are standalone — no coupling to v1 module.
- But code duplication means bug fixes in git operations must be applied to both v1 and v2 copies.
- In practice, v1 is frozen (no new features planned), so duplication cost is low.
- Orchestration loop in v2 poller is a single function — easy to trace.

---

## Score Summary

| Criterion | Weight | A (Lift) | B (Rewrite) | C (Hybrid) |
|-----------|--------|----------|-------------|------------|
| Technical Soundness | 1 | 2 | 5 | 5 |
| Blast Radius | 1 | 2 | 4 | 4 |
| Test Surface | 1 | 2 | 5 | 4 |
| Deploy Risk | 1 | 2 | 4 | 4 |
| Effort / Timeline | 1 | 3 | 3 | 4 |
| Maintainability | 1 | 1 | 5 | 4 |
| **Total** | | **12/30** | **26/30** | **25/30** |

---

## Analysis Conclusion

**Approach A is eliminated.** The SIGTERM conflict (AC-6), monkey-patching fragility, and poor test surface make it unsuitable for a production dispatch poller. Score: 12/30.

**Approaches B and C are both strong.** B scores 1 point higher on test surface and maintainability (cleaner modules). C scores 1 point higher on effort/timeline (lifts proven code). The difference is marginal.

**The tiebreaker is timeline.** The seed specifies "3-5 days; ship after STORY-859 is merged + stable for >= 24h." Approach B's 6-7 day estimate exceeds this. Approach C's 4.5-5.5 day estimate is within tolerance.

**Recommendation: Approach C (Hybrid Wrapper).**

### Open Questions Resolved

| Question | Answer | Rationale |
|----------|--------|-----------|
| Q1: Lift or rewrite? | Hybrid | Lift git ops + phase defs; rewrite orchestration for v2 |
| Q2: Resume in events or sidecar? | Events (primary) + deliverable (fallback) | Single source of truth; deliverable fallback for resilience |
| Q3: Phase events as column or strings? | event_type strings + JSONB payload | Zero-DDL; optional index for query perf |
| Q4: Single heartbeat or per-phase? | Single thread, all phases | Simpler; proven in v1 |
| Q5: Branch source? | claim.metadata > seed > story-id > regex | Structured first; regex last-resort |
| Q6: Default branch: per-claim or cache? | Per-claim | < 5ms local operation; no cache needed |
| Q7: Keep 859 regex? | Yes, as last-resort fallback | Remove in follow-up once metadata coverage confirmed |
| Q8: All phases or 5 checkpoints? | All phases in scope's list | Trivial overhead; better observability |
