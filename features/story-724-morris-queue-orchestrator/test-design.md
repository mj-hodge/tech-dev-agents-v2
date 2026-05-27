# Test Design — STORY-724: Morris Queue Orchestrator

**Phase:** 7 / 8 (original) + Review-Findings Fix (2026-04-26)
**Date:** 2026-04-26

---

## Overview

12 original test cases + 5 new test cases from the PR 159 dual-review
(Morris / Codex GPT-5.4) covering:
- `deployment/morris/scripts/detectors.py` — 6 pure detector functions
- `deployment/morris/scripts/interventions.py` — 6 action execution functions
- `deployment/morris/scripts/orchestrator_loop.py` — main entry point

Test file: `tests/deployment/test_morris_orchestrator_724.py`

### Review findings addressed (2026-04-26)

| ID | Severity | Finding | Fix module |
|----|----------|---------|-----------|
| H-1 | CRITICAL | No per-story release cooldown — infinite recycle loop possible | detectors.py + config |
| H-2 | CRITICAL | Variable content not HTML-escaped before Teams DM injection | interventions.py |
| M-1 | MEDIUM | Negative ages from clock skew not handled per-row | detectors.py |
| M-2 | MEDIUM | OPS console outage not detected; interventions still run on empty data | orchestrator_loop.py |
| M-3 | MEDIUM | session.post() response status not checked in post_dm | interventions.py |

---

## Test Case Table

| TC | Module | Function | Scenario | Assert |
|---|---|---|---|---|
| TC-1 | detectors | `detect_stale_never_started` | heartbeat=None, claimed_at=now-16min | 1 record returned; story_id=999 |
| TC-2 | detectors | `detect_stale_never_started` | heartbeat=None, claimed_at=now-5min (fresh) | empty list returned |
| TC-3 | detectors + interventions | `detect_pr_conflicts` + `invoke_rebase_subagent` | CONFLICTING, agent author, rebase.enabled=True | ConflictRecord has agent_owned=True; `invoke_rebase_subagent` called (via mock subprocess) |
| TC-4 | detectors | `detect_pr_conflicts` | CONFLICTING, human author | ConflictRecord has agent_owned=False |
| TC-5 | detectors | `detect_repeated_failures` | 3 failures within 24h for same story | 1 EscalateRecord returned with failure_count=3 |
| TC-6 | interventions | `post_load_imbalance_dm` | dan=3 pending, others=0 | session.post called once with [INFO] in body; no release calls |
| TC-7 | detectors | `detect_needs_info_decay` | status=needs_info, updated_at=now-5h (threshold=4h) | 1 NeedsInfoRecord returned; age_hours≥4 |
| TC-8 | orchestrator_loop | `run_briefing` | 5 pending, 2 claimed, 1 failed | session.post called once; body contains "[BRIEFING]" |
| TC-9 | orchestrator_loop | `main()` flock guard | Two concurrent calls via multiprocessing | Second process exits 0; interventions fire in only one process |
| TC-10 | interventions | `invoke_rebase_subagent` | Subprocess sleeps 400s; timeout_seconds=1 | TimeoutExpired caught; `post_dm` called with "[INFO]" timeout message |
| TC-11 | interventions | `post_approval_needed` | 3 failure entries | session.post called; body contains story_id and all 3 failure entries |
| TC-12 | interventions | All interventions with dry_run=True | `dry_run=True` on release_claim, invoke_rebase_subagent, post_approval_needed | Zero session.post calls; zero subprocess.run calls |
| **TC-13** | **detectors** | **`detect_stale_never_started`** | **stale_release_count=3 (at max_stale_releases=3 threshold)** | **Empty list — story skipped to prevent recycle loop (H-1)** |
| **TC-14** | **interventions** | **`post_dm`** | **bullet contains `<script>alert(1)</script>`** | **session.post called with HTML-escaped content; raw `<` not present in body (H-2)** |
| **TC-15** | **detectors** | **`detect_stale_never_started`** | **claimed_at = now+10min (future timestamp / clock skew)** | **Empty list — negative age row skipped without exception (M-1)** |
| **TC-16** | **orchestrator_loop** | **`execute_interventions` gate** | **Both fetch_queue and fetch_history return ok=False (outage sentinel)** | **Zero session.post calls; CRIT logged (M-2)** |
| **TC-17** | **interventions** | **`post_dm`** | **session.post returns status_code=403** | **logger.warning called mentioning 4xx status (M-3)** |

---

## Design Decisions

### Pure detector tests (TC-1, TC-2, TC-4, TC-5, TC-7)
Detectors accept plain Python dicts and an injectable `now` parameter. No mocking
required. Tests construct minimal dict payloads and assert on the returned record
lists. Using timezone-aware `datetime(..., tzinfo=timezone.utc)` throughout.

### Intervention tests (TC-6, TC-8, TC-10, TC-11)
Interventions call `requests.Session`. Tests use `unittest.mock.MagicMock()` for
the session object. Assertions inspect `session.post.call_args_list` to verify
the correct Graph API URL and body content.

### Flock test (TC-9)
Uses `multiprocessing.Process` to spawn a real second process. The first process
acquires the flock and holds it while the second tries. The second must exit
cleanly (exit code 0) and emit a "skip" log message.

### Timeout test (TC-10)
Uses `unittest.mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired(...))` to
simulate a timed-out subprocess without actually sleeping. Asserts that `post_dm`
was called with the "[INFO]" timeout message.

### dry_run contract (TC-12)
Each intervention is called with `dry_run=True` and a MagicMock session. Asserts
`session.post.call_count == 0` and (for rebase) patches subprocess.run to assert
it is never called.

---

## Record Types Expected

From `detectors.py` (dataclasses):

```python
@dataclass
class StaleClaimRecord:
    story_id: int
    reason: str          # 'never_started' | 'heartbeat_stale' | 'phase_stale'
    claimed_by: str
    claimed_at: str
    last_heartbeat: str | None
    last_updated: str | None

@dataclass
class EscalateRecord:
    story_id: int
    failure_count: int
    recent_failures: list[dict]

@dataclass
class ConflictRecord:
    pr_number: int
    repo: str
    head: str
    base: str
    author: str
    title: str
    agent_owned: bool

@dataclass
class NeedsInfoRecord:
    story_id: int
    updated_at: str
    age_hours: float
```

---

### Review-findings test cases (TC-13 through TC-17)

**TC-13 (H-1 — max-release cooldown):** Feed a queue row with
`stale_release_count=3` and `max_stale_releases=3`. Verify that
`detect_stale_never_started` returns an empty list — the detector must skip
stories at or above the max to prevent infinite recycle loops.

**TC-14 (H-2 — HTML escaping):** Call `post_dm` with a bullet string
containing `<script>alert(1)</script>`. Inspect the raw JSON payload sent to
`session.post` and assert that the string `<script>` does not appear — only the
HTML-escaped form `&lt;script&gt;`.

**TC-15 (M-1 — negative age / clock skew):** Set `claimed_at` to
`now + 10 minutes` (a future timestamp). Verify that `detect_stale_never_started`
returns an empty list without raising — negative ages must be silently skipped.

**TC-16 (M-2 — OPS console outage gate):** Patch `fetch_queue` and
`fetch_history` to return `_FetchResult([], ok=False)` (the outage sentinel).
Call `execute_interventions` / `main`-level logic and assert that
`session.post` is never called — no DMs sent when the API is down.

**TC-17 (M-3 — response status check):** Configure `session.post` to return a
mock response with `status_code=403`. Call `post_dm` and assert that
`logger.warning` was called with the 4xx status code in the message.

---

## Config Fixture

All tests use a minimal config dict matching `orchestrator_config.yaml` schema:

```python
BASE_CONFIG = {
    "thresholds": {
        "never_started_minutes": 15,
        "heartbeat_stale_minutes": 15,
        "phase_stale_minutes": 45,
        "needs_info_decay_hours": 4,
        "repeated_failure_count": 3,
        "overload_pending_count": 3,
        "max_stale_releases": 3,        # H-1: per-story release cooldown
    },
    "interventions": {
        "rebase": {"enabled": True, "timeout_seconds": 300},
        "release": {"enabled": True, "story_702_merged": True},
    },
    "agents": {
        "github_logins": {
            "dan": "Bot Dan",
            "derrick": "Bot Derrick",
            "morris": "Bot Morris",
            "daisy": "Bot Daisy",
            "devon": "Bot Devon",
        }
    },
    "ops_console": {"url": "http://ops:8000", "api_key": "test-key"},
    "teams": {"mark_chat_id": "fake-chat-id"},
    "workdir": "/home/hermes/dev",
}
```

---

## RED State Verification

Run:
```bash
python -m pytest tests/deployment/test_morris_orchestrator_724.py -v
```

Expected: All 12 tests fail with `ImportError: No module named
'deployment.morris.scripts.detectors'` (or similar). This is the correct RED
state — implementation (Phase 8, Task 2-4) will turn them GREEN.
