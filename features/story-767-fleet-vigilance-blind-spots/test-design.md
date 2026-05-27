# STORY-767 — Fleet-Vigilance Blind-Spot Closures
## Phase 7 — Test Design

---

## Numbering Note (STORY-766 not yet shipped)

STORY-766's "Check 9: Post-Merge Deploy + Re-Enqueue Sweep" had only a `seed.md` when
this phase ran (no `test-design.md`, no implementation). Per the escalation contract:

> "If 766 hasn't shipped when 767 starts Phase 7, agent should bump to Checks 9–14 and document the offset."

**This story claims Checks 9–14.** The seed referred to them as Checks 10–15 under the
assumption 766 would ship first. When 766 ships it will pick up Check 15 or negotiate a
number. All status-line tags, check IDs, and env vars in this design use **9–14**.

---

## Module Under Test

**New file:** `deployment/morris/scripts/blind_spot_checks.py`

Called from `deployment/morris/scripts/heartbeat-collector.py` after existing checks 0–8.

**Invocation from heartbeat-collector.py:**
```python
from blind_spot_checks import run_all_checks
blind_results = run_all_checks(agents=AGENTS, fetch_fn=db_fetch, run_fn=subprocess.run)
```

---

## API Design (Phase 8 contract)

All six check functions share this return shape:

```python
{
    "check_id": int,                # 9–14
    "severity": str,                # "ok" | "warn" | "crit" | "error_unavailable"
    "status_line": str,             # "[Check N Name] SEVERITY: <message>"
    "dm_payload": dict | None,      # None = no DM needed / suppressed
    "dm_suppressed": bool,          # True if CRIT was throttled (4-hour window)
    # check-specific fields below
}
```

### Function signatures

```python
def check_vm_reachability(
    agents: list[dict],
    run_fn=subprocess.run,
    suppression_path: str | None = None,
) -> dict:
    """Check 9. Returns: reachable: list[str], unreachable: list[str]."""

def check_stuck_push_code(
    run_fn=subprocess.run,
    suppression_path: str | None = None,
    stuck_threshold_min: int = 15,
) -> dict:
    """Check 10. Returns: stuck_pids: list[str]."""

def check_code_drift(
    agents: list[dict],
    run_fn=subprocess.run,
    suppression_path: str | None = None,
    drift_threshold_seconds: int = 3600,
) -> dict:
    """Check 11. Returns: drifted_agents: list[dict]."""

def check_null_failure_reason(
    fetch_fn: callable,
    suppression_path: str | None = None,
) -> dict:
    """Check 12. Returns: null_count: int."""

def check_zombie_heartbeat(
    agents: list[dict],
    run_fn=subprocess.run,
    suppression_path: str | None = None,
    phase_timeout_seconds: int = 2400,
) -> dict:
    """Check 13. Returns: zombies: list[dict]."""

def check_no_seed_dispatch(
    agents: list[dict],
    fetch_fn: callable,
    run_fn=subprocess.run,
    suppression_path: str | None = None,
) -> dict:
    """Check 14. Returns: missing_seeds: list[dict]."""

def run_all_checks(
    agents: list[dict],
    fetch_fn: callable | None = None,
    run_fn=subprocess.run,
    suppression_path: str | None = None,
    check_fns: list[callable] | None = None,
) -> list[dict]:
    """Run all 6 checks; catch exceptions per-check (SC-7)."""
```

`fetch_fn` signature: `fetch_fn(sql: str, params: tuple = ()) -> list[dict]`
— synchronous wrapper around `asyncpg` (or a test mock returning a list).

`run_fn` defaults to `subprocess.run`.

`suppression_path` defaults to `/home/hermes/state/morris/vigilance-dm-suppression.json`.

`check_fns` — when provided, replaces the default 6-function list. Used in tests.

---

## Test Groups

| Group | Check | Tests | File section |
|-------|-------|-------|--------------|
| A | Check 9 — VM Reachability      | 2 (pos + neg)   | `test_vm_reachability_*` |
| B | Check 10 — Stuck push-code.sh   | 2 (pos + neg)   | `test_stuck_push_code_*` |
| C | Check 11 — Code drift           | 2 (pos + neg)   | `test_code_drift_*` |
| D | Check 12 — NULL failure_reason  | 3 (crit/warn/ok)| `test_null_failure_reason_*` |
| E | Check 13 — Zombie heartbeat     | 2 (pos + neg)   | `test_zombie_heartbeat_*` |
| F | Check 14 — No-seed dispatch     | 2 (pos + neg)   | `test_no_seed_dispatch_*` |
| G | SC-7 error isolation            | 1               | `test_check_failures_dont_abort_run` |
| H | AC-7 DM throttling              | 1               | `test_dm_throttling_per_check` |

**Total: 15 tests** — 8 primary (match seed's "Done Looks Like") + 7 supporting.

---

## Test Matrix

### Group A — Check 9: VM Reachability (SC-1)

| Test | Fixture | Expected |
|------|---------|----------|
| `test_vm_reachability_check` | Mock SSH: dan→PONG, others→rc=255 | severity in {warn,crit}; "dan" in reachable; {"derrick","daisy","devon"}⊆unreachable; check_id=9; "[Check 9" in status_line |
| `test_vm_reachability_all_reachable` | All SSH→PONG | severity==ok; unreachable==[]; dm_payload==None |

### Group B — Check 10: Stuck push-code.sh (SC-2)

| Test | Fixture | Expected |
|------|---------|----------|
| `test_stuck_push_code_detection` | pgrep→"12345"; etime→"20:00" (20min) | severity==crit; stuck_pids non-empty; check_id=10; dm_payload not None; DM contains PID/push-code reference |
| `test_stuck_push_code_no_processes` | pgrep→rc=1 (no match) | severity==ok; stuck_pids==[]; dm_payload==None |

### Group C — Check 11: Code-version drift (SC-3)

| Test | Fixture | Expected |
|------|---------|----------|
| `test_code_drift_detection` | git log→"2026-04-29 18:42:00 +0000"; SSH stat→epoch 7h earlier | severity in {warn,crit}; drifted_agents non-empty; check_id=11 |
| `test_code_drift_ok` | git log→merge ts; SSH stat→epoch 40min after merge | severity==ok; drifted_agents==[] |

### Group D — Check 12: NULL failure_reason spike (SC-4)

| Test | Fixture | Expected |
|------|---------|----------|
| `test_null_failure_reason_spike` | fetch_fn→[{"count":7}] | severity==crit; null_count==7; check_id==12 |
| `test_null_failure_reason_warn` | fetch_fn→[{"count":3}] | severity==warn; null_count==3 |
| `test_null_failure_reason_ok` | fetch_fn→[{"count":0}] | severity==ok; dm_payload==None |

### Group E — Check 13: Zombie heartbeat (SC-5)

| Test | Fixture | Expected |
|------|---------|----------|
| `test_zombie_heartbeat_detection` | SSH heartbeat→recent line; SSH phase_end→6h-old line | severity==crit; zombies non-empty; dm_payload not None; check_id==13 |
| `test_zombie_heartbeat_no_heartbeats` | All SSH→"" | severity==ok; zombies==[] |

### Group F — Check 14: No-seed dispatch (SC-6)

| Test | Fixture | Expected |
|------|---------|----------|
| `test_no_seed_dispatch_detection` | fetch_fn→[{story_id:STORY-784,repo:advertising-amazon,...}]; SSH ls→rc=1 | severity in {warn,crit}; missing_seeds[0].story_id=="STORY-784"; check_id==14; dm_payload not None |
| `test_no_seed_dispatch_all_have_seeds` | fetch_fn→[{story_id:STORY-800,...}]; SSH ls→rc=0 | severity==ok; missing_seeds==[] |

### Group G — SC-7: Error isolation

| Test | Fixture | Expected |
|------|---------|----------|
| `test_check_failures_dont_abort_run` | custom check_fns=[_bad_check, _ok_check] where _bad_check raises | results is list; at least 1 result with severity=="error_unavailable"; at least 1 result with severity=="ok" |

### Group H — AC-7: DM throttling

| Test | Fixture | Expected |
|------|---------|----------|
| `test_dm_throttling_per_check` | suppression_file has check 9 fired 1h ago; all SSH→rc=255 | severity in {warn,crit}; dm_payload==None OR dm_suppressed==True |

---

## Mocking Strategy

All tests use **synchronous dependency injection**:

- `run_fn` parameter replaces `subprocess.run` → `patch("subprocess.run", side_effect=...)`
- `fetch_fn` parameter replaces asyncpg query → `MagicMock(return_value=[{"count": N}])`
- `suppression_path` parameter replaces `/home/hermes/state/morris/vigilance-dm-suppression.json` → `tmp_path / "vigilance-dm-suppression.json"`

No live SSH, no live DB, no filesystem writes during tests.

---

## RED State Explanation

`blind_spot_checks.py` does not exist at Phase 7 completion. The test loader
(`_load_bsc()`) checks for the file and raises `ImportError` if absent. Inside
each test, this is converted via `pytest.fail(...)` → all 15 tests FAIL with:

```
FAILED: blind_spot_checks.py not found at .../deployment/morris/scripts/blind_spot_checks.py.
Implement in Phase 8.
```

The **right reason**: implementation file missing, not a logic error.

---

## Status-line Format (fleet-health.md)

```
[Check 9 VM Reach] OK: dan ✓ derrick ✓ daisy ✓ devon ✓
[Check 9 VM Reach] CRIT: unreachable: daisy, devon
[Check 10 Stuck Deploy] OK: no push-code processes
[Check 10 Stuck Deploy] CRIT: PID 12345 stuck 20min (target: dan)
[Check 11 Code Drift] OK: all agents within 1h of latest hermes/* merge
[Check 11 Code Drift] CRIT: drifted: daisy (7h), devon (7h)
[Check 12 NULL failure_reason] OK: 0 NULL rows in last hour
[Check 12 NULL failure_reason] WARN: 3 NULL rows in last hour
[Check 12 NULL failure_reason] CRIT: 7 NULL rows in last hour
[Check 13 Zombie Heartbeat] OK: no zombies detected
[Check 13 Zombie Heartbeat] CRIT: STORY-010@dan heartbeat active, phase_end 6h ago
[Check 14 No-Seed Dispatch] OK: all active stories have seed.md
[Check 14 No-Seed Dispatch] WARN: STORY-784 (advertising-amazon) seed missing
```

---

## Severity Rubrics (matches seed.md AC-4)

| Check | WARN | CRIT |
|-------|------|------|
| 9 VM Reach | 1 unreachable | 2+ unreachable OR any unreachable (at implementer's discretion) |
| 10 Stuck Deploy | — | Any push-code.sh > 15min |
| 11 Code Drift | 1 agent drifted | 2+ agents drifted |
| 12 NULL failure_reason | 1–5 NULL rows | > 5 NULL rows |
| 13 Zombie Heartbeat | — | Any zombie detected |
| 14 No-Seed Dispatch | First detection | Repeated across cycles |

---

## DM Throttling (AC-7)

State file: `/home/hermes/state/morris/vigilance-dm-suppression.json`
(git-untracked, lives in state dir, same as fleet-health.md)

```json
{
  "9": 1746000000.0,
  "10": 1745990000.0
}
```

Each key is the check_id (string); value is the Unix timestamp of the last CRIT DM.
A check that fired CRIT < 4 hours ago: log the CRIT but set `dm_payload = None`
and `dm_suppressed = True`. When a check returns OK, remove or reset its key.

---

## Logging Format (AC-10)

```python
logging.info(f"[FLEET-VIGILANCE Check {check_id}] {severity.upper()}: {message}")
```

Visible in Loki via `{job="dispatch-poller"} |= "[FLEET-VIGILANCE Check"`.

---

## Environment Variables (AC-3)

| Variable | Default | Controls |
|----------|---------|---------|
| `FV_CHECK_9_VM_REACH` | `1` | Enable/disable Check 9 |
| `FV_CHECK_10_STUCK_DEPLOY` | `1` | Enable/disable Check 10 |
| `FV_CHECK_11_CODE_DRIFT` | `1` | Enable/disable Check 11 |
| `FV_CHECK_12_NULL_FAILURE` | `1` | Enable/disable Check 12 |
| `FV_CHECK_13_ZOMBIE_HB` | `1` | Enable/disable Check 13 |
| `FV_CHECK_14_NO_SEED` | `1` | Enable/disable Check 14 |
| `FV_STUCK_DEPLOY_MIN` | `15` | Stuck-deploy threshold (minutes) |
| `FV_DRIFT_THRESHOLD_SEC` | `3600` | Code-drift threshold (seconds) |
| `FV_NULL_FAILURE_WARN` | `1` | NULL-failure WARN threshold |
| `FV_NULL_FAILURE_CRIT` | `5` | NULL-failure CRIT threshold |
| `FV_PHASE_TIMEOUT_SEC` | `2400` | Phase timeout for zombie detection (seconds) |
| `FV_DM_SUPPRESS_SEC` | `14400` | DM suppression window (seconds; default 4h) |

---

## Acceptance Criteria Mapping

| AC | Covered by |
|----|-----------|
| AC-1 | Checks 9–14 added to SKILL.md (Phase 8) |
| AC-2 | `blind_spot_checks.py` — 6 check functions + `run_all_checks` |
| AC-3 | Env-var enable/disable per check (all with `FV_CHECK_N_*=1` default) |
| AC-4 | Every check writes `[Check N] SEVERITY: message` status_line |
| AC-5 | SSH commands: `-o ConnectTimeout=8` on all SSH invocations |
| AC-6 | `fetch_fn` uses existing dispatch DB pool — no new credentials |
| AC-7 | `suppression_path` + `dm_suppressed` logic — tested in Group H |
| AC-8 | Existing checks 0–9 are not modified |
| AC-9 | Covered by `pytest tests/morris/ -v` regression sweep |
| AC-10 | `logging.info(f"[FLEET-VIGILANCE Check N] ...")` in every check |
| AC-11 | `run_all_checks` catches each exception, records `error_unavailable` |

---

## Test File Location

```
tests/morris/test_fleet_vigilance_blind_spots.py
```

Run command:
```bash
pytest tests/morris/test_fleet_vigilance_blind_spots.py -v
```

Full regression:
```bash
pytest tests/ -x --ignore=tests/e2e -q
```
