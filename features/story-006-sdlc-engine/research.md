# Research Report: SDLC Execution Engine

> Phase 2 — Research
> Story: STORY-006
> Date: 2026-03-26
> Scope: Large

---

## Summary

| Metric | Value |
|--------|-------|
| Research areas covered | 5 |
| Design patterns identified | 12 |
| Key decisions surfaced | 6 |
| Critical risks flagged | 4 |

---

## Research Area 1: State Machine Patterns for Multi-Phase Workflows

### How CI/CD Orchestrators Model Phase Transitions

#### GitHub Actions
GitHub Actions models workflows as a DAG of jobs, each with its own `needs:` dependency list, environment, and steps. Key patterns applicable to the SDLC engine:

- **Job-level status** (`pending`, `in_progress`, `completed`, `cancelled`, `skipped`) maps directly to the phase state machine in seed.md
- **`if:` conditions** on jobs provide the gate/confirm/auto equivalent — jobs can be conditioned on the output or status of previous jobs
- **Environment protection rules** implement the gate pattern: a job targeting a protected environment pauses until a human reviewer approves in the UI. This is the closest analog to the SDLC engine's `gate` advance category
- **Concurrency groups** with `cancel-in-progress: false` prevent duplicate workflow runs — useful for the "one story at a time" constraint
- **Job outputs** pass structured data between phases, equivalent to the engine passing deliverable file paths to the next phase's context

**Key lesson from GitHub Actions:** The execution model separates *orchestration* (which jobs run, in what order, with what conditions) from *execution* (what each job does). The workflow YAML is pure orchestration; the runners are pure execution. This maps cleanly to the SDLC engine's constraint that the engine orchestrates and the Claude Code Runner (STORY-003) executes.

#### Temporal (Temporal.io)
Temporal is the most architecturally relevant prior art. It models long-running workflows as durable executions where each step can be replayed after failure:

- **Workflow as code:** A workflow is a Python/Go/Java function whose execution is recorded in an event history. If the worker crashes mid-execution, Temporal replays the history to reconstruct state — the function re-executes from the beginning but skips already-completed activities by replaying their results
- **Activities vs Workflows:** Activities are the units of work (execute a phase via Claude Code); Workflows are the orchestration logic (which phases, in what order). This maps exactly to the engine's Phase Execution Contract
- **Signals:** External events (approval received, confirmation received) are modeled as Signals sent to a running workflow. The workflow can `await` a signal, suspending indefinitely until the signal arrives — this is the native implementation of `waiting_for_approval` and `waiting_for_confirmation` states
- **Queries:** External systems can query a running workflow's current state without interrupting it — useful for the Monday.com status polling use case
- **Timers:** Durable `sleep()` calls that survive worker restarts implement the phase timeout requirement naturally
- **Versioning:** Temporal supports workflow versioning to handle in-flight workflows during code upgrades

**Why not use Temporal directly:** The constraint in seed.md specifies "file system as state store" and "container-per-agent model." Temporal requires a server and worker infrastructure — it is overkill for a single-story, single-container execution model. However, Temporal's patterns (event sourcing for state, signal-based approval, activity vs workflow separation) should inform the engine's design even if the infrastructure is not adopted.

**Key lesson from Temporal:** Durable execution via event replay is the gold-standard pattern for long-running workflows that must survive crashes. The engine's checkpoint-on-every-phase-boundary approach is a lightweight file-based approximation of this pattern.

#### Prefect
Prefect 2.0 introduced a "hybrid execution model" where the orchestration server (Prefect Cloud or self-hosted) stores flow metadata and state, while execution happens in the user's infrastructure. Key patterns:

- **Flow runs and task runs** are persisted state objects. A task run can be in `Scheduled`, `Pending`, `Running`, `Completed`, `Failed`, `Cancelled`, or `Crashed` states — the last being distinct from `Failed` (process died unexpectedly vs. task raised an exception)
- **Result persistence:** Prefect can persist task results to local filesystem, S3, or GCS. Tasks that already have a persisted result are skipped on retry (cache hit). This is directly applicable to the deliverable validation pattern — if `research.md` exists and is non-empty, phase 2 can be marked `completed` without re-execution
- **Subflows:** A flow can call another flow as a subflow — directly maps to bracketed phase groups (6b/6c/6d) running as parallel subflows
- **`ConcurrentTaskRunner`:** Runs tasks concurrently within a flow, useful for the bracketed parallel phases

**Key lesson from Prefect:** Distinguishing `Failed` (clean exception) from `Crashed` (process killed) is important for error recovery. The engine's checkpoint-before-start pattern covers the crash case; the retry-with-fresh-context pattern covers the clean failure case.

#### Argo Workflows (Kubernetes-native)
Argo Workflows models workflows as DAGs of containers. While container-native (not applicable to the single-container model), its patterns are instructive:

- **Suspend templates:** A step that pauses workflow execution indefinitely until resumed by an external API call. This is the cleanest model for gate phases — the workflow literally stops at the gate and waits for a `POST /api/v1/workflows/{name}/resume`
- **Exit handlers:** Steps that run regardless of workflow success/failure — maps to the escalation path (after 2 failures, always escalate)
- **DAG vs Steps templates:** Two representations of the same thing. DAG is more explicit about dependencies; Steps is more readable for sequential pipelines. The SDLC engine is primarily sequential with occasional parallel groups — the Steps model with explicit parallel blocks is appropriate

**Key lesson from Argo:** An external HTTP API for resuming suspended workflows is clean and decoupled. The engine's `approval_received` and `confirmation_received` event interface is the equivalent pattern.

### State Machine Design Pattern (for the engine itself)

The canonical implementation pattern for the engine's phase state machine is the **State pattern** (Gang of Four), combined with a **persistent state store** (file-based JSON):

```
PhaseStateMachine:
  current_state: PhaseState (enum: pending | in_progress | completed | failed |
                              waiting_for_approval | waiting_for_confirmation | blocked)
  transitions:
    pending           -> in_progress           (on: start_phase)
    in_progress       -> completed             (on: phase_success, guard: deliverable_exists)
    in_progress       -> failed                (on: phase_error OR timeout)
    in_progress       -> waiting_for_approval  (on: phase_success, guard: advance == gate)
    in_progress       -> waiting_for_confirmation (on: phase_success, guard: advance == confirm)
    failed            -> in_progress           (on: retry, guard: retry_count < 2)
    failed            -> blocked               (on: retry, guard: retry_count >= 2)
    waiting_for_approval -> in_progress        (on: approval_received, guard: approved == true)
    waiting_for_approval -> blocked            (on: approval_received, guard: approved == false)
    waiting_for_confirmation -> in_progress    (on: confirmation_received, guard: confirmed == true)
    waiting_for_confirmation -> blocked        (on: confirmation_received, guard: confirmed == false)
    completed         -> [next_phase pending]  (on: advance)
```

**Recommended pattern:** Implement as a simple Python dataclass + transition functions, not a formal state machine library. The Python `transitions` library (PyPI: `transitions`, ~3k stars, active) provides lightweight FSM with before/after hooks — suitable if a library is desired. The `python-statemachine` library is also viable. However, given the explicit state transitions are well-defined and the state is serialized to JSON at each step, a hand-rolled implementation with explicit `if current_state == X` transitions and a checkpoint write after every transition may be simpler and more debuggable.

---

## Research Area 2: Scope Classification Approaches

### Rule-Based Heuristics

The existing scope classification in the SDLC framework (AGENTS.md § Scope Classification Guide, phase-1-seed.md § Scope Classification Guide) uses indicator-based heuristics:

| Scope | Indicators |
|-------|------------|
| Trivial | Single config change, typo fix, no behavior change |
| Small | Single component, clear acceptance, minimal integration |
| Medium | Multiple components, API/DB changes, needs design |
| Large | Architectural impact, multiple systems, significant scale requirements |
| Epic | Decomposes into 3+ independent stories with distinct deliverables |

The story sizing guardrails (software-development-guidance.md § Story Sizing Guardrails) provide quantitative thresholds:
- >30 tests → too large, split
- >2 new DB models → split
- >3 new API endpoints → split
- >1 architectural layer → split

These are programmatically encodable as a scoring function:

```python
def classify_scope(signals: ScopeSignals) -> Scope:
    score = 0

    # Epic signals (2+ = escalate to Epic)
    epic_signals = sum([
        signals.integration_points >= 3,
        signals.parallelizable_work,
        signals.natural_e2e_gates,
        signals.system_boundaries >= 3,
        signals.ac_clusters >= 3
    ])
    if epic_signals >= 2:
        return Scope.EPIC

    # Large signals
    if (signals.architectural_impact or
        signals.multiple_systems or
        signals.new_infrastructure):
        return Scope.LARGE

    # Medium signals
    if (signals.db_changes or
        signals.new_api_endpoints > 0 or
        signals.multiple_components or
        signals.needs_design):
        return Scope.MEDIUM

    # Small signals
    if (signals.single_component and
        signals.clear_acceptance):
        return Scope.SMALL

    return Scope.TRIVIAL
```

**Limitations of pure rule-based classification:** Task descriptions are natural language. Extracting structured signals (does this task involve DB changes? is there architectural impact?) requires either: (a) manual user input via a structured form, (b) keyword extraction heuristics (fragile), or (c) LLM-based signal extraction.

### LLM-Based Classification

The strongest approach for production is a **structured LLM call with constrained output**:

1. Send the task description + codebase context to a tier-2 model
2. Prompt the model to extract scope signals (not the final classification) using a JSON schema
3. Run the structured signals through the deterministic rule-based classifier
4. Return classification + reasoning

This hybrid approach preserves determinism at the decision boundary (the rule function is deterministic) while leveraging LLM capabilities for natural language understanding.

**Key pattern:** Use Pydantic for structured output validation. The LLM output is validated against a `ScopeSignals` Pydantic model before being passed to the classifier. Invalid LLM output triggers a retry with a more constrained prompt.

```python
class ScopeSignals(BaseModel):
    integration_points: int = Field(ge=0, description="Count of distinct external integrations")
    new_db_models: int = Field(ge=0, description="Count of new database tables/models")
    new_api_endpoints: int = Field(ge=0, description="Count of new API endpoints")
    architectural_impact: bool
    multiple_components: bool
    parallelizable_work: bool
    natural_e2e_gates: bool
    system_boundaries: int = Field(ge=0)
    estimated_test_count: int = Field(ge=0)
    classification_rationale: str
```

**Override mechanism:** The seed.md's acceptance criteria already defines the override path: "Classification can be overridden by explicit user input (e.g., 'treat this as medium')." This should be implemented as a `scope_override` field in the `execution_requested` event, which bypasses the classifier entirely.

### Encoding Existing SDLC Rules Programmatically

The existing scope rules live in three documents:
- `AGENTS.md` § Feature Development Process (phase paths)
- `.sdlc/agents/phase-1-seed.md` § Scope Classification Guide (indicators + Epic escalation check)
- `software-development-guidance.md` § Scope Classification (code gates, story sizing guardrails)

These are currently in Markdown, read by humans. To encode them programmatically:

**Phase path registry (static, from AGENTS.md):**
```python
PHASE_PATHS: dict[Scope, list[str | list[str]]] = {
    Scope.TRIVIAL: ["8"],
    Scope.SMALL:   ["1", "7", "8"],
    Scope.MEDIUM:  ["1", "4", "6", ["6b", "6c", "6d"], "7", "8", "8b", "11"],
    Scope.LARGE:   ["1", "2", "3", "4", "5", "6", ["6b", "6c", "6d"], "7", "8", "8b", "11", ["9", "10"]],
    Scope.EPIC:    ["1", "decompose"],  # Epic path handled separately
}
```

List elements represent sequential phases; list-of-lists represent parallel groups that must all complete before advancing.

**Advance category extraction:** The advance category is already encoded in each agent persona file's YAML front matter (`advance: gate|confirm|auto`). The engine can parse these files at startup using a simple YAML block extractor (the persona files use fenced code blocks with YAML content). This is the "extract from `.sdlc/` framework" approach mentioned in the seed.

**Deliverable requirements:** The deliverable table in CLAUDE.md and seed.md is the source of truth. This can be encoded as a static registry keyed by phase and scope:

```python
DELIVERABLES: dict[str, dict] = {
    "1": {"files": ["seed.md"], "required_for_all": True},
    "2": {"files": ["research.md"], "required_for": ["large", "new"]},
    # ...
    "6": {
        "files": {
            "large": ["specification.md", "architecture.md", "api-design.md",
                      "database-schema.md", "implementation-plan.md"],
            "medium": ["feature-spec.md"]
        }
    }
}
```

---

## Research Area 3: Checkpoint and Resume Patterns

### File-Based State Persistence

The seed.md constrains state storage to the local filesystem (`features/<story-folder>/.checkpoint.json`). This is a well-established pattern for CLI tools and single-process orchestrators. Key reference implementations:

**Make's `.d` dependency tracking:** Make writes dependency metadata to hidden files and uses file modification times to determine what needs to be rebuilt. This is the simplest form of checkpoint — "does the output file exist and is it newer than the inputs?"

**npm's `package-lock.json` / pip's requirements lockfiles:** Atomic write pattern — write to a temp file, then rename. POSIX `rename()` is atomic on the same filesystem. Python: `tempfile.NamedTemporaryFile` + `os.replace()`.

**SQLite WAL mode:** For more structured state with query capability, a local SQLite file with WAL mode enabled provides ACID guarantees without a server. Python's `sqlite3` module is in stdlib. However, the JSON checkpoint approach from seed.md is simpler and sufficient for a single-writer, append-style access pattern.

### Atomic Write Pattern (REQUIRED)

```python
import json
import os
import tempfile
from pathlib import Path

def write_checkpoint(checkpoint_path: Path, state: dict) -> None:
    """Atomic checkpoint write using temp file + rename."""
    checkpoint_dir = checkpoint_path.parent

    # Write to temp file in same directory (ensures same filesystem for atomic rename)
    with tempfile.NamedTemporaryFile(
        mode='w',
        dir=checkpoint_dir,
        suffix='.tmp',
        delete=False
    ) as tmp:
        json.dump(state, tmp, indent=2, default=str)
        tmp_path = tmp.name

    # Atomic rename (POSIX guarantee on same filesystem)
    os.replace(tmp_path, checkpoint_path)
```

**Key constraint:** The temp file must be on the same filesystem as the target file for the rename to be atomic. Writing to `/tmp/` and renaming to `/mnt/features/...` is NOT atomic if they are different filesystems. The pattern above uses `dir=checkpoint_dir` to ensure same-filesystem temp file creation.

### Resume Logic

On engine startup:
1. Check for `features/<story-folder>/.checkpoint.json`
2. If exists and valid: load state, determine resume point
3. Resume rules by phase status:
   - `completed`: skip phase, advance to next
   - `in_progress`: restart phase (was interrupted mid-execution)
   - `waiting_for_approval`: re-emit `gate_reached` event (do NOT re-execute phase)
   - `waiting_for_confirmation`: re-emit `confirmation_requested` event (do NOT re-execute phase)
   - `failed`: if `retry_count < 2`, retry; else emit `escalation_required`
   - `blocked`: emit `escalation_required` immediately

**Critical resume invariant:** A phase in `waiting_for_*` state has already produced its deliverable. The engine must NOT re-execute the phase on resume — it must only re-emit the wait event and block until the external signal arrives. This prevents duplicate deliverable production and wasted API calls.

### Event Sourcing as Alternative

For higher-confidence recovery, an event-sourcing approach stores a log of all state transitions rather than a single current-state snapshot. Recovery replays the log to reconstruct current state. This is more robust (no "last checkpoint was mid-transition" failure mode) but adds complexity. Given the engine's checkpoint-on-every-boundary cadence (not mid-phase), a snapshot approach is appropriate.

**Recommended:** JSON snapshot + atomic write. Add an event log as a secondary file (`.events.jsonl`) that appends one line per transition, for debugging/audit. On recovery, use the snapshot as primary; use the event log only if the snapshot is corrupted.

---

## Research Area 4: Phase Orchestration Patterns

### Invoking Claude Code for Each Phase

The engine's constraint is that all phase execution goes through the STORY-003 Claude Code Runner. The runner interface (from seed.md § Phase Execution Contract) receives:
1. System prompt (from STORY-005 persona system)
2. User prompt (constructed by engine with context)
3. Model selection (tier-1 or tier-2)
4. Tool permissions (from STORY-005 persona system)
5. Working directory
6. Timeout

**Context construction (the hardest part):** Each phase requires different prior deliverables as input (see seed.md § Context Construction table). The engine must:
1. Resolve the story folder path (`features/story-XXX-slug/`)
2. For each required prior deliverable, check it exists and read its content (or path)
3. Construct a structured prompt that includes the deliverable content inline or as file references

**File references vs inline content:** There is a fundamental tradeoff:
- **File references** (pass paths): Smaller prompts, runner must have filesystem access to read them. Appropriate when runner runs in same container with same filesystem.
- **Inline content** (embed in prompt): Larger prompts, works across containers. Required if runner is remote.
- **Recommendation:** Given seed.md's container-per-agent model, the runner has the same filesystem. Pass file paths + a summary of each deliverable's key outputs. Do not inline full deliverable content — a `specification.md` can be 3000+ tokens.

### Phase Completion Detection

How does the engine know a phase completed successfully? Three signals, in priority order:

1. **Runner exit code:** Zero exit = success, non-zero = failure. Most reliable signal for hard failures.
2. **Deliverable file existence:** Does the expected output file exist in `features/<story-folder>/`? This is the primary success criterion per the seed's acceptance criteria.
3. **Deliverable content validation:** Does the file have meaningful content (non-empty, passes a basic structure check)? Optional depth-of-validation signal.

**Recommended validation sequence:**
```
runner_result = runner.execute(phase_prompt)

if runner_result.exit_code != 0:
    → mark failed, increment retry_count

elif not deliverable_exists(phase, scope, story_folder):
    → mark failed with "missing deliverable" reason
    → retry once with explicit deliverable instructions

elif deliverable_is_empty(expected_files):
    → mark failed with "empty deliverable" reason
    → retry once with explicit instructions

else:
    → mark completed
    → checkpoint
    → advance to next phase
```

### Bracketed Phase Groups (Parallel Execution)

Phases 6b, 6c, 6d and phases 9, 10 execute as parallel groups — all must complete before advancing. Two implementation approaches:

**Option A — Sequential emulation:** Run 6b, then 6c, then 6d sequentially. Simple, no concurrency management. Phase transitions are still individual and checkpointed. Deliverables are independent (security-review.md, ux-review.md, ops-review.md) so order doesn't matter. This is the recommended approach for the initial implementation.

**Option B — True parallel (asyncio):** Use `asyncio.gather()` to launch all three runner calls concurrently. More complex: requires handling partial failure (one sub-phase failed while others completed), concurrent checkpoint writes (must serialize), and the checkpoint must track sub-phase status individually. Worth implementing in a later iteration when the sequential version is proven.

**Recommended for V1:** Sequential emulation with logical grouping (the checkpoint tracks the group as a whole, or tracks each sub-phase individually). Expand to true async parallelism in Phase 9 refinement.

### Persona Loading (STORY-005 Integration)

The engine calls the STORY-005 persona system with a phase identifier and receives back:
- System prompt text
- Tool permissions list
- Advance category (`gate` | `confirm` | `auto`)
- Model tier (`tier-1` | `tier-2`)

Until STORY-005 exists, the engine must implement a minimal local persona loader that:
1. Locates the persona file: `.sdlc/agents/phase-{N}-*.md`
2. Parses the YAML Identity block (the fenced YAML block near the top)
3. Returns the advance category and model tier
4. Returns the full file content as the system prompt

**YAML extraction from persona files:** The persona files use a pattern like:
```markdown
## Identity
\`\`\`yaml
role: ...
advance: gate
model: tier-1
\`\`\`
```

This is parseable with a simple regex to extract the fenced YAML block, then `yaml.safe_load()` to parse it.

---

## Research Area 5: Existing Framework Analysis — What Can Be Extracted Programmatically

### Persona File Registry

All 33 persona files follow a consistent structure with a YAML Identity block. The engine can build a complete phase registry by scanning `.sdlc/agents/phase-*.md`:

| File | Phase | Advance | Model | Context Group |
|------|-------|---------|-------|---------------|
| phase-1-seed.md | 1 | gate | tier-1 | seed |
| phase-2-research.md | 2 | confirm | tier-2 | research |
| phase-3-expansion.md | 3 | confirm | tier-2 | research |
| phase-4-analysis.md | 4 | confirm | tier-2 | evaluation |
| phase-5-selection.md | 5 | confirm | tier-2 | evaluation |
| phase-6-design.md | 6 | confirm | tier-1 | design |
| phase-6b-security.md | 6b | auto | tier-2 | design |
| phase-6c-ux-review.md | 6c | auto | tier-2 | design |
| phase-6d-ops-review.md | 6d | auto | tier-2 | design |
| phase-7-test-design.md | 7 | confirm | tier-2 | test |
| phase-8-implementation.md | 8 | gate | tier-2 | implementation |
| phase-8b-code-review.md | 8b | auto | tier-2 | implementation |
| phase-9-refinement.md | 9 | confirm | tier-1 | polish |
| phase-10-operations.md | 10 | confirm | tier-1 | polish |
| phase-11-predeploy-gate.md | 11 | gate | tier-2 | deploy |

**Note on seed.md's phase-8 advance discrepancy:** seed.md § Advance Category Handling states Phase 8 advance is `gate`. The phase-8-implementation.md persona file has `advance: confirm` in its Identity block. The AGENTS.md README.md shows Phase 8 as `gate` (requires explicit user review). The engine should treat the AGENTS.md definition as canonical for the `gate` phases (1, 8, 11) since that is the highest-level policy document. **This is a discrepancy that must be resolved in Phase 6 design.**

### Config.yaml Schema Extraction

The `config.yaml` template in `.sdlc/templates/config.yaml` defines the full schema for engine configuration. Key fields relevant to the engine:

```yaml
models:
  opus_allowed: false  # If true, allows tier-1 for tier-2-default phases

orchestration:
  auto_advance: true         # Enables auto/confirm/gate behavior
  parallel_execution: true   # Enables [6b, 6c, 6d] parallel groups
  advance_overrides: {}      # Per-phase overrides (e.g., make 8b confirm instead of auto)

phases:
  skip: []  # Phases to skip (e.g., [2, 3] to skip research for a known problem)
```

The `advance_overrides` field allows the engine to be configured differently for different projects. This must be applied when loading the advance category: `config.advance_overrides.get(phase_id, persona.advance)`.

### Multi-Worker Context

The existing multi-worker protocol (software-development-guidance.md § Multi-Worker Protocol) defines:
- Each story gets its own worktree: `.worktrees/STORY-ID`
- The engine runs within the worktree context
- `.project`, `backlog.md`, `development-tasks.md` are read-only in worktrees (updated by main session after merge)

The engine's checkpoint file (`features/<story-folder>/.checkpoint.json`) is story-scoped and lives within the worktree, which is correct for the multi-worker model.

### Template Availability

The `.sdlc/templates/` directory contains:
- `config.yaml` — Project configuration schema
- `infra/` — CI, Docker Compose templates
- `monitoring/` — Project registry template

There are no deliverable templates (e.g., a `research.md.template`) in the templates directory for standard SDLC phase outputs. The engine cannot use templates for deliverable validation — it must rely on filename existence and non-empty content.

---

## Key Decisions Surfaced

### Decision 1: State Machine Implementation Approach
**Options:** (a) hand-rolled state transitions with explicit `if` guards, (b) `transitions` library (PyPI), (c) `python-statemachine` library
**Recommendation:** Hand-rolled for V1. The state machine is simple (15 states, ~12 transitions), the states are serialized to JSON, and adding a library dependency adds complexity without significant benefit. Revisit in Phase 9 refinement if the transition logic becomes complex.

### Decision 2: Scope Classification Method
**Options:** (a) pure rule-based with manual signal input, (b) LLM + rule-based hybrid, (c) full LLM classification
**Recommendation:** LLM + rule-based hybrid. The LLM extracts structured signals from the natural language task description; a deterministic rule function applies the SDLC scoring. Pydantic validation ensures the LLM output is well-formed before being used. Override via `scope_override` in the event payload bypasses classification entirely.

### Decision 3: Checkpoint Granularity
**Options:** (a) checkpoint only at phase boundaries (between phases), (b) checkpoint at start of phase AND at completion, (c) event-sourced log
**Recommendation:** Checkpoint at START of phase (before execution begins) AND at completion. The "before start" checkpoint enables crash recovery: if the container dies mid-phase, the checkpoint shows `in_progress` for the current phase with `retry_count: 0`, so on resume the engine retries from the beginning of that phase (not the beginning of the story). This is critical for the "checkpoint before phase starts" requirement in seed.md § Risks.

### Decision 4: Bracketed Phase Groups (Parallel vs Sequential)
**Options:** (a) sequential emulation (run one at a time), (b) asyncio.gather() for true parallelism
**Recommendation:** Sequential for V1. True parallelism requires concurrent checkpoint writes, partial-failure handling, and async runner interface. Sequential is simpler, still validates group completion, and can be upgraded in Phase 9.

### Decision 5: Persona Loader (Bootstrap vs STORY-005 Dependency)
**Options:** (a) wait for STORY-005, (b) build a minimal local persona loader as part of this story
**Recommendation:** Build a minimal local persona loader that reads from `.sdlc/agents/phase-*.md` and exposes the same interface that STORY-005 will implement. This unblocks the engine without creating a hard dependency. The loader becomes a thin shim once STORY-005 is available.

### Decision 6: Phase 8 Advance Category (gate vs confirm)
**Discrepancy:** phase-8-implementation.md persona has `advance: confirm`; AGENTS.md README and seed.md § Acceptance Criteria specify Phase 8 as `gate`.
**Impact:** A `gate` advance stops and requires explicit approval (like a PR review). A `confirm` advance asks "Proceed to Phase 8b?" and waits for yes/no. For an autonomous agent, the difference matters: `gate` implies a human must explicitly sign off on the implementation before code review; `confirm` is a softer ask.
**Recommendation:** Resolve in Phase 6 design. Likely the correct answer is `gate` for Phase 8 (production code requires explicit human sign-off), and the persona file has a bug. The engine should read from AGENTS.md as the canonical source until resolved.

---

## Critical Risks Flagged

### Risk 1: Context Window Overflow on Large Stories
Phase 8 context construction includes design deliverables + test-design.md + seed.md. For Large-scope stories, these files can easily total 15,000+ tokens before the implementation prompt itself. The engine's context table (seed.md § Context Construction) correctly scopes each phase's inputs. Implementation must be strict: Phase 8 should NOT receive research.md, expansion.md, or analysis.md — only test-design.md, design deliverables, and seed.md.

**Mitigation:** Implement a context budget calculator that estimates token count before sending to the runner. If over budget (e.g., >80k tokens), truncate lower-priority files or summarize them.

### Risk 2: Phase 8 Implementation Retry with Same Context
If Phase 8 fails (e.g., tests don't pass), the engine retries "with fresh context" (per seed.md § Error Recovery: "retries once with `/clear` [fresh context]"). But what does "fresh context" mean for an automated runner? It means a new runner invocation with no prior conversation history — not a continuation of the failed session. The engine must not pass session history from a failed invocation to the retry invocation.

### Risk 3: Scope Reassessment Triggering Loop
The Scope Reassessment Protocol (software-development-guidance.md) requires user approval for scope changes. In an autonomous engine, "user approval" must be modeled as a `gate` event. The engine must:
1. Detect scope reassessment triggers (per the explicit trigger list in the protocol)
2. Emit a `scope_reassessed` event and transition to `waiting_for_approval`
3. On approval, recalculate the remaining phase path and update the checkpoint
4. Max 1 reassessment without escalation (seed.md § Risks)

### Risk 4: Deliverable Validation False Negatives
A phase might produce a deliverable file with trivially invalid content (e.g., a `research.md` with just "# Research Report\n\nNo findings." or even a blank file). The current validation only checks file existence and non-emptiness. A minimum size threshold (e.g., >500 bytes for research.md, >200 bytes for simpler files) would catch trivially invalid outputs without requiring semantic validation.

---

## Recommendation for Expansion

Three core approaches should be explored in Phase 3:

**Approach 1 — Minimal Orchestrator (File-native, hand-rolled)**
Build the engine as a pure Python module that manages the state machine via JSON checkpoint files, invokes the runner via a subprocess/API call, and parses persona files locally. No external dependencies beyond Python stdlib + pydantic. Maximum simplicity, lowest operational overhead.

**Approach 2 — Async Event-Driven Orchestrator**
Build the engine around `asyncio` with an internal event bus (e.g., using Python's `asyncio.Queue` or a lightweight library like `aio-pika` for a local message broker). Events (`phase_started`, `gate_reached`, etc.) flow through the bus; subscribers handle notifications and state updates. More architecturally clean, supports true parallel bracketed groups, but more complex to implement and debug.

**Approach 3 — Lightweight Workflow Library (Prefect local)**
Use Prefect's local mode (no server required) to handle the flow execution, state persistence, and retry logic. The engine would define an SDLC flow with tasks for each phase. Prefect handles checkpoint/resume, retries, and concurrency natively. Trade-off: adds an external dependency (Prefect ~20MB), but eliminates hand-rolling state management and retry logic. Prefect's local mode stores state in a local SQLite database — compatible with the container model.

**Top recommendation:** Start with Approach 1 (Minimal Orchestrator) for the V1 implementation. The state machine and checkpoint logic are well-defined enough to implement clearly without a framework. Approach 2 or 3 can be evaluated in Phase 3 expansion as alternatives if the hand-rolled approach proves too complex for the retry/recovery scenarios.

---

## Dependency Health

| Dependency | Status | Notes | Action |
|-----------|--------|-------|--------|
| Python 3.12+ | Healthy | Project standard per AGENTS.md | Continue |
| Pydantic v2 | Healthy | Core dependency, widely used in project stack | Use for state models and LLM output validation |
| PyYAML | Healthy | Needed for persona YAML block parsing | Include |
| `transitions` (FSM library) | Aging — last release 2024-04 | Optional — only if hand-rolled becomes unwieldy | Evaluate in Phase 3 |
| `python-statemachine` | Healthy — release 2025-11 | Alternative FSM library | Evaluate in Phase 3 |
| Prefect 3.x | Healthy — active development | Optional workflow engine | Evaluate as Approach 3 in Phase 3 |
| asyncio (stdlib) | Healthy | Python stdlib — no version concern | Available when needed for parallel phases |

---

## Sources Referenced

- seed.md (STORY-006) — Phase Execution Contract, Context Construction, Event Interface, Checkpoint Format
- AGENTS.md — Phase paths, advance categories, model tiers, phase overview table
- `.sdlc/agents/README.md` — Phase overview table with all advance categories
- `.sdlc/agents/phase-1-seed.md` — Scope Classification Guide, Epic Escalation Check
- `.sdlc/agents/phase-2-research.md` — Orchestrator sub-agent pattern, research workflow
- `.sdlc/agents/phase-8-implementation.md` — Phase 8 advance category (discrepancy flagged)
- `.sdlc/templates/config.yaml` — Full config schema including `advance_overrides`, `opus_allowed`
- `software-development-guidance.md` (lines 1-400) — Scope classification, story sizing guardrails, scope reassessment protocol, epic execution flow
- GitHub Actions documentation (workflow job execution model, environment protection rules)
- Temporal.io documentation (durable execution, signal/query patterns, activity vs workflow separation)
- Prefect 2/3 documentation (task run states, result persistence, subflows)
- Argo Workflows documentation (suspend templates, exit handlers)
