# Feature Spec — STORY-1001: build_type Classifier + Pipeline Canon Auto-Load

**Story:** STORY-1001  
**Scope:** Medium  
**Phase:** 6 (Design)  
**Date:** 2026-05-18  
**Phase path:** 1 → 6 → 7 → 8 → Done

---

## Problem (from seed)

Phase 1 is build-type-blind. `spec` reads context and jumps to Phase 1 with only a free-text feature description. `phase-1` classifies *scope* (Trivial/Small/Medium/Large) but never asks *what kind of system* is being built. Result: pipeline changes are specced without ever loading `gc-data-v2/platform/pipeline-standard.md` or per-source READMEs — the 2026-05-04 STORY-871 v2 mirror desync root cause.

---

## Design Decisions (resolving Phase 6 open questions)

### OQ-1: Heuristic confidence threshold
**Decision:** Always prompt the user to confirm `build_type` — even when confidence is high. The heuristic pre-fills the suggestion, but one keypress confirms. Rationale: prompt fatigue is low (one extra question); silent auto-selection causes harder-to-find misclassification bugs.

### OQ-2: Source slug normalisation
**Decision:** Normalise before validation — lowercase, replace `_` and spaces with `-`. So `amazon_sp_api` and `Amazon SP API` both match `amazon-sp-api`. Rationale: reduces friction for human typists; canonical form stored in YAML is kebab-case regardless.

### OQ-3: `other` slug downstream behaviour
**Decision:** When source is `other`, omit the per-source README from the canon list and emit `seed_warning: source_other`. The `## Source` section in seed.md records `slug: other` with no `repo:` or `auth_method:` fields. Flag for canon-backport (STORY-1003).

### OQ-4: Multi-source stories
**Decision:** `.project` and `seed.md` accept source as a YAML list when multiple sources are given. The `## Source` section enumerates each. Each source README is loaded separately (or flagged missing individually).

### OQ-5: Config.yaml schema migration
**Decision:** Absent `build_type:` key treated as `unknown` downstream (back-compat). Only `pipeline` triggers the new canon-loading behaviour. No migration required for existing `config.yaml` files.

---

## Classifier Specification

### 1. build_type Enum

```
pipeline | feature | bug | ops | infra
```

Default (no heuristic match): `feature`.

### 2. Heuristic Keywords (stored in `build-type-classifier.md`, not hardcoded)

| build_type | Keywords |
|---|---|
| pipeline | pipeline, DAG, Airflow, bronze, silver, gold, ingest, dbt, ETL, ELT, warehouse, medallion |
| pipeline (source slugs) | amazon-sp-api, amazon-ads-api, walmart-supplier, walmart-ad-connect, shopify, levanta, toolio, netsuite |
| bug | bug, fix, regression, broken, error, crash, failure, off-by-one, typo |
| infra | infra, terraform, kubernetes, k8s, helm, docker, container, deploy, CI, CD |
| ops | ops, alert, monitor, dashboard, runbook, oncall, SLO, SLA, incident |

Match precedence when multiple types match: `pipeline > bug > infra > ops > feature`.

Matching is case-insensitive, whole-word-preferred (substring acceptable for compound words like "Airflow").

### 3. Prompt Format

```
build_type? [pipeline|feature|bug|ops|infra] (suggested: pipeline based on 'DAG')
```

User can type the full value or the first unambiguous prefix (e.g. `p` → `pipeline` when no other value starts with `p`). Unrecognised input retries once, then falls back to `feature` with a warning.

### 4. Source Selection Sub-Prompt (pipeline only)

```
Which source? [amazon-sp-api|amazon-ads-api|walmart-supplier|walmart-ad-connect|shopify|levanta|toolio|netsuite|other]
```

Input normalisation: lowercase, `_`/space → `-`.

Validation: check against `gc-data-v2/sources.yaml` (if reachable). If `sources.yaml` is absent or unreadable, fall back to the hardcoded 8-slug enum + `other`. Log a warning.

Error on unknown source:
```
Unknown source 'foobar' — not in gc-data-v2/sources.yaml. Use 'other' or add the source first.
```

Retry once. On second failure, abort with the same message and exit non-zero.

---

## Persistence Contract

### config.yaml (project-scoped)

Written at the **top level** of `config.yaml`, alongside existing `project:` and `council:` keys:

```yaml
build_type: pipeline   # pipeline | feature | bug | ops | infra
```

For pipeline builds, also write:
```yaml
pipeline:
  source: walmart-supplier    # or list: [walmart-supplier, walmart-ad-connect]
```

### .project (story-scoped, Phase Routing block)

Added under the story's Phase Routing entry:
```
| build_type | pipeline |
| source | walmart-supplier |
| gc_data_v2_commit | a7c1bf2e0d9f4c1a8e2d5f6a3b9c0d1e4f5a6b8e9d |
```

---

## Canon Loading (pipeline only, Phase 1)

When `build_type == pipeline`, after source selection, before writing `seed.md`:

1. **Resolve SHA:** `git -C /mnt/c/Projects/gc-data-v2 rev-parse HEAD` (or `~/test_projects/gc-data-v2` on Linux). If path absent, emit `needs_info` and abort (Escalation #1).

2. **Canon reading list** (loaded into Phase 1 context):
   - `gc-data-v2/platform/pipeline-standard.md`
   - `gc-data-v2/platform/failure-modes.md`
   - `gc-data-v2/platform/new-pipeline-repo.md`
   - `gc-data-v2/sources/{source}/README.md` (if present; else `seed_warning: missing_source_readme`)

3. **Emit to seed.md** (REQUIRED when pipeline):

```markdown
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

4. **Non-pipeline path:** No canon loading, no `## Canon loaded` section, no `## Source` section. Byte-identical to today's behaviour for `build_type != pipeline`.

---

## new-project Pipeline Scaffold

When `/new-project --type=pipeline <name>`:

1. Scaffold from `gc-data-v2/pipeline-template/` instead of generic `templates/readme.md`.
2. Copy verbatim:
   - `gc-data-v2/pipeline-template/.github/workflows/canon-drift-check.yml`
   - `gc-data-v2/pipeline-template/.github/pull_request_template.md`
3. Write to new repo's `config.yaml`:
   ```yaml
   pipeline_template_commit: <40-char SHA of gc-data-v2 at scaffold time>
   ```
4. Output structure includes `models/`, `sources/`, `tests/` directories from the template.

When `--type` is absent or not `pipeline`, behaviour is unchanged (generic scaffold).

---

## Python Module: `build_type_classifier.py`

Location: `deployment/hermes/build_type_classifier.py`

### API

```python
def classify_build_type(text: str) -> str:
    """Classify build type from free-text description.

    Args:
        text: Feature description text.

    Returns:
        One of: 'pipeline', 'feature', 'bug', 'ops', 'infra'.
        Default: 'feature'.
    """

def validate_source(source_input: str, sources_yaml_path: str | None = None) -> str:
    """Normalise and validate a source slug.

    Args:
        source_input: Raw user input (e.g. 'amazon_sp_api', 'Walmart Supplier').
        sources_yaml_path: Optional path to gc-data-v2/sources.yaml for validation.

    Returns:
        Normalised kebab-case slug (e.g. 'amazon-sp-api').

    Raises:
        ValueError: If slug not in known sources and not 'other'.
                    Message: "Unknown source '{slug}' — not in gc-data-v2/sources.yaml.
                              Use 'other' or add the source first."
    """

def resolve_gc_data_v2_commit(repo_paths: list[str] | None = None) -> str:
    """Resolve HEAD SHA of gc-data-v2 checkout.

    Args:
        repo_paths: Candidate paths to check (default: platform-specific defaults).

    Returns:
        40-char hex SHA string.

    Raises:
        RuntimeError: If gc-data-v2 not found at any candidate path.
    """

def load_pipeline_canon(gc_data_v2_root: str, source_slug: str) -> dict:
    """Return canon reading list for a pipeline build.

    Args:
        gc_data_v2_root: Absolute path to gc-data-v2 checkout.
        source_slug: Normalised source slug (e.g. 'walmart-supplier').

    Returns:
        dict with keys:
            'docs': list of file paths (relative to gc_data_v2_root)
            'source_readme': str | None (None if missing)
            'warnings': list of warning strings
            'commit': 40-char SHA
    """
```

### Constants (defined in module, not hardcoded in SKILL.md)

```python
PIPELINE_KEYWORDS = [
    "pipeline", "dag", "airflow", "bronze", "silver", "gold",
    "ingest", "dbt", "etl", "elt", "warehouse", "medallion",
    "amazon-sp-api", "amazon-ads-api", "walmart-supplier",
    "walmart-ad-connect", "shopify", "levanta", "toolio", "netsuite",
]

BUG_KEYWORDS = ["bug", "fix", "regression", "broken", "error", "crash",
                 "failure", "off-by-one", "typo"]

INFRA_KEYWORDS = ["infra", "terraform", "kubernetes", "k8s", "helm",
                  "docker", "container", "deploy", "ci", "cd"]

OPS_KEYWORDS = ["ops", "alert", "monitor", "dashboard", "runbook",
                "oncall", "slo", "sla", "incident"]

KNOWN_SOURCES = [
    "amazon-sp-api", "amazon-ads-api", "walmart-supplier",
    "walmart-ad-connect", "shopify", "levanta", "toolio", "netsuite",
]

GC_DATA_V2_PATHS = [
    "/mnt/c/Projects/gc-data-v2",
    "~/test_projects/gc-data-v2",
    "/opt/gc-data-v2",
]

CANON_PLATFORM_DOCS = [
    "platform/pipeline-standard.md",
    "platform/failure-modes.md",
    "platform/new-pipeline-repo.md",
]
```

---

## Files Modified/Created

### Modified (`.sdlc/` submodule)

| File | Change |
|---|---|
| `skills/spec/SKILL.md` | Add step 1.5 — build_type classifier between context-read and Phase 1 activation |
| `skills/spec/codex.md` | Add classifier flow to Codex execution rules (SC-8 parity) |
| `skills/phase-1/SKILL.md` | Add pipeline branch in step 4: canon loading, source prompt, SHA pin |
| `skills/new-project/SKILL.md` | Add `--type=pipeline` parameter and pipeline scaffold path |
| `software-development-guidance.md` | Add `build_type` documentation section |
| `templates/seed.md` | Add `## Canon loaded` + `## Source` sections (conditional pipeline) |

### Created (`.sdlc/` submodule)

| File | Purpose |
|---|---|
| `skills/spec/build-type-classifier.md` | Helper doc: heuristic keyword list (YAML), 5-value enum, source-selection sub-prompt. Data-driven so retro can update without touching SKILL.md. |
| `templates/pipeline-seed-additions.md` | Canonical text for `## Canon loaded` and `## Source` sections |

### Created (tech-dev-agents)

| File | Purpose |
|---|---|
| `deployment/hermes/build_type_classifier.py` | Python module implementing classify_build_type, validate_source, resolve_gc_data_v2_commit, load_pipeline_canon |
| `tests/sdlc/test_spec_classifier.py` | Phase 7 RED tests — classifier logic + spec SKILL.md content |
| `tests/sdlc/test_phase1_canon_load.py` | Phase 7 RED tests — phase-1 SKILL.md pipeline branch |
| `tests/sdlc/test_new_project_pipeline_scaffold.py` | Phase 7 RED tests — new-project SKILL.md pipeline path |
| `tests/fixtures/sdlc/` | Test fixtures for deterministic testing |

---

## Acceptance Criteria (keyed to seed SC-1..SC-8)

| SC | Spec decision |
|---|---|
| SC-1 | `spec/SKILL.md` has step 1.5 with `build_type?` prompt and 5-value enum |
| SC-2 | Persistence written to both `config.yaml` (top-level) and `.project` (story routing) |
| SC-3 | `phase-1/SKILL.md` pipeline branch lists all 4 canon paths in seed |
| SC-4 | `gc_data_v2_commit` resolved via `git rev-parse HEAD`; never hardcoded |
| SC-5 | `validate_source()` raises with exact error message for unknown input |
| SC-6 | Non-pipeline path: no `## Canon loaded`, no `## Source`, byte-identical flow |
| SC-7 | `new-project/SKILL.md` `--type=pipeline` scaffold from pipeline-template |
| SC-8 | `codex.md` documents same enum, same persistence, same source sub-prompt |

---

## Acceptance Diff

The PR for this story MUST include changes to these files. Phase 8 will
fail if any are missing from `git diff origin/main --name-only`:

- `.sdlc/skills/spec/SKILL.md` must-contain `build_type` must-contain `pipeline|feature|bug|ops|infra` — classifier step added
- `.sdlc/skills/spec/codex.md` must-contain `build_type` — Codex parity
- `.sdlc/skills/phase-1/SKILL.md` must-contain `build_type` must-contain `gc_data_v2_commit` — pipeline canon branch
- `.sdlc/skills/new-project/SKILL.md` must-contain `--type=pipeline` — pipeline scaffold path
- `.sdlc/skills/spec/build-type-classifier.md` — new helper doc
- `.sdlc/templates/pipeline-seed-additions.md` — new template
- `deployment/hermes/build_type_classifier.py` must-contain `classify_build_type` — classifier module
- `tests/sdlc/test_spec_classifier.py` — RED→GREEN tests
- `tests/sdlc/test_phase1_canon_load.py` — RED→GREEN tests
- `tests/sdlc/test_new_project_pipeline_scaffold.py` — RED→GREEN tests

---

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Heuristic over-classifies | Always prompt; user can override; log overrides |
| gc-data-v2 not present | Detect early; emit needs_info with clone command |
| sources.yaml unreadable | Fall back to hardcoded KNOWN_SOURCES enum + warning |
| Downstream crash on new build_type key | Absent key = 'unknown' (back-compat); only 'pipeline' triggers new behaviour |
