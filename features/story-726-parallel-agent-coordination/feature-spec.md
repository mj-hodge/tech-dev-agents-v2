# Feature Spec — STORY-726: Parallel Agent Coordination

## 1. Overview

Three in-band fixes to reduce coordination overhead in the 4-agent dispatch fleet:

| Gap | Problem | Mitigation | Feature Flag |
|---|---|---|---|
| Gap 1 | No back-off on 409 — thundering herd on burst | Jitter sleep before retry | `CLAIM_BACKOFF=1` (default on) |
| Gap 2 | `/next` returns same row to all agents — no scope routing | `preferred_scope` query param + SQL soft-preference | `SCOPE_AFFINITY=1` (default on) |
| Gap 3 | `_LOCALLY_COMPLETED` lost on restart — re-claim window | Persist recent completions to JSON file | always on |

No DB schema changes. No new endpoints. All changes are backward-compatible: the server handles the new `preferred_scope` param optionally; the poller falls back gracefully when the file is missing.

---

## 2. Gap 1 — Back-off Jitter on 409

### Problem

`poll_once` retries immediately on 409 (`continue` at line 1558 of `dispatch_poller.py`). With 4 agents polling simultaneously, the 09:00 burst produces a thundering herd: all three losers immediately re-fetch `/next`, see the same next row, and generate 2 more 409s. Net: 3 agents make ~2.5× the API calls needed to claim 2 stories.

### Specification

**New constant** (after `MAX_RETRIES = 3` at line 1383):
```python
_CLAIM_BACKOFF = os.environ.get("CLAIM_BACKOFF", "1") == "1"
_CLAIM_BACKOFF_BASE = float(os.environ.get("CLAIM_BACKOFF_BASE", "1.5"))
```

**Change** at line 1556-1558 (the 409 branch in `poll_once`):

Before (current):
```python
        if claim_resp.status_code == 409:
            print(f"[DISPATCH] {story_id} already claimed, retrying...", flush=True)
            continue
```

After:
```python
        if claim_resp.status_code == 409:
            if _CLAIM_BACKOFF and attempt < MAX_RETRIES - 1:
                delay = random.uniform(0.5, _CLAIM_BACKOFF_BASE) * (attempt + 1)
                print(
                    f"[DISPATCH] {story_id} already claimed — back-off {delay:.1f}s "
                    f"(attempt {attempt + 1}/{MAX_RETRIES})",
                    flush=True,
                )
                time.sleep(delay)
            else:
                print(f"[DISPATCH] {story_id} already claimed, retrying...", flush=True)
            continue
```

**Import**: `import random` already present in stdlib; confirm it's at the top of `dispatch_poller.py` (add if missing).

**Effect**: On a 4-agent burst, agents 2/3/4 each sleep a random 0.5–1.5s / 1.0–3.0s / 1.5–4.5s before their next retry. The fleet's retry calls spread over ~5s instead of < 100ms, eliminating the thundering herd.

### Rollback

Set `CLAIM_BACKOFF=0` in the agent's environment and restart `dispatch-poller`. No data changes.

---

## 3. Gap 2 — Scope-Aware Routing

### Problem

`GET /api/dispatch/next` returns the oldest pending item regardless of which agent is calling. An idle Daisy (best at React/frontend) and an idle Devon (best at backend Python) both see the same queue head. There is no mechanism to route the next Large story to the agent with the most headroom, or to prefer a Small bug-fix for an agent configured as `scope=small`.

### Specification

#### 3a. Agent side — `dispatch_poller.py`

**New env var** (read once at module load, after `MAX_RETRIES`):
```python
_AGENT_PREFERRED_SCOPE = os.environ.get("AGENT_PREFERRED_SCOPE", "")
# e.g. "small" or "medium" or "" (no preference)
```

**Change** in `poll_once`, the `/api/dispatch/next` GET call (around line 1452):

Before:
```python
            resp = session.get(
                f"{base_url}/api/dispatch/next",
                headers=headers,
                timeout=10,
            )
```

After:
```python
            params = {}
            if _AGENT_PREFERRED_SCOPE:
                params["preferred_scope"] = _AGENT_PREFERRED_SCOPE
            resp = session.get(
                f"{base_url}/api/dispatch/next",
                headers=headers,
                params=params,
                timeout=10,
            )
```

#### 3b. Server side — `dispatch_db_service.py`

**Change** `next_pending` signature and SQL:

Before (line 279):
```python
    async def next_pending(self) -> dict[str, Any] | None:
        ...
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT * FROM dispatch_items
                   WHERE status IN ('pending', 'paused')
                   ORDER BY priority DESC, enqueued_at ASC
                   LIMIT 1"""
            )
```

After:
```python
    async def next_pending(
        self,
        preferred_scope: str | None = None,
    ) -> dict[str, Any] | None:
        """Return highest-priority pending/paused item.

        preferred_scope: when set, items matching that scope sort before
        others at equal priority (soft preference — does not exclude items
        of other scopes when no match exists).
        """
        async with self._pool.acquire() as conn:
            if preferred_scope:
                row = await conn.fetchrow(
                    """SELECT * FROM dispatch_items
                       WHERE status IN ('pending', 'paused')
                       ORDER BY priority DESC,
                                CASE WHEN scope = $1 THEN 0 ELSE 1 END ASC,
                                enqueued_at ASC
                       LIMIT 1""",
                    preferred_scope,
                )
            else:
                row = await conn.fetchrow(
                    """SELECT * FROM dispatch_items
                       WHERE status IN ('pending', 'paused')
                       ORDER BY priority DESC, enqueued_at ASC
                       LIMIT 1"""
                )
```

#### 3c. Route — `routes/dispatch.py`

**Change** the `/api/dispatch/next` handler to forward `preferred_scope`:

```python
@router.get("/api/dispatch/next")
async def get_next(
    request: Request,
    preferred_scope: str | None = Query(default=None),
    ...
):
    ...
    item = await db.next_pending(preferred_scope=preferred_scope)
    ...
```

The `preferred_scope` param is optional — existing callers that don't send it get the current FIFO+priority behavior unchanged.

### Backward compatibility

- Server ignores unknown query params (FastAPI drops unrecognised Query params when not declared). After deploying the server change, old agent code continues to work.
- Deploy server first, then agents. No flag needed; the SQL CASE WHEN has zero overhead when `preferred_scope` is None.

---

## 4. Gap 3 — Durable Completion Guard

### Problem

`_LOCALLY_COMPLETED` (line 1388) is an in-process `set[str]`. On VM restart or `dispatch-poller` restart, it is empty. A story that was just completed (but whose completion API call returned 409 because another agent had a transient claim) will reappear in `/next` as `pending`, and the restarted agent will re-claim and re-work it.

### Specification

**Persistence file** (written atomically on each completion):
```
~/.hermes/recent-completions.json
```

Format:
```json
{
  "STORY-501": "2026-04-26T09:12:34Z",
  "STORY-502": "2026-04-26T09:45:01Z"
}
```

**New helpers** in `dispatch_poller.py`:

```python
_COMPLETIONS_FILE = os.path.expanduser(
    os.environ.get("COMPLETIONS_FILE", "~/.hermes/recent-completions.json")
)
_COMPLETIONS_TTL_HOURS = int(os.environ.get("COMPLETIONS_TTL_HOURS", "24"))


def _load_recent_completions() -> set[str]:
    """Load and prune completions file; return set of still-live story IDs."""
    try:
        with open(_COMPLETIONS_FILE) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return set()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=_COMPLETIONS_TTL_HOURS)
    live = {
        sid for sid, ts in data.items()
        if datetime.fromisoformat(ts.replace("Z", "+00:00")) > cutoff
    }
    return live


def _record_completion(story_id: str) -> None:
    """Persist story_id to the completions file (atomic write)."""
    try:
        with open(_COMPLETIONS_FILE) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    cutoff = datetime.now(timezone.utc) - timedelta(hours=_COMPLETIONS_TTL_HOURS)
    # Prune stale entries
    data = {
        sid: ts for sid, ts in data.items()
        if datetime.fromisoformat(ts.replace("Z", "+00:00")) > cutoff
    }
    data[story_id] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tmp = _COMPLETIONS_FILE + ".tmp"
    os.makedirs(os.path.dirname(_COMPLETIONS_FILE), exist_ok=True)
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, _COMPLETIONS_FILE)  # atomic on Linux
```

**Startup** (in `poll_loop` or module init, before the main loop):
```python
_LOCALLY_COMPLETED.update(_load_recent_completions())
```

**On story completion** (in `_report_complete` / wherever `_LOCALLY_COMPLETED.add(story_id)` currently lives):
```python
_LOCALLY_COMPLETED.add(story_id)
_record_completion(story_id)
```

**Imports** needed: `json` (already imported as `_json_mod`), `datetime`, `timedelta` (add if missing).

---

## 5. API Contract

| Endpoint | Change | Backward-compat |
|---|---|---|
| `GET /api/dispatch/next` | New optional `?preferred_scope=<str>` param | Yes — param ignored if absent |
| `POST /api/dispatch/claim/{story_id}` | No change | N/A |
| All other endpoints | No change | N/A |

`next_pending(preferred_scope=None)` signature is backward-compatible: all existing call sites pass no argument.

---

## 6. Deployment Sequence

1. **Deploy server** (ops-console on Morris VM): `next_pending` change + route change. Old agents continue working (no `preferred_scope` sent → original SQL path).
2. **Deploy one agent** (e.g., Derrick) with `AGENT_PREFERRED_SCOPE=small` and `CLAIM_BACKOFF=1`. Monitor retry rate and claim latency for 1 poll cycle (≤ 60s).
3. **Deploy remaining agents** with appropriate `AGENT_PREFERRED_SCOPE` values per their roles.
4. **Completions file** is created automatically on first completion after deploy — no manual step.

No DB migration needed.

---

## 7. Test Criteria

**T1 — Jitter fires on 409**: Given `poll_once` against a mock server that returns 409 twice then 200, with `CLAIM_BACKOFF=1`, the wall-clock elapsed time between attempts 1 and 2 is ≥ 0.5s.

**T2 — No jitter with flag off**: With `CLAIM_BACKOFF=0`, the same 409→409→200 sequence completes in < 100ms total (no sleep).

**T3 — `preferred_scope` forwarded**: When `AGENT_PREFERRED_SCOPE=small`, the GET to `/api/dispatch/next` includes `?preferred_scope=small` in the URL. Verified by capturing the request URL in a `requests_mock` or `responses` mock.

**T4 — Scope-aware SQL soft-preference**: With two pending rows (one `scope=large` priority=50, one `scope=small` priority=50), `next_pending(preferred_scope="small")` returns the small-scope row. `next_pending(preferred_scope=None)` returns the large-scope row (older by enqueued_at).

**T5 — Completions file written atomically**: After `_record_completion("STORY-999")`, the file exists and is valid JSON containing `"STORY-999"`. A second call with a different story appends without clobbering the first.

**T6 — Completions survive restart**: `_load_recent_completions()` returns a non-empty set when the file exists with entries < 24h old. Returns empty set when file is missing.

**T7 — Stale completions pruned**: Entries with timestamp > 24h old are not returned by `_load_recent_completions()`.

**T8 — `preferred_scope` None falls back to FIFO+priority**: `next_pending(preferred_scope=None)` uses the existing `ORDER BY priority DESC, enqueued_at ASC` query (no CASE WHEN executed).
