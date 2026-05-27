# Selection: SDLC Execution Engine — Approach Decision

> Phase 5 — Selection
> Story: STORY-006
> Date: 2026-03-26
> Scope: Large
> Decision maker: Phase 5 Pragmatic Executive

---

## Selected Approach: Approach B — Event-Driven Engine (Two-Milestone Plan)

**Approaches evaluated:** A (Minimal State Machine), B (Event-Driven Engine), C (Workflow DSL)

**Decision:** Approach B with a two-milestone delivery plan.

---

## 1. Why Approach B

### 1.1 Correct integration surface for downstream stories

Approach B's plugin interface pattern (abstract base classes for `RunnerPlugin`, `TrackerPlugin`, `NotifierPlugin`) is the right architectural boundary for STORY-007 (Approval Flow) and STORY-008 (Git Operations). STORY-007 needs to subscribe to `gate_reached` and `confirmation_requested` events; STORY-008 needs to react to `phase_completed` and `gate_reached` at Phase 11. With Approach A's string-keyed callback registry, STORY-007's implementor has no discoverable contract — they must read the engine source to learn what callback parameters to expect. With Approach B's typed `GateReachedEvent` dataclass and `NotifierPlugin` abstract class, the contract is self-documenting and enforced at instantiation time.

Building the callback-to-plugin migration path upfront costs approximately one additional day in V1. Not building it means paying a refactor tax across three stories (STORY-006, STORY-007, STORY-008) when the integration is wired. The one-day upfront cost wins.

### 1.2 Superior integration testability

Approach B's typed event bus enables integration tests to assert `isinstance(captured_event, GateReachedEvent)` rather than checking string equality against `"gate_reached"`. This is not a stylistic preference — it eliminates an entire class of typo-class bugs in test assertions and makes test failures self-explanatory. The event log (append-only, O_APPEND) enables post-hoc verification of the exact state transition sequence in a test run, which is the primary tool for validating the retry path (`fail → retry → fail → escalate`) and the resume path (`crash mid-phase → reload checkpoint → re-emit gate event`). These two paths are the highest-risk scenarios in the engine's error recovery logic and they must be covered by integration tests with verifiable transition sequences.

### 1.3 Architecture fit without over-engineering

Approach C (Workflow DSL) introduces a `standard.yaml` parallel artifact that must stay synchronized with `AGENTS.md`. Without a CI validator, the two will diverge — and writing a CI validator for a Markdown table is non-trivial (estimated 1–2 days). Approach C's hot-reload mechanism solves a problem that does not exist: the SDLC framework's phase inventory, scope paths, and advance categories have been stable for the project's entire history. Building a hot-reload interpreter for a document that changes infrequently is over-engineering that adds concurrency edge cases (checkpoint/workflow version mismatch on resume) without operational benefit.

Approach B defers live registry loading to Milestone 2 while delivering all acceptance criteria in V1. The V1 hardcoded metadata in `config.py` is a faithful transcription of `AGENTS.md` — explicit, auditable, and version-controlled.

### 1.4 Resolved advance category discrepancy

The analysis phase identified a discrepancy: `phase-8-implementation.md` has `advance: confirm` in its YAML Identity block, while `AGENTS.md` and the seed's acceptance criteria both specify Phase 8 as `gate`. The authoritative source is `AGENTS.md`. Phase 8 produces production code; explicit human sign-off (gate) is the correct semantic, not a "Proceed?" confirmation prompt. The engine's hardcoded `ADVANCE_CATEGORIES` dict will encode `"8": "gate"`. Phase 6 design must correct the persona file.

---

## 2. MVP Scope — Milestone 1 (Phase 8 Deliverable)

Milestone 1 delivers all STORY-006 acceptance criteria. Every item below is required for V1.

### 2.1 Typed Event Bus

An `EventBus` class using Python's `EventEmitter` pattern with typed event dataclasses. Events are defined as `@dataclass` classes (not string constants). Each event type has explicit fields — e.g., `GateReachedEvent(story_id, phase, summary)`.

The bus is **synchronous** in V1. Each handler executes in sequence within a `try/except` block that logs errors but does not halt other handlers. A `test_mode` flag re-raises exceptions during tests, preventing silent swallowing.

Events emitted by the engine:

| Event | Trigger |
|-------|---------|
| `PhaseStartedEvent` | Phase execution begins |
| `PhaseCompletedEvent` | Phase execution succeeds, deliverables verified |
| `PhaseFailedEvent` | Phase execution fails or deliverable validation fails |
| `GateReachedEvent` | Phase advance category is `gate` |
| `ConfirmationRequestedEvent` | Phase advance category is `confirm` |
| `EscalationRequiredEvent` | Two consecutive phase failures |
| `StoryCompletedEvent` | All phases in the path complete |
| `ScopeReassessedEvent` | Scope classification revised mid-execution |

Events consumed:

| Event | Source |
|-------|--------|
| `ExecutionRequestedEvent` | Orchestrator layer |
| `ApprovalReceivedEvent` | STORY-007 (Approval Flow) |
| `ConfirmationReceivedEvent` | STORY-007 (Approval Flow) |

### 2.2 Abstract Plugin Interfaces

Three abstract base classes define the integration contracts:

**`RunnerPlugin`** — wraps the Claude Code Runner (STORY-003):
```python
class RunnerPlugin(ABC):
    @abstractmethod
    def execute(self, request: PhaseExecutionRequest) -> PhaseExecutionResult: ...
```

**`TrackerPlugin`** — wraps Monday.com and `.project` updates (STORY-004):
```python
class TrackerPlugin(ABC):
    @abstractmethod
    def on_phase_completed(self, event: PhaseCompletedEvent) -> None: ...
    @abstractmethod
    def on_phase_failed(self, event: PhaseFailedEvent) -> None: ...
    @abstractmethod
    def on_story_completed(self, event: StoryCompletedEvent) -> None: ...
```

**`NotifierPlugin`** — wraps gate/confirm notifications (STORY-007):
```python
class NotifierPlugin(ABC):
    @abstractmethod
    def on_gate_reached(self, event: GateReachedEvent) -> None: ...
    @abstractmethod
    def on_confirmation_requested(self, event: ConfirmationRequestedEvent) -> None: ...
    @abstractmethod
    def on_escalation_required(self, event: EscalationRequiredEvent) -> None: ...
```

A `LoggingNotifierPlugin` concrete implementation ships with V1 as a reference implementation and test double.

### 2.3 State Machine with Explicit Transitions

The state machine governs per-phase and per-story states.

**Phase states:**
```
pending → in_progress → completed → [next phase]
                     ↘→ failed → retry → in_progress
                     ↘→ waiting_for_approval → approved → [next phase]
                                            ↘→ rejected → blocked
                     ↘→ waiting_for_confirmation → confirmed → [next phase]
                                                ↘→ declined → blocked
```

**Story states:**
```
not_started → in_progress → completed
                          ↘→ blocked  (gate rejection or user decline)
                          ↘→ failed   (unrecoverable after retry)
```

Transitions are driven by the `SDLCEngine.advance()` method. Each advance category produces a distinct transition:
- `gate`: transition to `waiting_for_approval`, emit `GateReachedEvent`, halt loop
- `confirm`: transition to `waiting_for_confirmation`, emit `ConfirmationRequestedEvent`, halt loop
- `auto`: transition immediately to `pending` for next phase, continue loop

### 2.4 Hardcoded Phase Metadata

Phase paths, advance categories, model tiers, and deliverable requirements are encoded as Python dictionaries in `config.py`. This is a direct, auditable transcription of `AGENTS.md`.

**Canonical phase paths:**

| Scope | Path |
|-------|------|
| Trivial | `8` |
| Small | `1 → 7 → 8` |
| Medium | `1 → 4 → 6 → [6b, 6c, 6d] → 7 → 8 → 8b → 11` |
| Large | `1 → 2 → 3 → 4 → 5 → 6 → [6b, 6c, 6d] → 7 → 8 → 8b → 11 → [9, 10]` |
| Epic | Decompose to child stories, then per-story SDLC |

**Advance categories (hardcoded, authoritative):**

| Phase | Advance | Note |
|-------|---------|------|
| 1 | gate | |
| 2 | confirm | |
| 3 | confirm | |
| 4 | confirm | |
| 5 | confirm | |
| 6 | confirm | |
| 6b | auto | |
| 6c | auto | |
| 6d | auto | |
| 7 | confirm | |
| 8 | **gate** | Persona file bug corrected — AGENTS.md is authoritative |
| 8b | auto | |
| 9 | confirm | |
| 10 | confirm | |
| 11 | gate | |

A CI test validates the hardcoded `ADVANCE_CATEGORIES` dict against `AGENTS.md` at every commit to catch drift.

### 2.5 File-Based Checkpoint with Atomic Write

The `CheckpointManager` persists execution state to `features/<story-folder>/.checkpoint.json` at every phase boundary (before phase starts and after phase completes). Writes use the atomic pattern: write to a `.checkpoint.json.tmp` sibling file, then `os.replace()` to the target path. This prevents checkpoint corruption from crashes mid-write.

Checkpoint structure follows the format established in seed.md:

```json
{
  "story_id": "STORY-XXX",
  "story_slug": "story-XXX-kebab-slug",
  "scope": "large",
  "phase_path": ["1", "2", "3", "4", "5", "6", ["6b","6c","6d"], "7", "8", "8b", "11", ["9","10"]],
  "completed_phases": [],
  "current_phase": "1",
  "phase_status": "in_progress",
  "advance_state": null,
  "retry_count": 0,
  "created_at": "<iso8601>",
  "updated_at": "<iso8601>",
  "deliverables": {},
  "scope_reassessments": [],
  "errors": []
}
```

Resume logic: on startup, if a checkpoint exists, the engine restores state without re-executing completed phases. If the checkpoint shows `waiting_for_approval`, the gate event is re-emitted rather than the phase being re-run.

### 2.6 Scope Classification (Rule-Based Heuristic)

The `Classifier` uses a deterministic rule-based scoring system before falling back to an LLM judgment call. Rules are drawn directly from the scope classification criteria in `.sdlc/software-development-guidance.md`:

| Signal | Weight |
|--------|--------|
| Number of files estimated to change | +1 per 3 files |
| New DB models | +2 each |
| New API endpoints | +1 each |
| New external integrations | +3 each |
| Cross-cutting concerns (auth, logging, migrations) | +2 each |
| Estimated test count | +1 per 5 tests |

Score ranges map to scope tiers (exact thresholds defined in Phase 6 design). The user can override classification via explicit `scope_override` parameter. Epic escalation triggers when 2+ epic signals are present.

Classification rationale is persisted to the checkpoint for auditability and tuning.

### 2.7 Sequential Execution of Bracketed Groups

Bracketed phases (e.g., `[6b, 6c, 6d]`) execute **sequentially** in V1: 6b completes, then 6c starts, then 6d starts. All three must complete before the engine advances to Phase 7. Each phase in a bracketed group is independently checkpointed and can be individually retried.

Sequential execution simplifies the state machine: no concurrent state tracking, no partial group completion handling, no join logic. The latency cost (sequential vs. parallel) is acceptable — each review phase runs in minutes and is not on a critical wall-clock path.

True `asyncio.gather()` parallelism for bracketed groups is deferred to Milestone 2.

### 2.8 Phase 8 Advance = Gate (Resolved Discrepancy)

Phase 8 advance category is definitively `gate`. Rationale: Phase 8 produces production code. The human must explicitly sign off on implementation before automated code review (Phase 8b) proceeds. A `confirm` prompt ("Proceed to Phase 8b?") does not require the human to verify implementation quality. A `gate` requires an explicit approval signal via the STORY-007 approval flow.

The engine hardcodes `"8": "gate"`. Phase 6 design must correct `phase-8-implementation.md` to set `advance: gate` in its YAML Identity block.

---

## 3. Deferred to Phase 9 — Milestone 2

The following items improve framework alignment and operational flexibility but do not affect correctness. All STORY-006 acceptance criteria are satisfied by Milestone 1.

### 3.1 Live Registry Loader from `.sdlc/agents/` Persona Files

A `PhaseRegistry` component that parses the YAML Identity block from each `phase-*.md` persona file using a regex extractor. This replaces the hardcoded `ADVANCE_CATEGORIES` and `MODEL_TIERS` dicts in `config.py` with values read live from the framework source files.

The registry loader adds a dependency on persona file format stability. A startup validation step asserts all persona files parse correctly before the engine runs. This is the correct architecture long-term but carries parsing risk that is not worth introducing in V1.

Phase paths are not in persona files — they live in `AGENTS.md`'s Markdown tables, which are fragile to parse. Milestone 2 must also produce a companion file (`.sdlc/phase-paths.yaml`) authored from the `AGENTS.md` table. This machine-readable file is the registry loader's source for phase path data and can be CI-validated against `AGENTS.md`.

### 3.2 config.yaml Override Support

Support for `models.opus_allowed: true` in `config.yaml` to permit Opus execution of tier-2-default phases. The engine reads this override flag before invoking the runner and adjusts the `model_tier` field in the `PhaseExecutionRequest`. Deferred because the `config.yaml` loading infrastructure (distinct from the hardcoded config) requires the registry loader as a foundation.

### 3.3 Async Parallel Bracketed Group Execution

Replace sequential bracketed group execution with `asyncio.gather()` for true parallel execution of phases in a group (e.g., 6b, 6c, 6d running concurrently). This requires the event bus to become async and the checkpoint manager to handle concurrent writes with per-phase locks. The concurrency management complexity is not justified in V1.

---

## 4. Key Constraints and Design Decisions

### 4.1 Engine orchestrates; runner executes

The engine never calls the Claude API directly. All phase execution is delegated to the `RunnerPlugin` (which wraps STORY-003). The engine constructs the request (system prompt, user prompt, model tier, tool permissions, working directory, timeout) and passes it to the runner. The runner owns the subprocess lifecycle.

### 4.2 Stateless phase invocations

Each phase invocation starts with fresh runner context. The engine's responsibility is to construct the correct context for each phase by reading previous deliverables from `features/<story-folder>/` and including only the relevant prior outputs (per the Context Inputs table in seed.md). Irrelevant deliverables are never included — this is the primary defense against context window overflow on large stories.

Large deliverables (> ~500 lines) are passed as file paths with a 500-token excerpt, not inlined in full. Phase 6 design must specify the per-phase token budget and the excerpt strategy.

### 4.3 Event bus decouples the engine from notification channels

The engine emits events; it never calls Teams, Monday.com, or GitHub directly. STORY-007 subscribes to `GateReachedEvent` and `ConfirmationRequestedEvent`. STORY-008 subscribes to `PhaseCompletedEvent` and `GateReachedEvent`. The engine has no compile-time dependency on any downstream story's implementation. This decoupling is the central architectural benefit of Approach B over Approach A.

### 4.4 Tracker plugin calls are fire-and-forget with timeout

The `TrackerPlugin` handles Monday.com MCP API calls, which may add 1–3 seconds per phase transition. To prevent tracking latency from blocking phase execution, the event bus invokes the tracker plugin with a 5-second timeout. If the call times out, a warning is logged and the engine continues. Tracking failures are never fatal to execution.

### 4.5 Scope reassessment is bounded

The engine supports at most one automatic scope reassessment per story. If a second reassessment is triggered, the engine emits an `EscalationRequiredEvent` and halts, requiring explicit human instruction. This prevents oscillation between scope tiers that could loop indefinitely.

### 4.6 File system is the checkpoint store

Checkpoint state is persisted to the local file system (`features/<story-folder>/.checkpoint.json`). No database dependency. This aligns with the container-per-agent model where each agent has its own filesystem. The container filesystem must be persistent across restarts (or use a mounted volume) for checkpoint resume to work correctly.

### 4.7 Module structure (Milestone 1)

```
sdlc_engine/
├── engine.py           # SDLCEngine — main orchestration loop
├── events.py           # Typed event dataclasses + EventBus
├── plugins.py          # RunnerPlugin, TrackerPlugin, NotifierPlugin ABCs
├── classifier.py       # Scope classifier (rule-based + LLM fallback)
├── checkpoint.py       # CheckpointManager (atomic JSON read/write)
├── context_builder.py  # Per-phase prompt context construction
├── persona_loader.py   # Minimal .sdlc/agents/ reader (system prompt + tools only)
├── models.py           # Pydantic state/checkpoint/request/result models
├── config.py           # Hardcoded PHASE_PATHS, ADVANCE_CATEGORIES, MODEL_TIERS, DELIVERABLES
└── fake_runner.py      # FakeRunner test double (writes stub deliverables)
```

---

## 5. Risks Accepted

| Risk | Mitigation |
|------|-----------|
| Hardcoded metadata drifts from `AGENTS.md` | CI test validates `ADVANCE_CATEGORIES` and `MODEL_TIERS` against `AGENTS.md` on every commit |
| Phase registry loader (Milestone 2) is deprioritized | V1 with hardcoded metadata satisfies all acceptance criteria indefinitely |
| Synchronous event bus adds tracker latency | Fire-and-forget tracker calls with 5-second timeout; tracker failures non-fatal |
| Scope classification accuracy | Classification rationale logged to checkpoint for tuning; user override always available |

## 6. Risks Rejected

| Risk | Reason Rejected |
|------|----------------|
| Approach C workflow YAML drift | Parallel artifact adds maintenance overhead with no correctness benefit over Approach B Milestone 2 |
| Approach C hot-reload complexity | SDLC framework is stable; hot-reload solves a problem that does not exist in production |
| Parallel bracketed groups in V1 | Sequential execution is correct and simpler; parallelism is a latency optimization, not a correctness requirement |

---

## 7. Phase 6 Design Directives

Phase 6 must address the following open items identified during analysis:

1. **Correct Phase 8 persona file:** `phase-8-implementation.md` must be updated to `advance: gate`. This is a documentation bug, not an engine change.
2. **Context budget per phase:** Specify the maximum token budget for each phase's context and the excerpt strategy for large deliverables (file path + 500-token summary).
3. **Classifier thresholds:** Define exact score-to-scope-tier mappings for the rule-based classifier.
4. **Error message contract:** Define the structure of the `EscalationRequiredEvent` payload — the information STORY-007 needs to produce a useful human notification.
5. **Fake runner contract:** Specify the deliverable stub files the `FakeRunner` must produce for each phase to enable realistic integration tests.

---

## Next Phase

**Phase 6: Design** — Produce the full specification, architecture, API design, data schema, and implementation plan for the Event-Driven Engine (Approach B, Milestone 1). Address all Phase 6 design directives listed above.
