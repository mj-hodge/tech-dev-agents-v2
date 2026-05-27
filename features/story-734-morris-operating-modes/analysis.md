# Analysis — STORY-734: Morris Operating Modes (Quota-Aware + Overnight Scheduling)

**Story:** STORY-734
**Phase:** 4 (Analysis)
**Date:** 2026-04-27
**Author:** Derrick (tech-agent-derrick@gorillacommerce.co)

---

## 1. Problem Summary

Morris runs seven scheduled jobs 24/7 with no awareness of quota consumption or time-of-day relevance. When quota is nearly exhausted, Claude-heavy jobs keep firing until invocations fail — wasting tokens on overhead instead of story work. Overnight runs produce briefings and summaries nobody reads until morning. Mark's only recourse is manual cron disabling with no audit trail and no automatic recovery.

## 2. Codebase Assessment

### 2.1 Existing Files to Edit

| File | Location | Current State | Required Change |
|------|----------|---------------|-----------------|
| `orchestrator_loop.py` | `deployment/morris/scripts/` | 548 lines. Main orchestrator entry point with `main()`, `run_briefing()`, `execute_interventions()`. No mode awareness. | Add `check_and_auto_transition()` call at top of main loop; suppress `invoke_rebase_subagent` in light mode; add mode gate for `--briefing-only` path. |
| `daily_contact_summary.py` | `deployment/morris/scripts/` | 250 lines. Standalone script with own `main()`. Posts contact summary DM. | Add mode gate at startup: exit cleanly in `light`/`minimal` modes. |
| `orchestrator_config.yaml` | `deployment/morris/scripts/` | 38 lines. Basic config with ops_console, teams, agents, thresholds, interventions. | Add `operating_modes` section with quota thresholds and overnight schedule. |
| `install-orchestrator-cron.sh` | `deployment/morris/scripts/` | 59 lines. Idempotent cron installer using marker-guarded `install_cron_block()` helper. | Add new cron block for overnight mode scheduling (05:00 UTC → light, 12:00 UTC → full). |
| `morris-fleet-check.sh` | `deployment/vm/` | 216 lines. Two-stage fleet check (bash data collection + Claude SDK reasoning). | Add mode gate before Stage 2 (SDK invocation) to skip Claude call in `light`/`minimal` modes. |
| `morris-crontab.txt` | `deployment/vm/` | 29 lines. Reference crontab. | Document the two new overnight cron entries. |

### 2.2 Missing File: `briefing.py`

The seed references `deployment/morris/scripts/briefing.py`, but this file does not exist. Briefing logic lives in `run_briefing()` (lines 373–456 of `orchestrator_loop.py`), invoked via the `--briefing-only` CLI flag. The mode gate for briefing will be added inside `orchestrator_loop.py` at the `--briefing-only` path, not in a separate file.

### 2.3 New Files

| File | Purpose |
|------|---------|
| `deployment/morris/scripts/mode_controller.py` | Mode state management: `get_mode()`, `set_mode()`, `check_and_auto_transition()`, `get_quota_pct()`, `overnight_window()`, `mode_allows()` |
| `deployment/morris/scripts/set_mode.py` | CLI entry point: `python set_mode.py light --reason overnight_schedule` |

### 2.4 Key Dependencies

- **Quota endpoint** (`GET /api/agents/morris/quota`, STORY-510): Returns `percent_used`. Fail-open: if unavailable, auto-transition is skipped.
- **Orchestrator loop** (STORY-724): Must be deployed. Already merged to main.
- **Teams DM** (`post_dm` in `interventions.py`): Used for transition notifications. Already available.

## 3. Architecture Decisions

### 3.1 State Persistence

Mode state persists in `/opt/morris/mode_state.json`. Flat JSON file, writable by `hermes` user. Created with `{"mode": "full", ...}` on first run if absent. Each cron invocation reads fresh — no in-memory state across processes.

### 3.2 Transition Logic

```
quota >= 0.90  → minimal (from any mode)
quota >= 0.70  → light (from full only)
quota <  0.60  → light (from minimal — stepwise recovery)
quota <  0.50  → full (from light, only outside overnight window)
```

Hysteresis prevents oscillation: recovery thresholds (0.60, 0.50) are well below trigger thresholds (0.70, 0.90).

### 3.3 Overnight Window

00:00–07:00 ET = 05:00–12:00 UTC. Two cron entries fire `set_mode.py`. The 12:00 UTC recovery respects quota precedence: if mode is `minimal` due to quota, it stays `minimal`.

### 3.4 Manual Override Cooldown

When Mark sets mode via Teams DM (`actor="mark"`), auto-transition does not override for 120 minutes. This prevents the system from immediately undoing a deliberate manual override.

### 3.5 Mode Gate Pattern

Each Claude-consuming script checks mode at startup:
```python
from mode_controller import get_mode, mode_allows
if not mode_allows(get_mode(), "briefing"):
    print("[MODE] ... suspended, exiting cleanly")
    sys.exit(0)
```

For `orchestrator_loop.py` (which has mixed job classes), the gate is more nuanced:
- In `minimal` mode: exit entirely (skip the orchestrator poll)
- In `light` mode: run detectors and interventions, but suppress subagent spawns and briefing

## 4. Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Quota endpoint unavailable | Medium | Low | Fail-open: skip auto-transition, keep current mode |
| Mode state file corrupted | Low | Low | Default to `full` mode on parse error |
| Cron timing drift | Low | Low | UTC-based crons are stable; mode state is idempotent |
| Mark cooldown blocks legitimate auto-transition | Low | Medium | 120-min cap is short; Mark can always `set mode full` |

## 5. Implementation Plan

1. **Create `mode_controller.py`** — Core module with all mode logic
2. **Create `set_mode.py`** — CLI wrapper
3. **Edit `orchestrator_config.yaml`** — Add `operating_modes` section
4. **Edit `orchestrator_loop.py`** — Add auto-transition call and mode gates
5. **Edit `daily_contact_summary.py`** — Add startup mode gate
6. **Edit `morris-fleet-check.sh`** — Add mode gate before Stage 2
7. **Edit `install-orchestrator-cron.sh`** — Add overnight cron block
8. **Edit `morris-crontab.txt`** — Document new entries

---

**End of Analysis.** Ready for Phase 6 (Feature Spec).
