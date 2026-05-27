# STORY-1008 — Morris canon-aware mods: `start-story`, `answer-needs-info`, NEW `canon-backport`

**Story ID:** STORY-1008
**Scope:** Medium
**Epic:** STORY-1000 (Cross-Repo Canon Alignment)
**Work-Stream:** B — Morris canon enforcement
**Priority:** P1 (after STORY-1005, STORY-1006)
**Repo touched:** `tech-dev-agents`
  - `deployment/vm/skills/morris/start-story/SKILL.md` (NEW Morris-side override)
  - `deployment/vm/skills/morris/answer-needs-info/SKILL.md` (NEW Morris-side override)
  - `deployment/vm/skills/morris/canon-backport/SKILL.md` (NEW)
  - `tech_dev_agents/morris/canon_loader/` (NEW Python helper)
**Date:** 2026-05-18
**Reporter:** Mark Oreta
**Status:** Phase 1 — seed in progress
**Frontend:** false

---

## Problem

Three Morris workflows currently operate **canon-blind**:

1. **`start-story`** — when Morris assigns a story to Dan / Derrick, the seed bundle handed off does not carry domain canon. For data-pipeline stories (Monday.com tag `domain:data-pipeline`), the agent has to discover `gc-data-v2/sources/<src>/README.md` and the right `platform/*.md` sections on its own. Most of the time it doesn't, and re-derives invariants that are already written down. This is the upstream cause of "the same lesson keeps being learned" in the retro on 2026-05-04.

2. **`answer-needs-info`** — when an agent posts a `needs_info` question, Morris drafts an ANSWER.md by reasoning from current context. The skill does not check whether the question is already answered in a canon doc that should have been loaded. Result: Morris re-decides things that are already decided in `gc-data-v2/platform/`, sometimes contradicting them.

3. **No Morris-side `canon-backport`** — STORY-1003 builds the framework-side `canon-backport` skill that fires from `complete-story`. But Morris is the entity that *operates* `complete-story` at fleet scale: when a completed story's `refinement-report.md` / commit message / closing comment flags a canon gap, Morris is the one who must open the PR to `gc-data-v2/platform/`. There is no skill that wraps this for Morris's environment.

Note on framework vs Morris skills: the framework skills live at `/mnt/c/Projects/tech-dev-agents/.sdlc/skills/{start-story,answer-needs-info,complete-story}`. There is no current Morris-side override at `deployment/vm/skills/morris/start-story/` etc. This story creates Morris-side overrides (which the Morris agent uses preferentially when present) rather than mutating the framework skills, so the dev-agent fleet's `start-story` is unaffected.

## Goal

1. **Morris-side `start-story/SKILL.md`** — wraps the framework `start-story` flow with: detect Monday.com `domain:data-pipeline` tag → auto-load `gc-data-v2/sources/<src>/README.md` + cited `platform/*.md` sections → attach to the seed bundle handed to the assigned agent. Record the loaded canon shas in `.project`.

2. **Morris-side `answer-needs-info/SKILL.md`** — wraps the framework flow with: before drafting ANSWER.md, check whether the question text is answered in the loaded canon (the docs attached to the story's seed bundle). If yes, the ANSWER.md is a citation, not a re-decision. If no, proceed with the original framework flow.

3. **NEW Morris-side `canon-backport/SKILL.md`** — fired by `complete-story` when the closing artifacts contain a `canon-gap` tag. Opens a PR against `gc-data-v2/platform/` (or `tech-gc-knowledgebase/wiki/` for business-context gaps) with the proposed canon update; creates a dependent Monday.com task linked to the original story.

## Scope

### 1. `deployment/vm/skills/morris/start-story/SKILL.md` (NEW)

- Frontmatter (`name: start-story`, `agent: morris`, `category: workflow-orchestration`).
- Step 1: read Monday.com task tags via existing MCP integration (`mcp__claude_ai_monday_com__get_board_items_page`).
- Step 2: if any tag matches `domain:data-pipeline`, identify the source by parsing the seed `# Title` or `repo` field (`spapi-reports-v2` → source `spapi-reports`, etc.) — keep a small mapping table in `tech_dev_agents/morris/canon_loader/source_map.py`.
- Step 3: fetch the source README at `https://raw.githubusercontent.com/hpi-gorillacommerce/gc-data-v2/<sha>/sources/<src>/README.md` (sha from `/home/hermes/state/morris/canon-pins.yaml`; fall back to `main`).
- Step 4: parse the source README for cross-references to `platform/*.md`; fetch each referenced section.
- Step 5: write the loaded canon docs to `features/<story-folder>/canon/` (sources/spapi-reports.md, platform/pipeline-standard.md, etc.).
- Step 6: append a "Loaded canon" section to the seed referencing the local copies; record the gc-data-v2 sha in `.project` under `gc_data_v2_commit:`.
- Step 7: delegate to the framework `start-story` flow with the augmented seed bundle.
- **Note:** this skill does not implement the framework `start-story` from scratch. It runs the canon-loader prelude, then invokes `/start-story` (the framework skill) so all the normal worktree-creation / Asana-claim / branch-creation logic is reused.

### 2. `deployment/vm/skills/morris/answer-needs-info/SKILL.md` (NEW)

- Frontmatter (`name: answer-needs-info`, `agent: morris`).
- Step 1: read the question via `GET /api/dispatch/needs-info/<story_id>` (existing endpoint).
- Step 2: list files in `features/<story-folder>/canon/` (created by Morris-side `start-story` above). If empty and tag is `domain:data-pipeline`, load on-the-fly.
- Step 3: grep canon for keywords from the question (tokenize, drop stopwords, match against headings and body text).
- Step 4: if a match ≥ threshold found, draft ANSWER.md as a citation: `> Per gc-data-v2/platform/pipeline-standard.md § "Failure-Recovery Invariants": <quoted sentence>. Therefore: <answer>.`
- Step 5: if no match, fall through to the framework `answer-needs-info` flow (Morris reasons from current context). Record `canon_consulted: true, canon_match: false` in the answer audit log so the gap is visible to the retro skill.
- Step 6: write ANSWER.md, POST it to the dispatch service, ack the question.

### 3. `deployment/vm/skills/morris/canon-backport/SKILL.md` (NEW)

- Frontmatter (`name: canon-backport`, `agent: morris`, `category: knowledge-management`).
- Trigger: `complete-story` invokes this skill when:
  - `features/<story-folder>/refinement-report.md` contains `canon-gap: true`, OR
  - The PR description has a `Canon-Gap:` line, OR
  - The closing Monday.com comment contains the keyword `canon-gap`.
- Step 1: parse the gap statement (free-text description of what canon was missing).
- Step 2: classify target repo:
  - Technical / platform invariant → `gc-data-v2/platform/`
  - Operational runbook → `gc-data-v2/platform/runbooks/` (post-STORY-1015)
  - Business context → `tech-gc-knowledgebase/wiki/`
- Step 3: draft the canon update as a diff (additive section in the right doc, with citation back to the closing story).
- Step 4: open the PR via `gh pr create` from a new branch `morris/canon-backport-<source-story-id>` against the target repo's `main`.
- Step 5: create a dependent Monday.com task on the source story's parent (or the source story itself) titled "Canon-backport PR <link> for STORY-<NNN>".
- Step 6: comment on the source PR / closed story with the canon-backport PR link.
- **Idempotency:** if a canon-backport PR for the same source-story-id already exists open, update it instead of opening a duplicate.

### 4. `tech_dev_agents/morris/canon_loader/`

- `__init__.py`
- `loader.py` — `load_canon_for_source(src: str, pin_sha: str | None) -> CanonBundle`; uses urllib + the pin file.
- `source_map.py` — dict mapping repo slugs to source slugs (`walmart-supplier-v2` → `walmart-supplier`, etc.).
- `keyword_matcher.py` — `find_canon_answer(question: str, canon_files: list[Path]) -> CanonMatch | None`. Naive token-overlap heuristic (no embeddings); threshold tuned to favor precision over recall (false-positive citations are worse than false-negative fall-throughs).
- `tests/test_loader.py`, `tests/test_keyword_matcher.py`.

## Out of scope

- Modifying the framework skills at `.sdlc/skills/{start-story,answer-needs-info,complete-story}` (those are STORY-1001 / STORY-1003's territory).
- Building the framework-side `canon-backport` (STORY-1003).
- Embedding-based semantic matching (deterministic keyword matching only).
- Auto-merging the canon-backport PR (data team reviews).
- Tag detection beyond `domain:data-pipeline` (other domain tags are future work).
- Migrating runbook content out of `data-pipelines-runbooks` (STORY-1015).
- The freshness frontmatter on `tech-gc-knowledgebase` (STORY-1016).

## Success criteria

| ID | Criterion | Pass / fail signal |
|---|---|---|
| **SC-1** | Three new Morris skill files exist with valid frontmatter and `agent: morris`. | `for s in start-story answer-needs-info canon-backport; do head -10 deployment/vm/skills/morris/$s/SKILL.md \| grep -q "agent: morris"; done` returns 0 for all three. |
| **SC-2** | `canon_loader.loader.load_canon_for_source("spapi-reports", "main")` returns a `CanonBundle` with ≥1 file from `sources/` and ≥1 file from `platform/`. | `pytest tech_dev_agents/morris/canon_loader/tests/test_loader.py::test_load_spapi -v` PASSES (uses HTTP mock). |
| **SC-3** | `source_map.py` covers all current v2 repos: `spapi-reports-v2`, `walmart-supplier-v2`, `walmart-ad-connect-v2`, `shopify-v2`, `api-advertising-amazon`, `levanta-v2`. | `pytest test_source_map.py::test_all_v2_repos_mapped -v` PASSES. |
| **SC-4** | Morris-side `start-story` invoked on a story tagged `domain:data-pipeline` in a v2 repo writes ≥1 file to `features/<story-folder>/canon/` and adds `gc_data_v2_commit:` to `.project`. | Documented in skill verification block; live test: invoke skill on a dummy story; assert directory + .project line. |
| **SC-5** | `keyword_matcher.find_canon_answer()` returns a match for a question whose answer is verbatim in a loaded canon doc. | `pytest test_keyword_matcher.py::test_verbatim_question_matches -v` PASSES. |
| **SC-6** | `keyword_matcher` returns `None` for an unrelated question (precision over recall). | `pytest test_keyword_matcher.py::test_unrelated_question_no_match -v` PASSES; threshold tuned in `keyword_matcher.py`. |
| **SC-7** | Morris-side `answer-needs-info` on a question with a canon-answer produces an ANSWER.md whose body cites the canon doc. | Live test: post a needs_info question with known canon answer; invoke skill; assert ANSWER.md body contains `Per gc-data-v2/platform/`. |
| **SC-8** | Morris-side `answer-needs-info` on a question with no canon match falls through to the framework flow and logs `canon_consulted: true, canon_match: false`. | Live test: post unrelated question; verify ANSWER.md exists and audit log has the `false` entry. |
| **SC-9** | `canon-backport` invoked on a story with `canon-gap: true` opens a PR against `gc-data-v2`. | Live test: dummy story; `gh pr list --repo hpi-gorillacommerce/gc-data-v2 --search "STORY-XXXX canon-backport" --state open --json url` returns one PR. |
| **SC-10** | `canon-backport` creates a dependent Monday.com task linked to the source story. | Live test: assert via `mcp__claude_ai_monday_com__search` or board API; one task exists with title containing `Canon-backport PR`. |
| **SC-11** | `canon-backport` is idempotent — re-running on the same source story updates the existing PR rather than creating a new one. | `pytest test_canon_backport.py::test_idempotent_pr -v` PASSES; live re-run verifies single open PR. |
| **SC-12** | Morris's skill inventory lists all three new skills. | `claude-sdk -p "list available skills" -w /opt/agent \| grep -E "^- start-story\|^- answer-needs-info\|^- canon-backport"` returns 3 lines. |

## Files to modify

| Path | Action |
|---|---|
| `deployment/vm/skills/morris/start-story/SKILL.md` | CREATE |
| `deployment/vm/skills/morris/answer-needs-info/SKILL.md` | CREATE |
| `deployment/vm/skills/morris/canon-backport/SKILL.md` | CREATE |
| `tech_dev_agents/morris/__init__.py` | CREATE if missing |
| `tech_dev_agents/morris/canon_loader/__init__.py` | CREATE |
| `tech_dev_agents/morris/canon_loader/loader.py` | CREATE |
| `tech_dev_agents/morris/canon_loader/source_map.py` | CREATE |
| `tech_dev_agents/morris/canon_loader/keyword_matcher.py` | CREATE |
| `tech_dev_agents/morris/canon_loader/tests/__init__.py` | CREATE |
| `tech_dev_agents/morris/canon_loader/tests/test_loader.py` | CREATE |
| `tech_dev_agents/morris/canon_loader/tests/test_source_map.py` | CREATE |
| `tech_dev_agents/morris/canon_loader/tests/test_keyword_matcher.py` | CREATE |
| `tech_dev_agents/morris/canon_loader/tests/test_canon_backport.py` | CREATE (skill is markdown-only, but the helper logic for PR drafting lives in `tech_dev_agents/morris/canon_loader/backport.py` and is tested here) |
| `tech_dev_agents/morris/canon_loader/backport.py` | CREATE — `draft_backport_pr(gap_text, target_repo, source_story_id) -> BackportDraft`; idempotency via `find_existing_backport_pr()` |

## Files to NOT modify

- **`.sdlc/skills/start-story/SKILL.md`** — framework-side; STORY-1001/1008 framework-half is in WS-A, not here.
- **`.sdlc/skills/answer-needs-info/SKILL.md`** — same.
- **`.sdlc/skills/complete-story/SKILL.md`** — same. (Hook from complete-story to Morris canon-backport is a STORY-1003 concern.)
- `deployment/vm/skills/morris/review-prs/SKILL.md` (STORY-1007).
- `deployment/vm/skills/morris/merge/SKILL.md` (STORY-1007).
- `deployment/vm/skills/morris/canon-check/SKILL.md` (STORY-1005).
- `deployment/vm/skills/morris/pre-dispatch-validate/SKILL.md` (STORY-1006).
- `tech_dev_agents/morris/canon_check/`, `tech_dev_agents/morris/pre_dispatch/`, `tech_dev_agents/morris/review_helpers/` (sibling stories own them).
- `gc-data-v2/platform/**` — read-only from here.
- Monday.com board structure (no column adds; only standard task creates via MCP).

## Verification plan

| SC | Command | Expected output |
|---|---|---|
| SC-1 | `for s in start-story answer-needs-info canon-backport; do head -10 deployment/vm/skills/morris/$s/SKILL.md \| grep -c "agent: morris"; done` | Three `1`s |
| SC-2 | `pytest tech_dev_agents/morris/canon_loader/tests/test_loader.py::test_load_spapi -v` | PASSED |
| SC-3 | `pytest tech_dev_agents/morris/canon_loader/tests/test_source_map.py::test_all_v2_repos_mapped -v` | PASSED |
| SC-4 | (Live) `claude-sdk -p "Run morris start-story on STORY-TEST in walmart-supplier-v2 (dummy)" -w /opt/agent && ls features/story-TEST-*/canon/ && grep gc_data_v2_commit features/story-TEST-*/.project` | Canon directory non-empty; `.project` contains the line |
| SC-5 | `pytest test_keyword_matcher.py::test_verbatim_question_matches -v` | PASSED |
| SC-6 | `pytest test_keyword_matcher.py::test_unrelated_question_no_match -v` | PASSED |
| SC-7 | Post needs_info: `curl -X POST .../api/dispatch/needs-info/STORY-TEST -d '{"question":"What is the retry policy for upstream timeouts?"}'`; invoke skill; `cat features/story-TEST-*/ANSWER.md \| head -3` | Body starts `Per gc-data-v2/platform/...` |
| SC-8 | Post unrelated question (`"What color is the dashboard button?"`); invoke skill; check audit log `cat /home/hermes/state/morris/needs-info-audit.jsonl \| tail -1 \| jq .canon_match` | `false` |
| SC-9 | (Live) Run `canon-backport` on dummy story with `canon-gap: true`; `gh pr list --repo hpi-gorillacommerce/gc-data-v2 --search "STORY-TEST canon-backport" --state open --json url --jq '. \| length'` | `1` |
| SC-10 | `mcp__claude_ai_monday_com__search` for "Canon-backport PR" linked to STORY-TEST | Returns ≥1 task |
| SC-11 | Re-run `canon-backport`; same gh pr list query | Still `1`, not `2` |
| SC-12 | `claude-sdk -p "What Morris skills are available?" -w /opt/agent \| grep -cE "^- (start-story\|answer-needs-info\|canon-backport)"` | `3` |

## Boundaries

| Always do | Ask first | Never do |
|---|---|---|
| Treat the Morris-side `start-story` as a *prelude* to the framework `start-story`, not a replacement — chain to the framework skill at Step 7. | Add a domain tag beyond `domain:data-pipeline` (e.g. `domain:frontend`). | Modify any file under `.sdlc/skills/` (those are framework-owned and shipped via submodule). |
| Use deterministic keyword matching with precision >> recall — false-positive citations are worse than missing them. | Tune the keyword-match threshold (default in `keyword_matcher.py`). | Use embeddings or LLM judgment in `keyword_matcher` (must be deterministic and fast). |
| Cite the loaded-canon files in the seed bundle so the assigned agent can grep them locally. | Add a new target repo to `canon-backport` beyond `gc-data-v2` / `tech-gc-knowledgebase`. | Open a canon-backport PR to a `*-v2` consumer repo — backports go to `gc-data-v2`, not pipelines. |
| Make `canon-backport` idempotent (existing-PR detection by branch name + source-story-id). | Open a Monday.com task on a board other than the source story's board. | Approve / merge the canon-backport PR (data team owns review). |
| Log every `canon_consulted` outcome (match / no-match / fall-through) to `/home/hermes/state/morris/needs-info-audit.jsonl` for retro analysis. | Auto-merge the canon-backport PR even if CI passes. | Auto-close the source story until the canon-backport PR is open (chain ordering matters). |

## Done looks like (terminal transcript)

```
$ pytest tech_dev_agents/morris/canon_loader/tests/ -v
test_loader.py::test_load_spapi PASSED
test_loader.py::test_load_walmart_supplier PASSED
test_loader.py::test_pin_sha_honored PASSED
test_loader.py::test_missing_source_404 PASSED
test_source_map.py::test_all_v2_repos_mapped PASSED
test_keyword_matcher.py::test_verbatim_question_matches PASSED
test_keyword_matcher.py::test_paraphrased_question_matches PASSED
test_keyword_matcher.py::test_unrelated_question_no_match PASSED
test_canon_backport.py::test_draft_targets_platform_md PASSED
test_canon_backport.py::test_idempotent_pr PASSED
test_canon_backport.py::test_business_context_targets_knowledgebase PASSED
================ 11 passed in 0.34s ================

# Live: run Morris-side start-story on dummy data-pipeline story
$ claude-sdk -p "Run start-story for STORY-TEST (walmart-supplier-v2, tag domain:data-pipeline)" -w /opt/agent
[…canon load output…]
$ ls features/story-TEST-*/canon/
sources/walmart-supplier.md
platform/pipeline-standard.md
platform/failure-modes.md
$ grep gc_data_v2_commit features/story-TEST-*/.project
gc_data_v2_commit: 7f3a8e2…

# Live: needs_info with canon-answer
$ cat features/story-TEST-*/ANSWER.md
> Per gc-data-v2/platform/pipeline-standard.md § "Retry Policy":
> "Upstream timeouts MUST be retried with exponential backoff capped at 5 attempts."
> Therefore: use exponential backoff, cap=5, base=2s.

# Live: canon-backport
$ gh pr list --repo hpi-gorillacommerce/gc-data-v2 --search "STORY-TEST canon-backport" --state open --json url
[{"url":"https://github.com/hpi-gorillacommerce/gc-data-v2/pull/142"}]
```

## Escalation contract

- **Source mapping ambiguous** (e.g. a repo not in `source_map.py`) — Morris's `start-story` `needs_info`s Mark with the repo slug; never guesses.
- **Canon-backport target unclear** (gap could plausibly go in `gc-data-v2/platform/` OR `tech-gc-knowledgebase/wiki/`) — open the PR as a draft against `gc-data-v2` AND DM Mark for adjudication. Don't open two PRs.
- **Data team rejects a canon-backport PR** — Morris must NOT auto-retry; pause `canon-backport` for that story and DM Mark with the rejection reason (per epic seed escalation contract item 3).
- **Keyword-matcher false-positive citation in answer-needs-info** — the originating agent or Mark can comment `@morris recheck-canon` on the dispatch needs_info row; Morris regenerates with `canon_consulted: false` flag set, triggering fallback flow.
- **Monday.com task creation fails** (rate-limit, auth, board-id wrong) — log + DM Mark; do NOT silently swallow.


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

Medium scope: `1 → 6 → 7 → 8 → Done`.

- Phase 1 (this seed): scope locked
- Phase 6 (design): three SKILL.md outlines, source-map schema, keyword-matcher algorithm + threshold, canon-backport PR template
- Phase 7 (test design): 11 unit tests in SC-2..SC-3..SC-5..SC-6..SC-11 (RED before Phase 8)
- Phase 8 (implementation): helpers → three skill files → deploy via `push-code.sh morris` → live tests against dummy stories

## Dependencies

- **Hard:** STORY-1005 (provides the `canon-pins.yaml` schema this story consumes, if STORY-1013 hasn't shipped yet, default to `main`).
- **Soft (parallel):** STORY-1003 (framework-side `canon-backport`). The two `canon-backport` skills (framework + Morris) are companions: framework one fires from `complete-story` in any agent context; Morris one fires from Morris's `complete-story` invocations specifically. They share the helper module (`tech_dev_agents/morris/canon_loader/backport.py`).
- **Soft:** STORY-1015 (when `data-pipelines-runbooks` merges into `gc-data-v2/platform/runbooks/`, update `backport.py` target classifier to route operational gaps to that subdir).
- **Downstream consumer:** retro workflow — needs_info audit log feeds the retro skill's canon-gap detection.

## Why the Morris-side override pattern

Mark's instruction was to modify `deployment/vm/skills/morris/start-story/SKILL.md`. That file doesn't exist; the current `start-story` is at `.sdlc/skills/start-story/` (framework-owned, shipped via submodule). Two options were considered:

- **A. Modify framework skill in-place** — couples dev-agent fleet's `start-story` to Morris-specific canon-loading. Rejected: dev agents don't always have access to Morris's state files; framework changes ship through a different submodule update flow; STORY-1001 already owns build_type-aware modifications.
- **B. Morris-side override (chosen)** — Morris invokes its own `start-story` which loads canon, then chains to the framework `start-story`. Skill-resolution precedence in `claude-sdk` favors the agent-local `deployment/vm/skills/morris/` path over `.sdlc/skills/`. Clean separation, no submodule churn, framework upgrades remain safe.

If Mark prefers option A, that is the call-out at Phase 6.
