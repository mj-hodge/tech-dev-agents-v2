# Seed: SDLC Execution Engine

> Phase 1 -- Concept & Seed
> Date: 2026-03-26
> Scope: Large
> Story: STORY-006
> Depends on: STORY-003 (Claude Code Runner), STORY-005 (Persona System)
> Downstream: STORY-007 (Approval Flow)

---

## Problem Statement

The autonomous dev agent needs a brain -- a component that reads a task, determines how much process to apply, and then drives the work through the correct sequence of SDLC phases at the correct depth. Without this engine, the agent either over-engineers trivial changes (running 12 phases for a one-line fix) or under-engineers complex features (skipping design and jumping straight to code).

Today, the SDLC framework exists as static documentation (`.sdlc/` directory with agent personas, phase guidance, templates, and scope classification rules). A human developer reads this documentation and manually navigates the process. The execution engine must encode this human judgment into software: classify scope, resolve the phase path, drive execution through each phase, enforce gates, produce deliverables, track state, and recover from failures.

This is the central orchestration component of the entire agent system. Every other story feeds into or consumes from it: STORY-003 provides the Claude Code Runner that executes individual phases, STORY-005 provides the persona system that configures agent behavior per phase, and STORY-007 consumes the gate detection events to trigger approval flows via Teams.

## Target User

**Primary:** The orchestrator layer of the autonomous dev agent. This engine is called programmatically -- it does not have a direct human interface.

**Secondary:** The human developer who monitors execution via Monday.com status updates and Teams notifications at gates.

---

## Success Criteria

When this is done:

1. Given a task description and repository context, the engine classifies scope as trivial, small, medium, large, or epic
2. Given a scope classification, the engine resolves the correct phase path (e.g., medium -> 1, 4, 6, 6b, 6c, 6d, 7, 8, 8b, 11)
3. The engine drives execution through each phase sequentially, invoking the Claude Code Runner (STORY-003) with the correct persona (STORY-005) for each phase
4. At each phase boundary, the engine checks the advance category and behaves correctly: `gate` phases stop and emit a notification event, `confirm` phases stop and emit a confirmation request, `auto` phases proceed immediately
5. Each phase produces its required deliverable file in `features/<story-folder>/`
6. The engine updates `.project` with current phase status after each transition
7. The engine updates Monday.com with phase progress after each transition
8. The engine persists checkpoint state to disk so that execution can resume after container restart, timeout, or crash
9. The engine handles phase failures gracefully: retries with fresh context, and after 2 failures escalates to the human

---

## Acceptance Criteria

### Scope Classification

- [ ] Engine accepts a task description (user story text, acceptance criteria, optional codebase context) and returns a scope classification: trivial | small | medium | large | epic
- [ ] Classification uses defined heuristics: number of files affected, number of new models/endpoints, presence of new integrations, cross-cutting concerns, and estimated test count
- [ ] Classification can be overridden by explicit user input (e.g., "treat this as medium")
- [ ] Engine detects epic signals per the Epic Escalation Check (`.sdlc/agents/phase-1-seed.md`) and escalates when 2+ signals are present
- [ ] Story sizing guardrails are enforced: >30 tests, >2 new DB models, >3 new endpoints, or >1 architectural layer triggers a mandatory split recommendation

### Phase Path Resolution

- [ ] Engine maps each scope to its canonical phase path:
  - Trivial: 8 -> Done
  - Small: 1 -> 7 -> 8 -> Done
  - Medium: 1 -> 4 -> 6 -> [6b, 6c, 6d] -> 7 -> 8 -> 8b -> 11 -> Done
  - Large: 1 -> 2 -> 3 -> 4 -> 5 -> 6 -> [6b, 6c, 6d] -> 7 -> 8 -> 8b -> 11 -> [9, 10] -> Done
  - Epic: 1 -> decompose -> per-story SDLC -> [E2E gate] -> repeat -> Retrospective -> Done
- [ ] Bracketed phases (e.g., [6b, 6c, 6d]) execute as a group -- all must complete before advancing to the next phase
- [ ] Phase path is determined once at engine start and stored in state; mid-execution scope reassessment can modify the remaining path
- [ ] Engine supports scope reassessment: if a phase reveals the scope is wrong (per the Scope Reassessment Protocol), the engine recalculates the remaining path and restarts from the first new required phase

### Advance Category Handling

- [ ] Engine reads the `advance` field from each phase's agent persona file
- [ ] **gate** (Phases 1, 8, 11): Engine stops execution, emits a `gate_reached` event with phase deliverable summary, and transitions to `waiting_for_approval` state. Execution resumes only on explicit external approval signal
- [ ] **confirm** (Phases 2, 3, 4, 5, 6, 7, 9, 10): Engine stops execution, emits a `confirmation_requested` event with a "Proceed to Phase X?" prompt, and transitions to `waiting_for_confirmation` state. Execution resumes on yes; on no, transitions to `blocked` state
- [ ] **auto** (Phases 6b, 6c, 6d, 8b): Engine proceeds immediately to the next phase without stopping. No event emitted, no wait
- [ ] Events are emitted via a pluggable event interface so that STORY-007 (Approval Flow) can subscribe to `gate_reached` and `confirmation_requested` without tight coupling

### Deliverable Production

- [ ] Engine creates `features/<story-folder>/` directory using the naming convention `features/story-XXX-kebab-case-slug/`
- [ ] After each phase completes, engine verifies the expected deliverable file exists:
  - Phase 1: `seed.md`
  - Phase 2: `research.md`
  - Phase 3: `expansion.md`
  - Phase 4: `analysis.md`
  - Phase 5: `selection.md`
  - Phase 6: `specification.md`, `architecture.md`, `api-design.md`, `database-schema.md`, `implementation-plan.md` (Large) or `feature-spec.md` (Medium)
  - Phase 6b: `security-review.md`
  - Phase 6c: `ux-review.md`
  - Phase 6d: `ops-review.md`
  - Phase 7: `test-design.md` + runnable test code in `tests/`
  - Phase 8: Implementation code (all tests GREEN)
  - Phase 8b: `code-review.md`
  - Phase 11: `predeploy-gate.md`
  - Phase 9: `refinement-report.md`
  - Phase 10: `site-reliability.md`
- [ ] If a deliverable is missing after phase execution, engine retries the phase once with explicit instructions to produce the file. After second failure, marks phase as `failed` and escalates
- [ ] Engine never writes deliverables to project root or `docs/` -- always to `features/<story-folder>/`

### Tracking Updates

- [ ] Engine updates `.project` file with: current phase, phase status (in_progress | completed | failed | waiting), timestamp, and story scope after every phase transition
- [ ] Engine updates Monday.com task with a comment summarizing each phase completion (phase name, key outputs, duration)
- [ ] Engine updates Monday.com task status column to reflect overall story progress
- [ ] Engine updates `backlog.md` with story status at phase boundaries
- [ ] Engine updates `development-tasks.md` during Phases 7-8

### Checkpoint and Resume

- [ ] Engine persists execution state to a JSON checkpoint file at every phase boundary: `features/<story-folder>/.checkpoint.json`
- [ ] Checkpoint contains: story ID, scope classification, full phase path, completed phases, current phase, phase status, advance category state (waiting/approved/rejected), timestamps, retry counts, and any mid-phase context (e.g., scope reassessment data)
- [ ] On startup, engine checks for existing checkpoint and resumes from last completed phase
- [ ] Resume correctly handles all states: if checkpoint shows `waiting_for_approval`, engine re-emits the gate event rather than re-executing the phase
- [ ] Checkpoint file is atomic (write-to-temp-then-rename) to prevent corruption from crashes during write
- [ ] Engine supports explicit checkpoint reset (discard state and restart from Phase 1) via API call

### Error Recovery

- [ ] Phase execution timeout: configurable per-phase (default 10 minutes for research phases, 30 minutes for implementation). On timeout, mark phase `failed`, persist checkpoint, escalate
- [ ] Claude Code Runner failure: if STORY-003 runner returns an error, engine retries once with `/clear` (fresh context). After second failure, mark `failed` and escalate
- [ ] Deliverable validation failure: if expected output file is missing after execution, retry once with explicit deliverable instructions. After second failure, escalate
- [ ] After 2 consecutive failures on the same phase, engine stops and emits an `escalation_required` event with failure details, phase context, and retry history
- [ ] All failures are logged with: phase, error type, retry count, duration, and the prompt/context that was sent to the runner

### Model Enforcement

- [ ] Engine selects the correct model tier for each phase per the Model Policy table:
  - tier-1 (Opus): Phases 1, 6, 9, 10
  - tier-2 (Sonnet): Phases 2-5, 7, 8, 8b, 11
- [ ] Engine passes model selection to the Claude Code Runner (STORY-003) so the runner invokes the correct model
- [ ] If `config.yaml` contains `models.opus_allowed: true`, engine allows tier-1 for tier-2-default phases
- [ ] For orchestrated phases (2, 3, 4, 8b), engine instructs the runner to dispatch sub-agents at the correct tier per the orchestration table

---

## Constraints

| Constraint | Detail |
|-----------|--------|
| **Framework alignment** | Must implement the process defined in `.sdlc/software-development-guidance.md` and `AGENTS.md` exactly -- not a reimagining of the SDLC, but a faithful programmatic encoding of the existing documented process |
| **Claude Code Runner** | Must use STORY-003's runner interface for all phase execution. The engine orchestrates; the runner executes. No direct Claude API calls from the engine |
| **Persona System** | Must use STORY-005's persona loading to configure agent behavior for each phase. The engine selects the persona; the persona system resolves the full system prompt and tool permissions |
| **Event-driven gates** | Gate and confirm stops must emit events, not call Teams/Monday.com directly. STORY-007 (Approval Flow) handles the notification delivery. The engine is decoupled from the notification channel |
| **Single-story scope** | The engine manages one story at a time. Multi-story coordination, parallel execution, and work assignment are out of scope (those are orchestrator-level concerns) |
| **Stateless between phases** | Each phase invocation via the runner starts with fresh context. The engine must pass all necessary context (previous deliverables, story metadata, persona config) to each phase invocation explicitly |
| **Deterministic paths** | Phase paths are deterministic given a scope classification. The engine does not use heuristics to skip or add phases dynamically (except via the formal Scope Reassessment Protocol) |
| **File system as state store** | Checkpoint state is stored on the local file system (JSON files), not in a database. This aligns with the container-per-agent model where each agent has its own filesystem |

---

## Out of Scope

- **Orchestrator agent**: Multi-story coordination, backlog triage, work assignment across agents. That is a future epic-level concern
- **Multi-story coordination**: Running multiple stories in parallel, managing dependencies between concurrent stories, merge conflict resolution
- **Epic-level management**: Story decomposition from epics, E2E gate coordination, cross-story dependency tracking
- **Notification delivery**: The engine emits events; STORY-007 handles delivering those events to Teams. No Teams SDK usage in this story
- **Git operations**: Branch creation, commits, and PR opening are STORY-008's responsibility. The engine may trigger git operations via events but does not implement them
- **Container provisioning**: The engine assumes it is running inside a provisioned container (STORY-001). No container lifecycle management
- **Cost tracking**: No budget enforcement or cost monitoring. The engine selects models per policy but does not track API costs
- **Interactive mode**: No REPL or interactive phase-by-phase human-driven mode. The engine is designed for autonomous execution with gate-based pauses
- **Custom phase paths**: No user-defined phase sequences. The engine implements the five canonical paths (trivial through epic) only
- **Self-modification**: The engine does not modify `.sdlc/` framework files, agent personas, or templates. It is a consumer of these artifacts, not a producer

---

## Technical Notes

### State Machine

The engine is fundamentally a state machine with the following states per phase:

```
pending -> in_progress -> completed -> [next phase]
                      \-> failed -> retry -> in_progress
                      \-> waiting_for_approval -> approved -> [next phase]
                                              \-> rejected -> blocked
                      \-> waiting_for_confirmation -> confirmed -> [next phase]
                                                   \-> declined -> blocked
```

Story-level states:

```
not_started -> in_progress -> completed
                           \-> blocked (gate rejection or repeated failure)
                           \-> failed (unrecoverable error)
```

### Phase Execution Contract

Each phase invocation sends to the Claude Code Runner:

1. **System prompt**: Loaded from persona system (STORY-005) for the target phase
2. **User prompt**: Constructed by the engine with: task description, previous phase deliverables (file paths or content), expected outputs, story metadata
3. **Model selection**: tier-1 or tier-2 per the model policy
4. **Tool permissions**: Loaded from persona system (STORY-005) for the target phase
5. **Working directory**: The target repository root (with `features/<story-folder>/` as the deliverable target)
6. **Timeout**: Phase-specific timeout value

The runner returns: success/failure, output files produced, cost data, session ID, and any error details.

### Context Construction

A critical responsibility of the engine is constructing the right context for each phase. Each phase needs different inputs:

| Phase | Context Inputs |
|-------|---------------|
| 1 (Seed) | Task description, codebase overview, existing patterns |
| 2 (Research) | seed.md |
| 3 (Expansion) | seed.md, research.md |
| 4 (Analysis) | seed.md, expansion.md (Large) or seed.md (Medium) |
| 5 (Selection) | seed.md, expansion.md, analysis.md |
| 6 (Design) | seed.md, selection.md (Large) or analysis.md (Medium) |
| 6b/6c/6d | Design deliverables from Phase 6 |
| 7 (Test Design) | Design deliverables, seed.md |
| 8 (Implementation) | test-design.md, design deliverables, seed.md |
| 8b (Code Review) | Implementation code, test results, design deliverables |
| 11 (Pre-Deploy) | All deliverables, test results, implementation code |
| 9 (Refinement) | All deliverables, implementation code, test results |
| 10 (Operations) | All deliverables, architecture.md, implementation code |

The engine must read previous deliverables from `features/<story-folder>/` and include them in the prompt context. It must not include irrelevant deliverables (e.g., do not send research.md to Phase 8).

### Event Interface

```
Events emitted:
  - phase_started(story_id, phase, scope)
  - phase_completed(story_id, phase, deliverables[], duration)
  - phase_failed(story_id, phase, error, retry_count)
  - gate_reached(story_id, phase, summary)
  - confirmation_requested(story_id, phase, next_phase, summary)
  - escalation_required(story_id, phase, error, context)
  - story_completed(story_id, scope, phases_completed[], total_duration)
  - scope_reassessed(story_id, old_scope, new_scope, trigger_phase, rationale)

Events consumed:
  - approval_received(story_id, phase, approved: bool, comment?)
  - confirmation_received(story_id, phase, confirmed: bool, comment?)
  - execution_requested(story_id, task_description, scope_override?)
```

---

## Risks

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Scope classification is too inaccurate | Wrong phase path leads to over/under-engineering | Use structured heuristics, not free-form LLM judgment. Allow user override. Log classification rationale for tuning |
| Phase execution produces wrong/missing deliverables | Downstream phases receive bad input | Validate deliverable existence and basic structure after each phase. Retry with explicit instructions on first failure |
| Checkpoint corruption during crash | Story execution restarts from beginning, wasting time and cost | Atomic writes (temp file + rename). Periodic checkpoint validation |
| Context window overflow on large stories | Phase execution degrades or fails silently | Only include relevant prior deliverables per the context table. Never send all deliverables to every phase |
| Long-running phases hit container timeout | Execution lost without clean state save | Checkpoint before each phase starts (not just after completion). Container restart triggers resume from checkpoint |
| Model policy violations cause cost overrun | 15x cost on Opus when Sonnet suffices | Engine enforces model selection before invoking runner. Runner rejects tier-1 requests for tier-2 phases unless config override is set |
| Scope reassessment creates infinite loops | Engine oscillates between scopes | Max 1 reassessment per story. Second reassessment requires human approval |

---

## Assumptions

1. STORY-003 (Claude Code Runner) provides a clean programmatic interface: accept a system prompt, user prompt, model selection, and tool permissions; return structured results
2. STORY-005 (Persona System) provides a loading interface: accept a phase identifier; return the system prompt, tool permissions, and advance category
3. The target repository contains a `.sdlc/` directory (or the engine carries its own copy) with agent personas and templates
4. The container filesystem is persistent across restarts (or mounted volume) so checkpoint files survive
5. Monday.com MCP tools are available for status updates (STORY-004 provides integration, but the engine uses it via MCP tools directly)
6. Phase execution is sequential within a story -- no intra-story parallelism except for bracketed groups (6b/6c/6d can run in parallel)

---

## Real Data Samples

The "data" for this engine is the SDLC framework itself. Key reference files:

- `.sdlc/software-development-guidance.md` -- Phase definitions, scope classification rules, deliverable requirements
- `.sdlc/agents/phase-*.md` -- Agent personas with advance categories, model tiers, and behavioral instructions
- `AGENTS.md` -- Canonical phase paths, advance categories, model policy, story sizing guardrails
- `CLAUDE.md` -- Deliverable location rules, phase deliverable table, context management rules
- `.sdlc/templates/` -- File templates for deliverables

Example checkpoint state:

```json
{
  "story_id": "STORY-042",
  "story_slug": "story-042-user-notifications",
  "scope": "medium",
  "phase_path": ["1", "4", "6", "6b", "6c", "6d", "7", "8", "8b", "11"],
  "completed_phases": ["1", "4", "6", "6b", "6c", "6d"],
  "current_phase": "7",
  "phase_status": "in_progress",
  "advance_state": null,
  "retry_count": 0,
  "created_at": "2026-03-26T10:00:00Z",
  "updated_at": "2026-03-26T14:32:00Z",
  "deliverables": {
    "1": "features/story-042-user-notifications/seed.md",
    "4": "features/story-042-user-notifications/analysis.md",
    "6": "features/story-042-user-notifications/feature-spec.md",
    "6b": "features/story-042-user-notifications/security-review.md",
    "6c": "features/story-042-user-notifications/ux-review.md",
    "6d": "features/story-042-user-notifications/ops-review.md"
  },
  "scope_reassessments": [],
  "errors": []
}
```

---

## Next Phase

**Phase 2: Research** -- Investigate state machine frameworks, checkpoint/resume patterns, and scope classification approaches suitable for encoding SDLC heuristics into a deterministic engine. Research how similar "workflow engine" or "pipeline orchestrator" patterns are implemented in comparable autonomous agent systems.
