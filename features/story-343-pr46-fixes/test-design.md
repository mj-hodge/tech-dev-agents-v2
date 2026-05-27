# Test Design — STORY-343: PR #46 Review Fixes

## Overview

Tests for the four bug fixes identified in PR #46 review. All tests target existing test files to maintain consistency.

## Test Cases

### Bug 1: `build_archive_files()` default detection

**File:** `tests/deployment/test_curator_teams_qa.py`

| ID | Test | Expected |
|----|------|----------|
| T40 | `build_archive_files()` called with `apply_defaults()` output where some Qs are defaulted | Answers file contains `_(default applied)_` for defaulted Qs only |
| T41 | Summary `answered_count` correct after `apply_defaults()` with partial answers | `Answered by Mark` count matches only explicitly answered Qs |
| T42 | All answers explicit (no defaults) — no `_(default applied)_` tags | Zero default tags in answers file |
| T46 | `build_archive_files()` called with `explicitly_answered=None` — heuristic fallback detects defaults by comparing to proposed_default | Answers differing from default not tagged; matching answers tagged |

### Bug 2: `fleet_review.py` SDLC compliance — recursive tree

**File:** `tests/scripts/test_fleet_review.py`

| ID | Test | Expected |
|----|------|----------|
| TC-30 | `check_sdlc_compliance()` gh api call URL contains `?recursive=1` | The subprocess call to `gh api` includes `recursive` parameter |
| TC-31 | Two stories: 320 has seed.md, 321 does not; path matching uses story prefix, not global substring | STORY-321 flagged for missing seed.md; STORY-320 not flagged |

### Bug 3: `parse_reply` regex — commas in answers

**File:** `tests/deployment/test_curator_teams_qa.py`

| ID | Test | Expected |
|----|------|----------|
| T43 | `parse_reply("Q1: yes, Q2: same thing, canonical name = tech-datawarehouse")` | `q2` value is `"same thing, canonical name = tech-datawarehouse"` (not truncated) |
| T44 | `parse_reply("Q1: a, b, c, Q2: d")` — first answer has commas, second is simple | `q1` = `"a, b, c"`, `q2` = `"d"` |

### Bug 4: Exception leak in curate route

**File:** `tests/deployment/test_curator_teams_qa.py`

| ID | Test | Expected |
|----|------|----------|
| T45 | POST `/api/morris/curate` when `enqueue()` raises `RuntimeError("connection refused to db:5432")` | Response status 500, body does NOT contain `"connection refused"` or `"db:5432"` |

## Test Strategy

- Add new test methods to existing test classes
- Tests written BEFORE implementation fixes (RED state per Phase 7)
- All tests should fail against current code, pass after fixes
