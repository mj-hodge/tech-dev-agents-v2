# UX Review: SDLC Execution Engine

> Phase 6c — UX Review
> Story: STORY-006
> Date: 2026-03-26

---

## 1. Engine Failure Mid-Phase — Error Messages and Recovery Steps

**Finding:** When a phase fails after 2 retries, the engine emits `EscalationRequiredEvent` and halts.
The spec defines the event payload but does not define what the operator actually sees or reads next.
The `LoggingNotifierPlugin` reference implementation logs to stdout — no actionable recovery instruction
is produced.

**Recommendations:**
- `on_escalation_required()` must print a structured human-readable block:
  ```
  ESCALATION REQUIRED — STORY-006 Phase 8
  Error:    runner_error (2 retries exhausted)
  Message:  Tests failed — 3 of 12 assertions failed
  Checkpoint: features/story-006-sdlc-engine/.checkpoint.json
  Next steps:
    1. Inspect the checkpoint file to review phase state
    2. Fix the underlying issue
    3. Re-run: sdlc resume STORY-006
  ```
- The CLI entry point must propagate `EscalationRequiredEvent` details to stderr with a non-zero
  exit code so CI/CD pipelines detect the failure.

---

## 2. Scope Classification Transparency

**Finding:** When the engine classifies a task as "medium" (skipping research and expansion phases),
developers have no visibility into why. `ScopeClassification.rationale` exists in the checkpoint but
is never surfaced to the operator during normal execution.

**Recommendations:**
- After classification, always print a one-line summary:
  `Scope: medium (score=7: 2 API endpoints, 1 DB model, cross-cutting auth concern)`
- If `guardrail_warnings` is non-empty, print each warning prominently before proceeding:
  `WARNING: estimated_test_count=35 exceeds 30-test limit — consider splitting the story`
- For `method: "override"`, print: `Scope: large (manual override — classification skipped)` so
  operators know the automated path was bypassed.

---

## 3. Checkpoint Debugging — Inspecting Engine State

**Finding:** The spec defines `.checkpoint.json` and `.events.jsonl` but provides no tooling for
operators to inspect them. Developers who need to diagnose a stuck or failed engine must manually
parse JSON.

**Recommendations:**
- Provide a `sdlc inspect STORY-XXX` CLI command that pretty-prints checkpoint state:
  current phase, status, retry count, completed phases, and any errors array entries.
- Include a `--events` flag to tail `.events.jsonl` with human-readable timestamps and summaries.
- On checkpoint load during resume, always log one line at `INFO` level:
  `Resuming STORY-006: phase=8, status=in_progress, retry=1/2`

---

## 4. Phase Progress Visibility

**Finding:** `PhaseStartedEvent` and `PhaseCompletedEvent` are emitted but the `LoggingNotifierPlugin`
only handles gate/confirm/escalation events. The operator running the engine in a terminal sees no
progress during long phases (Phase 6 and Phase 8 each have 30-minute timeouts).

**Recommendations:**
- The engine's execution loop must log progress at key points regardless of notifier:
  - Phase start: `[Phase 6 — Design] Starting (timeout: 30 min)…`
  - Deliverable validation: `[Phase 6 — Design] Checking deliverables: specification.md ✓, architecture.md ✓…`
  - Phase complete: `[Phase 6 — Design] Complete (18m 42s) — 5 deliverables written`
- For confirm-advance phases, the "Proceed?" prompt must name the next phase and its estimated
  duration: `Proceed to Phase 7 (Test Design, ~10 min)? [y/N]`
- Progress lines must go to stdout (not just structured JSON logs) so developers running locally
  get real-time feedback without needing a log aggregator.
