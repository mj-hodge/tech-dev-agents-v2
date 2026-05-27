# STORY-1016 — tech-gc-knowledgebase freshness frontmatter + CI + weekly Morris scan + complete-story auto-PR loop

**Story ID:** STORY-1016
**Scope:** Medium
**Epic:** STORY-1000 (Cross-Repo Canon Alignment)
**Work-Stream:** D — Knowledgebase curation
**Repos touched (cross-repo):**
- `tech-gc-knowledgebase` (primary — frontmatter backfill, freshness-check workflow, ownership convention)
- `tech-dev-agents` (Morris weekly scan skill + complete-story auto-PR draft hook)
**Date:** 2026-05-18
**Status:** Phase 1 — seed draft, gated awaiting approval
**Frontend:** false

---

## Problem

`tech-gc-knowledgebase` is the org's business / domain wiki — 142 markdown pages under `wiki/` covering systems, processes, products, glossary, research. Three structural problems make it a graveyard rather than a knowledgebase:

1. **No freshness signal.** Sampled wiki pages (`wiki/systems/advertising-amazon.md`, `wiki/processes/data-pipeline-operations.md`) have no YAML frontmatter — no `last-reviewed`, no `owner`, no `freshness-sla`. The only signal of staleness is `git log` — invisible to a reader landing on a page.

2. **No freshness gate.** A PR can edit any wiki page without updating its `last-reviewed`. There is no CI check. Stale content accumulates silently.

3. **No write-back loop from completed stories.** When `complete-story` (`tech-dev-agents/deployment/vm/skills/morris/complete-story/`) closes a story whose summary contains domain knowledge ("we decided X because Y", "the business rule for Z is..."), nothing drafts a wiki page or update. The knowledge stays in the closed story's seed/refinement-report and the wiki ages further.

The epic SC-6 promises freshness enforcement on every PR. The epic's canon-backport loop (STORY-1003 framework side, STORY-1008 Morris side) handles `gc-data-v2/platform/` write-back; this story handles the **wiki** (business/domain) write-back.

## Goal

Four coupled deliverables:

1. **Frontmatter backfill.** Add YAML frontmatter to every `tech-gc-knowledgebase/wiki/**/*.md` page:
   ```yaml
   ---
   last-reviewed: YYYY-MM-DD       # init: git-log last-edit date of the file
   owner: <team-or-individual>     # init: best-guess; team can edit
   freshness-sla: 90               # days; default 90
   ---
   ```

2. **Freshness CI gate.** New workflow `tech-gc-knowledgebase/.github/workflows/freshness-check.yml`. On `pull_request`: for each `wiki/**/*.md` file in the diff, assert `last-reviewed:` in the file's frontmatter is `>= today - <days-since-last-PR>`. Simpler v1 spec: if a wiki page is **touched** in the PR, `last-reviewed:` must have been updated in that same PR (i.e. the diff for that file must include `last-reviewed:`).

3. **Weekly Morris scan.** New Morris skill `tech-dev-agents/deployment/vm/skills/morris/kb-freshness-scan/`. Runs weekly via crontab. Walks all `wiki/**/*.md`, parses frontmatter, computes `(today - last-reviewed) > freshness-sla`. Opens (or updates) a single GitHub issue per stale page on `tech-gc-knowledgebase` titled `Stale: <relative-path>` with `last-reviewed`, `owner`, `days-overdue`.

4. **Complete-story auto-PR loop.** Hook into Morris's `complete-story` skill: when the story summary, refinement-report, or seed contains markers of domain knowledge (regex/keyword set: "decided", "business rule", "this is how X works", "policy", "convention", explicit `canon_gap: true` in the seed), Morris drafts a PR against `tech-gc-knowledgebase` with a new or updated `wiki/` page. Companion to STORY-1003's framework-side `canon-backport` (which targets `gc-data-v2/platform/`).

## Scope

**In:**
- Compute `last-reviewed` init date for every `wiki/**/*.md` via `git log -1 --format=%cs <file>` (most recent committer date).
- Owner field initialization: best-effort first pass; default to `unowned` if no clear signal. Add a one-shot script (`scripts/backfill_owners.py`) that accepts an optional `wiki-owners.csv` overlay if the team supplies one mid-PR.
- Frontmatter insertion preserves existing file body. If a file already has frontmatter (audit found 0 such files at seed time), merge keys rather than overwrite.
- `freshness-check.yml` workflow.
- Morris `kb-freshness-scan` skill (`SKILL.md` + `scan.py`) + crontab entry.
- Morris `complete-story` modification: detect domain-knowledge markers, draft `tech-gc-knowledgebase` PR.
- RED test set for: backfill script idempotence, freshness-check failure case, scan-opens-issue case, complete-story auto-PR case.

**Out:**
- Subject-matter rewrite of any wiki page content. Frontmatter only; bodies untouched in the backfill PR.
- Defining the "owner" canonical list (which teams exist, who owns what). Backfill uses `unowned` as a safe default; the team can iterate.
- Decommissioning pages that are far past their SLA (Morris opens issues; humans decide to update or delete).
- Auto-PR loop for `gc-data-v2/platform/` (that's STORY-1003 / STORY-1008's `canon-backport`).
- Changes to `tech-gc-knowledgebase/README.md` structure or wiki navigation.

## Out of scope (explicit Do-Not-Do)

- Do NOT rewrite wiki page bodies — frontmatter only.
- Do NOT delete any wiki page, even if it appears stale at backfill time.
- Do NOT change the `complete-story` skill's existing behavior (move story to Done, update Monday, merge worktree). Only ADD the optional draft-PR side-effect.
- Do NOT auto-merge the draft PRs created by `complete-story` — Mark/Morris reviews like any other PR.
- Do NOT scan or modify `tech-gc-knowledgebase/wiki/research/` raw notes if any (Phase 6 confirms whether `wiki/research/` should be in scope; default in-scope unless excluded).

## Success criteria

- **SC-1** — Every file under `/mnt/c/Projects/tech-gc-knowledgebase/wiki/**/*.md` starts with a valid YAML frontmatter block containing keys `last-reviewed`, `owner`, `freshness-sla`. Verified by: `python scripts/verify_frontmatter.py wiki/` exits 0; count = 142 (or current page count at PR time).
- **SC-2** — `ls /mnt/c/Projects/tech-gc-knowledgebase/.github/workflows/freshness-check.yml` exists; triggers on `pull_request` with `paths: ['wiki/**/*.md']`.
- **SC-3** — Test PR that edits `wiki/systems/advertising-amazon.md` body without bumping `last-reviewed:` fails the `freshness-check` job; the failure log names the offending file.
- **SC-4** — Same PR with `last-reviewed:` updated to today passes the job.
- **SC-5** — Morris weekly scan script (`scan.py`) on a fixture wiki dir with one page set to `last-reviewed: 2025-01-01, freshness-sla: 90` opens (in dry-run output) one issue with title `Stale: <path>` and body containing `days-overdue: <int >= 1>`. Verified by: `python scan.py --dry-run --root <fixture>` stdout.
- **SC-6** — On real `tech-gc-knowledgebase`: `crontab -l \| grep kb-freshness-scan` on Morris VM shows one weekly entry.
- **SC-7** — `complete-story` regression test: close a synthetic story whose `seed.md` contains `canon_gap: true` and a refinement-report with the phrase "the business rule is..." → Morris drafts a PR against `tech-gc-knowledgebase` containing a new `wiki/` entry referencing the source story. PR is OPEN (not auto-merged). Verified by: `gh -R hpi-gorillacommerce/tech-gc-knowledgebase pr list --search "STORY-XXXX wiki" --state open` returns 1 row.
- **SC-8** — Replays epic incident class "retro proposals not shipped": a test story flagged `canon_gap: true` always emits the draft PR; the queue cannot close the story without it.
- **SC-9** — Re-running the backfill script is idempotent: second run produces no diff.

## Files to modify (grouped by repo)

### `tech-gc-knowledgebase` (LARGE diff, single-purpose)
- `wiki/**/*.md` — frontmatter inserted at top of every file (142 files at audit time).
- `.github/workflows/freshness-check.yml` — NEW PR-gating workflow.
- `scripts/backfill_frontmatter.py` — NEW one-shot used by initial PR (kept in repo for reruns).
- `scripts/verify_frontmatter.py` — NEW verifier invoked by workflow + manual checks.
- `wiki-owners.csv` — NEW (optional overlay; empty or seed-row at first).
- `.github/CODEOWNERS` — optional add for wiki files (defer if team owners unclear).
- `README.md` — add a short "Freshness conventions" section pointing to the frontmatter schema + SLA.

### `tech-dev-agents`
- `deployment/vm/skills/morris/kb-freshness-scan/SKILL.md` — NEW skill spec.
- `deployment/vm/skills/morris/kb-freshness-scan/scan.py` — NEW scanner.
- `deployment/vm/skills/morris/complete-story/SKILL.md` — MODIFY (add "domain-knowledge detection → draft wiki PR" branch).
- `deployment/vm/skills/morris/complete-story/auto_pr_wiki.py` — NEW helper for the draft-PR path.
- `deployment/vm/morris-crontab.txt` (or wherever crontab is canonicalized) — add weekly entry.
- `features/story-1016-kb-freshness-and-autoflow/seed.md` (this file)
- `features/story-1016-kb-freshness-and-autoflow/feature-spec.md` (Phase 6)
- `features/story-1016-kb-freshness-and-autoflow/test-design.md` (Phase 7)

## Files to NOT modify

- Any `wiki/**/*.md` body content — frontmatter additions ONLY.
- `tech-gc-knowledgebase/wiki/processes/data-pipeline-operations.md` line 95-96 (STORY-1015 owns that fix).
- `tech-gc-knowledgebase/wiki/systems/data-platform-specs.md` (STORY-1015 creates it).
- `gc-data-v2/**` — separate write-back loop (STORY-1003 / STORY-1008).
- Existing Morris skills other than `complete-story` and the new `kb-freshness-scan`.
- Monday.com integration code paths.

## Verification plan

| Check | Command | Expected output |
|---|---|---|
| Frontmatter present on every wiki page | `python /mnt/c/Projects/tech-gc-knowledgebase/scripts/verify_frontmatter.py /mnt/c/Projects/tech-gc-knowledgebase/wiki/` | Exit 0; `OK: 142 files validated` |
| Frontmatter schema correct | `head -6 /mnt/c/Projects/tech-gc-knowledgebase/wiki/systems/advertising-amazon.md` | Lines `---`, `last-reviewed: YYYY-MM-DD`, `owner: ...`, `freshness-sla: 90`, `---`, blank |
| Backfill idempotent | Run `python scripts/backfill_frontmatter.py wiki/` twice; `git status` after second run | Working tree clean |
| Freshness workflow registered | `gh -R hpi-gorillacommerce/tech-gc-knowledgebase workflow list \| grep freshness-check` | Row present |
| RED: PR touching wiki page without bumping `last-reviewed:` | Open dummy PR editing `wiki/systems/advertising-amazon.md` body only → `gh pr checks <PR>` | `freshness-check` FAILED; log lists `wiki/systems/advertising-amazon.md: last-reviewed not updated` |
| GREEN: same PR with `last-reviewed:` updated | Update frontmatter line; push → `gh pr checks <PR>` | `freshness-check` PASSED |
| Morris scan dry-run | `python /mnt/c/Projects/tech-dev-agents/deployment/vm/skills/morris/kb-freshness-scan/scan.py --root <fixture-dir> --dry-run` | stdout: `Would-open issue: Stale: <path> (days-overdue: N)` |
| Morris scan idempotent (no duplicate issues) | Run live twice in quick succession | Second run: `No new issues — existing stale-issues still open` |
| Cron entry | `ssh morris@<vm> 'crontab -l \| grep kb-freshness-scan'` | One row, weekly cadence |
| Complete-story auto-PR triggers on `canon_gap: true` | Run `complete-story STORY-9998` (test story with `canon_gap: true` in seed) → `gh -R hpi-gorillacommerce/tech-gc-knowledgebase pr list --search "STORY-9998" --state open` | 1 row |
| Complete-story does NOT auto-PR on plain bug-fix story | Run `complete-story STORY-9997` (no canon_gap, no domain-knowledge markers) | No PR created |
| Replay SC-8 (epic): retro-proposal-style story closes with a wiki draft | Run `complete-story` on synthetic retro story | Draft PR open, linked from closed story comment |
| Pytest suite | `pytest /mnt/c/Projects/tech-dev-agents/tests/morris/test_kb_freshness_scan.py /mnt/c/Projects/tech-dev-agents/tests/morris/test_complete_story_wiki_autopr.py -v` | All GREEN after Phase 8 |

## Boundaries

| Always Do | Ask First | Never Do |
|---|---|---|
| Use `git log -1 --format=%cs <file>` for `last-reviewed:` init | Decide which team is "owner" for each wiki section — surface the wiki-owners.csv overlay to the team for review during Phase 6 | Modify wiki page bodies during backfill — frontmatter ONLY |
| Make backfill idempotent (rerunning is a no-op) | Lower `freshness-sla` below 90 days globally — Mark approves the default | Auto-merge a draft PR created by `complete-story` |
| Open at most ONE issue per stale page (de-dup by title) | Add CODEOWNERS rules — Phase 6 surfaces the question | Delete any wiki page even when "stale: 2 years overdue" |
| Link the draft auto-PR back to the source story in the PR body | Include `wiki/research/` in scope — Phase 6 confirms with team | Skip the frontmatter on a wiki page because "this is a navigation stub" — every `.md` gets frontmatter |
| Run Phase 7 RED tests BEFORE shipping freshness-check.yml | Bump default `freshness-sla` on a subset of pages (e.g. glossary = 365) | Page Mark from the weekly scan; cron output is for issues only |

## Done looks like

```
$ python /mnt/c/Projects/tech-gc-knowledgebase/scripts/verify_frontmatter.py /mnt/c/Projects/tech-gc-knowledgebase/wiki/
OK: 142 files validated. 0 missing frontmatter. 0 invalid SLA values.

$ head -6 /mnt/c/Projects/tech-gc-knowledgebase/wiki/systems/advertising-amazon.md
---
last-reviewed: 2025-11-08
owner: data-platform
freshness-sla: 90
---

$ gh -R hpi-gorillacommerce/tech-gc-knowledgebase workflow list | grep freshness-check
freshness-check    active

$ ssh morris@<vm> 'crontab -l | grep kb-freshness-scan'
0 13 * * 1  /opt/agent/skills/morris/kb-freshness-scan/scan.py >> /var/log/morris/kb-freshness.log 2>&1

# Replay SC-8 (epic incident class "retro proposals not shipped"):
$ complete-story STORY-test-canon-gap
[morris] story closed → draft PR opened: tech-gc-knowledgebase#1234
$ gh -R hpi-gorillacommerce/tech-gc-knowledgebase pr view 1234
state: OPEN
title: STORY-test-canon-gap — wiki draft: <inferred topic>
body: (links to closed story; PR awaits review)
```

## Escalation contract

- If the backfill PR is too large to review (142 frontmatter additions = noisy diff), split by top-level wiki section (`systems/`, `processes/`, `products/`, `glossary/`, `people/`, `company/`, `research/`, `trends/`) into 8 sub-PRs. Decision in Phase 6.
- If the `complete-story` auto-PR loop produces low-signal drafts (false positives), Mark may want a manual gate ("Morris asks before drafting"). Phase 6 surfaces this as a config flag `auto_draft_wiki_pr: true|ask|false`.
- If `wiki-owners.csv` cannot be sourced cleanly, default ALL pages to `owner: unowned` and file a follow-up story to set ownership.
- If the freshness-check workflow conflicts with existing repo automation (CODEOWNERS auto-review, branch protection), STOP and ask before changing branch-protection settings.


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

**Medium scope → `1 → 6 → 7 → 8 → Done`.**

Phase 6 decides: (a) split-PR strategy (1 big vs 8 small), (b) owner backfill source, (c) `complete-story` domain-knowledge detector (regex vs LLM gate), (d) `auto_draft_wiki_pr` gate semantics, (e) crontab cadence + time-of-day. Phase 7 writes RED tests for verify_frontmatter, backfill idempotence, freshness-check failure, scan-opens-issue, complete-story auto-PR. Phase 8 (a) ships backfill PR(s), (b) ships freshness-check workflow, (c) ships Morris scan + crontab, (d) modifies complete-story + test the auto-PR path end-to-end. Done = SC-1..SC-9 verified.

## Sequencing dependency

- **Loosely depends on STORY-1015** — STORY-1015 creates `wiki/systems/data-platform-specs.md` and edits `wiki/processes/data-pipeline-operations.md`. If STORY-1015 lands first, STORY-1016's backfill includes those files automatically. If STORY-1016 lands first, STORY-1015's PRs must remember to include frontmatter on any new wiki file they create — note this in STORY-1015 review.
- **Independent of STORY-1013 / STORY-1014.**
- **Companion to STORY-1003 (framework-side `canon-backport`)** — STORY-1003 targets `gc-data-v2/platform/`; STORY-1016 targets `tech-gc-knowledgebase/wiki/`. The detector logic should be shared/symmetric.

## Domain-knowledge detector — first-pass spec

Phase 6 finalizes; this is the seed for the detector that decides whether `complete-story` should draft a wiki PR. Inputs: the closed story's `seed.md`, `feature-spec.md`, and `refinement-report.md` (if it exists). Detector returns either a topic slug + section body, or `None`.

**Positive markers (any one fires the detector):**

- Explicit `canon_gap: true` line in `seed.md` frontmatter or body.
- Keywords in body (case-insensitive): `"business rule"`, `"the rule is"`, `"the convention is"`, `"the policy is"`, `"this is how"`, `"we decided"`, `"the decision is"`, `"by convention"`.
- An H2 section literally named `## Decisions`, `## Business Rules`, `## Policy`, or `## Convention` with non-empty body.
- A `refinement-report.md` "Lessons learned" section longer than 200 chars.

**Negative markers (override and suppress):**

- Story label `bug` or `hotfix` with no decision content.
- Body explicitly contains `wiki_autopr: false`.
- All positive matches lie inside fenced code blocks (likely example text, not real decision).

**Output shape (when fired):**

```yaml
draft_pr:
  target_repo: hpi-gorillacommerce/tech-gc-knowledgebase
  target_branch: main
  source_branch: morris/wiki-autopr-STORY-<N>
  files:
    - path: wiki/<inferred-section>/<inferred-slug>.md
      content: |
        ---
        last-reviewed: <today>
        owner: <story-owner>
        freshness-sla: 90
        ---
        # <inferred title>
        <Morris-summarized body, capped at 800 words>
        ## Source
        - STORY-<N>: <link to closed Asana/Monday card>
        - PR(s): <link>
```

## Wiki page inventory at seed time (2026-05-18)

```
$ find /mnt/c/Projects/tech-gc-knowledgebase/wiki -name '*.md' | wc -l
142

$ ls /mnt/c/Projects/tech-gc-knowledgebase/wiki/
README.md company/ glossary/ people/ processes/ products/ research/ systems/ trends/

# 0 of 3 sampled files have YAML frontmatter:
$ head -1 /mnt/c/Projects/tech-gc-knowledgebase/wiki/systems/advertising-amazon.md
# advertising-amazon
$ head -1 /mnt/c/Projects/tech-gc-knowledgebase/wiki/processes/data-pipeline-operations.md
# Data Pipeline Operations
```

This baseline confirms the backfill scope (142 files, all need frontmatter, no merge-with-existing logic required for the first pass — though `backfill_frontmatter.py` MUST still handle existing-frontmatter idempotently because future runs will encounter the keys it just wrote).
