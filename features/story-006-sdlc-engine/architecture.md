# Architecture: SDLC Execution Engine

> Phase 6 — System Architecture
> Story: STORY-006
> Date: 2026-03-26
> Scope: Large
> Approach: B — Event-Driven Engine, Milestone 1 (hardcoded metadata)

---

## 1. Component Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                         SDLCEngine                                  │
│                                                                     │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────────────┐ │
│  │ ScopeClassi- │  │ StateMachine │  │     ContextBuilder        │ │
│  │ fier         │  │              │  │                           │ │
│  │              │  │ phase_status │  │ - resolve context inputs  │ │
│  │ - extract    │  │ story_status │  │ - apply token budget      │ │
│  │   signals    │  │ transitions  │  │ - build user prompt       │ │
│  │ - score      │  │              │  │                           │ │
│  │ - classify   │  │              │  │                           │ │
│  └──────────────┘  └──────────────┘  └───────────────────────────┘ │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                        EventBus                              │   │
│  │                                                              │   │
│  │  publish(event) ─────────► [handler1, handler2, handler3]    │   │
│  │  subscribe(EventType, handler)                               │   │
│  │                                                              │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌──────────────────────────┐  ┌─────────────────────────────────┐ │
│  │   CheckpointManager      │  │      PhaseMetadata (config.py)  │ │
│  │                          │  │                                 │ │
│  │ - write (atomic)         │  │  PHASE_PATHS                   │ │
│  │ - load                   │  │  ADVANCE_CATEGORIES             │ │
│  │ - delete                 │  │  MODEL_TIERS                    │ │
│  │ - append_event_log       │  │  DELIVERABLES                   │ │
│  │                          │  │  CONTEXT_INPUTS                 │ │
│  │ .checkpoint.json         │  │  PHASE_TIMEOUTS                 │ │
│  │ .events.jsonl            │  │                                 │ │
│  └──────────────────────────┘  └─────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
         │                    │                    │
         │ execute()          │ publish()          │ on_*()
         ▼                    ▼                    ▼
┌─────────────┐    ┌──────────────┐    ┌──────────────────┐
│ RunnerPlugin│    │TrackerPlugin │    │ NotifierPlugin   │
│ (ABC)       │    │ (ABC)        │    │ (ABC)            │
│             │    │              │    │                  │
│ .execute()  │    │ .on_phase_   │    │ .on_gate_reached │
│             │    │  completed() │    │ .on_confirmation │
│             │    │ .on_phase_   │    │  _requested()    │
│             │    │  failed()    │    │ .on_escalation_  │
│             │    │ .on_story_   │    │  required()      │
│             │    │  completed() │    │                  │
└──────┬──────┘    └──────┬───────┘    └────────┬─────────┘
       │                  │                     │
       ▼                  ▼                     ▼
  STORY-003          STORY-004             STORY-007
  Claude Code        Monday.com +          Teams Approval
  Runner             .project tracker      Flow
```

---

## 2. Module Structure

```
sdlc_engine/
├── __init__.py             # Package exports: SDLCEngine, events, plugins
├── engine.py               # SDLCEngine class — main orchestration loop
├── events.py               # Typed event dataclasses + EventBus
├── state_machine.py        # StateMachine — phase/story state transitions
├── plugins.py              # RunnerPlugin, TrackerPlugin, NotifierPlugin ABCs
├── classifier.py           # ScopeClassifier — LLM signal extraction + rule scoring
├── checkpoint.py           # CheckpointManager — atomic JSON read/write + event log
├── context_builder.py      # ContextBuilder — per-phase prompt context construction
├── persona_loader.py       # PersonaLoader — reads .sdlc/agents/phase-*.md
├── models.py               # Pydantic models: EngineState, Checkpoint, requests, results
├── config.py               # Hardcoded registries + EngineConfig loader
├── exceptions.py           # Engine-specific exception types
├── logging_notifier.py     # LoggingNotifierPlugin — reference NotifierPlugin impl
└── fake_runner.py          # FakeRunner — test double that writes stub deliverables
```

---

## 3. Plugin Interfaces

### 3.1 RunnerPlugin

Wraps the Claude Code Runner (STORY-003). The engine never calls the Claude API directly — all LLM invocations go through this interface.

```python
from abc import ABC, abstractmethod

class RunnerPlugin(ABC):
    """Interface for executing a single SDLC phase via an LLM runner."""

    @abstractmethod
    def execute(self, request: PhaseExecutionRequest) -> PhaseExecutionResult:
        """
        Execute a phase and return the result.

        The implementation is responsible for:
        - Invoking the correct model (per request.model_tier)
        - Applying the system prompt and tool permissions
        - Enforcing the timeout
        - Returning structured results including success/failure, output files, and errors

        Args:
            request: Phase execution parameters (system prompt, user prompt,
                     model tier, tool permissions, working directory, timeout)

        Returns:
            PhaseExecutionResult with success status, output files, errors, duration

        Raises:
            RunnerUnavailableError: If the runner cannot be reached
        """
        ...
```

**V1 implementations:**
- `FakeRunner` — Test double. Always succeeds. Writes stub deliverable files to the story folder. Used in unit and integration tests.
- `LocalRunner` — Bootstrap implementation. Invokes `claude` CLI via subprocess with the constructed prompt. Bridges the gap until STORY-003 provides a programmatic interface.

### 3.2 TrackerPlugin

Wraps Monday.com status updates and `.project` file management (STORY-004).

```python
class TrackerPlugin(ABC):
    """Interface for updating external tracking systems after phase transitions."""

    @abstractmethod
    def on_phase_started(self, event: PhaseStartedEvent) -> None:
        """Update tracker with phase-in-progress status."""
        ...

    @abstractmethod
    def on_phase_completed(self, event: PhaseCompletedEvent) -> None:
        """
        Update tracker with phase completion.

        Implementations should:
        - Update Monday.com task with a comment (phase name, deliverables, duration)
        - Update Monday.com task status column
        - Update .project file with current phase status
        - Update backlog.md with story status
        """
        ...

    @abstractmethod
    def on_phase_failed(self, event: PhaseFailedEvent) -> None:
        """Update tracker with phase failure details."""
        ...

    @abstractmethod
    def on_story_completed(self, event: StoryCompletedEvent) -> None:
        """
        Update tracker with story completion.

        Implementations should:
        - Move Monday.com task to Done
        - Update .project, backlog.md, development-tasks.md
        """
        ...
```

**V1 implementations:**
- `LoggingTrackerPlugin` — Logs all tracking events to stdout. No external calls.
- `FileTrackerPlugin` — Updates `.project` file only (no Monday.com). Suitable for local development.

### 3.3 NotifierPlugin

Wraps gate and confirmation notifications (STORY-007).

```python
class NotifierPlugin(ABC):
    """Interface for delivering gate/confirm/escalation notifications."""

    @abstractmethod
    def on_gate_reached(self, event: GateReachedEvent) -> None:
        """
        Notify that a gate has been reached and approval is required.

        Implementations should deliver the notification to the appropriate channel
        (Teams, email, webhook) and include the gate summary and deliverables list.
        """
        ...

    @abstractmethod
    def on_confirmation_requested(self, event: ConfirmationRequestedEvent) -> None:
        """
        Notify that confirmation is requested to proceed to the next phase.

        Implementations should deliver a "Proceed to Phase X?" prompt.
        """
        ...

    @abstractmethod
    def on_escalation_required(self, event: EscalationRequiredEvent) -> None:
        """
        Notify that human intervention is required due to repeated failures.

        Implementations should deliver an urgent notification with full error context.
        """
        ...
```

**V1 implementations:**
- `LoggingNotifierPlugin` — Logs all notifications to stdout with structured fields. Ships as the default notifier and serves as a reference implementation for STORY-007.

---

## 4. Event Taxonomy

### 4.1 Event Definitions

All events are frozen dataclasses. They are immutable after creation to prevent handlers from modifying event state.

```python
from dataclasses import dataclass, field
from datetime import datetime

@dataclass(frozen=True)
class PhaseStartedEvent:
    story_id: str
    phase_id: str
    scope: str
    timestamp: datetime = field(default_factory=datetime.utcnow)

@dataclass(frozen=True)
class PhaseCompletedEvent:
    story_id: str
    phase_id: str
    deliverables: tuple[str, ...]   # Immutable sequence of file paths
    duration_seconds: float
    timestamp: datetime = field(default_factory=datetime.utcnow)

@dataclass(frozen=True)
class PhaseFailedEvent:
    story_id: str
    phase_id: str
    error_type: str                 # "runner_error" | "timeout" | "missing_deliverable" | "empty_deliverable"
    error_message: str
    retry_count: int
    timestamp: datetime = field(default_factory=datetime.utcnow)

@dataclass(frozen=True)
class GateReachedEvent:
    story_id: str
    phase_id: str
    summary: str
    deliverables: tuple[str, ...]
    timestamp: datetime = field(default_factory=datetime.utcnow)

@dataclass(frozen=True)
class ConfirmationRequestedEvent:
    story_id: str
    phase_id: str
    next_phase: str
    summary: str
    timestamp: datetime = field(default_factory=datetime.utcnow)

@dataclass(frozen=True)
class EscalationRequiredEvent:
    story_id: str
    phase_id: str
    error_type: str
    error_message: str
    retry_count: int
    phase_duration_total: float
    last_prompt_summary: str
    deliverables_produced: tuple[str, ...]
    checkpoint_path: str
    timestamp: datetime = field(default_factory=datetime.utcnow)

@dataclass(frozen=True)
class StoryCompletedEvent:
    story_id: str
    scope: str
    phases_completed: tuple[str, ...]
    total_duration_seconds: float
    deliverables: dict[str, tuple[str, ...]]   # phase_id → file paths
    timestamp: datetime = field(default_factory=datetime.utcnow)

@dataclass(frozen=True)
class ScopeReassessedEvent:
    story_id: str
    old_scope: str
    new_scope: str
    trigger_phase: str
    rationale: str
    timestamp: datetime = field(default_factory=datetime.utcnow)

@dataclass(frozen=True)
class EngineErrorEvent:
    story_id: str
    error_type: str
    error_message: str
    traceback: str
    timestamp: datetime = field(default_factory=datetime.utcnow)

# --- Events consumed by the engine (published by external systems) ---

@dataclass(frozen=True)
class ExecutionRequestedEvent:
    story_id: str
    story_slug: str
    task_description: str
    codebase_context: str | None = None
    scope_override: str | None = None

@dataclass(frozen=True)
class ApprovalReceivedEvent:
    story_id: str
    phase_id: str
    approved: bool
    comment: str | None = None

@dataclass(frozen=True)
class ConfirmationReceivedEvent:
    story_id: str
    phase_id: str
    confirmed: bool
    comment: str | None = None
```

### 4.2 EventBus

The EventBus is a synchronous, in-process publish/subscribe system keyed by event type.

```python
import logging
from collections import defaultdict
from typing import Any, Callable

logger = logging.getLogger(__name__)

class EventBus:
    """Synchronous typed event bus with error isolation."""

    def __init__(self, *, test_mode: bool = False):
        self._handlers: dict[type, list[Callable]] = defaultdict(list)
        self._test_mode = test_mode  # If True, re-raise handler exceptions

    def subscribe(self, event_type: type, handler: Callable) -> None:
        """Register a handler for an event type."""
        self._handlers[event_type].append(handler)

    def unsubscribe(self, event_type: type, handler: Callable) -> None:
        """Remove a handler registration."""
        self._handlers[event_type].remove(handler)

    def publish(self, event: Any) -> None:
        """
        Publish an event to all subscribed handlers.

        Each handler is called in registration order. Handler exceptions are:
        - In test_mode: re-raised immediately (fail-fast for tests)
        - In production: logged at ERROR level and swallowed (error isolation)
        """
        for handler in self._handlers[type(event)]:
            try:
                handler(event)
            except Exception as e:
                if self._test_mode:
                    raise
                logger.error(
                    "Handler %s failed on %s: %s",
                    handler.__qualname__, type(event).__name__, e,
                    exc_info=True
                )

    def clear(self) -> None:
        """Remove all handler registrations. Used in test teardown."""
        self._handlers.clear()
```

**Design decisions:**
- **Synchronous:** Handlers execute in-line. The tracker plugin manages its own timeout (5s) for Monday.com API calls. An async bus is deferred to Milestone 2.
- **Error isolation:** A failing handler does not block other handlers or the engine. In tests, `test_mode=True` surfaces handler bugs immediately.
- **Type-keyed:** `subscribe(PhaseCompletedEvent, handler)` — no string matching, no typo risk. Adding a new event type is a new dataclass, not a new string constant.

---

## 5. State Machine

### 5.1 State Machine Component

The `StateMachine` encapsulates all phase and story state transitions. It validates transitions against the allowed transition table and raises on invalid transitions.

```python
class StateMachine:
    """
    Manages phase-level and story-level state transitions.

    All transitions are validated against the allowed transition map.
    Invalid transitions raise InvalidTransitionError.
    """

    VALID_TRANSITIONS: dict[str, set[str]] = {
        # Phase states
        "pending":                    {"in_progress"},
        "in_progress":                {"completed", "failed", "waiting_for_approval", "waiting_for_confirmation"},
        "completed":                  {"pending"},     # next phase starts as pending
        "failed":                     {"in_progress", "blocked"},
        "waiting_for_approval":       {"completed", "blocked"},
        "waiting_for_confirmation":   {"completed", "blocked"},
        "blocked":                    set(),           # terminal — requires manual intervention
    }

    def __init__(self, state: EngineState):
        self._state = state

    def transition_phase(self, new_status: str) -> None:
        """Transition the current phase to a new status."""
        current = self._state.phase_status
        if new_status not in self.VALID_TRANSITIONS.get(current, set()):
            raise InvalidTransitionError(
                f"Cannot transition from '{current}' to '{new_status}'"
            )
        self._state.phase_status = new_status
        self._state.updated_at = datetime.utcnow()

    def transition_story(self, new_status: str) -> None:
        """Transition the story to a new status."""
        self._state.story_status = new_status
        self._state.updated_at = datetime.utcnow()

    @property
    def is_terminal(self) -> bool:
        """Whether the current phase is in a terminal state (blocked)."""
        return self._state.phase_status == "blocked"

    @property
    def can_retry(self) -> bool:
        """Whether the current phase can be retried."""
        return (
            self._state.phase_status == "failed"
            and self._state.retry_count < self._state.max_retries
        )
```

### 5.2 State Machine Diagram

```
                    ┌──────────────────────────────────────────┐
                    │              PHASE STATES                 │
                    │                                          │
   ┌─────────┐     │    ┌─────────────┐                       │
   │ pending │─────┼───►│ in_progress │                       │
   └─────────┘     │    └──────┬──────┘                       │
       ▲           │           │                              │
       │           │     ┌─────┼──────────────────┐           │
  [next phase      │     │     │                  │           │
   starts]         │     ▼     ▼                  ▼           │
       │           │  ┌──────┐ ┌──────────────┐ ┌───────────┐│
       │           │  │failed│ │waiting_for_  │ │waiting_for││
       │           │  │      │ │approval      │ │confirm    ││
       │           │  └──┬───┘ └──────┬───────┘ └─────┬─────┘│
       │           │     │            │               │       │
       │           │  ┌──┴──┐    ┌────┴────┐    ┌─────┴───┐  │
       │           │  │retry│    │approved │    │confirmed│  │
       │           │  │count│    │  ?      │    │  ?      │  │
       │           │  │< 2? │    └────┬────┘    └────┬────┘  │
       │           │  └──┬──┘         │              │       │
       │           │  yes│  no   yes  │  no     yes  │  no   │
       │           │     │   │        │   │          │   │   │
       │           │     ▼   ▼        ▼   ▼          ▼   ▼   │
       │           │  ┌────┐┌───────┐┌─────────┐           │ │
       └───────────┼──│comp││blocked││completed│           │ │
                   │  │lted│└───────┘└─────────┘           │ │
                   │  └────┘     ▲         ▲    ┌────────┐ │ │
                   │             └─────────┼────│blocked │ │ │
                   │                       │    └────────┘ │ │
                   └───────────────────────┘───────────────┘ │
                                                              │
                    ┌──────────────────────────────────────────┐
                    │              STORY STATES                 │
                    │                                          │
                    │  not_started → in_progress → completed   │
                    │                            ↘ blocked     │
                    │                            ↘ failed      │
                    └──────────────────────────────────────────┘
```

---

## 6. Data Flow

### 6.1 Normal Phase Execution (auto advance)

```
SDLCEngine.execute()
  │
  ├─ 1. CheckpointManager.write(phase_status="in_progress")
  │
  ├─ 2. EventBus.publish(PhaseStartedEvent)
  │     ├─► TrackerPlugin.on_phase_started()     → Monday.com "Phase X in progress"
  │     └─► CheckpointManager.append_event_log() → .events.jsonl
  │
  ├─ 3. ContextBuilder.build(phase_id, scope)
  │     ├─ Read prior deliverables from features/<story>/
  │     ├─ Apply token budget (truncate if > 80k tokens)
  │     └─ Return constructed user_prompt string
  │
  ├─ 4. PersonaLoader.load(phase_id)
  │     ├─ Read .sdlc/agents/phase-{id}-*.md
  │     └─ Return system_prompt text
  │
  ├─ 5. RunnerPlugin.execute(PhaseExecutionRequest)
  │     ├─ Invoke Claude with system_prompt + user_prompt
  │     ├─ Enforce timeout
  │     └─ Return PhaseExecutionResult
  │
  ├─ 6. Validate deliverables
  │     ├─ Check expected files exist in features/<story>/
  │     └─ Check file sizes > 100 bytes
  │
  ├─ 7. StateMachine.transition_phase("completed")
  │
  ├─ 8. CheckpointManager.write(phase_status="completed")
  │
  ├─ 9. EventBus.publish(PhaseCompletedEvent)
  │     ├─► TrackerPlugin.on_phase_completed()   → Monday.com comment + status
  │     └─► CheckpointManager.append_event_log()
  │
  └─ 10. Advance: auto → move to next phase, loop back to step 1
```

### 6.2 Gate Phase Execution

```
Steps 1–9 same as above, then:

  ├─ 10. ADVANCE_CATEGORIES[phase] == "gate"
  │
  ├─ 11. StateMachine.transition_phase("waiting_for_approval")
  │
  ├─ 12. CheckpointManager.write(phase_status="waiting_for_approval")
  │
  ├─ 13. EventBus.publish(GateReachedEvent)
  │     ├─► NotifierPlugin.on_gate_reached()     → Teams notification (STORY-007)
  │     └─► CheckpointManager.append_event_log()
  │
  └─ 14. HALT execution loop. Return to caller.

  ... time passes ... human reviews ...

  ApprovalReceivedEvent published to bus
  │
  ├─► SDLCEngine._on_approval(event)
  │   ├─ if approved: StateMachine.transition_phase("completed")
  │   │               → move to next phase, resume loop
  │   └─ if rejected: StateMachine.transition_phase("blocked")
  │                   → emit EscalationRequiredEvent, halt
  │
  └─► CheckpointManager.write(updated state)
```

### 6.3 Failure and Retry Flow

```
Phase execution fails (runner error, missing deliverable, timeout):

  ├─ 1. StateMachine.transition_phase("failed")
  │
  ├─ 2. Record error in checkpoint: errors.append({phase, type, message, timestamp})
  │
  ├─ 3. EventBus.publish(PhaseFailedEvent)
  │     ├─► TrackerPlugin.on_phase_failed()
  │     └─► CheckpointManager.append_event_log()
  │
  ├─ 4. Check retry eligibility: retry_count < max_retries?
  │
  ├─ YES: retry_count += 1
  │  ├─ Construct enhanced retry prompt (explicit deliverable instructions)
  │  ├─ StateMachine.transition_phase("in_progress")  [failed → in_progress]
  │  ├─ CheckpointManager.write()
  │  └─ Re-execute phase (loop back to RunnerPlugin.execute)
  │
  └─ NO: retry_count >= max_retries
     ├─ StateMachine.transition_phase("blocked")  [failed → blocked]
     ├─ StateMachine.transition_story("failed")
     ├─ EventBus.publish(EscalationRequiredEvent)
     │   └─► NotifierPlugin.on_escalation_required()  → urgent Teams notification
     ├─ CheckpointManager.write()
     └─ HALT execution loop
```

### 6.4 Resume from Checkpoint Flow

```
SDLCEngine.__init__() detects existing checkpoint:

  ├─ 1. CheckpointManager.load()
  │     ├─ Read .checkpoint.json
  │     ├─ Validate JSON structure
  │     └─ Return EngineState
  │
  ├─ 2. Clean up orphaned temp files (.checkpoint.json.tmp)
  │
  ├─ 3. Evaluate phase_status:
  │
  │  ┌─ "completed"
  │  │  └─ Move to next phase in path. Resume normal execution loop.
  │  │
  │  ├─ "in_progress"
  │  │  └─ Phase was interrupted. Increment retry_count.
  │  │     Re-execute phase from scratch (fresh runner invocation).
  │  │
  │  ├─ "waiting_for_approval"
  │  │  └─ Re-emit GateReachedEvent. Do NOT re-execute phase.
  │  │     Halt and wait for ApprovalReceivedEvent.
  │  │
  │  ├─ "waiting_for_confirmation"
  │  │  └─ Re-emit ConfirmationRequestedEvent. Do NOT re-execute.
  │  │     Halt and wait for ConfirmationReceivedEvent.
  │  │
  │  ├─ "failed" (retry_count < max)
  │  │  └─ Retry phase.
  │  │
  │  └─ "blocked" or "failed" (retry_count >= max)
  │     └─ Emit EscalationRequiredEvent. Halt.
```

---

## 7. Integration Points

### 7.1 STORY-003 (Claude Code Runner)

**Integration surface:** `RunnerPlugin` abstract base class.

**Contract:** The engine constructs a `PhaseExecutionRequest` and passes it to the runner. The runner is responsible for invoking the correct model, enforcing timeout, and returning structured results. The engine has no knowledge of the runner's internal implementation (subprocess, API call, etc.).

**Bootstrap (until STORY-003 is complete):** A `LocalRunner` implementation invokes the `claude` CLI via `subprocess.run()`:
- System prompt passed via `--system-prompt` flag or a temp file
- User prompt passed via stdin
- Model tier mapped to `--model` flag
- Timeout enforced via `subprocess.run(timeout=...)`
- Exit code checked for success/failure
- Output files detected by scanning the story folder for new/modified files

### 7.2 STORY-005 (Persona System)

**Integration surface:** `PersonaLoader` class (internal, not a plugin).

**Contract:** Given a phase ID, return the system prompt text and tool permissions list. The advance category and model tier are NOT loaded from personas in V1 — they come from the hardcoded registry.

**Bootstrap (until STORY-005 is complete):** The `PersonaLoader` reads `.sdlc/agents/phase-{id}-*.md` directly:
1. Glob for files matching the pattern
2. Read the file content as the system prompt
3. Return a `PersonaConfig(system_prompt=content, tool_permissions=["all"])`

### 7.3 STORY-004 (Monday.com Tracker)

**Integration surface:** `TrackerPlugin` abstract base class.

**Contract:** The tracker receives phase events and updates Monday.com and `.project`. The engine calls tracker methods within the event bus publish cycle, with a 5-second timeout. Tracker failures are logged but do not halt execution.

**V1 behavior:** `LoggingTrackerPlugin` logs events. `FileTrackerPlugin` updates `.project` only. Full Monday.com integration is wired when STORY-004 provides an MCP-based tracker implementation.

### 7.4 STORY-007 (Approval Flow)

**Integration surface:** `NotifierPlugin` abstract base class + `ApprovalReceivedEvent` / `ConfirmationReceivedEvent` on the event bus.

**Outbound:** When the engine reaches a gate or confirm phase, it emits a `GateReachedEvent` or `ConfirmationRequestedEvent`. The notifier plugin delivers these to Teams/email.

**Inbound:** When the human responds, STORY-007 publishes an `ApprovalReceivedEvent` or `ConfirmationReceivedEvent` to the engine's event bus. The engine's internal handler resumes execution.

**V1 behavior:** `LoggingNotifierPlugin` logs gate events. Approval/confirmation signals must be manually published to the bus (e.g., via a CLI command or test harness).

### 7.5 STORY-008 (Git Operations)

**Integration surface:** Event bus subscription.

**Contract:** STORY-008 subscribes to `PhaseCompletedEvent` (for auto-commit after phases) and `GateReachedEvent` at Phase 11 (for PR creation). The engine does not need to know about git — STORY-008 is a bus subscriber only.

---

## 8. Pydantic Models

### 8.1 Core State Model

```python
from pydantic import BaseModel, Field
from datetime import datetime
from enum import Enum

class Scope(str, Enum):
    TRIVIAL = "trivial"
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"
    EPIC = "epic"

class PhaseStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    WAITING_FOR_CONFIRMATION = "waiting_for_confirmation"
    BLOCKED = "blocked"

class StoryStatus(str, Enum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"

class PhaseError(BaseModel):
    phase_id: str
    error_type: str
    error_message: str
    retry_count: int
    timestamp: datetime

class ScopeClassificationRecord(BaseModel):
    scope: Scope
    signals: dict       # ScopeSignals as dict
    rationale: str
    method: str         # "override" | "rule_based" | "llm_hybrid"
    guardrail_warnings: list[str] = []

class EngineState(BaseModel):
    """Full engine state — serialized to .checkpoint.json."""
    version: int = 1
    story_id: str
    story_slug: str
    task_description: str
    scope: Scope
    scope_classification: ScopeClassificationRecord | None = None
    phase_path: list          # list of str | list[str]
    completed_phases: list[str] = []
    current_phase: str | None = None
    current_group: list[str] | None = None
    current_group_completed: list[str] = []
    phase_status: PhaseStatus = PhaseStatus.PENDING
    advance_state: str | None = None
    retry_count: int = 0
    max_retries: int = 2
    scope_reassessment_count: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    deliverables: dict[str, list[str]] = {}
    errors: list[PhaseError] = []
    story_status: StoryStatus = StoryStatus.NOT_STARTED
```

### 8.2 Request/Result Models

```python
class PhaseExecutionRequest(BaseModel):
    """Request sent to RunnerPlugin.execute()."""
    system_prompt: str
    user_prompt: str
    model_tier: str              # "tier-1" | "tier-2"
    tool_permissions: list[str]
    working_directory: str
    timeout_seconds: int
    story_id: str
    phase_id: str

class PhaseExecutionResult(BaseModel):
    """Result returned from RunnerPlugin.execute()."""
    success: bool
    exit_code: int
    output_files: list[str] = []
    error_message: str | None = None
    duration_seconds: float
    session_id: str | None = None

class ExecutionRequest(BaseModel):
    """Top-level request to SDLCEngine.execute()."""
    story_id: str
    story_slug: str
    task_description: str
    codebase_context: str | None = None
    scope_override: str | None = None

class ExecutionResult(BaseModel):
    """Top-level result from SDLCEngine.execute()."""
    story_id: str
    final_status: StoryStatus
    scope: Scope
    phases_completed: list[str]
    deliverables: dict[str, list[str]]
    total_duration_seconds: float
    errors: list[PhaseError]

class PersonaConfig(BaseModel):
    """Loaded persona data for a phase."""
    phase_id: str
    system_prompt: str
    tool_permissions: list[str] = ["all"]

class EngineConfig(BaseModel):
    """Engine configuration."""
    opus_allowed: bool = False
    auto_advance: bool = True
    max_retries: int = 2
    default_timeout_seconds: int = 600
    tracker_timeout_seconds: int = 5
    context_budget_tokens: int = 80000
    deliverable_min_bytes: int = 100
```

---

## 9. Error Handling Strategy

### 9.1 Exception Hierarchy

```python
class EngineError(Exception):
    """Base exception for all engine errors."""
    pass

class InvalidTransitionError(EngineError):
    """Raised when a state transition violates the allowed transition map."""
    pass

class CheckpointCorruptionError(EngineError):
    """Raised when checkpoint JSON cannot be parsed. Engine starts fresh."""
    pass

class RunnerUnavailableError(EngineError):
    """Raised when the runner plugin cannot be reached."""
    pass

class DeliverableValidationError(EngineError):
    """Raised when expected deliverables are missing or invalid."""
    pass

class ScopeClassificationError(EngineError):
    """Raised when scope classification fails after retries."""
    pass
```

### 9.2 Error Propagation Rules

| Error Source | Propagation | Engine Action |
|-------------|-------------|---------------|
| RunnerPlugin.execute() raises | Caught by engine | Mark phase failed, retry or escalate |
| RunnerPlugin.execute() times out | Caught by engine | Mark phase failed with "timeout" |
| TrackerPlugin method raises | Caught by EventBus | Log warning, continue (non-fatal) |
| NotifierPlugin method raises | Caught by EventBus | Log warning, continue (non-fatal) |
| StateMachine.transition raises InvalidTransitionError | NOT caught | Indicates engine bug, propagates to caller |
| CheckpointManager.write() fails | Caught by engine | Log error, emit EngineErrorEvent, halt |
| CheckpointManager.load() fails | Caught by engine | Log warning, start fresh (no checkpoint) |

---

## 10. Testing Strategy Overview

### 10.1 Test Doubles

| Component | Test Double | Behavior |
|-----------|------------|----------|
| RunnerPlugin | `FakeRunner` | Always succeeds. Writes stub deliverable files (populated with placeholder content matching expected filenames). Configurable to fail on specific phases for retry testing. |
| TrackerPlugin | `LoggingTrackerPlugin` | Logs events. No external calls. |
| NotifierPlugin | `LoggingNotifierPlugin` | Logs events. No external calls. |
| EventBus | Real EventBus with `test_mode=True` | Re-raises handler exceptions. |
| CheckpointManager | Real CheckpointManager with `tmp_dir` | Uses pytest's `tmp_path` fixture. |

### 10.2 FakeRunner Specification

```python
class FakeRunner(RunnerPlugin):
    """
    Test double that simulates phase execution.

    - Writes stub deliverable files to the story folder
    - Records all execution requests for assertion
    - Configurable failure injection per phase
    """

    def __init__(
        self,
        story_folder: Path,
        fail_phases: dict[str, int] | None = None,  # phase_id → fail on attempt N
    ):
        self.story_folder = story_folder
        self.fail_phases = fail_phases or {}
        self.calls: list[PhaseExecutionRequest] = []
        self._attempt_counts: dict[str, int] = defaultdict(int)

    def execute(self, request: PhaseExecutionRequest) -> PhaseExecutionResult:
        self.calls.append(request)
        self._attempt_counts[request.phase_id] += 1

        # Check for configured failure
        fail_on = self.fail_phases.get(request.phase_id, -1)
        if self._attempt_counts[request.phase_id] <= fail_on:
            return PhaseExecutionResult(
                success=False, exit_code=1,
                error_message=f"Simulated failure on phase {request.phase_id}",
                duration_seconds=1.0
            )

        # Write stub deliverables
        self._write_stubs(request.phase_id)
        return PhaseExecutionResult(
            success=True, exit_code=0,
            output_files=self._expected_files(request.phase_id),
            duration_seconds=2.5
        )
```
