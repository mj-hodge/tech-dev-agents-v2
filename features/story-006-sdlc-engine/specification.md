# Specification: SDLC Execution Engine

> Phase 6 — Functional Specification
> Story: STORY-006
> Date: 2026-03-26
> Scope: Large
> Approach: B — Event-Driven Engine, Milestone 1 (hardcoded metadata)

---

## 1. Engine Lifecycle

The engine follows a deterministic lifecycle for every story execution:

```
init → classify → resolve_path → [execute_phase → validate → advance]* → done
```

### 1.1 Initialization (`init`)

The engine is instantiated with four plugin dependencies and a configuration object:

```
SDLCEngine(
    runner:    RunnerPlugin,       # STORY-003 Claude Code Runner
    tracker:   TrackerPlugin,      # STORY-004 Monday.com + .project
    notifier:  NotifierPlugin,     # STORY-007 Approval Flow (or LoggingNotifier)
    bus:       EventBus,           # Internal typed event bus
    config:    EngineConfig        # Loaded from config.yaml + hardcoded defaults
)
```

On construction, the engine:
1. Registers internal event handlers on the bus (`ApprovalReceivedEvent`, `ConfirmationReceivedEvent`)
2. Initializes the `CheckpointManager` with the target story folder path
3. Initializes the `ContextBuilder` with the story folder path and deliverable registry
4. Checks for an existing checkpoint file — if found, enters **resume mode** (see section 5)

### 1.2 Execution Entry Point

The engine exposes a single entry point:

```python
def execute(self, request: ExecutionRequest) -> ExecutionResult:
    """
    Drive a story through its full SDLC phase path.

    Args:
        request: Contains story_id, story_slug, task_description,
                 codebase_context (optional), scope_override (optional)

    Returns:
        ExecutionResult with final status, completed phases, deliverables map,
        total duration, and any errors encountered.
    """
```

The `execute()` method is the only public method that drives the full lifecycle. All other operations (approval signals, confirmation signals) are received via events published to the bus.

### 1.3 Lifecycle Steps

**Step 1 — Create story folder:**
Create `features/<story-slug>/` if it does not exist.

**Step 2 — Classify scope:**
If `request.scope_override` is provided, use it directly. Otherwise, invoke the `ScopeClassifier` (see section 2). Store the classification and rationale in the checkpoint.

**Step 3 — Resolve phase path:**
Look up the canonical phase path for the classified scope from the hardcoded `PHASE_PATHS` registry. Store the full path in the checkpoint.

**Step 4 — Execute loop:**
For each phase entry in the path (a phase entry is either a string like `"6"` or a list like `["6b", "6c", "6d"]`):

1. If the entry is a string: execute it as a single phase (section 3)
2. If the entry is a list: execute it as a bracketed group — sequentially in V1, each sub-phase independently checkpointed
3. After each phase completes, apply advance category logic (section 4)
4. If advance is `gate` or `confirm`: halt the loop and wait for external signal
5. If advance is `auto`: continue to the next entry
6. On external signal receipt (approval/confirmation): resume the loop

**Step 5 — Completion:**
When all phases in the path have completed, emit `StoryCompletedEvent` and return `ExecutionResult`.

---

## 2. Scope Classification Algorithm

### 2.1 Input Signals

The classifier accepts an `ExecutionRequest` and produces a `ScopeClassification`:

```python
class ScopeSignals(BaseModel):
    """Structured signals extracted from task description."""
    files_affected: int = Field(ge=0, description="Estimated number of files to create or modify")
    new_db_models: int = Field(ge=0, description="New database tables/models")
    new_api_endpoints: int = Field(ge=0, description="New API endpoints")
    new_integrations: int = Field(ge=0, description="New external system integrations")
    cross_cutting_concerns: int = Field(ge=0, description="Auth, logging, migrations, etc.")
    estimated_test_count: int = Field(ge=0, description="Estimated number of test cases")
    architectural_impact: bool = Field(description="Affects system architecture or introduces new patterns")
    multiple_components: bool = Field(description="Spans multiple distinct components/modules")
    multiple_systems: bool = Field(description="Involves multiple deployed systems")
    new_infrastructure: bool = Field(description="Requires new infrastructure (queues, caches, etc.)")
    parallelizable_work: bool = Field(description="Could be split into independent parallel streams")
    natural_e2e_gates: bool = Field(description="Has natural integration checkpoints")
    system_boundaries: int = Field(ge=0, description="Number of system boundaries crossed")
    ac_clusters: int = Field(ge=0, description="Number of independent acceptance criteria clusters")
    rationale: str = Field(description="Brief explanation of signal extraction reasoning")

class ScopeClassification(BaseModel):
    scope: Scope  # trivial | small | medium | large | epic
    signals: ScopeSignals
    rationale: str
    method: str  # "override" | "rule_based" | "llm_hybrid"
    guardrail_warnings: list[str]  # Any sizing guardrail violations detected
```

### 2.2 Classification Method: Rule-Based with LLM Signal Extraction

The classification proceeds in three stages:

**Stage 1 — Override check:**
If `request.scope_override` is set, return immediately with `method: "override"`. No signal extraction.

**Stage 2 — Signal extraction (LLM call):**
Send the task description and optional codebase context to a tier-2 model via the runner, with a structured output prompt requesting `ScopeSignals`. The prompt instructs the model to analyze the task and extract concrete counts and boolean flags. The response is validated against the `ScopeSignals` Pydantic model. On validation failure, retry once with a more constrained prompt. On second failure, fall back to conservative defaults (all counts = 0, all booleans = False) which produces a `small` classification.

**Stage 3 — Deterministic scoring:**

```
Epic check (first — 2+ signals triggers epic):
    epic_signal_count = sum([
        signals.new_integrations >= 3,
        signals.parallelizable_work,
        signals.natural_e2e_gates,
        signals.system_boundaries >= 3,
        signals.ac_clusters >= 3,
    ])
    if epic_signal_count >= 2 → EPIC

Story sizing guardrail check (warn, do not auto-classify):
    warnings = []
    if signals.estimated_test_count > 30 → warn "exceeds 30-test limit"
    if signals.new_db_models > 2 → warn "exceeds 2-model limit"
    if signals.new_api_endpoints > 3 → warn "exceeds 3-endpoint limit"

Score accumulation:
    score = 0
    score += signals.files_affected // 3          # +1 per 3 files
    score += signals.new_db_models * 2            # +2 per model
    score += signals.new_api_endpoints            # +1 per endpoint
    score += signals.new_integrations * 3         # +3 per integration
    score += signals.cross_cutting_concerns * 2   # +2 per concern
    score += signals.estimated_test_count // 5    # +1 per 5 tests
    if signals.architectural_impact: score += 4
    if signals.multiple_systems: score += 4
    if signals.new_infrastructure: score += 3
    if signals.multiple_components: score += 2

Score-to-scope mapping:
    score 0       → TRIVIAL
    score 1–3     → SMALL
    score 4–9     → MEDIUM
    score 10+     → LARGE
```

### 2.3 Scope Reassessment

The engine supports at most **one** automatic scope reassessment per story. A phase may trigger reassessment by including a `scope_reassessment` signal in its output (detected by the engine via a marker in the deliverable file or runner result metadata).

**Reassessment triggers** (per `.sdlc/software-development-guidance.md`):
- Phase 2 research reveals bigger/smaller problem
- Phase 4 analysis shows more affected components
- Phase 6 design reveals unexpected DB/API/cross-cutting changes
- Phase 7 test design produces too many/few tests
- Phase 8 implementation hits unexpected complexity

**Reassessment process:**
1. Engine detects reassessment signal
2. Engine emits `ScopeReassessedEvent(old_scope, new_scope, trigger_phase, rationale)`
3. Engine transitions to `waiting_for_approval` — reassessment requires human approval
4. On approval: recalculate remaining phase path, update checkpoint, continue from the first new required phase
5. On rejection: continue with current scope
6. If a second reassessment is triggered: emit `EscalationRequiredEvent` and halt. No auto-reassessment beyond one.

---

## 3. Phase Execution Contract

Every phase invocation follows the same contract. The engine constructs a `PhaseExecutionRequest`, passes it to the `RunnerPlugin`, receives a `PhaseExecutionResult`, and validates the outcome.

### 3.1 Request Construction

For each phase, the engine performs these steps in order:

**Step 1 — Load persona:**
Read the persona file from `.sdlc/agents/phase-{id}-*.md`. Extract the full file content as the system prompt. The advance category and model tier are read from the hardcoded registry (V1), not parsed from the file.

**Step 2 — Determine model tier:**
Look up `MODEL_TIERS[phase_id]`. If `config.opus_allowed` is true and the phase's default tier is tier-2, allow tier-1. Otherwise, enforce the tier strictly.

**Step 3 — Construct context:**
The `ContextBuilder` assembles the user prompt from:

| Component | Source |
|-----------|--------|
| Task description | `request.task_description` (stored in checkpoint) |
| Story metadata | story_id, scope, current phase, story folder path |
| Prior deliverables | Read from `features/<story-folder>/` per the context table below |
| Expected outputs | Deliverable filenames for this phase |
| Phase-specific instructions | "Write your output to `features/<story-folder>/<filename>`" |

**Context inputs per phase (strict — no extra deliverables):**

| Phase | Context Inputs (file paths relative to story folder) |
|-------|-----------------------------------------------------|
| 1 (Seed) | Task description only. Optional: codebase context |
| 2 (Research) | `seed.md` |
| 3 (Expansion) | `seed.md`, `research.md` |
| 4 (Analysis) | `seed.md`, `expansion.md` (Large) or `seed.md` (Medium) |
| 5 (Selection) | `seed.md`, `expansion.md`, `analysis.md` |
| 6 (Design) | `seed.md`, `selection.md` (Large) or `seed.md`, `analysis.md` (Medium) |
| 6b | Phase 6 design deliverables (all files produced by Phase 6) |
| 6c | Phase 6 design deliverables |
| 6d | Phase 6 design deliverables |
| 7 (Test Design) | Phase 6 design deliverables, `seed.md` |
| 8 (Implementation) | `test-design.md`, Phase 6 design deliverables, `seed.md` |
| 8b (Code Review) | Implementation code paths, test results summary, Phase 6 design deliverables |
| 11 (Pre-Deploy) | All deliverables produced so far, test results, implementation code paths |
| 9 (Refinement) | All deliverables, implementation code paths, test results |
| 10 (Operations) | All deliverables, `architecture.md`, implementation code paths |

**Context budget enforcement:**
The `ContextBuilder` estimates token count for each included deliverable (rough heuristic: 1 token per 4 characters). If total context exceeds **80,000 tokens**, the builder applies a truncation strategy:
- Priority 1 (always full): `seed.md`, `test-design.md`, current phase's direct inputs
- Priority 2 (excerpt at 2,000 tokens): design deliverables (`specification.md`, `architecture.md`, etc.)
- Priority 3 (file path only, no content): `research.md`, `expansion.md`, `analysis.md`, `selection.md`
- Implementation code: passed as file paths with a 500-token excerpt of key sections

**Step 4 — Build request:**

```python
PhaseExecutionRequest(
    system_prompt: str,           # Full persona file content
    user_prompt: str,             # Constructed context from ContextBuilder
    model_tier: str,              # "tier-1" or "tier-2"
    tool_permissions: list[str],  # From persona file (V1: default all tools)
    working_directory: str,       # Repository root path
    timeout_seconds: int,         # Phase-specific timeout (see below)
    story_id: str,
    phase_id: str,
)
```

**Phase timeouts:**

| Phase | Timeout | Rationale |
|-------|---------|-----------|
| 1 (Seed) | 600s (10 min) | Scope analysis + seed writing |
| 2, 3 (Research/Expansion) | 600s (10 min) | Research can involve multiple sub-agent calls |
| 4, 5 (Analysis/Selection) | 600s (10 min) | Evaluation of approaches |
| 6 (Design) | 1800s (30 min) | Large-scope design produces 5 files |
| 6b, 6c, 6d (Reviews) | 300s (5 min) | Single focused review document |
| 7 (Test Design) | 600s (10 min) | Test design + test code generation |
| 8 (Implementation) | 1800s (30 min) | Code generation, largest phase |
| 8b (Code Review) | 600s (10 min) | Multi-reviewer orchestration |
| 9, 10 (Polish) | 600s (10 min) | Refinement and operations analysis |
| 11 (Pre-Deploy) | 300s (5 min) | Final gate checklist |

### 3.2 Result Handling

The runner returns:

```python
PhaseExecutionResult(
    success: bool,
    exit_code: int,
    output_files: list[str],     # Files created or modified
    error_message: str | None,
    duration_seconds: float,
    session_id: str | None,      # Runner session ID for debugging
)
```

### 3.3 Deliverable Validation

After the runner returns, the engine validates that the expected deliverable files exist:

**Expected deliverables per phase (hardcoded registry):**

| Phase | Scope | Expected Files |
|-------|-------|---------------|
| 1 | All | `seed.md` |
| 2 | Large | `research.md` |
| 3 | Large | `expansion.md` |
| 4 | Medium+ | `analysis.md` |
| 5 | Large | `selection.md` |
| 6 | Large | `specification.md`, `architecture.md`, `api-design.md`, `database-schema.md`, `implementation-plan.md` |
| 6 | Medium | `feature-spec.md` |
| 6b | Medium+ | `security-review.md` |
| 6c | Medium+ | `ux-review.md` |
| 6d | Medium+ | `ops-review.md` |
| 7 | All | `test-design.md` |
| 8 | All | (tests pass — validated by runner exit code, no specific file) |
| 8b | Medium+ | `code-review.md` |
| 9 | Large | `refinement-report.md` |
| 10 | Large | `site-reliability.md` |
| 11 | Medium+ | `predeploy-gate.md` |

**Validation sequence:**

```
1. Check runner result: if exit_code != 0 → FAIL("runner_error")
2. For each expected deliverable file:
   a. Check file exists in features/<story-folder>/
   b. Check file size > 100 bytes (catches empty/stub files)
   c. If any check fails → FAIL("missing_deliverable")
3. All checks pass → COMPLETE
```

### 3.4 Post-Phase Actions

After successful validation:

1. Record deliverable paths in checkpoint: `checkpoint.deliverables[phase_id] = [file_paths]`
2. Emit `PhaseCompletedEvent(story_id, phase_id, deliverables, duration)`
3. Invoke tracker: `tracker.on_phase_completed(event)` — updates Monday.com + `.project`
4. Write checkpoint with `phase_status: "completed"`
5. Apply advance category logic (section 4)

---

## 4. Advance Category Handling

Each phase has an advance category that determines what happens after successful completion. The category is read from the hardcoded `ADVANCE_CATEGORIES` registry.

### 4.1 Gate Phases (1, 8, 11)

**Behavior:** The engine stops and waits for explicit human approval.

1. After phase completes and deliverables are validated:
   - Transition `phase_status` to `"waiting_for_approval"`
   - Construct a gate summary: phase name, deliverables produced, key outputs
   - Emit `GateReachedEvent(story_id, phase_id, summary)`
   - Write checkpoint
   - **Halt the execution loop** — return control to the caller
2. The `NotifierPlugin` receives `GateReachedEvent` and delivers a notification (via STORY-007, or logs in V1)
3. When the human approves, an `ApprovalReceivedEvent(story_id, phase_id, approved=True)` is published to the bus
4. The engine's internal handler transitions `phase_status` to `"approved"`, moves to the next phase, and resumes the execution loop
5. If `approved=False`: transition to `"blocked"`, emit `EscalationRequiredEvent`, halt

### 4.2 Confirm Phases (2, 3, 4, 5, 6, 7, 9, 10)

**Behavior:** The engine stops and asks "Proceed to Phase X?"

1. After phase completes:
   - Transition `phase_status` to `"waiting_for_confirmation"`
   - Determine the next phase in the path
   - Emit `ConfirmationRequestedEvent(story_id, phase_id, next_phase, summary)`
   - Write checkpoint
   - **Halt the execution loop**
2. The `NotifierPlugin` delivers a "Proceed to Phase X?" prompt
3. When confirmed: `ConfirmationReceivedEvent(story_id, phase_id, confirmed=True)` → resume
4. When declined: `ConfirmationReceivedEvent(story_id, phase_id, confirmed=False)` → `"blocked"` → `EscalationRequiredEvent`

### 4.3 Auto Phases (6b, 6c, 6d, 8b)

**Behavior:** The engine proceeds immediately to the next phase.

1. After phase completes:
   - No wait state transition
   - No event emitted for the advance (the `PhaseCompletedEvent` was already emitted)
   - Move to the next phase entry in the path
   - Continue the execution loop without halting

### 4.4 Bracketed Group Advance

When a bracketed group (e.g., `["6b", "6c", "6d"]`) completes:
- Each sub-phase within the group has its own advance category (all `auto` for 6b/6c/6d)
- The group as a whole advances to the next entry in the path after all sub-phases complete
- There is no group-level advance event — the last sub-phase's `PhaseCompletedEvent` serves as the group completion signal

For the `["9", "10"]` group: both phases have `confirm` advance. In V1 (sequential), Phase 9 completes and waits for confirmation, then Phase 10 completes and waits for confirmation. Both confirmations are required before the story is marked complete.

---

## 5. Checkpoint and Resume Behavior

### 5.1 Checkpoint Structure

```json
{
  "version": 1,
  "story_id": "STORY-XXX",
  "story_slug": "story-XXX-kebab-slug",
  "task_description": "Original task text...",
  "scope": "large",
  "scope_classification": {
    "signals": { ... },
    "rationale": "...",
    "method": "llm_hybrid"
  },
  "phase_path": ["1", "2", "3", "4", "5", "6", ["6b","6c","6d"], "7", "8", "8b", "11", ["9","10"]],
  "completed_phases": ["1", "2"],
  "current_phase": "3",
  "current_group": null,
  "current_group_completed": [],
  "phase_status": "in_progress",
  "advance_state": null,
  "retry_count": 0,
  "max_retries": 2,
  "scope_reassessment_count": 0,
  "created_at": "2026-03-26T10:00:00Z",
  "updated_at": "2026-03-26T14:32:00Z",
  "deliverables": {
    "1": ["features/story-XXX-slug/seed.md"],
    "2": ["features/story-XXX-slug/research.md"]
  },
  "errors": [],
  "story_status": "in_progress"
}
```

**File location:** `features/<story-slug>/.checkpoint.json`

### 5.2 Checkpoint Write Points

The engine writes a checkpoint at exactly two points per phase:

1. **Before phase execution begins** — `phase_status: "in_progress"`, `retry_count` preserved. This ensures that a crash during execution can resume from the start of the current phase.

2. **After phase completes or transitions** — `phase_status: "completed"` or `"waiting_for_approval"` or `"waiting_for_confirmation"` or `"failed"`. This records the outcome.

### 5.3 Atomic Write Protocol

```
1. Serialize checkpoint state to JSON string
2. Write JSON to temp file: features/<story-slug>/.checkpoint.json.tmp
   (temp file is in the same directory to ensure same-filesystem atomic rename)
3. Call os.replace(temp_path, checkpoint_path)
   (POSIX atomic rename guarantee on same filesystem)
4. If write fails at step 2: temp file may be partial, but checkpoint file is untouched
5. If crash between step 2 and 3: temp file is orphaned but checkpoint is untouched
6. On startup: delete any orphaned .checkpoint.json.tmp files
```

### 5.4 Resume Logic

On engine startup, if a checkpoint file exists:

| Checkpoint `phase_status` | Resume Action |
|---------------------------|---------------|
| `"completed"` | Skip current phase, advance to next phase in path |
| `"in_progress"` | Restart current phase from scratch (was interrupted mid-execution). Increment `retry_count`. |
| `"waiting_for_approval"` | Re-emit `GateReachedEvent`. Do NOT re-execute the phase. Halt and wait. |
| `"waiting_for_confirmation"` | Re-emit `ConfirmationRequestedEvent`. Do NOT re-execute. Halt and wait. |
| `"failed"` | If `retry_count < max_retries`: retry phase. Else: emit `EscalationRequiredEvent` and halt. |
| `"blocked"` | Emit `EscalationRequiredEvent` immediately and halt. |

**Critical invariant:** A phase in `waiting_for_approval` or `waiting_for_confirmation` has already produced its deliverable. Re-executing the phase would waste API cost and potentially produce conflicting deliverables. The engine MUST only re-emit the wait event on resume.

### 5.5 Checkpoint Reset

The engine supports explicit checkpoint reset via `reset_checkpoint(story_id)`:
1. Delete `features/<story-slug>/.checkpoint.json`
2. Optionally delete all deliverable files (with a `delete_deliverables: bool` parameter)
3. On next `execute()` call, the engine starts from scratch (classify → resolve → execute)

---

## 6. Error Recovery

### 6.1 Failure Categories

| Category | Detection | Immediate Action |
|----------|-----------|-----------------|
| Runner error | `result.exit_code != 0` | Mark `failed`, log error details |
| Runner timeout | Runner does not return within `timeout_seconds` | Mark `failed` with "timeout" reason |
| Missing deliverable | Expected file not found after successful runner return | Mark `failed` with "missing_deliverable" reason |
| Empty deliverable | File exists but < 100 bytes | Mark `failed` with "empty_deliverable" reason |
| Checkpoint corruption | JSON parse error on checkpoint load | Log warning, start fresh (treat as no checkpoint) |

### 6.2 Retry Protocol

On any failure:

1. Increment `retry_count` for the current phase
2. If `retry_count < 2`:
   - Log the failure reason and the prompt context that was sent
   - Construct an enhanced retry prompt:
     - For "missing_deliverable": append explicit instruction — "You MUST write the file `<filename>` to `features/<story-folder>/`. This file was not produced in the previous attempt."
     - For "runner_error": use fresh context (no conversation history from the failed attempt)
     - For "timeout": same as runner_error (fresh context)
   - Write checkpoint with `phase_status: "in_progress"` and updated `retry_count`
   - Re-execute the phase
3. If `retry_count >= 2`:
   - Transition to `phase_status: "failed"`, `story_status: "failed"`
   - Record full error context: `{ phase, error_type, retry_count, duration, prompt_summary }`
   - Emit `PhaseFailedEvent(story_id, phase_id, error, retry_count=2)`
   - Emit `EscalationRequiredEvent(story_id, phase_id, error, context)`
   - Write checkpoint
   - Halt execution

### 6.3 Escalation Event Payload

The `EscalationRequiredEvent` contains all information needed for a human to diagnose and resolve:

```python
@dataclass
class EscalationRequiredEvent:
    story_id: str
    phase_id: str
    error_type: str          # "runner_error" | "timeout" | "missing_deliverable" | "empty_deliverable"
    error_message: str       # Human-readable description
    retry_count: int         # How many attempts were made
    phase_duration_total: float  # Total time spent on this phase across all attempts
    last_prompt_summary: str     # Abbreviated version of what was sent to the runner
    deliverables_produced: list[str]  # Any files that were produced (may be partial)
    checkpoint_path: str     # Path to checkpoint file for manual inspection
```

### 6.4 Error Logging

All failures are logged with structured fields:

```json
{
  "level": "ERROR",
  "event": "phase_failed",
  "story_id": "STORY-042",
  "phase_id": "8",
  "error_type": "runner_error",
  "error_message": "Runner exited with code 1: Tests failed — 3 of 12 assertions failed",
  "retry_count": 1,
  "duration_seconds": 245.3,
  "prompt_tokens_estimate": 42000,
  "timestamp": "2026-03-26T15:42:00Z"
}
```

---

## 7. Event Taxonomy

### 7.1 Events Emitted by Engine

| Event Class | Fields | Trigger |
|-------------|--------|---------|
| `PhaseStartedEvent` | story_id, phase_id, scope | Phase execution begins |
| `PhaseCompletedEvent` | story_id, phase_id, deliverables: list[str], duration_seconds: float | Phase succeeds, deliverables validated |
| `PhaseFailedEvent` | story_id, phase_id, error_type, error_message, retry_count | Phase fails (each attempt) |
| `GateReachedEvent` | story_id, phase_id, summary: str, deliverables: list[str] | Gate phase completes |
| `ConfirmationRequestedEvent` | story_id, phase_id, next_phase: str, summary: str | Confirm phase completes |
| `EscalationRequiredEvent` | (see section 6.3) | 2 consecutive failures, blocked state, or 2nd scope reassessment |
| `StoryCompletedEvent` | story_id, scope, phases_completed: list[str], total_duration: float, deliverables: dict | All phases done |
| `ScopeReassessedEvent` | story_id, old_scope, new_scope, trigger_phase, rationale | Scope change detected |
| `EngineErrorEvent` | story_id, error_type, error_message, traceback | Unexpected engine-level error |

### 7.2 Events Consumed by Engine

| Event Class | Fields | Source | Engine Action |
|-------------|--------|--------|---------------|
| `ExecutionRequestedEvent` | story_id, story_slug, task_description, scope_override | Orchestrator | Start `execute()` |
| `ApprovalReceivedEvent` | story_id, phase_id, approved: bool, comment | STORY-007 | Resume from gate |
| `ConfirmationReceivedEvent` | story_id, phase_id, confirmed: bool, comment | STORY-007 | Resume from confirm |

---

## 8. Story-Level State Machine

### 8.1 Story States

```
not_started → in_progress → completed
                          ↘ blocked   (gate rejection or user decline)
                          ↘ failed    (unrecoverable after 2 retries)
```

### 8.2 Phase States

```
pending
  → in_progress
      → completed → [advance logic → next phase pending]
      → failed
          → in_progress (retry, if retry_count < 2)
          → blocked (retry_count >= 2 → escalate)
      → waiting_for_approval (gate advance)
          → completed (approved=true → next phase)
          → blocked (approved=false)
      → waiting_for_confirmation (confirm advance)
          → completed (confirmed=true → next phase)
          → blocked (confirmed=false)
```

### 8.3 Transition Guards

| Transition | Guard Condition |
|------------|----------------|
| pending → in_progress | All prior phases completed |
| in_progress → completed | runner.success AND deliverables_present AND deliverables_valid |
| in_progress → failed | NOT runner.success OR NOT deliverables_present OR timeout |
| failed → in_progress | retry_count < max_retries (2) |
| failed → blocked | retry_count >= max_retries |
| waiting_for_approval → completed | ApprovalReceivedEvent.approved == True |
| waiting_for_approval → blocked | ApprovalReceivedEvent.approved == False |
| waiting_for_confirmation → completed | ConfirmationReceivedEvent.confirmed == True |
| waiting_for_confirmation → blocked | ConfirmationReceivedEvent.confirmed == False |

---

## 9. Configuration

### 9.1 EngineConfig

```python
class EngineConfig(BaseModel):
    """Engine configuration, loaded from config.yaml with defaults."""
    opus_allowed: bool = False
    auto_advance: bool = True       # Enables auto/confirm/gate behavior
    max_retries: int = 2            # Per-phase retry limit
    default_timeout_seconds: int = 600
    tracker_timeout_seconds: int = 5  # Timeout for tracker plugin calls
    context_budget_tokens: int = 80000
    deliverable_min_bytes: int = 100  # Minimum deliverable file size
```

### 9.2 Hardcoded Phase Metadata (V1)

Milestone 1 encodes all phase metadata as Python dictionaries in `config.py`:

- `PHASE_PATHS: dict[str, list]` — Scope to ordered phase path
- `ADVANCE_CATEGORIES: dict[str, str]` — Phase ID to advance category
- `MODEL_TIERS: dict[str, str]` — Phase ID to model tier
- `DELIVERABLES: dict[str, list[str] | dict[str, list[str]]]` — Phase ID to expected files (scope-conditional for Phase 6)
- `CONTEXT_INPUTS: dict[str, dict[str, list[str]]]` — Phase ID to context file list (scope-conditional where needed)
- `PHASE_TIMEOUTS: dict[str, int]` — Phase ID to timeout in seconds

These are direct transcriptions of the tables in AGENTS.md, CLAUDE.md, and this specification. A CI test validates that `ADVANCE_CATEGORIES` and `MODEL_TIERS` match the values documented in AGENTS.md.
