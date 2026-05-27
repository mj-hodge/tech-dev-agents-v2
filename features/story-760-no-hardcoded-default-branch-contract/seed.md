# STORY-760 — Contract Test: No Hardcoded Default-Branch Literals in Dispatch Code

## Overview
| Field | Value |
|-------|-------|
| Mode | feature_update |
| Scope | small |
| Feature Name | Hardcoded-default-branch contract test |
| Phase Path | 1 → 7 → 8 → Done |
| Repo | tech-dev-agents |
| Frontend | false |
| Depends On | **STORY-759 must be merged first** — its `_resolve_default_branch` helper is what callers must use |

## Problem Statement

STORY-759 (in flight as of 2026-04-29) replaces the hardcoded `"main"` literals in `deployment/hermes/sdlc_phase_runner.py:2218,2224` with a dynamic default-branch resolver. That fixes the immediate bug that broke `api-retail-target` (default branch `master`) for STORY-007–STORY-017.

But the *class* of bug — "dispatch flow assumes a repo property that varies across repos" — has no regression guard. Future contributors can re-introduce a hardcoded `"main"` to a dispatch git command and CI won't catch it. The next time someone onboards a `master`-default repo, we'll burn another batch of stories on the same root cause.

This story adds a **contract test** that lints every git-command invocation in `deployment/hermes/*.py` and `tech_dev_agents/ops_console/services/dispatch_db_service.py` for hardcoded branch-name literals. Test fails CI if any non-allowlisted location passes a literal `"main"`/`"master"`/`"develop"`/`"trunk"` string to a git command.

## Target User / Use Case

**User:** future contributors (human or agent) modifying dispatch code paths.
**Today:** they can copy-paste an existing `git checkout main` line into a new code path; CI ships it.
**After this story:** any such addition fails the contract test with a specific diagnostic naming the file, line, and offending literal — pointing them at `_resolve_default_branch` from STORY-759 instead.

## Success Criteria

1. **SC-1 — Lint scope is dispatch git callers.** A new test file `tests/deployment/test_no_hardcoded_default_branch.py` AST-parses (or regex-scans, see SC-3) the following files for hardcoded branch literals passed to git: `deployment/hermes/sdlc_phase_runner.py`, `deployment/hermes/dispatch_poller.py`, `tech_dev_agents/ops_console/services/dispatch_db_service.py`. Other files are out of scope.
2. **SC-2 — Forbidden literals are `main`, `master`, `develop`, `trunk`.** Any `git` subprocess call that passes one of these as a positional branch argument (e.g. `["git", "...", "checkout", "main"]` or `["git", "...", "pull", "...", "origin", "main"]`) fails the test, **unless the location is on an allowlist**.
3. **SC-3 — Detection by AST is preferred; regex acceptable if AST is brittle.** AST: parse the file, walk for `subprocess.run`/`subprocess.Popen` calls whose first arg is a list literal beginning with `"git"`, then check if any string literal in that list matches the forbidden set. Regex fallback acceptable if AST proves too noisy. **No live subprocess execution in the test.**
4. **SC-4 — Allowlist for legitimate uses.** Some `main`/`master` references are correct (e.g., the implementation of `_resolve_default_branch` itself, log-diff commands like `git log origin/main..branch` that are inspecting **caller-side** state of `tech-dev-agents` specifically). Allowlist is a list of `(file, line_range, reason)` tuples in the test module. Each allowlist entry must have a `reason:` string like `"STORY-759: fallback chain — fine because it's the resolver itself"`. Adding a new allowlist entry is allowed but requires the reason field.
5. **SC-5 — Failure diagnostic.** When the test fails, the message names: file, line number, the offending git argv list (truncated to 200 chars), and a one-line remediation hint pointing to `_resolve_default_branch`. Bare `AssertionError` is rejected.
6. **SC-6 — Negative case.** A demonstrable proof in the PR body: temporarily add a `subprocess.run(["git", "checkout", "main"])` line to `dispatch_poller.py`, run the test, see it fail with the diagnostic. Revert.
7. **SC-7 — Zero regressions.** All existing tests pass. The contract test must pass on current `main` (post-STORY-759 merge). If it fails on current `main`, either the allowlist needs a legitimate entry OR STORY-759's fix is incomplete — STOP and write QUESTION.md, do not paper over.

## Verification Plan

| SC | Command | Expected |
|----|---------|----------|
| SC-1 | `pytest tests/deployment/test_no_hardcoded_default_branch.py::test_scope_covers_three_files -v` | PASSED |
| SC-2 | `pytest tests/deployment/test_no_hardcoded_default_branch.py::test_main_literal_in_git_argv_is_forbidden -v` | PASSED |
| SC-3 | `pytest tests/deployment/test_no_hardcoded_default_branch.py::test_no_subprocess_run_in_test -v` | PASSED (asserts the test file itself doesn't shell out) |
| SC-4 | `pytest tests/deployment/test_no_hardcoded_default_branch.py::test_allowlist_entries_have_reasons -v` | PASSED |
| SC-5 | `pytest tests/deployment/test_no_hardcoded_default_branch.py::test_diagnostic_includes_file_line_argv_hint -v` | PASSED |
| SC-6 | Demonstrate negative case: `python -c "import subprocess; subprocess.run(['git', 'checkout', 'main'])"` is added to `deployment/hermes/dispatch_poller.py`, then run the contract test → FAILS with diagnostic. Revert. | Test fails with named-violation message; revert restores GREEN |
| SC-7 | `pytest tests/ -x --ignore=tests/e2e -q` | All tests pass; zero regressions |

## Test Criteria

- **Static analysis only.** No subprocess invocations during the test. No real git commands. No file modification.
- **Fast.** Full test file completes in < 1 second.
- **Hand-coded fixture proof for the negative case** in the test (a small inline string of fake source code containing a violation, parsed and asserted on). This proves the matcher works without modifying the real source.
- **Real-source assertion.** A second test loads the actual `sdlc_phase_runner.py` etc. and asserts no violations exist. This is what catches future regressions.
- All assertion messages name the location and remediation. No `assert False` without context.

## Validation

Phase 8 is NOT complete until ALL demonstrated in PR body:

| Step | Command | Pass criterion |
|------|---------|----------------|
| 1 | `pytest tests/deployment/test_no_hardcoded_default_branch.py -v` | All ≥ 5 tests GREEN |
| 2 | `pytest tests/ -x --ignore=tests/e2e -q` | Full suite GREEN; zero regressions |
| 3 | Negative-case demo (SC-6 above): inject violation, observe test fail with diagnostic, revert | Fail message names file/line/argv; revert restores GREEN |
| 4 | Allowlist audit: `grep -n "allowlist\|ALLOWLIST" tests/deployment/test_no_hardcoded_default_branch.py` | All entries have `reason` field; entries without reasons fail SC-4 |
| 5 | Run on a hypothetical "what if STORY-759 hadn't shipped" — temporarily revert STORY-759's `_resolve_default_branch` to hardcoded `"main"` and confirm the contract test catches it. Revert. | Contract test catches the regression |

## Acceptance Criteria

- [ ] AC-1: New file `tests/deployment/test_no_hardcoded_default_branch.py` exists and has ≥ 5 tests covering SC-1 through SC-7.
- [ ] AC-2: Test scans the three files named in SC-1 (no others). Adding a new file to scan requires a new test entry.
- [ ] AC-3: Forbidden literals set is `{"main", "master", "develop", "trunk"}` (constant in the test file).
- [ ] AC-4: Allowlist has at minimum the entries needed for STORY-759's `_resolve_default_branch` fallback chain (it legitimately tries `main` then `master`). Each entry includes `reason`.
- [ ] AC-5: When a violation is added, the test fails with a message including: filename, line number, the git argv list (≤ 200 chars), and `→ use _resolve_default_branch(workdir) instead`.
- [ ] AC-6: AST parser handles both `subprocess.run([...])` and `subprocess.Popen([...])` and any other `subprocess.*` calls receiving a list literal as the command.
- [ ] AC-7: Test does NOT execute git or any subprocess; AST/regex only. Confirmed by SC-3 self-check.
- [ ] AC-8: Test runs in < 1 second.
- [ ] AC-9: Negative-case demo recorded in PR body verbatim.
- [ ] AC-10: Existing test suite passes with zero regressions.
- [ ] AC-11: Error/logging AC — when the test fails, the diagnostic must be machine-parseable enough that `grep "violation" pytest.log` yields the (file, line, literal) tuple. No silent assertion errors.

## Constraints

| Constraint | Value |
|------------|-------|
| Budget | minimal — pure-static-analysis test |
| Timeline | flexible — but ship within 1 week of STORY-759 merging so the guard is in place before the next master-default repo onboards |
| Scale | n/a — test runs on every PR |
| Tech | Python 3.12, `ast` stdlib (preferred) or `re` stdlib (fallback). NO new deps. |

## Performance Requirements

n/a — pure CPU-bound static check on three small Python files. Must run < 1s.

## Security Constraints

- [ ] No subprocess execution; no live git commands.
- [ ] No file modification; read-only AST parsing.
- [ ] Test does not import the modules under inspection at runtime — parses them as text. (Importing `dispatch_poller.py` in a test environment can fail because it expects systemd context.)

## Operational Lifecycle

- **Configuration changes after deploy?** None — this is a CI gate.
- **How operators change behavior?** They don't.
- **Monitoring?** CI is the monitor. Test failure on a PR = PR blocked from merge.

## Boundaries

| Always Do | Ask First | Never Do |
|-----------|-----------|----------|
| AST-parse the three named files for git subprocess calls | Whether to extend the scan to more files (e.g., morris/*.py) — current scope is dispatch only | Run subprocess in the test |
| Fail with a specific diagnostic naming file/line/literal/hint | Whether to fail on `origin/main..` log-diff references at `dispatch_poller.py:941,948,956` (currently allowlisted as caller-side log inspection of tech-dev-agents) | Allowlist a violation without a `reason` field |
| Treat allowlist as data, not magic | Whether `develop`/`trunk` should be detected — yes for forward-protection (lots of teams use those) | Allowlist by file path glob — be specific (file, line range) |
| Self-check that the test file itself doesn't subprocess.run | Whether to also check tech-dev-agents own ops console code for the same pattern (out of scope for this story; see SC-1) | Hardcode line numbers — use literal-match-and-validate so refactors don't immediately break the test |
| Wait for STORY-759 to merge before opening this PR (its fixes must be in place) | Whether to extend the test to also scan tracker docs / yaml configs (probably overkill) | Open this PR if STORY-759's fix is not yet on main — test will fail incorrectly |

## Files to Modify

- `tests/deployment/test_no_hardcoded_default_branch.py` — **new file**, the contract test.
- `features/story-760-no-hardcoded-default-branch-contract/test-design.md` — Phase 7 deliverable.
- `.project`, `backlog.md` — tracking.

## Files to NOT Modify

- `deployment/hermes/sdlc_phase_runner.py` — STORY-759 already fixed it. Don't refactor further.
- `deployment/hermes/dispatch_poller.py` — out of scope.
- `tech_dev_agents/ops_console/services/dispatch_db_service.py` — read-only target of the lint.
- Existing `tests/deployment/test_*.py` — don't modify; new file only.
- `.sdlc/` submodule — out of scope.

## Done Looks Like

```
$ pytest tests/deployment/test_no_hardcoded_default_branch.py -v
============================= test session starts ==============================
test_scope_covers_three_files PASSED
test_main_literal_in_git_argv_is_forbidden PASSED
test_no_subprocess_run_in_test PASSED
test_allowlist_entries_have_reasons PASSED
test_diagnostic_includes_file_line_argv_hint PASSED
test_negative_case_inline_fixture PASSED
============================== 6 passed in 0.34s ===============================

$ pytest tests/ -x --ignore=tests/e2e -q
ALL PASSED

# Negative-case proof:
$ python -c "
import re
p='deployment/hermes/dispatch_poller.py'
s=open(p).read()
open(p,'w').write(s + '\n# TEST INJECTION\nsubprocess.run([\"git\", \"checkout\", \"main\"])\n')
"
$ pytest tests/deployment/test_no_hardcoded_default_branch.py -v 2>&1 | grep violation
E  Violation: deployment/hermes/dispatch_poller.py:NNN passes literal 'main' to git argv ['git', 'checkout', 'main']. → use _resolve_default_branch(workdir) instead
$ git checkout deployment/hermes/dispatch_poller.py  # revert
```

## Escalation Contract

1. **STORY-759 has NOT merged yet** → STOP, write QUESTION.md, mark needs_info. Do not paper over by allowlisting STORY-759's not-yet-fixed locations. The PR depends on 759.
2. **AST parsing of `dispatch_poller.py` is impractical** (e.g., dynamic call construction) → fall back to regex. Document the fallback in test code comment.
3. **Allowlist grows beyond ~5 entries** → that's a smell; ask Mark before adding more. Real legitimate entries should be rare.
4. **The test would require modifying the real source files to test against** — STOP, use inline string fixtures instead. The real source must remain untouched by the test.
5. **Existing tests fail after adding this contract** — that's a real bug; report it. Do not delete tests to make them pass.

**Default if no story-specific rule fires:** if more than 1 turn is spent guessing, write QUESTION.md and pause.

## Codebase Context

| Aspect | Details |
|--------|---------|
| Affected files | `tests/deployment/test_no_hardcoded_default_branch.py` (new) |
| Reads (does not modify) | `deployment/hermes/sdlc_phase_runner.py`, `deployment/hermes/dispatch_poller.py`, `tech_dev_agents/ops_console/services/dispatch_db_service.py` |
| Related stories | STORY-759 (the fix this guards against regressing); STORY-007–STORY-017 (the original incident victims); STORY-740 (similar contract-test pattern, good reference) |
| Current behavior | No regression guard. Hardcoded `main` can be reintroduced silently. |
| Desired change | CI fails any PR that adds a hardcoded `main`/`master`/`develop`/`trunk` to a dispatch git command (with explicit allowlist for legitimate cases). |
| Test coverage | New test file with ≥ 5 assertions; full project suite continues GREEN. |
| Architecture constraints | Pure-Python static analysis. No subprocess. < 1s. |

## Out of Scope

- Extending the lint to other directories (`morris/`, `tools/`, `scripts/`) — separate story if needed.
- Linting Bash scripts for the same pattern — separate story.
- Auto-fixing violations (replacing `main` literals with `_resolve_default_branch()`) — manual fix only; the test's job is to flag, not to rewrite.
- Lint for hardcoded GitHub org names, hardcoded `pyproject.toml` paths, etc. — file as STORY-761 if motivated.

## Notes for Implementer

- **Hard dependency:** STORY-759 must be on main BEFORE you start Phase 7. Run `git log --grep "STORY-759"` on main to verify before claiming this story.
- Reference STORY-740's `tests/ops_console/test_dispatch_contract.py` as a pattern for AST-based contract tests in this repo.
- The forbidden-literal set `{"main", "master", "develop", "trunk"}` is intentionally conservative. If a future repo uses a stranger convention (e.g., `production`), the contract test will not catch it — that's acceptable; we'll extend the set when it matters.
- Allowlist entries should look like: `("deployment/hermes/sdlc_phase_runner.py", (2010, 2050), "STORY-759: _resolve_default_branch fallback chain")`. Line ranges, not glob patterns.
- The negative case (SC-6) does NOT need to actually edit the source file — use an inline fixture string for the unit test, AND document the manual demonstration in the PR body.
- This is the same archetype as STORY-740's contract test. Keep it small and focused.
