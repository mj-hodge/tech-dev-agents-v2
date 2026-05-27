# Phase 8 Implementation — STORY-621: Fix Stale QUESTION.md Needs-Info Loop

## Status

**COMPLETE** — 14/14 tests GREEN (0.29s), PR #122 open.

## Commits

| SHA | Message |
|-----|---------|
| `337421a` | `phase 8: STORY-621 defense-in-depth fix for stale QUESTION.md needs_info loop` |

## Changes Made

**File:** `deployment/hermes/sdlc_phase_runner.py`

### Layer 1 — `_clear_stale_questions(workdir, story_id, story_folder)` (new function)

Called unconditionally from `run_sdlc_phases()` before the phase loop starts. Checks both the slug folder (`features/<story_folder>/QUESTION.md`) and the bare folder (`features/story-<N>/QUESTION.md`). If either exists, performs `git rm` + commits with:

```
chore(STORY-N): clear pre-phase QUESTION.md (stale from prior cycle)
```

No-ops cleanly (no commit) when neither file exists.

### Layer 2 — Hardened `_check_for_questions`

New signature: `_check_for_questions(workdir, story_id, story_folder, phase_start_ts, content_hash_at_start=None)`

Three staleness signals in priority order:

1. **Version marker** — parses `<!-- QUESTION-VERSION: ISO-TS phase=N agent=X -->` from file content. If `marker_ts < phase_start_ts` → stale, regardless of mtime (git checkout rewrites mtime, not content).
2. **Strict mtime** — `mtime >= phase_start_ts` required. Dropped the 2s grace window that masked real staleness.
3. **Content hash** — if `content_hash_at_start` matches current hash → stale (file not changed during phase despite mtime touch).

`run_sdlc_phases()` now records the hash at `_phase_start_ts` for each phase and passes it as `content_hash_at_start`.

### Layer 3 — Audit trail

Every call to `_check_for_questions` emits a structured JSON line:

```json
{"event": "question_check", "story_id": "STORY-621", "decision": "fresh|stale",
 "mtime": 1714050514.2, "phase_start_ts": 1714050515.4,
 "version_marker_ts": null, "content_hash_changed": false,
 "decision_reason": "mtime < phase_start_ts AND content_hash unchanged"}
```

Diagnosable via `journalctl ... | grep question_check`.

## Test Results

```
14 passed in 0.29s
```

All 12 RED tests from Phase 7 are now GREEN. 2 pre-existing PASS tests remain GREEN.

## Test Integrity

- [ ] No tests were modified to make them pass
- [ ] Implementation was changed, not the tests
- All modifications are in `deployment/hermes/sdlc_phase_runner.py` only

## PR

[PR #122](https://github.com/hpi-gorillacommerce/tech-dev-agents/pull/122) — open, ready for review.

## Follow-ups

- Deploy to all 5 VMs via `./deployment/vm/push-code.sh all` after PR merge
- Validate with manual scenario: dispatch small story, plant stale QUESTION.md, verify next phase clears it cleanly without needs_info bounce
- STORY-600 (event-log observability) will build on the audit JSON lines this fix emits
