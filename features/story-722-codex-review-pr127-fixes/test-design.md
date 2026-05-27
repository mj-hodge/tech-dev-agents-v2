# Test Design: STORY-722 -- Fix PR #127 review findings

## Overview
| Field | Value |
|-------|-------|
| Scope | Small |
| Coverage target | 50% (critical paths) |
| Test file | `tests/morris/test_codex_review_pr127_fixes.py` |
| Total tests | 10 |
| RED state | 10 skip (modules not on branch); 5 FAIL when run against buggy story-720 code |

## Test Groups

### Group A: `_is_duplicate_issue` over-match (CRITICAL -- Fix 1)

| Test | What It Verifies | RED Reason |
|------|------------------|------------|
| `test_exact_finding_id_match_returns_true` | Exact `codex-finding-id` marker -> True | PASS (correct behavior) |
| `test_different_finding_id_returns_false` | Different finding_id, same `[codex-debt]` title -> False | **FAIL**: fallback block returns True |
| `test_no_existing_issues_returns_false` | Empty issue list -> False | PASS (correct behavior) |

### Group B: End-to-end P2 dedup regression

| Test | What It Verifies | RED Reason |
|------|------------------|------------|
| `test_second_p2_creates_issue_when_first_exists` | Two different P2 findings both create separate issues | **FAIL**: over-match skips second P2 |

### Group C: Fingerprint window range (MEDIUM -- Fix 3)

| Test | What It Verifies | RED Reason |
|------|------------------|------------|
| `test_3_line_snippet_not_matched` | 3-line window NOT checked (below spec min of 5) | **FAIL**: range(1,...) includes 3 |
| `test_5_line_snippet_matched` | 5-line window IS checked (spec min) | PASS |
| `test_10_line_snippet_matched` | 10-line window IS checked (spec max) | PASS |
| `test_12_line_snippet_not_matched` | 12-line window NOT checked (above spec max of 10) | **FAIL**: range(...,15) includes 12 |

### Group D: Bare except logging (LOW -- Fix 4)

| Test | What It Verifies | RED Reason |
|------|------------------|------------|
| `test_exception_in_read_file_lines_is_logged` | Exception logged, not silently swallowed | **FAIL**: bare `pass` logs nothing |

### Group E: Output variance

| Test | What It Verifies | RED Reason |
|------|------------------|------------|
| `test_different_existing_issues_produce_different_skip_counts` | No issues vs matching issue -> different issue-creation counts | PASS |

## Fix 2 (BLOCKER -- seed.md section headings)

Not tested in pytest. The CI gate `test_every_seed_written_after_20260422_has_required_sections` already validates this. Phase 8 renames `## 6. Test Criteria` -> `## Test Criteria` and `## 7. Validation` -> `## Validation`.

## Fix 5 (LOW -- dead conftest fixtures)

Audit result: both session-scoped fixtures (`codex_review_post`, `codex_debt_autoclose`) in `tests/morris/conftest.py` are actively used by `test_codex_review_post.py` and `test_codex_debt_autoclose.py` respectively. No removal needed.

## API Mock Verification

No Playwright tests. All mocks target module-level functions via `unittest.mock.patch`:
- `codex_review_post.gh_create_review_comment`
- `codex_review_post.gh_create_issue`
- `codex_review_post.gh_submit_review`
- `codex_review_post.gh_list_reviews`
- `codex_review_post.gh_list_pr_comments`
- `codex_review_post.gh_list_repo_issues`
- `codex_review_post.read_file_lines`

All mock targets match actual function names in the source module.

## Checklist

- [x] Every test name clearly states what it verifies
- [x] AAA structure in all tests
- [x] Output-variance test (Group E)
- [x] Null/boundary coverage (Group A test_no_existing_issues, existing STORY-720 Group J)
- [x] No `pytest.raises(ImportError)` as passing condition -- uses `skipif` pattern
- [x] External API isolation inherited from existing STORY-720 tests (Group I)
- [x] `pytest --collect-only` discovers all 10 tests
- [x] 5 tests FAIL against buggy code, 5 PASS -- correct RED state
- [x] No import errors, no type errors
