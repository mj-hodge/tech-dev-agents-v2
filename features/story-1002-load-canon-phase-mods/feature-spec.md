# Feature Spec: STORY-1002 — load-canon skill + phase-6/7/10 pipeline-aware modifications

**Story:** STORY-1002
**Scope:** Medium
**Phase:** 6 (Design)
**Date:** 2026-05-19
**Status:** Phase 6 complete

---

## Summary

This story delivers:
1. A new standalone `load-canon` skill that any SDLC phase can invoke to load gc-data-v2 canon documents into context — phase-aware, idempotent, with SHA-refresh prompting.
2. Three pipeline-aware modifications to existing phase skills: Phase 6 (14-axis design validation), Phase 7 (contract-test gate), Phase 10 (Airflow/DAG-flavoured SRE output).
3. A `tech_dev_agents.sdlc.canon_helpers` Python module that implements the supporting logic for `.project` state management and pipeline-standard parsing.

---

## Deliverables

### NEW: `skills/load-canon/SKILL.md`

The load-canon skill is invocable as `/load-canon --phase=<N> --source=<slug>`. Key behaviours:

- **Pipeline-only:** Refuses with a non-zero exit when `build_type != pipeline`. Error message: `load-canon is only meaningful for pipeline builds; current build_type=<x>`.
- **Phase-aware slices:** The set of canon docs loaded is driven by the phase argument, defined in `skills/load-canon/phase-slices.md`.
- **Idempotent per (phase, path, sha) triple:** Re-loading the same canon at the same SHA is a no-op. Prints: `Canon already loaded for phase <N> at SHA <sha>; skipping (<count> entries unchanged).`
- **SHA-advance prompting:** If `gc-data-v2` HEAD has advanced since the last `canon_loaded:` entry for this phase, prompts: `gc-data-v2 has advanced from <old> to <new>; refresh? [Y/n]`. On yes: overwrites entries for the phase. On no: keeps old, exits 0 with warning.
- **Missing-doc tolerance:** If a canon doc is absent from gc-data-v2, emits `canon_warning: missing_doc <path>` and continues (does not abort).
- **Records in `.project`:** Under `canon_loaded:` key, adds `{phase, path, sha}` entries.

**Phase slice definitions** (from `skills/load-canon/phase-slices.md`):

| Phase | Document Set |
|-------|-------------|
| 1 | `pipeline-standard.md`, `failure-modes.md`, `new-pipeline-repo.md`, `sources/<slug>/README.md` |
| 6 | Phase 1 docs + `architecture.md`, `airflow-conventions.md`, `dbt-conventions.md`, `iceberg-conventions.md`, `storage-conventions.md`, `auth-patterns.md` (~10 docs) |
| 7 | Phase 6 docs + `testing-conventions.md`, `data-contracts.md`, `pipeline-template/tests/contracts/test_entities_yaml.py`, `pipeline-template/tests/contracts/test_conformance.py` (~14 docs) |
| 10 | `observability.md`, `slo-targets.md`, `airflow-conventions.md`, `failure-modes.md` (standalone, 4 docs) |

### NEW: `skills/load-canon/phase-slices.md`

Table of which gc-data-v2 documents load per phase, with rationale. Acts as a shared data source for STORY-1005 (Morris canon-check) and STORY-1006 (pre-dispatch-validate). Inheritance rules: Phase 6 inherits Phase 1; Phase 7 inherits Phase 6; Phase 10 is standalone.

### MODIFIED: `skills/phase-6/SKILL.md`

Adds a **Pipeline-Aware Branch** section (activates when `build_type == pipeline`), inserted after the Pre-flight Check:

- **Step P1:** Invoke `/load-canon --phase=6 --source=<slug>` before design work.
- **Step P2:** Invoke `/scaffold-drift-check` (from STORY-1004; skip if unavailable).
- **Step P3:** 14-axis validation against `pipeline-standard.md` sections 1-14. Produces `pipeline-design-validation.md`.
- **Step P4:** Advance gate — blocks Phase 7 if any axis is `fail` without `deviation_approved_by: mark`.

**New deliverable (pipeline builds):** `features/<story>/pipeline-design-validation.md`  
**Template:** `templates/pipeline-design-validation.md` — 14 axis rows with `pass | fail | n/a` + evidence.

Non-pipeline builds: unchanged behaviour (SC-9 regression requirement).

### MODIFIED: `skills/phase-7/SKILL.md`

Replaces the existing monolithic MANDATORY CHECK with a **branched** check:

- **If `build_type == pipeline`:** RED tests MUST include `tests/contracts/test_entities_yaml.py` and `tests/contracts/test_conformance.py` (copied from `gc-data-v2/pipeline-template/tests/contracts/`). Both must be in RED state. `test-design.md` must list them under a "Pipeline Contract Tests" section. Pinned to `gc_data_v2_commit`.
- **If `build_type != pipeline`:** Existing Gate 2a (External API Isolation Tests) remains.

### MODIFIED: `skills/phase-10/SKILL.md`

Adds a **Pipeline Variant** section (activates when `build_type == pipeline`):

- **Step 2a:** Load canon (`/load-canon --phase=10`).
- **Steps 6, 7, 12 reframed:** DAG-run health, task retry counters, Iceberg materialiser duration, landing-to-bronze lag — replace generic health/Prometheus/uptime checks.
- **Step 11:** Every alert MUST cross-link `gc-data-v2/platform/failure-modes.md#<anchor>`.
- **Step 13:** MUST reference `failure-modes.md` Deploy gates 1-5.

**New additions to `site-reliability.md`:** `## Airflow DAG Observability` section with ≥4 DAG-specific SLIs; every alert links a failure-modes.md anchor.

**Template:** `templates/pipeline-site-reliability-additions.md` — DAG SLI table, alerting definitions, deploy gates cross-reference, runbook template.

Non-pipeline builds: unchanged (SC-9 regression requirement).

### NEW: `templates/pipeline-design-validation.md`

14-axis template with placeholder rows. Each axis has `Status`, `Evidence`, and `Deviation` fields. Deviations log at bottom. Canon reference footer.

### NEW: `templates/pipeline-site-reliability-additions.md`

Template blocks for the pipeline `site-reliability.md` variant:
- `## Airflow DAG Observability` — DAG SLIs, health checks, alerting (with failure-modes.md links)
- `## Deployment Safety (Pipeline Variant)` — deploy gates cross-reference
- `## Runbooks (Pipeline Variant)` — runbook template with failure-modes.md cross-link

### NEW: `tech_dev_agents/sdlc/canon_helpers.py`

Python support module for the load-canon skill. Implements:

| Function | Signature | Behaviour |
|----------|-----------|-----------|
| `load_pipeline_standard(path)` | `str → dict[str, str]` | Reads a pipeline-standard.md file and returns a dict of `{axis_name: description}` for all 14 axes. Raises `FileNotFoundError` if path missing; `ValueError` if fewer than 14 axes found. |
| `compute_canon_hash(path)` | `str → str` | SHA-256 of file contents, returns first 12 hex chars. |
| `write_canon_state(content, state)` | `str, dict → str` | Inserts or replaces a `## Canon State` table section in `.project` content (between `## Phase Routing` and `## Project Overview`). Idempotent — second write replaces first. |
| `read_canon_state(content)` | `str → dict[str, str]` | Extracts key/value rows from the `## Canon State` table. Returns empty dict if section absent. |
| `is_canon_loaded(state)` | `dict → bool` | Returns `True` iff `state.get("canon_loaded") == "true"`. |

---

## Design Decisions

1. **SKILL.md files ARE the implementation** for this story. They encode the workflow instructions to agents. The Python `canon_helpers.py` is a supporting helper for `.project` state management.

2. **Idempotency via (phase, path, sha) triples** in `.project `— not a single boolean flag. This allows multiple phases to load canon independently and lets SHA-advance be detected per-phase.

3. **Non-pipeline regression (SC-9)** enforced by the branched structure: all pipeline-specific logic is inside `if build_type == pipeline` blocks that exit early for other build types.

4. **Phase 10 standalone slice** (does not inherit Phase 7's slice) — operations context differs from test context. Phase 10 loads `observability.md`, `slo-targets.md`, `airflow-conventions.md`, `failure-modes.md` — focused on monitoring, not contracts.

5. **Missing-doc tolerance** — canon loading continues even if a doc is absent, emitting a `canon_warning`. This prevents a missing source README from blocking Phase 6 design entirely. STORY-1003 handles backfill.

6. **`deviation_approved_by: mark`** in `pipeline-design-validation.md` is the approval signal. The `/next` advance check scans for this literal string adjacent to any `fail` axis. The string is intentionally human-written (not generated), providing a light-weight approval record.

---

## Files Modified / Created

| File | Action | Status |
|------|--------|--------|
| `.sdlc/skills/load-canon/SKILL.md` | Created | ✓ Done |
| `.sdlc/skills/load-canon/phase-slices.md` | Created | ✓ Done |
| `.sdlc/skills/phase-6/SKILL.md` | Modified | ✓ Done |
| `.sdlc/skills/phase-7/SKILL.md` | Modified | ✓ Done |
| `.sdlc/skills/phase-10/SKILL.md` | Modified | ✓ Done |
| `.sdlc/templates/pipeline-design-validation.md` | Created | ✓ Done |
| `.sdlc/templates/pipeline-site-reliability-additions.md` | Created | ✓ Done |
| `tech_dev_agents/sdlc/__init__.py` | Create | Phase 8 |
| `tech_dev_agents/sdlc/canon_helpers.py` | Create | Phase 8 |
| `tests/fixtures/sdlc/gc-data-v2-snapshot/platform/pipeline-standard.md` | Create | Phase 8 |

---

## Acceptance Criteria Mapping

| SC | Status |
|----|--------|
| SC-1 (load-canon invocable) | SKILL.md written ✓ |
| SC-2 (idempotency) | SKILL.md step 3 + canon_helpers write_canon_state ✓ |
| SC-3 (SHA-advance prompt) | SKILL.md step 3 ✓ |
| SC-4 (non-pipeline refusal) | SKILL.md step 1 ✓ |
| SC-5 (14-axis deliverable) | phase-6 SKILL.md step P3 + template ✓ |
| SC-6 (fail-axis blocks advance) | phase-6 SKILL.md step P4 ✓ |
| SC-7 (Phase 7 contract tests) | phase-7 SKILL.md ✓ |
| SC-8 (Phase 10 Airflow section) | phase-10 SKILL.md + template ✓ |
| SC-9 (non-pipeline regression) | branched structure preserves existing flow ✓ |

---

## Open Questions Resolved

1. **Phase 4/5 canon slices:** Out of scope for Medium path (Medium skips 4-5). Proposed in `phase-slices.md` Future Extensions table for Large/New.
2. **`n/a` justification:** Required (one-line). Noted in template and advance gate.
3. **Contract test bootstrapping:** Phase 7 copies from `gc-data-v2/pipeline-template/tests/contracts/`. Pinned to `gc_data_v2_commit`. Fixtures vendored in `tests/fixtures/sdlc/` for CI.
4. **Phase 10 non-Airflow:** Falls back if no `airflow/dags/` dir. Noted in SKILL.md.
5. **SLO pre-population:** Pre-populate from `slo-targets.md` tier defaults; user can override.
6. **Ad-hoc load-canon invocation:** Allowed from any phase (skill callable from any context).
