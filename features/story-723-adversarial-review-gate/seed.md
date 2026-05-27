# Seed: STORY-723 — Post-Phase-8 Adversarial Test Coverage Review Gate

**Story:** STORY-723
**Date:** 2026-04-26
**Author:** Derrick (tech-agent-derrick@gorillacommerce.co)
**Branch:** story-723/adversarial-review-gate (to be created)

---

## 1. Overview

| Field          | Value                                                                  |
|----------------|------------------------------------------------------------------------|
| Story ID       | STORY-723                                                              |
| Title          | Post-Phase-8 Adversarial Test Coverage Review Gate                     |
| Mode           | feature_add                                                            |
| Scope          | medium                                                                 |
| Frontend       | false                                                                  |
| Phase Path     | 1 → 6 → 7 → 8 → Done                                                   |
| Priority       | 85                                                                     |
| Owner          | tech-dev-agents fleet (orchestrator persona)                           |
| Depends on     | STORY-006 (sdlc_engine), STORY-029 (PR format), STORY-253 (commit gate)|
| Blocks         | none currently — quality gate, not feature blocker                     |

---

## 2. Idea / Trigger

On the night of 2026-04-25 / morning of 2026-04-26, a manual adversarial pass over three freshly merged Phase-8 deliverables surfaced a class of failure that our existing pipeline cannot detect: **tests pass and CI is green, but the implementation is wrong, missing, or trivially bypassed**. Specifics:

### STORY-701 — Dispatch Claim Heartbeat (taxonomy + DB write)

- Spec (Phase 6) listed **8 failure taxonomy categories** the `fail()` path had to emit.
- Implementation shipped **6** of the 8. `needs_info_unanswered` and `agent_died` were not present anywhere in `dispatch_poller.py` or the SDK tool.
- All **37 tests went GREEN**. CI passed. Morris's PR review approved.
- The "DB write happens on fail()" test was **pure static analysis** — it imported the module, asserted the function name was bound, and checked that the string `"UPDATE dispatch_items"` appeared in the source. It never instantiated a mock DB, never invoked `fail()`, never asserted a row mutation. A no-op `def fail(...): pass` would have passed it.

### STORY-720 — Codex Review Severity & Debt Log

- `_is_duplicate_issue(finding, existing_issues)` had a logic inversion: it returned `True` whenever **any** open debt issue existed on the repo, regardless of finding identity. Net effect: on any repo that already had a single P2 debt issue, all subsequent P2 findings would be silently dropped.
- The idempotency test ("re-running review for the same finding does not file a duplicate issue") used a **hardcoded synthetic finding ID** (`"finding-test-001"`) that the production hash function could never produce. The test's "no duplicate filed" assertion was technically true, but for the wrong reason — the lookup never matched, so the dedup branch was never exercised.
- Again: tests GREEN, CI green, PR approved.

### Pattern

In both cases the gap was not detectable from the test results alone. It required a skeptical second reading by something whose **goal is to find the gap, not confirm the green check**. The current pipeline has no such step. Phase 8b (code review) checks code style and architecture, not adversarial test coverage. Morris's PR review checks merge-readiness, not whether tests actually exercise the spec.

---

## 3. Problem Statement

There is a structural gap between **"tests pass"** and **"implementation is correct."** The Phase 8 gate trusts test results as proof of correctness. The 701 and 720 incidents demonstrate that a sufficiently motivated (or careless) implementation can ship green-CI code that:

1. **Omits spec requirements** (701: 2 of 8 taxonomy categories absent; tests covered the 6 that were implemented).
2. **Substitutes static analysis for behavioral testing** (701: source-string match instead of mock-DB invocation).
3. **Constructs tests that cannot exercise the bug they purport to guard** (720: hardcoded ID that can never collide with production hashes).
4. **Inverts conditional logic in helpers** that no test exercises with realistic fixtures (720: `_is_duplicate_issue` returns true on any open issue rather than matching ID).

The shared property: a test suite written by the same agent that wrote the implementation will share the same blind spots. We need an **independent, adversarial reader** between Phase 8 completion and PR-merge that has read access to (a) the spec, (b) the implementation diff, and (c) the test files, with an explicit mandate to **find what's missing or fake**.

We also need this to be **automated and mandatory**, not an after-the-fact human spot check, because the failures already escaped both Morris's review and the human-in-the-loop merge step.

---

## 4. Scope Classification

**Medium.** Justification:

- Touches `sdlc_phase_runner.py` (or equivalent orchestrator entry point) to inject a new mandatory step between Phase 8 completion and the "ready for merge" state — **orchestration change, not infra change**.
- Introduces a new reusable subagent prompt template (the skeptical reviewer) — **prompt-engineering surface, but no new persona file**.
- Touches PR-state plumbing (post comments, transition states, dispatch fix tasks) — **already exists for code-review and ops-review steps; we reuse the same plumbing**.
- No DB schema change. No new external service. No frontend.
- No new phase number — this is a **mandatory step within Phase 8**, not a new phase. (Open question for Phase 6: should it be renamed Phase 8c? See §5.)
- Covered by Phase Path 1 → 6 → 7 → 8 → Done. Skips Phase 2/3/4/5 (no research or expansion needed; the design space is well-bounded by existing review-step patterns) and Phase 9/10 (not large/new). Includes Phase 6b/c/d only if design surfaces security/UX/ops concerns; default skip.

---

## 5. Codebase Context

### 5.1 Where the new step lives

Primary integration point: `tech_dev_agents/sdlc_engine.py` — the canonical phase-path resolver. Specifically, the transition from Phase 8 (`COMPLETED`) → next phase needs to route through a new mandatory step before `Done` is emitted.

Secondary integration point: whichever module actually invokes the PR-create / merge-ready transition. Based on existing structure, this is downstream of `sdlc_engine` in the orchestrator runner. Phase 6 must locate the exact call site; candidates to investigate:

- `tech_dev_agents/sdlc_engine.py` — phase-path enum and advance-category logic. The new step needs an entry in the canonical Medium phase path.
- The dispatch poller (`deployment/hermes/dispatch_poller.py`) — the loop that picks up "Phase 8 complete" stories and currently transitions them to Done. This is where the new gate must run.
- Existing review-step patterns: Phase 8b (`code-review.md`), Phase 6b/c/d (`security-review.md`, `ux-review.md`, `ops-review.md`). These are precedent for "spawn a subagent, write a markdown file, gate on its output." Mirror that pattern.

### 5.2 The reviewer prompt template

Stored at `tech_dev_agents/prompts/adversarial_review.md` (new file). The template instructs a Sonnet-3.5+ subagent with a focused, skeptical mandate. The prompt MUST cover at minimum:

1. **Spec-vs-implementation completeness check.** Read `feature-spec.md` (Medium) or `specification.md` + `implementation-plan.md` (Large). Enumerate every concrete requirement (numbered list, tables, "must" / "shall" language). For each, locate the implementing code path. **Flag every requirement with no corresponding code as CRITICAL.** This is the defense against 701-style omissions.

2. **Behavioral vs. static test discrimination.** For each test file, classify each test as one of:
   - **Behavioral**: instantiates the SUT, calls it with inputs, asserts on outputs / mock interactions / state mutations.
   - **Static**: imports the module, asserts symbol existence, regex-matches source code, or only checks structural properties (signatures, type hints).
   Tests classified as static where the spec requires behavioral coverage (DB writes, API calls, state transitions, idempotency) are CRITICAL. This is the defense against 701-style fake DB tests.

3. **Test fixture realism.** For tests that use synthetic IDs / hashes / fixtures, check whether the fixture could plausibly be produced by the production code path. A finding ID that the production hash function cannot generate is a HIGH finding (potentially CRITICAL if it is the only test guarding a specific behavior, as in 720).

4. **Exception-path coverage.** Every `raise` and every `except` clause in the implementation must have at least one test that traverses it. Untested exception paths are HIGH.

5. **Boundary conditions.** For numeric inputs (counts, sizes, timeouts) verify zero, one, max-1, and overflow tests exist. For collections: empty, single, many. For strings: empty, whitespace, unicode, max-length. Missing boundary tests are MEDIUM.

6. **Idempotency and concurrency.** If the implementation has a "happens once" guarantee (dedup, debounce, single-write), the test suite must exercise the second invocation path with realistic state, not a synthetic fixture. Defense against 720-style false-pass idempotency tests. This is HIGH-to-CRITICAL depending on blast radius.

7. **Real DB / API write verification.** If the spec says "writes to dispatch_items" or "calls GitHub API," the test must use a mock that records the call (or a test DB that can be queried post-test) and assert on it. Source-grep tests are CRITICAL gaps.

8. **Negative-space audit.** What did the spec say that the diff does NOT touch? List spec sections with no diff hunks and explain why (justified omission vs. missed work).

The reviewer outputs a structured markdown file: `features/<story-folder>/adversarial-review.md`.

### 5.3 Reviewer output format

```markdown
# Adversarial Review: STORY-XXX

## Verdict
{APPROVE | APPROVE_WITH_CAVEATS | BLOCK}

## Findings

### CRITICAL
- [C-1] <one-line summary>
  - Location: <file:line or spec-section>
  - Evidence: <quote from impl/test/spec>
  - Required fix: <concrete action>

### HIGH
- [H-1] ...

### MEDIUM
- [M-1] ...

### LOW
- [L-1] ...

## Coverage Matrix
| Spec Requirement | Implementing Code | Test(s) | Test Type |
|---|---|---|---|
| ... | ... | ... | behavioral / static / missing |
```

### 5.4 Output parsing

The orchestrator parses the verdict line and counts findings under each severity heading. Regex:

- `^## Verdict\s*$\n^(APPROVE|APPROVE_WITH_CAVEATS|BLOCK)\b` (multiline)
- `^### CRITICAL$\n` followed by `^- \[C-\d+\]` lines until the next `^### ` header.

Counts and verdict are stored on the story state record (or in `.project` Phase Routing for the story).

### 5.5 Severity-gating policy

| Severity | PR action                                                                 | Story state                                |
|----------|---------------------------------------------------------------------------|--------------------------------------------|
| CRITICAL | Post all findings as PR review comments, request changes, **block merge** | Re-open Phase 8 with `required_fixes` payload |
| HIGH     | Post as PR review comment (advisory), allow merge, log to `tech-debt.md`  | Phase 8 stays complete; HIGH count tracked |
| MEDIUM   | Post as single summary PR comment, allow merge                            | Phase 8 stays complete                     |
| LOW      | Append to PR description footer, allow merge                              | Phase 8 stays complete                     |

If verdict is `APPROVE` (no findings) or `APPROVE_WITH_CAVEATS` (no CRITICAL), the gate transitions the story toward Done. If verdict is `BLOCK`, the gate writes the findings to a new dispatch task (story tagged `fix-required-from-723-gate`, phase set back to 8, prompt seeded with the CRITICAL list).

### 5.6 "Dispatch a fix task" semantics

Reuses existing dispatch infrastructure (STORY-026 / STORY-253). On BLOCK:

1. Mark the original PR `needs_review` / request changes.
2. Insert a new row in `dispatch_items` with:
   - `parent_story_id` = original story
   - `phase` = 8
   - `prompt_payload` = original Phase 6 spec + the CRITICAL findings as "required fixes" appendix
   - `status` = `claimable`
3. The next available agent picks it up via the standard poll loop and re-implements the missing work. The fix-task's Phase 8 must itself pass the adversarial gate before merge.

### 5.7 Cost and time budget

- Reviewer subagent: Sonnet, ~5–15k input tokens (spec + diff + tests), ~2–4k output tokens. Estimated cost: $0.04–0.10 per review. At 20 stories/week, ~$8/week. **Acceptable.**
- Wall-clock: ~30–90 seconds per review. Adds <2 minutes to the typical Phase 8 → Done transition. Acceptable.

---

## 6. Out of Scope

- **Not a security review.** Does not replace Phase 6b. Findings about secret handling, authn/z, SQLi etc. should be flagged but the security review remains canonical.
- **Not a replacement for Morris's PR review.** Morris reviews merge-readiness (commits clean, PR description accurate, no merge conflicts). The adversarial gate reviews **test/implementation correctness**. Both run.
- **Does not run for Phase 7.** Phase 7 produces RED tests by design — there is no implementation yet to compare against. The gate runs strictly post-Phase-8.
- **Does not run for hotfixes** that bypass Phase 6 (Small / Trivial scope, e.g. STORY-253 1→7→8→Done where there's no formal spec for the reviewer to compare against). Phase 6 is the spec-of-truth that the reviewer uses; without it, the reviewer has nothing to grade against. Future enhancement could derive a synthetic spec from acceptance criteria, but out of scope here.
- **Does not auto-rewrite tests.** The reviewer reports gaps; fixing them is the implementation agent's job (via the dispatch fix task).
- **Does not gate on coverage percentage.** This is qualitative review, not `pytest --cov` thresholding.
- **No web UI in v1.** Verdict and findings are surfaced via PR comment + `adversarial-review.md` file. A future story may add this to the ops console.

---

## Test Criteria

The Phase-7 RED tests must include at least the following assertions (all must go GREEN in Phase 8):

1. **TC-1 (orchestration):** Given a story with Phase 8 marked `COMPLETED`, when the orchestrator runs the next-phase resolver, the resulting next phase is the new adversarial review step (not `Done`).
2. **TC-2 (subagent invocation):** The review step invokes a Sonnet subagent with a prompt that includes (a) the path to `feature-spec.md`/`specification.md`, (b) the diff for the branch, (c) the test file paths. Test asserts on the constructed prompt's content.
3. **TC-3 (output parsing — CRITICAL block):** Given a fixture `adversarial-review.md` containing 2 CRITICAL findings, the parser returns `verdict=BLOCK`, `critical_count=2`, `findings_by_severity={"CRITICAL": [...]}`.
4. **TC-4 (output parsing — clean approval):** Given a fixture with verdict `APPROVE` and zero findings, the parser returns `should_merge=True`, `dispatch_fix_task=False`.
5. **TC-5 (severity gating — CRITICAL blocks):** When parser yields `critical_count > 0`, the orchestrator (a) does NOT advance the story to Done, (b) inserts a fix-task row in `dispatch_items` with `parent_story_id` set, (c) posts PR comments via the GitHub service mock.
6. **TC-6 (severity gating — HIGH only is advisory):** When parser yields `critical_count == 0` and `high_count > 0`, the orchestrator (a) DOES advance the story to Done, (b) posts the HIGH findings as a PR comment, (c) appends to `tech-debt.md`.
7. **TC-7 (severity gating — clean):** When parser yields zero findings, the orchestrator advances to Done with no PR comment posted.
8. **TC-8 (regression — STORY-701 taxonomy):** Given a synthetic spec that lists 8 taxonomy categories and an implementation that only handles 6, the reviewer produces at least one CRITICAL finding identifying the missing categories by name. (Drives the prompt template's spec-completeness check.)
9. **TC-9 (regression — STORY-701 fake DB test):** Given a test file whose only DB-write assertion is a source-string regex (no mock DB invocation), the reviewer flags this as CRITICAL with category "static-test-masquerading-as-behavioral".
10. **TC-10 (regression — STORY-720 dedup logic inversion):** Given an implementation where `_is_duplicate_issue` returns `True` whenever the existing-issues list is non-empty (regardless of identity), and a test that uses a hardcoded fixture ID, the reviewer flags BOTH the logic bug (CRITICAL) AND the unrealistic fixture (HIGH).
11. **TC-11 (idempotency — gate runs once per PR):** If the gate has already produced an `adversarial-review.md` for the current commit SHA, re-running it short-circuits (does not re-spend tokens) and returns the cached verdict.
12. **TC-12 (skip — non-Medium-or-larger scopes):** For a story with scope=Small, the gate is skipped entirely (logged, not invoked). For Medium, Large, New: gate runs.
13. **TC-13 (deliverable file written):** After a successful run, `features/<story-folder>/adversarial-review.md` exists, is non-empty, contains the canonical sections (`## Verdict`, `## Findings`, `## Coverage Matrix`).

---

## Validation

### 8.1 Synthetic regression fixtures

Build fixture branches under `tests/fixtures/adversarial_review/`:

- `fixture_701_taxonomy_gap/` — copy of the STORY-701 spec + the actually-shipped 6-category implementation + the actually-shipped 37 tests. Run the gate. **Must** produce CRITICAL findings naming `needs_info_unanswered` and `agent_died`. If it does not, the prompt template is insufficient.
- `fixture_701_fake_db_test/` — copy of the static-analysis "DB write" test. Gate **must** produce CRITICAL with "static test masquerading as behavioral."
- `fixture_720_dedup_inversion/` — copy of the broken `_is_duplicate_issue` and the hardcoded-ID test. Gate **must** produce CRITICAL on the logic and HIGH on the fixture.

These three fixtures are the acceptance bar. If the gate doesn't catch them, ship is blocked.

### 8.2 Live shadow run

Before flipping the gate to `BLOCK`-enforcing, run it in **shadow mode** for one week:

- Gate runs on every Medium+ Phase-8 completion.
- Output is written to `adversarial-review.md` and posted as a PR comment.
- Verdict is logged but **not enforced** — story still advances to Done regardless.
- At the end of the shadow week: review which findings were valid, tune the prompt, then flip enforcement on.

Acceptance for shadow → enforced: ≥80% precision on CRITICAL findings (manual spot-check). False-CRITICAL rate that would block legitimate merges must be low enough to avoid agent thrash.

### 8.3 Negative validation

Run the gate against three already-merged "good" PRs (cherry-picked by the team as exemplars). Expected: verdict `APPROVE` or `APPROVE_WITH_CAVEATS` with zero CRITICAL. If the gate flags any of them as BLOCK, the prompt is too aggressive.

### 8.4 Cost validation

Track token spend per run for the shadow week. If average exceeds 25k input tokens or $0.20 per run, revisit the prompt scope (likely trim the diff context).

---

## 9. Dispatch Notes

- **Branch:** `story-723/adversarial-review-gate`
- **Worker:** Any agent on the dispatch fleet may claim this. No special skill required beyond standard SDLC competence.
- **Phase Path enforcement:** 1 → 6 → 7 → 8 → Done. The runner must reject attempts to skip Phase 6 (this is a process-design story; the spec must be written before code).
- **Model policy reminder:** Phase 1 = Opus (this file). Phase 6 = Opus. Phase 7 = Sonnet. Phase 8 = Sonnet. Phase 8b code-review = Sonnet. The new gate itself, once shipped, runs on Sonnet at execution time.
- **Coordination:** The work on `sdlc_engine.py` may collide with STORY-006 / STORY-044 if they're in flight. Phase 6 must check for in-flight engine PRs and either rebase or sequence behind them. As of 2026-04-26 there are no open engine PRs, but verify before Phase 8.
- **Rollout flag:** Add a `config.yaml` flag `adversarial_gate.enabled: false` (default) and `adversarial_gate.mode: "shadow" | "enforce"`. Ship in shadow first per §8.2.
- **Observability:** Emit a structured log line `adversarial_gate.run` per invocation with fields `story_id`, `verdict`, `critical_count`, `high_count`, `tokens_in`, `tokens_out`, `wall_ms`. Surface in the ops dashboard cost panel.
- **Human override:** Document an escape hatch — a maintainer can post a PR comment `/override-adversarial-gate <reason>` to force-merge a BLOCK verdict. This must be logged and audit-trailed (writes to `tech-debt.md`). Used sparingly; abuse is a process smell.
- **Done definition:** The story is Done when (a) all Phase-7 tests are GREEN, (b) the three synthetic regression fixtures from §8.1 produce the expected CRITICAL findings, (c) the gate has run successfully in shadow mode against ≥3 real Phase-8 completions, (d) `predeploy-gate.md` (Phase 11) signs off, (e) `adversarial_gate.enabled: true, mode: "shadow"` is committed in main config.

---

*End of seed.md — STORY-723.*
