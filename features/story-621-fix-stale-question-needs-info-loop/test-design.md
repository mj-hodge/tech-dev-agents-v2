# Test Design — STORY-621: Fix Stale QUESTION.md Needs-Info Loop

## Overview

| Field | Value |
|-------|-------|
| Scope | Small |
| Coverage target | 60% of changed functions |
| Test file | `tests/deployment/test_phase_runner_question_md.py` |
| Level | Unit (mocked filesystem + subprocess) |
| Framework | pytest |

## Test Structure

```
tests/deployment/
└── test_phase_runner_question_md.py
    ├── Group A — Layer 1: Pre-phase cleanup (_clear_stale_questions)
    │   ├── A-01: stale QUESTION.md deleted at phase start
    │   ├── A-02: git rm + commit recorded for stale cleanup
    │   ├── A-03: commit message matches expected format
    │   ├── A-04: no-op when no QUESTION.md exists
    │   └── A-05: handles both story folder variants (slug and bare)
    │
    ├── Group B — Layer 2: Hardened _check_for_questions
    │   ├── B-01: fresh QUESTION.md written during phase routes to needs_info
    │   ├── B-02: version marker before phase_start_ts treated as stale
    │   ├── B-03: version marker after phase_start_ts treated as fresh
    │   ├── B-04: no marker + content unchanged = stale (hash check)
    │   ├── B-05: no marker + content changed = fresh (hash check)
    │   └── B-06: strict mtime check (no 2s grace window)
    │
    ├── Group C — Layer 3: Audit trail
    │   ├── C-01: question_check JSON emitted for fresh decision
    │   └── C-02: question_check JSON emitted for stale decision
    │
    └── Group D — Integration: run_sdlc_phases calls cleanup before loop
        └── D-01: _clear_stale_questions called before first phase runs
```

## Test Specifications

### Group A — Layer 1: Pre-phase cleanup

**A-01** `test_stale_question_md_deleted_at_phase_start`
- Arrange: Create `features/<story_folder>/QUESTION.md` with old mtime
- Act: Call `_clear_stale_questions(workdir, story_id, story_folder)`
- Assert: File no longer exists on disk

**A-02** `test_stale_cleanup_records_git_commit`
- Arrange: Create QUESTION.md in a real git repo (committed)
- Act: Call `_clear_stale_questions`
- Assert: `git log -1 --oneline` shows the cleanup commit

**A-03** `test_stale_cleanup_commit_message_format`
- Arrange: Same as A-02
- Act: Call `_clear_stale_questions`
- Assert: Commit message contains `chore(STORY-N): clear pre-phase QUESTION.md`

**A-04** `test_stale_cleanup_noop_when_no_question_md`
- Arrange: Empty features directory, no QUESTION.md
- Act: Call `_clear_stale_questions`
- Assert: No error, no git commit created

**A-05** `test_stale_cleanup_checks_both_folder_variants`
- Arrange: Create QUESTION.md in the bare `story-621` folder (not the slug folder)
- Act: Call `_clear_stale_questions`
- Assert: File deleted from the bare folder too

### Group B — Layer 2: Hardened _check_for_questions

**B-01** `test_fresh_question_written_during_phase_returns_content`
- Arrange: Write QUESTION.md with mtime after phase_start_ts and version marker after phase_start_ts
- Act: Call `_check_for_questions(workdir, story_id, story_folder, phase_start_ts)`
- Assert: Returns the question content string

**B-02** `test_version_marker_before_phase_start_treated_stale`
- Arrange: Write QUESTION.md with `<!-- QUESTION-VERSION: <old_ts> phase=6 agent=devon -->` where old_ts < phase_start_ts. Set mtime to after phase_start_ts (simulating git checkout touching mtime)
- Act: Call `_check_for_questions`
- Assert: Returns None (stale despite mtime)

**B-03** `test_version_marker_after_phase_start_treated_fresh`
- Arrange: Write QUESTION.md with `<!-- QUESTION-VERSION: <fresh_ts> phase=6 agent=devon -->` where fresh_ts > phase_start_ts
- Act: Call `_check_for_questions`
- Assert: Returns the question content

**B-04** `test_no_marker_content_unchanged_treated_stale`
- Arrange: Write QUESTION.md BEFORE phase start. Record its content hash via `_record_question_state(workdir, story_id, story_folder)`. Touch mtime to after phase_start_ts (content unchanged)
- Act: Call `_check_for_questions` with the recorded phase start state
- Assert: Returns None (hash unchanged = stale)

**B-05** `test_no_marker_content_changed_treated_fresh`
- Arrange: Record hash at phase start (file doesn't exist or has old content). Write NEW content to QUESTION.md during "phase"
- Act: Call `_check_for_questions`
- Assert: Returns the new question content

**B-06** `test_strict_mtime_no_grace_window`
- Arrange: Write QUESTION.md with mtime exactly 1s before phase_start_ts (within the OLD 2s grace window)
- Act: Call `_check_for_questions`
- Assert: Returns None (stale — no grace window)

### Group C — Layer 3: Audit trail

**C-01** `test_audit_log_emitted_for_fresh_question`
- Arrange: Write a fresh QUESTION.md (marker after phase_start_ts)
- Act: Call `_check_for_questions`, capture stdout
- Assert: stdout contains JSON with `"event": "question_check"`, `"decision": "fresh"`, and all required fields (mtime, phase_start_ts, version_marker_ts, content_hash_changed, decision_reason)

**C-02** `test_audit_log_emitted_for_stale_question`
- Arrange: Write a stale QUESTION.md (marker before phase_start_ts)
- Act: Call `_check_for_questions`, capture stdout
- Assert: stdout contains JSON with `"event": "question_check"`, `"decision": "stale"`, and all required fields

### Group D — Integration

**D-01** `test_run_sdlc_phases_calls_clear_stale_questions_before_loop`
- Arrange: Place stale QUESTION.md in workdir. Mock `_run_phase_sdk`, `_ensure_branch`, etc.
- Act: Call `run_sdlc_phases`
- Assert: QUESTION.md is gone before the first `_run_phase_sdk` call

## Output-Variance Tests

**B-01 vs B-02** serve as the output-variance pair: same function, two different inputs (fresh marker vs stale marker), two different outputs (content vs None).

**B-04 vs B-05** serve as a second pair: same function, unchanged content vs changed content, different outputs (None vs content).

## API Mock Verification

N/A — no API routes or Playwright tests. All tests are unit-level with mocked filesystem.

## LLM Error-Prone Coverage

- [x] Boundary: B-06 tests the exact mtime boundary (no grace window)
- [x] Edge: A-04 tests the no-file case; B-04 tests content-unchanged edge
- [x] Output format: C-01/C-02 validate the audit JSON shape
- [x] Null/None: A-04 (missing file), B-04 (no marker)

## Checklist

- [x] Every test name says what it verifies
- [x] AAA structure throughout
- [x] Junior-readable — comments explain "why"
- [x] Output-variance tests included (B-01/B-02, B-04/B-05)
- [x] Error observability tests (C-01, C-02 — audit log emission)
- [x] No implementation code written
- [x] Tests are runnable RED code
