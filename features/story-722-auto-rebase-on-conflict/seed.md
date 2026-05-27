# STORY-722 — Automatic Conflict Resolution (Auto-Rebase on CONFLICTING)

## 1. Overview

| Field         | Value                                                                  |
|---------------|------------------------------------------------------------------------|
| Story ID      | STORY-722                                                              |
| Title         | Automatic Conflict Resolution (Auto-Rebase on CONFLICTING)             |
| Mode          | feature_add                                                            |
| Scope         | small                                                                  |
| Frontend      | false                                                                  |
| Phase Path    | 1 -> 7 -> 8 -> Done                                                    |
| Priority      | 90 (high — directly prevents story failures)                           |
| Owner         | dispatch / hermes poller                                               |
| Created       | 2026-04-26                                                             |
| Status        | Phase 1 (Seed)                                                         |

---

## 2. Idea / Trigger

Tonight (2026-04-26) the dispatch fleet hit three back-to-back PR conflicts:

- PR #143 (STORY-645)
- PR #146 (STORY-701)
- PR on STORY-644 (declarative-state-convergence-pilot)

In each case a previous PR landed on `main` between the time the story branch was created and the time `gh pr create` ran. The new PR opened in `CONFLICTING` / `DIRTY` state. CI cannot run on a conflicting PR, the dispatch poller saw "no merge possible," and the stories either failed outright or sat blocked until a human spawned a rebase subagent. Once spawned, each rebase took **seconds** — it was deterministic mechanical work.

The trigger: this is the second time this week we have lost story throughput to a class of failure that the agent could fix itself. The intervention is small (single helper plus call site), highly leveraged (eliminates a recurring failure mode), and bounded (rebase is well-known; we already have the worktree).

---

## 3. Problem Statement

**Today:** When a story's PR enters `CONFLICTING` / `mergeStateStatus = DIRTY` after Phase 8 creates it, the dispatch system has no recovery path. The poller's `_report_complete` call eventually fires, and either:

1. The PR sits unmergeable until a human notices and rebases manually, **or**
2. CI fails to run, the validation path marks the story `failed`, and we lose the implementation work because no one routes it back through dispatch.

**Tonight's evidence:** 3 of ~12 PRs created tonight (25%) opened DIRTY. The conflicts were 100% in the SDLC tracking files (`.project`, `backlog.md`, `development-tasks.md`) — files that **every** story touches. So the conflict rate is structurally tied to dispatch concurrency: the more agents we run in parallel, the higher the probability that two stories race on tracking files.

**Cost of inaction:** As we scale fleet concurrency past ~3 simultaneous stories, expected PR-conflict rate trends toward >50%. Without auto-rebase, throughput collapses.

**Goal:** When Phase 8 finishes and `gh pr create` returns, automatically detect a `CONFLICTING` PR, run a deterministic rebase against `origin/main` with a known conflict-resolution policy, force-push with lease, and re-check. Only escalate to `needs_info` after 2 rebase attempts fail.

**Non-goal:** General-purpose merge-conflict AI. We are solving the narrow, high-frequency case (tracking-file conflicts during dispatch) — not arbitrary semantic conflicts.

---

## 4. Scope Classification

**Scope: small.**

Reasoning:
- Single file primary touch: `deployment/hermes/dispatch_poller.py` (the orchestrator that already wraps Phase 8 in `_run_and_complete`).
- Two new private helpers (`_check_pr_conflicting`, `_auto_rebase`) plus one call site.
- No new external dependencies. Uses `gh` CLI and `git` already present on the agent VM.
- No schema change, no API change, no UI.
- No persistence: rebase state is ephemeral; retry counter is a local int.
- Rollback is trivial: disable behind feature flag `DISPATCH_AUTO_REBASE_ENABLED` (default `true`, can be toggled to `false` to revert to legacy behavior).

Phase Path: **1 -> 7 -> 8 -> Done**. Skip Phases 2-6 because (a) the design is concrete and small, (b) the implementation pattern (subprocess to git/gh + JSON parsing) is well-established in the codebase, (c) no architectural decisions are pending. Skip Phase 8b because the diff is small and reviewable in the PR.

---

## 5. Codebase Context

### 5.1 Where the work lands

The user's seed brief refers to `deployment/hermes/sdlc_phase_runner.py` — that file does not yet exist. The Phase 8 orchestration lives in `deployment/hermes/dispatch_poller.py` inside the `_run_and_complete` closure (around line 477). That is the runner, and that is where the new logic plugs in.

| Symbol                                      | Location                                                                 | Role                                                |
|---------------------------------------------|--------------------------------------------------------------------------|-----------------------------------------------------|
| `_run_and_complete`                         | `deployment/hermes/dispatch_poller.py:477`                               | Per-story Phase 8 thread; opens PR, calls complete  |
| Branch-slug / PR-number capture             | `deployment/hermes/dispatch_poller.py:618-625` (`gh pr list --head ...`) | Already discovers the new PR after Phase 8          |
| `_report_complete`                          | `deployment/hermes/dispatch_poller.py:204`                               | POSTs `/api/dispatch/complete/{story_id}`           |
| `complete_story` route                      | `tech_dev_agents/ops_console/routes/dispatch.py:303`                     | Validates `pr_number` exists via GitHub API         |
| `_github_pr_exists`                         | `tech_dev_agents/ops_console/routes/dispatch.py:286`                     | Existing GitHub API helper (pattern to mirror)      |

The existing PR-discovery shell-out at lines 618-625 returns `validation_pr_number`. **Auto-rebase fits cleanly between PR discovery and `_report_complete`**: we already have the PR number in scope and the worktree path (`workdir`).

### 5.2 New helper: `_check_pr_conflicting`

```python
def _check_pr_conflicting(pr_number: int, repo: str = "hpi-gorillacommerce/tech-dev-agents") -> bool:
    """
    Return True if the PR is in a conflicting / dirty merge state.

    Uses `gh pr view` rather than the REST API to keep authentication
    consistent with the rest of the poller (single source of truth
    for gh creds on the agent VM).

    Note: GitHub may return mergeable=UNKNOWN immediately after PR
    creation. We poll up to 5x with 2s backoff before treating UNKNOWN
    as a transient and giving up (treating UNKNOWN as not-conflicting).
    """
    import json, subprocess, time
    for attempt in range(5):
        out = subprocess.run(
            ["gh", "pr", "view", str(pr_number), "--repo", repo,
             "--json", "mergeable,mergeStateStatus"],
            capture_output=True, text=True, timeout=30,
        )
        if out.returncode != 0:
            return False  # fail open — do not block completion on gh errors
        data = json.loads(out.stdout or "{}")
        merge_state = (data.get("mergeStateStatus") or "").upper()
        mergeable   = (data.get("mergeable") or "").upper()
        if mergeable == "UNKNOWN":
            time.sleep(2)
            continue
        return merge_state == "DIRTY" or mergeable == "CONFLICTING"
    return False
```

### 5.3 New helper: `_auto_rebase`

```python
TRACKING_FILES = {".project", "backlog.md", "development-tasks.md"}

def _auto_rebase(worktree_path: str, story_id: str, branch: str) -> bool:
    """
    Rebase `branch` in `worktree_path` onto origin/main with a
    deterministic conflict-resolution policy:

      - Tracking files (.project, backlog.md, development-tasks.md):
        take main's version (`git checkout --theirs`). These are
        regenerated on every story; main is authoritative.

      - Implementation files (everything else): take the story
        branch's version (`git checkout --ours` during a rebase
        means the branch being rebased onto, so we use --theirs
        when rebasing — see note below).

    GIT-REBASE NOTE: during `git rebase`, --ours refers to the
    upstream (main) and --theirs refers to the commits being
    replayed (story branch). This is the inverse of merge.
    The implementation MUST account for this.

    Returns True on success (clean tree, branch ahead of main,
    force-with-lease push succeeded), False otherwise.
    """
    import subprocess, os
    def git(*args, check=True):
        return subprocess.run(
            ["git", "-C", worktree_path, *args],
            capture_output=True, text=True, check=check, timeout=120,
        )

    git("fetch", "origin", "main")
    rebase = subprocess.run(
        ["git", "-C", worktree_path, "rebase", "origin/main"],
        capture_output=True, text=True, timeout=300,
    )
    if rebase.returncode == 0:
        # Trivial rebase, just force-with-lease.
        push = subprocess.run(
            ["git", "-C", worktree_path, "push", "--force-with-lease",
             "origin", branch],
            capture_output=True, text=True, timeout=120,
        )
        return push.returncode == 0

    # Conflict path. Walk conflicted paths, apply the policy.
    status = git("diff", "--name-only", "--diff-filter=U", check=False)
    conflicts = [ln for ln in status.stdout.splitlines() if ln.strip()]
    for path in conflicts:
        basename = os.path.basename(path)
        if basename in TRACKING_FILES:
            # Take main's version. During rebase, --ours == upstream (main).
            git("checkout", "--ours", "--", path, check=False)
        else:
            # Take story branch's version. During rebase, --theirs == replayed commit.
            git("checkout", "--theirs", "--", path, check=False)
        git("add", "--", path, check=False)

    cont = subprocess.run(
        ["git", "-C", worktree_path, "rebase", "--continue"],
        capture_output=True, text=True,
        env={**os.environ, "GIT_EDITOR": "true"}, timeout=300,
    )
    if cont.returncode != 0:
        # Abort to leave the worktree in a known state.
        subprocess.run(["git", "-C", worktree_path, "rebase", "--abort"],
                       capture_output=True, text=True, timeout=60)
        return False

    push = subprocess.run(
        ["git", "-C", worktree_path, "push", "--force-with-lease",
         "origin", branch],
        capture_output=True, text=True, timeout=120,
    )
    return push.returncode == 0
```

### 5.4 Call site in `_run_and_complete`

After PR discovery (`deployment/hermes/dispatch_poller.py:618-625`), before `_report_complete`:

```python
# STORY-722: auto-rebase on CONFLICTING.
if validation_pr_number and os.getenv("DISPATCH_AUTO_REBASE_ENABLED", "true").lower() == "true":
    for attempt in range(2):  # max 2 rebase attempts
        if not _check_pr_conflicting(validation_pr_number):
            break
        print(f"[DISPATCH] PR #{validation_pr_number} is CONFLICTING — auto-rebase attempt {attempt + 1}/2", flush=True)
        ok = _auto_rebase(workdir, story_id, branch_name)
        if not ok:
            print(f"[DISPATCH] auto-rebase attempt {attempt + 1} failed for {story_id}", flush=True)
            continue
        # Give GitHub a moment to recompute mergeability.
        time.sleep(5)
    else:
        # Both attempts failed. Escalate.
        _report_needs_info(
            story_id,
            reason=f"auto_rebase_failed: PR #{validation_pr_number} still CONFLICTING after 2 attempts",
        )
        return  # do NOT call _report_complete; let escalation own the state
```

### 5.5 Conflict resolution policy

| File class                  | Examples                                                | Resolution                  | Why                                                            |
|-----------------------------|---------------------------------------------------------|-----------------------------|----------------------------------------------------------------|
| Tracking / generated        | `.project`, `backlog.md`, `development-tasks.md`        | Take **main**               | Regenerated every story; main holds the latest fleet snapshot. |
| Implementation / story work | Everything else (`tech_dev_agents/**`, `tests/**`, etc.)| Take **story branch**       | The story did the work; main has not seen these files.         |
| Cross-class (impl conflicts on a file main also changed) | Rare — same source file edited by two stories | Take **story branch**, log warning | Honest "story branch wins" default; rare enough that human review on the PR catches problems. |

The first two rules cover ~99% of observed conflicts (all 3 cases tonight were tracking-file-only). The third is the safe fallback.

---

## 6. Out of Scope

- **Generic merge-conflict AI / semantic resolution.** We are solving the deterministic dispatch-tracking-file case. Anything that needs reasoning about code semantics escalates to `needs_info`.
- **Detecting conflicts pre-PR-creation.** A pre-flight `git fetch origin main && git merge-base` check could save a round trip, but adds complexity. Defer.
- **Rebasing branches with merge commits in the story history.** Phase 8 commits are linear by convention; if a story branch contains a merge commit we abort and escalate.
- **Cross-PR conflicts (PR A and PR B both open, both conflicting with each other).** Out of scope; the second one through will hit conflict on its rebase and escalate normally.
- **CI re-trigger logic.** Force-push naturally re-triggers CI on GitHub. We do not need explicit re-trigger.
- **Modifying `complete_story` route or the dispatch DB schema.** This story is purely poller-side.
- **Human notification on every rebase.** We log the rebase but do NOT page humans on success — only on the 2-failure escalation.

---

## Test Criteria

All tests live in `tests/dispatch/test_auto_rebase.py` and `tests/dispatch/test_pr_conflict_detection.py`. Use `subprocess.run` mocking via `unittest.mock` and a tmp-path fake git repo for the rebase tests.

1. **Detection — DIRTY state is detected.** `_check_pr_conflicting(123)` returns `True` when `gh pr view` returns `{"mergeStateStatus": "DIRTY", "mergeable": "CONFLICTING"}`. (assertion: function returns True; subprocess called with `gh pr view 123 --repo ... --json mergeable,mergeStateStatus`)

2. **Detection — CLEAN state is not flagged.** `_check_pr_conflicting(123)` returns `False` when `mergeStateStatus == "CLEAN"` and `mergeable == "MERGEABLE"`.

3. **Detection — UNKNOWN is polled.** When `gh` returns `mergeable=UNKNOWN` twice then `MERGEABLE`, `_check_pr_conflicting` polls 3 times total and returns `False`. (assertion: subprocess `mock.call_count == 3`)

4. **Successful rebase, no conflicts.** Given a fake worktree where `git rebase origin/main` exits 0, `_auto_rebase` calls `git push --force-with-lease origin <branch>` exactly once and returns `True`. (assertion: returns True; force-with-lease push observed in mock call list)

5. **Tracking-file conflict — take main.** Simulated rebase produces a conflict in `.project`. `_auto_rebase` runs `git checkout --ours -- .project` (during rebase, `--ours` = main), then `git add .project`, then `git rebase --continue`, then force-pushes. (assertion: `--ours` used for `.project`; final return True)

6. **Implementation-file conflict — take branch.** Simulated rebase produces a conflict in `tech_dev_agents/foo.py`. `_auto_rebase` runs `git checkout --theirs -- tech_dev_agents/foo.py`, `git add`, `--continue`, force-push. (assertion: `--theirs` used for the impl file; return True)

7. **Mixed conflicts — both policies applied in one pass.** Conflicts in both `.project` and `tech_dev_agents/foo.py`. The handler issues `--ours` for the tracking file and `--theirs` for the impl file in the same rebase, then continues. (assertion: both checkout flavors observed; return True)

8. **Rebase-continue fails — abort and return False.** If `git rebase --continue` exits non-zero (e.g. residual conflicts), `_auto_rebase` runs `git rebase --abort` and returns `False`. (assertion: `git rebase --abort` observed; return False)

9. **2-retry cap.** In the integration test of the call-site loop, when `_check_pr_conflicting` keeps returning True and `_auto_rebase` keeps returning False, the loop runs at most **2** rebase attempts. (assertion: `_auto_rebase.call_count == 2`)

10. **Escalation to `needs_info` after 2 failures.** After 2 failed attempts, `_report_needs_info` is called with reason starting `"auto_rebase_failed"` and `_report_complete` is **not** called. (assertion: `_report_needs_info.called and not _report_complete.called`)

11. **Feature flag off — bypass entirely.** With `DISPATCH_AUTO_REBASE_ENABLED=false`, neither `_check_pr_conflicting` nor `_auto_rebase` is invoked, regardless of PR state. (assertion: both mocks have `call_count == 0`)

12. **Force-with-lease (not force).** The push command MUST contain `--force-with-lease` and MUST NOT contain a bare `--force`. (assertion: argv inspection of mock push calls)

Acceptance is RED at end of Phase 7 (helpers do not yet exist) and GREEN after Phase 8.

---

## Validation

### Test invocation

```bash
cd /home/hermes/dev/hpi-gorillacommerce/tech-dev-agents
pytest tests/dispatch/test_auto_rebase.py tests/dispatch/test_pr_conflict_detection.py -v
```

All 12 assertions must pass. Coverage on `dispatch_poller.py` lines added by this story must be `>= 90%`.

### Manual / staging validation

After deploy to the agent VM (hermes):

1. **Synthetic conflict.** Create a throwaway branch off main, edit `.project`, push, open a PR. On main, push a different `.project` change. Trigger the dispatch poller against the throwaway PR. Expect: poller logs `auto-rebase attempt 1/2`, the PR returns to `MERGEABLE`, story completes normally.
2. **Forced-failure path.** Create a synthetic conflict in a non-tracking file with logically incompatible changes that survive the `--theirs` choice (e.g. introduce a syntax error). Confirm the second attempt also fails and the story moves to `needs_info` with the expected reason.
3. **Live observation.** Watch the next ~10 fleet stories. Conflict-rate metric (new log line `auto-rebase attempt`) should fire on ~25% (matches tonight's baseline). Resolution-success rate should be `>= 95%`.

### Telemetry / observability

Add three structured log lines (no new metrics infra needed; the poller is already log-scraped):

- `[DISPATCH] auto-rebase candidate story_id=... pr=... mergeStateStatus=...`
- `[DISPATCH] auto-rebase success story_id=... pr=... attempt=...`
- `[DISPATCH] auto-rebase exhausted story_id=... pr=... attempts=2`

Existing log aggregation will pick these up; no new Application Insights queries required, but a saved query `where message contains "auto-rebase"` is recommended.

---

## 9. Dispatch Notes

- **Dispatch label / priority:** P1 / 90. Same agent class as STORY-336, STORY-337 (poller hardening line).
- **Worktree:** `wt-722` off `main`.
- **Branch name:** `story-722/auto-rebase-on-conflict`.
- **Dependencies:** None blocking. Uses `gh` (already installed on hermes VM) and `git` (idem). Reuses existing GitHub auth context.
- **Risk surface:**
  - Force-push: mitigated by `--force-with-lease` (refuses if remote moved).
  - Wrong-side conflict resolution: mitigated by an explicit allow-list (`TRACKING_FILES`) — anything not on the list defaults to story-branch-wins, which is the "do no harm to implementation work" default.
  - Runaway loop: mitigated by hard cap of 2 attempts and explicit `needs_info` escalation.
  - Concurrent rebases on different worktrees racing on `origin/main`: each push uses lease; loser retries on its next attempt.
- **Rollback:** Set env var `DISPATCH_AUTO_REBASE_ENABLED=false` on the hermes VM and restart the poller. No data migration. Behavior reverts to pre-story state instantly.
- **Suggested model:** Phase 7 — Sonnet (test design, ~30 min). Phase 8 — Sonnet (implementation, ~45 min). No Opus required. Total expected agent cost: low single-digit dollars.
- **Touchpoint summary:**
  - Modify: `deployment/hermes/dispatch_poller.py` (add 2 helpers + 1 call-site block + 1 helper for needs_info escalation if not already present).
  - Add: `tests/dispatch/test_auto_rebase.py`, `tests/dispatch/test_pr_conflict_detection.py`.
  - Update: `.project`, `backlog.md`, `development-tasks.md`, Monday.com card for STORY-722.
- **Handoff to Phase 7:** Test-design agent should mock `subprocess.run`, fake `gh pr view` JSON outputs, and use a tmp directory to seed a real-but-tiny git repo for the rebase-conflict tests (so the actual `git checkout --ours/--theirs` semantics are exercised, not mocked).

---

**End of Phase 1 (Seed) for STORY-722.** Next phase: **Phase 7 (Test Design)** — small-scope path skips 2-6.
