# Test Design: STORY-1002 — load-canon skill + phase-6/7/10 pipeline-aware modifications

**Story:** STORY-1002
**Phase:** 7 (Test Design)
**Date:** 2026-05-19
**Status:** Phase 7 complete — all tests in RED state

---

## Test File Structure

```
tests/sdlc/test_load_canon_skill.py          # Primary — SKILL.md structure + canon_helpers module
tests/sdlc/test_phase_pipeline_mods.py       # Phase 6/7/10 SKILL.md modifications
tests/fixtures/sdlc/gc-data-v2-snapshot/
  platform/
    pipeline-standard.md                     # Vendored fixture: 14-axis standard
```

---

## Test Groups

### Group A — `TestLoadCanonSkillMd` (test_load_canon_skill.py)

Validates the `load-canon` SKILL.md has the correct structure per spec.

| ID | Test | RED Reason |
|----|------|------------|
| A-01 | `test_skill_md_exists` | File exists already (should PASS after Phase 8 if it exists) |
| A-02 | `test_skill_md_has_yaml_frontmatter` | Validates `name: load-canon` + `description:` in frontmatter |
| A-03 | `test_skill_md_has_required_sections` | Validates `## Workflow`, `## Outputs`, `## Error Handling`, `## Idempotency` present |
| A-04 | `test_phase_slices_md_exists` | `skills/load-canon/phase-slices.md` must exist |
| A-05 | `test_phase_slices_has_all_phases` | phase-slices.md must document slices for phases 1, 6, 7, 10 |

**RED reason for A-03:** Current SKILL.md has no `## Idempotency` section — needs to be added in Phase 8.

### Group B — `TestCanonHelpersPipelineLoading` (test_load_canon_skill.py)

Validates `tech_dev_agents.sdlc.canon_helpers` module — parsing and hashing.

| ID | Test | RED Reason |
|----|------|------------|
| B-01 | `test_canon_helpers_module_exists` | Module `tech_dev_agents.sdlc.canon_helpers` does not exist yet |
| B-02 | `test_load_pipeline_standard_parses_axes` | Module not yet importable; fixture not yet created |
| B-03 | `test_compute_canon_hash_format` | Module not yet importable |

### Group C — `TestProjectStateManagement` (test_load_canon_skill.py)

Validates `.project` state read/write functions in `canon_helpers`.

| ID | Test | RED Reason |
|----|------|------------|
| C-01 | `test_write_canon_state_adds_section` | Module not yet importable |
| C-02 | `test_read_canon_state_extracts_fields` | Module not yet importable |
| C-03 | `test_write_canon_state_idempotent` | Module not yet importable |

### Group D — `TestErrorHandling` (test_load_canon_skill.py)

Validates error conditions in canon_helpers.

| ID | Test | RED Reason |
|----|------|------------|
| D-01 | `test_load_pipeline_standard_missing_file` | Module not yet importable |
| D-02 | `test_load_pipeline_standard_malformed_content` | Module not yet importable |
| D-03 | `test_is_canon_loaded` | Module not yet importable |

### Group E — `TestPhase6PipelineMod` (test_phase_pipeline_mods.py)

Validates phase-6/SKILL.md has the pipeline-aware branch.

| ID | Test | RED Reason |
|----|------|------------|
| E-01 | `test_phase6_has_pipeline_branch_section` | Must find `## Pipeline-Aware Branch` heading |
| E-02 | `test_phase6_has_load_canon_step` | Must find `/load-canon --phase=6` in SKILL.md |
| E-03 | `test_phase6_has_14_axis_validation_step` | Must find `14` axes reference and `pipeline-design-validation.md` |
| E-04 | `test_phase6_advance_gate_blocks_on_fail` | Must find `cannot advance` error message in SKILL.md |
| E-05 | `test_pipeline_design_validation_template_exists` | `templates/pipeline-design-validation.md` must exist |
| E-06 | `test_pipeline_design_validation_template_has_14_axes` | Template must contain `## Axis 1` … `## Axis 14` |

**RED reasons:** E-05 and E-06 depend on template file (already created, so these may pass after confirming).

### Group F — `TestPhase7PipelineMod` (test_phase_pipeline_mods.py)

Validates phase-7/SKILL.md has the branched contract-test requirement.

| ID | Test | RED Reason |
|----|------|------------|
| F-01 | `test_phase7_has_branched_mandatory_check` | Must find `build_type == pipeline` condition |
| F-02 | `test_phase7_requires_contract_test_entities_yaml` | Must find `test_entities_yaml.py` reference |
| F-03 | `test_phase7_requires_contract_test_conformance` | Must find `test_conformance.py` reference |
| F-04 | `test_phase7_non_pipeline_gate_2a_preserved` | Must find `Gate 2a` for non-pipeline builds |

### Group G — `TestPhase10PipelineMod` (test_phase_pipeline_mods.py)

Validates phase-10/SKILL.md has the pipeline variant section.

| ID | Test | RED Reason |
|----|------|------------|
| G-01 | `test_phase10_has_pipeline_variant_section` | Must find `## Pipeline Variant` heading |
| G-02 | `test_phase10_airflow_dag_observability` | Must find `Airflow` and DAG-specific SLI references |
| G-03 | `test_phase10_failure_modes_crosslink` | Must find `failure-modes.md` cross-link requirement |
| G-04 | `test_phase10_deploy_gates_requirement` | Must find `Deploy gates 1-5` reference |
| G-05 | `test_pipeline_site_reliability_template_exists` | `templates/pipeline-site-reliability-additions.md` must exist |
| G-06 | `test_pipeline_site_reliability_template_airflow_section` | Template must have `## Airflow DAG Observability` |

---

## RED State Verification

All tests in groups B-D are RED because `tech_dev_agents.sdlc.canon_helpers` does not exist.

Tests in group A-03 are RED because `## Idempotency` section is missing from SKILL.md.

Tests in groups E-G test file/content existence — most should already pass after the Phase 6 SKILL.md modifications are confirmed. Any that fail are due to missing sections or incorrect section names.

---

## Phase 8 Implementation Required

To turn tests GREEN:

1. Create `tech_dev_agents/sdlc/__init__.py`
2. Create `tech_dev_agents/sdlc/canon_helpers.py` with all 5 functions
3. Create `tests/fixtures/sdlc/gc-data-v2-snapshot/platform/pipeline-standard.md` with 14 axis headings
4. Add `## Idempotency` section to `skills/load-canon/SKILL.md`
5. Confirm all SKILL.md modification sections are in place

---

## Fixture Design

### `tests/fixtures/sdlc/gc-data-v2-snapshot/platform/pipeline-standard.md`

A minimal but valid pipeline-standard.md with 14 axis headings. Used by `test_load_pipeline_standard_parses_axes`. Each axis has a `## Axis N: <name>` heading so `load_pipeline_standard()` can extract them.

Axes (in order from seed.md):
1. Repo Structure
2. Layers
3. Ingestion Patterns
4. Materialiser
5. Contracts
6. DAG Shape
7. Auth
8. Observability
9. dbt
10. CI
11. Deploy
12. Runbook
13. SLOs
14. Security

---

## Pipeline Contract Tests (SC-7)

Per the Phase 7 pipeline branch requirement, this story's own test-design also references the canonical contract tests. Since STORY-1002 is modifying the SDLC framework (not a pipeline repo itself), the contract test gate applies in spirit but not literally (no `entities.yaml` to validate in this repo).

Workaround: vendored fixture tests verify the Phase 7 instruction text is correct. The literal contract test copies are exercised in pipeline-repo stories (not in the framework repo). Documented deviation: `entity_yaml_test_skip: reason=sdlc-framework is not a pipeline repo`.
