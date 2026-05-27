# STORY-860 Phase 6 — Feature Specification

## 1. Architecture Overview

```
dispatch_poller_v2.py::poll_loop()
  │
  ├─ claim_next() → _ActiveClaim (extended with branch, parent_job_id, etc.)
  ├─ _write_active_lease()
  │
  ├─ if DISPATCH_V2_ORCHESTRATION == "1":
  │     run_orchestrated(claim, session, headers)
  │       │
  │       ├─ _start_heartbeat_thread(claim, session, headers)
  │       ├─ git_ops.resolve_default_branch(workspace)
  │       ├─ branch_resolver.resolve(claim, workspace, default_branch)
  │       ├─ git_ops.ensure_branch(workspace, branch, default_branch)
  │       ├─ git_ops.verify_clean_tree(workspace)
  │       ├─ _determine_resume_phase(claim, session, headers)
  │       ├─ for phase in phases[resume_idx:]:
  │       │     if _sigterm_received: break
  │       │     emit phase_started
  │       │     claim.current_phase = phase_num
  │       │     success, output = _run_sdk_for_phase(claim, phase)
  │       │     emit phase_completed / phase_failed
  │       │     if not success: break
  │       ├─ emit submitted / failed (terminal)
  │       └─ _stop_heartbeat_thread()
  │
  ├─ else:  # feature flag off — legacy single-shot path
  │     success, output = _run_sdk(claim)
  │     transition submitted / failed
  │
  ├─ _clear_active_lease()
  └─ sleep(1)
```

## 2. Module Design

### 2.1 `tech_dev_agents/orchestration/__init__.py`

Empty package init.

### 2.2 `tech_dev_agents/orchestration/git_ops.py`

```python
"""Git operations for v2 orchestration with typed exceptions."""

class GitOpsError(RuntimeError):
    """Base exception for git operations."""
    failure_class: str = "unknown"

class GitBranchSetupError(GitOpsError):
    failure_class = "git_branch_setup_failed"

class GitRebaseError(GitOpsError):
    failure_class = "git_rebase_failed"

class GitWorkspaceDirtyError(GitOpsError):
    failure_class = "git_workspace_dirty"

class DefaultBranchUnresolvableError(GitOpsError):
    failure_class = "git_branch_setup_failed"

def resolve_default_branch(workdir: str) -> str:
    """3-step fallback: symbolic-ref → ls-remote main → ls-remote master.
    Raises DefaultBranchUnresolvableError on failure."""

def ensure_branch(workdir: str, branch: str, default_branch: str) -> None:
    """Fetch + checkout branch. If branch doesn't exist remotely, create from default.
    Stashes dirty tree before checkout. Raises GitBranchSetupError on failure."""

def verify_clean_tree(workdir: str) -> None:
    """git status --porcelain. Raises GitWorkspaceDirtyError if not clean."""

def fetch_origin(workdir: str, ref: str | None = None) -> None:
    """git fetch origin [ref]. Raises GitBranchSetupError on failure."""

BRANCH_NAME_PATTERN = re.compile(r'^[A-Za-z0-9_/.\-]+$')

def validate_branch_name(branch: str) -> bool:
    """Validate branch name against safe pattern. Security constraint."""
```

### 2.3 `tech_dev_agents/orchestration/branch_resolver.py`

```python
"""Resolve the target branch for a v2 claim."""

def resolve(
    claim: "_ActiveClaim",
    workdir: str,
    default_branch: str,
) -> str:
    """Priority chain:
    1. claim.branch (from claim.metadata or top-level field)
    2. Seed ## Target Branch override (parsed from seed.md)
    3. story-{num}/{story_id} default
    4. 859 regex fallback (Rework of / Rebase only / PR #N)

    All returned branches are validated against BRANCH_NAME_PATTERN.
    """

def _parse_target_branch_from_seed(workdir: str, story_id: str) -> str | None:
    """Read seed.md, extract ## Target Branch."""

def _extract_branch_from_prompt(prompt: str) -> str | None:
    """859-compat regex fallback. Three patterns:
    - 'Rework of STORY-X' → story-x/story-x
    - 'Rebase only' → current branch (no change)
    - 'PR #N rework' → extract from PR metadata (best-effort)
    """
```

### 2.4 `tech_dev_agents/orchestration/phase_defs.py`

Lifted from `sdlc_phase_runner.py`:

```python
"""Phase definitions and path parsing for v2 orchestration."""

PHASES_SMALL = [...]   # Identical to v1
PHASES_MEDIUM = [...]
PHASES_LARGE = [...]
PHASES_RESEARCH = [...]

PHASE_MAP = {
    "small": PHASES_SMALL,
    "medium": PHASES_MEDIUM,
    "large": PHASES_LARGE,
    "research": PHASES_RESEARCH,
}

def parse_seed_phase_path(seed_text: str) -> list[int] | None:
    """Extract ordered phase numbers from seed's Phase Path declaration."""

def get_phases_for_scope(scope: str) -> list[tuple]:
    """Return the phase list for a scope."""

def build_phase_prompt(
    phase_num: int, prompt_template: str,
    story_id: str, repo: str, story_folder: str,
    original_prompt: str | None = None,
) -> str:
    """Build the prompt for a specific phase."""
```

### 2.5 `tech_dev_agents/orchestration/v2_orchestrator.py`

```python
"""V2 poller orchestration — phase loop with v2 lease/event contract."""

def run_orchestrated(
    claim: "_ActiveClaim",
    session: "requests.Session",
    headers: dict,
) -> None:
    """Run the full SDLC phase sequence for a claimed job.

    Emits phase_started/completed/failed events via transition_claim().
    Emits terminal submitted/failed event.
    Manages heartbeat thread lifecycle.
    Checks _sigterm_received between phases.
    """

def _start_heartbeat_thread(
    claim, session, headers,
    interval: int = 60,
) -> tuple[threading.Thread, threading.Event]:
    """Start daemon thread that calls send_heartbeat() every interval seconds.
    Returns (thread, stop_event)."""

def _determine_resume_phase(
    claim, session, headers,
) -> int | None:
    """Query parent job's phase events to find resume point.
    Returns phase number to resume from, or None (start from beginning).

    Logic:
    - If claim has no parent_job_id: return None (fresh start)
    - Query last phase event for parent_job_id
    - phase_completed(N) → resume from N+1
    - phase_failed(N) or phase_started(N) with no complete → resume from N
    - Ambiguous → return None (conservative: restart from phase 1)
    """

def _emit_phase_event(
    claim, session, headers,
    event_type: str,  # "phase_started" | "phase_completed" | "phase_failed"
    phase_num: int,
    phase_name: str,
    **kwargs,
) -> None:
    """Emit a phase event via transition_claim(). Best-effort — swallows errors."""

def _run_sdk_for_phase(
    claim, phase_num, phase_name, prompt, workspace, max_turns,
) -> tuple[bool, str]:
    """Launch SDK for a single phase. Returns (success, output).
    Updates _active_sdk_proc for SIGTERM handler."""

def _build_phase_failure_class(phase_num: int, sdk_output: str) -> str:
    """Determine the phase-scoped failure class.
    Checks inner classifier first; wraps with phase prefix."""

def _extract_story_folder(story_id: str, workdir: str, rework_of: str | None = None) -> str:
    """Find the features/ subfolder for this story."""
```

### 2.6 `dispatch_poller_v2.py` Changes

```python
# Extend _ActiveClaim.__init__:
class _ActiveClaim:
    def __init__(self, ...,
                 branch: str | None = None,
                 target_pr: str | None = None,
                 correlation_key: str | None = None,
                 parent_job_id: str | None = None):
        ...
        self.branch = branch
        self.target_pr = target_pr
        self.correlation_key = correlation_key
        self.parent_job_id = parent_job_id

# In claim_next(), parse new fields:
return _ActiveClaim(
    ...,
    branch=data.get("branch"),
    target_pr=data.get("target_pr"),
    correlation_key=data.get("correlation_key"),
    parent_job_id=data.get("parent_job_id"),
)

# In poll_loop(), feature-flag gate:
ORCHESTRATION_ENABLED = os.environ.get("DISPATCH_V2_ORCHESTRATION", "0") == "1"

if ORCHESTRATION_ENABLED:
    from tech_dev_agents.orchestration.v2_orchestrator import run_orchestrated
    run_orchestrated(claim, session, headers)
else:
    success, output = _run_sdk(claim)
    # ... existing submitted/failed logic
```

## 3. Database Migration

### `scripts/migrations/059_phase_event_types.sql`

```sql
-- STORY-860: Add phase event types to dispatch_v2_events CHECK constraint
-- and update the state trigger to treat them as informational (no state change).

-- Step 1: Drop and recreate the CHECK constraint with new values
ALTER TABLE dispatch_v2_events
    DROP CONSTRAINT IF EXISTS dispatch_v2_events_event_type_check;

ALTER TABLE dispatch_v2_events
    ADD CONSTRAINT dispatch_v2_events_event_type_check
    CHECK (event_type IN (
        'enqueued', 'leased', 'heartbeat', 'released',
        'needs_info', 'resumed', 'submitted', 'accepted',
        'rejected', 'failed', 'cancelled', 'dead_lettered',
        'quarantined', 'requeued',
        -- STORY-860: phase orchestration events (informational, no state change)
        'phase_started', 'phase_completed', 'phase_failed'
    ));

-- Step 2: Update the state trigger to pass-through phase events (like heartbeat)
CREATE OR REPLACE FUNCTION dispatch_state_apply() RETURNS TRIGGER AS $$
DECLARE
    v_state         TEXT;
    v_lane          TEXT;
    v_leased_by     TEXT;
    v_leased_at     TIMESTAMPTZ;
    v_needs_info    TEXT;
    v_fail_class    TEXT;
    v_kind          TEXT;
BEGIN
    -- heartbeat + phase events: informational only, no state/lane change
    IF NEW.event_type IN ('heartbeat', 'phase_started', 'phase_completed', 'phase_failed') THEN
        RETURN NEW;
    END IF;

    v_leased_by  := NULL;
    v_leased_at  := NULL;
    v_needs_info := NULL;
    v_fail_class := NULL;

    CASE NEW.event_type
        WHEN 'enqueued' THEN
            v_state := 'pending';    v_lane := 'work_queue';
        WHEN 'leased' THEN
            v_state := 'leased';     v_lane := 'in_progress';
            v_leased_by := NEW.event_data->>'agent';
            SELECT leased_at INTO v_leased_at FROM dispatch_leases WHERE job_id = NEW.job_id;
            IF v_leased_at IS NULL THEN v_leased_at := now(); END IF;
        WHEN 'released' THEN
            v_state := 'pending';    v_lane := 'work_queue';
        WHEN 'submitted' THEN
            v_state := 'in_review';  v_lane := 'in_review';
        WHEN 'accepted' THEN
            v_state := 'completed';  v_lane := 'terminal';
        WHEN 'rejected' THEN
            v_state := 'pending';    v_lane := 'work_queue';
        WHEN 'needs_info' THEN
            v_state := 'needs_info';
            v_kind := NEW.event_data->>'kind';
            v_needs_info := COALESCE(v_kind, 'question');
            IF v_kind = 'attention' THEN v_lane := 'attention_queue';
            ELSE v_lane := 'human_queue'; END IF;
        WHEN 'resumed' THEN
            v_state := 'leased';     v_lane := 'in_progress';
            v_leased_by := NEW.event_data->>'agent';
            SELECT leased_at INTO v_leased_at FROM dispatch_leases WHERE job_id = NEW.job_id;
            IF v_leased_at IS NULL THEN v_leased_at := now(); END IF;
        WHEN 'failed' THEN
            v_state := 'failed';     v_lane := 'attention_queue';
            v_fail_class := NEW.event_data->>'failure_class';
        WHEN 'cancelled' THEN
            v_state := 'cancelled';  v_lane := 'terminal';
        WHEN 'dead_lettered' THEN
            v_state := 'dead_letter'; v_lane := 'terminal';
        WHEN 'quarantined' THEN
            v_state := 'quarantined'; v_lane := 'quarantined';
        WHEN 'requeued' THEN
            v_state := 'pending';    v_lane := 'work_queue';
        ELSE
            RAISE EXCEPTION 'Unknown event_type: %', NEW.event_type;
    END CASE;

    INSERT INTO dispatch_state_current
        (job_id, state, lane, last_event_id, updated_at,
         leased_by, leased_at, needs_info_kind, failure_class)
    VALUES
        (NEW.job_id, v_state, v_lane, NEW.event_id, now(),
         v_leased_by, v_leased_at, v_needs_info, v_fail_class)
    ON CONFLICT (job_id) DO UPDATE SET
        state           = EXCLUDED.state,
        lane            = EXCLUDED.lane,
        last_event_id   = EXCLUDED.last_event_id,
        updated_at      = EXCLUDED.updated_at,
        leased_by       = EXCLUDED.leased_by,
        leased_at       = EXCLUDED.leased_at,
        needs_info_kind = EXCLUDED.needs_info_kind,
        failure_class   = EXCLUDED.failure_class;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Step 3: Index for resume queries (AC-10)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_dispatch_v2_events_phase
    ON dispatch_v2_events(job_id, event_type, (event_data->>'phase'))
    WHERE event_type IN ('phase_started', 'phase_completed', 'phase_failed');
```

## 4. Failure Policy Extension

### New POLICY_TABLE entries in `dispatch_failure_policy.py`

```python
# STORY-860: Phase-scoped failure classes
"phase_1_seed_error":       {"retryable": True,  "max_attempts": 2, "cooldown_sec": 60,  "next_lane": "work_queue"},
"phase_2_research_error":   {"retryable": True,  "max_attempts": 2, "cooldown_sec": 60,  "next_lane": "work_queue"},
"phase_3_expansion_error":  {"retryable": True,  "max_attempts": 2, "cooldown_sec": 60,  "next_lane": "work_queue"},
"phase_4_analysis_error":   {"retryable": True,  "max_attempts": 2, "cooldown_sec": 60,  "next_lane": "work_queue"},
"phase_5_selection_error":  {"retryable": True,  "max_attempts": 2, "cooldown_sec": 60,  "next_lane": "work_queue"},
"phase_6_design_error":     {"retryable": True,  "max_attempts": 2, "cooldown_sec": 60,  "next_lane": "work_queue"},
"phase_7_test_red":         {"retryable": False, "max_attempts": 0, "cooldown_sec": 0,   "next_lane": "attention_queue"},
"phase_8_impl_fail":        {"retryable": True,  "max_attempts": 2, "cooldown_sec": 60,  "next_lane": "work_queue"},
"phase_9_refinement_error": {"retryable": True,  "max_attempts": 2, "cooldown_sec": 60,  "next_lane": "work_queue"},
"phase_10_ops_error":       {"retryable": True,  "max_attempts": 2, "cooldown_sec": 60,  "next_lane": "work_queue"},
"git_branch_setup_failed":  {"retryable": True,  "max_attempts": 3, "cooldown_sec": 60,  "next_lane": "work_queue"},
"git_rebase_failed":        {"retryable": True,  "max_attempts": 2, "cooldown_sec": 60,  "next_lane": "work_queue"},
"git_workspace_dirty":      {"retryable": False, "max_attempts": 0, "cooldown_sec": 0,   "next_lane": "attention_queue"},
```

Note: `phase_7_test_red` is non-retryable — test failures need human review. All other phase errors are retryable with 2 attempts + 60s cooldown.

## 5. _ActiveClaim Extension

New fields added to `_ActiveClaim.__init__()` with backward-compatible defaults:

| Field | Type | Source | Default |
|-------|------|--------|---------|
| `branch` | `str \| None` | `data.get("branch")` or `data.get("metadata", {}).get("branch")` | `None` |
| `target_pr` | `str \| None` | `data.get("target_pr")` or `data.get("metadata", {}).get("target_pr")` | `None` |
| `correlation_key` | `str \| None` | `data.get("correlation_key")` | `None` |
| `parent_job_id` | `str \| None` | `data.get("parent_job_id")` | `None` |

## 6. Feature Flag

```python
# In dispatch_poller_v2.py
ORCHESTRATION_ENABLED = os.environ.get("DISPATCH_V2_ORCHESTRATION", "0") == "1"
```

- Default: `"0"` (off) — legacy single-shot path.
- Set `DISPATCH_V2_ORCHESTRATION=1` on agent VMs to enable orchestration.
- Rollback: unset the env var and `push-code.sh`.

## 7. Heartbeat Thread Design

```python
def _heartbeat_loop(claim, session, headers, stop_event, interval=60):
    """Daemon thread target. Fires send_heartbeat() every interval seconds."""
    while not stop_event.wait(timeout=interval):
        try:
            workspace = _resolve_workspace(claim.repo)
            sha = _get_current_git_sha(workspace)
            ok = send_heartbeat(claim, session=session, headers=headers, git_head_sha=sha)
            if not ok:
                # Stale lease (409) — set flag for main thread
                logger.warning("[ORCH] heartbeat stale lease for job=%s", claim.job_id)
                claim._lease_lost = True
                break
        except Exception as exc:
            logger.warning("[ORCH] heartbeat error: %s", exc)
```

The main phase loop checks `claim._lease_lost` between phases and exits if True.

## 8. Canary Set

Selected from Cluster 1 (triage-2026-05-04.md) to span failure shapes:

| # | Story | Repo | Shape | Why Selected |
|---|-------|------|-------|-------------|
| 1 | STORY-839 | advertising-amazon | PR rework | Fix-PR dispatch with existing branch |
| 2 | STORY-844 | tech-dev-agents | PR rebase | Rebase onto updated main |
| 3 | STORY-845 | tech-dev-agents | Rework-of | Rework chain with metadata |
| 4 | STORY-847 | tech-dev-agents | Fix-PR rework | Playwright fix with branch context |
| 5 | STORY-854 | advertising-amazon | Large multi-PR rebase | Complex branch lifecycle |

These 5 stories span both repos, all failure shapes from Cluster 1, and include both simple (single-PR) and complex (multi-PR) cases.

## 9. Logging Contract (AC-12)

```
[ORCH] phase=1 name=Seed action=start job=abc123
[ORCH] phase=1 name=Seed action=complete duration_s=45 job=abc123
[ORCH] phase=7 name=Test_Design action=fail duration_s=12 failure_class=phase_7_test_red job=abc123
[ORCH] branch=story-860/story-860 action=checkout source=claim.metadata job=abc123
[ORCH] resume=phase_4 parent_job=def456 job=abc123
[ORCH] heartbeat=ok lease_expires=2026-05-04T13:00:00Z job=abc123
```

All lines use structured key=value pairs for Loki query compatibility.

## 10. Implementation Plan

| Step | File | Description | Est. Lines |
|------|------|-------------|-----------|
| 1 | `tech_dev_agents/orchestration/__init__.py` | Package init | 5 |
| 2 | `tech_dev_agents/orchestration/phase_defs.py` | Lift phase maps + path parser from v1 | 120 |
| 3 | `tech_dev_agents/orchestration/git_ops.py` | Git operations with typed exceptions | 150 |
| 4 | `tech_dev_agents/orchestration/branch_resolver.py` | Branch resolution chain | 80 |
| 5 | `tech_dev_agents/orchestration/v2_orchestrator.py` | Main orchestration function + helpers | 300 |
| 6 | `dispatch_poller_v2.py` | Extend _ActiveClaim, add feature flag, integrate orchestrator | 80 |
| 7 | `dispatch_failure_policy.py` | Add phase-scoped classes to POLICY_TABLE | 30 |
| 8 | `scripts/migrations/059_phase_event_types.sql` | CHECK constraint + trigger + index | 80 |
| 9 | Tests (Phase 7) | 3 test files, ~14 tests | 400 |
| **Total** | | | **~1245** |
