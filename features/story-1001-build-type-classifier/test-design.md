# Test Design — STORY-1001: build_type Classifier

**Story:** STORY-1001  
**Phase:** 7 (Test Design)  
**Date:** 2026-05-18  
**State:** RED (all tests fail until Phase 8)

---

## Test Files

| File | Groups | Tests |
|---|---|---|
| `tests/sdlc/test_spec_classifier.py` | A–E | 20 tests |
| `tests/sdlc/test_phase1_canon_load.py` | A–F | 19 tests |
| `tests/sdlc/test_new_project_pipeline_scaffold.py` | A–D | 12 tests |

**Total:** 51 tests, all RED.

---

## RED Reasons

All tests fail because:
1. `deployment/hermes/build_type_classifier.py` does not exist (Phase 8 creates it)
2. `.sdlc/skills/spec/SKILL.md` has no `build_type` step
3. `.sdlc/skills/phase-1/SKILL.md` has no pipeline branch
4. `.sdlc/skills/new-project/SKILL.md` has no `--type=pipeline` parameter
5. `.sdlc/skills/spec/build-type-classifier.md` does not exist
6. `.sdlc/templates/pipeline-seed-additions.md` does not exist

---

## Fixtures Needed

| File | Purpose |
|---|---|
| `tests/fixtures/sdlc/spec-pipeline-input.txt` | Synthetic pipeline description (DAG/walmart-supplier) |
| `tests/fixtures/sdlc/spec-feature-input.txt` | Synthetic feature description (dark mode toggle) |
| `tests/fixtures/sdlc/spec-bug-input.txt` | Synthetic bug description (off-by-one) |
| `tests/fixtures/sdlc/gc-data-v2-snapshot/sources.yaml` | Pinned sources.yaml for deterministic validation |

All fixtures written to `tests/fixtures/sdlc/` — present on disk.

---

## Group Summary

### test_spec_classifier.py

**Group A — classify_build_type() logic (T01–A05c)**
- Pipeline keyword `DAG` → `pipeline`
- Pipeline keyword `Airflow` + `bronze` → `pipeline`
- No match → `feature` (default, SC-6)
- `bug`/`fix` → `bug`
- Source slug `walmart-supplier` → `pipeline`
- Fixture: pipeline input → `pipeline`
- Fixture: feature input → `feature`

**Group B — validate_source() (T06–T09)**
- Exact kebab slug passes
- Underscore normalised to kebab (OQ-2)
- Unknown source raises `ValueError` with exact message (SC-5)
- `other` passes unconditionally (OQ-3)

**Group C — resolve_gc_data_v2_commit() (T10–T12)**
- Missing path raises `RuntimeError` mentioning gc-data-v2
- Valid git repo returns 40-char lowercase hex SHA (SC-4)
- No hardcoded SHAs in source file (boundary check)

**Group D — spec/SKILL.md content (T13–D17b)**
- `build_type` keyword present
- All 5 enum values present
- `config.yaml` persistence documented
- `.project` persistence documented
- `build-type-classifier.md` helper doc exists
- Helper doc has YAML pipeline keywords (DAG, Airflow, bronze, silver)

**Group E — codex.md parity (T18–T20)**
- `build_type` in codex.md (SC-8)
- All 5 enum values in codex.md (SC-8)
- Source enum (e.g. `amazon-sp-api`) in codex.md (SC-8)

---

### test_phase1_canon_load.py

**Group A — pipeline branch presence (T01–T04)**
- `build_type` in phase-1/SKILL.md
- `pipeline-standard.md` referenced (SC-3)
- `failure-modes.md` referenced (SC-3)
- `new-pipeline-repo.md` referenced (SC-3)

**Group B — source selection (T05–T07)**
- Source prompt present
- 4+ source slugs listed
- `other` option present (OQ-3)

**Group C — SHA pinning (T08–T10)**
- `gc_data_v2_commit` mentioned (SC-4)
- `rev-parse` instruction present (SC-4)
- `.project` write documented (SC-4)

**Group D — seed sections (T11–T13)**
- `## Canon loaded` section documented (SC-3)
- `## Source` section documented (SC-3)
- Conditional gate on `build_type == pipeline` (SC-6)

**Group E — load_pipeline_canon() function (T14–T15)**
- Returns dict with `docs`, `warnings`, `commit` keys
- `docs` list has exactly 4 paths (3 platform + 1 source README) (SC-3)

**Group F — pipeline-seed-additions.md template (T16–F16b)**
- Template file exists
- Contains `## Canon loaded` and `## Source` sections

---

### test_new_project_pipeline_scaffold.py

**Group A — --type=pipeline flag (T01–T04)**
- `--type=pipeline` in new-project/SKILL.md (SC-7)
- `pipeline-template` scaffold path documented (SC-7)
- Default path preserved (regression)
- `gc-data-v2` referenced as template source (SC-7)

**Group B — CI file wiring (T05–T07)**
- `canon-drift-check` documented (SC-7)
- `pull_request_template` documented (SC-7)
- `.github` directory reference present (SC-7)

**Group C — template commit pinning (T08–T09)**
- `pipeline_template_commit` documented (SC-7)
- Dynamic SHA resolution instructed (SC-7)

**Group D — scaffold_pipeline_project() function (T10–T10b)**
- Creates `models/`, `sources/`, `tests/` directories (SC-7)
- Idempotent (safe to run twice)

---

## SC Coverage

| SC | Test(s) |
|---|---|
| SC-1 | A01, A02, D13, D14, B05–B07 (source enum) |
| SC-2 | D15, D16 |
| SC-3 | A02, A03, A04, D11, D12, E14, E15 |
| SC-4 | C08, C09, C10, C12 |
| SC-5 | B08 |
| SC-6 | A03, A07c, D13 |
| SC-7 | A01–A04, B05–B07, C08, D10 |
| SC-8 | E18, E19, E20 |

---

## Run Command

```bash
python3 -m pytest tests/sdlc/test_spec_classifier.py \
                   tests/sdlc/test_phase1_canon_load.py \
                   tests/sdlc/test_new_project_pipeline_scaffold.py \
                   -v --tb=short
```

Expected Phase 7 result: all 51 tests RED (import errors or assertion failures).
Expected Phase 8 result: all 51 tests GREEN.
