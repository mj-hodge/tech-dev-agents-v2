# Seed — STORY-343: PR #46 Review Fixes

## Summary

PR #46 (STORY-340: Curator Teams Q&A) received a code review from Morris that identified four bugs requiring fixes before merge. This story tracks the remediation of all four issues.

## Scope

**Classification:** Small (bug fixes only, no new features)

## Bugs

### Bug 1: `build_archive_files()` default detection fails after `apply_defaults()`

**File:** `deployment/vm/skills/morris/curator/teams_qa.py`
**Lines:** 252-254

The `is_default` check on line 253 uses `q.id not in answers` to decide whether to tag an answer as `_(default applied)_`. However, when `build_archive_files()` is called with the output of `apply_defaults()` (the normal flow), every question ID is present in the answers dict — even those that received the default. This means `is_default` is always `False` and the archive never labels defaults.

The same logic error affects `answered_count` on lines 263-266, causing the summary to miscount explicit vs defaulted answers.

**Fix:** Accept an optional `explicitly_answered` set (or track it in `apply_defaults()` return) so `build_archive_files()` can distinguish explicit answers from defaulted ones.

### Bug 2: `fleet_review.py` SDLC compliance — GitHub tree API missing `?recursive=1`

**File:** `scripts/fleet_review.py`
**Line:** 211

The `gh api` call queries `repos/.../git/trees/main` without `?recursive=1`. The GitHub Trees API only returns top-level entries by default. Since deliverables live at `features/story-XXX/seed.md` (depth 3), they are never found, causing false positives for missing deliverables.

**Fix:** Append `?recursive=1` to the tree API URL.

### Bug 3: `parse_reply` regex greedy edge case — answers with commas truncated

**File:** `deployment/vm/skills/morris/curator/teams_qa.py`
**Line:** 34-36

The `_QA_COMMA_PATTERN` regex `(.+?)(?=\s*,\s*[Qq]\d+\s*:|$)` uses a lazy quantifier `(.+?)` that stops at the first possible match. For the last answer in a comma-separated list, the `$` end-of-string anchor causes the lazy match to capture as little as possible. If the last answer contains a comma (e.g., `Q2: same thing, canonical name = tech-datawarehouse`), only the text before the first comma is captured.

**Fix:** Change the last capture group to use a greedy quantifier for the final segment, or restructure the regex to split on the delimiter pattern first.

### Bug 4: Exception leak in `routes/curate.py`

**File:** `tech_dev_agents/ops_console/routes/curate.py`
**Line:** 57

The `raise HTTPException(500, f"Failed to enqueue: {exc}")` includes the raw exception message in the HTTP response. This could leak internal details (database errors, connection strings, stack info) to API consumers.

**Fix:** Replace with a generic error message. Log the detailed error server-side only.

## Acceptance Criteria

- [ ] Archive files correctly tag `_(default applied)_` when called with `apply_defaults()` output
- [ ] Summary file correctly counts explicit vs defaulted answers
- [ ] Fleet review SDLC check finds nested deliverable files
- [ ] `parse_reply` correctly handles answers containing commas
- [ ] Curate endpoint returns generic 500 message without exception details
- [ ] All existing tests pass; new tests cover the fixed edge cases
