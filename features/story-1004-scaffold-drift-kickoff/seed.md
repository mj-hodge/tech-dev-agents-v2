# STORY-1004 — NEW skills `scaffold-drift-check` + `pipeline-kickoff`

**Story ID:** STORY-1004
**Scope:** Small
**Epic:** STORY-1000 (Cross-Repo Canon Alignment)
**Work-Stream:** A — SDLC framework knowledgebase integration
**Repo touched:** `sdlc-framework` (and the `.sdlc` submodule pointer in `tech-dev-agents`)
**Date:** 2026-05-18
**Status:** Phase 1 — seed written, gated awaiting approval
**Frontend:** false

## Problem

`gc-data-v2/pipeline-template/.github/workflows/canon-drift-check.yml` is the data team's mechanism for catching when a v2 pipeline's canonical files drift from the scaffold (PR template, canon-readback workflow, GLIBC pin in deploy-function-app.yaml). It byte-diffs `https://raw.githubusercontent.com/hpi-gorillacommerce/gc-data-v2/main/pipeline-template/<file>` against the local copy in CI. That works once the pipeline repo exists and CI is wired — but it doesn't help a Phase 6 designer who's about to choose a path that drifts from the scaffold. There's no local equivalent, no way to run drift detection during design rather than after PR submission.

Separately, `gc-data-v2/templates/pipeline-kickoff-prompt.md` is a 12-blank Mad Libs template (Source Name, source-slug, source-section, Auth Type, repo-name, pipeline, schema_line, API docs URL, etc.) the data team uses to kick off a new pipeline build. It's a doc — agents read it and copy-paste — but there's no skill that fills the blanks, validates the sources.yaml entry, and produces a ready-to-feed kickoff prompt. Result: kickoff happens by hand each time, and the sources.yaml entry might be missing entirely (epic seed § Work-Stream A line 44: "validates a `sources.yaml` entry exists for the new source").

## Goal

Two new skills. `scaffold-drift-check` runs the gc-data-v2 drift logic locally during Phase 6 (catches drift before PR, not after). `pipeline-kickoff` wraps the 12-blank template into an interactive skill that validates `sources.yaml` and outputs a ready-to-dispatch kickoff prompt.

## Scope

- **NEW skill** `/mnt/c/Projects/sdlc-framework/skills/scaffold-drift-check/SKILL.md`:
  - Local equivalent of `gc-data-v2/pipeline-template/.github/workflows/canon-drift-check.yml`.
  - Byte-diffs the following local files against `gc-data-v2/pipeline-template/<same-path>` (using the locally-cloned `gc-data-v2` checkout, not the GitHub raw URL — STORY-1001's `gc_data_v2_commit` pin gives us a known SHA):
    1. `.github/pull_request_template.md`
    2. `.github/workflows/pr-canon-readback.yml`
    3. `.github/workflows/canon-drift-check.yml` (self-check)
  - Additionally asserts the `manylinux_2_17` content pin in `.github/workflows/deploy-function-app.yaml` (matches the canon-drift-check.yml content assertion in `gc-data-v2/pipeline-template/.github/workflows/canon-drift-check.yml` lines 65-79).
  - Runs in Phase 6 (registered as a sub-step in `skills/phase-6/SKILL.md` for pipeline builds — coordinate with STORY-1002 ordering).
  - Output: `features/story-XXXX/scaffold-drift-report.md` listing each file's status (`match | drift | missing`) with a unified diff for any `drift` rows.
  - Refuses to mark Phase 6 complete if any row is `drift` without a `drift_approved_by: mark` line.
  - Reads the pinned `gc_data_v2_commit` from `.project` and runs `git -C /mnt/c/Projects/gc-data-v2 show <sha>:pipeline-template/<file>` to fetch the scaffold version (deterministic; no network call).
- **NEW skill** `/mnt/c/Projects/sdlc-framework/skills/pipeline-kickoff/SKILL.md`:
  - Wraps `gc-data-v2/templates/pipeline-kickoff-prompt.md` (the Mad Libs template — see lines 49-77 of that file: "Source Name", "source-slug", "source-section", "Auth Type", "Format", "Frequency", "Rate limits", "Functional area", "Repo", "Brownfield/Greenfield", "schema_line", "API docs URL").
  - Interactive prompt: asks the 12 fields one at a time. Defaults derived from `gc-data-v2/sources.yaml` where possible.
  - **Validation:** before continuing, looks up the supplied `source-slug` in `gc-data-v2/sources.yaml`. If missing, prints: `sources.yaml has no entry for '<slug>'. Add one before proceeding (see gc-data-v2/AGENTS.md § sources.yaml). Aborting.` and exits non-zero.
  - **Output:** writes `features/story-XXXX/pipeline-kickoff-prompt.md` — the fully-filled template, ready to be pasted into a dispatch / new-project invocation.
  - Records the source slug, auth type, and `gc_data_v2_commit` SHA into the new story's `seed.md` (extends STORY-1001's `## Source` section).
  - Idempotent: rerunning on a story with an existing `pipeline-kickoff-prompt.md` prompts `kickoff prompt already exists; overwrite? [y/N]`.

## Out of scope

- The `build_type` classifier + Phase 1 canon load (STORY-1001).
- The `load-canon` skill and Phase 6/7/10 mods that *call* `scaffold-drift-check` from Phase 6 (STORY-1002 — this story ships the skill; STORY-1002 wires it into Phase 6).
- The 3-question gate + `canon-backport` (STORY-1003).
- Modifying `gc-data-v2/templates/pipeline-kickoff-prompt.md` itself — wrapped as-is.
- Modifying `gc-data-v2/pipeline-template/.github/workflows/canon-drift-check.yml` — mirrored, not modified.
- A CI-side equivalent of `scaffold-drift-check` in `sdlc-framework`'s own GitHub Actions — out of scope; the existing gc-data-v2 CI workflow remains authoritative at PR time.
- Auto-fixing drift — `scaffold-drift-check` reports only.

## Success criteria (acceptance criteria)

1. **SC-1 — `scaffold-drift-check` skill exists:** `/scaffold-drift-check` (invoked from any pipeline-build worktree) writes `features/story-XXXX/scaffold-drift-report.md` and exits 0 when no drift, 1 when drift present.
2. **SC-2 — Match path:** running against a byte-identical scaffold copy reports all 4 rows as `match` and exits 0.
3. **SC-3 — Drift detection:** mutating one byte in local `.github/pull_request_template.md` causes the row status to be `drift` and the report to include a unified diff for that file.
4. **SC-4 — Missing-file detection:** deleting `.github/workflows/canon-drift-check.yml` locally causes the row status to be `missing` and references the canonical path.
5. **SC-5 — GLIBC pin content assertion:** removing `manylinux_2_17` from `.github/workflows/deploy-function-app.yaml` triggers a `drift` row pointing to "Deploy gate 2b in gc-data-v2/platform/failure-modes.md" (matching the gc-data-v2 CI error text).
6. **SC-6 — Deterministic SHA pin:** drift-check resolves scaffold files via `git -C /mnt/c/Projects/gc-data-v2 show <sha>:...`, not via network. Confirmed by `unshare -n /scaffold-drift-check` (network-namespace isolation) producing the same result.
7. **SC-7 — `pipeline-kickoff` skill exists:** `/pipeline-kickoff` asks the 12 Mad Libs fields and writes `features/story-XXXX/pipeline-kickoff-prompt.md`.
8. **SC-8 — `sources.yaml` validation:** invoking `/pipeline-kickoff` with a slug not in `sources.yaml` (e.g. `made-up-source`) exits non-zero with `sources.yaml has no entry for 'made-up-source'. Add one before proceeding ...`.
9. **SC-9 — Defaults from `sources.yaml`:** when the slug exists, defaults for `Auth Type`, `Frequency`, and `Functional area` come from the `sources.yaml` row (e.g. `amazon-sp-api` → `OAuth 2.0 (LWA)`, `daily`, marketplaces).
10. **SC-10 — Idempotency:** rerunning `/pipeline-kickoff` on a story with existing prompt prints `kickoff prompt already exists; overwrite? [y/N]`.

## Files to modify

**NEW:**
- `/mnt/c/Projects/sdlc-framework/skills/scaffold-drift-check/SKILL.md` — the new skill.
- `/mnt/c/Projects/sdlc-framework/skills/scaffold-drift-check/files-watched.md` — the canonical list of 3 byte-diffed files + 1 content-asserted file (mirrors `canon-drift-check.yml` lines 9-13 and 65-79).
- `/mnt/c/Projects/sdlc-framework/skills/scaffold-drift-check/templates/scaffold-drift-report.md` — output template.
- `/mnt/c/Projects/sdlc-framework/skills/pipeline-kickoff/SKILL.md` — the new skill.
- `/mnt/c/Projects/sdlc-framework/skills/pipeline-kickoff/field-prompts.md` — the 12 interactive prompts + validation rules.
- `/mnt/c/Projects/sdlc-framework/skills/pipeline-kickoff/templates/filled-kickoff-prompt.md` — output template (echoes `gc-data-v2/templates/pipeline-kickoff-prompt.md` with `{Field}` placeholders).

**MODIFY:**
- `/mnt/c/Projects/sdlc-framework/software-development-guidance.md` — add references to both skills in the relevant phase sections (Phase 6 for scaffold-drift; Phase 1 / new-project for pipeline-kickoff).
- `/mnt/c/Projects/sdlc-framework/AGENTS.md` (if it lists skills) — add entries.

**Tracking docs:**
- `/mnt/c/Projects/tech-dev-agents/.project` — Phase 1 status updates for STORY-1004.
- Monday.com task comment.

## Files to NOT modify

- `/mnt/c/Projects/gc-data-v2/pipeline-template/.github/workflows/canon-drift-check.yml` — mirrored, not modified.
- `/mnt/c/Projects/gc-data-v2/templates/pipeline-kickoff-prompt.md` — wrapped as-is.
- `/mnt/c/Projects/gc-data-v2/sources.yaml` — read-only consumption.
- `/mnt/c/Projects/sdlc-framework/skills/phase-6/SKILL.md` — wiring scaffold-drift-check into Phase 6 is STORY-1002's job (`load-canon` + phase-6 mod story). This story produces the skill; STORY-1002 calls it.
- `/mnt/c/Projects/sdlc-framework/skills/spec/`, `phase-1/`, `new-project/` — STORY-1001 scope (though `pipeline-kickoff` *complements* `new-project`, it doesn't modify it).

## Verification plan

| SC | Shell command | Expected output |
|---|---|---|
| SC-1 | `cd /tmp/scratch-pipeline && /scaffold-drift-check`; `echo $?` | Report written; exit 0 if no drift |
| SC-2 | In a clean scaffold copy: `/scaffold-drift-check` then `grep -c '^|.*\bmatch\b' scaffold-drift-report.md` | `4` (4 rows all match) |
| SC-3 | `echo '<!-- drift -->' >> .github/pull_request_template.md && /scaffold-drift-check; grep -A20 'pull_request_template' scaffold-drift-report.md` | Row status `drift`; unified diff present; exit 1 |
| SC-4 | `rm .github/workflows/canon-drift-check.yml && /scaffold-drift-check; grep canon-drift-check.yml scaffold-drift-report.md` | Row status `missing`; references canonical path |
| SC-5 | `sed -i '/manylinux_2_17/d' .github/workflows/deploy-function-app.yaml && /scaffold-drift-check; grep -i glibc scaffold-drift-report.md` | Row status `drift`; cites failure-modes.md Deploy gate 2b |
| SC-6 | `unshare -rn /scaffold-drift-check; echo $?` (network namespace removed) | Same exit code as with network; result deterministic from pinned SHA |
| SC-7 | `/pipeline-kickoff` with answers → `cat features/story-XXXX/pipeline-kickoff-prompt.md \| grep -c '{'` | `0` (all 12 `{Field}` placeholders filled in) |
| SC-8 | `/pipeline-kickoff` with slug `made-up-source` | Stderr: `sources.yaml has no entry for 'made-up-source'. Add one before proceeding ...`; exit non-zero |
| SC-9 | `/pipeline-kickoff` with slug `amazon-sp-api`; observe the `Auth Type` default | Default shown: `OAuth 2.0 (LWA)` (from `sources.yaml` row `auth_method`) |
| SC-10 | Run `/pipeline-kickoff` twice on the same story | Second run prompts `kickoff prompt already exists; overwrite? [y/N]` |

## Boundaries

| Always Do | Ask First | Never Do |
|---|---|---|
| Resolve scaffold files via `git -C <gc-data-v2> show <sha>:...` (deterministic, offline-safe) | Add a 5th file to the byte-diff watch list | Fetch scaffold files via `curl https://raw.githubusercontent.com/...` from inside the skill — gc-data-v2's *CI* uses that path, our local skill uses the pinned SHA |
| Use the pinned `gc_data_v2_commit` from `.project` (set by STORY-1001's Phase 1) | Auto-update the pinned SHA mid-story | Pin a SHA into the skill source code |
| Validate every `pipeline-kickoff` source slug against `gc-data-v2/sources.yaml` | Accept a new source slug as a one-off (e.g. for a brand-new vendor) — gate it via "add to sources.yaml first" | Auto-create a `sources.yaml` entry from `pipeline-kickoff` — that's a `gc-data-v2` PR, not a side effect |
| Keep the report human-readable + machine-greppable (one row per file) | Emit JSON instead of Markdown for the drift report | Auto-fix drift (open PRs, edit files) — report only |
| Mirror the gc-data-v2 CI error messages verbatim (so users can grep across surfaces) | Improve the error wording without coordinating with the data team | Diverge silently from `canon-drift-check.yml` content assertions |

## Done looks like

```
$ cd ~/test_projects/test-pipeline-v2

$ /scaffold-drift-check
Resolving gc_data_v2_commit from .project... a7c1bf2e...8e9d
Comparing 3 byte-diff files + 1 content assertion against gc-data-v2/pipeline-template@a7c1bf2e:
  .github/pull_request_template.md             ✓ match
  .github/workflows/pr-canon-readback.yml      ✓ match
  .github/workflows/canon-drift-check.yml      ✓ match
  manylinux_2_17 pin (deploy-function-app.yaml) ✓ match
Report: features/story-XXXX/scaffold-drift-report.md
Phase 6 may proceed.

$ /pipeline-kickoff
Source slug: tiktok-business
Looking up tiktok-business in gc-data-v2/sources.yaml... found.
Source Name [TikTok Business]:
Auth Type [OAuth 2.0 (generic)]:
Format [JSON]:
Frequency [daily]:
... (8 more fields, defaults from sources.yaml)
Functional area [Marketplace Order Data]: Advertising
Brownfield/Greenfield [Greenfield]:
schema_line [   - sources/other-platforms/schemas/tiktok-business.json]:
API docs URL: https://business-api.tiktok.com/portal/docs

Writing features/story-NNN-tiktok-business-bootstrap/pipeline-kickoff-prompt.md...
12/12 fields filled. Ready to feed into /new-project --type=pipeline or /dispatch.

$ wc -l features/story-NNN-tiktok-business-bootstrap/pipeline-kickoff-prompt.md
142 features/story-NNN-tiktok-business-bootstrap/pipeline-kickoff-prompt.md
```

## Escalation contract

Inherits the epic's escalation contract. Story-specific escalations to Mark:

1. The local `gc-data-v2` checkout is missing the pinned SHA (e.g. shallow clone) — `scaffold-drift-check` should print `git -C /mnt/c/Projects/gc-data-v2 fetch --unshallow` guidance and exit non-zero.
2. The 3 byte-diffed files diverge between `gc-data-v2/pipeline-template/` and what `canon-drift-check.yml` line 39-42 actually watches (mismatch between our watch list and theirs) — pause; coordinate with Work-Stream D before merging.
3. `sources.yaml` schema changes (e.g. `auth_method` renamed) — STORY-1013 (Work-Stream D) handles versioning; coordinate.
4. A user genuinely needs to scaffold a pipeline before adding to `sources.yaml` (e.g. exploratory spike) — accept with `--force-no-sources-yaml` flag; flag the spike in `seed.md` and require backfilling sources.yaml before merge.


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

Small scope: `1 → 7 → 8 → Done` (per epic seed line 205 and Phase 1 SKILL.md line 87 — Small skips Phase 6). Phase 7 produces RED tests `tests/sdlc/test_scaffold_drift_check_match.py`, `test_scaffold_drift_check_drift.py`, `test_scaffold_drift_check_missing.py`, `test_scaffold_drift_check_glibc_pin.py`, `test_pipeline_kickoff_validates_sources_yaml.py`, `test_pipeline_kickoff_fills_template.py`. Phase 8 turns them GREEN and ships the PR against `sdlc-framework`. Independent of STORY-1002/1003 — can ship in parallel.

## Open questions for Phase 7

(Small scope skips Phase 6, so design questions land in test design / implementation.)

1. **Cross-repo paths.** Should the skill require `gc-data-v2` to be cloned at a fixed path (`/mnt/c/Projects/gc-data-v2`), or read the path from a config key? Default proposal: read from `config.yaml` key `gc_data_v2_path:` with the fixed path as fallback default.
2. **Drift-check output format.** Markdown table is greppable but verbose; JSON is machine-readable but less human-friendly. Default proposal: emit Markdown by default, JSON via `--format=json` flag.
3. **Auto-refresh of pinned SHA.** If the local `gc-data-v2` checkout has advanced past the pinned `.project` SHA, should `scaffold-drift-check` use the newer SHA or stick to the pin? Default proposal: stick to the pin (deterministic); emit a warning if HEAD is newer.
4. **`pipeline-kickoff` template variants.** Some sources (greenfield vs brownfield, OAuth1 vs OAuth2) need different sub-templates per `gc-data-v2/pipeline-template/{GREENFIELD,OAUTH1}.md`. Should `pipeline-kickoff` auto-include the right sub-template? Default proposal: yes — based on `Brownfield/Greenfield` and `Auth Type` answers, append the relevant section.
5. **Fallback when `gc-data-v2` is missing.** What does the skill do on hosts without the gc-data-v2 checkout (e.g. CI of a different repo)? Default proposal: print the canonical clone command and exit non-zero with code 2 (distinct from "drift detected" exit 1).
6. **Concurrent edits to the watched files.** A user editing `.github/pull_request_template.md` for a legitimate reason (e.g. adding a story-specific section) will trigger drift. How is "approved deviation" recorded? Default proposal: a `drift_approved_by: mark` line in the story's `scaffold-drift-report.md`; user-edited PRs that approve drift must include this line.

## Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| The watched-file list drifts from `gc-data-v2/canon-drift-check.yml` (we add a file they don't, or vice versa) | Medium | High (false sense of safety) | `scaffold-drift-check` parses `canon-drift-check.yml` itself at startup to extract the list — single source of truth |
| `manylinux_2_17` pin assertion produces false positives on repos without a deploy-function-app.yaml (stub repos) | Medium | Low | Skip the assertion if the file doesn't exist (matches gc-data-v2 CI behaviour, lines 68-72) |
| `pipeline-kickoff` overwrites a hand-edited kickoff prompt | Low | Medium | Idempotency prompt SC-10 |
| `sources.yaml` becomes very large; lookup gets slow | Low | Low | YAML parse once, cache; acceptable up to ~1k entries |
| The 12 Mad Libs fields drift from what `gc-data-v2/templates/pipeline-kickoff-prompt.md` defines | Medium | Medium | Parse the template at runtime; field list is data, not code |
| GLIBC pin gate misses other content assertions gc-data-v2 may add later | Medium | Medium | Re-parse `canon-drift-check.yml` on each run to discover new assertions (graceful: warn on unknown patterns) |
| Local `gc-data-v2` checkout is on a stale branch | Low | Medium | Compare pinned SHA against current branch HEAD; warn if divergence > 7 days |

## Dependencies & sequencing

- **Upstream:** STORY-1001 (provides `gc_data_v2_commit` in `.project` — the SHA `scaffold-drift-check` reads).
- **Independent of:** STORY-1002, STORY-1003. Can ship in parallel.
- **Downstream:**
  - STORY-1002's Phase 6 mod calls `scaffold-drift-check` as a sub-step. If STORY-1002 ships first, this story's skill must be present in `sdlc-framework` before the Phase 6 mod activates. Coordinate via merge ordering — STORY-1004 ships before STORY-1002's Phase 6 mod goes live.
  - Morris STORY-1005 (`canon-check`) shares the watched-file list. Single source of truth: `gc-data-v2/.github/workflows/canon-drift-check.yml`. Both skills parse it.
- **Cross-stream:** STORY-1011 (sdlc-framework version tagging) — `scaffold-drift-check` should record both `gc_data_v2_commit` AND `sdlc_framework_tag` in its report header for full traceability.

## Test fixtures needed (for Phase 7)

- `tests/fixtures/sdlc/clean-pipeline-scaffold/` — a synthetic byte-identical scaffold copy (for SC-2).
- `tests/fixtures/sdlc/drift-pull-request-template/` — same but with `<!-- drift -->` appended to `pull_request_template.md` (SC-3).
- `tests/fixtures/sdlc/missing-canon-workflow/` — same but `canon-drift-check.yml` deleted (SC-4).
- `tests/fixtures/sdlc/missing-glibc-pin/` — same but `manylinux_2_17` removed from deploy-function-app.yaml (SC-5).
- `tests/fixtures/sdlc/gc-data-v2-snapshot/` — shared with STORY-1001/1002.
- `tests/fixtures/sdlc/sources.yaml.minimal` — small sources.yaml with `amazon-sp-api` only (for SC-9 default-resolution test).
- `tests/fixtures/sdlc/sources.yaml.missing-slug` — sources.yaml without the target slug (for SC-8 rejection test).
- `tests/fixtures/sdlc/expected-kickoff-prompt.md` — golden filled template (for SC-7).

## Notes for Phase 7 / 8

- `scaffold-drift-check` is **pure I/O** — no LLM calls, no network (after SHA pin). That makes it cheap and CI-friendly. Design it as a thin Python module or shell script invocable by name.
- `pipeline-kickoff` is **interactive** — but for tests, accept all 12 fields as flags (`--source-name="..." --source-slug=... --auth-type=...`) so test harnesses can drive it non-interactively.
- The output of `scaffold-drift-check` should be **deterministic** under a pinned SHA — same inputs → byte-identical report. Tests assert this directly.
- The skill must not silently swallow `git` errors. If `git -C /mnt/c/Projects/gc-data-v2 show <sha>:...` fails (SHA unknown, repo not cloned, etc.), print the exact `git` error and the recovery command, then exit non-zero.

## Done looks like (expanded — Phase 8 close)

```
$ /scaffold-drift-check
Resolving gc_data_v2_commit from .project... a7c1bf2e0d9f4c1a8e2d5f6a3b9c0d1e4f5a6b8e9d
Watched files (read from gc-data-v2/.github/workflows/canon-drift-check.yml):
  - .github/pull_request_template.md
  - .github/workflows/pr-canon-readback.yml
  - .github/workflows/canon-drift-check.yml
  - manylinux_2_17 content assertion in .github/workflows/deploy-function-app.yaml
Comparing against gc-data-v2/pipeline-template@a7c1bf2e:
  ✓ .github/pull_request_template.md             match
  ✓ .github/workflows/pr-canon-readback.yml      match
  ✓ .github/workflows/canon-drift-check.yml      match
  ✓ manylinux_2_17 pin                            match
Report: features/story-XXXX/scaffold-drift-report.md
Result: clean — Phase 6 may advance.
Exit code: 0

$ /pipeline-kickoff --source-slug=tiktok-business --source-name="TikTok Business" \
    --auth-type="OAuth 2.0 (generic)" --format=JSON --frequency=daily \
    --rate-limits="1200 req/min" --functional-area=Advertising \
    --repo-name=tiktok-business-v2 --pipeline=tiktok_business \
    --brownfield-greenfield=Greenfield \
    --schema-line="- sources/other-platforms/schemas/tiktok-business.json" \
    --api-docs-url=https://business-api.tiktok.com/portal/docs
Looking up tiktok-business in gc-data-v2/sources.yaml... found.
Filling 12 fields...
Including GREENFIELD.md section (build is Greenfield)...
Writing features/story-NNN-tiktok-business-bootstrap/pipeline-kickoff-prompt.md (142 lines)
Updating seed.md ## Source section with slug=tiktok-business, auth=OAuth 2.0 (generic).
Done.
```

## Why Small scope (not Medium)

- Two skills, neither cross-cutting.
- No phase-mods (Phase 6 wiring is STORY-1002's job; this story only ships the callable skill).
- Pure I/O (no LLM orchestration, no async).
- Deterministic, fixture-driven testing.
- Reuses STORY-1001 infrastructure (`.project` keys, `gc-data-v2` checkout).

If Phase 7 discovery reveals hidden complexity (e.g. canon-drift-check.yml structure needs parsing that's non-trivial, or pipeline-kickoff field defaults require cross-doc lookup), bump to Medium and add Phase 6.
