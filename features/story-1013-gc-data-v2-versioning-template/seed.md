# STORY-1013 — gc-data-v2 scaffold versioning + sources/TEMPLATE.md + structural-completeness CI

**Story ID:** STORY-1013
**Scope:** Medium
**Epic:** STORY-1000 (Cross-Repo Canon Alignment)
**Work-Stream:** D — Knowledgebase curation
**Repos touched (cross-repo):**
- `gc-data-v2` (primary — tags, new files, new CI)
- `tech-dev-agents` (this story's SDLC artifacts only, under `features/story-1013-.../`)
**Date:** 2026-05-18
**Status:** Phase 1 — seed draft, gated awaiting approval
**Frontend:** false

---

## Problem

`gc-data-v2` is the canon repo for the v2 data platform. Two structural gaps make canon drift inevitable:

1. **No scaffold versioning.** `gc-data-v2/pipeline-template/` is the source-of-truth scaffold copied into every `*-v2` pipeline repo. There is **no version tag** on the template — drift-check compares against `main` HEAD, which moves daily. When the template ships a breaking change (e.g. requirements.txt restructure, scrubber wiring), downstream pipelines have no migration anchor and no way to say "we are pinned to v1.0." Captured in `gc-data-v2/platform/platform-gaps.md` § **M3** ("No scaffold versioning") and § **M5** ("No project-wide changelog").

2. **No structural template for `sources/*` READMEs.** Source documentation quality is wildly uneven:
   - `sources/amazon-sp-api/README.md` — 69 lines, 9 well-defined sections, the de-facto exemplar.
   - `sources/toolio/README.md` — 45 lines, missing Auth Details, Rate-Limit Strategy, APIs Used.
   - `sources/netsuite/README.md` — 65 lines, partial.
   - `sources/shopify/README.md` — 128 lines but inconsistent section names.
   - `sources/other-platforms/README.md` — 63 lines, 5 platforms collapsed in one doc.

   The reader cannot rely on any one section being present. There is no CI gate. New sources can be added at any quality level.

These are not isolated problems. The epic's incident audit (STORY-1000 § Problem) lists "v2 mirror desync" (2026-05-04) as a class of failure that scaffold versioning + drift-check would catch.

## Goal

Introduce three coupled artifacts to `gc-data-v2`:

1. **A version tag scheme** on `pipeline-template/` (`pipeline-template/v1.0.0` baseline → `v1.1.0` on next change), so downstream repos can pin and so the drift-check has a stable anchor.
2. **A new `CHANGELOG.canon.md`** at repo root, with one entry per `pipeline-template/*` or `platform/*.md` change, gated by `M5`.
3. **A `sources/TEMPLATE.md`** modelled on Amazon SP-API + a CI gate (`sources-completeness-check.yml`) that fails any PR adding a source directory under `sources/` that lacks the required README sections.

Backfilling thin source READMEs (Toolio, NetSuite, Shopify, other-platforms) is **explicitly out of scope** for this story — call them out as follow-up tasks.

## Scope

**In:**
- Tag `pipeline-template/v1.0.0` against current `main` HEAD as the baseline.
- Plan the `v1.1.0` tag for the merge commit that ships this story (tag-on-merge documented in the new `CHANGELOG.canon.md`).
- Create `gc-data-v2/CHANGELOG.canon.md` with two seed entries (v1.0.0 baseline + v1.1.0 introducing TEMPLATE.md and CI).
- Create `gc-data-v2/sources/TEMPLATE.md` with the section skeleton derived from `sources/amazon-sp-api/README.md`.
- Create `gc-data-v2/.github/workflows/sources-completeness-check.yml` — runs on `pull_request`, parses each `sources/<src>/README.md`, asserts required section headings exist, fails the PR with a list of missing sections.
- Author a Python (or `grep`-based shell) parser script invoked by the workflow — placed under `gc-data-v2/scripts/check_sources_completeness.py`.
- RED test set for the parser before implementation.

**Out:**
- Backfilling Toolio / NetSuite / Shopify / other-platforms / walmart-supplier / walmart-ad-connect / amazon-ads-api READMEs. Follow-up tasks (STORY-1013-followup-N, to be filed after merge).
- Changing any `platform/*.md` doc (referenced by `gc-data-v2/AGENTS.md` as canon — not modified here).
- Changes to `pipeline-template/*` files themselves (only the tag is new).
- Wiring downstream `*-v2` repos to pin `pipeline-template@v1.0.0` (covered by STORY-1011).

## Out of scope (explicit Do-Not-Do)

- Do NOT modify `gc-data-v2/pipeline-template/AGENTS.md`, `CHANGELOG.md`, `GREENFIELD.md`, `README.md`, `pyproject.toml`, or any file under `pipeline-template/`. The tag is on the **current** state.
- Do NOT rewrite thin source READMEs (Toolio, NetSuite, etc.) in this PR — file follow-up tasks instead.
- Do NOT touch `platform/platform-gaps.md` (STORY-1014 closes S7; STORY-1013 only references gaps M3 / M5).
- Do NOT create the CI workflow without a corresponding RED parser test set in `gc-data-v2/tests/`.

## Success criteria

- **SC-1** — `git -C /mnt/c/Projects/gc-data-v2 tag --list 'pipeline-template/*'` returns `pipeline-template/v1.0.0` after the baseline-tag step.
- **SC-2** — `ls /mnt/c/Projects/gc-data-v2/CHANGELOG.canon.md` exists; file contains literal headings `## pipeline-template/v1.0.0` and `## pipeline-template/v1.1.0`.
- **SC-3** — `ls /mnt/c/Projects/gc-data-v2/sources/TEMPLATE.md` exists; contains required section headings: `## Overview`, `## Implementation`, `## Auth Details`, `## Rate-Limit Strategy`, `## APIs Used`, `## Reports`, `## Credentials Status`, `## Open Items`, `## Related Docs`.
- **SC-4** — `ls /mnt/c/Projects/gc-data-v2/.github/workflows/sources-completeness-check.yml` exists and triggers on `pull_request` with `paths: ['sources/**']`.
- **SC-5** — Running `python gc-data-v2/scripts/check_sources_completeness.py sources/amazon-sp-api/README.md` exits 0 (the exemplar passes its own template).
- **SC-6** — Running `python gc-data-v2/scripts/check_sources_completeness.py sources/toolio/README.md` exits non-zero with output naming each missing section (proves the gate works against current state, but does NOT block this PR because the workflow is scoped only to PRs that **add** a source dir or touch `TEMPLATE.md` — see Boundaries).
- **SC-7** — Test PR that adds a fake `sources/fake-src/README.md` missing `## Auth Details` fails CI; the failure log names `Auth Details` as missing.
- **SC-8** — `git -C gc-data-v2 log --oneline CHANGELOG.canon.md` shows a commit titled per the format defined in `gc-data-v2/AGENTS.md`.

## Files to modify (grouped by repo)

### `gc-data-v2` (NEW files)
- `CHANGELOG.canon.md` — root-level changelog for canon (pipeline-template + platform/*).
- `sources/TEMPLATE.md` — required-section skeleton.
- `scripts/check_sources_completeness.py` — parser/validator.
- `.github/workflows/sources-completeness-check.yml` — PR gate.
- `tests/test_check_sources_completeness.py` — RED test set (Phase 7).

### `gc-data-v2` (REPO operations, not file edits)
- Annotated tag `pipeline-template/v1.0.0` on current `main` HEAD.
- Tag `pipeline-template/v1.1.0` on the merge commit (post-merge, by Morris merge skill or manual).

### `tech-dev-agents` (SDLC artifacts)
- `features/story-1013-gc-data-v2-versioning-template/seed.md` (this file)
- `features/story-1013-gc-data-v2-versioning-template/feature-spec.md` (Phase 6)
- `features/story-1013-gc-data-v2-versioning-template/test-design.md` (Phase 7)

## Files to NOT modify

- `gc-data-v2/pipeline-template/**` — the tag captures the current state; modifying it would invalidate `v1.0.0`.
- `gc-data-v2/platform/*.md` — out of scope (referenced, not edited; STORY-1014 handles S7).
- `gc-data-v2/sources/{amazon-sp-api,amazon-ads-api,toolio,netsuite,shopify,other-platforms,walmart-supplier,walmart-ad-connect}/README.md` — backfill is a follow-up.
- `gc-data-v2/AGENTS.md`, `README.md`, `sources.yaml` — not in scope.
- Any file under `tech-gc-knowledgebase/` or `data-pipelines-runbooks/` — STORY-1015 / 1016 territory.

## Verification plan

| Check | Command | Expected output |
|---|---|---|
| Baseline tag exists | `git -C /mnt/c/Projects/gc-data-v2 tag --list 'pipeline-template/v1.0.0'` | `pipeline-template/v1.0.0` |
| Changelog file present | `ls /mnt/c/Projects/gc-data-v2/CHANGELOG.canon.md` | File listed |
| Changelog has both tag headings | `grep -E '^## pipeline-template/v1\.[01]\.0' /mnt/c/Projects/gc-data-v2/CHANGELOG.canon.md \| wc -l` | `2` |
| TEMPLATE.md exists | `ls /mnt/c/Projects/gc-data-v2/sources/TEMPLATE.md` | File listed |
| TEMPLATE.md has all required section headings | `grep -cE '^## (Overview\|Implementation\|Auth Details\|Rate-Limit Strategy\|APIs Used\|Reports\|Credentials Status\|Open Items\|Related Docs)$' /mnt/c/Projects/gc-data-v2/sources/TEMPLATE.md` | `9` |
| Parser script runs | `python /mnt/c/Projects/gc-data-v2/scripts/check_sources_completeness.py /mnt/c/Projects/gc-data-v2/sources/amazon-sp-api/README.md` | Exit 0 |
| Parser detects thin source | `python /mnt/c/Projects/gc-data-v2/scripts/check_sources_completeness.py /mnt/c/Projects/gc-data-v2/sources/toolio/README.md ; echo $?` | Non-zero exit, output names `Auth Details` and `Rate-Limit Strategy` |
| RED → GREEN unit tests | `pytest /mnt/c/Projects/gc-data-v2/tests/test_check_sources_completeness.py -v` | All GREEN after Phase 8 |
| CI workflow registered | `gh -R hpi-gorillacommerce/gc-data-v2 workflow list \| grep sources-completeness` | Row present |
| Test PR blocks missing-section source | Open PR adding `sources/fake-src/README.md` missing `## Auth Details` → check `gh pr checks <PR>` | `sources-completeness-check` job FAILED; log names missing section |
| Replay: structural-completeness catches Toolio-shaped gap | Run parser on a copy of `toolio/README.md` in fixture | Exit non-zero |

## Boundaries

| Always Do | Ask First | Never Do |
|---|---|---|
| Tag `pipeline-template/v1.0.0` against the current `main` HEAD before any new file lands | Push tags to origin (`git push origin pipeline-template/v1.0.0`) — Mark must confirm canon-tag publication | Delete or move any file under `pipeline-template/` |
| Use `sources/amazon-sp-api/README.md` (lines 1-69) as the literal exemplar for TEMPLATE.md | Scope the workflow to anything broader than `paths: ['sources/**']` | Backfill thin source READMEs in this PR — file follow-up tasks instead |
| Write RED tests in Phase 7 before the parser in Phase 8 | Adjust required-sections list if Mark wants different headings | Skip the parser smoke test in CI |
| Run the parser script against the exemplar in CI as a self-check | Tag `v1.1.0` before Mark approves the merge | Use Python features unavailable in the GitHub runner default (assume 3.11) |

## Done looks like

```
$ git -C /mnt/c/Projects/gc-data-v2 tag --list 'pipeline-template/*'
pipeline-template/v1.0.0
pipeline-template/v1.1.0

$ ls /mnt/c/Projects/gc-data-v2/CHANGELOG.canon.md /mnt/c/Projects/gc-data-v2/sources/TEMPLATE.md /mnt/c/Projects/gc-data-v2/.github/workflows/sources-completeness-check.yml /mnt/c/Projects/gc-data-v2/scripts/check_sources_completeness.py
# all four files exist

$ pytest /mnt/c/Projects/gc-data-v2/tests/test_check_sources_completeness.py -v
# all GREEN

$ gh -R hpi-gorillacommerce/gc-data-v2 pr checks <test-PR-with-broken-fake-source>
sources-completeness-check    fail    "Missing required sections: Auth Details, Rate-Limit Strategy"
```

## Escalation contract

- If the data team objects to the required-sections list (e.g. "Rate-Limit Strategy doesn't apply to NetSuite"), pause Phase 6, file `needs_info`, and ask: should the list be required-OR-N/A (header with `N/A` body counts)? Default proposal: header must exist; body may be a single line `N/A — <reason>`.
- If `pipeline-template/v1.0.0` cannot be tagged (existing tag conflict), STOP and ask Mark — tagging history is canon. Do not force-update.
- If the workflow fails on `sources/TEMPLATE.md` itself (false positive), iterate the parser, not the template.


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

Phase 6 designs the parser interface (input: path to source README; output: structured list of missing sections), TEMPLATE.md skeleton, and the YAML workflow shape. Phase 7 writes the failing parser tests (RED). Phase 8 implements the parser to GREEN, ships the workflow, tags `v1.0.0`, opens the PR. Done = SC-1..SC-8 verified.

## Follow-up tasks (NOT in this story; file as separate stories on close)

- `STORY-1013-followup-toolio` — backfill `sources/toolio/README.md` to template. Today: 45 lines, missing `Auth Details`, `Rate-Limit Strategy`, `APIs Used`.
- `STORY-1013-followup-netsuite` — backfill `sources/netsuite/README.md` to template. Today: 65 lines, partial.
- `STORY-1013-followup-shopify` — backfill `sources/shopify/README.md` to template. Today: 128 lines, inconsistent section names.
- `STORY-1013-followup-other-platforms` — backfill or split `sources/other-platforms/README.md`. Today: 63 lines, 5 platforms collapsed.
- `STORY-1013-followup-walmart` — backfill `sources/walmart-supplier/README.md` (70 lines) and `walmart-ad-connect/README.md` (76 lines).
- `STORY-1013-followup-amazon-ads` — verify `sources/amazon-ads-api/README.md` (98 lines) against template.

## Exemplar reference — TEMPLATE.md skeleton anchor

The exemplar `gc-data-v2/sources/amazon-sp-api/README.md` defines the canonical section ordering. Sections present at lines 1-69:

1. `# <Source Name>` (line 1)
2. `## Overview` (line 3) — 1-2 paragraphs of business context.
3. `## Implementation` (line 7) — table with rows: Pipeline repo, Status, Python package, Auth, Rate limits, Geo handling.
4. `## Auth Details` (line 19) — bullet list: LWA application, Credentials list, Token endpoint, Token lifetime, Rate-limit bucket.
5. `## Rate-Limit Strategy` (line 27) — library used, Airflow pool slots, max_hold_seconds.
6. `## APIs Used` (line 33) — table with rows per API endpoint.
7. `## Reports` (line 42) — table with columns: Report, Status, Frequency, Functional Areas.
8. `## Credentials Status` — table with rows per credential (Status, Key Vault name).
9. `## Open Items` (line 63) — `[ ]` checklist.

TEMPLATE.md MUST mirror these section names exactly (the parser matches on exact `## <heading>` strings). Optional sections (`## Integration Guide`, `## Exemplar`) MAY be appended but are not required by the gate.

## Parser interface (Phase 6 will finalize)

```
$ python scripts/check_sources_completeness.py <path-to-README.md>
# exit 0: all required sections present
# exit 1: missing sections; stdout lists each missing heading, one per line:
#   MISSING: ## Auth Details
#   MISSING: ## Rate-Limit Strategy
# exit 2: file does not exist or is not parseable
```

Workflow invocation: iterate `sources/*/README.md` in the PR diff, call parser on each, aggregate failures.
