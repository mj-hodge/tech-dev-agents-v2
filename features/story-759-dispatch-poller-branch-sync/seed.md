# STORY-759 — Default Branch Detection in `_ensure_branch` (Hardcoded `main` Breaks `master`-Default Repos)

## Overview
| Field | Value |
|-------|-------|
| Mode | feature_update |
| Scope | small |
| Feature Name | Default-branch detection in dispatch flow |
| Phase Path | 1 → 7 → 8 → Done |
| Repo | tech-dev-agents |
| Frontend | false |

## Problem Statement (CORRECTED 2026-04-29 — actual bug is in sdlc_phase_runner.py, NOT dispatch_poller.py)

**The actual location of the bug:** `deployment/hermes/sdlc_phase_runner.py:2217-2227` inside the `_ensure_branch()` function. The hardcoded literals are:
```python
checkout_main = subprocess.run(
    ["git", "-C", workdir, "checkout", "main"],   # line 2218 — hardcoded "main"
    capture_output=True, text=True, timeout=10,
)
_git_check(checkout_main, "checkout main", story_id)

pull_main = subprocess.run(
    ["git", "-C", workdir, "pull", "--ff-only", "origin", "main"],  # line 2224 — hardcoded "main"
    capture_output=True, text=True, timeout=30,
)
_git_check(pull_main, "pull --ff-only", story_id)
```

When the dispatched repo's default branch is `master` (e.g., `api-retail-target`), `git checkout main` exits 1 with `pathspec 'main' did not match any file(s) known to git`. `_git_check` raises, the dispatch is recorded as `branch_setup_failed`, and the story is failed in 1–2 seconds — never reaching Phase 7.

**2026-04-28/29 incident:** All 11 stories STORY-007 through STORY-017 in `api-retail-target` failed within 2 seconds of claim. Loki/journalctl on dan's VM shows: `git checkout main failed for STORY-017 (rc=1): error: pathspec 'main' did not match any file(s) known to git`. Repeated for all 11 stories. Cause: the original story-740 framework-compliance fix-of-fixes loop didn't surface this bug because tech-dev-agents uses `main`. The bug only manifests on `master`-default repos.

The wrong fix would be to add `git checkout master` as a fallback. The right fix is to **resolve the repo's default branch dynamically** and use it everywhere `main` is currently hardcoded.

## Target User / Use Case
**User:** any agent processing a dispatch in a `master`-default repo (today: `api-retail-target`; future: any non-`main` repo).
**Today:** `_ensure_branch` runs `git checkout main`; on `master` repos this fails immediately with `pathspec 'main' did not match`. Story fails in ~1 second without ever reaching Phase 7.
**After this story:** `_ensure_branch` resolves the repo's actual default branch via `git symbolic-ref refs/remotes/origin/HEAD` (with a `main` → `master` fallback) and uses that branch everywhere `main` is currently hardcoded.

## Success Criteria
1. **SC-1 — Default-branch helper.** A new helper `_resolve_default_branch(workdir) -> str` returns the repo's default branch name. Resolution order: (1) parse `git symbolic-ref refs/remotes/origin/HEAD` → returns e.g. `refs/remotes/origin/master` → strip prefix; (2) if that fails, try `git ls-remote --heads origin main`; (3) if that fails, try `git ls-remote --heads origin master`; (4) if all fail, raise with a message naming the workdir.
2. **SC-2 — `_ensure_branch` uses the resolved branch.** Lines 2218 and 2224 (the two hardcoded `"main"` literals) become the resolved default branch. Both `checkout` and `pull --ff-only` use the same value.
3. **SC-3 — Other hardcoded `main` references audited.** A grep for `"main"` strings inside `sdlc_phase_runner.py` is performed; if any other location passes `"main"` to a git command for the dispatched workdir, it is also fixed. (Lines 941/948/956 in `dispatch_poller.py` referencing `origin/main` for log diffs are caller-side and out of scope unless they break `master` repos similarly.)
4. **SC-4 — Failure observability.** When default-branch resolution itself fails, the failure is recorded with a specific `failure_reason` like `"default_branch_unresolvable: <workdir>"`. The existing `_git_check` failure path for `checkout`/`pull` continues to surface those errors as today (with the actual branch name now correct).
5. **SC-5 — Tested for both `main` and `master` cases.** Unit tests mock `subprocess.run` to return `refs/remotes/origin/main` and `refs/remotes/origin/master` respectively, and assert that `_ensure_branch` uses the correct branch in subsequent git invocations.
6. **SC-6 — Zero regressions.** All existing tests pass. The behavior on `main`-default repos is unchanged.

## Verification Plan

| SC | Command | Expected Output |
|----|---------|-----------------|
| SC-1 | `pytest tests/deployment/test_dispatch_poller_branch_sync.py::test_resolve_default_branch -v` | PASSED |
| SC-2 | `pytest tests/deployment/test_dispatch_poller_branch_sync.py::test_sync_before_launch_runs_fetch_checkout_pull -v` | PASSED |
| SC-3 | `pytest tests/deployment/test_dispatch_poller_branch_sync.py::test_rework_skips_default_branch_sync -v` | PASSED |
| SC-4 | `pytest tests/deployment/test_dispatch_poller_branch_sync.py::test_sync_failure_records_specific_reason -v` | PASSED |
| SC-5 | `pytest tests/deployment/test_dispatch_poller_branch_sync.py::test_sync_idempotent_when_already_clean -v` | PASSED |
| SC-6 | `pytest tests/ -x --ignore=tests/e2e -q` | All tests pass; zero regressions |

## Test Criteria
The branch-sync logic must be regression-protected:
- **Unit tests** mock `subprocess.run` and assert the exact git commands executed in order: `fetch`, `checkout <default>`, `pull --ff-only`.
- **Negative test**: when `git fetch` fails, the test asserts the story is NOT launched and `failure_reason` is set to a specific string identifying the failed step.
- **Rework test**: when `pr_branch` is set, no default-branch sync runs (only the rework checkout).
- **Idempotent test**: when the workdir is already clean and on the default branch, sync is fast (≤ 2 git invocations) and produces no errors.
- All tests deterministic, < 2 seconds total. No live network or VM calls.

## Validation
Phase 8 is NOT complete until ALL of the following are demonstrated in the PR description:

| Step | Command | Pass criterion |
|------|---------|----------------|
| 1 | `pytest tests/deployment/test_dispatch_poller_branch_sync.py -v` | All new tests GREEN |
| 2 | `pytest tests/ -x --ignore=tests/e2e -q` | Full suite passes; zero regressions |
| 3 | Negative-case demo: temporarily make `_resolve_default_branch` return wrong branch, run unit tests | At least one test fails with diagnostic naming branch sync |
| 4 | Code-path inspection: `grep -n 'fetch\|checkout\|pull' deployment/hermes/dispatch_poller.py` shows new sync block exists in `launch_sdk_for_story` before the SDK subprocess invocation | New block present, only fires when `pr_branch == ""` |
| 5 | After deploy: trigger a dispatch on a fresh repo; SSH to the agent and verify the working tree is on `main`/`master` and matches `origin/<default>` | Manual proof in PR body |

## Acceptance Criteria
- [ ] AC-1: New helper `_resolve_default_branch(workdir)` returns `main` or `master` correctly for both repo conventions.
- [ ] AC-2: `launch_sdk_for_story` invokes fetch + checkout + pull --ff-only BEFORE spawning the SDK, when `pr_branch == ""`.
- [ ] AC-3: When `pr_branch` is set (rework), default-branch sync is skipped (existing rework checkout flow unchanged).
- [ ] AC-4: Sync command failures are caught and recorded with a specific failure_reason like `"branch_sync_failed: git fetch returned exit 128"` — pause the story (do not auto-retry into oblivion).
- [ ] AC-5: All sync subprocess calls use `git -C <workdir>` (no `cwd=`) to avoid the bare-repo permission prompt and to match the rest of the file's convention.
- [ ] AC-6: Sync runs only when workdir is found in candidate list (existing logic). When `workdir is None` and the auto-clone fallback is used, sync is skipped (clone already lands on the default branch).
- [ ] AC-7: Idempotent fast-path: when `git status -s` is empty AND HEAD == origin/<default>, sync is a no-op.
- [ ] AC-8: New tests cover all 5 SCs as listed in Test Criteria.
- [ ] AC-9: Logging — every sync invocation prints `[DISPATCH] branch-sync: <repo> on <branch> ✓` (or specific failure) so it's visible in Loki.
- [ ] AC-10: Zero regressions in existing `tests/deployment/test_dispatch_poller*.py` and `tests/test_dispatch_poller*.py`.
- [ ] AC-11: Error/logging AC — when sync fails, the failure log includes the failing git command, exit code, and stderr (truncated to 500 chars). No silent failures.

## Constraints
| Constraint | Value |
|------------|-------|
| Budget | minimal — guardrail logic + tests, no behavior change for happy path |
| Timeline | flexible — but ship before next batch of multi-repo dispatches |
| Scale | n/a |
| Tech | Python 3.12, subprocess only — no GitPython or other deps |

## Performance Requirements
n/a — sync adds ~1–2 seconds per dispatch on a clean cache, which is negligible compared to SDK startup. Idempotent fast-path keeps it fast when already clean.

## Security Constraints
- [ ] No new credentials or auth surface — git auth uses existing token.
- [ ] No new network calls beyond `git fetch origin` (which is already implicit in current rework flow).
- [ ] Sync output (stdout/stderr) is logged but truncated to 500 chars to avoid leaking sensitive paths.

## Operational Lifecycle
- **Configuration changes after deploy?** None.
- **How operators change behavior?** They don't. This is a default-on guardrail.
- **Monitoring?** New `[DISPATCH] branch-sync:` log lines visible in Loki under `{job="hermes-gateway"}`. Failure spike → branch-sync issue.

## Boundaries

| Always Do | Ask First | Never Do |
|-----------|-----------|----------|
| Run sync only when `pr_branch == ""` (regular dispatch) | Whether to sync also for rework dispatches (current scope says no) | Sync for rework — would clobber the PR branch checkout |
| Use `git -C <workdir>` form everywhere (matches existing convention) | Whether to use a thread/asyncio for sync (current scope says synchronous is fine — adds ~1-2s) | Use `cwd=` form (triggers bare-repo prompt per `.sdlc/CLAUDE.md`) |
| Record specific failure_reason on each sync failure | Whether failure should pause vs auto-retry (current scope: pause) | Auto-retry on sync failure — that's exactly the loop we're trying to escape |
| Treat "already clean" as fast-path no-op | Whether to also call `git gc` or housekeeping (out of scope) | Add housekeeping commands |
| Log every sync invocation to stdout (visible in Loki) | Whether to also write to a separate audit file (probably overkill) | Suppress sync output |

## Files to Modify
- `deployment/hermes/sdlc_phase_runner.py` — add `_resolve_default_branch(workdir)` helper near `_git_check` (~line 2015–2050). Update `_ensure_branch` (line 2053): replace the hardcoded `"main"` at lines 2218 and 2224 with the resolved default branch. Also update the `if current and current != "main" and current != "master":` guard at line 2209 to use the resolved branch (or accept both).
- `tests/deployment/test_default_branch_resolution.py` — **new file** with the 6 SC tests.
- `features/story-759-dispatch-poller-branch-sync/test-design.md` — Phase 7 deliverable.
- `.project`, `backlog.md` — tracking.

## Files to NOT Modify
- `deployment/hermes/dispatch_poller.py` — the original seed (now corrected) wrongly named this file. The actual hardcoded-`main` bug is in `sdlc_phase_runner.py:2218` only. The `origin/main..` log-diff references at `dispatch_poller.py:941,948,956` are caller-side and out of scope (they're for PR diff inspection, separate concern).
- The auto-clone fallback path in `dispatch_poller.py:542-548` — fresh clone already lands on default branch; sync unneeded.
- The rework checkout flow (`pr_branch != ""`) — preserve existing behavior. This story is about the regular dispatch path.
- `claude_sdk_tool.py` — out of scope.
- Any agent-side code (`/opt/agent/`) — changes happen in repo, then `push-code.sh` deploys. Don't SSH-patch.

## Done Looks Like

```
$ pytest tests/deployment/test_dispatch_poller_branch_sync.py -v
============================= test session starts ==============================
tests/deployment/test_dispatch_poller_branch_sync.py::test_resolve_default_branch_main PASSED
tests/deployment/test_dispatch_poller_branch_sync.py::test_resolve_default_branch_master PASSED
tests/deployment/test_dispatch_poller_branch_sync.py::test_sync_before_launch_runs_fetch_checkout_pull PASSED
tests/deployment/test_dispatch_poller_branch_sync.py::test_rework_skips_default_branch_sync PASSED
tests/deployment/test_dispatch_poller_branch_sync.py::test_sync_failure_records_specific_reason PASSED
tests/deployment/test_dispatch_poller_branch_sync.py::test_sync_idempotent_when_already_clean PASSED
============================== 6 passed in 0.42s ===============================

$ pytest tests/ -x --ignore=tests/e2e -q
.................................................. (existing tests)
====== ALL PASSED ======

$ grep -n 'branch-sync' deployment/hermes/dispatch_poller.py
[shows the new block in launch_sdk_for_story]

$ gh pr view --web
# PR open, CI green, ready for review.
```

## Escalation Contract

If during Phase 7 or 8 the agent finds:
1. **The default branch detection is more nuanced than expected** (e.g., a repo configures upstream to a fork) → write QUESTION.md with examples, mark needs_info. Do not invent fallback heuristics.
2. **Existing `launch_sdk_for_story` is being refactored or has new shape** → adapt the patch to the current code; don't revert the refactor. Note any conflicts in PR body.
3. **The auto-clone fallback (line 542-548) already does sync** → confirm this and DO NOT duplicate. Update the seed's "Files to NOT Modify" if so.
4. **Synchronous sync adds > 5 seconds latency in tests** → ask before introducing async/threading. Synchronous is the default scope.
5. **A test requires hitting the network** → STOP. All sync tests must mock subprocess. Do not introduce a network-dependent test.

**Default if no story-specific rule fires:** if more than 1 turn is spent guessing, write QUESTION.md and pause.

## Codebase Context
| Aspect | Details |
|--------|---------|
| Affected files | `deployment/hermes/dispatch_poller.py` (lines 528–600 of `launch_sdk_for_story`), new `tests/deployment/test_dispatch_poller_branch_sync.py` |
| Related components | `claude_sdk_tool.py` (caller — does NOT need changes); fleet VMs receive new dispatch_poller via `push-code.sh` after merge |
| Current behavior | Poller picks workdir, launches SDK with whatever branch was last checked out. New dispatches inherit stale branch state from previous ones. |
| Desired change | Poller resets workdir to latest default branch before launching SDK. Failures on sync are observable, not silently retried. |
| Test coverage | New file with 6 tests covering the 5 SCs. Existing dispatch_poller tests must continue to pass. |
| Architecture constraints | Must use subprocess (no GitPython); must use `git -C <workdir>` form; must skip sync for rework dispatches. |

## Out of Scope
- Sync logic for rework dispatches (explicit non-goal — preserve existing behavior).
- Cleanup of stale branches on agent VMs (separate housekeeping story if needed).
- Migrating the auto-clone fallback to use the new helper (keep them parallel for now).
- Frontend / dashboard changes — none required.

## Notes for Implementer
- **Bug location:** `deployment/hermes/sdlc_phase_runner.py:2217-2227` inside `_ensure_branch`. Read that function (starts ~line 2053) before changing anything.
- **Evidence (Loki/journalctl 2026-04-29 01:14-01:17):** `git checkout main failed for STORY-009 (rc=1): error: pathspec 'main' did not match any file(s) known to git` — this exact error fired 11 times for `api-retail-target` stories.
- **`api-retail-target` default branch is `master`.** Verify with: `gh repo view hpi-gorillacommerce/api-retail-target --json defaultBranchRef -q .defaultBranchRef.name` → `master`.
- **`tech-dev-agents` default branch is `main`.** Don't break this case — your tests must cover both.
- **`git symbolic-ref refs/remotes/origin/HEAD`** returns e.g. `refs/remotes/origin/master`. Strip the prefix `refs/remotes/origin/` to get the branch name.
- If `origin/HEAD` symbolic-ref isn't set on the agent's local clone (older clones might not have it), fall back: try `git ls-remote --exit-code --heads origin main`, then `master`. If both fail, raise with a clear error message including the workdir and remote URL.
- The 2026-04-28/29 incident affected api-retail-target STORY-007 through STORY-017. Mark already manually re-enqueued them; they're failing again *because of this exact bug*. **You don't need to re-enqueue or fix individual stories — your fix is what unblocks them.**
- Existing rework code uses `cwd=workdir` in some places (line 981 of `dispatch_poller.py`) — that's a known violation per `.sdlc/CLAUDE.md`, but **don't try to fix it in this PR** — out of scope.

## Correction Log
- **2026-04-29 01:35Z:** Mark's seed initially placed the bug in `dispatch_poller.py` and characterized it as "stale branch carry-over". Loki/journalctl evidence proved the actual bug is the hardcoded `"main"` in `sdlc_phase_runner.py:2218,2224` inside `_ensure_branch`. Seed corrected; scope unchanged (still small).
