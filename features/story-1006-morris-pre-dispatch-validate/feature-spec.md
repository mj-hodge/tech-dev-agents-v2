# STORY-1006 — Feature Spec — Morris `pre-dispatch-validate` skill + API-layer seed gate

**Story ID:** STORY-1006
**Phase:** 6 (Design)
**Date:** 2026-05-18
**Status:** Locked

---

## 1. Module layout

```
tech_dev_agents/morris/pre_dispatch/
├── __init__.py            # re-exports validate_seed, validate_dispatch_seed, ValidationResult
├── models.py              # ValidationResult dataclass
├── rules.py               # REQUIRED_SECTIONS, MEDIUM_PLUS_REQUIRED, PIPELINE_REQUIRED_REFS, header aliases
├── validator.py           # validate_seed_completeness(), extract_required_sections(), helpers
└── validate_seed.py       # validate_dispatch_seed(payload) → ValidationResult (STORY-1009 callsite)
```

`__init__.py` re-exports the public API:

```python
from .models import ValidationResult, MissingPath
from .validator import validate_seed_completeness, extract_required_sections, verify_referenced_paths_exist
from .validate_seed import validate_dispatch_seed
```

## 2. ValidationResult dataclass

```python
@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    missing: list[str]
    warnings: list[str] = field(default_factory=list)
    structured: dict[str, object] = field(default_factory=dict)

    def as_error_payload(self, *, error_code: str = "seed_validation", story_id: str | None = None) -> dict:
        body = {"error": error_code, "missing": list(self.missing)}
        if story_id:
            body["story_id"] = story_id
        if self.structured:
            body["structured"] = self.structured
        return body
```

## 3. `validate_seed_completeness(seed_text, scope, build_type)` — STORY-1006 entry

Pure function. No I/O.

| Step | Action |
|---|---|
| 1 | Tokenize seed_text into `{normalized_heading → body_text}` (case-insensitive, strip leading `#`). |
| 2 | For every key in REQUIRED_SECTIONS (and any alias), confirm presence + non-empty body. Missing → append normalized key (e.g. `"problem"`, `"verification_plan"`) to `missing`. |
| 3 | If scope ∈ {medium, large, new_project}: require `Do not do` OR `Out of scope` heading non-empty. Missing → append `"do_not_do"`. |
| 4 | If scope ∈ {medium, large, new_project}: scan Verification Plan body for ≥1 occurrence of `pytest`, `curl`, `gh`, or fenced shell. None → append `"verification_plan_coverage"`. |
| 5 | If build_type == "pipeline" OR repo matches `*-v2`/`api-advertising-amazon`: require ≥1 ref to `gc-data-v2/sources/` AND ≥1 ref to `gc-data-v2/platform/`. Missing → append `"pipeline_canon_refs"`. |
| 6 | Return `ValidationResult(ok=len(missing)==0, missing=missing, ...)`. |

## 4. REQUIRED_SECTIONS (canonical key → accepted heading aliases)

| Key | Aliases (case-insensitive substring match on heading line) |
|---|---|
| `problem` | "problem" |
| `goal` | "goal" |
| `success_criteria` | "success criteria", "acceptance criteria" |
| `files_to_modify` | "files to modify", "files modified" |
| `files_to_not_modify` | "files to not modify", "files not to modify", "files to NOT modify" |
| `verification_plan` | "verification plan" |
| `boundaries` | "boundaries" |
| `done_looks_like` | "done looks like" |
| `escalation_contract` | "escalation contract", "escalation" |

## 5. `validate_dispatch_seed(payload)` — STORY-1009 entry

Hybrid: looks at the **payload** (what STORY-1009 wants) — when `seed_text` is supplied, also runs the content validator.

```python
def validate_dispatch_seed(payload: dict) -> ValidationResult:
    """Payload-level gate used by /api/dispatch/v2/enqueue and /v2/rework.

    Required payload sections (STORY-1009 contract):
      - do_not_do
      - verification_plan
      - red_test_paths
      - acceptance_criteria

    When payload["seed_text"] is set, also runs validate_seed_completeness()
    and merges the missing[] list. When payload["scope"] == "small", only
    REQUIRED_SECTIONS rules apply (Do-Not-Do not enforced).

    For the /v2/rework endpoint, callers must include `failure_list` —
    when payload["_endpoint"] == "rework", failure_list is checked as an
    additional required field.
    """
```

Missing-field semantics:
- "missing" = field absent OR value is falsy (empty string, empty list, None).
- Returns `ValidationResult(ok=..., missing=[...], structured={"payload_keys": [...]})`.

## 6. API integration points

### 6a. `dispatch_v2.py` `enqueue()` (line ~560)

Insert at the top of the handler, before any DB I/O:

```python
validation = validate_dispatch_seed({
    "story_id": req.story_id,
    "repo": req.repo,
    "scope": req.scope,
    "do_not_do": getattr(req, "do_not_do", None),
    "verification_plan": getattr(req, "verification_plan", None),
    "red_test_paths": getattr(req, "red_test_paths", None),
    "acceptance_criteria": getattr(req, "acceptance_criteria", None),
})
if not validation.ok:
    raise HTTPException(status_code=422, detail=validation.as_error_payload(story_id=req.story_id))
```

To support this, `EnqueueRequest` gains 4 optional fields (`do_not_do`, `verification_plan`, `red_test_paths`, `acceptance_criteria`). When absent, validator records them as missing → 422.

**Important:** the gate is enforced **only when feature flag `DISPATCH_V2_SEED_VALIDATION_ENABLED` is true** OR `app.state.dispatch_v2_seed_validation_enabled is True`. Default off (preserves existing tests). Tests in this story flip it on.

### 6b. `dispatch_v2.py` `POST /rework` (NEW endpoint)

```python
class ReworkRequest(BaseModel):
    original_story_id: str
    story_id: str
    repo: str
    scope: str = "small"
    failure_list: list[str]
    fresh_implementation: bool = False
    reason: str
    enqueued_by: str = "morris"
    title: str | None = None
    do_not_do: str | None = None
    verification_plan: str | None = None
    red_test_paths: list[str] | None = None
    acceptance_criteria: str | None = None


class ReworkResponse(BaseModel):
    job_id: str
    repo: str
    story_id: str
    rework_of: str
    enqueued_at: str
```

Flow:
1. Build payload dict including `_endpoint="rework"` so `failure_list` is required.
2. Run `validate_dispatch_seed(payload)`; 422 on `ok=False`.
3. Render rework prompt from `templates/rework_prompt.md.j2`.
4. Reuse existing transaction shape: idempotency check on `(repo, story_id)`, then INSERT into `dispatch_jobs` with `rework_of=original_story_id`, then enqueue event.

## 7. Rework prompt template — `tech_dev_agents/ops_console/templates/rework_prompt.md.j2`

```jinja
Rework of {{ original_story_id }} — {{ reason }}.

{% if fresh_implementation -%}
This is a FRESH implementation against current main. Do NOT reuse the old branch.
{%- endif %}

## Failures to address

{% for failure in failure_list -%}
- {{ failure }}
{% endfor %}

## COMMIT AND PUSH (REQUIRED)

git add -A
git commit -m "{{ story_id }}: rework of {{ original_story_id }}"
git push -u origin {{ story_id | lower }}/{{ story_id | lower }}
gh pr create --title "{{ story_id }}: rework of {{ original_story_id }}" --body "Rework of {{ original_story_id }}. {{ reason }}"

THIS IS NOT DONE UNTIL THE PR IS CREATED.
```

## 8. Morris skill

`deployment/vm/skills/morris/pre-dispatch-validate/SKILL.md` — described in story seed. Invokes the validator module on a seed file and exits non-zero when invalid.

## 9. Out of scope

- LLM-based seed quality scoring (deterministic-only).
- Bypass token + fingerprint TTL plumbing (deferred to follow-up; the flag pattern from STORY-898 is sufficient for this PR's tests).
- Modifying v1 dispatch routes.
