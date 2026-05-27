# Ops Review: SDLC Execution Engine

> Phase 6d — Ops Review
> Story: STORY-006
> Date: 2026-03-26

---

## 1. Checkpoint Storage Durability — Ephemeral Container Risk

**Finding (HIGH):** `.checkpoint.json` and `.events.jsonl` are written to `features/<story-slug>/`
inside the working directory. If the engine runs in an ephemeral container (CI runner, Docker) without
a persistent volume mount, a container restart after a crash destroys all checkpoint state. The engine
would re-start from Phase 1, re-spending API budget on already-completed phases.

**Recommendations:**
- Document that `features/` must be on a persistent volume or backed by git (committed after each
  phase) when running in CI.
- Add a post-phase git commit hook option (`config.commit_after_phase: bool`, default false) that
  commits the story folder after each successful phase — this doubles as both durability and
  a natural audit trail.
- The `CheckpointManager` should log the storage path at startup with an `INFO` message so operators
  can verify the mount is correct.

---

## 2. Engine Crash Recovery Reliability

**Finding (MEDIUM):** The atomic write protocol (write to `.checkpoint.json.tmp` then `os.replace()`)
is correct for single-process crashes. However, if the engine is killed with SIGKILL between phases,
the "before execution begins" write may not have occurred, and the checkpoint reflects the previous
phase as `completed` while the current phase was never started. Resume would correctly skip to the
next phase — but only if the phase transition write happened before the kill.

**Recommendations:**
- Add a startup self-check: if `completed_phases` contains phase N and `current_phase` is N+1 but
  no deliverable for N+1 exists, treat `current_phase` as `in_progress` and increment retry_count.
- Test this scenario explicitly: `SIGKILL` the engine between the phase-completed write and the
  next phase-started write, then verify `resume` produces correct output.
- Log `orphaned .checkpoint.json.tmp detected and deleted` at `WARN` level (spec mentions cleanup
  but does not require logging it).

---

## 3. Monitoring and Alerting — Stuck Phases

**Finding (MEDIUM):** Phase timeouts are enforced by the runner (per `timeout_seconds`), but there is
no external watchdog. If the runner itself hangs (subprocess deadlock, network stall in tracker call),
the engine can be silently stuck beyond the stated timeout with no alert generated.

**Recommendations:**
- Emit a heartbeat log line every 60 seconds while a phase is in `in_progress`:
  `[HEARTBEAT] STORY-006 Phase 8 still running (elapsed: 12m 00s, timeout: 30m)`
- `TrackerPlugin.on_phase_started()` should set a Monday.com "last heartbeat" timestamp column
  so an external monitor can alert if no update in `timeout_seconds + 120s`.
- The `tracker_timeout_seconds: 5` config value is good — verify it is enforced with a `threading`
  or `asyncio` timeout wrapper around every tracker call, not just documented.

---

## 4. Resource Usage During Long-Running Phases

**Finding (LOW):** `ContextBuilder` reads all prior deliverable files into memory to estimate token
count. For a Large-scope story at Phase 11 (Pre-Deploy), "all deliverables produced so far" could be
10–15 files. No maximum total memory limit is defined.

**Recommendations:**
- Read deliverable files lazily (stream in chunks) rather than loading all into memory simultaneously.
- Cap individual file read size at `2 * context_budget_tokens * 4` bytes (2x budget × 4 bytes/token)
  to prevent a runaway deliverable from consuming unbounded memory.
- Log estimated total context size at `DEBUG` level before each runner call so memory pressure is
  visible in logs: `Context built: 12 files, ~54,200 tokens estimated`.

---

## 5. Log Verbosity and Structured Logging

**Finding (LOW):** The spec defines a structured log format for `phase_failed` events (section 6.4)
but does not define log levels or structure for normal-path events. Without consistent structured
logging, log aggregators (Datadog, CloudWatch) cannot build dashboards or alert on story throughput.

**Recommendations:**
- Standardize all engine log events on the same JSON schema: `level`, `event`, `story_id`,
  `phase_id`, `timestamp`, plus event-specific fields.
- Define a log level policy: `DEBUG` for context construction details; `INFO` for phase
  start/complete/gate; `WARN` for retries, guardrail warnings, orphaned temp files;
  `ERROR` for failures and escalations.
- Ship a sample Datadog/CloudWatch log filter config alongside the engine so teams can
  instrument `phase_failed` and `escalation_required` alerts from day one.
