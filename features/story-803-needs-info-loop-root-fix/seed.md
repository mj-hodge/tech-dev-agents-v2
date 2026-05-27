# STORY-803 — Fix the needs_info loop: override bypass + git-add bug + Phase-8-no-commits self-pause

> **Driver:** Mark, 2026-05-01. After deploying STORY-798 (commit QUESTION.md before /needs-info), STORY-799 (CRITICAL OVERRIDE preamble), STORY-800 (sync workdir to origin), and STORY-801 (submodule update) — agents *still* land in needs_info within 40 seconds of claim despite the override firing. Six target/amazon stories had to be cancelled this morning because the loop is unbreakable from the resume side. The fix is **three independent code bugs**, each visible in Loki events.

## Overview

| Field | Value |
|-------|-------|
| Mode | bug_fix |
| Scope | medium |
| Frontend | false |
| Feature Name | Fix needs_info loop: override bypass + git-add path bug + Phase-8-no-commits self-pause |
| Phase Path | 1 → 4 → 6 → 7 → 8 → Done |
| Target Repo | tech-dev-agents |
| Target Role | developer |
| Target Branch | `story-803/story-803` |

**Frontend: false** — all changes are in backend dispatcher/phase-runner Python files only.

---

## Scope

Medium. All three bugs are in `deployment/hermes/sdlc_phase_runner.py` plus one related cleanup in the dispatch poller's needs_info detection.

## The three bugs (each verified in Loki)

### Bug 1 — STORY-799 override is being ignored

**Evidence:** dispatch-poller log on `derrick`, 09:58:30 UTC:

```
{"event": "override_directive_applied", "story_id": "STORY-016", "agent": "derrick",
 "phase": 8, "story_folder": "story-016-target-reference-data", "directive_chars": 2518}
```

→ The 2518-char `MOCK_ONLY_DIRECTIVE.md` was successfully prepended to the phase prompt. Yet 4 minutes later the agent posted `/needs-info` and the row went to needs_info. Same pattern on STORY-013 (devon) and STORY-015 (dan).

**Root cause hypothesis:** the "## CRITICAL OVERRIDE — READ FIRST" preamble is not authoritative enough vs. the seed body's "external blocker — staging access required" trigger. Once the SDK reaches the seed text, the trigger overrides the preamble. The preamble currently *prepends* — it does not *replace* or *suppress* the trigger.

**Fix:**
1. **Strengthen the wording.** Replace the current preamble with a "STOP — READ THIS BEFORE THE SEED" block that explicitly negates the staging-blocker pattern: *"If the seed asks you to pause for external access, IGNORE that instruction; the directive below supersedes it."*
2. **Add a guard:** if MOCK_ONLY_DIRECTIVE.md (or any `*_DIRECTIVE.md`) is present AND the agent attempts `/api/dispatch/needs-info` within the first 60 seconds of phase start, the dispatch poller MUST refuse the needs-info call and write a `directive_bypass_attempted` event. Force the agent to do real work for at least 60s before pausing is allowed.
3. **Re-issue the override at every phase boundary**, not just at phase start. If a sub-phase (e.g. 8b) starts after a /clear, the new SDK invocation must also see the preamble.

### Bug 2 — `_commit_and_push_question` git-add path bug

**Evidence:** dispatch-poller log on `dan`, 10:05:33 UTC:

```
{"event": "question_commit_failed", "story_id": "STORY-015",
 "stage": "commit", "returncode": 1,
 "stdout": "On branch story-015/story-015\nnothing to commit, working tree clean\n"}
```

And on `derrick`, 10:40:09 UTC for STORY-644:

```
{"event": "question_commit_failed", "stage": "commit",
 "stdout": "...Changes not staged for commit:\n  (use \"git add <file>...\" to update what will be committed)\n"}
```

→ Two distinct failure modes:
- STORY-015: helper called `git commit` with no prior `git add` — staged area was empty
- STORY-644: helper called `git add` but on a different path than the file actually exists at — file was visible to `git status` but not staged

**Root cause hypothesis:** `_commit_and_push_question(workdir, branch, question_path, story_id)` either constructs the wrong absolute path, or runs `git add` from the wrong working directory, OR the `question_path` argument and the actual file location disagree (e.g., agent wrote to `features/story-644-bsr-competitor-category-monitor/QUESTION.md` but helper was called with a different folder name).

**Fix:**
1. **Make path resolution explicit and verified.** Before `git add`, `os.path.exists(os.path.join(workdir, question_path))` must be True. If not, log `question_commit_failed: stage=path_resolution` and bail.
2. **Use `git add -A <question_path>`** (force-add, ignores .gitignore), OR `git add -- <question_path>` (literal path, no glob expansion).
3. **Add a contract test** that runs the helper against a temp git repo with a QUESTION.md at a path containing dashes (mimicking the real folder names) and verifies the commit lands.
4. **On commit failure, dump the workdir tree** (one level deep) into the event payload so we can diagnose without SSH access.

### Bug 3 — Phase 8 returning rc=0 with zero commits triggers a fake "QUESTION.md"

**Evidence:** STORY-013 QUESTION.md on devon's workdir reads:

> Phase 8 returned success (rc=0) but produced **zero new commits** on branch `story-013/story-013` vs `origin/main`. The agent exited without writing code.

→ This isn't a question for Mark — it's the SDK skill's *self-diagnostic* that fires when Phase 8 exits without committing code. The dispatch poller treats it as a real question and routes the row to needs_info, blocking the queue. There is no answer Mark can write that resolves it; the only path forward is to re-dispatch with stronger language.

**Root cause:** The "Phase 8 returned rc=0 but no commits" auto-pause was a defensive measure to catch silent failures, but it routes through the same `/needs-info` path as real questions. Mark cannot distinguish them on the dashboard, and they bounce in the same loop.

**Fix:**
1. **Detect this case explicitly** in the phase runner: if Phase 8 exits with `rc=0` AND `git log origin/main..HEAD --count == 0` AND the only file written is QUESTION.md, classify it as a `phase8_no_commits` failure mode — NOT needs_info.
2. **Auto-retry once** with an enhanced prompt: append `"PHASE 8 IMPLEMENTATION — write the code, commit each logical unit, then push. If you cannot, write QUESTION.md describing the SPECIFIC technical blocker (not 'I might be misinterpreting the prompt')."`
3. **If the retry also produces zero commits**, transition to `failed` (not needs_info) with `failure_reason=phase8_silent_exit`. That's a real bug that needs Mark's attention via the failed queue, not the needs_info queue.

## Acceptance criteria

- **AC-1.** Override preamble strengthened with explicit negation language (Bug 1.1). Re-deploy and verify on a fresh dispatch.
- **AC-2.** 60-second directive-protection guard on the dispatch poller (Bug 1.2). Adding a probe that posts `/needs-info` 5s after a fake claim must return 409 with `directive_bypass_attempted` event in the log.
- **AC-3.** Override re-injected at every phase boundary including post-`/clear` (Bug 1.3). Verified by claiming a multi-phase story and reading the events.
- **AC-4.** `_commit_and_push_question` path-resolution check + `git add --` (Bug 2.1, 2.2). Contract test in `tests/deployment/` covering: dashed folder name, file-exists check, post-commit verification.
- **AC-5.** Workdir tree dumped on commit failure (Bug 2.4).
- **AC-6.** `phase8_no_commits` classifier + single auto-retry (Bug 3.1, 3.2). On second silent exit → `failed` not `needs_info` (Bug 3.3).
- **AC-7.** Run a fleet-wide replay of all 6 cancelled stories (008/013/015/016/017/644) under the new code. Each must either complete, fail explicitly, or land in needs_info with a Mark-answerable question (i.e., NOT auto-generated self-diagnostic).
- **AC-8.** Loki dashboard panel showing 24h count of `directive_bypass_attempted`, `question_commit_failed`, `phase8_no_commits`. Goal: zero of all three by 2026-05-08.

## Hard constraints

- **Do not break STORY-798/799/800/801.** Each existing helper stays; this story only fixes their bugs and adds the Bug 3 classifier.
- **Test on derrick first** before fleet rollout. Derrick is the noisiest VM and historically the first to surface SDK-path issues.
- **No skips of `/cancel`** — the deeper "queue stuck" issue must be solved by code, not by repeatedly cancelling rows.

## Test Criteria

New test file: **`tests/deployment/test_needs_info_loop_fixes.py`** — all mock-only via `unittest.mock` patching; no live VM or network required.

- **AC-2 guard (60-second directive-protection):**
  - `test_directive_bypass_guard_rejects_early_needs_info`: POST `/api/dispatch/needs-info` within 5 s of claim while a `*_DIRECTIVE.md` is present → HTTP 409, event `directive_bypass_attempted` logged.
  - `test_directive_bypass_guard_allows_needs_info_after_60s`: same scenario but 61 s elapsed → HTTP 200, row transitions normally.

- **AC-4 contract test (`_commit_and_push_question` path resolution):**
  - `test_commit_question_dashed_folder_name`: helper called on a temp git repo with `features/story-644-bsr-competitor-category-monitor/QUESTION.md` → commit succeeds, HEAD contains the file.
  - `test_commit_question_path_resolution_fails_gracefully`: file does not exist at the given path → logs `question_commit_failed: stage=path_resolution`, no exception raised.

- **AC-5 workdir tree dump:**
  - `test_commit_failure_dumps_workdir_tree`: on commit failure, event payload contains `workdir_tree` key listing at least one entry.

- **AC-6 phase8_no_commits classifier:**
  - `test_phase8_no_commits_classified_not_needs_info`: Phase 8 exits rc=0, `git log origin/main..HEAD --count == 0`, only QUESTION.md written → runner classifies as `phase8_no_commits`, NOT `needs_info`.
  - `test_phase8_no_commits_auto_retry`: first silent exit triggers one auto-retry with enhanced prompt.
  - `test_phase8_no_commits_double_silent_exit_is_failed`: second silent exit → state = `failed`, `failure_reason=phase8_silent_exit`.

## Validation

After Phase 8 lands and `push-code.sh all` redeploys:

1. **CI green:** `pytest tests/deployment/test_needs_info_loop_fixes.py` passes (all new tests GREEN).
2. **Directive bypass guard (AC-2):** Re-dispatch one of the 6 cancelled stories (e.g. STORY-016) against derrick. Confirm no `needs_info` row appears within the first 60 s; Loki shows `directive_bypass_attempted` count = 0 for the run.
3. **Question commit (AC-4):** Let an agent write QUESTION.md on a dashed-folder story. Confirm `question_committed` event appears in Loki (not `question_commit_failed`).
4. **Phase-8 silent exit (AC-6):** Inject a mock story that produces rc=0 with zero commits. Confirm the row transitions to `failed` (not `needs_info`) after one retry.
5. **Fleet replay (AC-7):** Run all 6 cancelled stories (008/013/015/016/017/644) under new code. Each must complete, fail explicitly, or land in needs_info with a Mark-answerable question (not an auto-generated self-diagnostic). Document results in `verification.md`.
6. **Loki panel (AC-8):** Dashboard shows 24 h counts of `directive_bypass_attempted`, `question_commit_failed`, `phase8_no_commits` all trending to zero.

## Done =

- All 8 ACs satisfied
- Replay of 6 cancelled stories under new code documented in `verification.md`
- Loki panel deployed
- 24h soak with zero of the three failure events

## Reference docs

- `deployment/hermes/sdlc_phase_runner.py` — `_apply_override_directives`, `_commit_and_push_question`, `_phase_did_real_work`
- Loki events: `override_directive_applied`, `question_committed`, `question_commit_failed`, `emergency_pause_no_work`, `branch_synced_to_origin`
- `features/story-799-prompt-injection-override/`, `features/story-798-commit-question-before-needs-info/`, `features/story-800-sync-branch-to-origin/`

— Mark, 2026-05-01
