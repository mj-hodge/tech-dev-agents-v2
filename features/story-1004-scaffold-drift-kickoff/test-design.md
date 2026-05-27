# STORY-1004 — Test Design (Phase 7)

**Scope:** small  
**Phase path:** 1 → 7 → 8 → Done  
**Test file:** `tests/skills/test_1004_scaffold_drift_kickoff.py`

## Test groups

### Group A — scaffold-drift-check SKILL.md (5 tests)

| # | Test | RED reason | GREEN after |
|---|------|-----------|-------------|
| A1 | `test_scaffold_drift_check_skill_exists` | File does not exist | Phase 8 creates `skills/scaffold-drift-check/SKILL.md` |
| A2 | `test_scaffold_drift_check_has_valid_frontmatter` | File does not exist | Frontmatter contains `name: scaffold-drift-check` and `description:` |
| A3 | `test_scaffold_drift_check_has_steps_section` | File does not exist | Body contains `## Steps` section |
| A4 | `test_scaffold_drift_check_references_canonical_files` | File does not exist | Body mentions CLAUDE.md, AGENTS.md, config.yaml |
| A5 | `test_scaffold_drift_check_has_drift_reporting` | File does not exist | Body contains PASS/FAIL/FIX reporting language |

### Group B — pipeline-kickoff SKILL.md (5 tests)

| # | Test | RED reason | GREEN after |
|---|------|-----------|-------------|
| B1 | `test_pipeline_kickoff_skill_exists` | File does not exist | Phase 8 creates `skills/pipeline-kickoff/SKILL.md` |
| B2 | `test_pipeline_kickoff_has_valid_frontmatter` | File does not exist | Frontmatter contains `name: pipeline-kickoff` and `description:` |
| B3 | `test_pipeline_kickoff_has_steps_section` | File does not exist | Body contains `## Steps` section |
| B4 | `test_pipeline_kickoff_validates_sources_yaml` | File does not exist | Body references `sources.yaml` or `data-sources.yaml` validation |
| B5 | `test_pipeline_kickoff_wraps_prompt_template` | File does not exist | Body references `pipeline-kickoff-prompt.md` |

### Group C — pipeline-kickoff-prompt.md (2 tests)

| # | Test | RED reason | GREEN after |
|---|------|-----------|-------------|
| C1 | `test_pipeline_kickoff_prompt_exists` | File does not exist | Phase 8 creates `pipeline-kickoff-prompt.md` |
| C2 | `test_pipeline_kickoff_prompt_has_required_sections` | File does not exist | File contains source system, target tables, schedule, latency sections |

### Group D — MANUAL-STEPS.md (4 tests)

| # | Test | RED reason | GREEN after |
|---|------|-----------|-------------|
| D1 | `test_manual_steps_exists` | File does not exist | Phase 8 creates `MANUAL-STEPS.md` |
| D2 | `test_manual_steps_has_copy_instructions` | File does not exist | Contains `cp` or `copy` + `.sdlc/skills` |
| D3 | `test_manual_steps_has_git_submodule_reference` | File does not exist | Contains `git -C .sdlc` |
| D4 | `test_manual_steps_has_numbered_steps` | File does not exist | At least 4 numbered steps |

## Acceptance criteria mapping

| AC | Description | Tests |
|----|-------------|-------|
| AC-1 | scaffold-drift-check skill file is well-formed | A1–A5 |
| AC-2 | pipeline-kickoff skill file is well-formed and validates source catalog | B1–B5 |
| AC-3 | pipeline-kickoff-prompt.md template exists with required sections | C1–C2 |
| AC-4 | MANUAL-STEPS.md has numbered instructions for Mark | D1–D4 |

## RED state confirmation

Before Phase 8 implementation, the test suite produces:

- 16 tests collected
- All tests FAIL or SKIP (files not yet created)
- `test_scaffold_drift_check_skill_exists`, `test_pipeline_kickoff_skill_exists`,
  `test_pipeline_kickoff_prompt_exists`, `test_manual_steps_exists` → **FAIL** (assert .exists())
- Remaining 12 tests → **SKIP** (guarded by `pytest.skip` when prerequisite file missing)

## GREEN state (post-Phase-8)

All 16 tests PASS after Phase 8 creates the skill files, prompt template, and MANUAL-STEPS.md.
No runtime code is exercised — all tests are content assertions on markdown files.
