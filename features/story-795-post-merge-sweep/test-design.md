# STORY-795 — Test Design (Phase 7)

## Test Strategy

All tests use mocked subprocess + mocked urllib (no real push-code.sh, no real DB, no real Graph API).
State-file behavior tested with `tmp_path` fixture for idempotency.
Module loaded dynamically via `importlib` to fail gracefully if implementation missing.

## Test Cases

### 1. `test_detect_new_merges_since_last_run`
**What:** Verify merge detection finds commits touching agent-VM paths since the last state-file SHA.
**Why:** SC-1 — core merge detection logic.
**How:** Mock `subprocess.run` to return 2 SHAs. Write a state file with a previous SHA + timestamp.
Assert `detect_new_merges()` returns the 2 SHAs.
**Key assertion:** `assert len(merges) == 2`

### 2. `test_no_merges_flag_absent`
**What:** Verify the git log command does NOT include `--no-merges`.
**Why:** H-2 fix — `--no-merges` filters out the merge commits this repo uses for PRs.
**How:** Mock subprocess, capture the command list, assert `--no-merges` not in it.
**Key assertion:** `assert '--no-merges' not in captured_cmd`

### 3. `test_first_run_initializes_from_head`
**What:** On first run (no state file), sweep initializes from `git rev-parse HEAD` and returns [].
**Why:** M-1 fix — prevents full history scan from 1970.
**How:** Use `tmp_path` with no state file. Mock subprocess to return HEAD SHA.
Assert returns [], state file now exists with HEAD SHA.
**Key assertion:** `assert merges == []` and `assert state_file.exists()`

### 4. `test_smoke_failure_blocks_reenqueue`
**What:** When push-code.sh returns non-zero, sweep does NOT re-enqueue any stories.
**Why:** AC-7 — smoke failure is a hard gate.
**How:** Mock `run_deploy()` to return `{"success": False, ...}`. Assert no re-enqueue calls made.
**Key assertion:** `assert reenqueued == 0`

### 5. `test_idempotent_no_new_merges`
**What:** Running sweep twice with no new merges is a no-op.
**Why:** SC-5 — idempotency via state file.
**How:** Mock git log to return empty. Assert `run_sweep()` returns `{"action": "no-op"}`.
**Key assertion:** `assert result["action"] == "no-op"`

### 6. `test_teams_dm_summary_format`
**What:** DM summary string matches expected `[FLEET-SWEEP]` format.
**Why:** SC-6 + H-1 — DM delivery integration.
**How:** Call `format_summary_dm()` with known inputs. Assert format.
**Key assertion:** `assert '[FLEET-SWEEP]' in dm`

### 7. `test_lookback_window_configurable`
**What:** Setting `FLEET_SWEEP_LOOKBACK_HOURS=48` causes query to use 48h window.
**Why:** SC-9 + T-1 fix — the old test used `assert 48 in url or True` (always passes).
**How:** Set env var, mock urlopen, capture the URL, assert `lookback_hours=48` in URL.
**Key assertion:** `assert 'lookback_hours=48' in captured_url`

### 8. `test_api_key_guard_raises_valueerror`
**What:** `query_eligible_failures()` raises `ValueError` when API key is empty.
**Why:** S-1 fix — guard against empty OPS_CONSOLE_API_KEY.
**How:** Call with `api_key=""`. Assert `pytest.raises(ValueError)`.
**Key assertion:** `with pytest.raises(ValueError, match="OPS_CONSOLE_API_KEY")`

### 9. `test_reenqueue_strips_retry_prefix`
**What:** Re-enqueue strips existing `[RETRY N/N]` prefix from prompt.
**Why:** AC-6 — STORY-764 pattern.
**How:** Mock urlopen, call `reenqueue_story()` with prompt containing `[RETRY 2/3]`, capture payload.
**Key assertion:** `assert '[RETRY' not in sent_prompt`

### 10. `test_dm_delivery_called_on_success`
**What:** `send_teams_dm()` is called with summary when sweep succeeds.
**Why:** H-1 fix — DM delivery must actually happen.
**How:** Mock `send_teams_dm`, run sweep with mocked success path. Assert it was called.
**Key assertion:** `mock_send_dm.assert_called_once()`

### 11. `test_dm_delivery_called_on_crit`
**What:** `send_teams_dm()` is called with CRIT message when deploy fails.
**Why:** H-1 fix — CRIT path also needs DM delivery.
**How:** Mock deploy to fail, run sweep. Assert `send_teams_dm` called with `[CRIT]` in message.
**Key assertion:** `assert '[CRIT]' in mock_send_dm.call_args[0][0]`

### 12. `test_cancel_before_reenqueue_nonterminal`
**What:** Non-terminal rows (paused/claimed) get cancelled before re-enqueue.
**Why:** SC-4 / AC-5 — STORY-765 manager override.
**How:** Mock eligible rows with `status='paused'`. Assert DELETE called before POST.
**Key assertion:** `assert cancel_call happened before reenqueue_call`

## Validation Commands

```bash
pytest tests/morris/test_fleet_vigilance_post_merge_sweep.py -v
pytest tests/ -x --ignore=tests/e2e -q  # full regression
```
