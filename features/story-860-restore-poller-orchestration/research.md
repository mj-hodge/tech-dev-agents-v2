# STORY-860 — Research: Restore Poller-Side Orchestration

**Phase:** 2 — Research
**Date:** 2026-05-04
**Scope:** Large (v1 anatomy · v2 contract gaps · migration requirements · peer-art)

---

## 1. V1 `run_sdlc_phases` Anatomy

### 1.1 Signature & Location

```python
# sdlc_phase_runner.py:2861
def run_sdlc_phases(
    *,
    story_id: str,
    repo: str,
    scope: str,
    prompt: str,
    workdir: str,
    env: dict | None = None,
    resumed_question_path: str | None = None,
    rework_of: str | None = None,
) -> tuple[bool, str | None]:
```

File is 2800+ lines; function body spans ~600 lines.

### 1.2 Phase Sequences (Scope-Defined Constants)

Each tuple: `(phase_num, name, deliverable_file, prompt_template, max_turns)`

| Scope | Phase sequence |
|-------|----------------|
| small | 1 → 7 → 8 |
| medium | 1 → 4 → 6 → 7 → 8 |
| large | 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10 |
| research | 2 only |

Phase path can be overridden by `## Phase Path` declaration in `seed.md` (parsed by `_extract_phase_path()`). This lets large stories skip phases 9/10 when not needed.

### 1.3 Pre-loop Guards

Before entering the phase loop, `run_sdlc_phases` checks the ops-console for terminal states
(`done`, `completed`, `phase_8_complete`). If found → return immediately. Prevents duplicate
work when a story somehow gets dispatched twice.

### 1.4 Resume Logic — Deliverable-Based (Current V1)

**V1 uses filesystem-based phase skip:**
- Before running phase N, checks if the phase's expected deliverable already exists on disk
  (e.g., `features/{story_folder}/seed.md`)
- File exists → emit `phase_skip` event, advance to N+1
- **No DB query** — the local filesystem is the state store

**Implication for v2:**
- Works when the same VM picks up the retry (deliverable persists)
- Fails across VM boundaries (different agent, no deliverables on disk)
- **V2 event log enables VM-portable resume**: query `dispatch_v2_events` for last
  `phase_completed` of the prior `job_id`, skip preceding phases
- Phase 4 decision: keep v1 filesystem check as fast-path OR go events-only for portability

### 1.5 Branch Setup — `_ensure_branch`

Location: `sdlc_phase_runner.py:~2500`

**Resume path (remote branch exists):**
1. `git ls-remote --heads origin story-{N}/*` — probes for existing remote branch
2. `git fetch origin <branch>`
3. Stash dirty tree if any (never blocks checkout)
4. `git checkout -B <branch> --track origin/<branch>` (fallback: plain `checkout`)

**Greenfield path (no remote branch):**
1. `_resolve_default_branch()` — uses `git symbolic-ref refs/remotes/origin/HEAD` (never hardcodes "main")
2. `git fetch origin` → `git checkout <default>` → `git pull`
3. `git checkout -B story-{N}/work`

**Post-checkout sync (STORY-800):**
`git fetch origin <branch>` + `git reset --hard origin/<branch>` + `git submodule update --init --recursive` — ensures operator edits (overrides, answers) reach the workdir.

**Rework routing:**
When `rework_of` is set, `ls-remote` targets `story-{rework_num}/*` (base story's branch).
Deliverables go to the base story's folder.

**Error handling:**
All git ops via `_git_check()` helper — raises `RuntimeError` + emits `git_command_failed` event on non-zero return.

### 1.6 Heartbeat Thread

**Configuration:** Single daemonic thread, one per story, covers ALL phases.
- **Interval:** `HEARTBEAT_INTERVAL = 300 s` (constant; `DISPATCH_LEASE_RENEW_INTERVAL` env var controls v2 equivalent)
- **POSTs to:** `/api/dispatch/heartbeat/{story_id}` (v1 API) — v2 equivalent is `/api/dispatch/v2/heartbeat` with `lease_token`
- **Watchdog (STORY-763):**
  - **(a) PID liveness:** `os.kill(sdk_pid, 0)` each tick — dead PID → fail story
  - **(b) Progress stall:** `time.time() - last_output_ts[0] > phase_timeout_s × 2.0` → SIGTERM → SIGKILL

Single thread covers all phases — no restart at phase boundaries. This is the right design for v2 too.

### 1.7 Phase SDK Invocation (`_run_phase_sdk`)

```
python3 /opt/agent/claude_sdk_tool.py -p <phase_prompt> -w <workdir> [--model opus]
```

- **Model selection:** Opus for phases {1, 6, 9, 10}; Sonnet for all others
- **Session resume (STORY-511):** `--resume <session_id>` passed across phases so Claude keeps context
- **Per-phase timeout:** Phase 8: 3600s (large) / 1800s (medium) / 1200s (small); others: 1200s
- **Post-phase save:** Commits + pushes partial work after EVERY phase (success or failure)
- **Rate-limit detection:** Fast exit (<10 s) + non-zero rc + tiny output → rc=-429

### 1.8 V1 Phase Event Emission (stdout JSON)

V1 emits JSON events to **stdout** (Loki/journald), NOT to `dispatch_v2_events`:
- `phase_start` — phase_num, phase_name, scope, session_id, timeout_s
- `phase_end` — duration_s, rc, rate_limited, session_id
- `phase_skip` — phase_num, deliverable_path, reason
- `branch_resume`, `branch_synced_to_origin`, `git_command_failed`, etc.

**Key gap:** None appear in `dispatch_v2_events`. Dashboard lineage view cannot see per-phase progress.

### 1.9 V1 SIGTERM Handler — Incompatible with V2

V1's `_graceful_shutdown`:
1. Sets `_shutdown_requested` threading.Event (stops phase loop)
2. Commits partial work
3. POSTs to `/api/dispatch/pause/{story_id}` — **v1-only endpoint, absent from v2 API**
4. Exits 143

**V2 already has its own SIGTERM handler** (`_handle_sigterm` in `dispatch_poller_v2.py`,
STORY-857) that kills the SDK subprocess and calls `/api/dispatch/v2/release`.

**Constraint for v2 orchestrator:** Must NOT install a competing SIGTERM handler. Must use
STORY-857's existing `_handle_sigterm` machinery. The phase loop checks `_sigterm_received`
flag (or a passed-in stop-event) to exit gracefully.

---

## 2. V2 Contract — Current State

### 2.1 `_ActiveClaim` Fields

```python
job_id: str           # UUID string
lease_token: str      # UUID string
expires_at: str       # ISO-8601
repo: str
story_id: str
prompt: str
scope: str
rework_of: str | None  # UUID FK → parent job_id (NOT parent story_id string)
claimed_at: float     # monotonic.time()
current_phase: str | None      # used in heartbeat payload
phase_started_at: str | None   # used in heartbeat payload
last_test_status: str | None   # used in heartbeat payload
```

### 2.2 `/claim-next` Response Fields

Returns exactly: `job_id`, `lease_token`, `expires_at`, `repo`, `story_id`, `prompt`, `scope`, `rework_of`

**NOT returned:**
- `metadata` — no such column in `dispatch_jobs`
- `branch` — not pre-computed; must be extracted from prompt or derived
- `correlation_key` — internal only (not exposed to pollers)
- `parent_job_id` — tracked via `rework_of` FK

### 2.3 Morris Dispatch Payload

Morris enqueues via `POST /api/dispatch/v2/enqueue`:
- Initial: `{story_id, repo, scope, prompt, enqueued_by}`
- Redispatch adds: `{parent_job_id, correlation_key, pr_number/branch_name, expected_pr_head_sha, idempotency_key}`

**Critical gap vs AC-3:** The seed's AC-3 references `claim.metadata.rework_of` — **this field does not exist**. What exists:
- `claim.rework_of` = UUID of the parent job_id (direct field)
- Branch name = embedded in `claim.prompt` (regex extraction, STORY-859 pattern)

AC-3's structured resolver must use `claim.rework_of` (UUID) to derive the branch. Resolution path options:
1. Derive branch from `claim.story_id` → `story-{N}/work` convention (matches existing pattern)
2. Look up parent job's prompt from DB to extract branch name
3. Add `branch` field to `dispatch_jobs` table (schema change, highest fidelity)

**Phase 4 must confirm the branch derivation path.**

### 2.4 Event Type Whitelist — CRITICAL FINDING

**The `dispatch_v2_events` table has a CHECK constraint:**
```sql
event_type TEXT NOT NULL CHECK (event_type IN (
    'enqueued', 'leased', 'heartbeat', 'released',
    'needs_info', 'resumed', 'submitted', 'accepted',
    'rejected', 'failed', 'cancelled', 'dead_lettered',
    'quarantined', 'requeued'
))
```

`phase_started`, `phase_completed`, `phase_failed` are **absent**. Any INSERT will fail with a PG constraint violation.

**Python-layer whitelist** in `dispatch_v2_service.py`:
```python
_TRANSITION_ALLOWED: dict[str, set[str]] = {
    "leased": {"heartbeat", "submitted", "released", "needs_info", "failed", "cancelled", ...},
    ...
}
```
Phase event types are not in the `"leased"` set. The `/transition` endpoint returns HTTP 422 for unlisted event_types.

**Trigger `dispatch_state_apply()`** has an explicit ELSE clause:
```sql
ELSE
    RAISE EXCEPTION 'Unknown event_type: %', NEW.event_type;
```

**Conclusion:** A migration IS required — not just an optional index. Full migration scope:
1. Expand the CHECK constraint to include `phase_started`, `phase_completed`, `phase_failed`
2. Update `dispatch_state_apply()` trigger to treat phase events as no-ops (like `heartbeat`)
3. Update `_TRANSITION_ALLOWED["leased"]` in service layer
4. Add index on `(job_id, event_type, (event_data->>'phase'))` for lineage queries

**All changes are additive.** No existing data migrated. No existing event_types changed.

### 2.5 V2 Heartbeat Already Has Phase Fields

The heartbeat endpoint (`POST /api/dispatch/v2/heartbeat`) already accepts:
- `current_phase: str | None`
- `phase_started_at: str | None`
- `last_test_status: str | None`

These are written to `dispatch_leases.heartbeat_data` JSONB. The self-healing watcher already reads `heartbeat_data.phase_started_at` to detect phase overruns.

**Implication:** The v2 orchestrator just needs to update `claim.current_phase` and `claim.phase_started_at` before each heartbeat fires. No new heartbeat infrastructure required.

### 2.6 Lineage API

`GET /api/dispatch/v2/lineage/{job_id}` — **implemented**. Recursive CTE walks ancestor chain via `rework_of` FK, returns `{chain: [{job_id, attempt, state, parent_job_id, redispatched_at}, ...]}`.

Used for resume: given a new claim's `rework_of` UUID, query lineage → find prior job → query that job's `phase_completed` events → determine resume phase.

---

## 3. Failure Classification — Gaps and Extensions

### 3.1 Existing Policy Table (Relevant Entries)

| Class | Retryable | Max | Lane |
|-------|-----------|-----|------|
| `phase_runner_crash` | Yes | 3 | work_queue |
| `git_rebase_failed` | No | 0 | attention_queue |
| `branch_setup_failed` | Yes | 3 | work_queue |
| `workspace_missing` | No | 0 | attention_queue |
| `sdk_died_silent` | Yes | 3 | work_queue |
| `lease_lost` | Yes | 2 | work_queue |
| `sigterm_shutdown` | Yes | 3 | work_queue |
| `code_test_red` | No | 0 | attention_queue |
| `unknown` | No | 0 | attention_queue |

### 3.2 Required New Phase-Scoped Classes (AC-5)

These are **config-layer additions** to `POLICY_TABLE` + `_CLASSIFY_PATTERNS`. The applier `apply()` function handles them automatically — no applier code change needed.

| New Class | Retryable | Max | Lane | Trigger |
|-----------|-----------|-----|------|---------|
| `phase_1_seed_error` | Yes | 2 | work_queue | Phase 1 SDK non-zero exit |
| `phase_4_analysis_error` | Yes | 2 | work_queue | Phase 4 SDK non-zero exit |
| `phase_6_design_error` | Yes | 2 | work_queue | Phase 6 SDK non-zero exit |
| `phase_7_test_red` | No | 0 | attention_queue | Phase 7 exits with red tests (non-retryable — needs human) |
| `phase_8_impl_fail` | Yes | 2 | work_queue | Phase 8 implementation failure |
| `git_branch_setup_failed` | No | 0 | attention_queue | Branch resolution / fetch / checkout failure |
| `git_workspace_dirty` | No | 0 | attention_queue | Pre-phase clean workspace check fails |
| `sdk_timeout_phase_N` | Yes | 1 | work_queue | Phase N hit 2h timeout |

**Classification match:** `classify()` function already runs pattern matching. New patterns added to `_CLASSIFY_PATTERNS` for each new class. Phase-specific failures can be identified by combining SDK output patterns + the phase number context injected by the orchestrator.

---

## 4. Peer-Art Survey

### 4.1 Temporal Workflows — Most Relevant

**Model:** Durable workflow execution via immutable append-only history log. Each activity = phase. On failure+retry, completed activities are replayed from history (results memoized — not re-executed).

**SDLC phases are non-deterministic** (Claude outputs vary). Do NOT replay phases. Instead, use the event log as a skip-list: `SELECT MAX(event_data->>'phase') FROM dispatch_v2_events WHERE job_id=$1 AND event_type='phase_completed'` → skip all phases ≤ that number.

**Key lesson:** "Event log as source of truth" is already the v2 architecture. Resume = scan events, not replay.

### 4.2 Argo Workflows

**Model:** K8s-native DAG. Each step has independent retry strategy (max attempts, backoff). Step state in CRD.

**Key lesson:** Per-step failure classification (this story's AC-5) is standard practice, not over-engineering. Phase 7 failures (test_red) are naturally non-retryable; Phase 1 failures may be retryable. Granular retry policy at step/phase level prevents the "burn 3 retries on a deterministic failure" problem that cluster-1 exposed.

### 4.3 Sidekiq Pro Batches

**Model:** Redis batch counters. Resume = replay entire job sequence.

**Low relevance:** Sequential phase dependency (each phase's output feeds the next) doesn't map to parallel batch jobs. Job-level granularity is wrong for a 10-phase SDLC run.

### 4.4 Python Celery Chains

**Low relevance:** `celery.chain` is the closest analogy but lacks lease tokens, per-phase resume, and heartbeat protocol. Adding them would require Redis + complex state management exceeding what the current events table already provides.

**Verdict from peer-art:** Approach B (explicit poller-side state machine, per-phase events to `dispatch_v2_events`) is the standard pattern across distributed orchestration systems. No external library needed.

---

## 5. Approach Preview (For Phase 3 Expansion)

### Approach A — Lift v1 wholesale

Import/call `run_sdlc_phases()` from v2 poller. Minimal refactor.
- **Pros:** Fast. Proven. All v1 logic (branch, rework, heartbeat) intact.
- **Cons:** No per-phase events. SIGTERM conflict (v1 installs own handler). Heartbeat POSTs to v1 endpoint. Resume is deliverable-based (VM-local only). Many v1-only conventions leak in.
- **Verdict:** Fast-path but operationally blind. Not the long-term answer.

### Approach B — New v2_phase_runner module

Poller orchestrates each phase explicitly:
`phase_started` event → SDK call → `phase_completed/failed` event → loop.
Resume via events query. Branch resolver replaces regex pre-step.
- **Pros:** Full per-phase observability. VM-portable resume. Phase-scoped failures. Clean v2 contract. SIGTERM natural.
- **Cons:** Larger write surface. Migration required. Phase SDK prompt strategy needs specification.
- **Verdict:** Correct long-term architecture.

### Approach C — Hybrid wrapper

Call `run_sdlc_phases()` but parse stdout to synthesize retroactive phase events. SDK writes `phases_completed.json` state file for resume.
- **Pros:** Fewer changes to SDK path.
- **Cons:** Events are retroactive (no real-time phase visibility). State file coupling fragile. SIGTERM conflict still present. Incomplete events if SDK crashes mid-phase.
- **Verdict:** Viable bridge only if timeline is <48 h; not recommended for 860.

---

## 6. Open Questions (Carried to Phase 4)

| # | Question | Seed Default | Research Verdict |
|---|----------|--------------|-----------------|
| Q1 | Approach A/B/C? | B (new v2_phase_runner) | B confirmed by peer-art; A has SIGTERM conflict + blind lineage |
| Q2 | Resume: deliverable-based vs events-table? | Events table | Events table is VM-portable; deliverable-based breaks cross-VM |
| Q3 | Migration scope — just index or also CHECK + trigger + Python whitelist? | "Just event_type strings + optional index" | **All four required.** Migration 059 is mandatory, not optional |
| Q4 | Branch source when `claim.metadata.branch` absent? | `claim.metadata` first | **`claim.metadata` doesn't exist.** Options: derive from `story_id` convention, look up parent job prompt, or add `branch` column to `dispatch_jobs` |
| Q5 | `rework_of` is UUID — does orchestrator need to resolve to a story_id? | Not specified | Branch derivation may require story_id → branch mapping. Phase 4 must confirm |
| Q6 | All 10 phases or just 5 key checkpoints for events? | 5 (1,4,6,7,8) | 500 extra rows/day — trivial. Phase 4 decides if all 10 worth it |
| Q7 | Keep 859 regex fallback or delete entirely? | Keep as narrow fallback | Keep as last-resort safety net for claims where branch can't be resolved via rework_of |
| Q8 | Single heartbeat thread or per-phase restart? | Single thread | Single thread confirmed — v2 heartbeat endpoint already accepts phase metadata |
| Q9 | `git_workspace_dirty` — auto-clean (`git reset --hard`) or fail-closed? | Fail-closed | Fail-closed is safer (matches seed boundaries); Phase 4 confirms |

---

## 7. Dependency Health Check

| Dependency | Status | Notes |
|------------|--------|-------|
| STORY-857 (SIGTERM drain + lease write) | **Merged** | `_handle_sigterm` + `_write_active_lease` in poller. Orchestrator must NOT install competing handler. |
| STORY-857a (typed failures + 4KB capture) | **Merged** | `_failure_event_data()` expanded. Phase-scoped classes extend via config. |
| STORY-859 (surgical rebase pre-step) | **Merged** | Superseded by 860. Tests ported per AC-8. The `_is_rebase_prompt` block in poll_loop is replaced by structured branch resolver. |
| PostgreSQL + asyncpg/requests | In use | No new deps. |
| `dispatch_v2_events` schema | Production | Migration 059 required before phase events can be emitted. |
| Morris dispatch skill | In use | Morris already emits `rework_of` UUID via dispatch_jobs FK. No Morris-side changes expected. |

---

## 8. Files Confirmed for Phase 6 Design

| File | Action | Rationale |
|------|--------|-----------|
| `deployment/hermes/dispatch_poller_v2.py` | Edit | Remove 859 pre-step; call orchestrator |
| `tech_dev_agents/orchestration/v2_phase_runner.py` | **Create** | Core phase loop + phase events |
| `tech_dev_agents/orchestration/git_ops.py` | **Create** | Structured git helper, typed exceptions |
| `tech_dev_agents/orchestration/branch_resolver.py` | **Create** | Branch/rework/target-PR from claim fields + prompt |
| `tech_dev_agents/ops_console/services/dispatch_v2_service.py` | Edit | Add phase events to `_TRANSITION_ALLOWED["leased"]` |
| `tech_dev_agents/ops_console/services/dispatch_failure_policy.py` | Edit | Add phase-scoped classes to POLICY_TABLE + _CLASSIFY_PATTERNS |
| `scripts/migrations/059_phase_event_types.sql` | **Create** | Expand CHECK constraint; update trigger (no-op for phase_*); add index |
| `tests/deployment/test_phase_runner_orchestration_860.py` | **Create** | Full suite incl. 859 ported tests |
| `tests/deployment/test_branch_lifecycle.py` | **Create** | Real git fixture integration tests |
| `tests/deployment/test_resume_aware_retry.py` | **Create** | Resume-from-phase-N |
| `tests/deployment/test_default_branch_detection.py` | **Create** | 3-variant branch detection |

Files NOT to modify: `sdlc_phase_runner.py`, `dispatch_failure_policy.py` applier logic,
`dispatch_v2_events` table DDL (only additive event_types), `claude_sdk_tool.py`, Morris skills.
