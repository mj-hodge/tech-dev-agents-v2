# Feature Spec — STORY-795: Fleet Vigilance Post-Merge Deploy + Re-Enqueue Sweep (Check 8)

## Overview

| Field | Value |
|-------|-------|
| Story | STORY-795 |
| Scope | Small |
| Rework of | STORY-766 (PR #244 closed with REQUEST_CHANGES) |
| Frontend | No |
| API Contract Changes | `failure_reason` added to `DispatchItem` model; `/dispatch/history` now surfaces it |

---

## 1. Components

### 1.1 `deployment/morris/scripts/post_merge_sweep.py` (New file)

Fleet-vigilance Check 8 implementation. Pure Python stdlib + subprocess.

**Key functions:**

| Function | Purpose |
|----------|---------|
| `_validate_api_key()` | S-1 fix: raise `ValueError` on empty `OPS_CONSOLE_API_KEY` |
| `_read_state()` / `_write_state()` | SC-5: idempotency via state file |
| `_get_head_sha()` | M-1 fix: first-run initialization from HEAD |
| `_detect_merges()` | SC-1 / H-2: git log WITHOUT `--no-merges` |
| `_run_push_code()` | SC-2 / AC-7 / AC-11: deploy with timeout gate |
| `_query_eligible_failures()` | SC-9: query `/api/dispatch/history?status=failed` |
| `_strip_retry_prefix()` | AC-6: strip `[RETRY]` prefix (STORY-764 pattern) |
| `_cancel_item()` | SC-4 / AC-5: STORY-765 manager override cancel |
| `_reenqueue_item()` | SC-3: POST to `/api/dispatch` with audit header |
| `check_post_merge_sweep()` | Main entry point; returns heartbeat-compatible dict |

**Critical implementation notes:**

- `_detect_merges()` does NOT use `--no-merges` (H-2 fix). This repo lands PRs as merge commits.
- `_query_eligible_failures()` uses `GET /api/dispatch/history?status=failed` (not `/queue`).
  The `/queue` endpoint has no `failed` bucket — using it silently returns zero eligible rows.
- First run (no state file) initializes from HEAD and returns early (M-1 fix).
- State file format: `<SHA> <ISO-timestamp>` at `/home/hermes/state/morris/last-fleet-vigilance-merge-sweep.txt`.

### 1.2 `deployment/morris/scripts/heartbeat-collector.py` (Modified)

**Change:** `_run_post_merge_sweep()` imports `check_post_merge_sweep` from `post_merge_sweep`
(underscored module name, not hyphenated). Result appended to `payload["blind_spot_checks"]`.

### 1.3 `deployment/vm/skills/fleet-vigilance/SKILL.md` (Modified)

**Change:** Check 8 section added after Check 7, before Checks 9-14 (M-3 fix).

### 1.4 `tech_dev_agents/ops_console/models/responses.py` (Modified)

**Change:** `failure_reason: str | None = None` added to `DispatchItem` model so the
`/dispatch/history` endpoint surfaces it for sweep eligibility filtering.

### 1.5 `tech_dev_agents/ops_console/routes/dispatch.py` (Modified)

**Change:** `dispatch_history()` route now passes `failure_reason=row.get("failure_reason")`
when constructing `DispatchItem` objects from DB rows.

---

## 2. API Changes

### GET `/api/dispatch/history?status=failed`

Response items now include `failure_reason`:

```json
{
  "items": [
    {
      "story_id": "STORY-759",
      "repo": "tech-dev-agents",
      "status": "failed",
      "failure_reason": "branch_setup_failed: ...",
      "...": "..."
    }
  ],
  "total": 11,
  "limit": 200,
  "offset": 0,
  "fetched_at": "2026-05-04T12:00:00+00:00"
}
```

The sweep filters items by `_ELIGIBLE_FAILURE_PREFIXES`:
- `branch_setup_failed:`
- `phase_progress_stalled:`
- `sdk_died_no_phase_end:`

---

## 3. Environment Variables

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `OPS_CONSOLE_API_KEY` | Yes | — | S-1: dispatch API auth (ValueError if empty) |
| `FLEET_SWEEP_LOOKBACK_HOURS` | No | 24 | SC-9: failed-item query window |
| `FLEET_SWEEP_DEPLOY_TIMEOUT_SEC` | No | 900 | AC-11: push-code.sh timeout |

---

## 4. Heartbeat Integration

`check_post_merge_sweep()` returns a heartbeat-compatible dict:

```python
{
    "check_id": 8,
    "severity": "ok" | "crit" | "warn" | "error_unavailable",
    "status_line": "[FLEET-VIGILANCE Check 8] Post-Merge Deploy + Re-Enqueue Sweep: ...",
    "dm_payload": {"text": "..."} | None,
    "dm_suppressed": False,
    "merges_detected": 2,
    "deploy_success": True,
    "requeued": 3,
    "skipped": 0,
}
```

This is appended to `payload["blind_spot_checks"]` alongside Checks 9-14 and Check 16.
