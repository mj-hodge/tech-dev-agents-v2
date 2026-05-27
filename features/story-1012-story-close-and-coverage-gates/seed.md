# STORY-1012 — `complete-story` 3-question gate + new-behavior-assertion gate + canon-version pin in PR descriptions

**Story ID:** STORY-1012
**Scope:** Medium
**Epic:** STORY-1000 (Cross-Repo Canon Alignment)
**Work-Stream:** C — Queue validation & code-stability gates
**Repo touched:** `tech-dev-agents` (primary). The `complete-story` skill lives in `.sdlc/skills/complete-story/SKILL.md` — that file IS the submodule's canonical copy; editing it is editing `sdlc-framework`. We edit via the framework's standard "update the skill in framework, bump the pin" flow (see STORY-1011 — but for this story we ship the skill change directly in framework via a coordination PR, then bump the pin here).
**Date:** 2026-05-18
**Reporter:** Mark Oreta
**Status:** Phase 1 — seed written, awaiting gate
**Frontend:** false

---

## Problem

Three closely related close-time / merge-time gates are missing, all visible in the 2026-05-04..05-18 audit:

### Hole 1 — `complete-story` lets stories close with unanswered canon questions

The current `complete-story` skill at `/mnt/c/Projects/tech-dev-agents/.sdlc/skills/complete-story/SKILL.md` (123 lines, inspected during seed authoring) walks through merge, Asana move, `.project` update, and backlog update. It does NOT require the closer to answer:

1. **Canon-doc gap** — did this story discover something that should be backported into `gc-data-v2/platform/*.md` or `tech-gc-knowledgebase/wiki/`?
2. **Scaffold backport** — did this story modify pipeline-template-shaped scaffolding that needs to propagate to siblings?
3. **Sibling sweep** — are there sibling repos/pipelines that should get the same fix?

The retro on 2026-05-04 named all three as "things we don't ask, so they don't ship." Result: 7 framework changes captured in the retro, zero shipped, because no gate forced the closer to either answer "yes, here's the linked PR" or "no, and here's why."

### Hole 2 — New-behavior assertion gate (PR #244 / STORY-766 anti-pattern)

PR #244 (STORY-766, fleet vigilance Check 9) shipped a test that read `assert 48 in url or True`. The `or True` makes the assertion always pass — it's a no-op disguised as a test. STORY-795 had to re-ship the whole feature to fix this. **No CI gate caught it** because the test "ran" and the test "passed." The fix is to lint new test code in a PR: for any test function added/modified, if its `assert` statements logically reduce to a tautology (`or True`, `or 1`, comparing literals to themselves, etc.), fail the PR with a clear message naming the line.

### Hole 3 — Dispatch-created PRs don't pin canon versions

Per the epic's SC-8: every PR created by the dispatch service must carry `gc-data-v2: <40-char sha>` and `sdlc-framework: v<semver>` in its body. Currently PRs are created by `tech_dev_agents/git_workflow.py:220` (`open_pr()`) and the `body` is whatever the agent passes in — no canon pinning. Without these pins we can't reproduce "what canon was live when this PR was merged" months later.

## Goal

1. Modify `complete-story` (`.sdlc/skills/complete-story/SKILL.md`): for Medium+ closures, the closer cannot complete without all three questions answered with **linked artifacts** (`https://github.com/...` URL or `https://docs....` URL — anything that's a fetchable resource). N/A is allowed only with a one-line justification (`canon_gap: N/A — feature is internal-only ops console route, no canon docs apply`).
2. Add a `new-behavior-assertion-check` job to `tech-dev-agents` CI that uses `ast` to walk new/modified test functions in the PR diff and flag tautological asserts. Fails the PR if any are found.
3. Modify the PR-creation helper in `tech_dev_agents/git_workflow.py` (the `open_pr()` method at line 220 — confirmed: this is the right file) to automatically append a `## Canon Versions` footer block to every PR body it creates, containing:
   - `gc-data-v2: <40-char sha>` — read from `tech_dev_agents/ops_console/services/canon_service.py` (created by Work-Stream A/B — STORY-1001 / STORY-1005) or, if that service is not yet wired, from the env var `GC_DATA_V2_PIN` falling back to "unknown" with a warning log.
   - `sdlc-framework: v<semver>` — read from `.sdlc/PINNED_VERSION` (introduced by STORY-1011).
   - Append idempotently — if the body already has a `## Canon Versions` block, replace it; don't duplicate.

## Scope

**In scope:**

- Edit `complete-story` skill — via coordination PR against `sdlc-framework`, then bump `.sdlc/PINNED_VERSION` in this repo. The skill text gains a "Step 11 — Three-Question Gate (Medium+)" section that runs before the Asana close-out, lists the three questions with the exact link-or-justification rule, and writes the answers into `features/<story-folder>/close-out.md`.
- New file produced at close: `features/<story-folder>/close-out.md` with the three Q&A entries. Used by the canon-backport loop (STORY-1003) downstream.
- `tools/tautology_lint.py` (NEW) — AST walker that takes a Python file path + a list of test function names (added/modified in the diff) and returns `[]` if clean, or a list of `(line, expression)` tuples for any tautological assert. Patterns to detect (start with the highest-value, lowest-false-positive set):
  - `assert X or True` / `assert True or X` / `assert X or 1` / `assert X or any-truthy-literal`
  - `assert X == X` (same name on both sides, same form)
  - `assert True` literal
  - `assert <non-empty-literal>` (e.g. `assert "yes"`, `assert 1`)
  - `assert not False`
- `.github/workflows/test-quality.yml` (NEW) — runs on PR with `paths: ['**/*.py']`, checks out PR HEAD, computes the diff against base, extracts the changed test function names per file, runs `tautology_lint.py` per file. Fails CI on any hit. Output: `error: tautological assert at tests/foo.py:42 — assert lookback in url or True` plus a hint pointing at PR #244 as the canonical example.
- Edit `tech_dev_agents/git_workflow.py` `open_pr()` to inject `## Canon Versions` footer in `body` before the `gh pr create` shell-out. Add `_append_canon_versions(body: str) -> str` private helper.
- Tests:
  - `tests/tools/test_tautology_lint.py` — 10 cases (true positives + true negatives + edge cases).
  - `tests/git_workflow/test_open_pr_canon_pin.py` — PR body has the footer; idempotency on replay.
  - `tests/skills/test_complete_story_three_question_gate.py` — schema test on the skill markdown (mostly: assert the 3 questions are listed, the link-or-justification phrase is present, the close-out.md path is referenced).

**Out of scope:**

- The canon-backport loop itself (STORY-1003 — owned by Work-Stream A). This story only ensures the `close-out.md` artifact exists with the right shape; consuming it is a separate story.
- Auto-detecting more tautology classes beyond the listed five — start with the five; expand later if false-negative rate is high.
- The "freshness" / `last-reviewed` enforcement on wiki pages (STORY-1016 — Work-Stream D).
- Wiring the `canon-version` pin into v1 dispatch flows — v1 is frozen.
- Touching the `complete-story` skill for Small stories — gate applies to Medium+ only (the retro evidence is all on Medium+ stories where canon-discovery is plausible).

## Files to modify

- (Coordination PR on `sdlc-framework`) `/mnt/c/Projects/sdlc-framework/skills/complete-story/SKILL.md` — add Step 11 Three-Question Gate.
- `/mnt/c/Projects/tech-dev-agents/.sdlc/PINNED_VERSION` — bump to the framework version that includes the new skill (e.g. `v1.1.0`). Depends on STORY-1011 being merged.
- `/mnt/c/Projects/tech-dev-agents/tools/tautology_lint.py` — NEW.
- `/mnt/c/Projects/tech-dev-agents/.github/workflows/test-quality.yml` — NEW.
- `/mnt/c/Projects/tech-dev-agents/tech_dev_agents/git_workflow.py` — modify `open_pr()` (line 220) + add `_append_canon_versions()` helper.
- `/mnt/c/Projects/tech-dev-agents/tests/tools/test_tautology_lint.py` — NEW.
- `/mnt/c/Projects/tech-dev-agents/tests/git_workflow/test_open_pr_canon_pin.py` — NEW.
- `/mnt/c/Projects/tech-dev-agents/tests/skills/test_complete_story_three_question_gate.py` — NEW.

## Files to NOT modify

- Other skills under `.sdlc/skills/` — touch only `complete-story`. `spec`, `start-story`, `dispatch` are out of scope.
- `tech_dev_agents/ops_console/routes/dispatch.py` or `dispatch_v2.py` — the canon-pin lives in the PR helper, not the dispatch endpoints.
- `tech_dev_agents/ops_console/services/self_healing.py` and the `pr_link_backfill_sweeper` — orthogonal.
- v1 `routes/dispatch.py`.
- Any existing CI workflow (`contract-critical.yml`, `deploy-ops-console.yml`, `migration-invariant.yml`, `test.yml`, `sdlc-drift-check.yml` from STORY-1011).
- `.sdlc/CLAUDE.md` (framework-managed).
- Asana board structure.

## Success criteria

- **SC-1 — Three-Question Gate text in skill:** `grep -E "canon.gap|scaffold.backport|sibling.sweep" /mnt/c/Projects/tech-dev-agents/.sdlc/skills/complete-story/SKILL.md` returns 3+ matches (one per question). The skill text references `features/<story-folder>/close-out.md` as the output artifact.
- **SC-2 — Closing a Medium story without answers is blocked:** Phase 7 introduces a behavioural test `tests/skills/test_complete_story_three_question_gate.py::test_close_with_na_no_justification_is_blocked` simulating the skill against a stub close-out missing the justification — the test asserts the skill's enforcement logic rejects the close. (The skill is markdown — the "enforcement" is documented protocol; the test asserts the markdown explicitly states the rejection condition with the exact wording.)
- **SC-3 — Tautological assert linter catches `assert X or True`:** `python tools/tautology_lint.py tests/_fixtures/tautology_examples.py --funcs test_lookback_window_configurable` outputs `error: tautological assert at line 12 — assert 48 in url or True`. Verified by `tests/tools/test_tautology_lint.py::test_or_true_detected`.
- **SC-4 — Linter false-positive rate is zero on the existing test suite:** `python tools/tautology_lint.py --scan-all tests/` (a debug mode) outputs zero hits against the current `tests/` tree. Verified manually + by the test `test_full_suite_clean`.
- **SC-5 — CI workflow fails on a planted tautology:** open a demo PR adding `assert "x" or True` to a test, observe `test-quality ❌ failure` in `gh pr checks`. Verified manually in Phase 8.
- **SC-6 — PR bodies carry canon pins:** any PR created by `git_workflow.open_pr()` after rollout has `## Canon Versions\ngc-data-v2: <40-char sha>\nsdlc-framework: v<semver>` in its body. Verified by `tests/git_workflow/test_open_pr_canon_pin.py::test_pr_body_contains_canon_versions_block`.
- **SC-7 — Canon pin is idempotent:** calling `_append_canon_versions(body)` twice produces the same output as calling it once. Verified by `test_append_canon_idempotent`.
- **SC-8 — Inspected dispatch-created PR has the pins:** `gh pr view <any_post_rollout_PR> --json body --jq .body | grep -E "^(gc-data-v2|sdlc-framework):"` returns 2 matching lines.

## Verification plan

| SC | Command | Expected output |
|---|---|---|
| SC-1 | `grep -E "canon.gap\|scaffold.backport\|sibling.sweep" /mnt/c/Projects/tech-dev-agents/.sdlc/skills/complete-story/SKILL.md \| wc -l` | `>= 3` |
| SC-2 | `pytest tests/skills/test_complete_story_three_question_gate.py -v` | `4 passed` (4 cases: clean close, missing justification, all N/A blocked, linked-artifact accepted) |
| SC-3 | `python /mnt/c/Projects/tech-dev-agents/tools/tautology_lint.py /mnt/c/Projects/tech-dev-agents/tests/_fixtures/tautology_examples.py --funcs test_lookback_window_configurable` | exit code 1; stdout contains `error: tautological assert at line` |
| SC-4 | `python /mnt/c/Projects/tech-dev-agents/tools/tautology_lint.py --scan-all /mnt/c/Projects/tech-dev-agents/tests/` | exit code 0; no output |
| SC-5 | (demo PR) `gh pr checks <demo_pr_number>` | `test-quality ❌ failure`; job log contains `error: tautological assert` |
| SC-6 | `pytest tests/git_workflow/test_open_pr_canon_pin.py -v` | `5 passed` |
| SC-7 | `python -c "from tech_dev_agents.git_workflow import _append_canon_versions; b='hello'; print(_append_canon_versions(_append_canon_versions(b)) == _append_canon_versions(b))"` | `True` |
| SC-8 | `gh pr view <recent_dispatch_pr> --json body --jq .body \| grep -cE "^(gc-data-v2\|sdlc-framework):"` | `2` |

## Boundaries

| Always Do | Ask First | Never Do |
|---|---|---|
| Require linked artifacts (URLs) for canon-gap / scaffold-backport / sibling-sweep answers — N/A allowed only with a one-line justification | Expand the 3-question gate to Small stories (currently Medium+ only — start narrow) | Allow a Medium+ story to close with `canon_gap: N/A` (no justification) — that's the exact failure mode |
| Start the tautology linter with the 5 named patterns; add more only after data shows false-negative rate is high | Add a new tautology pattern to the linter (false-positive risk to existing PRs) | Lint test files outside the PR diff — only test functions added/modified in the PR |
| Read `gc-data-v2` SHA from the canon service (STORY-1001) when available; fall back to `GC_DATA_V2_PIN` env var; never silently write "unknown" without a WARN log line | Make the canon-pin block customizable per repo (start with one shape) | Block PR creation if canon SHA is unknown — log loudly and fall back; we don't want to wedge the fleet while STORY-1001 is in flight |
| Replace existing `## Canon Versions` block on idempotent reapply — don't duplicate | Move the helper into the dispatch service instead of `git_workflow.py` (test layering says it belongs with `open_pr`) | Push the canon-pin into the v1 dispatch flow (v1 frozen) |

## Done looks like

```
$ pytest tests/tools/test_tautology_lint.py tests/git_workflow/test_open_pr_canon_pin.py tests/skills/test_complete_story_three_question_gate.py -v
test_or_true_detected ............................................... PASSED
test_assert_true_detected ........................................... PASSED
test_assert_one_detected ............................................ PASSED
test_x_eq_x_detected ................................................ PASSED
test_not_false_detected ............................................. PASSED
test_real_assert_not_flagged ........................................ PASSED
test_complex_real_assert_not_flagged ................................ PASSED
test_assert_in_helper_not_flagged_when_not_in_changed_funcs ......... PASSED
test_full_suite_clean ............................................... PASSED
test_pr_body_contains_canon_versions_block .......................... PASSED
test_append_canon_idempotent ........................................ PASSED
test_canon_unknown_logs_warn_but_continues .......................... PASSED
test_canon_block_at_pr_body_end_not_top ............................. PASSED
test_existing_block_replaced_not_duplicated ......................... PASSED
test_close_with_na_no_justification_is_blocked ...................... PASSED
test_close_with_linked_artifact_passes .............................. PASSED
test_close_with_one_line_justification_passes ....................... PASSED
test_skill_md_contains_three_question_block ......................... PASSED

18 passed in 3.4s

$ gh pr view 999 --json body --jq .body | tail -10
## Canon Versions
gc-data-v2: 8f3a92c4c6e0d4e1f1c8a7b6e5d4c3b2a1908070
sdlc-framework: v1.1.0

$ grep -A6 "Three.Question" /mnt/c/Projects/tech-dev-agents/.sdlc/skills/complete-story/SKILL.md | head -10
### Step 11 — Three-Question Gate (Medium+ only)
Before closing a Medium+ story, answer THREE questions in features/<story-folder>/close-out.md:
1. canon_gap: <PR_URL_TO_GC_DATA_V2_OR_KB> OR "N/A — <one-line-justification>"
2. scaffold_backport: <PR_URL_OR_LIST> OR "N/A — <justification>"
3. sibling_sweep: <ISSUE_URL_LIST> OR "N/A — <justification>"
N/A WITHOUT a justification is REJECTED.
```

## Escalation contract

Standard 60s directive guard. Escalate to Mark via `needs_info` when:

1. STORY-1011 has not yet shipped (no `.sdlc/PINNED_VERSION` to read) — stub with env-var fallback and leave a `TODO(STORY-1011)` comment; do not block on it.
2. STORY-1001's canon service is not yet wired (`gc-data-v2` SHA source unavailable) — same pattern: fall back to env var + WARN log; do not block.
3. The tautology linter flags a legitimate test in the existing suite (false positive on first scan) — escalate before tightening the patterns; we may need to remove that pattern.
4. The 3-question gate would require Mark to invent a "canon gap" answer for a routine bug fix where there genuinely is none — confirm the "N/A + one-line justification" rule is sufficient and we don't need a fourth escape hatch.
5. Linter run time on a real PR exceeds 30 seconds — escalate for perf budget; CI gates must stay fast.


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

Medium → `1 → 6 → 7 → 8 → Done`.

- Phase 1: this seed.
- Phase 6: `feature-spec.md` covering: the exact text added to `complete-story` (proposed diff), the `close-out.md` schema, the 5 tautology patterns + their regex/AST signatures, the canon-pin block format, the fallback chain for unknown canon SHA.
- Phase 7: RED tests — `tests/tools/test_tautology_lint.py` (the 9+ cases listed), `tests/git_workflow/test_open_pr_canon_pin.py` (the 5 cases listed), `tests/skills/test_complete_story_three_question_gate.py` (the 4 cases listed). Plus a CI workflow dry-run (`act` or documented manual step).
- Phase 8: implement, coordinate the `sdlc-framework` skill PR + bump `.sdlc/PINNED_VERSION`, push the planted-tautology demo PR to prove SC-5, then push the main PR.
- Done: PR merged. Demo PR is closed (its only job was proving the gate works). All 18 tests GREEN. Any dispatch-created PR after rollout has the canon-pin footer.

## Dependencies & sequencing notes

- **STORY-1011 (framework versioning) should ship first** — supplies `.sdlc/PINNED_VERSION` for the canon-pin block. Documented fallback (env var + warn log) if it hasn't.
- **STORY-1001 (canon service) would be cleanest** for the `gc-data-v2` SHA source, but is in Work-Stream A and may land in parallel. Fallback chain: canon service → `GC_DATA_V2_PIN` env var → literal `"unknown"` with WARN log.
- **No dependency on STORY-1009 or STORY-1010** — orthogonal.
- **Coordination PR on `sdlc-framework`:** the `complete-story` skill text change happens upstream. This story merges *after* the framework PR + the pin-bump are both live in `tech-dev-agents`. Without that ordering the local skill content drifts from the pin and STORY-1011's drift check fails.

## Phase 6 — design deliverable breakdown

`features/story-1012-story-close-and-coverage-gates/feature-spec.md` will include:

1. **Skill diff** — full proposed addition to `.sdlc/skills/complete-story/SKILL.md` (Step 11 — Three-Question Gate). Includes the exact prompt text, the link-or-justification rule with examples, and the `close-out.md` output path.
2. **`close-out.md` schema** — YAML frontmatter (`story_id`, `closed_at`, `closer`) + three required answers (`canon_gap`, `scaffold_backport`, `sibling_sweep`) each with `value` (URL or `N/A`) and `justification` (required when `value: N/A`).
3. **Tautology patterns** — for each of the 5 patterns, the AST node shape, an example positive case, and an example negative case (to lock in false-positive avoidance).
4. **Canon-pin block format** — verbatim multi-line string template, position in the body (always at end), idempotency mechanism (look for the `## Canon Versions` heading and replace the block beneath it, else append).
5. **Fallback chain for unknown canon SHA** — pseudo-code for the resolution order; WARN log shape.
6. **Workflow YAML** — full `.github/workflows/test-quality.yml`, trigger config, diff computation strategy (`git diff origin/${{ github.base_ref }}...HEAD -- '**/*.py'`), function-name extraction approach.

## Implementation watch-points

- The diff between PR HEAD and base must run on the actual changed files only; do not lint the entire `tests/` tree on every PR (slow + creates false-positive cascade on legacy tests).
- `_append_canon_versions` must NOT introduce trailing whitespace differences in the PR body; idempotency is byte-exact.
- The `gc-data-v2` SHA resolution must time out fast (1s budget) — never block PR creation for canon resolution.
- The skill change is markdown-only; the "enforcement" is by protocol (the closer reads the skill and acts on it). The `tests/skills/` tests assert the markdown contains the required text — they do not execute the skill.
