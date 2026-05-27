# STORY-1007 Feature Spec — Morris review-prs + merge v2-PR gates

**Story ID:** STORY-1007
**Scope:** Medium
**Phase:** 6 (Design)
**Date:** 2026-05-18

---

## Overview

Add AST-based new-behavior assertion checking and 3-question PR template
validation to Morris's `review-prs` and `merge` skills. Closes the
enforcement gap that allowed PR #244 (STORY-766) to ship with
`assert 48 in url or True` — a test that always passes and validates nothing.

---

## Components

### 1. `assertion_checker.py`

Module: `tech_dev_agents/morris/review_helpers/assertion_checker.py`

Public API:
```python
def check_new_behavior_assertions(
    diff_text: str,
    pr_files: list[str],
) -> list[AntiPatternFinding]:
```

Anti-patterns detected (via `ast.parse`, never regex):

| Type | Example | Detection |
|------|---------|-----------|
| `OR_TRUE_BYPASS` | `assert 48 in url or True` | `BoolOp(Or)` with truthy final value |
| `ASSERT_CONSTANT` | `assert True` / `assert 1 == 1` | Constant expression / constant comparison |
| `BARE_PASS` | `def test_foo(): pass` | No asserts + only Pass/docstring stmts |

CLI invocation (used by skill):
```bash
gh pr diff <PR> | python3 -m tech_dev_agents.morris.review_helpers.assertion_checker
```
Exits 0 if clean, 1 if findings; prints JSON array of findings to stdout.

### 2. `v2_template_checker.py`

Module: `tech_dev_agents/morris/review_helpers/v2_template_checker.py`

Public API:
```python
def check_three_question_template(pr_body: str) -> ThreeQResult:
def is_v2_repo(repo_name: str) -> bool:
```

Required headings (exact match):
- `### 1. Canon-doc impact`
- `### 2. Scaffold backport`
- `### 3. Sibling-pipeline sweep`

Validation rules:
- All 3 headings must be present.
- Each section must have >= 1 non-empty content line.
- Bare `N/A` without justification is rejected.
- `N/A — <reason>` or `N/A -- <reason>` is accepted.
- `- [x] <text>` (checked checkbox) is accepted.

CLI invocation:
```bash
gh pr view <PR> --json body --jq '.body' | \
    python3 -m tech_dev_agents.morris.review_helpers.v2_template_checker
```
Exits 0 if passed, 1 if failed; prints JSON result to stdout.

v2 repo pattern: `^(.*-v2|api-advertising-amazon)$` against repo basename.

### 3. `review-prs/SKILL.md` changes

- Step 5: Replace "Has tests OR is test-exempt" with "New-behavior assertions present"
- Step 4b (NEW): AST assertion check via `assertion_checker.py`
- Step 5a (NEW): v2-only three-question review prompt

### 4. `merge/SKILL.md` changes

- Step 2: Add G6 (3-question template filled) and G7 (`morris/canon-check` GREEN)
- Step 2a (NEW): v2 merge gate logic
- Step 3: Decision table extended — G6/G7 fail → DO NOT merge

---

## Data Flow

```
gh pr diff → assertion_checker.py → findings JSON → SKILL.md Step 4b
gh pr view --json body → v2_template_checker.py → result JSON → SKILL.md Step 2a (G6)
gh pr view --json statusCheckRollup → canon-check status → Step 2a (G7)
```

---

## Scope Boundaries

- Only affects `review-prs` and `merge` skills.
- G6/G7 gates fire ONLY for repos matching the v2 pattern.
- Non-v2 PRs: no change to review/merge flow.
- `assertion_checker.py` checks only added/modified test functions in diff.
- Does not auto-fill PR template sections or override human reviewers.

---

## Dependencies

- STORY-1005: provides `morris/canon-check` status name (G7 consumer).
- STORY-1006: pre-dispatch validator (complementary; this story handles PR-review time).
