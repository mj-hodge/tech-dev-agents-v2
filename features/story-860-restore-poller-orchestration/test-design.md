# STORY-860 Phase 7 — Test Design

## Test File: `tests/deployment/test_poller_v2_orchestration.py`

14 tests covering all 13 ACs. Tests are RED before Phase 8 implementation.

### Test Structure

```
tests/deployment/test_poller_v2_orchestration.py
  ├── Fixtures
  │   ├── _claim()                    — builds _ActiveClaim with all new fields
  │   ├── git_fixture(tmp_path)       — real git repo with origin remote
  │   └── mock_transition()           — captures transition_claim calls
  │
  ├── Branch Lifecycle (AC-1)
  │   └── test_branch_lifecycle_creates_branch_from_claim_metadata
  │
  ├── Default Branch Detection (AC-2)
  │   ├── test_default_branch_main_repo
  │   ├── test_default_branch_master_repo
  │   └── test_default_branch_custom_repo
  │
  ├── Rework Threading (AC-3)
  │   └── test_rework_threading_metadata_path
  │
  ├── Phase Events (AC-4, AC-10)
  │   └── test_phase_events_emitted_for_all_checkpoints
  │
  ├── Phase-Scoped Failure Classes (AC-5)
  │   └── test_phase_scoped_failure_classes
  │
  ├── SIGTERM (AC-6)
  │   └── test_sigterm_during_phase_releases_lease_cleanly
  │
  ├── Resume (AC-7)
  │   └── test_resume_from_failed_phase
  │
  ├── 859 Supersession (AC-8)
  │   ├── test_859_pattern_rebase_only_via_structured_resolver
  │   ├── test_859_pattern_rework_of_via_structured_resolver
  │   └── test_859_pattern_fix_pr_n_via_structured_resolver
  │
  ├── Heartbeat Continuity (AC-11)
  │   └── test_heartbeat_continuity_during_phase_transitions
  │
  └── Backward Compat (AC-13)
      └── test_backward_compat_single_shot_happy_path
```

### Test Specifications

---

#### 1. `test_branch_lifecycle_creates_branch_from_claim_metadata` (AC-1)

**Setup:** Create a real git fixture with a bare origin and a local clone. Push a commit to `main`. Create a claim with `branch="story-860/story-860"`.

**Action:** Call `git_ops.ensure_branch(workdir, "story-860/story-860", "main")`.

**Assert:**
- Current branch is `story-860/story-860`.
- Working tree is clean (`git status --porcelain` is empty).
- Branch tracks origin or was created from `origin/main`.

---

#### 2. `test_default_branch_main_repo` (AC-2)

**Setup:** Git fixture with `HEAD -> refs/remotes/origin/HEAD -> refs/remotes/origin/main`.

**Action:** Call `git_ops.resolve_default_branch(workdir)`.

**Assert:** Returns `"main"`.

---

#### 3. `test_default_branch_master_repo` (AC-2)

**Setup:** Git fixture with `HEAD -> refs/remotes/origin/HEAD -> refs/remotes/origin/master`.

**Action:** Call `git_ops.resolve_default_branch(workdir)`.

**Assert:** Returns `"master"`.

---

#### 4. `test_default_branch_custom_repo` (AC-2)

**Setup:** Git fixture with `HEAD -> refs/remotes/origin/HEAD -> refs/remotes/origin/develop`.

**Action:** Call `git_ops.resolve_default_branch(workdir)`.

**Assert:** Returns `"develop"`.

---

#### 5. `test_rework_threading_metadata_path` (AC-3)

**Setup:** Claim with `rework_of="STORY-500"`, `branch="story-500/story-500"`, `target_pr="123"`.

**Action:** Call `branch_resolver.resolve(claim, workdir, "main")`.

**Assert:**
- Returns `"story-500/story-500"` (from claim.branch, not derived from claim.story_id).
- Does NOT fall back to regex parsing of prompt.

---

#### 6. `test_phase_events_emitted_for_all_checkpoints` (AC-4, AC-10)

**Setup:** Claim with scope="medium" (phases 1,4,6,7,8). Mock `_run_sdk_for_phase` to return `(True, "ok")` for all phases. Mock `transition_claim` to capture calls.

**Action:** Call `run_orchestrated(claim, session, headers)`.

**Assert:**
- `transition_claim` called with `event_type="phase_started"` 5 times (phases 1,4,6,7,8).
- `transition_claim` called with `event_type="phase_completed"` 5 times.
- Each phase event has `event_data["phase"]` set to the correct phase number.
- Each `phase_completed` event has `event_data["duration_s"]` > 0.
- Final call is `event_type="submitted"`.

---

#### 7. `test_phase_scoped_failure_classes` (AC-5)

**Setup:** Claim with scope="small" (phases 1,7,8). Mock `_run_sdk_for_phase` to return `(False, "test assertion error")` on phase 7.

**Action:** Call `run_orchestrated(claim, session, headers)`.

**Assert:**
- `transition_claim` called with `event_type="phase_failed"` for phase 7.
- `event_data["failure_class"]` is `"phase_7_test_red"`.
- Terminal `event_type="failed"` also has `failure_class="phase_7_test_red"`.
- Phases 1 completed; phase 8 never started.

---

#### 8. `test_sigterm_during_phase_releases_lease_cleanly` (AC-6)

**Setup:** Claim with scope="medium". Mock `_run_sdk_for_phase` to set `_sigterm_received = True` during phase 4 execution (simulating SIGTERM mid-phase).

**Action:** Call `run_orchestrated(claim, session, headers)`.

**Assert:**
- Phase 1 completed normally.
- Phase 4 started.
- After phase 4 SDK returns, the loop exits (does NOT proceed to phase 6).
- No `phase_completed` event for phase 4 if SDK returned failure.
- Terminal `failed` event emitted with appropriate class.

---

#### 9. `test_resume_from_failed_phase` (AC-7)

**Setup:** Claim with `parent_job_id="parent-123"`. Mock the event query to return `phase_failed(phase=4)` for the parent job. Scope="medium" (phases 1,4,6,7,8).

**Action:** Call `_determine_resume_phase(claim, session, headers)`.

**Assert:**
- Returns `4` (resume from the failed phase, not phase 1).
- When `run_orchestrated` is called, it skips phase 1 and starts at phase 4.

---

#### 10. `test_859_pattern_rebase_only_via_structured_resolver` (AC-8)

**Setup:** Claim with `branch=None`, prompt containing "Rebase only". No claim.metadata.branch.

**Action:** Call `branch_resolver.resolve(claim, workdir, "main")`.

**Assert:**
- Returns a valid branch (current branch or default).
- Resolution came via the regex fallback path (logged).

---

#### 11. `test_859_pattern_rework_of_via_structured_resolver` (AC-8)

**Setup:** Claim with `rework_of="STORY-500"`, `branch=None`.

**Action:** Call `branch_resolver.resolve(claim, workdir, "main")`.

**Assert:**
- Returns `"story-500/story-500"` (derived from rework_of).
- Does NOT require regex parsing of prompt.

---

#### 12. `test_859_pattern_fix_pr_n_via_structured_resolver` (AC-8)

**Setup:** Claim with `branch=None`, `target_pr="456"`, prompt containing "Fix PR #456".

**Action:** Call `branch_resolver.resolve(claim, workdir, "main")`.

**Assert:**
- Resolution uses target_pr metadata if branch is available from PR context.
- Falls back to regex if no structured data available.

---

#### 13. `test_heartbeat_continuity_during_phase_transitions` (AC-11)

**Setup:** Claim with scope="small" (phases 1,7,8). Mock `send_heartbeat` to record call timestamps. Mock `_run_sdk_for_phase` to take ~0.5s per phase (via `time.sleep`).

**Action:** Call `run_orchestrated(claim, session, headers)` with `heartbeat_interval=0.2`.

**Assert:**
- `send_heartbeat` called at least 3 times during the ~1.5s run.
- No gap between heartbeat calls exceeds 2x the interval.
- Heartbeat fires across phase boundaries (not just within a single phase).

---

#### 14. `test_backward_compat_single_shot_happy_path` (AC-13)

**Setup:** Claim with scope="small", no rework_of, no branch, simple prompt. Mock SDK to return `(True, "done")` for all phases.

**Action:** Call `run_orchestrated(claim, session, headers)`.

**Assert:**
- All 3 phases (1,7,8) complete normally.
- Phase events + terminal `submitted` event emitted.
- No errors logged.
- Total event count = 3 × (started + completed) + 1 (submitted) = 7.
