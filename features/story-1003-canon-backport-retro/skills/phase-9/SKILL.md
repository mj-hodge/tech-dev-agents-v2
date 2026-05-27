---
name: phase-9
description: Run Phase 9 (Refinement) to polish the solution, handle edge cases, and increase test coverage.
---

# Phase 9: Distinguished Engineer

The Distinguished Engineer polishes the solution, handles edge cases, and ensures high quality standards.

## Identity
- **Role:** Distinguished Engineer
- **Goal:** Refine, polish, and achieve high coverage
- **Persona:** `.sdlc/agents/phase-9-refinement.md`

## Turn Budget & Efficiency (STORY-511)

**Completion is the contract. Conciseness is the tactic.** Your job is to produce this phase's deliverable — not to bail out at the budget. The target below is a pace-setter, not a quit signal.

**Target pace:** ~20 turns. If you're working efficiently (see tactics below) you should land here. The harness cap is higher as a safety ceiling — going over the target is a smell, not a failure.

**Tactics to hit the pace:**

- **Reuse session context.** If this phase was launched with `--resume <session-id>`, prior phases already read `seed.md`, `feature-spec.md`, `test-design.md`, etc. in this same session. Do not re-read them — trust the session cache. Re-reading is the #1 cause of overruns (observed 4× re-read tax across phases on 2026-04-21).
- **Read once, narrowly.** Each file at most once per phase. Use `offset`/`limit` to grab only the part you need. Don't re-open a file to "double-check" — your prior read is authoritative.
- **Stay in scope.** Produce this phase's deliverable first. "While I'm here" cleanups, refactors, side explorations — note them in the deliverable's *Follow-ups* section, don't execute them.
- **Concise output.** Deliverables are file content, not narration. No "I'll now..." framing, no post-hoc recap paragraphs. Ship the file, update tracking docs, stop.
- **Commit as you go.** In Phase 8 specifically, commit after each logical unit (one endpoint, one model, one migration). Prevents stranded uncommitted work if you hit the harness cap mid-phase.

**If the phase is genuinely over-scope (rare):**

Only when the work truly cannot fit in the harness cap — e.g., a Large Phase 8 with 5 independent endpoints. In that case:

1. Complete and commit what you can (don't abandon the partial work — it must be on disk and in git).
2. In the deliverable file, add a **## Resume Marker** section listing what's done and what's still TODO with enough detail for the next session to pick up cleanly.
3. Update `.project` → Phase Routing to note "partial — resume needed."
4. Exit. The next dispatch will claim the story and `--resume` into the same session to finish.

This is iteration, not abandonment. Partial-but-committed is always better than complete-but-uncommitted.

## 3-Question Gate (REQUIRED)

Before Phase 9 can mark complete, the following three questions must be answered. These mirror the continuous-improvement checklist in `gc-data-v2/pipeline-template/.github/pull_request_template.md`. The canonical gate text lives in `.sdlc/templates/three-question-gate.md`.

1. **Canon-doc impact** — does this work expose a gap in `gc-data-v2/platform/*.md`?
   > (Yes / No / N/A + justification)

2. **Scaffold backport** — should this land in `gc-data-v2/pipeline-template/`?
   > (Yes / No / N/A + justification)

3. **Sibling sweep** — do other v2 pipelines need this?
   > (Yes — list / No / Unknown — flag for triage)

### Gate Rules

- **All three questions MUST be answered** before Phase 9 can close. If any question is unanswered, Phase 9 cannot mark complete: `Phase 9 cannot close: 3-question gate incomplete`.
- **"N/A" requires a one-line justification.** Answering "N/A" without justification is rejected: `N/A requires justification (e.g. 'N/A — pipeline-specific business logic')`.
- For non-pipeline builds (`build_type != pipeline`), Q2 (scaffold backport) is auto-answered: `N/A — build is not a pipeline; no pipeline-template to backport into`.
- Answers are written into `refinement-report.md` under a new `## 3-question gate` heading.

### Auto-Invocation of `/canon-backport`

After all three questions are answered, Phase 9 automatically invokes `/canon-backport <STORY-ID>`:
- The invocation result is logged in `.project` as `canon_backport_invoked: true` with the PR URL(s) or `no_gap_found`.
- To skip auto-invocation, pass `--no-backport` to the `/phase-9` command.
- The `canon-backport` skill diffs the story's lessons learned against `gc-data-v2/platform/*.md` and `tech-gc-knowledgebase/wiki/**/*.md`, drafting a PR when a gap is found.
- See `.sdlc/skills/canon-backport/SKILL.md` for full details.

## Workflow
1. **Address Deferred Items:** Resolve findings from Phase 8b
2. **Polish Edge Cases:** Improve error handling, performance, UX
3. **Increase Coverage:** Target 80%+ test coverage
4. **Produce Refinement Report**
5. **Answer the 3-Question Gate** (required to close Phase 9)
6. **Auto-invoke `/canon-backport`** (unless `--no-backport` flag passed)

## Outputs

- `refinement-report.md` — Edge cases, performance, coverage increase, dependency health, cleanup, **and the 3-question gate answers**
- `canon-backport-results.json` — Gap detection results (if canon-backport was invoked)
- `.project` — Updated with Phase 9 complete and `canon_backport_invoked` status

## Advance
- **Type:** confirm
- **Next:** Done
