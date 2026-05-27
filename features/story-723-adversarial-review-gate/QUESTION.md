# QUESTION: STORY-728 — What does this rework need to produce?

**Filed by:** Phase 8 agent (Devon, 2026-04-26)  
**Story:** STORY-728 dispatched with `story_folder=story-723-adversarial-review-gate`  
**PR:** #158 (open, on branch `story-723/adversarial-review-gate`)

---

## Situation

STORY-728 was dispatched as Phase 8 targeting the `story-723-adversarial-review-gate` folder. However:

1. **No STORY-728 seed.md exists** — there is no `features/story-728-*/seed.md` or any story-728 folder. The dispatch uses the story-723 folder.
2. **STORY-723 Phase 8 is already complete**: `adversarial_reviewer.py` and `dispatch_poller.py` integration are implemented; all 15 tests pass GREEN; PR #158 is open.
3. **Previous STORY-728 agents** (Daisy and Devon) only wrote `.sdlc` and `.project` tracking changes — no real implementation was produced.
4. **The `.project` already records** `STORY-728 | small | 8 | phase 8 complete — 15/15 tests GREEN, PR #158`.

## What's missing from the test-design.md

The `test-design.md` for STORY-723 lists 13 test cases (TC-1 through TC-13), but the test file on `origin/main` only implements 11 of them. Two are absent:

- **TC-1** — Orchestration: verify `dispatch_poller` calls `run_adversarial_review` for `scope='medium'` after Phase 8 success (mocked). Not in `test_adversarial_reviewer_723.py`.
- **TC-2** — Prompt builder: assert `build_reviewer_prompt()` output contains spec path, test file reference, and all 8 review checks. `build_reviewer_prompt` is imported in the test file but never tested.

**TC-8** (verdict regex table-driven for APPROVE/APPROVE_WITH_CAVEATS/BLOCK) is implicitly covered by TC-3/4/6 but has no dedicated parametrized test.

## The question

**Is STORY-728's purpose to add the missing TC-1 and TC-2 tests (and optionally TC-8)?**

Or was STORY-728 dispatched for a different reason (e.g., a specific bug found in STORY-723's implementation, or a completion-gate rejection on the server side)?

If the answer is "add TC-1 and TC-2": I can implement them. Note that TC-1 (orchestration) is architecturally challenging because `_run_and_complete` is a closure inside `start_story` — the test would likely need to be a structural/source-inspection test or require a small refactor to expose the adversarial-review call path.

Please clarify which work is expected so the agent can proceed without guessing.
