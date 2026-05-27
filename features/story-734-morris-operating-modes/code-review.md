# Code Review — STORY-734: Morris Operating Modes (Quota-Aware + Overnight Scheduling)

**Story:** STORY-734
**Phase:** 8 (Implementation) — Self-Review
**Date:** 2026-04-27
**Author:** Derrick (tech-agent-derrick@gorillacommerce.co)

---

## 1. Files Changed

### New Files
| File | Lines | Purpose |
|------|-------|---------|
| `deployment/morris/scripts/mode_controller.py` | ~230 | Core mode management module |
| `deployment/morris/scripts/set_mode.py` | ~80 | CLI for mode transitions |
| `tests/deployment/test_morris_operating_modes_734.py` | ~490 | 29 unit tests covering all 12 TCs |

### Edited Files
| File | Change Summary |
|------|---------------|
| `deployment/morris/scripts/orchestrator_loop.py` | Added `check_and_auto_transition()` call, mode gates for briefing and minimal mode, subagent suppression in light mode, `mode` param to `execute_interventions()` |
| `deployment/morris/scripts/daily_contact_summary.py` | Added import-path bootstrap and mode gate at startup |
| `deployment/morris/scripts/orchestrator_config.yaml` | Added `operating_modes` section with all thresholds |
| `deployment/morris/scripts/install-orchestrator-cron.sh` | Added overnight mode cron block (marker-guarded) |
| `deployment/vm/morris-fleet-check.sh` | Added mode gate before Stage 2 Claude invocation |
| `deployment/vm/morris-crontab.txt` | Documented new overnight cron entries |

## 2. Design Review

### Correctness
- **Transition logic** matches seed spec exactly: thresholds at 0.70/0.90, recovery at 0.60/0.50, hysteresis prevents oscillation
- **Fail-open** on quota unavailability — no false restrictions
- **Atomic writes** via `tempfile` + `os.rename()` prevent state corruption
- **Mark cooldown** respects 120-minute window, auto-transition resumes after

### Backwards Compatibility
- `execute_interventions()` new `mode` param defaults to `"full"` — existing callers unaffected
- `daily_contact_summary.py` mode gate wrapped in `try/except ImportError` — works if `mode_controller.py` not yet deployed
- `morris-fleet-check.sh` mode gate uses `|| echo "yes"` fallback — runs normally if mode_controller missing
- All existing 64 orchestrator tests continue to pass

### Security
- No new secrets or credentials introduced
- Mode state file is plain JSON — no sensitive data
- Quota endpoint uses existing ops console auth (session headers)

### Operational Safety
- **Fail-open everywhere**: quota check failure → no transition; mode file missing → default to `full`
- **Idempotent crons**: marker-guarded installation prevents duplicate entries
- **Graceful degradation**: if mode_controller.py is missing on deploy, existing scripts run unchanged

## 3. Test Coverage

29 tests covering all 12 seed test criteria plus edge cases:
- TC-1 through TC-12: all explicitly tested
- Edge cases: corrupt JSON, missing file, invalid mode, unavailable quota source
- Integration: orchestrator main() with minimal and light mode gates

## 4. Identified Risks

| Risk | Assessment | Mitigation |
|------|-----------|------------|
| First deploy — mode_state.json doesn't exist | Low | `get_mode()` defaults to `"full"` |
| `set_mode.py` cron runs before orchestrator_loop imports are available | Low | Wrapped in try/except; DM may not send but mode still persists |
| Concurrent cron writes to mode_state.json | Very Low | Atomic rename pattern; 10-min cron interval makes collision unlikely |

---

**Review verdict:** PASS — implementation matches spec and all tests green.
