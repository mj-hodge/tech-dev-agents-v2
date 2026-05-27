# STORY-730: Test Design — project_file.py deploy verification

## Test Criteria

### 1. Unit test: phase runner .project update path (mock project_file)

**File**: `tests/deployment/test_phase_runner_project_file.py` (existing)

**What to verify**: When `update_story_status` is available (not `None`),
the phase runner calls it with the correct arguments and a new row is
appended for the phase.

**Approach**: Mock `project_file.update_story_status`, run the phase-end
code path, assert the mock was called with expected kwargs including
`story_id`, `current_phase`, `project_path`, and `is_final`.

**Pass criteria**: Mock called exactly once per phase-end; kwargs match the
phase being completed.

---

### 2. Integration test: push-code.sh deploys project_file.py to VMs

**File**: `tests/deployment/test_push_code_safety.sh`

**What to verify**:
- (a) `/opt/agent/project_file.py` exists on the target VM after deploy.
- (b) The `.project` file gets a new entry when a phase runs.
- (c) The smoke test (Step 6) includes a `project_file` import check —
  so a deploy with a missing/corrupted `project_file.py` **fails** the
  smoke test instead of silently succeeding.

**Existing coverage** (STORY-524):
- TC-12: `PROJECT_FILE` variable declared in `push-code.sh`.
- TC-13: `$PROJECT_FILE` included in the copy loop.
- TC-14: Mock scp log shows `project_file.py` was transferred.

**New test case — TC-20** (this story):
- Verify the smoke-test command string in `push-code.sh` includes
  `project_file` (i.e., the import validation covers it, not just scp).

**Pass criteria**: All of TC-12, TC-13, TC-14, TC-20 pass.

---

### 3. Compliance test: project_file.py in deploy manifest

**File**: `tests/deployment/test_push_code_safety.sh`

**What to verify**: The deploy manifest (the `for f in ...` loop and the
`echo "Files: ..."` summary line) both include `project_file.py`.

**Existing coverage**: TC-12 and TC-13 cover the variable and loop.

**Additional check (TC-20)**: The smoke-test section of `push-code.sh`
references `project_file` in its Python import line, ensuring post-deploy
verification is complete.

**Pass criteria**: `grep -q 'project_file' <smoke-test-section>` succeeds.

---

## E2E Validation (post-deploy, manual)

After deploying with `push-code.sh`:

1. Run one Small story end-to-end on a test VM.
2. Verify **zero** `'project_file not available'` warnings in the
   `dispatch-poller` journal (`journalctl -u dispatch-poller`).
3. Verify the story's `.project` file shows the phase completion entry.

---

## Test Matrix Summary

| ID    | Type        | Scope                                      | Automated | Status  |
|-------|-------------|---------------------------------------------|-----------|---------|
| TC-12 | Static      | PROJECT_FILE variable declared              | Yes       | GREEN   |
| TC-13 | Static      | $PROJECT_FILE in copy loop                  | Yes       | GREEN   |
| TC-14 | Integration | scp copies project_file.py                  | Yes       | GREEN   |
| TC-20 | Static      | Smoke test imports project_file             | Yes       | **RED** |
| UT-1  | Unit        | Phase runner calls update_story_status      | Yes       | GREEN   |
| E2E-1 | Manual      | Zero warnings after full story run          | No        | Pending |
