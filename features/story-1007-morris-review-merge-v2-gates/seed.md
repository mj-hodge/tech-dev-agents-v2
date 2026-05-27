# STORY-1007 — Morris `review-prs` + `merge` v2-PR gates + new-behavior-assertion check

**Story ID:** STORY-1007
**Scope:** Medium
**Epic:** STORY-1000 (Cross-Repo Canon Alignment)
**Work-Stream:** B — Morris canon enforcement
**Priority:** P1 (after STORY-1005, STORY-1006)
**Repo touched:** `tech-dev-agents`
  - `deployment/vm/skills/morris/review-prs/SKILL.md` (MODIFY)
  - `deployment/vm/skills/morris/merge/SKILL.md` (MODIFY)
**Date:** 2026-05-18
**Reporter:** Mark Oreta
**Status:** Phase 1 — seed in progress
**Frontend:** false

---

## Problem

Morris's review and merge skills do not enforce the canon contract on `*-v2` PRs. Two concrete gaps:

1. **`review-prs/SKILL.md` Step 5** (lines 173-185) — auto-merge criteria checks "Has tests OR is test-exempt". This check is satisfied by the existence of test files, not by whether the tests actually assert anything. Concrete evidence: PR #244 (STORY-766) shipped a test containing `assert 48 in url or True` — see `dispatch_795.py:18`:
   > "Fix test: test_lookback_window_configurable has 'assert 48 in url or True' which always passes."
   That test "exists" and "has assertions." It tests nothing. Morris approved the PR. The rework was hand-dispatched as STORY-795.

2. **`merge/SKILL.md` Step 2** (lines 38-51) — pre-merge hard gates check `mergeable`, `mergeStateStatus`, `statusCheckRollup`, `reviewDecision`. For `*-v2` repos this is insufficient:
   - The `gc-data-v2/pipeline-template/.github/pull_request_template.md` requires the 3-question continuous-improvement checklist (canon-doc impact / scaffold backport / sibling-pipeline sweep — see `pull_request_template.md:14-44`). PR templates can be filled with all-N/A or left blank entirely and `mergeStateStatus` will still be `CLEAN`.
   - The `morris/canon-check` failing status (introduced by STORY-1005) must block the merge.

The 2026-05-04 v2 mirror desync (epic seed line 22) was exactly the merge-gate gap: a v2 PR landed without the 3-question checklist answered, and without canon-drift catching it.

## Goal

Three additive changes:

1. **`review-prs/SKILL.md` Step 5** — replace the "Has tests" bullet with **"new behavior paths assert observable outcomes"**. Operationalized: for every test function added or modified in the diff, the function body must contain ≥1 `assert` statement where the asserted expression involves at least one symbol introduced by the PR (call the new function, reference the new constant, etc.), AND does not match the `or True` / `or 1` / `assert True` / `pass` anti-patterns.

2. **`review-prs/SKILL.md` Step 5 (v2 PRs only)** — when `repo` matches `hpi-gorillacommerce/*-v2` or `api-advertising-amazon`, add three questions to the review checklist:
   - **Q1 — Parallel `gc-data-v2` PR?** If the PR's diff touches canon (e.g. `auth/`, deploy workflow, observability scrubber), is there a sibling PR open against `gc-data-v2` linked in the body?
   - **Q2 — New-behavior assertions?** Same rule as #1 above but tightened: tests touching v2 data-platform code must reference symbols from `gc-data-v2/platform/*.md` invariants where applicable.
   - **Q3 — Sibling backport?** Are sibling pipelines listed in the PR template's question 3 (or marked N/A with justification)?

3. **`merge/SKILL.md` Step 2 (v2 PRs only)** — add two hard gates:
   - **G6 — 3-question PR template answered:** PR body must contain all three sections from `pipeline-template/pull_request_template.md` filled with non-empty bullets. "N/A" is allowed but must include a justification phrase (not just the bare token "N/A"). Implementation: regex-match the three required headings (`### 1. Canon-doc impact`, `### 2. Scaffold backport`, `### 3. Sibling-pipeline sweep`), then for each section assert ≥1 line either starts with `- [x]` (checked box) or contains a non-N/A explanation.
   - **G7 — `morris/canon-check` status GREEN:** the commit status named `morris/canon-check` (set by STORY-1005) must be `SUCCESS`. If `FAILURE` or `PENDING`, refuse merge.

## Scope

1. **Edit `deployment/vm/skills/morris/review-prs/SKILL.md`:**
   - Replace bullet at line 180 (`- [ ] Has tests OR is test-exempt …`) with: `- [ ] **New-behavior assertions present** — every added/modified test contains an assert that involves a PR-introduced symbol AND does not match anti-patterns (`assert True`, `or True`, `or 1`, `pass`, `assert <constant>`). See Step 4b.`
   - Insert new **Step 4b — New-behavior assertion check** between current Step 4 and Step 5. Pseudocode in the skill:
     ```bash
     # For each modified test file:
     gh pr diff [PR] --repo … | python3 - <<'PY'
     # ast-parse hunks; for each new/modified `def test_*`:
     #   collect asserts; reject if matches anti-pattern; require ≥1 reference to PR-new symbols
     PY
     ```
   - Insert new **Step 5a — v2 PR three-question review** that fires only when repo matches `*-v2` or `api-advertising-amazon`. Posts the 3-question review prompt as part of the review comment body.
2. **Edit `deployment/vm/skills/morris/merge/SKILL.md`:**
   - Add **G6** and **G7** to the Step 2 hard-gates list.
   - Add **Step 2a — v2 merge gates** with the regex/status logic for G6/G7.
   - In Step 3 decision table: any `*-v2` PR with G6 or G7 failing → "DO NOT merge — investigate" branch.
3. **NEW helper module** `tech_dev_agents/morris/review_helpers/`:
   - `assertion_checker.py` — `check_new_behavior_assertions(diff_text: str, pr_files: list[str]) -> list[Anti­Pattern­Find­ing]`
   - `v2_template_checker.py` — `check_three_question_template(pr_body: str) -> ThreeQResult`
   - `tests/test_assertion_checker.py` — incl. the `assert 48 in url or True` case as a fixture
   - `tests/test_v2_template_checker.py` — incl. blank-template / all-N/A-no-justification / properly-filled cases
4. **Skill invocations** — both skills import and shell out to these helpers via `python -m tech_dev_agents.morris.review_helpers.*`. Keep the skill files runnable as documentation; keep the logic in importable Python.

## Out of scope

- Creating `canon-check` (STORY-1005 owns).
- Creating `pre-dispatch-validate` (STORY-1006 owns).
- Modifying `gc-data-v2/pipeline-template/pull_request_template.md` (it is canon; do not change it from this story).
- Modifying `fix-pr` skill (the autofix loop is independent).
- Touching `dispatch-queue`, `daily-standup`, `curator`, or other Morris skills.
- Building an LLM-based assertion-quality grader (must be deterministic AST-based).
- Backfilling reviews on PRs that merged before this story ships.

## Success criteria

| ID | Criterion | Pass / fail signal |
|---|---|---|
| **SC-1** | `review-prs/SKILL.md` line 180 no longer reads "Has tests OR is test-exempt"; replaced with "New-behavior assertions present". | `grep -n "New-behavior assertions" deployment/vm/skills/morris/review-prs/SKILL.md` returns a match; old phrase absent. |
| **SC-2** | `review-prs/SKILL.md` contains Step 4b (assertion-check) and Step 5a (v2 three-question review). | `grep -E "Step 4b\|Step 5a" deployment/vm/skills/morris/review-prs/SKILL.md` returns both. |
| **SC-3** | `merge/SKILL.md` Step 2 hard-gates list contains G6 (3-question template) and G7 (`morris/canon-check` GREEN). | `grep -E "G6\|G7" deployment/vm/skills/morris/merge/SKILL.md` returns both; both reference the trigger condition `*-v2`. |
| **SC-4** | `assertion_checker.py` flags the `assert 48 in url or True` anti-pattern as `OR_TRUE_BYPASS`. | `pytest tech_dev_agents/morris/review_helpers/tests/test_assertion_checker.py::test_or_true_antipattern_caught -v` PASSES. |
| **SC-5** | `assertion_checker.py` flags `assert True` / `assert 1 == 1` / bare `pass` as anti-patterns. | `pytest test_assertion_checker.py -k "antipattern" -v` PASSES for all four anti-pattern variants. |
| **SC-6** | `assertion_checker.py` does NOT flag a legitimate parametrized assertion that references a PR-new symbol. | `pytest test_assertion_checker.py::test_legitimate_assertion_passes -v` PASSES. |
| **SC-7** | `v2_template_checker.py` rejects a PR body missing one of the three required headings. | `pytest test_v2_template_checker.py::test_missing_heading_rejected -v` PASSES. |
| **SC-8** | `v2_template_checker.py` rejects an all-N/A body with no justification text. | `pytest test_v2_template_checker.py::test_bare_NA_rejected -v` PASSES. |
| **SC-9** | `v2_template_checker.py` accepts an all-N/A body where each section includes a non-template-boilerplate justification sentence. | `pytest test_v2_template_checker.py::test_justified_NA_accepted -v` PASSES. |
| **SC-10** | **Live PR test** — open a test PR in `walmart-supplier-v2` with a blank PR body and a fake `assert 48 in url or True` test. Run `review-prs`. Morris comment must mention BOTH "new-behavior assertion" issue AND "3-question template" issue. Run `merge` skill; it must refuse with reason naming G6. | Test commands documented; review comment contains both strings; `gh pr merge` not invoked. |
| **SC-11** | **Canon-check status honored** — for the same test PR, with `morris/canon-check` status set to `FAILURE`, the `merge` skill must refuse with reason naming G7 even if everything else is green. | Documented; logs show "G7 failed: morris/canon-check=FAILURE". |
| **SC-12** | **No regression on non-v2 PRs** — a Small PR in `tech-dev-agents` with no v2 markers reviews and merges normally (G6/G7 not evaluated). | Documented; existing review flow unchanged. |

## Files to modify

| Path | Action |
|---|---|
| `deployment/vm/skills/morris/review-prs/SKILL.md` | MODIFY (Steps 4b, 5, 5a) |
| `deployment/vm/skills/morris/merge/SKILL.md` | MODIFY (Steps 2, 2a, 3) |
| `tech_dev_agents/morris/review_helpers/__init__.py` | CREATE |
| `tech_dev_agents/morris/review_helpers/assertion_checker.py` | CREATE |
| `tech_dev_agents/morris/review_helpers/v2_template_checker.py` | CREATE |
| `tech_dev_agents/morris/review_helpers/tests/__init__.py` | CREATE |
| `tech_dev_agents/morris/review_helpers/tests/test_assertion_checker.py` | CREATE |
| `tech_dev_agents/morris/review_helpers/tests/test_v2_template_checker.py` | CREATE |
| `tests/epic_1000/test_incident_replays.py` | EXTEND — add `test_pr_244_or_true_antipattern_caught` |

## Files to NOT modify

- `deployment/vm/skills/morris/canon-check/**` (STORY-1005).
- `tech_dev_agents/morris/canon_check/**` (STORY-1005).
- `tech_dev_agents/morris/pre_dispatch/**` (STORY-1006).
- `tech_dev_agents/ops_console/routes/dispatch.py` / `dispatch_v2.py` (STORY-1006).
- `deployment/vm/skills/morris/fix-pr/SKILL.md`, `dispatch-queue/SKILL.md`, `daily-standup/SKILL.md`, `curator/SKILL.md`, `fleet-health/SKILL.md`, `reliability-check/SKILL.md`.
- `gc-data-v2/pipeline-template/.github/pull_request_template.md` (canon; consume, don't modify).
- The Step 1/2/3/4 sections of `review-prs/SKILL.md` — only Steps 4b/5/5a get changes.
- Step 4–7 of `merge/SKILL.md` — only Steps 2, 2a, 3 change.

## Verification plan

| SC | Command | Expected output |
|---|---|---|
| SC-1 | `grep -c "Has tests OR is test-exempt" deployment/vm/skills/morris/review-prs/SKILL.md` and `grep -c "New-behavior assertions" deployment/vm/skills/morris/review-prs/SKILL.md` | First: `0`; second: `≥1` |
| SC-2 | `grep -E "^## Step 4b\|^## Step 5a" deployment/vm/skills/morris/review-prs/SKILL.md \| wc -l` | `2` |
| SC-3 | `grep -E "G6\|G7" deployment/vm/skills/morris/merge/SKILL.md \| wc -l` | `≥2` and grep `*-v2` | proves trigger condition |
| SC-4 | `pytest tech_dev_agents/morris/review_helpers/tests/test_assertion_checker.py::test_or_true_antipattern_caught -v` | PASSED |
| SC-5 | `pytest tech_dev_agents/morris/review_helpers/tests/test_assertion_checker.py -k "antipattern" -v` | All PASSED |
| SC-6 | `pytest test_assertion_checker.py::test_legitimate_assertion_passes -v` | PASSED |
| SC-7 | `pytest test_v2_template_checker.py::test_missing_heading_rejected -v` | PASSED |
| SC-8 | `pytest test_v2_template_checker.py::test_bare_NA_rejected -v` | PASSED |
| SC-9 | `pytest test_v2_template_checker.py::test_justified_NA_accepted -v` | PASSED |
| SC-10 | Create test PR per scope; `claude-sdk -p "Run review-prs on PR <N> in walmart-supplier-v2" -w /opt/agent`; then `gh pr view <N> --json comments --jq '.comments[-1].body'` | Body contains both `new-behavior` and `3-question` |
| SC-11 | Set status via `gh api -X POST /repos/.../statuses/<sha> -f state=failure -f context=morris/canon-check`; invoke `merge` skill; check Morris logs | Logs: `G7 failed: morris/canon-check=FAILURE`; no merge call |
| SC-12 | Open a small PR in `tech-dev-agents` (non-v2); invoke `review-prs`; assert review posted, no G6/G7 mention | Comment body absent G6/G7 strings |

## Boundaries

| Always do | Ask first | Never do |
|---|---|---|
| Detect v2 repos by regex `^(.*-v2\|api-advertising-amazon)$` against the repo basename. | Add a repo to the v2-trigger list (e.g. a new pipeline repo not yet matching the pattern). | Apply G6/G7 gates to non-v2 repos. |
| Use Python AST parsing (`ast.parse` on hunk text) for the assertion check — not regex. Regex misses anti-patterns; AST nails them. | Loosen the assertion check (e.g. accept tests with no new-symbol references). | Approve a v2 PR whose template heading is present but body is empty. |
| Reference the `dispatch_795.py:18` evidence in commit messages so future agents see the failure mode. | Change the canon-check status name (`morris/canon-check` is locked by STORY-1005). | Auto-fill a missing 3-question section on behalf of the PR author. |
| Coordinate with STORY-1005 on the canon-check status integration. | Mark a PR as `test-exempt` (docs/config) under the new rule — that exemption survives but must be explicit. | Merge a `*-v2` PR with `morris/canon-check=FAILURE` under any circumstance. |

## Done looks like (terminal transcript)

```
$ pytest tech_dev_agents/morris/review_helpers/tests/ -v
test_assertion_checker.py::test_or_true_antipattern_caught PASSED
test_assertion_checker.py::test_assert_true_antipattern_caught PASSED
test_assertion_checker.py::test_assert_constant_antipattern_caught PASSED
test_assertion_checker.py::test_bare_pass_antipattern_caught PASSED
test_assertion_checker.py::test_legitimate_assertion_passes PASSED
test_v2_template_checker.py::test_missing_heading_rejected PASSED
test_v2_template_checker.py::test_bare_NA_rejected PASSED
test_v2_template_checker.py::test_justified_NA_accepted PASSED
test_v2_template_checker.py::test_three_filled_sections_accepted PASSED
================ 9 passed in 0.11s ================

$ pytest tests/epic_1000/test_incident_replays.py::test_pr_244_or_true_antipattern_caught -v
test_pr_244_or_true_antipattern_caught PASSED

# Live: blank PR body + assert-or-true test in walmart-supplier-v2 PR #N
$ gh pr view N --repo hpi-gorillacommerce/walmart-supplier-v2 --json comments --jq '.comments[-1].body' | grep -E "new-behavior|3-question"
- [ ] New-behavior assertion: FAIL — test_lookback uses `or True` bypass (assertion_checker.py:42)
- [ ] 3-question template: FAIL — `### 1. Canon-doc impact` heading present but empty body

$ claude-sdk -p "Merge PR N in walmart-supplier-v2" -w /opt/agent
G6 failed: 3-question template incomplete. Refusing merge.
G7 failed: morris/canon-check=FAILURE. Refusing merge.
```

## Escalation contract

- **Assertion checker disagrees with a human reviewer** — Morris's review comment must say "automated assertion check flagged X; if this is a false positive, comment `@morris override-assertion-check` and Mark will adjudicate." The checker does not auto-override.
- **A v2 PR genuinely has no canon impact** — author still must answer Q1/Q2/Q3 with N/A + a one-sentence justification (per template line 25, 32, 41). Bare N/A is rejected. Justified N/A passes G6.
- **Canon-check status missing entirely** (STORY-1005 not yet deployed in target env) — G7 falls through to "treat as PENDING" and `merge` refuses with reason "canon-check has not run; deploy STORY-1005 first." Never default-pass.
- **PR template upstream change** — if `gc-data-v2/pipeline-template/pull_request_template.md` changes its three headings, the v2 template checker must be updated in lockstep. This is detected by STORY-1005's `canon-check` (Morris's own template gets compared too in `tech-dev-agents`? No — `tech-dev-agents` is not v2. The escalation: data team must announce template changes; Mark updates `v2_template_checker.py`).


## Test Criteria

| Test | Validates |
|------|-----------|
| RED tests from Phase 7 (see `## Verification plan`) | Every SC has a literal test or shell command |
| Full pytest suite GREEN, zero regressions | No unrelated breakage |
| Incident-replay tests (where applicable) | The 2026-05-04..05-18 failure classes are catchable by the new gate |

See `## Success criteria` and `## Verification plan` above for SC-keyed predicates.


## Validation

After merge:

1. **Tests:** all RED tests turned GREEN; `pytest <story test paths>` exits 0.
2. **No regressions:** full suite passes; CI green on the PR.
3. **Per-SC verification:** every command in `## Verification plan` runs and produces the expected output.
4. **Tracking updated:** `.project` and `backlog.md` reflect Phase 8 completion; Monday.com task updated.

## Phase path

Medium scope: `1 → 6 → 7 → 8 → Done`.

- Phase 1 (this seed): scope locked
- Phase 6 (design): AST detection rules for assertion anti-patterns, v2-template regex set, decision table for G6/G7
- Phase 7 (test design): the 9 unit tests in SC-4..SC-9 + the REPLAY test for PR #244
- Phase 8 (implementation): helpers → skill edits → deploy to Morris VM → live test on dummy v2 PR

## Dependencies

- **Hard:** STORY-1005 (provides the `morris/canon-check` status name and signal that G7 consumes).
- **Soft:** STORY-1006 (the pre-dispatch validator catches some of the same anti-patterns at enqueue time; this story catches what slips through to PR-review time).
- **Downstream consumer:** STORY-1012 (`complete-story` 3-question gate uses the same `v2_template_checker.py`).
