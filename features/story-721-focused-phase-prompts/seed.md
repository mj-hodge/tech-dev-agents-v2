# STORY-721 — Focused Phase Prompts for the Dispatch Queue

## Overview

| Field          | Value                                                                 |
| -------------- | --------------------------------------------------------------------- |
| Mode           | feature_add                                                           |
| Scope          | medium                                                                |
| Frontend       | false                                                                 |
| Feature Name   | Focused Phase Prompts for the Dispatch Queue                          |
| Phase Path     | 1 (Seed) -> 6 (Design) -> 7 (Test Design) -> 8 (Implementation) -> Done |
| Repo           | tech-dev-agents                                                       |
| Target Branch  | story-721/focused-phase-prompts                                       |
| Status         | Phase 1 (Seed) — drafted                                              |
| Priority       | 85                                                                    |

---

## Idea / Trigger

The dispatch queue (`dispatch_poller.py` + the SDK runner it spawns) currently builds the agent's prompt by **appending a generic SDLC compliance block** to whatever the operator queued, regardless of which phase is actually executing. That compliance block is the same for Phase 1 (Seed) as it is for Phase 8 (Implementation): it lists every required deliverable for the scope, repeats SDLC framework preamble, and trusts the agent to figure out which artifacts are relevant *right now*. The `CLAUDE.md` directives, the framework lessons, and the persona definitions are then re-loaded by the agent itself from disk on every run.

The trigger for this story is direct evidence from a single operator-orchestrated session today (2026-04-26):

- 7 stories were taken from claim to merged PR in roughly 1 hour.
- Each story was executed by a Sonnet subagent invoked directly with a tight prompt: `"read features/story-XXX/seed.md, edit these N files, write these M tests, push and open a PR"`.
- No subagent received the SDLC framework preamble, the full CLAUDE.md, or the per-scope deliverable matrix. They received only the seed plus exact targets.
- Output quality was higher (no scope creep, no unrequested features, no unprompted research phases) and the wall-clock per story was ~5–10 minutes versus the queue's ~30–45 minute baseline.

The same agents, given the queue's generic prompt, regularly:

- Re-execute Phase 2/3 research on stories where the seed already names every file to touch.
- Add unrequested features ("while I was here I also refactored…").
- Burn 30k+ input tokens reciting framework documentation back to themselves before writing a single line of code.
- Mis-target deliverable filenames because they re-derive scope from the SDLC matrix instead of reading the seed.

The improvement is structural: have the queue **assemble a focused, per-phase, per-story prompt** at dispatch time, containing only the context that phase actually needs. The agent stops being a context-gathering loop and starts being an execution loop.

---

## Problem Statement

### 1. Token waste

Every phase invocation today carries:

- The full SDLC compliance block (same for every phase).
- Implicit re-reads of `CLAUDE.md`, `AGENTS.md`, `software-development-guidance.md` (the agent re-loads these because the prompt doesn't tell it which ones matter).
- Re-reads of every prior phase deliverable, even when only one (e.g., `test-design.md`) is needed.

For a Phase 8 implementation run on a medium story, this routinely costs 40k–60k input tokens before the model writes its first line of code. A focused prompt that ships the seed + the test-design + the named target files inline costs ~8k–15k input tokens. **Estimated 60–70% reduction in input tokens per phase run, weighted by phase frequency.**

### 2. Agent over-scoping

Generic prompts are an invitation to over-scope. When the agent is told "you are running Phase 8 on a medium story" without a tight target list, it frequently:

- Promotes the story scope (medium -> large) by deciding it needs a research phase.
- Adds unrequested refactors to neighbouring code it had to read.
- Writes documentation files (`README.md`, `*.md` in repo root) that the seed never asked for.
- Produces feature-spec.md style "design rationale" prose during what should be a code-only phase.

A targeted prompt — "edit these three files, make these tests green, push" — eliminates that surface area entirely because the agent has no ambient license to wander.

### 3. Contrast with direct subagent orchestration

Today's session demonstrated empirically that a Sonnet subagent given:

- the seed,
- a list of files to read,
- a list of files to edit,
- the exact deliverable target paths,

…outperforms the same Sonnet model given the queue's generic prompt on the same story, on every observable axis (tokens, wall-clock, scope discipline, PR cleanliness). The dispatch queue should be doing the orchestration the operator did manually: parse the seed, infer the targets, build the focused prompt.

### 4. Coordination overhead

The queue adds ~20–30 minutes per story in coordination alone (claim, dispatch, wait for SDK boot, retry loop, completion guard, merge gate). That overhead is fixed; the only lever to improve queue throughput is to make each phase invocation faster and tighter. Focused prompts pull that lever.

---

## Scope Classification

**Medium.**

Rationale:

- The change touches the dispatch poller (`deployment/hermes/dispatch_poller.py`), which is production-critical: every queued story flows through it. A regression here halts the entire queue.
- It introduces a new module (`sdlc_phase_runner.py`) that owns prompt construction and is exercised on every dispatch.
- It does **not** alter the SDLC framework, the phase set, the acceptance gates, or the completion guard — those remain unchanged.
- No DB migration, no new external service, no UI changes. Single repo, single subsystem.
- Test design will need both unit tests (prompt construction is pure-function-friendly) and at least one integration test that runs the poller end-to-end against a fixture seed.

A "small" classification would understate the production-criticality of the poller. A "large" classification would invite an unnecessary research phase. Medium is correct.

---

## Codebase Context

### Files that change

- **`deployment/hermes/dispatch_poller.py`** — currently constructs the SDLC compliance block inline (around lines 426–458 in the current revision). This is where the generic prompt is appended today. It needs to delegate prompt construction to the new phase runner module and pass the focused prompt to the SDK subprocess instead of the generic one.
- **`deployment/hermes/sdlc_phase_runner.py`** — **NEW**. Owns the function `build_phase_prompt(story_id, phase, scope, story_folder, base_prompt, pr_branch=None) -> str`. Pure function: takes story metadata + phase number, reads the seed and any prior-phase deliverables that this phase actually needs, returns the focused prompt string. No subprocess work, no I/O beyond filesystem reads of `features/<story-folder>/`.
- **`tech_dev_agents/sdlc_engine.py`** — already exposes `expected_deliverables(scope)` and phase-path resolution. The phase runner should consume this for "what file should this phase produce" rather than re-deriving it. No changes expected here, but a thin helper to expose "prior deliverables this phase reads from" may be added.
- **`features/<story-folder>/seed.md`** (input) — the phase runner reads this for every dispatch and extracts the structured sections it needs (Implementation Target, Codebase Context file list, Out of Scope, Test Criteria).

### Files NOT touched

- `tech_dev_agents/sdlc_engine.py` core logic (scope classification, advance decisions) — out of scope.
- `claude_sdk_tool.py` — the SDK runner — receives the prompt as `-p`; no change needed there.
- `deployment/hermes/bot_server.py`, `teams.py`, `health_server.py` — unrelated.
- `CLAUDE.md`, `AGENTS.md`, `.sdlc/` — the framework itself is untouched.

### Sketch of the approach

1. **Parse the seed** at dispatch time. The `sdlc_phase_runner.build_phase_prompt` call begins by reading `features/<story-folder>/seed.md`. It uses simple section-header parsing (markdown `##` headers) to extract:
   - Overview table (gives scope, phase path, repo, target branch).
   - Codebase Context (names the files the agent will read/edit — this is the gold).
   - Out of Scope (negative constraints).
   - Test Criteria (what success looks like).
   - Optional **Implementation Target** subsection (when present, lists exact files to edit).

2. **Determine what this phase actually needs.** A static `_PHASE_INPUTS` map drives this:

   | Phase | Inputs included in prompt                                              |
   | ----- | ---------------------------------------------------------------------- |
   | 1     | nothing — Phase 1 produces the seed                                    |
   | 6     | seed.md, analysis.md (if present)                                      |
   | 7     | seed.md, feature-spec.md (or specification.md+architecture.md), Codebase Context |
   | 8     | seed.md, test-design.md, Codebase Context (file targets), Test Criteria |
   | 8b    | seed.md, the diff of the implementation commits                        |
   | 11    | seed.md, code-review.md                                                |

   Anything not in the input set for the active phase is **omitted** from the prompt. This is the core saving.

3. **Build a phase-shaped instruction block.** Replace today's one-size-fits-all SDLC compliance block with a phase-specific one:
   - Phase 7: "Write `features/<folder>/test-design.md` first, then runnable tests in `tests/` that fail (RED). Do NOT write implementation code."
   - Phase 8: "Make the tests in `<paths from test-design.md>` pass. Edit only these files: `<list>`. Do NOT add features beyond Test Criteria. When green, push and open a PR."
   - Phase 6: "Produce `features/<folder>/feature-spec.md`. Do NOT write code. Do NOT write tests."

4. **Keep the rework path.** When `pr_branch` is set, the existing rework-dispatch instructions still apply but are likewise focused — only the phase that's being reworked gets re-driven, with its specific input set.

5. **Return the assembled prompt.** `dispatch_poller.dispatch_local` calls `build_phase_prompt(...)` and uses the returned string verbatim as the `-p` argument to the SDK subprocess. The poller itself becomes thinner (it no longer knows about deliverable matrices).

6. **Backward compatibility / kill switch.** Behind an env flag `FOCUSED_PHASE_PROMPTS=1` (default ON in dev, OFF in prod for the first 24h) so we can roll back to the old generic block by toggling the env var without redeploying. Removed once the production bake-in is clean.

---

## Out of Scope

- Changing the SDLC framework itself (phase definitions, deliverable matrix, advance categories) — unchanged.
- Changing **which phases run** for a given scope — phase paths in `sdlc_engine.py` are unchanged.
- Changing acceptance gates or the completion guard — they continue to verify the same set of deliverable files.
- Changing the model policy, the persona system, or the `CLAUDE.md` directives — agents still read those when they need to; the queue just stops re-asserting them inline.
- Changing `claude_sdk_tool.py` — it remains a dumb pipe between the poller and the model.
- Frontend / Ops Console UI changes — none in this story.
- Cost telemetry / token-usage dashboards — a follow-up story will instrument the savings; this story does not add new metrics beyond what the SDK already emits.
- Multi-repo orchestration changes — orthogonal.
- Replacing seed-section parsing with a richer schema (YAML front-matter, structured JSON) — out of scope; markdown-section parsing is sufficient for v1.

---

## Test Criteria

The implementation must satisfy at least the following concrete assertions. All are testable at the unit level except (T5) which is integration.

1. **T1 — Phase 8 prompt omits research/analysis content.** Given a fixture seed for a medium story plus a `test-design.md`, `build_phase_prompt(story_id, phase=8, scope="medium", ...)` returns a string that contains the seed's "Test Criteria" section and the test-design content, but does **not** contain the strings `"research.md"`, `"Phase 2"`, or `"Phase 3"`.

2. **T2 — Phase 7 prompt forbids implementation code.** `build_phase_prompt(..., phase=7, ...)` returns a string containing the literal instruction "Do NOT write implementation code" and includes the seed's Codebase Context but excludes any prior `code-review.md`.

3. **T3 — Token budget is reduced.** For a representative medium-story fixture, `len(build_phase_prompt(..., phase=8, ...))` is at least **50% smaller** than the prompt that the current `dispatch_poller.dispatch_local` produces for the same inputs (measured in characters as a proxy for tokens; assertion uses a recorded baseline from a frozen snapshot fixture).

4. **T4 — File targets propagate from seed to prompt.** When the fixture seed's "Codebase Context" lists files `a.py`, `b.py`, `c.py`, the Phase 8 prompt contains exactly those three filenames in its "Edit only these files" instruction list, and contains no other `*.py` filenames that were not in the seed.

5. **T5 — Integration: poller calls runner and forwards focused prompt.** With `FOCUSED_PHASE_PROMPTS=1`, dispatching a fixture story through `dispatch_poller.dispatch_local` results in the SDK subprocess being invoked with `-p <focused_prompt>` where `<focused_prompt>` matches `build_phase_prompt(...)` byte-for-byte. (Verified by mocking `subprocess.Popen` and asserting on the captured `cmd` arg list.)

6. **T6 — Kill switch restores legacy behavior.** With `FOCUSED_PHASE_PROMPTS=0`, the same dispatch produces the legacy SDLC-compliance-block prompt unchanged. (Snapshot test against a frozen golden file from main.)

7. **T7 — Missing seed is an error, not a silent fallback.** If `features/<story-folder>/seed.md` does not exist, `build_phase_prompt` raises a `SeedNotFoundError` and the poller refuses to dispatch the story (it goes to a `needs_human` state, not into a generic-prompt fallback).

8. **T8 — Rework path preserves PR-branch semantics.** With `pr_branch="story-XXX/foo"`, the focused prompt contains `git checkout story-XXX/foo` and `Push to story-XXX/foo` and does **not** contain "create a new branch".

---

## Validation

How we know this works once it ships:

1. **Dry-run replay.** Before merging, run `sdlc_phase_runner.build_phase_prompt` against the last 20 dispatched stories' seeds (offline, no actual dispatch) and diff the resulting prompts against the legacy prompts. Confirm size reduction is in the 60–70% band and that no instructions critical to the agent (push, PR, deliverable target paths) are dropped.
2. **Shadow dispatch (24h).** With `FOCUSED_PHASE_PROMPTS=0` in production but logging the *would-be* focused prompt alongside the actual prompt for every dispatch. Operator reviews 5–10 to confirm tone/contents look correct and no surprising omissions.
3. **Canary flip.** Set `FOCUSED_PHASE_PROMPTS=1` for the `derrick` agent only (single bot). Observe over 10 dispatches:
   - Wall-clock per phase drops by ≥ 30% on Phase 8 runs.
   - No increase in retry rate, no increase in `needs_human` transitions, no new completion-guard failures.
   - PR diffs do not show unrequested files (no surprise `*.md` in repo root, no refactors outside the seed's file list).
4. **Full rollout.** If the canary is clean for 48h, flip the flag for all four bots (dan, derrick, daisy, devon).
5. **Token telemetry.** Compare `cost_observability` aggregates pre/post rollout (the SDK already emits per-call token counts). Expect a step-function drop in mean input tokens per phase invocation.
6. **Rollback criterion.** Any one of: retry rate up >2x, completion-guard pass rate down >5pp, or operator-reported scope drift on ≥ 2 stories in the canary period -> flip the flag back to 0 and triage.

---

## Dispatch Notes

- **Phase path:** This story explicitly skips Phases 2–5 (no research, no expansion, no analysis, no selection) and goes 1 -> 6 -> 7 -> 8. The trigger is concrete and the design surface is small; research would be ceremony.
- **Phase 6 (Design)** should produce a single `feature-spec.md` covering: the `_PHASE_INPUTS` table, the seed-section parser contract (which headers, what to do on malformed seeds), the prompt template per phase, the env-flag rollout plan, and the rework-path interaction. Opus-default per model policy.
- **Phase 7 (Test Design)** writes `test-design.md` plus runnable RED tests under `tests/deployment/test_sdlc_phase_runner.py` and `tests/deployment/test_dispatch_poller_focused_prompts.py`. Sonnet-default.
- **Phase 8 (Implementation)** creates `deployment/hermes/sdlc_phase_runner.py`, edits `deployment/hermes/dispatch_poller.py` to call it, wires the env flag, and turns all RED tests GREEN. Sonnet-default.
- **Code review (8b)** is required for medium scope.
- **Pre-deploy gate (Phase 11)** must confirm: env flag defaults are correct for prod, the snapshot-baseline fixture is checked in, and a manual smoke dispatch through the canary bot succeeded.
- **Branch:** `story-721/focused-phase-prompts` off `main`.
- **PR title:** `feat(dispatch): focused per-phase prompts (STORY-721)`.
- **Risk callouts:**
  - Production-critical path. Land behind the env flag; do not enable in prod until canary is clean.
  - Seed-parser brittleness — malformed seeds must fail loud (T7), not fall back silently to a generic prompt (which would mask the parser regression).
  - Rework dispatches share code paths with normal dispatches; T8 must pass before rollout.
- **Owner:** dispatch queue / hermes deployment.
- **Estimated effort:** Phase 6 ~1.5h, Phase 7 ~1.5h, Phase 8 ~3h, review + gate ~1h. Total ≈ 7h focused work.
