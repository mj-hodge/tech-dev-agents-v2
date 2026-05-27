# Analysis: SDLC Execution Engine — Approach Evaluation

> Phase 4 — Analysis
> Story: STORY-006
> Date: 2026-03-26
> Scope: Large
> Analyst model: Sonnet (tier-2, phase-default)

---

## Summary

| Dimension | Approach A | Approach B | Approach C |
|-----------|-----------|-----------|-----------|
| Implementation complexity | Low | Medium | High |
| Unit testability | High | High | Medium–High |
| Integration testability | High | High | Medium |
| Extensibility | Low–Medium | High | Very High |
| Framework alignment | Medium | High | Medium |
| Crash recovery reliability | High | High | Medium |
| Solo-developer maintenance burden | Low | Low–Medium | High |
| STORY-003/005 integration | Adequate | Clean | Clean |
| Time to V1 (days) | 3–4 | 5–7 (hybrid: 4–5) | 8–12 |
| Over-engineering risk | Low | Low–Medium | High |
| Under-engineering risk | Medium | Low | Low |

**Recommendation: Approach B (Event-Driven Engine), two-milestone plan.**

---

## 1. Technical Analysis

### 1.1 Implementation Complexity

**Approach A** is a single `SDLCEngine` class with explicit `if/elif` state guards. The state machine is ~12 transitions over ~7 states. All phase metadata is hardcoded in `config.py`. There is no parsing of external files at startup, no event typing system, and no plugin interface to implement. A developer reading `engine.py` sees the entire orchestration logic in one place.

The cost of this simplicity is that complexity migrates into the hardcoded dictionaries (`PHASE_PATHS`, `ADVANCE_CATEGORIES`, `DELIVERABLES`). These are legitimate Python; they are auditable and version-controlled. The maintenance question is whether they drift from `AGENTS.md` — that risk is real but manageable through code review and a CI test that validates the hardcoded values against the source markdown at test time.

**Approach B** introduces an `EventBus`, a `PhaseRegistry`, a `CheckpointManager`, and a plugin interface layer (`RunnerPlugin`, `TrackerPlugin`, `NotifierPlugin`). Each component is focused and independently testable. The total line count is higher than Approach A (~1,000–1,400 lines vs ~600–800) but the complexity is distributed rather than concentrated. The phase registry loader is the single most complex component — it parses YAML Identity blocks from persona markdown files using a regex extractor. This is the trickiest piece in Approach B and deserves scrutiny:

The persona files use a consistent fenced YAML block pattern. The regex `r"```yaml\n(.*?)```"` with `re.DOTALL` is reliable given the existing file format. The risk is future persona file edits that break the block structure. This is mitigated by a startup validation step that checks all persona files parse correctly, with clear error messages, before the engine runs any phases.

**Approach C** has three distinct layers: workflow YAML schema, interpreter, and engine. The scope-conditional union type in YAML (`deliverables` can be a list or a dict keyed by scope name) is an ergonomics problem. Typos in scope keys (e.g., `lareg`) produce empty deliverables lists silently — exactly the failure mode that the deliverable validation step exists to catch, but applied to the engine's own configuration rather than phase outputs. The hot-reload mechanism adds state management complexity: the engine must decide how to reconcile a reloaded workflow against an in-progress execution, particularly inside bracketed groups.

**Assessment:** Approach A is fastest to a working engine. Approach B is the right architecture for the integration surface required by STORY-007 and STORY-008. Approach C's complexity is not justified by the current requirements.

### 1.2 Testability

**Unit test surface:**

All three approaches support unit testing of the state machine transitions. The critical patterns are:
1. Inject mock runner and mock persona loader via constructor
2. Pre-load a checkpoint fixture (a JSON file with known state)
3. Call `advance()` or `run_phase()`
4. Assert state transitions and events emitted

Approach A's callback registry (`on()` / `_emit()`) requires a recorder pattern to assert events. Approach B's typed event bus (`@dataclass` events) is more explicit — tests can assert `isinstance(captured_event, GateReachedEvent)` rather than checking string event names. Approach C introduces a test fixture burden: tests must supply both a workflow YAML fixture and a runner mock.

**Integration test surface:**

Approach B's dual checkpoint (snapshot + event log) provides a stronger integration test surface than Approach A's single checkpoint. The event log enables post-hoc verification of the exact sequence of state transitions in an integration test run, which is valuable for validating the retry path (fail → retry → fail → escalate) and the resume path (crash mid-phase → reload checkpoint → re-emit gate event).

Approach C's hot-reload behavior requires dedicated integration tests with file modification simulation. This is non-trivial to set up reliably and adds fragility to the CI test suite.

**Assessment:** Approaches A and B have equivalent unit testability. Approach B has superior integration testability due to the event log. Approach C adds testing complexity without a proportional benefit.

### 1.3 Extensibility for Future Stories

The downstream consumers of the engine are:

- **STORY-007 (Approval Flow):** Needs to subscribe to `gate_reached` and `confirmation_requested` events. In Approach A, STORY-007 registers a callback via `engine.on("gate_reached", handler)`. In Approach B, STORY-007 implements `NotifierPlugin` and subscribes to `GateReachedEvent` via the event bus. The plugin interface is cleaner: it is an explicit contract (abstract base class) rather than an implicit string-keyed callback. When STORY-007 is implemented, the Approach B interface requires less documentation — the abstract class documents itself.

- **STORY-008 (Git Operations):** Needs to trigger branch creation and PR opening at specific phase boundaries (likely at the `gate_reached` event for Phase 11 and possibly Phase 8). Approach B's `RunnerPlugin` interface accommodates this as a second subscriber to `PhaseCompletedEvent`. Approach A requires adding another callback registration.

- **Future scope tiers or phase additions:** If the SDLC framework adds a "micro" scope tier or a new review phase in the future, Approach A requires editing `PHASE_PATHS` and `ADVANCE_CATEGORIES` in `config.py`. Approach B requires editing the same but also has the registry loader path — once the registry loader is implemented in Milestone 2, framework additions are picked up automatically. Approach C requires only YAML edits.

**Assessment:** Approach B provides the right extensibility for the immediate downstream stories (STORY-007, STORY-008) without Approach C's complexity overhead. Approach A would require refactoring the callback registry to a plugin interface once STORY-007 is implemented.

### 1.4 Alignment with .sdlc/ Framework

The engine's constraint (from seed.md) is to implement the process defined in `.sdlc/software-development-guidance.md` and `AGENTS.md` exactly — not a reimagination but a faithful programmatic encoding.

**Approach A** hardcodes the encoding. The `ADVANCE_CATEGORIES` dict in `config.py` is a direct transcription of the advance category table in `AGENTS.md`. If `AGENTS.md` changes (e.g., Phase 7 changes from `confirm` to `gate`), `config.py` must be manually updated. This is a maintenance task, not a correctness risk, as long as the change is caught in code review or a CI validation test.

**Approach B with Milestone 1** also hardcodes the encoding (same as Approach A). The registry loader is deferred to Milestone 2. So in V1, the framework alignment is identical to Approach A. In Milestone 2, the registry loader reads advance categories and model tiers directly from persona files — eliminating manual synchronization for those fields. Phase paths remain hardcoded (the persona files do not encode them; that information lives in `AGENTS.md`).

**Approach C** creates a parallel artifact (`standard.yaml`) that must stay synchronized with `AGENTS.md`. This is a net-negative for framework alignment: it adds a third source of truth (alongside `AGENTS.md` and the persona files) without mechanical enforcement of consistency. Unless a CI check validates the workflow YAML against `AGENTS.md` on every commit, drift is inevitable.

**Assessment:** Approach B (Milestone 1) and Approach A are equivalent for V1. Approach B (Milestone 2) achieves better framework alignment by reading live from persona files. Approach C's parallel artifact is a framework alignment anti-pattern.

### 1.5 Crash Recovery Reliability

All three approaches use the same underlying mechanism: atomic JSON checkpoint writes (temp file + `os.replace()`), checkpoint on phase start and on phase completion, and deterministic resume rules based on phase status.

The critical correctness invariant is that a phase in `waiting_for_approval` or `waiting_for_confirmation` state must NOT be re-executed on resume — only the wait event must be re-emitted. All three approaches handle this correctly in their design.

**Where approaches differ:**

Approach B's dual checkpoint (snapshot + event log) provides a fallback: if the snapshot file is corrupted (mid-write crash in the snapshot itself), the event log can be replayed to reconstruct state. This is belt-and-suspenders reliability. The event log append uses Linux's O_APPEND semantics, which is effectively atomic for small writes on a local filesystem — a reasonable assumption for the container-per-agent model.

Approach C's hot-reload introduces a specific crash recovery edge case: if the engine crashes after a workflow reload but before persisting the updated state, the next startup uses the reloaded workflow but loads a checkpoint written under the previous workflow version. If the reload changed phase paths, the checkpoint's `phase_path` field may be stale. This requires the checkpoint to store the full phase path (which the seed.md checkpoint format already does), and the engine to prefer the checkpoint's stored path over the workflow's computed path on resume. This is solvable but adds a correctness burden.

**Assessment:** Approaches A and B have equivalent crash recovery reliability. Approach B's event log adds a minor reliability improvement. Approach C introduces a hot-reload edge case that requires careful handling.

### 1.6 Integration with STORY-003 (Runner) and STORY-005 (Personas)

**STORY-003 (Claude Code Runner):**

All three approaches interface with the runner through the same `PhaseExecutionRequest` / `PhaseExecutionResult` contract. In Approach A, the runner is injected as a typed callable or protocol. In Approaches B and C, the runner is a `RunnerPlugin` (abstract base class). The plugin interface is strictly better for integration testing: a `FakeRunner` that always returns success and writes stub deliverable files can be injected at construction time without any mocking framework.

Until STORY-003 is complete, the engine needs a minimal `LocalRunner` that invokes `claude` via subprocess. The plugin interface makes this substitution clean.

**STORY-005 (Persona System):**

Both the seed.md and expansion.md acknowledge that STORY-005 may not be complete when STORY-006 is implemented. The engine needs a minimal local persona loader (parses `.sdlc/agents/phase-*.md`) as a bootstrap. In Approach A, this is a `persona_loader` callable injected into `SDLCEngine`. In Approach B, this becomes part of the `PhaseRegistry`. In both cases, the interface is the same: given a phase ID, return the system prompt, tool permissions, advance category, and model tier.

The research flagged that the advance category can be extracted from the persona file YAML Identity block. This is the basis for Approach B's Milestone 2 registry loader. For V1 (hardcoded metadata), the persona loader only needs to read the system prompt text and tool permissions from the persona file — the structural metadata is already encoded in `config.py`.

**Assessment:** Approach B provides the cleanest integration point for both STORY-003 and STORY-005 via abstract plugin interfaces. Approach A provides adequate integration via injected callables, which are less self-documenting but functionally equivalent.

---

## 2. Business Analysis

### 2.1 Time to Implement

| Approach | V1 Estimate | Notes |
|----------|------------|-------|
| A | 3–4 days | Complete, tested, covers all acceptance criteria |
| B (hybrid) | 4–5 days | V1 with hardcoded metadata (Milestone 1) |
| B (full) | 5–7 days | Full registry loader (Milestone 2, deferred to Phase 9) |
| C | 8–12 days | Three-layer architecture, YAML schema, hot-reload |

The engine unblocks STORY-007 and STORY-008. Every day of delay on STORY-006 is a day of delay on both downstream stories. For a solo developer, the 1–2 day difference between Approach A and Approach B (Milestone 1) is meaningful but not decisive. The 4–8 day difference between Approach B and Approach C is decisive.

The hybrid Approach B plan (hardcode metadata in V1, add registry loader in Phase 9) reduces the V1 implementation to 4–5 days while preserving the correct final architecture. This is the key insight from the expansion.md recommendation: the plugin interfaces and event bus are worth building upfront because refactoring from Approach A callbacks to a plugin system later would require touching both the engine and every downstream story that registered callbacks.

### 2.2 Maintenance Burden for a Solo Developer

**Approach A maintenance burden:**

- When `AGENTS.md` advance categories change: edit `ADVANCE_CATEGORIES` in `config.py`. One-line change per modified phase. Detected at PR review.
- When a new phase is added to the SDLC: add to `PHASE_PATHS`, `ADVANCE_CATEGORIES`, `MODEL_TIERS`, and `DELIVERABLES` dicts. ~5 lines. Detectable via a CI test that validates dict keys match the persona file inventory.
- When STORY-007 or STORY-008 needs a new event: the callback registry is flexible — add `engine.on("new_event", handler)`. No engine changes needed.
- Long-term risk: `SDLCEngine.advance()` accumulates branches as new advance categories are added. This is a "god class" accumulation risk, but SDLC advance categories are stable (the framework has had 3 categories for its lifetime).

**Approach B maintenance burden:**

- Plugin interfaces require downstream stories (STORY-007, STORY-008) to implement abstract base classes. This is a slightly higher bar for contributors but provides compile-time contract enforcement.
- The event bus error isolation (each handler in `try/except`) can swallow exceptions. A test mode flag that re-raises exceptions in tests is essential.
- The `PhaseRegistry` parser adds a maintenance surface: if persona file format changes (e.g., the YAML block fencing convention changes), the parser breaks. A startup validation step catches this immediately.
- The dual checkpoint adds a secondary failure mode: event log append failures. These should be logged but not fatal — the snapshot is the primary recovery source.

**Approach C maintenance burden:**

- The `standard.yaml` file is a new artifact that all contributors must understand. It is not obviously related to the `.sdlc/` markdown files, requiring documentation.
- Schema changes to `WorkflowDefinition` require updating both the Pydantic model and the YAML file simultaneously.
- Hot-reload adds an operational concern: an operator who edits `standard.yaml` during a running execution must understand the resumption semantics. This is too much operational complexity for a solo developer's autonomous agent.

**Assessment:** Approach A has the lowest maintenance burden for a solo developer. Approach B's maintenance burden is modest and mostly front-loaded (the plugin interfaces and registry parser). Approach C's maintenance burden is ongoing and disproportionate to the problem.

### 2.3 Risk of Over-Engineering vs Under-Engineering

**Under-engineering (Approach A only):**

The callback registry `engine.on("gate_reached", handler)` is an adequate mechanism for one downstream consumer (STORY-007). When STORY-008 is added as a second consumer of `gate_reached`, both consumers are registered and the engine fires both. This works fine in practice. The under-engineering risk is not a functionality gap — it is an architecture gap. String-keyed callbacks are weakly typed, undiscoverable, and provide no contract for STORY-007's implementor. The risk manifests as integration friction rather than functional failures.

If this were a team project with multiple developers working on STORY-006 and STORY-007 concurrently, the weak typing would cause integration bugs. For a solo developer who writes both, it is manageable but still less robust than a typed plugin interface.

**Over-engineering (Approach C):**

The hot-reload feature solves a problem that does not exist in the current operational context: the SDLC framework phases and paths have been stable for months and are unlikely to change mid-execution. Building a hot-reload interpreter for this scenario is classic over-engineering: it adds complexity, testing burden, and operational surface area for a capability that may never be used.

The workflow YAML as "single source of truth" is also over-engineering: the existing `.sdlc/` markdown files are already the single source of truth. Adding a YAML file that parallels them creates maintenance overhead with no operational benefit that Approach B's registry loader does not also provide.

**Assessment:** Approach A carries a modest under-engineering risk (weak plugin contract) that manifests primarily when STORY-007 is implemented. This risk is mitigated by designing the callback registry with typed parameters even if it is not an abstract base class. Approach C over-engineers the framework evolution problem and the hot-reload problem. Approach B hits the balance point.

---

## 3. Risk Analysis

### 3.1 Approach A Specific Risks

**Risk: Hardcoded metadata drifts from AGENTS.md**
- Probability: Medium. AGENTS.md changes are infrequent (the SDLC process is stable) but do happen.
- Impact: Engine implements wrong phase path or wrong advance behavior for a scope.
- Mitigation: Write a CI test that reads both `AGENTS.md` and `config.py` and asserts the advance categories match. This test runs on every commit and catches drift automatically. Implementation cost: ~2 hours.

**Risk: Callback registry provides no typed contract for STORY-007**
- Probability: High (it will require runtime debugging to wire STORY-007 correctly).
- Impact: Integration friction, not a functional failure.
- Mitigation: Document the exact expected function signature for each event's callback in the engine's docstring. Use `Protocol` typing (Python 3.8+) on the callback type annotations.

**Risk: God class accumulation in SDLCEngine**
- Probability: Low (SDLC advance categories are stable).
- Impact: Code becomes harder to read over time.
- Mitigation: Apply the single-responsibility principle within the class: separate methods for state transitions, event emission, retry logic, and checkpoint writes. Keep the `advance()` method under 30 lines.

### 3.2 Approach B Specific Risks

**Risk: PhaseRegistry persona file parser breaks on format change**
- Probability: Low (persona file format is stable and defined by the SDLC framework).
- Impact: Engine fails to start; all phase metadata falls back to hardcoded defaults.
- Mitigation: Startup validation that asserts all expected persona files parse correctly with clear error messages. This prevents silent degradation — the engine refuses to run with a broken registry rather than running with corrupted metadata.

**Risk: Synchronous event bus blocks phase progression on slow handlers**
- Probability: Medium. The Monday.com tracker plugin involves an MCP API call that may add 1–3 seconds per phase transition.
- Impact: Phase-to-phase latency increases, but functional correctness is unaffected.
- Mitigation: Tracker plugin calls within the event bus are fire-and-forget with a timeout (e.g., 5 seconds). If the tracker times out, log a warning and continue — tracking failures should not block phase execution.

**Risk: Milestone 2 registry loader is never implemented**
- Probability: Low but non-zero. Phase 9 refinement may be deprioritized.
- Impact: V1 with hardcoded metadata continues to work correctly indefinitely. The only cost is manual synchronization of `ADVANCE_CATEGORIES` when the SDLC framework changes.
- Assessment: This is not actually a risk — V1 with hardcoded metadata satisfies all acceptance criteria. Milestone 2 is an upgrade, not a correctness fix.

**Risk: Plugin interface adds boilerplate for STORY-007 implementor**
- Probability: High (abstract base classes always require boilerplate).
- Impact: Minor friction for STORY-007 implementation.
- Mitigation: Provide a concrete `LoggingNotifierPlugin` that implements the interface and logs to stdout. STORY-007 can use this as a reference implementation and test double.

### 3.3 Approach C Specific Risks

**Risk: standard.yaml drifts from AGENTS.md**
- Probability: High. Without a CI validator, the parallel artifact will eventually diverge.
- Impact: Engine implements a different SDLC process than documented. This is a correctness failure, not a style issue.
- Mitigation: Write a CI validator that parses both `AGENTS.md` and `standard.yaml` and asserts they agree. This is a non-trivial parser given that `AGENTS.md` uses a Markdown table. Estimated cost: 1–2 days of implementation.

**Risk: scope_conditional YAML typo produces empty deliverables**
- Probability: High. YAML key typos are a common error class.
- Impact: Phase 6 produces no deliverables for a given scope, triggering a retry cascade.
- Mitigation: Strict enum validation in `WorkflowDefinition` Pydantic model. Scope keys in `scope_conditional` must match the defined scope names exactly. This catches typos at workflow load time.

**Risk: Hot-reload creates checkpoint/workflow version mismatch**
- Probability: Medium. If the SDLC process changes, an operator may edit `standard.yaml` during an active execution.
- Impact: Engine resumes from a checkpoint written under the old workflow, with remaining phases from the new workflow. Phase path changes can produce wrong deliverables.
- Mitigation: Prohibit hot-reload during execution (only allow between phase boundaries). Store the workflow version hash in the checkpoint. On resume, if checkpoint workflow hash != current workflow hash, emit a warning and require explicit user acknowledgment before continuing.

### 3.4 Phase 8 Advance Discrepancy (gate vs confirm)

The research phase identified a discrepancy: `phase-8-implementation.md` has `advance: confirm` in its YAML Identity block, while `AGENTS.md` and seed.md § Acceptance Criteria both specify Phase 8 as `gate`.

**Analysis of the discrepancy:**

A `gate` advance means: execution stops, a `gate_reached` event is emitted, and the story remains in `waiting_for_approval` state until explicit external approval is received. A `confirm` advance means: execution stops, a `confirmation_requested` event is emitted with "Proceed to Phase 8b?", and execution resumes on yes.

For Phase 8 (implementation), the intent is clear from the seed's success criteria #4: "gate phases stop and emit a notification event" and `gate` is explicitly listed as applying to "Phases 1, 8, 11." Phase 8 produces production code. The human must explicitly review the implementation before automated code review (Phase 8b) proceeds. A `confirm` prompt asking "Proceed?" does not require the human to verify the implementation quality — it only asks if they want to continue. A `gate` requires explicit sign-off.

**Resolution:** Phase 8 advance category is `gate`. The persona file (`phase-8-implementation.md`) has a bug. The authoritative source is `AGENTS.md` (the highest-level policy document). The engine's hardcoded `ADVANCE_CATEGORIES` in `config.py` (Approaches A and B, V1) should set `"8": "gate"`. This is also a bug that must be corrected in the persona file in Phase 6 design. Phase 6 must produce an updated persona file with `advance: gate`.

**Impact on engine design:** None — the engine reads from `ADVANCE_CATEGORIES` (hardcoded to `gate` for Phase 8) in V1. The persona file fix is a documentation correction, not an engine change.

### 3.5 Evaluation of the Two-Milestone Plan (Approach B)

The expansion.md proposes implementing Approach B in two milestones:

**Milestone 1 (Phase 8 deliverable):** Event bus + plugin interfaces + state machine + hardcoded metadata. Delivers all acceptance criteria in 4–5 days.

**Milestone 2 (Phase 9 refinement):** Live phase registry loader (reads from `.sdlc/agents/phase-*.md`), `config.yaml` override support, replacement of hardcoded `config.py` values with registry lookups.

**Assessment of the two-milestone plan:**

The plan is sound. The key insight is that the event bus and plugin interface architecture is worth building in Milestone 1 — these are load-bearing structural decisions that cannot be added as an afterthought. The phase registry loader and config override support are enhancements that improve framework alignment but do not affect correctness.

The plan correctly identifies that starting with Approach A (callbacks) and later migrating to Approach B (plugins) would require changes to both the engine and every downstream story that registered callbacks. Building the plugin interface in V1 prevents this migration cost.

One adjustment to the plan: Milestone 2 should also address the open question about the phase path source of truth. The phase paths are not in persona files — they are in `AGENTS.md`'s Markdown tables. Parsing Markdown tables is fragile. The recommended resolution is to add a companion machine-readable file (e.g., `.sdlc/phase-paths.yaml`) that the Milestone 2 registry loader reads. This requires a one-time authoring of `phase-paths.yaml` from the existing `AGENTS.md` table, but eliminates Markdown parsing risk. The file can be validated against `AGENTS.md` by a CI test.

**Open questions resolved for Phase 6 design:**

1. **Phase 8 advance category:** `gate`. Persona file must be corrected in Phase 6.
2. **Phase path source of truth:** Add `.sdlc/phase-paths.yaml` for Milestone 2. V1 uses hardcoded paths in `config.py`.
3. **Parallel bracketed groups:** Sequential-first. Parallel execution is a Phase 9 upgrade.
4. **Context budget enforcement:** Phase 6 must specify a per-phase token budget estimate. Phase 8 context (design deliverables + test-design.md + seed.md) must be bounded to ~80k tokens. Large deliverables should be passed as file paths with a 500-token excerpt, not inlined in full.
5. **Event bus async vs sync:** Synchronous event bus in V1. The notifier plugin manages its own async context via a threadpool or fire-and-forget pattern. An async event bus is a Milestone 2 consideration if Monday.com MCP latency proves problematic.

---

## 4. Recommendation

**Approach B (Event-Driven Engine), two-milestone plan.**

### Rationale

1. **Architecture fit:** The plugin interface pattern (abstract base classes for `RunnerPlugin`, `TrackerPlugin`, `NotifierPlugin`) is the correct integration surface for STORY-007 and STORY-008. Building this in V1 prevents a costly refactor when those stories are implemented. Approach A's callback registry is functionally equivalent but architecturally weaker — the weak typing will produce integration friction that costs more time to debug than the 1-day implementation difference.

2. **Testability:** Approach B's typed event bus and plugin interfaces produce a more self-documenting test suite than Approach A's string-keyed callbacks. The dual checkpoint (snapshot + event log) enables integration tests to verify the exact state transition sequence, which is essential for validating the retry and resume paths — the two highest-risk scenarios in the engine's error recovery logic.

3. **Scope fit:** Approach C is over-engineered for the problem. The SDLC framework is stable. The three advance categories (gate/confirm/auto), five scope tiers, and fifteen-phase inventory have not changed materially in the project's history. Hot-reload and a workflow DSL add complexity for framework evolution scenarios that are unlikely to occur frequently enough to justify the implementation cost.

4. **Risk profile:** The two-milestone plan eliminates the largest risk in a pure Approach B implementation: the phase registry parser complexity. By hardcoding metadata in V1 (Milestone 1), the engine's correctness depends only on a correctly authored `config.py` — a static artifact with no parsing risk. The registry parser is deferred to Milestone 2 where it can be implemented and tested without blocking the V1 delivery.

5. **Phase 8 discrepancy:** The `gate` vs `confirm` discrepancy is resolved in favor of `gate` per AGENTS.md. The engine's hardcoded `ADVANCE_CATEGORIES` dict must reflect this. The persona file is a bug that Phase 6 design must fix.

### What Approach B V1 (Milestone 1) Must Deliver

| Component | Required for V1 | Deferred to Milestone 2 |
|-----------|----------------|------------------------|
| SDLCEngine orchestration loop | Yes | |
| EventBus (sync, typed events) | Yes | Async handlers |
| Plugin interfaces (RunnerPlugin, TrackerPlugin, NotifierPlugin) | Yes | |
| CheckpointManager (atomic snapshot) | Yes | Event log |
| ContextBuilder (per-phase context) | Yes | |
| Classifier (LLM + rule-based hybrid) | Yes | |
| Hardcoded PHASE_PATHS, ADVANCE_CATEGORIES | Yes | Replace with registry |
| Minimal LocalPersonaLoader (reads system prompt from persona files) | Yes | |
| PhaseRegistry (parses persona YAML blocks) | No | Yes |
| config.yaml advance_overrides support | No | Yes |
| Sequential bracketed groups (6b→6c→6d) | Yes | True asyncio.gather() |
| scope_reassessment protocol | Yes | |
| FakeRunner for testing | Yes | |

### Risks Accepted

- Hardcoded metadata in V1 requires manual sync with `AGENTS.md` on framework changes. Mitigated by a CI validation test.
- Phase registry parser (Milestone 2) may be deprioritized. Accepted: V1 with hardcoded metadata satisfies all acceptance criteria.
- Synchronous event bus may add tracker latency. Mitigated by fire-and-forget tracker calls with 5-second timeout.

### Risks Rejected

- Approach C's workflow drift risk (parallel YAML artifact) is not worth the extensibility benefit.
- Approach C's hot-reload complexity has no operational justification.
- True parallel execution of bracketed groups in V1 is not required by acceptance criteria and adds concurrency management complexity.
