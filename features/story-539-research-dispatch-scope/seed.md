# STORY-539 — Research Dispatch Scope

**Scope:** small
**Repo:** tech-dev-agents
**Target Branch:** main

## Context

The SDLC is story-centric. Every story that enters the dispatch queue carries `scope=small|medium|large` which implies implementation — seed.md → tests → code → PR. There is no primitive for "ask an agent a research question and get findings back without code."

The immediate pain: on 2026-04-22 Morris did research in his own SDK session and hit his rate-limit before finishing. He couldn't hand the question to an idle developer agent because the dispatch queue won't accept a story without an implementation plan. Mark hits the same problem every time he wants to explore "how does X work in our codebase" or "what are the tradeoffs of Y" without committing to building it.

Adding a `scope=research` value reuses all the existing dispatch plumbing (queue, claim, status, Teams notifications, acceptance diff, completion gate) and just swaps the phase plan. No new endpoint, no new visual layer, no new pipeline. The agent runs Phase 2 (Research), writes `features/story-XXX-slug/research.md`, and the queue marks the story complete. No PR, no code change, no implementation phases.

This is deliberately a small story — the plumbing is all there. The delta is:

- `PHASE_MAP["research"] = [Phase 2]`
- Scope validation regex accepts `research`
- Completion gate accepts a story as "done" when `research.md` exists on the branch, skips the normal "PR created" check
- Seed is optional for research scope — the prompt IS the question

## Acceptance Diff

The implementation MUST add or modify these files with the must-contain tokens below.

- `deployment/hermes/sdlc_phase_runner.py` — must-contain `PHASES_RESEARCH`, must-contain `"research": PHASES_RESEARCH`, must-contain `/phase-2 story_id=`
- `tech_dev_agents/ops_console/models/responses.py` — must-contain `pattern=r"^(small|medium|large|research)$"` (the scope field regex in `DispatchItem`)
- `tech_dev_agents/ops_console/models/requests.py` — must-contain `research` (same allowlist anywhere scope is validated on inbound dispatch requests)
- `tech_dev_agents/ops_console/routes/dispatch.py` — must-contain `if scope == "research"` in whichever code path constructs deliverable expectations for the completion gate
- `tests/deployment/test_research_scope_phase_map.py` — must-contain `def test_research_scope_has_only_phase_2`, must-contain `def test_research_deliverable_is_research_md`
- `tests/ops_console/test_dispatch_research_scope.py` — must-contain `def test_dispatch_accepts_scope_research`, must-contain `def test_completion_gate_accepts_research_without_pr`, must-contain `def test_research_seed_is_optional`

## Test Criteria

Every assertion below must have a corresponding pytest-level test that fails RED before implementation and passes GREEN after.

1. **Phase plan**
   - `PHASE_MAP["research"]` exists and is a list of exactly one tuple.
   - The tuple's phase_number is 2, name is `"Research"`, deliverable_file is `"research.md"`, prompt starts with `"/phase-2 "` and includes `story_id=`, `repo=`, `story_folder=`.
   - The tuple's `max_turns` is a concrete int (pick 50, matching the other research-style phases).

2. **Scope validation**
   - `POST /dispatch` with `scope="research"` → 201 (currently 422 — `research` isn't in the regex).
   - `POST /dispatch` with `scope="tiny"` or other unknown value → 422 (unchanged).
   - `GET /dispatch/queue` returns the research story with `scope="research"` preserved.

3. **Completion gate for research scope**
   - The completion gate in `routes/dispatch.py` (the code that checks deliverable presence before accepting a `/dispatch/complete/{story_id}` call) MUST NOT require PR creation for `scope=research`. Only the `research.md` file must exist in the story folder on the story branch.
   - `POST /dispatch/complete/STORY-X` with scope=research and `research.md` present → 200.
   - Same call with `research.md` missing → 422, body contains `"research.md"`.

4. **Seed is optional for research scope**
   - The poller's seed-presence check (`_verify_deliverable(seed.md)` at Phase 1 time) MUST be skipped for `scope=research` — Phase 2 IS the starting phase.
   - Instead, the research phase reads the `prompt` field off the dispatch record directly. The prompt IS the research question.
   - Test: dispatch a research story with NO `seed.md` pre-committed → poller enters Phase 2 (not Phase 1) → agent writes `research.md` → completion accepted.

5. **No regressions**
   - `scope=small` still runs Phase 1 → 7 → 8. `scope=medium` still runs 1 → 4 → 6 → 7 → 8. `scope=large` unchanged. Existing contract tests in `test_sdlc_framework_compliance.py` must continue to pass.

## Validation

After implementation lands and is deployed:

1. From Mark's machine, dispatch a research story:
   ```bash
   curl -X POST .../api/dispatch -d '{
     "story_id":"STORY-540",
     "repo":"tech-dev-agents",
     "scope":"research",
     "prompt":"How does our Loki-based quota aggregator reconcile with the SDK'\''s own rate-limit response body? Document the flow, surface any inconsistencies, recommend whether they should be merged into one source of truth. Write findings to features/story-540-quota-source-of-truth/research.md.",
     "enqueued_by":"mark"
   }'
   ```
2. Watch an idle developer agent (daisy or devon) claim it. Confirm in logs: `[DISPATCH] Starting SDLC phases for STORY-540 (scope=research, 1 phase)`.
3. Agent runs Phase 2 only. No Phase 7, no Phase 8. Duration should be 1-3 minutes, not 10+. Cost should be under $3.
4. Completion: `features/story-540-.../research.md` appears on branch `story-540/story-540`. No PR is created (research scope explicitly skips PR). Queue marks STORY-540 completed.
5. Teams notification fires with the research.md link (reuse the existing completion-notification path — no new wiring).
6. If Mark wants the research merged to main, he runs `git merge story-540/story-540` manually. Research scope doesn't create PRs because not every research answer is worth merging.

## Implementation Notes for the SDK Agent

- Keep the delta surgical. Do NOT touch phase-N skill files, personas, or the phase runner's resume logic. This story is plumbing-only.
- The phase-2 skill (`.sdlc/skills/phase-2/SKILL.md`) already produces `research.md`. You don't need to modify it.
- For the completion gate: find the code that builds the "expected deliverables" list per scope. Add a branch for `scope == "research"` that requires only `research.md`.
- For the seed-optional path: locate where Phase 1 is either run or skipped based on deliverable presence. Add a scope-level guard: if `scope == "research"`, always start at Phase 2 and never require a pre-existing seed.md.
- The dispatch `prompt` field already arrives at the phase runner. Make sure it's passed into Phase 2 as the research question (check `_run_phase_sdk` — the prompt IS the question for research scope; for other scopes the prompt is the story description and phases read from seed.md instead).
- Do NOT add a new `/dispatch/research` endpoint. The existing `POST /dispatch` takes scope as a param — just let it be `research`.
- Teams notification: the existing completion-notification path fires on `dispatch/complete`. No wiring change needed; verify in the test that a research completion triggers it.

Phase 7 writes the tests (RED). Phase 8 implements (GREEN). Phase 8 also runs `./deployment/vm/push-code.sh all` to deploy the new PHASE_MAP and poller logic to every agent VM.
