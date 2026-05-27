# STORY-1001 — `spec` + `phase-1` + `new-project`: build_type classifier + pipeline canon auto-load

**Story ID:** STORY-1001
**Scope:** Medium
**Epic:** STORY-1000 (Cross-Repo Canon Alignment)
**Work-Stream:** A — SDLC framework knowledgebase integration
**Repo touched:** `sdlc-framework` (and the `.sdlc` submodule pointer in `tech-dev-agents`)
**Date:** 2026-05-18
**Status:** Phase 1 — seed written, gated awaiting approval
**Frontend:** false

## Problem

Phase 1 today is build-type-blind. `skills/spec/SKILL.md` lines 28-32 read `.project`, `config.yaml`, and `backlog.md` then jump to `phase-1` with only a free-text feature description. `skills/phase-1/SKILL.md` lines 22-26 classify *scope* (Trivial/Small/Medium/Large) but never asks *what kind of system* is being built. The result is the 2026-05-04 STORY-871 v2 mirror desync from the epic audit: a pipeline change was specced and dispatched without ever loading `gc-data-v2/platform/pipeline-standard.md` or the per-source README, so the seed contained no 14-axis context. Morris has zero references to `gc-data-v2` in any skill (epic seed line 27). `new-project` (`skills/new-project/SKILL.md`) scaffolds every project from a single generic template — there is no path that copies `gc-data-v2/pipeline-template/` for a data pipeline, and no wiring of `canon-drift-check.yml`.

## Goal

Phase 1 asks one new question — `build_type` — and on `build_type == pipeline` auto-loads the gc-data-v2 canon (platform docs + per-source README) into the seed context and pins the gc-data-v2 commit SHA into `.project`. `new-project --type=pipeline` scaffolds from `gc-data-v2/pipeline-template/` and wires `canon-drift-check.yml` on day one.

## Scope

- Add a `build_type` classifier question to `skills/spec/SKILL.md` step 2 (between context gather and Phase 1 activation). Accepted values: `pipeline | feature | bug | ops | infra`. Default heuristic: if the description contains "pipeline", "DAG", "Airflow", "bronze", "silver", "gold", "ingest", or matches a source slug in `gc-data-v2/sources.yaml`, propose `pipeline` and confirm.
- Write `build_type` to **both** `config.yaml` (top-level `build_type:` key) and `.project` (under the story's Phase Routing section, e.g. `build_type: pipeline`).
- Modify `skills/phase-1/SKILL.md` step 4 ("For feature updates: Use Explore agent to understand affected codebase areas"). When `build_type == pipeline`:
  - Auto-load `gc-data-v2/platform/pipeline-standard.md`, `gc-data-v2/platform/failure-modes.md`, and `gc-data-v2/platform/new-pipeline-repo.md` into the seed's reading list.
  - Ask the user "Which source?" with the closed set: `amazon-sp-api | amazon-ads-api | walmart-supplier | walmart-ad-connect | shopify | levanta | toolio | netsuite | other`. Validate that an entry exists in `gc-data-v2/sources.yaml` (skip validation for `other`).
  - Load `gc-data-v2/sources/{source}/README.md` (if present) into the reading list. If absent, emit `seed_warning: missing_source_readme` and continue.
  - Pin `gc_data_v2_commit: <40-char sha>` into `.project` (resolved via `git -C /mnt/c/Projects/gc-data-v2 rev-parse HEAD`).
- Extend `seed.md` template (in `software-development-guidance.md`) with two optional sections that are REQUIRED when `build_type == pipeline`: `## Canon loaded` (list of doc paths + commit) and `## Source` (the source slug + sources.yaml row).
- Modify `skills/new-project/SKILL.md` to accept `--type=pipeline` (in addition to default feature/service). When set:
  - Scaffold from `gc-data-v2/pipeline-template/` (not the existing generic `templates/readme.md` path) via `cp -r ~/test_projects/gc-data-v2/pipeline-template ~/test_projects/<repo>` per `gc-data-v2/platform/new-pipeline-repo.md` step 1.
  - Copy `gc-data-v2/pipeline-template/.github/workflows/canon-drift-check.yml` and `gc-data-v2/pipeline-template/.github/pull_request_template.md` into the new repo verbatim.
  - Write `pipeline_template_commit: <sha>` into the new repo's `config.yaml`.
- Update `skills/spec/codex.md` (referenced from `skills/spec/SKILL.md` line 12) with the same classifier flow so Codex executions match Claude/Gemini.

## Out of scope

- The `load-canon` skill that makes loading callable from any phase (STORY-1002).
- Phase-6/7/10 pipeline-aware validation logic (STORY-1002).
- `canon-backport` and the retro 3-question gate (STORY-1003).
- `scaffold-drift-check` and `pipeline-kickoff` skills (STORY-1004).
- Queue-side `validate_dispatch_seed()` rejection logic (STORY-1009, Work-Stream C).
- Morris `pre-dispatch-validate` skill (STORY-1006, Work-Stream B).
- Modifying `gc-data-v2` itself — this story consumes canon, never edits it.

## Success criteria (acceptance criteria)

1. **SC-1 — Classifier prompt present:** running `/spec "Add walmart-supplier-v2 orders DAG"` produces an interactive prompt asking `build_type?` with the 5 accepted values; the heuristic pre-selects `pipeline`.
2. **SC-2 — build_type persisted in two places:** after Phase 1 closes for a pipeline story, `grep -E '^build_type:' config.yaml` returns `build_type: pipeline` AND `.project` contains `build_type: pipeline` under that story's Phase Routing block.
3. **SC-3 — Canon auto-loaded on pipeline:** the resulting `seed.md` contains a `## Canon loaded` section listing all four required doc paths (`pipeline-standard.md`, `failure-modes.md`, `new-pipeline-repo.md`, `sources/<src>/README.md`).
4. **SC-4 — Commit SHA pinned:** `.project` contains `gc_data_v2_commit: <40-char hex sha>` for any pipeline story; the SHA matches `git -C /mnt/c/Projects/gc-data-v2 rev-parse HEAD` at seed-write time.
5. **SC-5 — Source validation:** running `/spec` with a non-existent source (e.g. `foobar`) is rejected with the message `Unknown source 'foobar' — not in gc-data-v2/sources.yaml. Use 'other' or add the source first.`
6. **SC-6 — Non-pipeline path unchanged:** running `/spec "Add dark mode toggle"` produces `build_type: feature` and the `seed.md` contains no `## Canon loaded` / `## Source` section (back-compat regression check).
7. **SC-7 — new-project pipeline scaffold:** running `/new-project --type=pipeline test-pipeline-v2` copies `gc-data-v2/pipeline-template/**` into the new repo (byte-identical for `canon-drift-check.yml` and `pull_request_template.md`) and writes `pipeline_template_commit` into the new repo's `config.yaml`.
8. **SC-8 — Codex parity:** `skills/spec/codex.md` documents the same `build_type` flow (no divergence between platforms).

## Files to modify

**MODIFY:**
- `/mnt/c/Projects/sdlc-framework/skills/spec/SKILL.md` — add `build_type` classifier step between current steps 1 and 2; document persistence to `config.yaml` + `.project`.
- `/mnt/c/Projects/sdlc-framework/skills/spec/codex.md` — mirror the classifier flow for the Codex execution path.
- `/mnt/c/Projects/sdlc-framework/skills/phase-1/SKILL.md` — add pipeline-aware branch in step 4 (auto-load canon, ask source, pin SHA); add the two new seed sections.
- `/mnt/c/Projects/sdlc-framework/skills/new-project/SKILL.md` — accept `--type=pipeline`; document the gc-data-v2/pipeline-template scaffold path.
- `/mnt/c/Projects/sdlc-framework/software-development-guidance.md` — add `build_type` documentation + extend seed.md template with `## Canon loaded` and `## Source` sections (required when pipeline).
- `/mnt/c/Projects/sdlc-framework/templates/seed.md` (if exists) — same template addition.

**NEW:**
- `/mnt/c/Projects/sdlc-framework/skills/spec/build-type-classifier.md` — helper doc describing the heuristic, the 5 values, and the source-selection sub-prompt. Referenced from both `SKILL.md` and `codex.md`.
- `/mnt/c/Projects/sdlc-framework/templates/pipeline-seed-additions.md` — the canonical text for `## Canon loaded` + `## Source` sections.

**Tracking docs (always):**
- `/mnt/c/Projects/tech-dev-agents/.project` — Phase 1 status updates for STORY-1001.
- `/mnt/c/Projects/tech-dev-agents/backlog.md` — already updated per epic note; no further change.
- Monday.com task comment summarising Phase 1.

## Files to NOT modify

- `/mnt/c/Projects/gc-data-v2/**` — referenced only, never edited (see epic boundaries line 128).
- `/mnt/c/Projects/sdlc-framework/skills/start-story/`, `dispatch/`, `complete-story/` — those flow paths are touched in other stories (STORY-1008, STORY-1012).
- `/mnt/c/Projects/sdlc-framework/skills/phase-2..phase-5`, `phase-6..phase-11` — phase-mod work belongs to STORY-1002 / STORY-1003.
- `/mnt/c/Projects/tech-dev-agents/deployment/vm/skills/morris/**` — Morris is Work-Stream B.
- `.sdlc` submodule pointer in `tech-dev-agents` — bumped only after the sdlc-framework PR merges (sequencing handled at epic level).

## Verification plan

| SC | Shell command | Expected output |
|---|---|---|
| SC-1 | `cd /tmp/scratch && /spec "Add walmart-supplier-v2 orders DAG"` (transcript inspection) | Prompt `build_type? [pipeline|feature|bug|ops|infra] (suggested: pipeline)` is shown |
| SC-2 | `grep -E '^build_type:' /tmp/scratch/config.yaml && grep -A1 'STORY-' /tmp/scratch/.project \| grep build_type` | `build_type: pipeline` printed twice (config + .project) |
| SC-3 | `grep -A6 '^## Canon loaded' /tmp/scratch/features/story-XXX-walmart-orders/seed.md` | Lists 4 paths: `gc-data-v2/platform/pipeline-standard.md`, `failure-modes.md`, `new-pipeline-repo.md`, `sources/walmart-supplier/README.md` |
| SC-4 | `grep gc_data_v2_commit /tmp/scratch/.project` then `git -C /mnt/c/Projects/gc-data-v2 rev-parse HEAD` | Both 40-char SHAs match |
| SC-5 | `/spec "..." --source=foobar` (or interactive answer `foobar`) | Error: `Unknown source 'foobar' — not in gc-data-v2/sources.yaml. Use 'other' or add the source first.` Exit non-zero. |
| SC-6 | `/spec "Add dark mode toggle"` → `grep -c '^## Canon loaded' features/story-YYY-dark-mode/seed.md` | `0` |
| SC-7 | `/new-project --type=pipeline test-pipeline-v2` then `diff /tmp/test-pipeline-v2/.github/workflows/canon-drift-check.yml /mnt/c/Projects/gc-data-v2/pipeline-template/.github/workflows/canon-drift-check.yml; grep pipeline_template_commit /tmp/test-pipeline-v2/config.yaml` | `diff` exits 0; commit-pin line present |
| SC-8 | `diff <(grep -A20 build_type /mnt/c/Projects/sdlc-framework/skills/spec/SKILL.md) <(grep -A20 build_type /mnt/c/Projects/sdlc-framework/skills/spec/codex.md)` | Semantic match (same enum values, same persistence, same source-selection sub-prompt) |

## Boundaries

| Always Do | Ask First | Never Do |
|---|---|---|
| Persist `build_type` to **both** `config.yaml` and `.project` | Add a new `build_type` value beyond the 5 listed (e.g. "service") | Modify any file under `/mnt/c/Projects/gc-data-v2/` |
| Resolve `gc_data_v2_commit` from `git rev-parse HEAD` at seed-write time | Change the heuristic keyword list (pipeline/DAG/Airflow/bronze/...) | Hard-code a SHA — always resolve dynamically |
| Require source selection from the closed enum or explicit `other` | Add an automatic fallback that picks a source if the user is silent | Skip the source-selection prompt for pipeline builds |
| Keep the non-pipeline path byte-identical to today (regression check via SC-6) | Bump the `.sdlc` submodule pointer in tech-dev-agents | Couple this story to STORY-1006 (Morris gate) — that's Work-Stream B |
| Mirror every flow change into both `SKILL.md` and `codex.md` | | Inline the gc-data-v2 docs into the seed (link them, don't copy them — keeps freshness) |

## Done looks like

```
$ /spec "Add walmart-supplier orders DAG for marketplaces fan-out"
> build_type? [pipeline|feature|bug|ops|infra] (suggested: pipeline based on 'DAG')
pipeline
> Which source? [amazon-sp-api|amazon-ads-api|walmart-supplier|walmart-ad-connect|shopify|levanta|toolio|netsuite|other]
walmart-supplier
> Loading canon... pinned gc_data_v2_commit=a7c1...8e9d
> seed.md written to features/story-NNN-walmart-orders-dag/seed.md
> .project updated: build_type=pipeline, gc_data_v2_commit=a7c1...8e9d
> config.yaml updated: build_type=pipeline
Phase 1 complete — gated. Run /next STORY-NNN after approval.

$ grep -E 'build_type|gc_data_v2_commit' .project
build_type: pipeline
gc_data_v2_commit: a7c1bf2e0d9f4c1a8e2d5f6a3b9c0d1e4f5a6b8e9d

$ grep -A4 '^## Canon loaded' features/story-NNN-walmart-orders-dag/seed.md
## Canon loaded
- gc-data-v2/platform/pipeline-standard.md
- gc-data-v2/platform/failure-modes.md
- gc-data-v2/platform/new-pipeline-repo.md
- gc-data-v2/sources/walmart-supplier/README.md
```

## Escalation contract

Inherits the epic's escalation contract (STORY-1000 seed § Escalation contract). Story-specific escalations to Mark:

1. The `gc-data-v2` path is not present on the host running `/spec` (cannot resolve commit SHA). Default behaviour: abort with a needs_info row explaining how to clone gc-data-v2.
2. A user picks `other` as the source and the seed cannot link a README — emit warning, continue, flag for canon-backport (STORY-1003) to create the source folder.
3. The heuristic mis-classifies a non-pipeline build as `pipeline` and the user disagrees twice — accept the override and log it for retro analysis. Do not loop.
4. `config.yaml` schema changes break a downstream consumer (e.g. ops-console reads `config.yaml`). Pause; coordinate with Work-Stream C before merging.


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

Medium scope: `1 → 6 → 7 → 8 → Done` (per epic seed line 205: "most Medium = `1 → 6 → 7 → 8 → Done`"). Phase 6 produces `feature-spec.md` covering the classifier UX, persistence contract, source-validation logic, and the new-project scaffold path. Phase 7 produces RED tests in `tests/sdlc/test_spec_classifier.py`, `test_phase1_canon_load.py`, `test_new_project_pipeline_scaffold.py`. Phase 8 turns them GREEN and ships the PR against `sdlc-framework`.

## Open questions for Phase 6

These are intentionally NOT decided in Phase 1 — flag them for the design phase:

1. **Heuristic confidence threshold.** Should the heuristic auto-select `pipeline` when matching ≥2 keywords (high confidence), or always prompt the user to confirm? Default proposal: always prompt with the suggested value pre-filled.
2. **Source slug normalisation.** `gc-data-v2/sources.yaml` uses kebab-case keys (`amazon-sp-api`); some users will type `amazon_sp_api` or `Amazon SP API`. Should we normalise (lowercase + replace `_`/space → `-`), or reject with "exact match required"? Default proposal: normalise, then validate.
3. **`other` slug downstream behaviour.** When `other` is chosen, what stand-in path is recorded for the source README? Default proposal: omit the README from the canon list and emit `seed_warning: source_other`.
4. **Multi-source stories.** If a story spans 2+ sources (e.g. backfill that touches both walmart-supplier and walmart-ad-connect), should `build_type: pipeline` carry a list of sources? Default proposal: yes — `.project` accepts `source:` as a string or YAML list; `seed.md`'s `## Source` section enumerates each.
5. **Config.yaml schema migration.** Existing `config.yaml` files in `tech-dev-agents` and other repos have no `build_type:` key. Does writing it constitute a schema bump that requires updating downstream readers? Default proposal: treat absent key as `unknown` (back-compat), only `pipeline` triggers new behaviour.

## Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Heuristic over-classifies feature builds as pipeline (false positive) | Medium | Low (user can override) | Always prompt; never auto-decide; log overrides for retro analysis |
| `gc-data-v2` not present on host (e.g. Codex execution context) | Low–Medium | High (blocks Phase 1) | Detect absence early; emit needs_info with `git clone https://github.com/hpi-gorillacommerce/gc-data-v2 ~/test_projects/gc-data-v2` and pause |
| `sources.yaml` parsing fails (malformed YAML upstream) | Low | Medium | Fall back to a hardcoded enum (the 8 named sources) + `other`; log warning |
| Downstream consumers (Morris, dispatch service) crash on new `build_type` key | Low | High | Defensive defaults in this story's PR description; Work-Stream B + C consumers gated separately |
| Phase 1 prompt fatigue (one more question delays seeds) | Medium | Low | Default value via heuristic; one keypress to accept |
| `.sdlc` submodule bump in tech-dev-agents collides with concurrent epic work | Medium | Medium | Coordinate via Morris merge gate; submodule bump is the *last* PR in this story |

## Dependencies & sequencing

- **Upstream:** none (this is the leading story in Work-Stream A).
- **Downstream:** STORY-1002 consumes `build_type` + `gc_data_v2_commit`; STORY-1003 uses `build_type` in its retro-keyword match; STORY-1004's `pipeline-kickoff` reads `sources.yaml`. All three Medium stories in Work-Stream A wait on this PR merging first.
- **Cross-stream:** STORY-1006 (Morris `pre-dispatch-validate`) reads `build_type` from `config.yaml` to know which seed-completeness schema to apply. Per epic sequencing (line 105-108), STORY-1005 + 1006 ship in week 1 alongside this story — coordinate via Morris merge gate so the `build_type` key is present in `config.yaml` before `pre-dispatch-validate` starts reading it.

## Test fixtures needed (for Phase 7)

The following fixture files MUST exist before Phase 7 can write RED tests:

- `tests/fixtures/sdlc/spec-pipeline-input.txt` — synthetic input "Add walmart-supplier orders DAG"
- `tests/fixtures/sdlc/spec-feature-input.txt` — synthetic input "Add dark mode toggle"
- `tests/fixtures/sdlc/spec-bug-input.txt` — synthetic input "Fix off-by-one in order count"
- `tests/fixtures/sdlc/expected-pipeline-seed.md` — golden file for SC-3 assertion
- `tests/fixtures/sdlc/expected-feature-seed.md` — golden file for SC-6 regression
- `tests/fixtures/sdlc/gc-data-v2-snapshot/` — pinned-SHA snapshot of `gc-data-v2/sources.yaml` + minimum platform docs (so tests are deterministic and don't depend on the live checkout)

Phase 7 deliverable `test-design.md` MUST list these fixtures explicitly.

## Notes for Phase 6 designer

- The classifier is a 5-value enum, **not** free text. Phase 6 must spec a closed enum with validation, not a "describe your build" prompt that returns a string.
- `build_type` is written twice deliberately. `config.yaml` is project-scoped (persists across stories, read by Morris / dispatch). `.project` is story-scoped (per-story, read by phase skills). Don't collapse them into one location.
- The source-selection sub-prompt is **only** for `build_type == pipeline`. Other build types skip it; don't generalise prematurely.
- `gc_data_v2_commit` SHA is pinned **once at seed-write time**. Mid-story refreshes are STORY-1002's job via `load-canon --refresh`. Don't add refresh logic to Phase 1.
- The heuristic keyword list should be **data**, not hardcoded into prompts: store in `skills/spec/build-type-classifier.md` as a YAML block so retro can update it without touching the SKILL.md.
- Codex parity (SC-8) means: if a user runs `spec <description>` in Codex plain language, they should get the same `build_type` prompt, the same source enum, the same persistence. Same flow, same surface, different runtime.

## Worked example — Phase 1 with new classifier

User: `/spec "Add walmart-supplier orders DAG for marketplaces fan-out"`

Phase 1 flow:

1. Read `.project`, `config.yaml`, `backlog.md`. Existing build_type unknown.
2. **NEW STEP** — Scan description for heuristic keywords: matches `DAG`, `walmart-supplier`, `marketplaces`. Suggests `build_type: pipeline`.
3. Prompt user: `build_type? [pipeline|feature|bug|ops|infra] (suggested: pipeline)`. User confirms.
4. **NEW STEP** — Since `pipeline`, prompt: `Which source? [amazon-sp-api|amazon-ads-api|walmart-supplier|walmart-ad-connect|shopify|levanta|toolio|netsuite|other]`. User: `walmart-supplier`.
5. Validate against `gc-data-v2/sources.yaml`. Found.
6. **NEW STEP** — Resolve `gc_data_v2_commit` via `git -C /mnt/c/Projects/gc-data-v2 rev-parse HEAD`. Pin into `.project` under the new story's Phase Routing.
7. Continue existing Phase 1: scope classification (Medium — crosses DAG + transforms + dbt), seed.md template, etc.
8. `seed.md` now includes:
   ```
   ## Canon loaded
   - gc-data-v2/platform/pipeline-standard.md
   - gc-data-v2/platform/failure-modes.md
   - gc-data-v2/platform/new-pipeline-repo.md
   - gc-data-v2/sources/walmart-supplier/README.md

   ## Source
   slug: walmart-supplier
   auth_method: OAuth 2.0 (generic)
   repo: hpi-gorillacommerce/walmart-supplier-v2
   status: active
   gc_data_v2_commit: a7c1bf2e0d9f4c1a8e2d5f6a3b9c0d1e4f5a6b8e9d
   ```
9. Write `build_type: pipeline` to both `config.yaml` (top level) and `.project` (under STORY-NNN block).
10. Asana task created as usual. Phase 1 gate. Done.

Existing feature flow (regression baseline):

1. User: `/spec "Add dark mode toggle"`
2. Heuristic: no matches. Suggests `feature`.
3. Prompt `build_type?` — user confirms `feature`.
4. **No source prompt** (only fires for `pipeline`).
5. **No gc_data_v2_commit pinning** (no canon to pin).
6. Continue existing flow. `seed.md` has no `## Canon loaded` or `## Source` sections.
7. `config.yaml` writes `build_type: feature`.
