# STORY-523 — Seed

## Story
**Title:** Dispatch enqueue: reject prompts whose `STORY-N` reference disagrees with `story_id`

**Type:** Bug / Hardening (dispatch API input validation)
**Scope:** Small (single endpoint, single validator, ≤4 new unit tests + 1 regression fixture)
**Repo:** tech-dev-agents
**Target file:** `tech_dev_agents/ops_console/routes/dispatch.py` (`enqueue_story`)
**Request model:** `DispatchRequest` in `tech_dev_agents/ops_console/models/responses.py`

## Problem

`/api/dispatch` (POST `enqueue_story`) currently accepts any `DispatchRequest` whose fields
parse individually, including **self-contradictory** payloads where the free-text `prompt`
references a different `STORY-N` than the structured `story_id` field.

When this happens, the agent that claims the story wastes a full Phase-1 SDK turn before
it can refuse. The cost per occurrence is roughly one claim round-trip + one Opus/Sonnet
phase-1 session + a `QUESTION.md` hand-back + a re-dispatch.

### 2026-04-22 evidence

| Dispatch | `story_id` field | Prompt referenced | Outcome |
|----------|------------------|-------------------|---------|
| STORY-518 | `STORY-518` | `STORY-267` work | Agent refused, wrote `QUESTION.md`, phase-1 SDK turn burned |
| STORY-520 | `STORY-520` | `STORY-517` work | Agent refused, wrote `QUESTION.md`, phase-1 SDK turn burned |

Both were caught downstream by the agents' own sanity checks, but only AFTER the
claim + phase-1 session had already been charged. The dispatch API should have
rejected both at enqueue time.

## Desired behavior

`POST /api/dispatch` validates that every `STORY-\d+` token found in `prompt` matches the
`story_id` field. On mismatch:

1. **Default:** return **HTTP 422** with a clear error message naming the mismatching IDs.
2. **Opt-in override:** accept a new boolean flag on `DispatchRequest` —
   `cross_story_reference: bool = False`. When `true`, the request is accepted
   (HTTP 201) and a `WARNING`-level log line is emitted recording the cross-reference
   so audit can surface intentional cross-story prompts.

No change to any other dispatch endpoint. No DB schema change.

## Acceptance criteria (MANDATORY — carried forward to Phase 7)

1. **AC-1 — Reject mismatched:** `POST /api/dispatch` with `story_id=STORY-500` and a
   prompt containing `STORY-267 work` returns **422** with a body naming both IDs.
2. **AC-2 — Accept matched:** `POST /api/dispatch` with matching `story_id` and prompt
   returns **201** (no behavior regression vs. today).
3. **AC-3 — Opt-in override:** `POST /api/dispatch` with `cross_story_reference=true`
   AND contradictory IDs returns **201**, and a WARNING-level log entry is emitted
   naming the mismatch.
4. **AC-4 — Regression fixtures:** The exact STORY-518 and STORY-520 prompt/story_id
   pairs from 2026-04-22 are stored as test fixtures and must each return **422**.
5. **E2E — Post-deploy:** Re-enqueueing the STORY-518 and STORY-520 payloads against
   the deployed ops-console must each return **422**. No SDK session is started.

### Non-goals

- Cross-field validation for fields other than `prompt`/`story_id`.
- Semantic understanding of the prompt (e.g., detecting "port STORY-X to STORY-Y"
  phrasing). The validator is strict regex/structured: any `STORY-\d+` in `prompt`
  that does not equal `story_id` triggers the gate.
- Retroactive cleanup of the existing queue — this only affects future enqueues.
- Any change to `/dispatch/claim`, `/dispatch/complete`, or the poller.

## Scope classification

**Small.** One endpoint, one request model, one validator function, ≤20 LOC of logic.
Path: `1 → 7 → 8 → Done`.

## Deliverables (MANDATORY from dispatch context)

- **Phase 1 (this file):** `features/story-523/seed.md` — problem, examples, AC ✅
- **Phase 7:** `features/story-523/test-design.md` — 4 unit tests + 1 regression fixture
  with pytest skeletons (BEFORE implementation)
- **Phase 8:** Validator implementation in `enqueue_story`, all tests GREEN, PR created
- **Post-deploy E2E:** Re-play STORY-518 + STORY-520 payloads against production — both 422

## Design sketch (for Phase 7/8 — NOT implementation)

Proposed validator, invoked inside `enqueue_story` before `db_svc.enqueue(...)`:

```python
_STORY_REF_RE = re.compile(r"\bSTORY-(\d+)\b", re.IGNORECASE)

def _prompt_references_other_story(prompt: str, story_id: str) -> list[str]:
    """Return a sorted list of STORY-N tokens in `prompt` that differ from story_id."""
    canonical = story_id.upper()
    found = {f"STORY-{m.group(1)}" for m in _STORY_REF_RE.finditer(prompt)}
    return sorted(ref for ref in found if ref.upper() != canonical)
```

In the endpoint:

```python
mismatches = _prompt_references_other_story(body.prompt, body.story_id)
if mismatches and not body.cross_story_reference:
    raise HTTPException(
        422,
        f"prompt references {mismatches} but story_id is {body.story_id}. "
        f"Set cross_story_reference=true to accept intentionally.",
    )
if mismatches and body.cross_story_reference:
    logger.warning(
        "dispatch_cross_story_reference story_id=%s refs=%s enqueued_by=%s",
        body.story_id, mismatches, body.enqueued_by,
    )
```

Model change (in `DispatchRequest`):

```python
cross_story_reference: bool = Field(
    False,
    description="Opt-in flag — allow prompts that reference a different STORY-N "
                "than story_id. Without this, such payloads are rejected at enqueue "
                "time to prevent phase-1 SDK waste (2026-04-22 incident).",
)
```

## Risks / open questions

1. **False positives for paragraph-style prompts** that legitimately mention prior
   related stories (e.g., "After STORY-507 merged, we now need to …"). Mitigated by
   the opt-in flag. The default should still be strict — the cost of a 422 is low;
   the cost of a wasted SDK turn is high.
2. **Case sensitivity:** The regex uses `IGNORECASE`; comparison canonicalises to
   upper-case before equality. No edge case around `story-518` vs `STORY-518`.
3. **Leading zeros / length:** `STORY-\d+` accepts any digit run. Consistent with
   the existing `story_id` pattern `^STORY-\d+$` on `DispatchRequest`.
4. **Error body shape:** FastAPI `HTTPException(422, detail=...)` — matches the
   existing queue-full 422 at line 136 of `dispatch.py`. No new error schema.

## Follow-ups (OUT OF SCOPE — log, do not execute)

- Consider a similar guard on `prompt.scope` vs. the scope claimed in prose
  (e.g., prompt says "Large" but `scope="small"`). Defer — different failure
  mode, different incidents required to justify.
- Consider a daily cron that scans the queue for cross-story references to
  audit intentional opt-ins. Defer.

## Next phase

**Phase 7 — Test Design.** Small scope path: `1 → 7 → 8 → Done`.
