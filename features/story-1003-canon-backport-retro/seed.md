# STORY-1003 — NEW skill `canon-backport` + `retro` companion proposal + phase-9 3-question gate

**Story ID:** STORY-1003
**Scope:** Medium
**Epic:** STORY-1000 (Cross-Repo Canon Alignment)
**Work-Stream:** A — SDLC framework knowledgebase integration
**Repo touched:** `sdlc-framework` (and the `.sdlc` submodule pointer in `tech-dev-agents`)
**Date:** 2026-05-18
**Status:** Phase 1 — seed written, gated awaiting approval
**Frontend:** false

## Problem

The 2026-05-04 retro identified 7 framework changes that never shipped (epic seed line 25: "Ongoing | 2026-05-04 retro's 7 framework changes never shipped | learning loop | No mechanism to convert retro findings into enforcing gates"). The `retro` skill (`skills/retro/SKILL.md` lines 51-81) emits exactly one proposal file — `retro-proposal.yaml` — which only targets the SDLC framework. When a story discovers a gap in `gc-data-v2/platform/*.md` or `tech-gc-knowledgebase/wiki/`, that finding has no home: the framework retro proposal can't update the data-platform canon, and there's no skill to draft the PR back to `gc-data-v2`. Meanwhile `skills/phase-9/SKILL.md` (lines 16-19) lists generic refinement workflow steps but never asks the three questions baked into `gc-data-v2/pipeline-template/.github/pull_request_template.md`: (1) canon-doc impact? (2) scaffold backport? (3) sibling-pipeline sweep? Those three questions are the exact mechanism the data team uses to keep 11 pipelines in sync — without them, lessons learned in one story die in that story's refinement-report.md.

## Goal

A `canon-backport` skill that diffs a closing story's lessons learned against `gc-data-v2/platform/*.md` and `tech-gc-knowledgebase/wiki/`, drafting a PR when a gap is found. A `retro` skill that emits a *second* proposal file (`retro-proposal-gc-data-v2.yaml`) when findings touch platform concerns. A `phase-9` workflow that mandates the three questions before refinement can close.

## Scope

- **NEW skill** `/mnt/c/Projects/sdlc-framework/skills/canon-backport/SKILL.md`:
  - Invoked at story close (called from `complete-story` per epic SC-5) or manually as `/canon-backport`.
  - Inputs: the story's `seed.md`, `refinement-report.md` (if exists), `code-review.md` (if exists), and the answers to the 3-question gate.
  - Diffs the lessons-learned text against the headings + body of `gc-data-v2/platform/*.md` (all 30 docs) and `tech-gc-knowledgebase/wiki/**/*.md`. Gap detection uses keyword + heading match (e.g. a refinement note saying "Walmart token endpoint returned 400 because KV slot referenced placeholder values" matches `failure-modes.md` "Author-time gate 3: KV secret slot vs FA env-var reference"; if the gate exists, no gap; if it doesn't, the note is a candidate gap).
  - When a gap is found:
    - If it touches platform/architectural canon → draft a PR against `hpi-gorillacommerce/gc-data-v2`, branch `canon/story-XXXX-<slug>`, modifying the matched `platform/*.md` (or proposing a new section).
    - If it touches business/process canon → draft a PR against `hpi-gorillacommerce/tech-gc-knowledgebase`, branch `wiki/story-XXXX-<slug>`.
  - PR body links back to the closing story, cites the exact paragraph(s) in `refinement-report.md` that motivated the change, and references the 3-question gate answers.
  - **Idempotent:** rerunning on the same story doesn't open a duplicate PR (looks up `gh pr list --search "STORY-XXXX canon"` first).
  - **Output:** writes `canon-backport-results.json` under `features/story-XXXX/` listing `{ matched_canon_docs: [...], gaps_found: [...], prs_drafted: [...] }`.
- **MODIFY** `/mnt/c/Projects/sdlc-framework/skills/phase-9/SKILL.md`:
  - Add a "3-question gate" subsection (mirroring `gc-data-v2/pipeline-template/.github/pull_request_template.md` § Continuous-improvement checklist). Three questions:
    1. **Canon-doc impact** — does this work expose a gap in `gc-data-v2/platform/*.md`? (Yes / No / N/A + justification)
    2. **Scaffold backport** — should this land in `gc-data-v2/pipeline-template/`? (Yes / No / N/A + justification)
    3. **Sibling sweep** — do other v2 pipelines need this? (Yes — list / No / Unknown — flag for triage)
  - Answers WRITTEN into `refinement-report.md` under a new `## 3-question gate` heading; "N/A" requires a one-line justification.
  - Phase 9 cannot mark complete until all three are answered.
  - After answering, automatically invoke `/canon-backport` (unless `--no-backport` flag passed).
- **MODIFY** `/mnt/c/Projects/sdlc-framework/skills/retro/SKILL.md`:
  - In step 7 ("Export proposal file"), emit a second YAML when any finding's `category` matches a platform concern. Category pattern match: `pipeline|data|canon|gc-data-v2|airflow|dbt|iceberg|bronze|silver|gold|storage|auth|observability`.
  - Second file path: `features/<epic-folder>/retro-proposal-gc-data-v2.yaml`.
  - Schema: same shape as existing `retro-proposal.yaml` but with `target_repo: gc-data-v2` and `target_file: platform/<doc>.md` (vs. `agents/phase-N-*.md`).
  - Both YAMLs are emitted in the same step; the original `retro-proposal.yaml` continues to handle framework changes (no change to its shape).
  - Update `skills/retro/SKILL.md` § "Submitting Proposals" to document the dual-proposal flow and the data-team review path.
- Update `software-development-guidance.md` § Phase 9 with the 3-question gate.
- Update `agents/phase-9-refinement.md` agent persona to call out the gate.

## Out of scope

- Auto-merging the drafted PRs in `gc-data-v2` — those go through the data team's normal review.
- Modifying `gc-data-v2/pipeline-template/.github/pull_request_template.md` — that template is our *source* for the 3 questions, not modified here.
- The Monday.com/Asana wiring that closes a story (touched in STORY-1012's `complete-story` updates, Work-Stream C).
- Morris's role in surfacing `canon-backport`-drafted PRs (STORY-1008, Work-Stream B).
- Cross-repo file moves (STORY-1015, Work-Stream D).
- A retro-proposal review UI — proposals stay YAML files for now.
- `tech-gc-knowledgebase` freshness frontmatter (STORY-1016).

## Success criteria (acceptance criteria)

1. **SC-1 — `canon-backport` skill exists:** running `/canon-backport STORY-XXXX` on a test story with a seeded "Walmart KV slot gotcha" lesson opens a draft PR against `hpi-gorillacommerce/gc-data-v2` (or links the existing canon section if already documented).
2. **SC-2 — Idempotency:** running `/canon-backport STORY-XXXX` twice does not open a second PR; second run prints `canon-backport already ran for STORY-XXXX; existing PR: <url>`.
3. **SC-3 — Result file:** `features/story-XXXX/canon-backport-results.json` exists, validates against a defined schema (keys `matched_canon_docs`, `gaps_found`, `prs_drafted`).
4. **SC-4 — Phase 9 gate present:** running `/phase-9` on any story produces `refinement-report.md` containing a `## 3-question gate` heading with three answered questions (or fails with `Phase 9 cannot close: 3-question gate incomplete`).
5. **SC-5 — N/A requires justification:** answering "N/A" without a one-line justification rejects the answer with `N/A requires justification (e.g. 'N/A — pipeline-specific business logic')`.
6. **SC-6 — Auto-invocation:** completing Phase 9 (`/next` from Phase 9) automatically invokes `/canon-backport` unless `--no-backport` was passed; the invocation result is logged in `.project`.
7. **SC-7 — Retro emits dual proposals:** running `/retro <epic>` on an epic where at least one finding has category matching the platform-keyword regex produces both `retro-proposal.yaml` and `retro-proposal-gc-data-v2.yaml`. The latter file's proposals all have `target_repo: gc-data-v2`.
8. **SC-8 — Retro single-proposal regression:** running `/retro` on an epic with no platform-keyword findings produces only `retro-proposal.yaml` (no spurious second file).
9. **SC-9 — Epic REPLAY-4 passes:** `tests/epic_1000/test_replay_4_retro_emits_dual_proposals.py` is GREEN (this is the epic-level scenario from STORY-1000 line 99).

## Files to modify

**NEW:**
- `/mnt/c/Projects/sdlc-framework/skills/canon-backport/SKILL.md` — the new skill.
- `/mnt/c/Projects/sdlc-framework/skills/canon-backport/gap-detection.md` — keyword-and-heading match heuristics + the canonical canon file list (30 platform docs + wiki tree).
- `/mnt/c/Projects/sdlc-framework/skills/canon-backport/templates/pr-body-gc-data-v2.md` — PR body template targeting gc-data-v2.
- `/mnt/c/Projects/sdlc-framework/skills/canon-backport/templates/pr-body-tech-gc-knowledgebase.md` — PR body template targeting tech-gc-knowledgebase.
- `/mnt/c/Projects/sdlc-framework/templates/three-question-gate.md` — canonical text block for the 3 questions (so Phase 9 stays in sync with `gc-data-v2`'s PR template).
- `/mnt/c/Projects/sdlc-framework/templates/retro-proposal-gc-data-v2.yaml` — schema example.

**MODIFY:**
- `/mnt/c/Projects/sdlc-framework/skills/phase-9/SKILL.md` — add 3-question gate workflow, auto-invoke canon-backport.
- `/mnt/c/Projects/sdlc-framework/skills/retro/SKILL.md` — emit dual proposals on platform-keyword match.
- `/mnt/c/Projects/sdlc-framework/agents/phase-9-refinement.md` — persona update.
- `/mnt/c/Projects/sdlc-framework/agents/retro-process-engineer.md` — persona update.
- `/mnt/c/Projects/sdlc-framework/software-development-guidance.md` — Phase 9 gate documentation; retro dual-proposal documentation.

**Tracking docs:**
- `/mnt/c/Projects/tech-dev-agents/.project` — Phase 1 status updates for STORY-1003.
- Monday.com task comment.

## Files to NOT modify

- `/mnt/c/Projects/gc-data-v2/pipeline-template/.github/pull_request_template.md` — source of truth for the 3 questions, never edited from this side (STORY-1004 byte-diffs against it).
- `/mnt/c/Projects/gc-data-v2/platform/*.md` — `canon-backport` opens PRs against these but never modifies them directly from this story's branch.
- `/mnt/c/Projects/sdlc-framework/skills/spec/`, `phase-1/`, `phase-6/`, `phase-7/`, `phase-10/` — earlier stories' scope.
- `/mnt/c/Projects/sdlc-framework/skills/complete-story/` — STORY-1012's scope.
- Morris skills under `/mnt/c/Projects/tech-dev-agents/deployment/vm/skills/morris/` — Work-Stream B.

## Verification plan

| SC | Shell command | Expected output |
|---|---|---|
| SC-1 | Seed a fixture story `STORY-T001` with refinement note "Walmart 400 — KV slot placeholder"; run `/canon-backport STORY-T001`; `gh pr list --repo hpi-gorillacommerce/gc-data-v2 --search "STORY-T001"` | One draft PR returned; PR body cites refinement-report.md paragraph |
| SC-2 | Rerun `/canon-backport STORY-T001` | Stdout: `canon-backport already ran for STORY-T001; existing PR: <url>`. `gh pr list` count unchanged. |
| SC-3 | `cat features/story-T001-fixture/canon-backport-results.json \| jq 'keys'` | `["gaps_found","matched_canon_docs","prs_drafted"]` |
| SC-4 | Run `/phase-9` on STORY-T001; `grep -c '^## 3-question gate' features/story-T001-fixture/refinement-report.md` | `1` (heading present) |
| SC-5 | Inject "N/A" with no justification on Q1; `/phase-9` close attempt | Stderr: `N/A requires justification ...`; exit non-zero |
| SC-6 | Complete Phase 9 with all three answered; check `.project` for `canon_backport_invoked: true` | Line present with PR URL or `no_gap_found` |
| SC-7 | Run `/retro` on a fixture epic with one finding category=`Pipeline canon`; `ls features/<epic>/retro-proposal*.yaml` | Two files: `retro-proposal.yaml` AND `retro-proposal-gc-data-v2.yaml`. The second has `target_repo: gc-data-v2` in every proposal entry. |
| SC-8 | Run `/retro` on a fixture epic with all findings category=`Phase 7` (no platform keywords); `ls features/<epic>/retro-proposal*.yaml` | Only `retro-proposal.yaml` exists |
| SC-9 | `pytest tests/epic_1000/test_replay_4_retro_emits_dual_proposals.py -v` | PASSED |

## Boundaries

| Always Do | Ask First | Never Do |
|---|---|---|
| Open `canon-backport` PRs as **draft** (data team has merge authority) | Add a third target repo beyond `gc-data-v2` and `tech-gc-knowledgebase` | Force-push or auto-merge a `canon-backport` PR |
| Cite the exact paragraph from `refinement-report.md` in every PR body | Tweak the keyword regex for "platform concern" detection | Modify `gc-data-v2/platform/*.md` from this story's branch directly |
| Keep the 3-question gate text byte-identical to `gc-data-v2/pipeline-template/.github/pull_request_template.md` source | Add a 4th question to the gate | Skip the gate for "small" Phase 9 runs |
| Mark "N/A" as acceptable only with a one-line justification | Auto-answer "N/A" based on heuristics | Block Phase 9 close indefinitely if user answers all three with valid justifications |
| Make `canon-backport` idempotent at the PR-search level (`gh pr list --search`) | Re-open a closed `canon-backport` PR if a related story re-runs | Auto-merge `retro-proposal*.yaml` — those are *proposals*, applied via `/retro-apply` |

## Done looks like

```
$ /phase-9 STORY-T001-fixture
Refining... 3 edge cases addressed, coverage 78→84%.
Now answering the 3-question gate (required to close Phase 9):

  1. Canon-doc impact: does this expose a gap in gc-data-v2/platform/*.md?
     > yes — Walmart KV slot placeholder gotcha not documented; will backport.

  2. Scaffold backport: should this land in gc-data-v2/pipeline-template/?
     > N/A — pipeline-specific (one repo, no scaffold change).

  3. Sibling sweep: do other v2 pipelines need this?
     > unknown — flagging for triage; opening issue.

3-question gate written to refinement-report.md.
Auto-invoking /canon-backport STORY-T001-fixture...

$ /canon-backport STORY-T001-fixture
Scanning 30 platform/*.md + 47 wiki/*.md for matches...
Match score 0.83: gc-data-v2/platform/failure-modes.md § "Author-time gate 3: KV secret slot..."
  → existing section, gap is partial (placeholder-vs-working distinction missing)
Drafting PR against gc-data-v2: canon/story-T001-walmart-kv-placeholder
  → PR #142 (draft) opened
canon-backport-results.json written.

$ gh pr list --repo hpi-gorillacommerce/gc-data-v2 --search "STORY-T001"
142  canon/story-T001-walmart-kv-placeholder  DRAFT

$ /retro epic-walmart-supplier-v2
Findings: 8 (4 with platform-keyword match)
Emitting retro-proposal.yaml (3 framework changes)
Emitting retro-proposal-gc-data-v2.yaml (4 canon-doc proposals)
```

## Escalation contract

Inherits the epic's escalation contract. Story-specific escalations to Mark:

1. A `canon-backport` PR opened against `gc-data-v2` is rejected by the data team — per epic § Escalation 3, pause the `canon-backport` loop until alignment.
2. The gap-detection heuristic produces a false positive that drafts a spurious PR — close the PR, log the false-positive pattern in `gap-detection.md`, tune the regex.
3. The 3-question gate text in `gc-data-v2/pipeline-template/.github/pull_request_template.md` changes mid-story — STORY-1004's drift-check should catch it; coordinate before adopting changes.
4. A story's refinement-report.md is genuinely empty of canon-worthy lessons (e.g. typo fix) — `canon-backport` returns `no_gap_found`; that is a valid result, not an error.


## Test Criteria

| Test | Validates |
|------|-----------|
| RED tests from Phase 7 (see `## Verification plan`) | Every SC has a literal test or shell command |
| Full pytest suite GREEN, zero regressions | No unrelated breakage |
| Incident-replay tests (where applicable) | The 2026-05-04..05-18 failure classes are catchable by the new gate |

See `## Success criteria` and `## Verification plan` above for SC-keyed predicates.


## Validation

After merge:

1. **Tests:** all RED tests turned GREEN; `pytest <story test paths>` exits 0.
2. **No regressions:** full suite passes; CI green on the PR.
3. **Per-SC verification:** every command in `## Verification plan` runs and produces the expected output.
4. **Tracking updated:** `.project` and `backlog.md` reflect Phase 8 completion; Monday.com task updated.

## Phase path

Medium scope: `1 → 6 → 7 → 8 → Done`. Phase 6 produces `feature-spec.md` covering `canon-backport` API, gap-detection rules, the 3-question gate workflow, and the retro dual-proposal logic. Phase 7 produces RED tests `tests/sdlc/test_canon_backport_idempotent.py`, `test_canon_backport_pr_draft.py`, `test_phase9_three_question_gate.py`, `test_retro_dual_proposal_emit.py`. Phase 8 turns them GREEN. Depends on STORY-1001 (for `build_type` context) but does not strictly require STORY-1002 to merge first — the 3-question gate applies to all builds, only the canon-doc-search portion of `canon-backport` is pipeline-specific.

## Open questions for Phase 6

1. **Gap-detection algorithm.** Is keyword+heading match good enough, or should we use embedding similarity (e.g. `sentence-transformers`) for better recall? Default proposal: ship v1 with keyword+heading (simple, fast, debuggable); revisit after measuring false-negative rate via retro.
2. **PR auto-creation vs. issue creation.** Should `canon-backport` open a draft PR (with proposed diff) or just an issue (with prose)? Default proposal: PR with proposed diff — forces the proposer to actually write the canon text, not just gesture at the gap. Empty PRs are rejected; the skill MUST attempt a concrete edit.
3. **Multi-canon spanning.** If a single lesson spans both `gc-data-v2` (platform invariant) AND `tech-gc-knowledgebase` (business context), do we open two PRs or one cross-linked? Default proposal: two PRs, each linking the other in its body.
4. **Phase 9 gate for non-pipeline builds.** The 3 questions originated in gc-data-v2's pipeline PR template — do they apply to feature/bug/ops builds? Default proposal: yes, but Q2 (scaffold backport) is auto-`N/A` for non-pipeline (`build_type != pipeline`) with the justification "build is not a pipeline; no pipeline-template to backport into".
5. **Retro-proposal-gc-data-v2.yaml apply path.** The existing `retro-apply` skill ingests `retro-proposal.yaml` into `coding-ai-config`. Does the data team need an equivalent for `gc-data-v2`? Default proposal: not in this story — the data team reviews `retro-proposal-gc-data-v2.yaml` manually and merges the PRs by hand. A future story can automate.
6. **Idempotency horizon.** If a story is closed → reopened → closed again, should `canon-backport` re-run? Default proposal: run if `.project` shows the story reopened since the last canon-backport timestamp; otherwise skip.
7. **Authorship attribution in PR body.** Should drafted PRs carry `Co-Authored-By: Claude` markers, or be authored by the user? Default proposal: marked as drafted by Claude (transparent), but Mark / story owner is listed as reviewer.

## Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| False-positive gap detection floods data team with spurious PRs | Medium | Medium | Open as **draft**; tune regex on first false-positive; rate-limit to 1 backport PR per story |
| Missed gap (false negative) — real lesson never makes it to canon | Medium | High | Mark spot-checks via retro; future v2 with embedding similarity |
| 3-question gate becomes rubber-stamp ("all N/A") | Medium | High | Require justification per N/A; retro audits N/A patterns and proposes tightening |
| `canon-backport` PR merge conflicts with concurrent data-team work | Medium | Low | Draft state; rebase responsibility on the human merging |
| Retro dual-proposal mis-classifies framework findings as canon (or vice versa) | Low | Low | Category regex is explicit and conservative; mis-classification produces a no-op (extra proposal file, easily ignored) |
| Author privacy / leak via PR body (e.g. seed contains internal incident detail) | Low | High | PR body cites refinement-report.md, which is curated; no raw logs included |
| `gh` CLI not available on the host running `canon-backport` | Low | Medium | Detect early; emit `gh auth login` guidance and exit non-zero |
| `tech-gc-knowledgebase` wiki structure changes (e.g. directory rename) | Low | Medium | Resolve wiki paths at runtime; warn on unknown subdir |

## Dependencies & sequencing

- **Upstream:** STORY-1001 (for `build_type` context — Q2 auto-N/A logic uses it).
- **Independent of:** STORY-1002 (the 3-question gate applies to all builds). Can ship in parallel.
- **Downstream:**
  - Morris STORY-1008 (`canon-backport` canon-aware mods) consumes this skill and surfaces results in PR review comments.
  - STORY-1012 (`complete-story` 3-question gate) — the gate text in `complete-story` mirrors this story's gate. Coordinate via shared template `sdlc-framework/templates/three-question-gate.md`.
- **Cross-stream:** Work-Stream D STORY-1016 (tech-gc-knowledgebase freshness frontmatter) creates the `last-reviewed:` field that `canon-backport` may need to update when proposing wiki changes. Coordinate ordering: if STORY-1016 ships first, `canon-backport` bumps `last-reviewed:`; if not, leaves it alone.

## Test fixtures needed (for Phase 7)

- `tests/fixtures/sdlc/seed.canon-gap.md` — synthetic seed with a refinement note flagging a documented invariant.
- `tests/fixtures/sdlc/seed.no-canon-gap.md` — synthetic seed with no platform-relevant lessons (regression baseline for SC-8).
- `tests/fixtures/sdlc/refinement-report.kv-slot-gotcha.md` — concrete lesson "Walmart 400 — KV slot placeholder" (matches `failure-modes.md` Author-time gate 3).
- `tests/fixtures/sdlc/gc-data-v2-snapshot/` — shared with STORY-1001 / STORY-1002.
- `tests/fixtures/sdlc/wiki-snapshot/` — synthetic 5-page tech-gc-knowledgebase tree for gap-search tests.
- `tests/fixtures/sdlc/retro-proposal.pipeline-finding.yaml` — golden output for SC-7.
- `tests/fixtures/sdlc/retro-proposal.framework-only.yaml` — golden output for SC-8 (one file, no companion).
- Mocked `gh` CLI (via `unittest.mock.patch` on subprocess) — never opens real PRs in test runs.

## Notes for Phase 6 designer

- The skill spans two repos when opening PRs. Make the target repo a clean parameter (`--target=gc-data-v2|tech-gc-knowledgebase`) driven by the gap category, not hardcoded.
- Keep the gap-detection logic *separate* from the PR-drafting logic. `canon-backport` should be re-runnable in dry-run mode (`--dry-run` flag) that emits `canon-backport-results.json` without opening PRs — useful for retro analysis and CI testing.
- The 3-question gate should not be a unique-to-Phase-9 concept long-term; it's the same gate `complete-story` will enforce in STORY-1012. Factor the gate text + validation into `sdlc-framework/templates/three-question-gate.md` so both skills consume the same source.
- The retro dual-proposal logic is a small change inside the existing `retro` flow. Don't over-engineer — emit the second file only when at least one finding's category matches the platform-keyword regex.

## Worked example — Walmart KV slot gotcha (the SC-1 scenario)

A pipeline story closes with `refinement-report.md` containing the paragraph:

> "Walmart token endpoint returned 400 in production. Root cause: TF authored two KV slots — `walmart-v2-client-{id,secret}` (placeholder values from initial provisioning) and `wpa-client-{id,secret}` (real working values). FA app-setting referenced the placeholder slot. Fixed by repointing TF to `wpa-client-*` slot."

`canon-backport` runs:

1. Searches `gc-data-v2/platform/*.md` for keywords {`KV`, `Key Vault`, `secret`, `slot`, `placeholder`, `walmart`, `token endpoint`}.
2. Matches `failure-modes.md` § "Author-time gate 3: KV secret slot vs FA env-var reference" with high confidence (0.85).
3. Reads that section. Notes it documents the *symptom* (FA returns 500 with "Walmart token endpoint returned 400") and *cause* (placeholder vs working slot) but **does NOT explicitly call out the dual-slot provisioning anti-pattern** (the TF authored two slots, one being placeholder).
4. Drafts a PR against `gc-data-v2` adding a sub-bullet under "Cause:" with the dual-slot pattern + a "Prevention" subsection recommending naming conventions to distinguish placeholder vs working slots.
5. Sets PR status to `draft`, opens against the data team.
6. Writes `canon-backport-results.json`:
   ```json
   {
     "matched_canon_docs": ["gc-data-v2/platform/failure-modes.md#author-time-gate-3"],
     "gaps_found": [{"section": "Author-time gate 3", "type": "partial", "missing": "dual-slot anti-pattern"}],
     "prs_drafted": [{"repo": "gc-data-v2", "number": 142, "url": "https://github.com/hpi-gorillacommerce/gc-data-v2/pull/142"}]
   }
   ```
