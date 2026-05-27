# Seed: STORY-573 — Dispatch Poller Validation: use `git ls-remote` instead of local remote-tracking refs

## Overview
| Field | Value |
|-------|-------|
| Mode | feature_replace |
| Scope | small |
| Frontend | false |
| Feature Name | Replace `git branch -r --list` with `git ls-remote --heads origin` in the poller's post-completion validation so restricted-refspec clones don't blind-fail valid pushes |
| Phase Path | 1 (Seed) → 7 (Test Design) → 8 (Implementation) → Done |
| Target Repo | tech-dev-agents |
| Target Role | developer |
| Target Branch | `story-573/story-573` |
| Status | Seed written 2026-04-24 — critical, dispatched with priority 100 |

**Frontend: false** — single backend validator change in `deployment/hermes/dispatch_poller.py` + unit tests.

---

## 1. Idea / Trigger

2026-04-24: PR #108 (merged earlier today) added `git fetch origin --quiet --prune` before the branch-existence check in `dispatch_poller.py`. The fix was intended to refresh local remote-tracking refs so the validation could see pushed story branches. **It did not work.**

At 17:39Z devon's session completed STORY-565 with real work (Phase 4 analysis.md, Phase 6 feature-spec.md, Phase 7 test-design.md — all committed and pushed, PR #106 open). The post-completion validation ran:

```
[DISPATCH] SDK exited for STORY-565 (rc=0)
[DISPATCH] VALIDATION FAILED STORY-565: missing pushed branch or new commits (branch=no, commits=no) — work not shipped
```

Direct investigation on the agent VM:

```
$ git config --get-all remote.origin.fetch
+refs/heads/main:refs/remotes/origin/main

$ git fetch origin --quiet --prune  # my PR #108 fix
$ git branch -r --list '*565*'       # returns empty

$ git ls-remote --heads origin '*565*'
7294bd69154fb09ac4e4dfe087c2284f59405f8e	refs/heads/story-565/story-565
```

The agent clones were provisioned with a restricted fetch refspec — `+refs/heads/main:refs/remotes/origin/main` — that only pulls `main`. `git fetch origin` honors that refspec and never brings non-`main` branches into `refs/remotes/origin/*`. My PR #108 `git fetch` call was correct in spirit but ineffective against this refspec.

Result: **every completing story false-fails validation**, auto-retries, and falsely re-dispatches. Today's queue has STORY-552, STORY-555, STORY-565 all in various states of this loop with real pushed work that validation refuses to see.

## 2. Problem Statement

- **Every completing story false-fails validation** on agent VMs whose clone config has a restricted refspec. Observed today on devon's STORY-565, daisy's STORY-552, dan's STORY-555.
- The auto-retry wastes a full SDK invocation per false-fail ($0.50–$2.00 per retry at today's prompt sizes).
- STORY-552 is blocked entirely — every Phase completes, validation false-fails, queue shows no progress. Human confidence in the queue is eroded.
- PR #108 shipped the *wrong fix* for the right-sounding reason. Re-site the validation on a source of truth that bypasses local tracking refs entirely.

## 3. Scope Classification

**Small.** Single file (`deployment/hermes/dispatch_poller.py`) + new unit tests. No schema, no API, no cross-repo. Phase path `1 → 7 → 8 → Done`.

## 4. Codebase Context

### Affected File

**`deployment/hermes/dispatch_poller.py`** — around lines 883–947 (the `validation_passed` block inside the `if rc == 0:` branch of the post-completion path). Two substitutions plus the commits check:

1. **Replace `git branch -r --list '*<slug>*'`** with `git ls-remote --heads origin '*<slug>*'`. `ls-remote` queries the remote directly and is unaffected by the local refspec.

2. **Parse ls-remote output** (`<sha>\t<refname>\n...`) to extract both the SHA and the branch name. First match = primary remote branch. Strip the `refs/heads/` prefix.

3. **For the commits check**, fetch the specific branch explicitly (bypassing the restricted refspec) with `git fetch origin refs/heads/<branch>:refs/remotes/origin/<branch> --quiet --no-tags`. Then `git log --oneline origin/main..origin/<branch> --max-count=1` works.

4. **Preserve the STORY-253 SHA extraction** — after materializing the branch locally, `git rev-parse origin/<branch>` gives the tip SHA directly; or use the SHA from ls-remote (same value).

5. **Preserve the STORY-??? (PR #108) fetch-before-validate** for `origin/main` freshness. The `git fetch origin --quiet --prune` call stays (main can be days stale) but is no longer load-bearing for branch detection.

6. **Preserve the fraud guard semantics** — staleness is still measured against `origin/main..<remote-branch>`, never local HEAD. The change is solely about HOW we learn the remote branch exists.

### Files NOT to touch

- `tech_dev_agents/ops_console/services/dispatch_db_service.py` — validation is poller-side only.
- Any ops-console route.
- No DB schema.
- The agent clone's `git config remote.origin.fetch` — widening the refspec is a SEPARATE, riskier change (would pull dozens of branches at every fetch). This story doesn't touch provisioning.

### Helpers to reuse

- None — the ls-remote parse is a ~5-line helper. Inline it.

## 5. Success Criteria

- [ ] **SC-1:** The poller's validation uses `git ls-remote --heads origin '*<slug>*'` as the source-of-truth for "did the agent push a story branch?". `git branch -r --list` is no longer called in this path.
- [ ] **SC-2:** When ls-remote returns a branch, the poller runs `git fetch origin refs/heads/<branch>:refs/remotes/origin/<branch> --quiet --no-tags` to materialize the branch locally, then the existing `git log origin/main..origin/<branch>` commits check works.
- [ ] **SC-3:** When ls-remote returns empty, `has_branch=False` and the existing fraud-guard behavior is preserved (retry, flag, etc.). No regression.
- [ ] **SC-4:** When ls-remote returns a branch but the commits check shows no commits ahead of main (agent pushed an empty branch), `has_commits=False` and validation fails the story correctly. No regression on the STORY-??? Derrick-fraud scenario.
- [ ] **SC-5:** The STORY-253 commit-SHA extraction still populates `validation_commit_sha` — can use either `ls-remote` output's first field or `git rev-parse origin/<branch>` post-fetch. Must be 40-char SHA.
- [ ] **SC-6:** All six test cases in `tests/deployment/test_dispatch_poller_validation.py` (new) pass mock-only — no real git, no real remote.
- [ ] **SC-7:** The error message on validation failure names the glob and suggests "check that you pushed to a remote branch matching `story-<N>/*`" so agents can self-correct.

## 6. Out of Scope

- **Widening the fetch refspec** on the agent clones. Risky (pulls many branches), and not needed once validation stops relying on local tracking refs.
- **Fixing the completion-reporting bookkeeping** (null `pr_number` / `commit_sha` in completed rows) — that's STORY-560, already dispatched.
- **Fixing PR-detection** (`gh pr list --head '*slug*'`). Different subprocess, orthogonal to this bug.
- **Caching `ls-remote` output.** Validation runs once per completion; an extra 1-2s network round-trip is acceptable.
- **Handling agents whose clone has a DIFFERENT restricted refspec** (e.g. specific tags only). `ls-remote` bypasses this entirely; one fix covers all restricted configs.

## Test Criteria

New file: **`tests/deployment/test_dispatch_poller_validation.py`** — all mock-only via `subprocess.run` patching.

- **A (primary fix):** ls-remote returns a branch + log shows commits → `has_branch=True`, `has_commits=True`, `validation_commit_sha` populated, validation passes.
- **B (empty remote branch):** ls-remote returns empty → `has_branch=False`, validation fails with the no-branch message, retry path triggers.
- **C (branch exists, no commits ahead of main):** ls-remote returns a branch; `git log origin/main..origin/<branch>` empty → `has_branch=True`, `has_commits=False`, validation fails — matches the pre-existing Derrick-fraud guard.
- **D (fetch error):** ls-remote succeeds; `git fetch refs/heads/<branch>...` fails (network hiccup) → validation fails SKIPPED with the existing `VALIDATION SKIPPED` message pattern; no uncaught exception.
- **E (SHA extraction):** verifies `validation_commit_sha` is the 40-char tip of the remote branch.
- **F (call-site order):** asserts that `git branch -r --list` is NOT invoked anywhere in the validation path (prevents regression to the old pattern).

The fixture mocks `subprocess.run` to return deterministic `CompletedProcess` objects keyed on the argv pattern — match against `ls-remote`, `fetch`, `log`, `rev-parse` prefixes.

## Validation

After Phase 8 lands + `push-code.sh --force all`:

1. `python-tests` CI is GREEN.
2. Kick one in-flight story through a full completion cycle (e.g. re-dispatch a tiny Small story) and confirm `VALIDATION OK` appears in the agent's journal instead of `VALIDATION FAILED branch=no`.
3. Verify today's STORY-552 unblocks: re-dispatch STORY-552 post-merge, watch for a real PR this time.
4. Confirm auto-retry rate drops to near-zero for the next 24 hours (heartbeat headline tracks this).
5. Regression: dispatch a faux story that creates a branch with zero commits ahead of main; confirm validation correctly fails with the no-commits message.

## 9. Dispatch Notes

- Target repo: `tech-dev-agents`
- Branch: `story-573/story-573`
- Target role: `developer`
- Scope: `small`
- **Priority: 100** — this is blocking the fleet.
- Expected runtime: Phase 7 ~10 min, Phase 8 ~20 min.
- Context for agent: reuse the subprocess-mock pattern from `tests/deployment/test_phase_runner_frontend_enforcement.py` if it exists; otherwise build fresh.

## 10. Acceptance Diff

Expected diff shape:

- `deployment/hermes/dispatch_poller.py` — +25 to +45 lines, -10 lines: replace the `git branch -r --list` call with `git ls-remote --heads origin`, parse output, add the explicit `git fetch origin refs/heads/X:refs/remotes/origin/X` call, preserve everything else.
- `tests/deployment/test_dispatch_poller_validation.py` — new file, ~180 lines (6 test cases A-F + subprocess-mock helper + fixtures).

Must-contain tokens:
- `git", "ls-remote", "--heads", "origin"`
- `refs/heads/<` or equivalent explicit refspec fetch
- `test_validation_uses_ls_remote_not_branch_r` (or SC-F equivalent)
- `test_validation_passes_when_remote_has_branch_with_commits`
- `test_validation_fails_when_remote_has_no_such_branch`

Must-NOT-contain:
- `git", "branch", "-r", "--list"` — the old pattern should be deleted, not kept as a fallback.
- Edits to `dispatch_db_service.py` or ops-console routes.
- Widening `remote.origin.fetch` anywhere.
