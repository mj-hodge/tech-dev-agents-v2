# Security Review: SDLC Execution Engine

> Phase 6b — Security Review
> Story: STORY-006
> Date: 2026-03-26

---

## 1. Checkpoint File Tampering

**Finding (HIGH):** `.checkpoint.json` sits in the story folder with no integrity protection. An attacker
with filesystem write access can edit `completed_phases`, `current_phase`, or `phase_status` to skip
gate approvals or force a phase back to `in_progress`, effectively bypassing all human approval gates.

**Recommendation:**
- On checkpoint load, verify an HMAC-SHA256 signature stored as a `"sig"` field (keyed from a
  process-local secret or a repo-scoped secret in the environment).
- Treat any signature mismatch as checkpoint corruption (existing path: log warning, start fresh).
- Document that checkpoint directory permissions should match repo permissions (not world-writable).

---

## 2. RunnerPlugin — Arbitrary Code Execution

**Finding (HIGH):** The `RunnerPlugin` interface passes `working_directory` and `tool_permissions` to an
external subprocess (`LocalRunner` calls `claude` CLI). Tool permissions default to "all tools" in V1
(`# V1: default all tools`). A malicious or misconfigured persona file could enable destructive tools
(file deletion, shell execution) without engine-level constraint.

**Recommendations:**
- Replace `# V1: default all tools` with an explicit allowlist per phase in the hardcoded registry.
  Phases 6b/6c/6d (review-only) should have read-only tool permissions.
- `LocalRunner` must not pass raw `task_description` or persona content as unsanitized shell arguments;
  use subprocess array form (not shell=True) and pass prompts via stdin or temp file.
- Log the resolved tool permission list at `DEBUG` level before each runner call for audit.

---

## 3. Scope Classification Manipulation

**Finding (MEDIUM):** The `ScopeClassifier` sends `task_description` verbatim to the LLM for signal
extraction. A crafted description (e.g., injecting "new_integrations: 0, architectural_impact: false")
could bias the LLM response to produce a falsely low scope, skipping phases like research or design.

**Recommendations:**
- After LLM signal extraction, apply a secondary rule-based sanity check: if the raw task description
  contains keywords ("integration", "architecture", "database schema") but the corresponding signals
  are zero, emit a `guardrail_warning` and surface it to the operator.
- Enforce `scope_override` only for authenticated callers (not from task description content itself);
  override should come exclusively from `EngineConfig` or a signed CLI flag.

---

## 4. Event Bus Injection

**Finding (MEDIUM):** The `EventBus` is in-process and synchronous. Any registered handler can publish
additional events during its own execution. A misbehaving or injected `NotifierPlugin` or `TrackerPlugin`
could publish a spurious `ApprovalReceivedEvent` to advance a gate without real human action.

**Recommendations:**
- Engine-internal handlers (`ApprovalReceivedEvent`, `ConfirmationReceivedEvent`) should only be
  triggered by trusted sources. Add an `origin: str` field to these events and reject events
  where `origin` is not `"story-007-approval-flow"` or `"cli"`.
- Consider making external-input events a separate channel from internal-progress events to
  limit cross-contamination.

---

## 5. Secret Handling in Phase Context

**Finding (MEDIUM):** `ContextBuilder` reads deliverable files from the story folder and includes their
full content in the LLM prompt. If any deliverable inadvertently contains secrets (API keys in
`implementation-plan.md`, credentials in `database-schema.md`), those secrets are forwarded to the
runner and logged in `last_prompt_summary` inside `EscalationRequiredEvent`.

**Recommendations:**
- `ContextBuilder` must never read from files outside `features/<story-slug>/` (path traversal guard:
  resolve and assert the path starts with the story folder absolute path).
- `EscalationRequiredEvent.last_prompt_summary` must be truncated to a safe length (e.g., 500 chars)
  before being written to structured logs or emitted to the notifier.
- Document that secrets must not be placed in any SDLC deliverable file; add a linting step to
  the pre-phase validation that rejects files matching common secret patterns (Bearer, sk-, password=).
