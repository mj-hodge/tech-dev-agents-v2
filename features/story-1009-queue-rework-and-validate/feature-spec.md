# STORY-1009 — Feature Spec — `validate_dispatch_seed()` gate + `/v2/rework` endpoint

**Story ID:** STORY-1009
**Phase:** 6 (Design)
**Date:** 2026-05-18
**Status:** Locked

---

## 1. Validator import contract

STORY-1006 owns `tech_dev_agents/morris/pre_dispatch/`. STORY-1009 imports:

```python
from tech_dev_agents.morris.pre_dispatch import validate_dispatch_seed, ValidationResult
```

`validate_dispatch_seed(payload: dict) -> ValidationResult` checks the payload for the four canonical required sections:

- `do_not_do`
- `verification_plan`
- `red_test_paths`
- `acceptance_criteria`

A "missing" field is one that is absent **or** has a falsy value (empty string, empty list, None). When called with `payload["_endpoint"] == "rework"`, `failure_list` is added to the requirement set.

## 2. Wiring on `POST /api/dispatch/v2/enqueue`

Add the seed-validation hop before the idempotency check (existing line 587). Behind feature flag `DISPATCH_V2_SEED_VALIDATION_ENABLED` (default off — same pattern as STORY-898's id-reuse gate). When the flag is on:

```python
if _get_seed_validation_gate(request):
    validation = validate_dispatch_seed({
        "story_id": req.story_id,
        "repo": normalized_repo,
        "scope": req.scope,
        "do_not_do": req.do_not_do,
        "verification_plan": req.verification_plan,
        "red_test_paths": req.red_test_paths,
        "acceptance_criteria": req.acceptance_criteria,
    })
    if not validation.ok:
        raise HTTPException(
            status_code=422,
            detail=validation.as_error_payload(story_id=req.story_id),
        )
```

`EnqueueRequest` gains 4 optional fields (default `None` / `[]`) so the existing call-sites (and STORY-898 tests with `id_reuse_gate=True`/seed gate=False) continue to pass.

## 3. NEW endpoint — `POST /api/dispatch/v2/rework`

### Request

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
```

### Response (200)

```json
{ "job_id": "<uuid>", "repo": "tech-dev-agents", "story_id": "STORY-803", "rework_of": "STORY-802", "enqueued_at": "2026-05-18T..." }
```

### Status codes

| Code | When |
|---|---|
| 200 | Created — or idempotent re-POST returns existing rework row |
| 409 | Active rework row exists for (repo, story_id) — return existing |
| 422 | Validation failure (missing required section or empty `failure_list`) |
| 503 | DB pool unavailable |

### Behavior

1. Build full payload dict + `_endpoint="rework"`; run `validate_dispatch_seed`.
2. Render rework prompt from Jinja2 template (Section 4).
3. Idempotency: if (repo, story_id) has an active row, return it.
4. INSERT `dispatch_jobs` row with:
   - `repo`, `story_id`, `scope`, `prompt` (rendered), `enqueued_by`, `title`, `target_role="developer"`, `rework_of=original_story_id`.
5. INSERT `dispatch_v2_events` row `event_type='enqueued'` with `event_data={"source": "v2_rework_endpoint", "original_story_id": ...}`.

The gate is **always on** for `/v2/rework` — there is no flag to disable it; the entire purpose of the endpoint is to enforce the gate.

## 4. Prompt template — `tech_dev_agents/ops_console/templates/rework_prompt.md.j2`

(See STORY-1006 feature-spec § 7 for the Jinja2 source — owned by STORY-1006 module layout, consumed here.)

Module loader:

```python
from pathlib import Path
from jinja2 import Template

_TEMPLATE_PATH = Path(__file__).parent.parent / "templates" / "rework_prompt.md.j2"

def render_rework_prompt(context: dict) -> str:
    return Template(_TEMPLATE_PATH.read_text()).render(**context)
```

## 5. Replay matrix (REPLAY-2a/2b/2c)

| Replay | Original story | Rework script (deleted) | Rework story_id | failure_list |
|---|---|---|---|---|
| 2a | STORY-738 (PR #227) | `dispatch_804.py` | STORY-804 | 1 item: partial-gate documentation |
| 2b | STORY-766 (PR #244) | `dispatch_795.py` | STORY-795 | 6 items (--no-merges, DM, test fix, first-run, ordering, API key) |
| 2c | STORY-802 (PR #256) | `dispatch_803.py` | STORY-803 | 3 items (contract test rename, LokiClient method, PR body) |

**Acceptance test shape (per replay):**

```python
def test_replay_2X_manual_surgery_NNN_blocked(client):
    # Reconstruct the original seed missing do_not_do + verification_plan
    bad_payload = {"story_id": "STORY-NNN", "repo": "tech-dev-agents", "scope": "medium", "prompt": "..."}
    resp = client.post("/api/dispatch/v2/enqueue", json=bad_payload, headers=...)
    assert resp.status_code == 422
    body = resp.json()
    assert "do_not_do" in body["detail"]["missing"]
    assert "verification_plan" in body["detail"]["missing"]
```

## 6. Migration: delete `dispatch_795.py`, `dispatch_803.py`, `dispatch_804.py`

Order of operations in Phase 8:
1. Land validator module + rework endpoint behind feature flag (default off).
2. Replay tests GREEN with flag on.
3. **Delete the three scripts** as part of the same PR (they have no callers; their job is now `POST /v2/rework`).
4. Add a sentence in PR description recording the lineage so the *why* is grep-able.

## 7. Test plan

- `tests/ops_console/test_dispatch_rework.py` — new file:
  - `test_enqueue_rejects_missing_do_not_do`
  - `test_enqueue_rejects_missing_verification_plan`
  - `test_enqueue_rejects_missing_red_test_paths`
  - `test_enqueue_passes_with_complete_seed`
  - `test_rework_rejects_empty_failure_list`
  - `test_rework_creates_job_with_correct_lineage`
  - `test_rework_idempotent_on_duplicate_story_id`
  - `test_replay_2a_manual_surgery_738_blocked`
  - `test_replay_2b_manual_surgery_766_blocked`
  - `test_replay_2c_manual_surgery_802_blocked`

Tests use the mock-pool pattern from `test_dispatch_v2_enqueue_id_reuse_gate.py`.

## 8. Out of scope

- v1 `/api/dispatch` gate (frozen).
- `dispatch_jobs` / `dispatch_v2_events` schema changes.
- STORY-898 id-reuse gate (orthogonal — must continue to work alongside this gate).
- Generating the rework PR (the agent that picks up the rework job creates it).
