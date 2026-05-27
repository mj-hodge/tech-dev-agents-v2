# Implementation Plan: SDLC Execution Engine

> Phase 6 — Implementation Plan
> Story: STORY-006
> Date: 2026-03-26
> Scope: Large
> Approach: B — Event-Driven Engine, Milestone 1 (hardcoded metadata)

---

## 1. File Inventory

All files are created under `src/sdlc_engine/` (source) and `tests/sdlc_engine/` (tests).

| # | File | Purpose | Depends On | Est. Lines | Complexity |
|---|------|---------|------------|-----------|------------|
| 1 | `src/sdlc_engine/models.py` | Pydantic models: EngineState, Scope, PhaseStatus, StoryStatus, requests, results, PersonaConfig, EngineConfig | None | 150 | Low |
| 2 | `src/sdlc_engine/exceptions.py` | Exception hierarchy: EngineError, InvalidTransitionError, CheckpointCorruptionError, etc. | None | 40 | Low |
| 3 | `src/sdlc_engine/config.py` | Hardcoded registries (PHASE_PATHS, ADVANCE_CATEGORIES, MODEL_TIERS, DELIVERABLES, CONTEXT_INPUTS, PHASE_TIMEOUTS) + EngineConfig loader | models | 120 | Low |
| 4 | `src/sdlc_engine/events.py` | Typed event dataclasses (frozen) + EventBus class | None | 150 | Low |
| 5 | `src/sdlc_engine/state_machine.py` | StateMachine class with validated transitions | models, exceptions | 80 | Medium |
| 6 | `src/sdlc_engine/checkpoint.py` | CheckpointManager: atomic write, load, delete, event log append | models, exceptions | 120 | Medium |
| 7 | `src/sdlc_engine/plugins.py` | Abstract base classes: RunnerPlugin, TrackerPlugin, NotifierPlugin | models, events | 60 | Low |
| 8 | `src/sdlc_engine/persona_loader.py` | PersonaLoader: reads .sdlc/agents/phase-*.md, returns system prompt | models | 70 | Low |
| 9 | `src/sdlc_engine/context_builder.py` | ContextBuilder: per-phase context construction with token budget | models, config | 150 | Medium |
| 10 | `src/sdlc_engine/classifier.py` | ScopeClassifier: LLM signal extraction + deterministic scoring | models, config, plugins (RunnerPlugin for LLM call) | 130 | Medium |
| 11 | `src/sdlc_engine/engine.py` | SDLCEngine: main orchestration loop, phase execution, advance logic | All above | 300 | High |
| 12 | `src/sdlc_engine/logging_notifier.py` | LoggingNotifierPlugin: reference NotifierPlugin implementation | plugins, events | 40 | Low |
| 13 | `src/sdlc_engine/fake_runner.py` | FakeRunner: test double that writes stub deliverables | plugins, models, config | 100 | Low |
| 14 | `src/sdlc_engine/__init__.py` | Package exports | All | 20 | Low |

**Total estimated source lines:** ~1,530

### Test Files

| # | File | Tests | Depends On |
|---|------|-------|------------|
| T1 | `tests/sdlc_engine/test_models.py` | Model serialization, enum values, validation | models |
| T2 | `tests/sdlc_engine/test_events.py` | EventBus subscribe/publish, error isolation, test_mode | events |
| T3 | `tests/sdlc_engine/test_state_machine.py` | All valid transitions, invalid transition rejection | state_machine |
| T4 | `tests/sdlc_engine/test_checkpoint.py` | Atomic write, load, resume logic, corruption handling | checkpoint |
| T5 | `tests/sdlc_engine/test_config.py` | Registry completeness, ADVANCE_CATEGORIES correctness | config |
| T6 | `tests/sdlc_engine/test_context_builder.py` | Per-phase context, token budget truncation | context_builder |
| T7 | `tests/sdlc_engine/test_classifier.py` | Score calculation, scope mapping, override, epic detection | classifier |
| T8 | `tests/sdlc_engine/test_persona_loader.py` | File discovery, content loading | persona_loader |
| T9 | `tests/sdlc_engine/test_engine.py` | Full lifecycle, gate handling, retry, resume | engine (integration) |
| T10 | `tests/sdlc_engine/test_engine_advance.py` | Advance category logic: gate/confirm/auto per phase | engine |
| T11 | `tests/sdlc_engine/test_engine_bracketed.py` | Bracketed group execution, partial completion | engine |
| T12 | `tests/sdlc_engine/conftest.py` | Shared fixtures: FakeRunner, tmp story folder, sample checkpoint | All |

**Total estimated test count:** ~25-28 tests (within the 30-test guardrail)

---

## 2. Implementation Order

The implementation follows a bottom-up dependency order. Each step produces a commit-worthy unit with passing tests.

### Step 1: Foundation Models and Config
**Files:** `models.py`, `exceptions.py`, `config.py`
**Tests:** `test_models.py`, `test_config.py`
**Commit:** `phase 8: [sdlc-engine] add foundation models, exceptions, and hardcoded phase registries`

**What to build:**
- All Pydantic models: `Scope`, `PhaseStatus`, `StoryStatus`, `PhaseError`, `ScopeClassificationRecord`, `EngineState`, `PhaseExecutionRequest`, `PhaseExecutionResult`, `ExecutionRequest`, `ExecutionResult`, `PersonaConfig`, `EngineConfig`
- Exception classes: `EngineError`, `InvalidTransitionError`, `CheckpointCorruptionError`, `RunnerUnavailableError`, `DeliverableValidationError`, `ScopeClassificationError`
- Hardcoded registries: `PHASE_PATHS`, `ADVANCE_CATEGORIES`, `MODEL_TIERS`, `DELIVERABLES`, `CONTEXT_INPUTS`, `PHASE_TIMEOUTS`
- `load_config(path: Path | None) -> EngineConfig` function

**Test coverage:**
- `EngineState` serializes to JSON and deserializes correctly (round-trip)
- `Scope` and `PhaseStatus` enum values match specification
- All phase IDs in `ADVANCE_CATEGORIES` have corresponding entries in `MODEL_TIERS`, `DELIVERABLES`, and `PHASE_TIMEOUTS`
- `ADVANCE_CATEGORIES` values are all in {"gate", "confirm", "auto"}
- Phase 8 advance is "gate" (regression test for the persona file bug)
- `PHASE_PATHS` covers all five scope tiers

**Estimated effort:** 2-3 hours
**Dependencies:** None

---

### Step 2: Event System
**Files:** `events.py`
**Tests:** `test_events.py`
**Commit:** `phase 8: [sdlc-engine] add typed event system with EventBus`

**What to build:**
- All frozen event dataclasses (11 event types)
- `EventBus` class with `subscribe()`, `unsubscribe()`, `publish()`, `clear()`
- `test_mode` flag for re-raising handler exceptions in tests

**Test coverage:**
- Subscribe a handler, publish an event, assert handler is called with correct event
- Multiple handlers for same event type all fire
- Handler exception in production mode is swallowed (logged)
- Handler exception in test mode is re-raised
- Unsubscribe removes the handler
- Clear removes all handlers
- Events are frozen (cannot be modified after creation)

**Estimated effort:** 1-2 hours
**Dependencies:** None (events are standalone dataclasses)

---

### Step 3: State Machine
**Files:** `state_machine.py`
**Tests:** `test_state_machine.py`
**Commit:** `phase 8: [sdlc-engine] add state machine with validated transitions`

**What to build:**
- `StateMachine` class with `transition_phase()`, `transition_story()`, `is_terminal`, `can_retry`
- `VALID_TRANSITIONS` map
- Transition validation that raises `InvalidTransitionError` on invalid transitions

**Test coverage:**
- Each valid transition succeeds (all 10 transitions in the map)
- Invalid transitions raise `InvalidTransitionError` (e.g., `pending` → `completed`, `blocked` → anything)
- `is_terminal` returns True only for `blocked`
- `can_retry` returns True when `failed` and `retry_count < max_retries`
- `can_retry` returns False when `retry_count >= max_retries`
- `updated_at` timestamp is refreshed on transition

**Estimated effort:** 1-2 hours
**Dependencies:** models, exceptions

---

### Step 4: Checkpoint Manager
**Files:** `checkpoint.py`
**Tests:** `test_checkpoint.py`
**Commit:** `phase 8: [sdlc-engine] add atomic checkpoint manager with event log`

**What to build:**
- `CheckpointManager` class with `write()`, `load()`, `delete()`, `exists()`, `append_event_log()`, `cleanup_temp_files()`
- Atomic write protocol (temp file + `os.replace()`)
- Event log append (`.events.jsonl`, one JSON line per event)
- Corruption recovery: log warning, return None (engine starts fresh)

**Test coverage:**
- Write and load round-trip preserves all fields
- Atomic write: if write is interrupted (simulate by checking temp file), checkpoint file is untouched
- Load returns None for missing checkpoint
- Load returns None for corrupted JSON (logs warning)
- Event log appends correctly (multiple events, one per line)
- cleanup_temp_files removes orphaned `.tmp` files
- Checkpoint file is created in the correct directory

**Estimated effort:** 2-3 hours
**Dependencies:** models, exceptions

---

### Step 5: Plugin Interfaces and Test Doubles
**Files:** `plugins.py`, `logging_notifier.py`, `fake_runner.py`, `conftest.py`
**Tests:** (tested implicitly via engine tests)
**Commit:** `phase 8: [sdlc-engine] add plugin interfaces and test doubles`

**What to build:**
- `RunnerPlugin` ABC with `execute()` method
- `TrackerPlugin` ABC with `on_phase_started()`, `on_phase_completed()`, `on_phase_failed()`, `on_story_completed()`
- `NotifierPlugin` ABC with `on_gate_reached()`, `on_confirmation_requested()`, `on_escalation_required()`
- `LoggingNotifierPlugin` concrete implementation
- `FakeRunner` with configurable failure injection and stub deliverable writing
- `conftest.py` with shared fixtures: `fake_runner`, `tmp_story_folder`, `event_bus`, `sample_engine_state`

**Estimated effort:** 2-3 hours
**Dependencies:** models, events, config (for deliverable file lists)

---

### Step 6: Persona Loader
**Files:** `persona_loader.py`
**Tests:** `test_persona_loader.py`
**Commit:** `phase 8: [sdlc-engine] add persona loader for .sdlc/agents/ files`

**What to build:**
- `PersonaLoader` class with `load(phase_id: str) -> PersonaConfig`
- File discovery: glob `.sdlc/agents/phase-{id}-*.md`
- Content loading: read full file as system prompt
- Error handling: raise clear error if persona file not found

**Test coverage:**
- Loads a known persona file and returns correct system prompt content
- Returns correct phase_id in PersonaConfig
- Raises FileNotFoundError with helpful message for unknown phase ID
- Handles persona files with various naming patterns (phase-1-seed.md, phase-6b-security.md)

**Estimated effort:** 1 hour
**Dependencies:** models

---

### Step 7: Context Builder
**Files:** `context_builder.py`
**Tests:** `test_context_builder.py`
**Commit:** `phase 8: [sdlc-engine] add per-phase context builder with token budget`

**What to build:**
- `ContextBuilder` class with `build(phase_id: str, scope: str, story_folder: Path, task_description: str, deliverables: dict) -> str`
- Context input resolution per phase (from `CONTEXT_INPUTS` registry)
- Token budget estimation (len(text) // 4 as rough heuristic)
- Truncation strategy: priority-based (seed.md always full, design docs excerpted, research/expansion as paths only)
- Deliverable reading: read file content from story folder

**Test coverage:**
- Phase 1 context includes task description only (no prior deliverables)
- Phase 8 context includes test-design.md, design deliverables, seed.md
- Phase 8 context does NOT include research.md, expansion.md, analysis.md, selection.md
- Token budget truncation kicks in when context exceeds 80k tokens
- Missing deliverable file produces a warning in context (not a crash)
- Context includes explicit output instructions ("Write to features/<story>/...")

**Estimated effort:** 2-3 hours
**Dependencies:** models, config

---

### Step 8: Scope Classifier
**Files:** `classifier.py`
**Tests:** `test_classifier.py`
**Commit:** `phase 8: [sdlc-engine] add scope classifier with rule-based scoring`

**What to build:**
- `ScopeClassifier` class with `classify(request: ExecutionRequest) -> ScopeClassification`
- Override path: if `scope_override` is set, return immediately
- Signal extraction: construct a structured prompt, send via RunnerPlugin, parse ScopeSignals response
- Deterministic scoring function: apply weights, map score to scope
- Epic detection: 2+ epic signals → EPIC
- Guardrail warnings: check sizing limits
- Fallback on LLM failure: conservative defaults → SMALL

**Test coverage:**
- Override bypasses all classification logic
- Score 0 → TRIVIAL, score 1-3 → SMALL, score 4-9 → MEDIUM, score 10+ → LARGE
- Epic detection: 2 epic signals → EPIC, 1 signal → not epic
- Guardrail warnings generated for >30 tests, >2 models, >3 endpoints
- LLM extraction failure falls back to SMALL with warning
- Classification rationale is populated

**Estimated effort:** 2-3 hours
**Dependencies:** models, config, plugins (RunnerPlugin interface for LLM call)

---

### Step 9: Engine Core — Orchestration Loop
**Files:** `engine.py`, `__init__.py`
**Tests:** `test_engine.py`, `test_engine_advance.py`, `test_engine_bracketed.py`
**Commit:** `phase 8: [sdlc-engine] add engine orchestration loop with full lifecycle`

**What to build:**
- `SDLCEngine` class with `execute(request: ExecutionRequest) -> ExecutionResult`
- Internal event handler registration (`_on_approval`, `_on_confirmation`)
- Phase execution loop: iterate phase path, execute each phase, validate, advance
- Advance category handling: gate (halt + emit), confirm (halt + emit), auto (continue)
- Bracketed group handling: sequential iteration, group completion tracking
- Retry logic: enhanced retry prompt, retry_count tracking
- Resume from checkpoint: detect existing checkpoint, apply resume rules
- Scope reassessment: detect signal, emit event, halt for approval, recalculate path
- Error recovery: 2 failures → escalation
- Story completion: emit StoryCompletedEvent

**Test coverage:**
- **Full lifecycle (small scope):** execute() drives through 1 → 7 → 8 with FakeRunner. All phases complete. StoryCompletedEvent emitted. All deliverables present.
- **Gate handling:** Phase 1 (gate) halts execution. ApprovalReceivedEvent resumes. Rejection blocks.
- **Confirm handling:** Phase 2 (confirm) halts execution. ConfirmationReceivedEvent resumes. Decline blocks.
- **Auto handling:** Phase 6b (auto) proceeds immediately to 6c without halting.
- **Retry on failure:** FakeRunner configured to fail Phase 7 once. Engine retries. Second attempt succeeds.
- **Escalation after 2 failures:** FakeRunner configured to fail Phase 7 twice. Engine emits EscalationRequiredEvent. Story status is "failed".
- **Resume from checkpoint:** Pre-write a checkpoint at Phase 4 "completed". Engine resumes from Phase 5 (or next in path). Phases 1-4 are NOT re-executed.
- **Resume from waiting_for_approval:** Pre-write checkpoint with "waiting_for_approval". Engine re-emits GateReachedEvent without re-executing the phase.
- **Bracketed group:** Medium-scope path includes [6b, 6c, 6d]. All three execute sequentially. Engine advances to Phase 7 after all three complete.
- **Deliverable validation failure:** FakeRunner produces no files. Engine detects missing deliverable. Retries with enhanced prompt.

**Estimated effort:** 4-6 hours
**Dependencies:** All previous steps

---

### Step 10: Integration Validation
**Tests:** (additions to `test_engine.py`)
**Commit:** `phase 8: [sdlc-engine] add integration tests for full engine lifecycle`

**What to build:**
- End-to-end integration test: full large-scope execution with FakeRunner
- Checkpoint persistence validation: engine creates checkpoint, process "restarts" (new engine instance), resumes correctly
- Event sequence validation: capture all events via test subscriber, assert correct order
- Event log validation: read `.events.jsonl`, verify all transitions are recorded

**Estimated effort:** 2-3 hours
**Dependencies:** All source modules

---

## 3. Commit Sequence

| Commit | Step | Files | Boundary |
|--------|------|-------|----------|
| 1 | Step 1 | models.py, exceptions.py, config.py, test_models.py, test_config.py | Foundation complete |
| 2 | Step 2 | events.py, test_events.py | Event system complete |
| 3 | Step 3 | state_machine.py, test_state_machine.py | State machine complete |
| 4 | Step 4 | checkpoint.py, test_checkpoint.py | Persistence complete |
| 5 | Step 5 | plugins.py, logging_notifier.py, fake_runner.py, conftest.py | Plugin layer complete |
| 6 | Step 6 | persona_loader.py, test_persona_loader.py | Persona loading complete |
| 7 | Step 7 | context_builder.py, test_context_builder.py | Context construction complete |
| 8 | Step 8 | classifier.py, test_classifier.py | Classification complete |
| 9 | Step 9 | engine.py, __init__.py, test_engine*.py | Engine core complete |
| 10 | Step 10 | test_engine.py (additions) | Integration validated |

Each commit is independently testable — running `pytest tests/sdlc_engine/` after each commit should produce all-green results for the tests written up to that point.

---

## 4. Dependency Graph

```
models.py ◄─────┬──── exceptions.py
    │            │
    ├────────────┼──── config.py
    │            │
    │            ├──── events.py (standalone)
    │            │
    ├────────────┼──── state_machine.py
    │            │
    ├────────────┼──── checkpoint.py
    │            │
    ├────────────┼──── plugins.py ◄── events.py
    │            │
    ├────────────┼──── persona_loader.py
    │            │
    ├────────────┼──── context_builder.py ◄── config.py
    │            │
    ├────────────┼──── classifier.py ◄── config.py, plugins.py
    │            │
    └────────────┴──── engine.py ◄── ALL ABOVE
                       │
                       ├── logging_notifier.py
                       └── fake_runner.py
```

**Key observation:** `models.py` and `exceptions.py` have zero internal dependencies and are the foundation. `events.py` is also standalone. These three can be built in parallel.

---

## 5. Integration Points with Other Stories

### 5.1 STORY-003 (Claude Code Runner) — RunnerPlugin

**Integration timing:** Engine can be built and tested entirely with `FakeRunner`. When STORY-003 delivers a programmatic runner interface, a `ClaudeCodeRunnerPlugin` adapter is written that implements `RunnerPlugin` and wraps the STORY-003 API. No engine changes required.

**Interface contract:**
- Engine calls `runner.execute(PhaseExecutionRequest)` → `PhaseExecutionResult`
- Runner is responsible for model selection, subprocess lifecycle, timeout enforcement
- Runner returns structured results; engine never parses runner output directly

**Bootstrap (before STORY-003):** `LocalRunner` invokes `claude` CLI via subprocess. This is a thin, temporary adapter that lets the engine be tested with real LLM calls before STORY-003 is complete.

### 5.2 STORY-005 (Persona System) — PersonaLoader

**Integration timing:** Engine ships with a minimal `PersonaLoader` that reads persona files directly. When STORY-005 delivers a persona system API, the engine's `PersonaLoader` is replaced with a `PersonaSystemAdapter` that calls the STORY-005 interface. No engine changes required — `PersonaLoader` is used internally, not exposed as a plugin.

**Interface contract:**
- Engine calls `persona_loader.load(phase_id)` → `PersonaConfig`
- PersonaConfig contains system_prompt, tool_permissions
- Advance category and model tier are NOT loaded from personas in V1 (hardcoded registry)

### 5.3 STORY-004 (Monday.com Tracker) — TrackerPlugin

**Integration timing:** Engine ships with `LoggingTrackerPlugin`. When STORY-004 delivers Monday.com MCP integration, a `MondayTrackerPlugin` is written that implements `TrackerPlugin` and uses MCP tools. No engine changes required.

**Interface contract:**
- Engine publishes events → EventBus → TrackerPlugin methods
- Tracker updates Monday.com task (comment + status) and `.project` file
- Tracker failures are non-fatal (5-second timeout, errors logged and swallowed)

### 5.4 STORY-007 (Approval Flow) — NotifierPlugin + Event Bus

**Integration timing:** Engine ships with `LoggingNotifierPlugin`. STORY-007 implements a `TeamsNotifierPlugin` and wires it to the engine's event bus. STORY-007 also implements the inbound side: receiving Teams responses and publishing `ApprovalReceivedEvent` / `ConfirmationReceivedEvent` to the engine's bus.

**Outbound contract:**
- Engine emits `GateReachedEvent`, `ConfirmationRequestedEvent`, `EscalationRequiredEvent`
- NotifierPlugin receives these and delivers to Teams

**Inbound contract:**
- External system publishes `ApprovalReceivedEvent(story_id, phase_id, approved)` to bus
- Engine's internal handler resumes or blocks execution

### 5.5 STORY-008 (Git Operations) — Event Bus Subscriber

**Integration timing:** STORY-008 subscribes to the engine's event bus. No plugin interface needed — pure event subscription.

**Events consumed by STORY-008:**
- `PhaseCompletedEvent` → auto-commit phase deliverables
- `GateReachedEvent` (Phase 11) → create PR
- `StoryCompletedEvent` → finalize branch

---

## 6. Test Strategy

### 6.1 Unit Tests

Each module has its own test file. Tests use `FakeRunner`, `LoggingNotifierPlugin`, and `LoggingTrackerPlugin` as test doubles. No external dependencies (no filesystem beyond `tmp_path`, no network, no LLM calls).

**Key unit test patterns:**
- State machine: pre-set a state, call transition, assert new state or exception
- Checkpoint: write state to `tmp_path`, load it back, assert equality
- EventBus: subscribe a recording lambda, publish event, assert captured
- Classifier: pass pre-built `ScopeSignals`, assert correct scope mapping
- ContextBuilder: create stub deliverable files in `tmp_path`, assert context string contains correct files

### 6.2 Integration Tests

Integration tests in `test_engine.py` exercise the full engine with `FakeRunner`:

- **Happy path (small scope):** Phases 1 → 7 → 8. All gates auto-approved via test harness.
- **Happy path (medium scope):** Full medium path including bracketed group.
- **Failure and retry:** Phase configured to fail once, then succeed.
- **Escalation:** Phase configured to fail twice.
- **Checkpoint resume:** Write checkpoint, create new engine instance, assert correct resume.
- **Event sequence:** Capture all events, assert correct ordering and completeness.

### 6.3 CI Validation Test

A special test in `test_config.py` validates the hardcoded registries against `AGENTS.md`:

```python
def test_advance_categories_match_agents_md():
    """Validate hardcoded ADVANCE_CATEGORIES against AGENTS.md."""
    # Parse the advance categories from AGENTS.md text
    # Assert they match config.ADVANCE_CATEGORIES
    # This catches drift when AGENTS.md is updated but config.py is not
```

This test reads `AGENTS.md` and extracts the gate/confirm/auto assignments from the "Hard Stop Rules" section, then asserts they match the hardcoded values. It runs on every CI build.

---

## 7. Milestone 2 Scope (Phase 9 — Deferred)

The following items are explicitly deferred from the Phase 8 implementation. They are documented here as the target for Phase 9 refinement.

| Item | Description | Prerequisite |
|------|-------------|--------------|
| PhaseRegistry loader | Parse YAML Identity blocks from persona files to replace hardcoded `ADVANCE_CATEGORIES` and `MODEL_TIERS` | Persona file format stability |
| `.sdlc/phase-paths.yaml` | Machine-readable phase path definitions | Author from AGENTS.md |
| config.yaml overrides | `advance_overrides`, `phases.skip` support | PhaseRegistry |
| Async event bus | `asyncio`-compatible publish with async handlers | Profiling shows tracker latency is a problem |
| Parallel bracketed groups | `asyncio.gather()` for [6b,6c,6d] and [9,10] | Async event bus |
| Context budget auto-tuning | Dynamic token budget based on model context window | Model metadata from STORY-003 |
| Event log replay | Reconstruct state from `.events.jsonl` when snapshot is corrupted | Event log format stabilization |

---

## 8. Risk Mitigations (Implementation-Specific)

| Risk | Mitigation in Implementation |
|------|------------------------------|
| FakeRunner stubs do not match real deliverables | FakeRunner reads expected filenames from `DELIVERABLES` registry — same source of truth as the engine |
| Checkpoint format changes break resume | `version` field in checkpoint. Loader checks version and migrates if needed. V1 → V2 migration is a function, not a schema-level concern. |
| Phase timeout too short for real LLM calls | Timeouts are in `PHASE_TIMEOUTS` dict — easily adjusted without code changes. Default 600s is conservative. |
| Test count exceeds 30-test guardrail | Current estimate is 25-28 tests. If Phase 7 test design produces >30, split classifier tests into a separate story. |
| Engine.execute() becomes a god method | Keep `execute()` under 50 lines. Delegate to private methods: `_execute_phase()`, `_handle_advance()`, `_handle_failure()`, `_handle_group()`, `_resume_from_checkpoint()`. |
