# STORY-1002 — NEW skill `load-canon` + phase-6/7/10 pipeline-aware modifications

**Story ID:** STORY-1002
**Scope:** Medium
**Epic:** STORY-1000 (Cross-Repo Canon Alignment)
**Work-Stream:** A — SDLC framework knowledgebase integration
**Repo touched:** `sdlc-framework` (and the `.sdlc` submodule pointer in `tech-dev-agents`)
**Date:** 2026-05-18
**Status:** Phase 1 — seed written, gated awaiting approval
**Frontend:** false

## Problem

STORY-1001 wires canon loading into Phase 1 only. But the 14 axes in `gc-data-v2/platform/pipeline-standard.md` are relevant at every phase: Phase 6 needs to validate the design against them, Phase 7 needs to enforce that `contracts/test_entities_yaml.py` + `contracts/test_conformance.py` are RED before implementation (those are the two CI gates in `gc-data-v2/platform/failure-modes.md` § "CI gate 1" and "CI gate 2"), and Phase 10 needs to point at `gc-data-v2/platform/observability.md` for Airflow/DAG monitoring instead of the generic SRE checklist in `skills/phase-10/SKILL.md` lines 30-46. Today none of those phases knows whether the build is a pipeline; the Phase 6 deliverable (`feature-spec.md`) has no pipeline-design-validation step, Phase 7's mandatory test list is API-write-path-focused (line 23: "Gate 2a (External API Isolation Tests)") not pipeline-contract-focused, and Phase 10 is generic SRE — no Airflow, no DAG observability, no Iceberg-table SLOs. Additionally, canon loading is hard-coded into Phase 1 with no callable hook for later phases — a Phase 6 designer who wants to refresh the canon mid-design has no skill to call.

## Goal

A standalone `load-canon` skill that any phase can invoke to load (or re-load) the right canon docs into context, recording what was loaded in `.project` idempotently. Plus three pipeline-aware phase modifications: Phase 6 validates designs against the 14 axes, Phase 7 enforces the two contract-test gates, Phase 10 produces an Airflow/DAG-flavoured `site-reliability.md`.

## Scope

- **NEW skill** `/mnt/c/Projects/sdlc-framework/skills/load-canon/SKILL.md`:
  - Phase-aware: takes `--phase=<N>` argument; loads a phase-appropriate slice of canon. Default slice per phase:
    - Phase 1: `pipeline-standard.md`, `failure-modes.md`, `new-pipeline-repo.md`, `sources/<src>/README.md` (matches STORY-1001's Phase 1 loader).
    - Phase 6: above + `architecture.md`, `airflow-conventions.md`, `dbt-conventions.md`, `iceberg-conventions.md`, `storage-conventions.md`, `auth-patterns.md` (the 6 design-time docs).
    - Phase 7: above + `testing-conventions.md`, `data-contracts.md`, `pipeline-template/tests/contracts/test_entities_yaml.py`, `pipeline-template/tests/contracts/test_conformance.py`.
    - Phase 10: `observability.md`, `slo-targets.md`, `airflow-conventions.md` (DAG retry/alerting), `failure-modes.md` (runbook table).
  - Records loaded paths under `.project` key `canon_loaded:` as a list of `{phase: N, path: <p>, sha: <gc_data_v2_commit>}` entries.
  - **Idempotent:** re-loading the same canon (same paths, same SHA) is a no-op and prints `canon already loaded at SHA <x>; skipping`. If the SHA has advanced, prompt the user to refresh (default: yes).
  - Callable as `/load-canon --phase=6 --source=walmart-supplier` from any phase or directly by an agent.
  - Refuses to run if `build_type` is not `pipeline` (returns: `load-canon is only meaningful for pipeline builds; current build_type=<x>`). Future stories can extend to other build types.
- **MODIFY** `/mnt/c/Projects/sdlc-framework/skills/phase-6/SKILL.md`:
  - Insert a "Pipeline-aware branch" section after the Pre-flight Check. When `build_type == pipeline`: invoke `/load-canon --phase=6` first, then run a new validation step against the 14 axes of `pipeline-standard.md` (sections 1-14: repo structure, layers, ingestion patterns, materialiser, contracts, DAG shape, auth, observability, dbt, CI, deploy, runbook, SLOs, security).
  - Produce a new sub-deliverable `pipeline-design-validation.md` alongside `feature-spec.md` (Medium) / `architecture.md` (Large). Format: one row per axis with status `pass | fail | n/a` + one-line evidence.
  - Refuse to advance to Phase 7 if any axis is `fail` without a Mark-approved deviation note.
- **MODIFY** `/mnt/c/Projects/sdlc-framework/skills/phase-7/SKILL.md`:
  - Replace the current "MANDATORY CHECK" (line 23 — Gate 2a External API Isolation) with branched logic: for `build_type == pipeline`, RED tests MUST include copies (or wired equivalents) of `gc-data-v2/pipeline-template/tests/contracts/test_entities_yaml.py` and `gc-data-v2/pipeline-template/tests/contracts/test_conformance.py`. For other build types, current Gate 2a stays.
  - Both contract tests must be in RED state initially (Phase 7's existing invariant — line 33: "All tests must be in RED state").
  - Add the canonical paths to the `test-design.md` template so the deliverable lists them explicitly.
- **MODIFY** `/mnt/c/Projects/sdlc-framework/skills/phase-10/SKILL.md`:
  - Insert a "Pipeline variant" branch after step 2 (persona adoption). When `build_type == pipeline`:
    - Steps 6 (health endpoints), 7 (Prometheus metrics), and 12 (external monitoring) are reframed for Airflow: DAG-run health (success rate, last-success age), task-level retry counters, Iceberg materialiser duration, landing→bronze freshness lag.
    - Step 11 (runbooks) MUST cross-link `gc-data-v2/platform/failure-modes.md` for each alert. Every alert in the resulting `site-reliability.md` links to the failure-modes.md section that owns its symptom.
    - Step 13 (deployment safety) MUST reference `gc-data-v2/platform/failure-modes.md` Deploy gates 1-5 (OIDC, vendoring, GLIBC pin, KV reference, etc.).
  - Produce the same `site-reliability.md` filename for back-compat; only the *contents* differ.
- Update `software-development-guidance.md` table of phases with the pipeline-aware deliverables.

## Out of scope

- The `build_type` classifier itself (STORY-1001 — this story depends on it being merged first).
- The 3-question retro gate + `canon-backport` (STORY-1003).
- `scaffold-drift-check` byte-diff logic (STORY-1004 — that runs in Phase 6 but is its own skill).
- Phase 8 implementation behaviour (no change here — Phase 8 turns RED tests GREEN regardless of build_type).
- Phase 8b code review pipeline-awareness — covered by Morris PR review (Work-Stream B).
- Non-pipeline build types (feature/bug/ops/infra) — `load-canon` refuses; future stories may extend.
- Modifying the contract-test source files in `gc-data-v2` — Phase 7 *consumes* them.

## Success criteria (acceptance criteria)

1. **SC-1 — `load-canon` skill exists and is invocable:** `/load-canon --phase=6 --source=walmart-supplier` from a pipeline story prints the 10+ doc paths it loaded and exits 0.
2. **SC-2 — Idempotency:** running `/load-canon --phase=6 ...` twice in a row produces the second time: `canon already loaded at SHA <x>; skipping`. `.project` has exactly one entry per `(phase, path, sha)` triple.
3. **SC-3 — SHA-advance prompt:** if `gc-data-v2` HEAD has advanced since the last `canon_loaded` entry, `/load-canon` prompts `gc-data-v2 has advanced from <old> to <new>; refresh? [Y/n]` and on yes overwrites the entries for the matching phase.
4. **SC-4 — Non-pipeline refusal:** `/load-canon --phase=6` in a `build_type: feature` story prints `load-canon is only meaningful for pipeline builds; current build_type=feature` and exits non-zero.
5. **SC-5 — Phase 6 validation deliverable:** running Phase 6 in a pipeline story produces `features/story-XXX/pipeline-design-validation.md` containing exactly 14 axis rows (one per § in `pipeline-standard.md` § "The 14 axes").
6. **SC-6 — Phase 6 fail-axis blocks advance:** if any axis row is `fail` and no `deviation_approved_by: mark` line is present, attempting `/next` from Phase 6 returns `cannot advance: axis '<name>' is fail without approved deviation`.
7. **SC-7 — Phase 7 contract tests required:** `test-design.md` from a Phase 7 pipeline run lists `tests/contracts/test_entities_yaml.py` and `tests/contracts/test_conformance.py`; both files exist under the story's `tests/contracts/` and are RED.
8. **SC-8 — Phase 10 pipeline variant:** `site-reliability.md` from a Phase 10 pipeline run contains `## Airflow DAG observability` section, has at least 4 DAG-specific SLIs, and every alert row links a `gc-data-v2/platform/failure-modes.md#...` anchor.
9. **SC-9 — Non-pipeline regression:** running Phase 6, 7, 10 in a `feature` story produces deliverables byte-identical to today's output (no behavioural change for feature builds).

## Files to modify

**NEW:**
- `/mnt/c/Projects/sdlc-framework/skills/load-canon/SKILL.md` — the new skill.
- `/mnt/c/Projects/sdlc-framework/skills/load-canon/phase-slices.md` — table of which docs load per phase (referenced from SKILL.md).
- `/mnt/c/Projects/sdlc-framework/templates/pipeline-design-validation.md` — 14-axis template for Phase 6.
- `/mnt/c/Projects/sdlc-framework/templates/pipeline-site-reliability-additions.md` — Airflow/DAG section blocks for Phase 10.

**MODIFY:**
- `/mnt/c/Projects/sdlc-framework/skills/phase-6/SKILL.md` — pipeline-aware branch + `pipeline-design-validation.md` deliverable.
- `/mnt/c/Projects/sdlc-framework/skills/phase-7/SKILL.md` — branched MANDATORY CHECK; contract-test requirement.
- `/mnt/c/Projects/sdlc-framework/skills/phase-10/SKILL.md` — pipeline variant for steps 6, 7, 11, 12, 13.
- `/mnt/c/Projects/sdlc-framework/software-development-guidance.md` — update phase-deliverable table.
- `/mnt/c/Projects/sdlc-framework/agents/phase-6-design.md` — append the 14-axis validation instructions.
- `/mnt/c/Projects/sdlc-framework/agents/phase-7-test-design.md` — append contract-test enforcement.
- `/mnt/c/Projects/sdlc-framework/agents/phase-10-operations.md` — append the Airflow/DAG SRE variant.

**Tracking docs:**
- `/mnt/c/Projects/tech-dev-agents/.project` — Phase 1 status updates for STORY-1002.
- Monday.com task comment.

## Files to NOT modify

- `/mnt/c/Projects/gc-data-v2/**` — referenced only.
- `/mnt/c/Projects/sdlc-framework/skills/spec/`, `phase-1/`, `new-project/` — those are STORY-1001's scope. `load-canon` is *invoked from* Phase 1 (and reuses its loader) but doesn't modify the Phase 1 skill.
- `/mnt/c/Projects/sdlc-framework/skills/phase-8/`, `phase-8b/`, `phase-9/`, `phase-11/` — out of scope this story.
- `tests/contracts/test_entities_yaml.py` and `test_conformance.py` in `gc-data-v2/pipeline-template/` — consumed verbatim.

## Verification plan

| SC | Shell command | Expected output |
|---|---|---|
| SC-1 | In a pipeline-build worktree: `/load-canon --phase=6 --source=walmart-supplier` then `grep -c '^- ' /tmp/load-canon.log` | `>=10` lines starting with `- ` (paths loaded) |
| SC-2 | Run `/load-canon --phase=6 ...` twice; `grep -c walmart-supplier .project` | second run prints `canon already loaded`; entry count unchanged between runs |
| SC-3 | Mutate fake SHA in `.project` to old value; rerun `/load-canon --phase=6` | Prompts `gc-data-v2 has advanced from <old> to <new>; refresh? [Y/n]` |
| SC-4 | In a `build_type: feature` story: `/load-canon --phase=6`; capture exit code | Stdout: `load-canon is only meaningful for pipeline builds; current build_type=feature`. `$?` != 0 |
| SC-5 | After Phase 6 run on pipeline story: `awk '/^## Axis [0-9]+/' features/story-XXX/pipeline-design-validation.md \| wc -l` | `14` |
| SC-6 | Inject `fail` on axis 7 with no deviation; run `/next` | Stderr: `cannot advance: axis 'auth' is fail without approved deviation`; exit non-zero |
| SC-7 | After Phase 7 run on pipeline story: `pytest tests/contracts/test_entities_yaml.py tests/contracts/test_conformance.py` | Both tests FAIL (RED state); `test-design.md` lists both paths |
| SC-8 | `grep -c 'failure-modes.md#' features/story-XXX/site-reliability.md` and `grep -A2 '^## Airflow DAG observability' features/story-XXX/site-reliability.md` | `>=1` failure-modes link per alert; Airflow section exists with >=4 SLI bullets |
| SC-9 | Run Phase 6/7/10 against a `build_type: feature` baseline story before and after this change; `diff` the deliverables | `diff` empty for feature stories |

## Boundaries

| Always Do | Ask First | Never Do |
|---|---|---|
| Keep `load-canon` idempotent per `(phase, path, sha)` triple | Add a phase slice for a non-pipeline build_type | Modify `gc-data-v2/platform/*.md` from this story |
| Re-use STORY-1001's commit-pin logic (don't fork) | Add a 15th axis to the Phase 6 validation table | Bypass the contract-test requirement in Phase 7 for a "small" pipeline change |
| Cross-link every Phase 10 alert to a `failure-modes.md` anchor | Promote the Airflow/DAG SRE section into the default Phase 10 (replacing the generic flow) | Hard-code SHA, source slug, or doc path lists into `load-canon` — all driven by config + `gc-data-v2/sources.yaml` |
| Keep non-pipeline runs byte-identical (regression check SC-9) | Skip a phase slice (e.g. omit `dbt-conventions.md` for source-only changes) | Implement contract tests inside the SDLC framework — *load* the canonical ones from `gc-data-v2/pipeline-template/` |
| Refresh canon on SHA advance only with user confirmation | Auto-refresh on every phase invocation | Silently overwrite `canon_loaded:` entries — that hides drift |

## Done looks like

```
$ /load-canon --phase=6 --source=walmart-supplier
Resolving gc-data-v2 HEAD... a7c1bf2e0d9f4c1a8e2d5f6a3b9c0d1e4f5a6b8e9d
Loading canon for phase 6 (10 docs):
  - gc-data-v2/platform/pipeline-standard.md
  - gc-data-v2/platform/failure-modes.md
  - gc-data-v2/platform/new-pipeline-repo.md
  - gc-data-v2/platform/architecture.md
  - gc-data-v2/platform/airflow-conventions.md
  - gc-data-v2/platform/dbt-conventions.md
  - gc-data-v2/platform/iceberg-conventions.md
  - gc-data-v2/platform/storage-conventions.md
  - gc-data-v2/platform/auth-patterns.md
  - gc-data-v2/sources/walmart-supplier/README.md
Recorded 10 entries to .project under canon_loaded (phase=6).

$ /load-canon --phase=6 --source=walmart-supplier   # re-run
Canon already loaded for phase 6 at SHA a7c1bf2e...8e9d; skipping (10 entries unchanged).

$ /next   # advance Phase 6 → Phase 7
Phase 6 deliverables: feature-spec.md ✓, pipeline-design-validation.md ✓ (14/14 axes pass)
Advancing to Phase 7...

$ cat features/story-XXX/pipeline-design-validation.md | grep -c '^## Axis '
14
```

## Escalation contract

Inherits the epic's escalation contract. Story-specific escalations to Mark:

1. A canon doc referenced by `load-canon` is missing from `gc-data-v2` (e.g. a source README absent). Default: emit `canon_warning: missing_doc <path>`, continue, flag for `canon-backport` (STORY-1003).
2. The 14 axes in `pipeline-standard.md` change (gc-data-v2 PR adds a 15th, drops one) between this story's Phase 1 and Phase 8 — pause; coordinate with Work-Stream D (STORY-1013 changelog) before adapting.
3. A pipeline story genuinely needs a deviation from an axis (e.g. greenfield source with no dbt yet). Default: Phase 6 marks axis `n/a` with one-line justification; `fail` requires Mark approval.
4. The two contract tests (`test_entities_yaml.py`, `test_conformance.py`) themselves change in `gc-data-v2/pipeline-template/` mid-story — STORY-1004's drift-check should catch this; coordinate.


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

Medium scope: `1 → 6 → 7 → 8 → Done`. Phase 6 produces `feature-spec.md` covering the `load-canon` skill spec, the phase-slice table, and the three phase-mod contracts (6/7/10). Phase 7 produces RED tests under `tests/sdlc/test_load_canon_idempotent.py`, `test_load_canon_sha_refresh.py`, `test_phase6_pipeline_validation.py`, `test_phase7_contract_test_requirement.py`, `test_phase10_pipeline_variant.py`. Phase 8 turns them GREEN and ships the PR against `sdlc-framework`. Depends on STORY-1001 merging first (uses its `build_type` + commit-pin infrastructure).

## Open questions for Phase 6

1. **Phase 4/5 canon slices.** Do Phase 4 (Analysis) and Phase 5 (Selection) in Large/New paths need their own canon slices? Default proposal: yes for Large/New, no for Medium (Medium skips 4-5 per phase-1 SKILL.md line 78). Phase 4 slice = `architecture.md`, `aws-migration-path.md`, `maturity.md`; Phase 5 slice = `platform-gaps.md`, `pipeline-integration.md`.
2. **14-axis mapping to story scope.** Some axes (e.g. axis 9 dbt, axis 13 SLOs) may not apply to a story that's modifying an existing pipeline in a narrow way. Should `n/a` be allowed without justification for those? Default proposal: `n/a` always requires one-line justification (same rule as 3-question gate in STORY-1003).
3. **Contract test bootstrapping.** When a pipeline story is in a brand-new repo (no existing `tests/contracts/`), where do the canonical contract tests come from? Default proposal: `cp gc-data-v2/pipeline-template/tests/contracts/*.py <repo>/tests/contracts/` invoked from Phase 7. Pin to `gc_data_v2_commit`.
4. **Phase 10 in non-Airflow pipelines.** If a future pipeline doesn't use Airflow (e.g. Lambda-based), the DAG-flavoured Phase 10 variant breaks. Default proposal: detect Airflow presence via `airflow/dags/` directory in repo; if absent, fall back to a smaller variant that still cross-links failure-modes.md.
5. **SLO targets in Phase 10.** Should the pipeline variant pre-populate SLOs from `gc-data-v2/platform/slo-targets.md`, or always start from scratch? Default proposal: pre-populate with tier defaults; user can override.
6. **`load-canon` invocation from non-Phase contexts** (e.g. ad-hoc inspection during Phase 8 debugging). Allowed? Default proposal: yes — skill is callable from any phase, the `--phase=` arg drives the slice, no requirement that the calling phase actually be active.

## Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| `load-canon` doc list drifts from gc-data-v2 platform/ layout | Medium | Medium | Phase-slice table is data, not code; refresh on each `gc_data_v2_commit` bump |
| `.project` `canon_loaded:` list grows unbounded over a long story | Low | Low | Idempotency caps growth at one entry per `(phase, path, sha)`; documented Phase-10 close prunes stale entries |
| 14-axis validation becomes rubber-stamp ("all pass" without genuine review) | Medium | High | Require one-line evidence per axis; Mark-spot-checks via retro |
| Phase 7 contract-test enforcement breaks tiny pipeline changes (e.g. a single transform tweak) | Medium | Medium | Allow `entity_yaml_test_skip: reason=...` annotation; require deviation note |
| Phase 10 Airflow variant misses non-Airflow orchestrators | Low (today) | Medium (future) | See Open Question 4 |
| Concurrent `gc-data-v2` PRs change canon mid-story | Medium | Low (SHA-pinning shields) | SHA-refresh prompt (SC-3) gives explicit refresh path |
| Test fixtures for Phase 7 require gc-data-v2 checkout in CI | Medium | Low | Use a vendored snapshot in `tests/fixtures/sdlc/gc-data-v2-snapshot/` (shared with STORY-1001) |

## Dependencies & sequencing

- **Upstream:** STORY-1001 must merge first (provides `build_type`, `gc_data_v2_commit`, source slug in `.project`).
- **Downstream:**
  - STORY-1004's `scaffold-drift-check` is *invoked from* Phase 6 — this story's `phase-6/SKILL.md` mod must call it.
  - Morris STORY-1005 `canon-check` reuses the same canonical file list (the 3 byte-diffed + 1 content-asserted from `canon-drift-check.yml`). Coordinate via shared template under `sdlc-framework/skills/load-canon/phase-slices.md`.
- **Cross-stream:** STORY-1006 (Morris `pre-dispatch-validate`) inspects whether `canon_loaded:` is populated when `build_type == pipeline` — coordinate the `.project` key name.

## Test fixtures needed (for Phase 7)

- `tests/fixtures/sdlc/gc-data-v2-snapshot/` — same vendored snapshot as STORY-1001.
- `tests/fixtures/sdlc/pipeline-design-validation.golden.md` — golden 14-axis report.
- `tests/fixtures/sdlc/site-reliability-pipeline.golden.md` — golden Phase 10 Airflow variant.
- `tests/fixtures/sdlc/site-reliability-feature.golden.md` — golden Phase 10 generic (regression baseline).
- `tests/fixtures/sdlc/contract-test-fixtures/test_entities_yaml.py` — vendored copy of the canonical test (RED-state expected when run against an incomplete entities.yaml).
- `tests/fixtures/sdlc/contract-test-fixtures/test_conformance.py` — vendored copy of the canonical test (RED-state expected against an incomplete scaffold).

## Notes for Phase 6 designer

- The Phase 6 mod is the most invasive of the three phase-mods. Keep the existing Phase 6 flow intact for non-pipeline builds; the pipeline branch is *additive*, not a replacement.
- Be deliberate about ordering inside Phase 6 for pipeline builds:
  1. Pre-flight (worktree check) — existing.
  2. `/load-canon --phase=6` — new.
  3. `/scaffold-drift-check` — new (from STORY-1004).
  4. Design architecture/API/DB — existing.
  5. 14-axis validation — new, produces `pipeline-design-validation.md`.
  6. Advance gate — existing, with the new "all axes pass or deviation approved" check.
- Treat the 14 axes as **invariants the design must satisfy**, not a checklist of features to implement. Phase 8's job is implementation; Phase 6's job is proving the design respects each axis.
