# Seed: STORY-722 — Fix PR #127 review findings (codex review severity + debt log)

## Overview
| Field | Value |
|-------|-------|
| Mode | feature_update |
| Scope | small |
| Frontend | false |
| Feature Name | PR #127 review fixes for STORY-720 codex review severity + debt log |
| Phase Path | 1 (Seed) → 7 (Test Design) → 8 (Implementation) → Done |
| Repo | tech-dev-agents |
| Target Branch | `story-722/story-722` (fixes applied on top of `story-720/story-720`) |
| Status | Seed written 2026-04-25 |
| Priority | 80 — blocks PR #127 merge; CRITICAL + BLOCKER findings |
| Depends on | STORY-720 (PR #127 must exist; fixes land on its branch) |

---

## 1. Idea / Trigger

Morris reviewed PR #127 (STORY-720: codex review severity calibration + debt log) and found 5 issues ranging from CRITICAL to LOW. Two findings (the `_is_duplicate_issue()` over-match and the missing seed.md CI sections) block the PR from merging. The remaining three are correctness and hygiene fixes that should land before merge.

## 2. Problem Statement

PR #127 cannot merge because:

1. **CRITICAL** — `_is_duplicate_issue()` in `codex_review_post.py:272-287` has a title/body fallback check (lines 284-286) that returns `True` for ANY existing codex-debt issue, not just the one matching the current finding. Once any single P2 debt issue exists in the repo, no new P2 debt issues will ever be created. The `finding_marker` check on line 282-283 is correct; the fallback block must be removed.

2. **BLOCKER** — `features/story-720-codex-review-severity-and-debt-log/seed.md` uses numbered section headings (`## 6. Test Criteria`, `## 7. Validation`) but the CI gate `test_every_seed_written_after_20260422_has_required_sections` checks for exact substrings `## Test Criteria` and `## Validation`. The numbered headings don't match, so CI fails.

3. **MEDIUM** — `check_fingerprint_in_file()` in `codex_debt_autoclose.py:80-100` iterates `range(1, min(len+1, 15))` for window sizes, but the documented spec says fingerprints are 5-10 line snippets. The window range should be `range(5, min(len+1, 11))` to match the spec and avoid unnecessary computation on 1-4 line windows.

4. **LOW** — Bare `except Exception: pass` in `codex_review_post.py:251-256`. Silently swallowing exceptions makes debugging impossible. Should log the exception or narrow to a specific exception type.

5. **LOW** — Dead conftest fixtures in `tests/morris/conftest.py` if any fixtures are not actually imported by test files. Verify usage and remove unused fixtures.

## 3. Scope Classification

**Small.** Five isolated fixes in three files, all within the existing STORY-720 branch. No new features, no API changes, no database changes. All fixes are mechanical corrections to existing code.

## 4. Codebase Context

| Aspect | Details |
|--------|---------|
| Affected files | `deployment/morris/scripts/codex_review_post.py`, `deployment/morris/scripts/codex_debt_autoclose.py`, `tests/morris/conftest.py`, `features/story-720-codex-review-severity-and-debt-log/seed.md` |
| Related components | Morris review pipeline, codex-debt issue lifecycle |
| Current behavior | `_is_duplicate_issue()` over-matches; seed.md fails CI; fingerprint window too wide; bare except swallows errors |
| Desired change | Each finding fixed per Morris's review feedback |
| Test coverage | 29/29 tests GREEN on STORY-720 branch (tests don't cover the over-match bug — need new test) |
| Architecture constraints | Fixes must land on `story-720/story-720` branch or be cherry-pickable onto it |

### Fix details

**Fix 1 — `_is_duplicate_issue()` (CRITICAL):**
Remove lines 284-286 (the `[codex-debt]` title + `codex-finding-id:` body fallback). Keep only the exact `finding_marker in body` check on line 282-283. This ensures each P2 finding is deduplicated by its own unique finding ID, not by the mere existence of any debt issue.

**Fix 2 — seed.md sections (BLOCKER):**
Rename `## 6. Test Criteria` → `## Test Criteria` and `## 7. Validation` → `## Validation` in `features/story-720-codex-review-severity-and-debt-log/seed.md`. Also renumber or un-number remaining headings for consistency.

**Fix 3 — fingerprint window range (MEDIUM):**
In `codex_debt_autoclose.py`, change `range(1, min(len(lines) + 1, 15))` to `range(5, min(len(lines) + 1, 11))`. This matches the documented 5-10 line window spec and avoids hashing unnecessary 1-4 line windows.

**Fix 4 — bare except (LOW):**
Replace `except Exception: pass` with `except Exception: logger.debug(...)` or narrow to the specific expected exception type.

**Fix 5 — dead conftest fixtures (LOW):**
Audit `tests/morris/conftest.py` for fixtures not referenced by any test file. The two session-scoped fixtures (`codex_review_post`, `codex_debt_autoclose`) appear to be in active use by `tests/morris/test_codex_review_post.py` and `tests/morris/test_codex_debt_autoclose.py` respectively. If both are used, no removal needed — document the audit result.

## 5. Out of Scope

- Refactoring the broader review pipeline.
- Adding new features to codex_review_post.py or codex_debt_autoclose.py.
- Changing the Codex review prompt or severity tagging.
- Modifying the CI gate logic itself.

## 6. Success Criteria

- [ ] SC-1: `_is_duplicate_issue()` only returns True when the exact finding_id marker is found in an existing issue body.
- [ ] SC-2: A new P2 finding creates a debt issue even when other unrelated P2 debt issues already exist.
- [ ] SC-3: `features/story-720-codex-review-severity-and-debt-log/seed.md` passes `test_every_seed_written_after_20260422_has_required_sections`.
- [ ] SC-4: `check_fingerprint_in_file()` only checks window sizes 5-10 (not 1-14).
- [ ] SC-5: No bare `except Exception: pass` in codex_review_post.py.
- [ ] SC-6: All 29 existing STORY-720 tests remain GREEN.
- [ ] SC-7: New test(s) cover the `_is_duplicate_issue()` over-match regression.

## Test Criteria

Phase 7 produces `test-design.md` and updates tests at `tests/morris/test_codex_review_post.py` and `tests/morris/test_codex_debt_autoclose.py`.

1. **Duplicate issue: exact match** — `_is_duplicate_issue()` returns True when an existing issue body contains the exact `codex-finding-id:<id>` marker for the current finding.
2. **Duplicate issue: different finding** — `_is_duplicate_issue()` returns False when existing issues have different finding IDs (even if they have `[codex-debt]` in title).
3. **Duplicate issue: no issues** — `_is_duplicate_issue()` returns False when no existing issues.
4. **Fingerprint window range** — `check_fingerprint_in_file()` does NOT match a 3-line snippet but DOES match a 5-line and 10-line snippet.
5. **Exception logging** — verify the formerly-bare except now logs (or is narrowed).
6. **Existing tests pass** — all 29 STORY-720 tests remain GREEN after fixes.

## Validation

After Phase 8:
1. Run `pytest tests/morris/ -v` — all tests GREEN including new regression tests.
2. Run `pytest tests/test_sdlc_framework_compliance.py::test_every_seed_written_after_20260422_has_required_sections -v` — passes (seed.md sections fixed).
3. Push to PR #127 branch — CI passes.
4. Manual spot-check: create two mock P2 findings with different IDs, process them, verify both create separate debt issues.

## 8. Dispatch Notes

- Repo: tech-dev-agents
- Branch: `story-722/story-722` (or apply fixes directly on `story-720/story-720`)
- Scope: small
- Priority: 80
- Expected runtime: Phase 7 ~10 min, Phase 8 ~20 min
- Must merge before or with PR #127.
