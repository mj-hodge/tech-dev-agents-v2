# Test Design: STORY-626 — Remove Playwright artifacts from STORY-621 PR

## Test Strategy

**Scope:** Small (cleanup/rework)
**Coverage target:** 50% — critical paths only
**Test level:** Unit (file-system and git-index verification)
**Test file:** `tests/deployment/test_gitignore_test_results.py`

This story is a mechanical cleanup — no business logic changes. Tests verify:
1. The `.gitignore` file contains `test-results/` to prevent future accidents
2. No `test-results/` files are tracked in the git index
3. The existing STORY-621 tests remain importable and discoverable (no regression)

## Test Matrix

| ID | Test Name | AC | Condition | Expected | Fails Because |
|----|-----------|-----|-----------|----------|---------------|
| T1 | `test_gitignore_contains_test_results_entry` | AC3 | Read `.gitignore` from repo root | `test-results/` line present | `.gitignore` does not yet contain the entry |
| T2 | `test_no_test_results_files_tracked_in_git` | AC1,AC2 | Run `git ls-files test-results/` | Empty output (no tracked files) | `test-results/` files are still in the index on the target branch |
| T3 | `test_gitignore_entry_is_directory_pattern` | AC3 | Parse `.gitignore` for test-results pattern | Entry ends with `/` (directory pattern, not file pattern) | Entry not present yet |

## Implementation Notes

- **Mocking:** T1 and T3 read the real `.gitignore` file — no mocks needed.
- **T2** uses `subprocess.run(["git", "ls-files", "test-results/"])` to check the index.
- **RED state rationale:** All three tests fail because `.gitignore` does not yet contain `test-results/`. T2 will additionally fail on the `story-621/story-621` branch where the files are still tracked.
- Tests are deliberately simple — a junior can read and understand them in under a minute.

## Test Quality Gates

- [x] Every test name clearly states what it verifies
- [x] AAA structure used throughout
- [x] No external API calls (Gate 2a: N/A — no external APIs)
- [x] No DB interactions (Gate 3: N/A)
- [x] No file uploads (Gate 7: N/A)
- [x] No multi-tenant endpoints (Gate 6: N/A)
- [x] No stateful operations (Gate 9: N/A)
- [x] Tests are independent and can run in any order
