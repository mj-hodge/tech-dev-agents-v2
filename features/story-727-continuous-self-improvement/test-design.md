# STORY-727 — Test Design: Continuous Self-Improvement Loop

**Phase:** 7 — Test Design
**Scope:** Medium
**Branch:** `story-727/continuous-self-improvement`
**Date:** 2026-04-26
**State at end of phase:** RED (all 10 tests failing — modules not yet implemented)

---

## 1. Test File Location

Per the spec's §9 Module Layout:

```
tests/deployment/test_self_improvement_727.py   ← single consolidated file (Phase 7)
```

The spec targets `tests/morris/improvement/` as the final split layout for Phase 8. For Phase 7 RED state we use a single consolidated file under `tests/deployment/` in line with the project's existing convention for story-scoped RED test files.

---

## 2. Modules Under Test

All five modules are NEW — they do not exist at end of Phase 7. Import attempts produce `ModuleNotFoundError`, confirming RED state.

| Module | Path | Role |
|--------|------|------|
| `pattern_detector` | `deployment/morris/scripts/improvement/pattern_detector.py` | Aggregates adversarial findings + DB failure reasons; emits `Pattern` objects above threshold |
| `proposal_generator` | `deployment/morris/scripts/improvement/proposal_generator.py` | Converts a `Pattern` into a unified-diff `Proposal`; raises `ProposalDiffInvalid` on bad diffs |
| `approval_handler` | `deployment/morris/scripts/improvement/approval_handler.py` | Posts Teams DMs; routes approve/reject; applies Tier-1 diffs or opens Tier-2 PRs |
| `tracker` | `deployment/morris/scripts/improvement/tracker.py` | Check-back: computes `expected_metric` 14 days after apply; inserts `improvement_tracking` rows |
| `retrospective` | `deployment/morris/scripts/improvement/retrospective.py` | Monday briefing DM: top-3 patterns, recent decisions, tracking measurements, pending proposals |

---

## 3. Test Criteria Map (T1–T10)

### T1 — Pattern detector fires on ≥ 3 CRITICAL findings within 30 days

**Module:** `pattern_detector`
**Function:** `detect_patterns(repo_root, config)` (or equivalent entry point)

The test constructs a temporary fixture directory with 4 `adversarial-review.md` files:
- 3 files each contain a `[CRITICAL] static-test-masquerading-as-behavioral` finding dated within the last 30 days.
- 1 file contains a different finding type (`spec-requirement-omitted`) — must NOT contribute to the static-masquerading count.

**Assert:** The returned list includes a `Pattern` with:
- `key == "adversarial.static_test_masquerading_as_behavioral"`
- `count == 3`
- `story_ids` list of length 3 (the three contributing story folders)

The 4th file's story folder is absent from `story_ids`.

**Config override:** `threshold_critical = 3` (default; no override needed).

---

### T2 — Pattern detector does NOT fire when only 2 CRITICAL findings (below threshold)

**Module:** `pattern_detector`
**Function:** `detect_patterns(repo_root, config)`

Fixture: 2 files with `[CRITICAL] static-test-masquerading-as-behavioral` (both within 30 days). Threshold is 3.

**Assert:**
- The returned list contains NO pattern with `key == "adversarial.static_test_masquerading_as_behavioral"`.
- The pattern IS logged at DEBUG level (verified via `caplog` or mock logger).
- No row is inserted into `improvement_proposals` (no DB call made).

---

### T3 — `generate_proposal()` returns a diff-style string for Tier-1 (prose) changes

**Module:** `proposal_generator`
**Function:** `generate_proposal(pattern, config, repo_root)`

Input: a `Pattern` object with `key="adversarial.static_test_masquerading_as_behavioral"`, `count=3`, `story_ids=["story-701", "story-720", "story-722"]`.

The Sonnet subagent call is mocked to return a well-formed unified diff string targeting `tech_dev_agents/sdlc/phase_prompts/phase_7.md`.

**Assert:**
- `proposal.diff_text` is non-empty.
- `proposal.diff_text` starts with `--- a/tech_dev_agents/sdlc/phase_prompts/phase_7.md`.
- `proposal.diff_text` contains `+++ b/tech_dev_agents/sdlc/phase_prompts/phase_7.md`.
- `proposal.rationale` is non-empty and contains at least one of the story IDs from `pattern.story_ids`.
- `proposal.proposal_type == "tier1"`.

---

### T4 — `apply_tier1_proposal()` writes file changes atomically; rolls back on failure

**Module:** `approval_handler`
**Functions:** `apply_tier1_proposal(proposal, repo_root)` / `_apply_proposal(proposal_id)`

Tier-1 proposal targets `tech_dev_agents/sdlc/phase_prompts/phase_7.md`.

Two sub-cases:

**4a — Happy path:**
Mock `subprocess.run` to return `returncode=0` for `git apply`. Mock git commit/push/PR-open calls. Mock DB service to accept status updates.

Assert:
- `improvement_proposals.status` transitions to `"applied"`.
- `applied_commit` is set to a non-empty string.
- The mock commit call's message contains `"STORY-727"`, the proposal ID, and the pattern key.
- The single-file constraint check passes (mocked `git diff --name-only HEAD~1` returns only `phase_7.md`).

**4b — Failure/rollback path:**
Mock `git apply` to return `returncode=1`. Assert that no DB status change to `"applied"` is made and a `ProposalDiffInvalid` (or equivalent exception) is raised.

---

### T5 — `record_approval(proposal_id, approver)` inserts into `improvement_proposals` with `status='approved'`

**Module:** `approval_handler`
**Function:** `record_approval(proposal_id, approver)` (or `_apply_proposal` invoked via approval webhook path)

Use a mock DB service (`improvement_service`). Call the approval handler with a mock webhook payload containing `[APPROVE]` and sender UPN `"mark@gorillacommerce.co"`.

**Assert:**
- The mock `improvement_service.mark_proposal_decided` is called with:
  - `proposal_id` matching the proposal.
  - `decision == "approved"`.
  - `decided_by == "mark@gorillacommerce.co"`.
- Status field on the in-memory proposal transitions to `"approved"` before the apply step is invoked.

---

### T6 — `track_metric(proposal_id, before, after)` computes delta and stores in `improvement_tracking`

**Module:** `tracker`
**Function:** `check_back(now, db_service, config)` or `track_metric(proposal_id, before_value, after_value)`

Time-travel setup:
- Insert a proposal row with `applied_at = now - 14 days`, `expected_metric = "adversarial.static_test_masquerading.weekly_count"`, `expected_direction = "decrease"`.
- Before-apply fixture value: 5 occurrences.
- After-apply measured value: 2 occurrences (computed by mocking the metric query to return 2).

**Assert:**
- `improvement_service.record_tracking_measurement` is called with:
  - `metric_value == 2`.
  - `window_days == 14`.
- No DM is sent to Mark (value decreased as expected — no warning warranted).

Inverse: if mocked metric returns 7 (increased), assert exactly one `[INFO]` DM is posted naming the proposal ID.

---

### T7 — `run_retrospective()` returns summary dict with `proposals_applied`, `avg_delta`, `open_patterns`

**Module:** `retrospective`
**Function:** `generate(now, db_service, pattern_counts, config)` or `run_retrospective()`

Fixture week pattern counts:
```python
{
    "adversarial.static_test_masquerading_as_behavioral": 7,
    "adversarial.spec_requirement_omitted": 4,
    "adversarial.unrealistic_test_fixture": 3,
    "retry_storm.short_duration": 2,
}
```

Mock DB returns: 0 pending proposals, 0 recent decisions, 0 tracking measurements.

**Assert:**
- The output string (or dict) contains a "Top 3 recurring patterns" section.
- Top 3 listed (in descending count order): `static_test_masquerading` (7), `spec_requirement_omitted` (4), `unrealistic_test_fixture` (3).
- `retry_storm.short_duration` (count=2) is NOT in the top-3 section.
- All three empty-state sections are present and render "(none)" rather than being omitted:
  - Proposals decided in past 7 days: (none)
  - Tracking measurements: (none)
  - Pending proposals: (none)

---

### T8 — `generate_proposal()` with `mode='shadow'` does NOT write any files

**Module:** `proposal_generator`
**Function:** `generate_proposal(pattern, config, repo_root)` with `config.mode = "shadow"`

Assert:
- No file system writes occur (use `tmp_path` and assert no new files created).
- No DM is posted to Mark (mock Teams client records zero calls).
- The `Proposal` object IS returned in-memory (proposals are still generated and stored to DB in shadow mode).
- `improvement_service.insert_proposal` IS called (shadow mode queues proposals; it only suppresses DMs to Mark).

---

### T9 — `detect_patterns()` skips patterns where `enabled=False` in config

**Module:** `pattern_detector`
**Function:** `detect_patterns(repo_root, config)`

Config: set `enabled = False` at the top-level `[morris.improvement]` config or per the kill-switch mechanism.

Fixture: 4 adversarial-review files all with CRITICAL static-masquerading findings (well above threshold).

**Assert:**
- `detect_patterns()` returns an empty list (or raises no-op early exit).
- No DB calls are made.
- No DMs posted.

Variant: test that a pattern with `enabled=False` in a per-pattern config entry (if the spec exposes that) is skipped while other patterns proceed. If the spec only has a global kill-switch, test the global `enabled = False` behavior only.

---

### T10 — Full cycle integration: detect → propose → approve → apply → track (mocked DB)

**Modules:** all five (`pattern_detector`, `proposal_generator`, `approval_handler`, `tracker`, `retrospective`)
**Functions:** full pipeline run with mocked DB and mocked Teams client

Setup:
1. Fixture directory with 3 adversarial-review files triggering `adversarial.static_test_masquerading_as_behavioral`.
2. Mock DB: `improvement_service` (in-memory list simulating table rows).
3. Mock Teams client: records all `post_dm` calls.
4. Mock Sonnet subagent: returns a valid unified diff for `phase_7.md`.
5. Mock `git apply --check` and commit/push/PR calls.

**Assert end-to-end:**
- Phase 1 (detect): pattern detected with count=3.
- Phase 2 (propose): proposal inserted with `status="pending"`.
- Phase 3 (approve): approval handler receives mocked `[APPROVE]` webhook; status transitions to `"approved"` then `"applied"`.
- Phase 4 (track): 14 days later (frozen clock), tracker inserts `improvement_tracking` row with `metric_value`.
- Phase 5 (retro): retrospective output mentions the applied proposal.
- Teams client call count: exactly 3 DMs across the full cycle:
  1. `[APPROVAL-NEEDED]` when proposed.
  2. `[ACTION]` when applied.
  3. `[INFO]` or `[BRIEFING]` from retrospective.

---

## 4. Test File

`tests/deployment/test_self_improvement_727.py`

Tests import directly from the five new modules under `deployment/morris/scripts/improvement/`. Since those modules do not exist at end of Phase 7, all imports produce `ModuleNotFoundError` and all 10 test functions fail immediately — confirming RED state.

---

## 5. Fixture Files

Adversarial-review fixture content is embedded inline in the test file (no separate fixture files needed for Phase 7 RED state). Phase 8 may split fixtures into `tests/fixtures/improvement/` if the fixtures grow complex.

---

## 6. RED → GREEN Acceptance

All 10 tests must be GREEN at end of Phase 8. The implementation checklist for Phase 8:

- [ ] `deployment/morris/scripts/improvement/__init__.py`
- [ ] `deployment/morris/scripts/improvement/pattern_detector.py` — `detect_patterns()`, `aggregate_by_pattern_key()`
- [ ] `deployment/morris/scripts/improvement/proposal_generator.py` — `generate_proposal()`, `ProposalDiffInvalid`
- [ ] `deployment/morris/scripts/improvement/approval_handler.py` — `record_approval()`, `apply_tier1_proposal()`, Tier-2 PR path
- [ ] `deployment/morris/scripts/improvement/tracker.py` — `check_back()`, `track_metric()`
- [ ] `deployment/morris/scripts/improvement/retrospective.py` — `generate()` / `run_retrospective()`
- [ ] `deployment/morris/scripts/improvement/config.py` — `ImprovementConfig` dataclass
- [ ] `tech_dev_agents/ops_console/services/improvement_service.py` — all 6 async DB functions
- [ ] `tech_dev_agents/quality/adversarial_review_parser.py` — `parse_adversarial_review(path)` (lifted from STORY-723)
- [ ] `scripts/migrations/004_improvement_proposals.sql`

---

*End of test-design.md — STORY-727 Phase 7.*
