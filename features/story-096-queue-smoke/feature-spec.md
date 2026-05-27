# STORY-096: Queue Smoke Tests — Feature Spec

## Phase 6 Design (Medium)

### Overview

STORY-096 adds `@pytest.mark.smoke`-annotated tests for the dispatch queue subsystem so the
pre-deploy gate (`08_smoke_dry_run.sh`) catches queue regressions before production deployments.
The story is **Medium scope** because smoke-testing the dispatch API via `DispatchFallbackService`
requires minimal service additions that were missing from that class.

---

## Production Changes

### `tech_dev_agents/ops_console/services/dispatch_service.py` — `DispatchFallbackService`

Three additions are required to make the smoke tests runnable against the JSON-fallback backend:

#### 1. `has_active_claim(agent_name: str) → bool`

The dispatch route calls `db_svc.has_active_claim(agent_name)` as an agent guard before
accepting a new claim. `DispatchFallbackService` was missing this method entirely, causing
an `AttributeError` when the route executed under the JSON-fallback backend.

```python
async def has_active_claim(self, agent_name: str) -> bool:
    data = self._json_svc.load()
    return any(
        item.get("claimed_by") == agent_name
        for item in data["claimed"]
    )
```

**Design note:** The JSON backend stores claimed items in `data["claimed"]`; the method
iterates that list and checks `claimed_by`. No DB query needed.

#### 2. `list_queue()` — dual-key response

The route constructs its response from `db_svc.list_queue()["in_progress"]`. The original
fallback implementation only returned `{"pending": ..., "claimed": ...}`, omitting the
`in_progress` key. The fix returns both keys (alias pattern):

```python
async def list_queue(self) -> dict[str, list[dict[str, Any]]]:
    data = self._json_svc.load()
    claimed = data["claimed"]
    return {"pending": data["pending"], "in_progress": claimed, "claimed": claimed}
```

**Backward compat:** `claimed` is retained for older consumers (STORY-496 rename policy).

#### 3. `claim()` — `repo` keyword argument with NotImplementedError guard

`DispatchDBService.claim()` accepts `repo: str | None = None` for multi-repo disambiguation
(STORY-531). `DispatchFallbackService.claim()` must carry the same signature to satisfy the
duck-typing contract used by the route layer.

The JSON fallback has no repo column and cannot perform repo-filtered claim matching.
Silently ignoring `repo` would produce misleading results in multi-repo environments, so
the method raises `NotImplementedError` when a non-None repo is supplied:

```python
async def claim(self, story_id: str, agent_name: str, *, repo: str | None = None) -> dict[str, Any]:
    if repo is not None:
        raise NotImplementedError(
            "DispatchFallbackService does not support repo-filtered claim. "
            "Pass repo=None or use DispatchDBService for repo-aware routing."
        )
    ...
```

**Design decision:** `NotImplementedError` over silent ignore. Explicit failure prevents
subtle correctness bugs in multi-repo deployments that happen to be using the JSON fallback.

---

## Test Design Summary

See `test-design.md` for full test cases. Summary:

| Test | Target | SC |
|------|--------|----|
| T01: WorkQueue round-trip | `scripts/work_queue.py` | SC-1 |
| T02: Dispatch API lifecycle (enqueue → list → claim) | `routes/dispatch.py` + `DispatchFallbackService` | SC-2 |
| T03: Stale entry detection | `scripts/work_queue.py` stale logic | SC-3 |
| T04a: Side-task rejection | `work_queue.enqueue()` filter | SC-4 |
| T04b: Normal story acceptance | `work_queue.enqueue()` filter | SC-4 |

**SC-2 scope boundary:** POST `/dispatch/complete` requires a `commit_sha`
proof-of-work via GitHub API (STORY-253) and cannot run in an isolated smoke context.
SC-2 ends at claim. This boundary is documented in `seed.md` and `test-design.md`.

---

## Non-Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| DB dependency | JSON fallback only | Smoke tests must run without PostgreSQL |
| `repo` guard | `NotImplementedError` | Explicit failure > silent wrong behaviour |
| `complete` in SC-2 | Out of scope | Needs GitHub API — breaks smoke isolation |
| Scope reclassification | Small → Medium | Production service additions require Phase 6 artefact |
