# Test Design: Git Workflow (STORY-008)

> Phase 7 — Test Design
> Date: 2026-03-26
> Scope: Small
> Story path: 1 → 7 → 8 → Done

---

## Test Strategy

**Approach:** Pure unit tests plus subprocess mocking. The module should expose deterministic helpers for URL building, branch naming, commit formatting, branch-preparation decisions, and PR body generation. All git and gh calls are intercepted with `unittest.mock` so the suite never touches a real repo or GitHub account.

**Framework:** `pytest` with standard library `unittest.mock`.

**Module under test:** `tech_dev_agents.git_workflow`

**Coverage target:** All acceptance criteria from the story seed, with emphasis on safety guards and idempotent decision logic. The suite stays compact and focused for a small story.

**State:** These tests start in RED state until Phase 8 implements the module.

---

## Test Groups

### Group 1 — Authenticated Clone URL + Masking Helpers

| # | Test | Description |
|---|------|-------------|
| T01 | `test_build_authenticated_clone_url_inserts_token` | Build `https://<token>@github.com/org/repo.git` from a standard GitHub HTTPS URL. |
| T02 | `test_mask_clone_url_redacts_token` | Token masking helper replaces the secret with `***` so debug output is safe. |
| T03 | `test_workspace_clone_path_uses_repo_name` | Clone workspace path resolves to a repo-named subdirectory for isolation. |

### Group 2 — Branch Naming Convention

| # | Test | Description |
|---|------|-------------|
| T04 | `test_build_agent_branch_name_sanitizes_slug` | Branch name uses `agent/<story-id>/<slug>` with lowercase hyphenated slugging. |
| T05 | `test_build_agent_branch_name_trims_noise` | Special characters, duplicate separators, and whitespace are normalized away. |

### Group 3 — Push Guard + Commit Formatting

| # | Test | Description |
|---|------|-------------|
| T06 | `test_push_branch_blocks_main_and_master` | `push_branch()` raises before executing git for `main` and `master`. |
| T07 | `test_format_commit_message_renders_all_fields` | Commit formatter renders type/scope/subject, optional body, and trailers cleanly. |
| T08 | `test_format_commit_message_omits_blank_body_section` | Missing optional body does not leave extra blank lines or placeholders. |

### Group 4 — Rebase Conflict Detection

| # | Test | Description |
|---|------|-------------|
| T09 | `test_pull_rebase_aborts_and_raises_conflict_error_with_files` | A rebase conflict triggers `git rebase --abort` and raises a structured error with file paths. |

### Group 5 — Idempotent Branch Preparation Contract

| # | Test | Description |
|---|------|-------------|
| T10 | `test_decide_branch_preparation_prefers_current_branch_noop` | If the target branch is already checked out, the helper returns a no-op decision. |
| T11 | `test_decide_branch_preparation_handles_existing_local_branch` | Existing local branches produce a checkout decision instead of a failure. |
| T12 | `test_decide_branch_preparation_handles_remote_only_branch` | Remote-only branches produce a fetch-and-checkout decision. |
| T13 | `test_decide_branch_preparation_creates_new_branch_when_missing` | Missing branches produce a create-new decision. |

### Group 6 — PR Creation

| # | Test | Description |
|---|------|-------------|
| T14 | `test_build_pr_body_includes_story_scope_phase_and_changes` | PR body interpolation includes story ID, scope classification, phase summary, and checklist entries. |
| T15 | `test_open_pr_invokes_gh_pr_create_with_expected_body` | `gh pr create` is called with the supplied title, body, and base branch. |

---

## Files Produced

| File | Purpose |
|------|---------|
| `tests/test_git_workflow.py` | RED-state unit tests for the git workflow wrapper |
| `features/story-008-git-workflow/test-design.md` | Phase 7 test-design artifact |
