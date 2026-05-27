# Expansion: SDLC Execution Engine — Architectural Approaches

> Phase 3 — Expansion
> Story: STORY-006
> Date: 2026-03-26
> Scope: Large

---

## Summary

This document presents three distinct architectural approaches for implementing the SDLC Execution Engine. Each approach is evaluated across five dimensions: implementation complexity, testability, extensibility, alignment with the existing `.sdlc/` framework, and risk. A recommendation follows the comparative analysis.

---

## Approach A: Minimal State Machine (Conservative)

### Overview

A single Python module (`engine.py`) containing a class (`SDLCEngine`) that manages phase progression via explicit `if/elif` state transition guards. State is persisted to a JSON checkpoint file at every phase boundary. Phase paths and advance categories are hardcoded as Python dictionaries in the module. The engine calls the Claude Code Runner and persona loader directly as function imports.

### Architecture

```
sdlc_engine/
├── engine.py              # SDLCEngine class — all orchestration logic
├── classifier.py          # Scope classifier (LLM + rule-based hybrid)
├── checkpoint.py          # Atomic JSON read/write
├── context_builder.py     # Builds per-phase prompt context
├── persona_loader.py      # Minimal .sdlc/agents/ reader
├── models.py              # Pydantic state/checkpoint models
└── config.py              # Loads config.yaml, hardcoded PHASE_PATHS and DELIVERABLES
```

**Core data structures:**

```python
# config.py — hardcoded phase registry
PHASE_PATHS: dict[str, list] = {
    "trivial": ["8"],
    "small":   ["1", "7", "8"],
    "medium":  ["1", "4", "6", ["6b", "6c", "6d"], "7", "8", "8b", "11"],
    "large":   ["1", "2", "3", "4", "5", "6", ["6b", "6c", "6d"],
                "7", "8", "8b", "11", ["9", "10"]],
}

ADVANCE_CATEGORIES: dict[str, str] = {
    "1": "gate", "2": "confirm", "3": "confirm", "4": "confirm",
    "5": "confirm", "6": "confirm", "6b": "auto", "6c": "auto",
    "6d": "auto", "7": "confirm", "8": "gate", "8b": "auto",
    "9": "confirm", "10": "confirm", "11": "gate",
}

MODEL_TIERS: dict[str, str] = {
    "1": "tier-1", "2": "tier-2", "3": "tier-2", "4": "tier-2",
    "5": "tier-2", "6": "tier-1", "6b": "tier-2", "6c": "tier-2",
    "6d": "tier-2", "7": "tier-2", "8": "tier-2", "8b": "tier-2",
    "9": "tier-1", "10": "tier-1", "11": "tier-2",
}

DELIVERABLES: dict[str, list[str]] = {
    "1": ["seed.md"], "2": ["research.md"], "3": ["expansion.md"],
    "4": ["analysis.md"], "5": ["selection.md"],
    "6_large": ["specification.md", "architecture.md", "api-design.md",
                "database-schema.md", "implementation-plan.md"],
    "6_medium": ["feature-spec.md"],
    "6b": ["security-review.md"], "6c": ["ux-review.md"],
    "6d": ["ops-review.md"], "7": ["test-design.md"],
    "8b": ["code-review.md"], "11": ["predeploy-gate.md"],
    "9": ["refinement-report.md"], "10": ["site-reliability.md"],
}
```

**State machine transitions — explicit guards:**

```python
class SDLCEngine:
    def advance(self) -> None:
        phase = self.state.current_phase
        advance_cat = ADVANCE_CATEGORIES[phase]

        if self.state.phase_status == "completed":
            if advance_cat == "gate":
                self._transition("waiting_for_approval")
                self._emit("gate_reached", phase)
            elif advance_cat == "confirm":
                self._transition("waiting_for_confirmation")
                self._emit("confirmation_requested", phase)
            elif advance_cat == "auto":
                self._move_to_next_phase()

        elif self.state.phase_status == "failed":
            if self.state.retry_count < 2:
                self._transition("in_progress")
                self._retry_phase()
            else:
                self._transition("blocked")
                self._emit("escalation_required", phase)
```

**Event emission — simple callback registry:**

```python
class SDLCEngine:
    def __init__(self):
        self._listeners: dict[str, list[Callable]] = defaultdict(list)

    def on(self, event: str, handler: Callable) -> None:
        self._listeners[event].append(handler)

    def _emit(self, event: str, *args, **kwargs) -> None:
        for handler in self._listeners[event]:
            handler(*args, **kwargs)
```

STORY-007 registers its approval handler via `engine.on("gate_reached", handle_gate)`.

**Checkpoint write (before and after each phase):**

```python
# checkpoint.py
def write_checkpoint(path: Path, state: EngineState) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(state.model_dump_json(indent=2))
    os.replace(tmp, path)   # POSIX atomic rename
```

**Bracketed groups — sequential emulation:**

When `current_phase_entry` is a list (`["6b", "6c", "6d"]`), the engine iterates through the sub-phases sequentially. Each sub-phase is checkpointed individually. The group advances only after all sub-phases are `completed`.

### Evaluation

**Implementation Complexity: LOW**

The entire engine can be implemented as ~600–800 lines of Python with no external framework dependencies beyond Pydantic and PyYAML. The state transitions are explicit, readable, and directly traceable to the seed.md state diagram. No event loop, no concurrency management, no plugin loading.

Estimated implementation time: 3–4 days for a complete, tested V1.

**Testability: HIGH**

- All state transitions are direct method calls — no need to mock event loops or message buses
- `SDLCEngine` takes the runner and persona loader as constructor injections, enabling full mock substitution in tests
- Checkpoint files are plain JSON — test setup and teardown is trivial (write a JSON fixture, assert JSON after execution)
- Each state transition can be unit-tested in isolation with a pre-loaded checkpoint
- The callback registry (`on()` / `_emit()`) can be tested by registering a recorder and asserting events fired

Example test pattern:
```python
def test_gate_phase_emits_event():
    events = []
    engine = SDLCEngine(runner=MockRunner(), persona_loader=MockPersonaLoader())
    engine.on("gate_reached", lambda p, s: events.append(p))
    engine.state.phase_status = "completed"
    engine.state.current_phase = "1"
    engine.advance()
    assert events == ["1"]
    assert engine.state.phase_status == "waiting_for_approval"
```

**Extensibility: LOW–MEDIUM**

Adding a new advance category requires modifying both `ADVANCE_CATEGORIES` and the `advance()` method's `if/elif` chain. Adding new phase paths (e.g., a new scope tier) requires editing the hardcoded `PHASE_PATHS` dict. Neither is difficult, but neither is data-driven — the code must be changed for framework changes.

New subscribers (STORY-007, STORY-008) can register via the callback `on()` interface without modifying the engine, which is correct. The plugin surface is limited to event callbacks; the runner and persona loader are injected but not plugin-loaded at runtime.

The advance category discrepancy (Phase 8: `gate` vs `confirm`) is resolved by editing `ADVANCE_CATEGORIES` — a one-line change.

**Alignment with .sdlc/ Framework: MEDIUM**

The engine hardcodes the SDLC's phase paths and advance categories in Python, which means it encodes the framework at a point in time. When `AGENTS.md` or a persona file changes, the engine code must be manually updated. There is no automatic pickup of framework changes.

This is a deliberate trade-off in this approach: simplicity over dynamism. The framework changes infrequently (it is a stable process definition), so manual synchronization is low-risk.

The persona loader does read live from `.sdlc/agents/phase-*.md` for the system prompt content and tool permissions, so phase behavioral changes (what the agent does) are picked up automatically. Only structural metadata (advance category, model tier) is hardcoded.

**Risk: LOW**

- No dependency on external services or complex infrastructure
- All failure modes are simple: file I/O errors, runner errors, JSON parse errors
- Resume from checkpoint is straightforward: load JSON, check `phase_status`, apply deterministic resume rules
- The callback registry has no ordering guarantees or error isolation between handlers — a buggy STORY-007 handler could raise an exception inside `_emit()`. **Mitigation:** wrap each handler call in `try/except` and log failures without propagating.

### Pros

- Fastest path to a working, tested engine
- Easiest to understand, debug, and maintain
- No external framework risk (version conflicts, deprecations, behavioral surprises)
- All state transitions are visible in source code — no "magic" routing
- Container restart recovery is trivially simple: read checkpoint JSON, match phase_status to resume rule

### Cons

- Phase paths and advance categories are hardcoded — framework changes require code changes
- Bracketed groups run sequentially only — no parallel execution in V1
- The callback registry is synchronous and in-process — if STORY-007 needs async notification delivery, the handler must manage its own async context
- No built-in support for scheduled retries (engine retries immediately on failure, not after a delay)
- `SDLCEngine` class will accumulate methods over time — risk of becoming a "god class" without discipline

---

## Approach B: Event-Driven Engine (Balanced)

### Overview

The engine is built around an internal event bus. All phase transitions, gate detections, deliverable validations, and tracking updates are modeled as events. Components subscribe to relevant events and react. Phase metadata (paths, advance categories, deliverables) is loaded from the `.sdlc/` framework at startup rather than hardcoded. A plugin interface allows runners, trackers, and notifiers to be registered independently.

### Architecture

```
sdlc_engine/
├── engine.py              # SDLCEngine — orchestration loop
├── event_bus.py           # EventBus (sync/async, per-event typing)
├── phase_registry.py      # Loads phase metadata from .sdlc/ at startup
├── checkpoint.py          # Atomic JSON checkpoint with event log
├── context_builder.py     # Phase-specific context construction
├── classifier.py          # Scope classification (LLM + rule-based)
├── models.py              # Pydantic event, state, checkpoint models
├── plugins/
│   ├── base.py            # Plugin abstract base classes
│   ├── runner_plugin.py   # Claude Code Runner plugin interface
│   ├── tracker_plugin.py  # Monday.com / .project tracker plugin
│   └── notifier_plugin.py # Approval flow notifier plugin
└── config.py              # Config loading (config.yaml + framework)
```

**Phase registry — loaded from .sdlc/ at startup:**

```python
# phase_registry.py
class PhaseRegistry:
    def __init__(self, sdlc_dir: Path, config: EngineConfig):
        self._phases: dict[str, PhaseDefinition] = {}
        self._phase_paths: dict[str, list] = {}
        self._load_from_agents_dir(sdlc_dir / "agents")
        self._load_phase_paths_from_agents_readme(sdlc_dir)
        self._apply_config_overrides(config)

    def _load_from_agents_dir(self, agents_dir: Path) -> None:
        for persona_file in sorted(agents_dir.glob("phase-*.md")):
            phase_id, metadata = self._parse_persona_file(persona_file)
            self._phases[phase_id] = PhaseDefinition(
                phase_id=phase_id,
                advance=metadata["advance"],
                model_tier=metadata["model"],
                system_prompt_path=persona_file,
                context_group=metadata.get("context_group"),
            )

    def _parse_persona_file(self, path: Path) -> tuple[str, dict]:
        content = path.read_text()
        # Extract YAML Identity block from fenced code block
        match = re.search(r"```yaml\n(.*?)```", content, re.DOTALL)
        if match:
            metadata = yaml.safe_load(match.group(1))
        phase_id = re.search(r"phase-(\w+)-", path.name).group(1)
        return phase_id, metadata

    def get_phase_path(self, scope: str) -> list:
        return self._phase_paths[scope]

    def get_advance(self, phase_id: str) -> str:
        override = self._config.advance_overrides.get(phase_id)
        return override or self._phases[phase_id].advance
```

**Event bus — typed events with sync and async support:**

```python
# event_bus.py
@dataclass
class PhaseCompletedEvent:
    story_id: str
    phase: str
    deliverables: list[str]
    duration: float

@dataclass
class GateReachedEvent:
    story_id: str
    phase: str
    summary: str

class EventBus:
    def __init__(self):
        self._handlers: dict[type, list[Callable]] = defaultdict(list)

    def subscribe(self, event_type: type, handler: Callable) -> None:
        self._handlers[event_type].append(handler)

    def publish(self, event: Any) -> None:
        for handler in self._handlers[type(event)]:
            try:
                handler(event)
            except Exception as e:
                logger.error("Handler %s failed on %s: %s", handler, event, e)
```

**Plugin interface:**

```python
# plugins/base.py
class RunnerPlugin(ABC):
    @abstractmethod
    def execute(self, request: PhaseExecutionRequest) -> PhaseExecutionResult:
        ...

class TrackerPlugin(ABC):
    @abstractmethod
    def update_phase_status(self, story_id: str, phase: str, status: str) -> None:
        ...
    @abstractmethod
    def add_phase_comment(self, story_id: str, phase: str, summary: str) -> None:
        ...

class NotifierPlugin(ABC):
    @abstractmethod
    def handle_gate(self, event: GateReachedEvent) -> None:
        ...
    @abstractmethod
    def handle_confirmation(self, event: ConfirmationRequestedEvent) -> None:
        ...
```

**Engine orchestration loop:**

```python
class SDLCEngine:
    def __init__(self, registry: PhaseRegistry, bus: EventBus,
                 runner: RunnerPlugin, tracker: TrackerPlugin):
        self.registry = registry
        self.bus = bus
        self.runner = runner
        self.tracker = tracker
        # Subscribe to own events for state management
        self.bus.subscribe(ApprovalReceivedEvent, self._on_approval)
        self.bus.subscribe(ConfirmationReceivedEvent, self._on_confirmation)

    def run_phase(self, phase_id: str) -> None:
        self.state.phase_status = "in_progress"
        self._write_checkpoint()
        self.bus.publish(PhaseStartedEvent(story_id=self.state.story_id,
                                           phase=phase_id,
                                           scope=self.state.scope))
        result = self.runner.execute(self._build_request(phase_id))
        self._validate_and_transition(phase_id, result)

    def _validate_and_transition(self, phase_id: str, result: PhaseExecutionResult) -> None:
        if not result.success or not self._deliverables_present(phase_id):
            self._handle_failure(phase_id)
            return
        self.state.phase_status = "completed"
        self.bus.publish(PhaseCompletedEvent(...))
        self.tracker.add_phase_comment(...)
        self._write_checkpoint()
        advance = self.registry.get_advance(phase_id)
        if advance == "gate":
            self.state.phase_status = "waiting_for_approval"
            self.bus.publish(GateReachedEvent(...))
        elif advance == "confirm":
            self.state.phase_status = "waiting_for_confirmation"
            self.bus.publish(ConfirmationRequestedEvent(...))
        elif advance == "auto":
            self._move_to_next_phase()
        self._write_checkpoint()
```

**Checkpoint with dual write (snapshot + event log):**

```python
# checkpoint.py
class CheckpointManager:
    def write(self, state: EngineState) -> None:
        """Atomic snapshot write."""
        tmp = self.snapshot_path.with_suffix(".tmp")
        tmp.write_text(state.model_dump_json(indent=2))
        os.replace(tmp, self.snapshot_path)

    def append_event(self, event: dict) -> None:
        """Non-atomic append to event log (append is atomic on Linux for <4096 bytes)."""
        with self.event_log_path.open("a") as f:
            f.write(json.dumps({**event, "ts": datetime.utcnow().isoformat()}) + "\n")
```

**Bracketed groups — sequential with logical group tracking:**

The `PhaseRegistry` knows which phases are in a group. The engine exposes a `run_group()` method that iterates sub-phases sequentially, tracking each in the checkpoint's `group_status` dict. The group advances only when all sub-phases are `completed`. Group-level advance category is `auto` (proceed after all complete) since individual phases within the group have their own advance categories.

### Evaluation

**Implementation Complexity: MEDIUM**

More moving parts than Approach A, but each component is focused and independently testable. The event bus, plugin interfaces, phase registry, and checkpoint manager are distinct modules. The phase registry loader requires a reliable YAML parser and regex for the fenced block extraction — this is the trickiest single component (persona files must be consistently structured for this to work).

Estimated implementation time: 5–7 days for a complete, tested V1.

**Testability: HIGH**

- Event bus enables fine-grained unit testing: publish a synthetic event, assert handlers fire
- Plugin interfaces (abstract base classes) enable clean mocking for runner, tracker, notifier
- Phase registry is independently testable: point at a test `.sdlc/agents/` dir, assert correct PhaseDefinition objects load
- Checkpoint manager is independently testable: assert snapshot and event log files are written correctly
- Integration tests can use a minimal fake runner (`FakeRunner`) that always returns success and writes stub deliverable files
- The dual checkpoint (snapshot + event log) gives a replay path for debugging failures in CI

**Extensibility: HIGH**

- New phase metadata (e.g., a `context_budget` field) is added to the persona file YAML and `PhaseDefinition` model — no engine code change
- New advance categories are handled by adding a branch in `_validate_and_transition()` — one place
- New plugins (e.g., a Slack notifier, a GitHub PR opener) implement the plugin interface and are registered at engine construction — no engine code change
- The `config.yaml` `advance_overrides` field is natively consumed by the phase registry — framework operators can change behavior without code changes
- Phase paths live in a parseable source (`.sdlc/agents/README.md` or `AGENTS.md`) — when the SDLC process changes, the registry reloads correctly at next startup

**Alignment with .sdlc/ Framework: HIGH**

The phase registry reads live from `.sdlc/agents/phase-*.md`. Every advance category, model tier, and system prompt update in the framework is automatically picked up at engine startup. The phase paths are parsed from the framework's authoritative source (`AGENTS.md` or `.sdlc/agents/README.md`) rather than hardcoded.

`config.yaml` override support (`advance_overrides`, `opus_allowed`, `phases.skip`) is natively integrated via the registry's `_apply_config_overrides()` method. This directly implements the config schema from `.sdlc/templates/config.yaml`.

The event interface defined in seed.md § Event Interface maps directly to the event bus event types — the events are typed Python dataclasses matching the seed specification exactly.

**Risk: MEDIUM**

- Phase registry parsing requires that all persona files follow a consistent YAML Identity block structure. A malformed persona file (missing `advance:` field, incorrectly fenced YAML block) causes a registry load failure at startup. **Mitigation:** validate all registry entries at startup with clear error messages; fall back to hardcoded defaults for missing fields.
- Event bus error isolation (each handler in `try/except`) prevents cascading failures but can silently swallow important errors. **Mitigation:** failed handler errors are logged at ERROR level and emitted as `handler_error` events; a test mode option re-raises handler exceptions.
- The synchronous event bus means `publish()` blocks until all handlers complete. A slow tracker plugin (e.g., Monday.com MCP call) adds latency between phases. **Mitigation:** tracker plugin calls are fire-and-forget with a configurable timeout.

### Pros

- Framework changes (persona file edits, new advance categories) are automatically picked up at startup — no code changes needed for SDLC process evolution
- Plugin interfaces make STORY-007 (approval flow) and STORY-008 (git operations) clean integrations
- Event log provides a full audit trail of all phase transitions — invaluable for debugging failures
- Typed events (Pydantic dataclasses) make the event contract explicit and discoverable
- `advance_overrides` in `config.yaml` is natively consumed — project-level customization without code forks
- Each component (bus, registry, checkpoint, context builder) is independently testable

### Cons

- Phase registry parsing adds fragility: if persona file structure changes, the regex/YAML extractor breaks. Requires a contract between the persona file format and the engine
- More moving parts than Approach A — a new contributor must understand the event bus and plugin system before making changes
- Synchronous event bus means handler errors can block phase progression if not carefully isolated
- Parsing phase paths from the SDLC markdown documents (`AGENTS.md`) is non-trivial — the format is a Markdown table, not a machine-readable YAML/JSON. This may require a dedicated parser or a companion YAML file
- The dual checkpoint (snapshot + event log) adds I/O overhead and a secondary failure mode (log append fails)

---

## Approach C: Workflow DSL (Innovative)

### Overview

The SDLC process is represented as a declarative YAML workflow definition. Phase paths, advance categories, deliverable requirements, model tiers, timeout values, and context inputs are all configuration-driven. The engine is a pure interpreter of this workflow definition — it contains no knowledge of specific phases or scopes. Changing the SDLC process means changing the YAML workflow file, not the engine code.

### Architecture

```
sdlc_engine/
├── interpreter.py         # WorkflowInterpreter — reads and executes workflow YAML
├── engine.py              # SDLCEngine — thin wrapper over interpreter
├── event_bus.py           # EventBus (same as Approach B)
├── checkpoint.py          # Atomic checkpoint (same as Approach B)
├── context_builder.py     # Context construction from workflow context_inputs
├── classifier.py          # Scope classification
├── models.py              # Pydantic workflow, state, event models
└── config.py              # Config loading

sdlc/workflows/
└── standard.yaml          # The SDLC workflow definition (hot-reloadable)
```

**Workflow definition — `sdlc/workflows/standard.yaml`:**

```yaml
version: "1.0"
name: standard-sdlc

scopes:
  trivial:
    phase_path: ["8"]
  small:
    phase_path: ["1", "7", "8"]
  medium:
    phase_path: ["1", "4", "6", ["6b", "6c", "6d"], "7", "8", "8b", "11"]
  large:
    phase_path: ["1", "2", "3", "4", "5", "6", ["6b", "6c", "6d"],
                 "7", "8", "8b", "11", ["9", "10"]]

phases:
  "1":
    name: Seed
    advance: gate
    model_tier: tier-1
    timeout_minutes: 15
    deliverables:
      - seed.md
    context_inputs: []
    persona_file: "phase-1-seed.md"

  "2":
    name: Research
    advance: confirm
    model_tier: tier-2
    timeout_minutes: 30
    deliverables:
      - research.md
    context_inputs:
      - seed.md
    persona_file: "phase-2-research.md"

  "6":
    name: Design
    advance: confirm
    model_tier: tier-1
    timeout_minutes: 30
    deliverables:
      scope_conditional:
        large: ["specification.md", "architecture.md", "api-design.md",
                "database-schema.md", "implementation-plan.md"]
        medium: ["feature-spec.md"]
    context_inputs:
      scope_conditional:
        large: ["seed.md", "selection.md"]
        medium: ["seed.md", "analysis.md"]
    persona_file: "phase-6-design.md"

  # ... remaining phases

gates:
  - phase: "1"
    type: gate
    description: "Seed approved — scope and approach confirmed"
  - phase: "8"
    type: gate
    description: "Implementation approved — code review and tests passed"
  - phase: "11"
    type: gate
    description: "Pre-deploy gate — production readiness confirmed"

story_sizing_guardrails:
  max_tests: 30
  max_db_models: 2
  max_api_endpoints: 3
  max_architectural_layers: 1
```

**Workflow interpreter:**

```python
# interpreter.py
class WorkflowInterpreter:
    def __init__(self, workflow_path: Path, config: EngineConfig):
        self._workflow = self._load_workflow(workflow_path)
        self._config = config
        self._last_modified = workflow_path.stat().st_mtime

    def _load_workflow(self, path: Path) -> WorkflowDefinition:
        raw = yaml.safe_load(path.read_text())
        return WorkflowDefinition.model_validate(raw)

    def maybe_hot_reload(self, path: Path) -> bool:
        """Check if workflow file changed; reload if so. Returns True if reloaded."""
        mtime = path.stat().st_mtime
        if mtime > self._last_modified:
            self._workflow = self._load_workflow(path)
            self._last_modified = mtime
            return True
        return False

    def resolve_phase_path(self, scope: str) -> list:
        return self._workflow.scopes[scope].phase_path

    def get_phase_definition(self, phase_id: str) -> PhaseDefinition:
        return self._workflow.phases[phase_id]

    def resolve_deliverables(self, phase_id: str, scope: str) -> list[str]:
        phase = self._workflow.phases[phase_id]
        if isinstance(phase.deliverables, dict):  # scope_conditional
            return phase.deliverables.scope_conditional.get(scope, [])
        return phase.deliverables

    def resolve_context_inputs(self, phase_id: str, scope: str) -> list[str]:
        phase = self._workflow.phases[phase_id]
        if isinstance(phase.context_inputs, dict):  # scope_conditional
            return phase.context_inputs.scope_conditional.get(scope, [])
        return phase.context_inputs
```

**Engine — thin orchestration layer:**

```python
# engine.py
class SDLCEngine:
    def __init__(self, interpreter: WorkflowInterpreter, bus: EventBus,
                 runner: RunnerPlugin, tracker: TrackerPlugin):
        self.interpreter = interpreter
        ...

    def run_phase(self, phase_id: str) -> None:
        phase_def = self.interpreter.get_phase_definition(phase_id)
        context = self._build_context(phase_id, phase_def)
        persona = self._load_persona(phase_def.persona_file)
        result = self.runner.execute(PhaseExecutionRequest(
            system_prompt=persona.system_prompt,
            user_prompt=context,
            model_tier=phase_def.model_tier,
            tool_permissions=persona.tool_permissions,
            timeout=phase_def.timeout_minutes * 60,
        ))
        expected_deliverables = self.interpreter.resolve_deliverables(
            phase_id, self.state.scope)
        self._validate_and_transition(phase_id, result, expected_deliverables,
                                      phase_def.advance)

    def _build_context(self, phase_id: str, phase_def: PhaseDefinition) -> str:
        input_files = self.interpreter.resolve_context_inputs(phase_id, self.state.scope)
        return self.context_builder.build(
            story_folder=self.state.story_folder,
            task_description=self.state.task_description,
            input_files=input_files,
        )
```

**Hot-reload support:**

Between phases, the engine calls `interpreter.maybe_hot_reload(workflow_path)`. If the workflow file was modified (e.g., an operator changed a timeout value or added a new phase to a scope's path), the interpreter reloads without restarting the engine process. The reload only affects the *remaining* phase path — already-completed phases are unaffected.

```python
def execute(self) -> None:
    while not self._is_complete():
        self.interpreter.maybe_hot_reload(self.workflow_path)
        current_phase = self.state.current_phase
        self.run_phase(current_phase)
        self._await_advance_if_needed(current_phase)
```

**Workflow validation at startup:**

```python
class WorkflowValidator:
    def validate(self, workflow: WorkflowDefinition) -> list[str]:
        errors = []
        for scope, scope_def in workflow.scopes.items():
            for phase_entry in flatten(scope_def.phase_path):
                if phase_entry not in workflow.phases:
                    errors.append(f"Scope {scope} references undefined phase {phase_entry}")
        for phase_id, phase_def in workflow.phases.items():
            if phase_def.advance not in ("gate", "confirm", "auto"):
                errors.append(f"Phase {phase_id}: invalid advance '{phase_def.advance}'")
            if not (Path(".sdlc/agents") / phase_def.persona_file).exists():
                errors.append(f"Phase {phase_id}: persona file not found: {phase_def.persona_file}")
        return errors
```

### Evaluation

**Implementation Complexity: HIGH**

Three layers must be built and maintained: the workflow YAML schema (`WorkflowDefinition` Pydantic model), the interpreter that reads and executes it, and the engine orchestration loop. The scope-conditional deliverables and context inputs require a discriminated union type in Pydantic, which adds schema complexity. The hot-reload logic requires careful state management to avoid inconsistency between a partially-executed story and a reloaded workflow.

Estimated implementation time: 8–12 days for a complete, tested V1. The YAML schema definition and interpreter are non-trivial. The workflow validator must be comprehensive to catch configuration errors early.

**Testability: MEDIUM–HIGH**

- The interpreter is independently testable with synthetic workflow YAML
- The engine is testable with a mock interpreter and mock runner
- Hot-reload behavior requires dedicated integration tests with file modification simulation
- YAML workflow validation is testable: write invalid YAML fixtures, assert correct validation errors
- However, the "schema of the workflow" itself is a new artifact that requires its own testing strategy — a schema change that introduces a bug in `scope_conditional` logic can affect all phases silently
- Testing the full matrix of scope × phase combinations requires more fixtures than Approach A or B

**Extensibility: VERY HIGH**

This is the approach's core strength. Adding a new scope tier (e.g., "micro" between trivial and small) requires only a new entry in `scopes:` in the YAML — no engine code change. Adding a new advance category requires adding it to the YAML schema and one branch in the interpreter's `_advance()` method. Adding a new per-phase field (e.g., `max_retries`, `context_budget`) requires adding it to the `PhaseDefinition` Pydantic model and using it in the interpreter — the workflow YAML is the configuration surface.

Hot-reload enables live tuning: an operator can change a timeout value in the YAML and it takes effect for the next phase without restarting the container. This is particularly valuable during the initial deployment period when calibration is needed.

**Alignment with .sdlc/ Framework: MEDIUM**

The workflow YAML is a *parallel representation* of the SDLC framework rather than a direct reading of it. The engine does not parse `AGENTS.md` or persona YAML Identity blocks — it reads the workflow YAML. This means the framework and the workflow YAML can drift out of sync. A change to `AGENTS.md`'s advance category table does not automatically update `standard.yaml`.

Mitigating this: the workflow YAML can be generated from the `.sdlc/` framework (a one-time generation step that extracts advance categories, model tiers, etc.). The generator becomes a maintenance tool. This is a process overhead that Approach B avoids by reading the framework directly.

The persona files are still referenced by filename in the workflow YAML (`persona_file: "phase-6-design.md"`), so persona content changes are picked up automatically. Only the structural metadata in the workflow YAML can drift.

**Risk: HIGH**

- **Workflow drift:** The `standard.yaml` and `AGENTS.md` can diverge. Without a CI check that validates the workflow against the framework, the engine silently implements a different process than the documented one. This is the most significant risk of this approach.
- **Schema complexity:** The `scope_conditional` union type (deliverables can be a list OR a dict with scope keys) is a source of validation bugs and confusing YAML. A typo in a scope key (`lareg` instead of `large`) silently produces an empty deliverables list.
- **Hot-reload edge cases:** Reloading a workflow during a bracketed group (where one sub-phase completed but others haven't) can produce inconsistent state if the group definition changed. **Mitigation:** prohibit hot-reload during group execution.
- **Persona file still required:** Despite the workflow being declarative, the persona files remain the source of system prompt content. The YAML adds a layer without fully eliminating the `phase-*.md` dependency.

### Pros

- Framework evolution (new phases, new scopes, changed timeouts) requires only YAML changes — zero code deployments
- Hot-reload enables live calibration without container restarts
- The workflow YAML is the single source of truth for phase orchestration — readable by non-engineers
- All scope-conditional logic (different deliverables for large vs medium) is explicit in YAML rather than scattered through code
- The interpreter layer cleanly separates "what the SDLC process is" from "how to execute it"
- A workflow diff shows exactly what changed in the SDLC process — useful for auditing process evolution

### Cons

- Highest implementation complexity — three distinct layers (workflow schema, interpreter, engine) all must be built and tested
- The workflow YAML can drift from `AGENTS.md` unless a CI validation step enforces consistency
- `scope_conditional` in YAML is an ergonomics challenge — easy to make typos, hard to review in code review
- Hot-reload adds state management complexity that Approaches A and B avoid entirely
- Adds a new artifact type (`.yaml` workflow files) that the team must understand and maintain alongside the existing `.sdlc/` markdown files
- Persona file parsing is still required for system prompt content — the DSL does not eliminate markdown files

---

## Comparative Analysis

| Dimension | A: Minimal State Machine | B: Event-Driven Engine | C: Workflow DSL |
|-----------|--------------------------|------------------------|-----------------|
| Implementation complexity | Low | Medium | High |
| Testability | High | High | Medium–High |
| Extensibility | Low–Medium | High | Very High |
| Framework alignment | Medium (hardcoded) | High (reads live) | Medium (parallel artifact) |
| Risk | Low | Medium | High |
| Estimated implementation time | 3–4 days | 5–7 days | 8–12 days |
| Dependencies added | Pydantic, PyYAML | Pydantic, PyYAML | Pydantic, PyYAML |
| Hot-reload support | No | No (restart needed) | Yes |
| True parallel bracketed groups | No (V1) | No (V1) | No (V1) |
| Framework drift risk | High (hardcoded) | Low (reads live) | Medium (parallel YAML) |
| Config override support | Manual | Native (config.yaml) | Native (YAML overrides) |
| Debug/audit capability | Moderate | High (event log) | High (event log + YAML diff) |
| STORY-007/008 integration | Callback registry | Plugin interfaces | Plugin interfaces |

---

## Key Tensions

### Tension 1: Simplicity vs Framework Alignment

Approach A is the simplest implementation but encodes the SDLC framework at a point in time. Every `AGENTS.md` change requires a corresponding code change in `config.py`. Approach B eliminates this by reading live from persona files, at the cost of a more complex registry parser. Approach C goes further (full DSL) but introduces a new drift risk.

**Resolution:** Approach B achieves the best balance. The persona file YAML Identity blocks are the canonical machine-readable metadata, and parsing them is a well-bounded problem with a clear format. The phase paths (the only data not in persona files) can be encoded as a small config block in `config.yaml` or a companion YAML file, avoiding any need to parse Markdown tables from `AGENTS.md`.

### Tension 2: Testability vs Extensibility

Approach A's explicit state transitions are maximally testable but minimally extensible. Approach C's interpreter is highly extensible but the schema adds testing surface. Approach B threads the needle: the plugin interfaces and event bus are both highly testable (via fakes and mock subscribers) and highly extensible (new subscribers and plugins without engine changes).

### Tension 3: Time to Working V1

The seed.md constraint is "engine drives execution through correct phase sequence." Approach A can deliver a working engine in 3–4 days. Approaches B and C take 5–12 days. Given that this engine unblocks STORY-007 (Approval Flow) and STORY-008 (Git Operations), delivery speed matters.

**Resolution:** Approach B's event bus and plugin interfaces are the target architecture, but the implementation can start with a hybrid: hand-roll the state machine (Approach A style) with the event bus and plugin interfaces from Approach B (skipping the runtime phase registry load for V1, substituting hardcoded metadata). The registry parser and config override support can be added in Phase 9 refinement without changing the engine's external interface.

---

## Recommendation

**Primary recommendation: Approach B (Event-Driven Engine), implemented in two milestones.**

**Milestone 1 (V1 — Phase 8 deliverable):**
Implement the event bus, plugin interfaces, checkpoint manager, context builder, and state machine. Phase metadata (advance categories, model tiers, phase paths, deliverables) is hardcoded in `config.py` as in Approach A. This delivers a working, tested engine in 4–5 days.

**Milestone 2 (Phase 9 refinement):**
Implement the live phase registry loader (reads from `.sdlc/agents/phase-*.md`) and `config.yaml` override support. Replace the hardcoded `config.py` values with registry lookups. This delivers full framework alignment without requiring a rewrite of the engine.

**Rationale:**
- The event bus and plugin interfaces are the right architecture for STORY-007 and STORY-008 integration — starting with callbacks (Approach A) would require a rewrite when STORY-007 needs to be decoupled
- Hardcoding metadata in V1 (rather than parsing persona files) reduces implementation risk while still delivering all acceptance criteria
- The plugin interface for the runner (STORY-003) and tracker (Monday.com) enables the engine to be tested end-to-end before those dependencies are complete
- The dual checkpoint (snapshot + event log) is worth the extra I/O overhead for the debugging value it provides during initial deployment

**Approach C is deferred** until there is evidence that the SDLC process changes frequently enough to justify the hot-reload and DSL complexity. If the phase paths and advance categories are stable over the first 6 months of production use (likely, given the framework's maturity), the DSL adds complexity without benefit.

---

## Open Questions for Phase 6 Design

1. **Phase 8 advance category (gate vs confirm):** Research confirmed the discrepancy between `phase-8-implementation.md` (confirm) and `AGENTS.md` + seed.md (gate). Phase 6 must resolve this definitively and update the authoritative source.

2. **Phase path source of truth:** Where do the canonical phase paths live in a machine-readable format? Options: (a) add a `phase-paths.yaml` to `.sdlc/`; (b) encode in `config.yaml` template; (c) parse from `AGENTS.md` Markdown table. This affects the Milestone 2 registry implementation.

3. **Parallel bracketed groups in V1 or deferred:** The seed specifies that `[6b, 6c, 6d]` "can run in parallel" (seed.md § Assumptions). The analysis recommends sequential for V1. Does the team want sequential-first with a Phase 9 upgrade path, or is parallel required for the V1 acceptance criteria?

4. **Context budget enforcement:** Research flagged context window overflow risk for Large-scope stories. Phase 6 design must specify the context budget limit and summarization strategy for Phase 8 (which has the largest context surface: design deliverables + test-design.md + seed.md).

5. **Event bus async vs sync:** For STORY-007 (Teams notification), the notifier plugin likely involves an async HTTP call (Teams webhook). Should the event bus support async handlers from V1, or should the notifier plugin manage its own async context?

---

## Next Phase

**Phase 4 (Analysis):** Evaluate the three approaches against specific implementation risks and architectural constraints. Particular focus areas: (1) the hybrid V1/V2 implementation plan for Approach B, (2) the phase registry parser contract (what persona file structure is required), (3) the exact Pydantic model hierarchy for checkpoint, event, and phase state objects, and (4) resolution of the open questions above.
