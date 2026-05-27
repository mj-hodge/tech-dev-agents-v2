# Seed: STORY-621 — Fix the stale-QUESTION.md needs_info loop

## Overview

| Field | Value |
|-------|-------|
| Mode | bug_fix |
| Scope | small |
| Frontend | false |
| Feature Name | Phase runner stops re-flagging needs_info on a resolved/leftover QUESTION.md |
| Phase Path | 1 (Seed) → 7 (Test Design) → 8 (Implementation) → Done |
| Repo | tech-dev-agents |
| Target Branch | main |
| Status | Seed written 2026-04-25 14:25 UTC |
| Priority | 90 — actively blocking real stories (STORY-575 hit it tonight, took 30 min to clear by hand) |

---

## 1. Idea / Trigger

Tonight at 14:08–14:14 UTC, STORY-575 (advertising-amazon, FBA ingest into ACA Job) entered a needs_info ping-pong:

1. Mark appended his Phase 4 direction to `QUESTION.md` and pushed (`d132768`)
2. POST `/dispatch/resume/STORY-575` succeeded → status `needs_info` → `pending`
3. Devon claimed; Phase 6 (Design) ran for 372 s
4. Phase 6 ended `rc=1` and the journal logged `[DISPATCH] Phase 6 (Design) — agent has a QUESTION, marking STORY-575 needs_info`
5. Story bounced back to `needs_info` despite the QUESTION.md only containing the ALREADY-RESOLVED Phase 4 question + Mark's resolution

The stale-QUESTION.md guard added in STORY-505/528/529 (`_check_for_questions(workdir, story_id, story_folder, phase_start_ts)`) is supposed to prevent exactly this. It wasn't reliable on this run. Either the mtime check didn't fire, the resume-cleanup didn't delete the file before Phase 6 ran, or the `git checkout` of the branch reset the file mtime in a way that made it look fresh.

The system needs a more defensive design that doesn't depend on mtime alone.

## 2. Problem Statement

- **Real production blocker.** STORY-575 was needs_info'd then re-needs_info'd. Required manual intervention (delete file, commit, push, /resume) to break the loop.
- **Root cause is hard to pin** because we don't have a per-story event timeline (STORY-600 territory). Either:
  - `_consume_resumed_question` failed silently (file not deleted before Phase 6 ran)
  - The mtime check at phase end was bypassed (mtime was somehow ≥ phase_start_ts despite the file being from a prior cycle)
  - The agent EDITED `QUESTION.md` during the phase (e.g., reading and re-writing it), updating mtime, making the stale check think it's fresh
- **The current single-signal heuristic (mtime) is too brittle.** Need belt-and-suspenders.

## 3. Scope Classification

**Small.** Two files (`deployment/hermes/sdlc_phase_runner.py` for the runner logic, `tests/deployment/test_phase_runner_question_md.py` for tests). No schema, no API, no cross-repo. Phase path `1 → 7 → 8 → Done`.

## 4. Codebase Context

### Affected files

- **`deployment/hermes/sdlc_phase_runner.py`** — `_check_for_questions` (line 366) and its caller (around line 1914). Plus the resume-cleanup path (`_consume_resumed_question` near line 1727). All three need to be hardened.

### Files NOT to touch

- `dispatch_poller.py` — it's the consumer, not the producer of this state.
- `routes/dispatch.py` — `/needs_info` and `/resume` API contracts unchanged.
- Any test outside `tests/deployment/test_phase_runner_question_md.py` (existing tests stay green).

## 5. The Fix — three layers, defense in depth

### Layer 1: Always delete QUESTION.md at phase START, with a commit

Today the resume-cleanup runs only when `resumed_question_path` is non-None (i.e., the claim was a /resume from needs_info). If the QUESTION.md exists but the row is in normal `pending` state — for any reason — cleanup doesn't fire.

New behavior: at the top of `run_sdlc_phases`, BEFORE Phase 1 of the loop:
1. For each `features/story-<num>-*/QUESTION.md` (and the bare `features/story-<num>/QUESTION.md`):
   - If file exists, `git rm` it and commit with message `chore(STORY-N): clear pre-phase QUESTION.md (stale from prior cycle)`
   - Do NOT push here (the partial-work save logic at phase end pushes everything). The commit is enough.
2. The phase loop starts with no QUESTION.md on disk.
3. If the agent writes a new one DURING a phase, it's unambiguously fresh.

This makes the system robust to ANY upstream mistake (failed resume cleanup, divergent agent worktrees, partial pushes) — the runner always starts clean.

### Layer 2: Tighter mtime check + version marker

Keep the existing `phase_start_ts` mtime guard, but harden it:

1. **Drop the 2-second grace window.** It was added "for test fixtures and timing jitter" but in practice masks real staleness — a checkout completing 1.8 s before phase start is treated as fresh. Replace with strict `mtime >= phase_start_ts`.
2. **Add a version marker to QUESTION.md.** When the agent writes one, prepend a line like `<!-- QUESTION-VERSION: 2026-04-25T14:14:48Z phase=6 agent=devon -->`. The runner reads this on phase end. If the timestamp in the marker is older than `phase_start_ts`, treat as stale regardless of mtime. This is a content-level signal that survives `git checkout` (which can rewrite mtime) and editor-tools (which can touch mtime without changing the question).
3. **If marker missing, fall back to mtime + content hash.** The runner records a hash of QUESTION.md at phase START in `_phase_start_state`. At phase END, if the hash is unchanged, the file wasn't touched — definitely not a fresh question. Skip needs_info routing.

### Layer 3: Audit trail in journal

Whenever `_check_for_questions` decides "this is fresh" or "this is stale," log a structured line with the inputs:
```
{"event": "question_check",
 "story_id": "STORY-575",
 "phase": 6,
 "decision": "stale|fresh",
 "mtime": 1714050514.2,
 "phase_start_ts": 1714050515.4,
 "version_marker_ts": null,
 "content_hash_changed": false,
 "decision_reason": "mtime < phase_start_ts AND content_hash unchanged"}
```

So next time this loops, we can `journalctl ... | grep question_check` and know exactly why the decision went the way it did. Today there's no diagnostic trail at all.

## 6. Out of Scope

- The bigger event-log work (STORY-600). This bug fix is targeted; the event log is the broader observability story.
- Refactoring `_check_for_questions` to be async or moved to a service. Surgical fix only.
- Changing the QUESTION.md authoring contract for agents (i.e., the prompt that asks them to write one). The version marker prepend can be done by the runner itself, not the agent — runner intercepts the write or post-processes.

## Test Criteria

Phase 7 produces `test-design.md` and RED tests at `tests/deployment/test_phase_runner_question_md.py` covering:

| Test | Setup | Expectation |
|---|---|---|
| Stale QUESTION.md from prior cycle is cleared at phase start | Place a QUESTION.md with mtime in the past; call `run_sdlc_phases` | File deleted before Phase 1 runs; commit recorded |
| Fresh QUESTION.md written DURING phase routes to needs_info | Mock SDK to write QUESTION.md mid-phase | `_check_for_questions` returns the content; `/needs_info` POST fires |
| QUESTION.md with version marker timestamp BEFORE phase_start_ts is treated stale | Pre-place QUESTION.md with marker `<!-- QUESTION-VERSION: 2026-04-24T... -->` | `_check_for_questions` returns None (stale per marker) |
| QUESTION.md with version marker timestamp AFTER phase_start_ts is treated fresh | Pre-place QUESTION.md with marker for now+1s | Returns content (fresh per marker) |
| QUESTION.md with no marker, content unchanged from phase start | Snapshot hash at phase_start_ts; same hash at phase end | Returns None (no real change despite mtime touch) |
| QUESTION.md with no marker, content changed from phase start | Hash differs at phase end | Returns content (real new question) |
| Cleanup commit message is correct | Run with stale QUESTION.md present | `git log -1` shows `chore(STORY-N): clear pre-phase QUESTION.md` |
| Audit log line emitted for every check | Run any of the above scenarios | Journal includes a `question_check` JSON line with the right inputs/decision |

All tests are unit-level with mocked filesystem + subprocess. No live agent VM required.

## Validation

After Phase 8 lands + push-code.sh deploys to all 5 VMs:

1. Re-create tonight's scenario manually: dispatch a small story, manually place a stale QUESTION.md on the agent's worktree, observe that the next phase clears it cleanly and DOES NOT re-flag needs_info.
2. Re-run STORY-575 from its current state — should advance through Phase 6/7/8 without bouncing back to needs_info.
3. journalctl shows `question_check` JSON lines on every phase end.

## 9. Dispatch Notes

- Repo: tech-dev-agents
- Branch: `story-621/fix-stale-question-needs-info-loop`
- Scope: small
- Priority: 90 (production blocker)
- Expected runtime: Phase 7 ~15 min, Phase 8 ~30 min
- Implementing agent should: (a) read the existing `_check_for_questions` and `_consume_resumed_question` impls, (b) add the three layers without removing existing safety code, (c) write the audit log JSON line at every check, (d) cover all 8 test cases above before Phase 8.

## Test Criteria

See numbered Test Criteria section above.

## Validation

See numbered Validation section above.
