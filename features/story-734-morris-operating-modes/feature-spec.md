# Feature Spec — STORY-734: Morris Operating Modes (Quota-Aware + Overnight Scheduling)

**Story:** STORY-734
**Phase:** 6 (Feature Spec)
**Date:** 2026-04-27
**Author:** Derrick (tech-agent-derrick@gorillacommerce.co)

---

## 1. Overview

This feature adds three operating modes to Morris (`full`, `light`, `minimal`) that automatically throttle Claude-consuming scheduled jobs based on quota consumption and time of day. The system persists mode state to a JSON file, auto-transitions based on quota thresholds with hysteresis, supports overnight scheduling via cron, and allows manual override from Mark with a 2-hour cooldown.

## 2. Module: `mode_controller.py`

### 2.1 Constants and Types

```python
MODE_STATE_PATH = Path(os.environ.get("MORRIS_MODE_STATE", "/opt/morris/mode_state.json"))

MODES = ("full", "light", "minimal")

JOB_CLASS = {
    "shell_infra":       {"full", "light", "minimal"},
    "orchestrator_poll": {"full", "light"},
    "briefing":          {"full"},
    "fleet_check_llm":   {"full"},
    "self_improvement":  {"full"},
    "contact_summary":   {"full"},
}
```

### 2.2 Public API

#### `get_mode() -> str`
Reads `MODE_STATE_PATH`, returns the `mode` field. If the file is missing or corrupt, returns `"full"` (fail-open).

#### `set_mode(mode: str, reason: str, actor: str, config: dict | None = None) -> None`
Writes mode state to `MODE_STATE_PATH`. Posts a Teams DM via `interventions.post_dm()` if config is provided. State structure:
```json
{
  "mode": "light",
  "set_at": "2026-04-26T05:00:00Z",
  "reason": "overnight_schedule",
  "actor": "cron",
  "quota_pct_at_set": 0.45
}
```

#### `check_and_auto_transition(config: dict, session: Any) -> str`
Reads quota via `get_quota_pct()`, applies threshold logic:
1. `quota >= 0.90` → `minimal` (from any mode)
2. `quota >= 0.70` and current is `full` → `light`
3. `quota < 0.60` and current is `minimal` → `light` (stepwise recovery)
4. `quota < 0.50` and current is `light` and not overnight → `full`

Respects manual-override cooldown: if `actor == "mark"` and `set_at` is within `mark_cooldown_minutes`, skip auto-transition.

Returns the current mode after any transition.

#### `get_quota_pct(config: dict) -> float | None`
Calls `GET {ops_console_url}/api/agents/morris/quota`. Returns `percent_used` as float (0.0–1.0). Returns `None` on any error (fail-open).

#### `overnight_window(config: dict) -> bool`
Returns `True` if current UTC time is between `overnight_start_utc` and `overnight_end_utc` from config. Default: 05:00–12:00 UTC.

#### `mode_allows(mode: str, job_class: str) -> bool`
Returns `True` if the given mode permits the given job class to run, per the `JOB_CLASS` lookup table.

### 2.3 Thresholds (from config)

```yaml
operating_modes:
  light_quota_threshold: 0.70
  minimal_quota_threshold: 0.90
  recovery_to_light_threshold: 0.60
  recovery_to_full_threshold: 0.50
  mark_cooldown_minutes: 120
  overnight_start_utc: "05:00"
  overnight_end_utc: "12:00"
```

## 3. CLI: `set_mode.py`

```
Usage: python set_mode.py <mode> --reason <reason> [--config path] [--actor cron|mark|admin]
```

- Validates mode is one of `full`, `light`, `minimal`
- Calls `mode_controller.set_mode()` with provided args
- Default actor: `"cron"` (for cron-scheduled calls)
- Logs the transition and exits 0

## 4. Edits to `orchestrator_loop.py`

### 4.1 Auto-transition at cycle start

At the top of `main()`, after config load and session build, before the flock:
```python
from mode_controller import check_and_auto_transition, get_mode, mode_allows

# After session = build_session(config):
current_mode = check_and_auto_transition(config, session)
```

### 4.2 Minimal mode → exit

```python
if not mode_allows(current_mode, "orchestrator_poll"):
    logger.info("[MODE] %s — orchestrator poll suspended, exiting cleanly", current_mode)
    return
```

### 4.3 Briefing mode gate

In the `--briefing-only` path:
```python
if args.briefing_only:
    if not mode_allows(current_mode, "briefing"):
        logger.info("[MODE] %s — briefing suspended, exiting cleanly", current_mode)
        return
    run_briefing(session, config, args.dry_run)
    return
```

### 4.4 Subagent suppression in light mode

In `execute_interventions()`, the `invoke_rebase_subagent` call is wrapped:
```python
if current_mode == "light":
    logger.info("[MODE light] subagent suppressed: rebase PR#%s", r.pr_number)
else:
    invoke_rebase_subagent(r, config, dry_run, session=session)
```

The mode is passed through via a new `mode` parameter to `execute_interventions()`.

## 5. Edits to `daily_contact_summary.py`

Add mode gate at the top of `main()`, after config load:
```python
from mode_controller import get_mode, mode_allows
if not mode_allows(get_mode(), "contact_summary"):
    logger.info("[MODE] %s — contact summary suspended, exiting cleanly", get_mode())
    sys.exit(0)
```

## 6. Edits to `morris-fleet-check.sh`

Add mode gate before Stage 2 (line ~176, before the `sudo -u hermes timeout 180 claude` call):
```bash
# Mode gate — skip Claude invocation in light/minimal modes
MODE=$(python3 -c "
import sys; sys.path.insert(0, '/opt/morris')
from mode_controller import get_mode, mode_allows
print('yes' if mode_allows(get_mode(), 'fleet_check_llm') else 'no')
" 2>/dev/null || echo "yes")

if [ "$MODE" = "no" ]; then
    echo "$TS [MODE] fleet-check Claude call suppressed (mode=$(python3 -c '...'))" >> "$LOG"
    echo "$TS === fleet-check DONE (mode-gated) ===" >> "$LOG"
    exit 0
fi
```

Stage 1 (bash data collection) still runs — it's free (no Claude cost).

## 7. Edits to `install-orchestrator-cron.sh`

New cron block using existing `install_cron_block()` helper:
```bash
install_cron_block "# morris-operating-modes-734" "Overnight mode" "# morris-operating-modes-734
0 5 * * *  /opt/morris/venv/bin/python /opt/morris/set_mode.py light --reason overnight_schedule >> /var/log/morris/orchestrator.log 2>&1
0 12 * * * /opt/morris/venv/bin/python /opt/morris/set_mode.py full  --reason overnight_end      >> /var/log/morris/orchestrator.log 2>&1"
```

## 8. Edits to `orchestrator_config.yaml`

Append `operating_modes` section (see §2.3).

## 9. Teams DM Format

On every `set_mode()` call:
```
[MODE] Morris → light
Reason: quota_approaching (quota at 73%)
Shell jobs continue. Claude-consuming jobs suspended.
To override: message Morris "set mode full"
```

## 10. Edge Cases

- **Quota endpoint down**: `get_quota_pct()` returns `None` → auto-transition skipped (fail-open)
- **Mode state file missing**: `get_mode()` returns `"full"`, `set_mode()` creates the file
- **Mode state file corrupt JSON**: `get_mode()` returns `"full"`, logs warning
- **12:00 UTC recovery cron + minimal from quota**: `set_mode.py full` respects quota — if `check_and_auto_transition()` immediately re-evaluates on next orchestrator cycle, mode goes back to minimal. The `set_mode.py` CLI for cron does NOT run `check_and_auto_transition` — it just sets the mode. The next orchestrator loop tick (within 10 min) will re-evaluate quota and potentially override the cron-set mode.
- **Concurrent writes to mode state**: Atomic write via `tempfile` + `os.rename()` prevents corruption.

---

**End of Feature Spec.** Ready for Phase 7 (Test Design).
