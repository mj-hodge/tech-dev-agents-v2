# Seed — STORY-734: Morris Operating Modes (Quota-Aware + Overnight Scheduling)

**Story:** STORY-734
**Date:** 2026-04-26
**Author:** Derrick (tech-agent-derrick@gorillacommerce.co)
**Phase:** 1 (Seed)

---

## Overview

| Field | Value |
|-------|-------|
| **Story ID** | STORY-734 |
| **Title** | Morris Operating Modes — Quota-Aware + Overnight Scheduling |
| **Mode** | feature_add |
| **Scope** | Medium |
| Frontend | false |
| **Phase Path** | 1 → 4 → 6 → 7 → 8 → Done |
| **Priority** | 80 |
| **Owner Persona** | Morris (Engineering Manager) |
| **Depends On** | STORY-724 (orchestrator loop — must be deployed first) |
| **Related** | STORY-510 (quota endpoint), STORY-543 (Loki P90 baseline), STORY-585 (foundry pace check) |

---

## 1. Trigger

On 2026-04-26, Morris's quota was nearly exhausted from overnight firefighting. Mark had to manually disable most Morris crons to prevent further spend — an error-prone, invisible process with no audit trail and no automatic recovery. The quota burned out because Morris has no mechanism to detect approaching limits and throttle work accordingly. He also has no concept of "overnight" — he runs the same LLM-heavy schedule at 3am as at 10am, burning sessions on briefings and fleet checks that no one reads until morning anyway.

Two recurring pain points this story addresses:
1. **No quota awareness** — Morris only finds out he's out of tokens when a Claude invocation fails. By then the damage is done.
2. **No overnight scaling** — resource-intensive cron jobs run 24/7 even when no operator is present to act on their output.

---

## 2. Problem Statement

Morris runs seven distinct scheduled jobs, each with different resource profiles:

| Job | Resource class | Quota consumed |
|-----|---------------|----------------|
| `orchestrator_loop.py` (10-min cron) | Claude SDK calls when interventions fire | Variable — 0 to ~$0.50/run |
| `orchestrator_loop.py --briefing-only` | Claude call to generate DM | ~$0.05–0.15/run |
| `morris-fleet-check.sh` | Claude call for fleet analysis | ~$0.10–0.30/run |
| `daily_contact_summary.py` | Claude call to summarize | ~$0.05/run |
| `cost_monitor.sh` | Pure shell | $0 |
| `refresh_graph_token.sh` | m365 CLI, no LLM | $0 |
| `health-ping.sh` | curl, no LLM | $0 |

When quota is constrained, continuing to run the Claude-heavy jobs burns tokens on operational overhead instead of story work. The right behavior: when quota gets low, suspend the discretionary Claude jobs and keep only the infrastructure jobs running. When quota is critically low, suspend everything non-essential.

Similarly, the briefing and contact summary only matter when Mark is at his desk. Running them at 2am produces output nobody reads until morning. The overnight window is pure waste.

---

## 3. Proposed Solution

### 3.1 Three Operating Modes

| Mode | When active | Jobs that run |
|------|------------|--------------|
| `full` | Normal — quota healthy, business hours | Everything |
| `light` | Quota >70% of daily cap OR overnight window (00:00–07:00 ET) | Shell crons only + orchestrator loop (intervention-check only, no briefing, no subagent spawns) + stale-claim release |
| `minimal` | Quota >90% of daily cap OR manual override | `refresh_graph_token.sh`, `cost_monitor.sh` only — orchestrator loop and all Claude crons suspended |

"Quota %" is calculated against the daily session cap tracked by the quota endpoint (`GET /api/agents/morris/quota`) or ccusage if the endpoint is unavailable.

### 3.2 Mode Controller Module

New `mode_controller.py` at `/opt/morris/`:

```python
# Key interface
get_mode() -> Literal["full", "light", "minimal"]
set_mode(mode, reason, actor) -> None          # writes to mode_state.json, posts Teams DM
check_and_auto_transition() -> str             # reads quota, applies thresholds, transitions if needed
get_quota_pct() -> float | None                # 0.0–1.0, None if unavailable
```

Mode state persists in `/opt/morris/mode_state.json`:
```json
{
  "mode": "light",
  "set_at": "2026-04-26T00:00:00Z",
  "reason": "overnight_schedule",
  "actor": "cron",
  "quota_pct_at_set": 0.45
}
```

### 3.3 Job Classification and Mode Gate

Each Claude-consuming script adds a two-line gate at startup:

```python
from mode_controller import get_mode, JOB_CLASS
mode = get_mode()
if not mode_allows(mode, JOB_CLASS["briefing"]):
    print(f"[MODE] {mode} — briefing suspended, exiting cleanly")
    sys.exit(0)
```

Job classes and their mode requirements:

| Job class | Runs in `full` | Runs in `light` | Runs in `minimal` |
|-----------|:--------------:|:---------------:|:-----------------:|
| `shell_infra` | ✓ | ✓ | ✓ |
| `orchestrator_poll` | ✓ | ✓ (no subagents) | ✗ |
| `briefing` | ✓ | ✗ | ✗ |
| `fleet_check_llm` | ✓ | ✗ | ✗ |
| `self_improvement` | ✓ | ✗ | ✗ |
| `contact_summary` | ✓ | ✗ | ✗ |

In `light` mode the orchestrator loop runs but `subagent_runner.run()` is suppressed — it logs `[MODE light] subagent suppressed: rebase` instead of invoking.

### 3.4 Overnight Schedule

Two new cron entries (added by `install-orchestrator-cron.sh`):

```
# morris-operating-modes-734
0 5 * * *  /opt/morris/venv/bin/python /opt/morris/set_mode.py light  --reason overnight_schedule >> /var/log/morris/orchestrator.log 2>&1
0 12 * * * /opt/morris/venv/bin/python /opt/morris/set_mode.py full   --reason overnight_end      >> /var/log/morris/orchestrator.log 2>&1
```

Times are UTC: 00:00 ET = 05:00 UTC, 07:00 ET = 12:00 UTC. Weekends use the same schedule.

If quota is already `minimal` when the 12:00 UTC recovery cron runs, mode stays `minimal` (quota gate takes precedence over schedule).

### 3.5 Auto-transition in Orchestrator Loop

`check_and_auto_transition()` runs at the top of every `orchestrator_loop.py` cycle:

```
quota_pct = get_quota_pct()
if quota_pct >= 0.90 and current_mode != "minimal":
    set_mode("minimal", "quota_critical", "auto")
elif quota_pct >= 0.70 and current_mode == "full":
    set_mode("light", "quota_approaching", "auto")
elif quota_pct < 0.60 and current_mode == "minimal":
    set_mode("light", "quota_recovering", "auto")    # don't jump straight to full
elif quota_pct < 0.50 and current_mode == "light" and not overnight_window():
    set_mode("full", "quota_healthy", "auto")
```

Hysteresis is intentional — recovery requires quota to drop below 50% before returning to `full` to prevent oscillation near the threshold.

### 3.6 Teams DM on Every Transition

```
[MODE] Morris → light
Reason: quota_approaching (quota at 73%)
Shell jobs continue. Claude-consuming jobs suspended.
To override: message Morris "set mode full"
```

```
[MODE] Morris → minimal
Reason: quota_critical (quota at 92%)
Only graph token + cost monitor running.
Orchestrator loop suspended until quota recovers below 90%.
```

### 3.7 Manual Override

Morris listens for Teams messages containing `set mode <mode>` (already handled via hermes-gateway). The mode controller's `set_mode()` accepts `actor="mark"` and records it. Recovery from manual-minimal requires explicit `set mode full` or `set mode light` — auto-transition does NOT override a Mark-set mode for 2 hours (cooldown).

---

## 4. Codebase Context

### 4.1 New files

| File | Purpose |
|------|---------|
| `deployment/morris/scripts/mode_controller.py` | Mode state read/write, quota check, auto-transition logic |
| `deployment/morris/scripts/set_mode.py` | CLI: `python set_mode.py light --reason overnight_schedule` |

### 4.2 Files to edit

| File | Change |
|------|--------|
| `deployment/morris/scripts/orchestrator_loop.py` | Call `check_and_auto_transition()` at top; suppress `subagent_runner.run()` in light mode |
| `deployment/morris/scripts/briefing.py` | Mode gate at startup |
| `deployment/morris/scripts/daily_contact_summary.py` | Mode gate at startup |
| `deployment/morris/scripts/orchestrator_config.yaml` | Add `operating_modes` section with thresholds |
| `deployment/morris/scripts/install-orchestrator-cron.sh` | Add overnight schedule crons (idempotent, marker-guarded) |
| `deployment/vm/morris-fleet-check.sh` | Mode gate before Claude invocation |
| `deployment/vm/morris-crontab.txt` | Document the two new overnight cron entries |

### 4.3 Quota source

`GET /api/agents/morris/quota` (ops console endpoint, from STORY-510). Returns `percent_used` field. If endpoint returns non-200 or `source=unavailable`, `get_quota_pct()` returns `None` and auto-transition is skipped (fail-open: don't restrict Morris just because quota check failed).

### 4.4 Mode state file

`/opt/morris/mode_state.json` — writable by `hermes` user. Created with `{"mode": "full", ...}` on first run if absent. Mode persists across process restarts (cron invocations are stateless; they must read the file each time).

---

## 5. Out of Scope

- **Agent VM quota management** — this story is Morris-only. Dan/Derrick/Daisy/Devon have separate quota tracking; a future story could apply the same pattern to them.
- **Automatic story rescheduling** — in minimal mode, Morris doesn't try to reschedule the work he skipped. It just doesn't run. Stories that would have been briefed or checked will be caught on the next full-mode cycle.
- **Persistent quota database** — mode state is a flat JSON file. No DB schema changes.
- **UI** — no frontend surface. Mode is observable via Teams DMs and the orchestrator log.
- **Per-job overrides** — mode applies fleet-wide to job classes. Per-job granularity is a future refinement.

---

## Test Criteria

1. **TC-1 — Auto-transition to light.** Given `get_quota_pct()` returns 0.73, `check_and_auto_transition()` from `full` mode calls `set_mode("light", "quota_approaching", "auto")` exactly once and posts a Teams DM.
2. **TC-2 — Auto-transition to minimal.** Given 0.92, transitions from any mode to `minimal`.
3. **TC-3 — Hysteresis.** Given 0.88 (below 0.90), mode stays `minimal` — does NOT recover prematurely.
4. **TC-4 — Recovery to light.** Given 0.55 with current mode `minimal`, transitions to `light` (not `full`).
5. **TC-5 — Recovery to full.** Given 0.45 and not overnight, transitions from `light` to `full`.
6. **TC-6 — Overnight gate.** Given 03:00 ET, `overnight_window()` returns True; orchestrator loop's briefing call is suppressed even if mode is `full`.
7. **TC-7 — Quota unavailable → no transition.** Given `get_quota_pct()` returns None, `check_and_auto_transition()` exits without changing mode.
8. **TC-8 — Mark cooldown.** Given Mark manually set mode to `minimal`, auto-transition does NOT override it for 120 minutes.
9. **TC-9 — Light mode suppresses subagents.** Given mode is `light`, `subagent_runner.run()` logs `[MODE light] subagent suppressed` and returns without spawning.
10. **TC-10 — Shell jobs unaffected.** `cost_monitor.sh` and `refresh_graph_token.sh` exit code 0 regardless of mode (they have no mode gate).
11. **TC-11 — Teams DM on every transition.** Mock Teams client asserts a DM is posted for each `set_mode()` call.
12. **TC-12 — Overnight crons idempotent.** Running `install-orchestrator-cron.sh` twice does not duplicate the overnight entries.

---

## Validation

1. **Staging dry-run:** On Morris VM, manually call `python set_mode.py light --reason test` → verify `mode_state.json` written, Teams DM received, next orchestrator loop cycle exits briefing early.
2. **Quota simulation:** Mock quota endpoint returning 0.75 → verify `check_and_auto_transition()` transitions from full → light in the orchestrator loop.
3. **Overnight window:** At 05:00 UTC, cron fires `set_mode.py light` → Morris stays in light until 12:00 UTC cron fires `set_mode.py full` (or quota gate keeps it lower).
4. **Recovery:** Set quota mock to 0.40, current mode `minimal` → verify loop transitions to light, then to full on next cycle once below 0.50 and not overnight.
5. **Manual override cooldown:** `set mode minimal` via Teams DM → auto-transition does not override for 2h.

---

## Dispatch Notes

- **Suggested agent:** Dan or Derrick (both familiar with Morris VM and orchestrator codebase from STORY-724 and STORY-044).
- **Coordinate:** No in-flight stories touch `orchestrator_loop.py` or `mode_controller` (new file). Safe to dispatch immediately.
- **Deploy note:** After Phase 8, copy `mode_controller.py` and `set_mode.py` to Morris VM and run `install-orchestrator-cron.sh`. The overnight crons are harmless to add even if Morris is currently in minimal mode — they'll fire at their scheduled times and transition correctly.
- **Config:** Add to `orchestrator_config.yaml`:

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

---

**End of Seed.** Ready to advance to Phase 4 (Analysis) on operator approval.
