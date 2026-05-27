# STORY-802 — Post-Rollout Verification (AC-6)

## Rollout Layer Status

| Layer | Scope | Status | Date | Verified By |
|-------|-------|--------|------|-------------|
| 1 | derrick pilot (config) | _pending_ | | |
| 2 | dan, devon, daisy, morris (config) | _pending_ | | |
| 3 | OPUS_PHASES {1, 9, 10} (sdlc_phase_runner.py) | **complete** | 2026-05-01 | Phase 8 commit |
| 4 | daily_cost_alert.sh + AlertService | **complete** | 2026-05-01 | Phase 8 commit |

## Code Changes Verified

- [x] `OPUS_PHASES = {1, 9, 10}` in `deployment/hermes/sdlc_phase_runner.py` (Phase 6 removed)
- [x] `daily_cost_alert.sh` created at `deployment/ops-console/scripts/`
- [x] AlertService surfaces `[COST_ALERT]` via `query_cost_alerts` Loki query
- [x] STORY-741 test `test_phase_6_includes_model_opus` updated to assert Phase 6 EXCLUDES `--model`
- [x] 32/32 STORY-802 tests GREEN (17 previously RED now GREEN + 15 regression guards)

## VM Config Verification (Operational — post-deploy)

| VM | Config Applied | Restart Persistence | state.db Sonnet | Compression Cascade <3 |
|----|---------------|--------------------|-----------------|-----------------------|
| derrick | _pending_ | _pending_ | _pending_ | _pending_ |
| dan | _pending_ | _pending_ | _pending_ | _pending_ |
| devon | _pending_ | _pending_ | _pending_ | _pending_ |
| daisy | _pending_ | _pending_ | _pending_ | _pending_ |
| morris | _pending_ | _pending_ | _pending_ | _pending_ |

## Daily Cost Alert Test

| Test | Result | Date |
|------|--------|------|
| Synthetic low-threshold trigger | _pending_ | |
| Dashboard surfaces [COST_ALERT] | _pending_ | |

## 7-Day Cost Gate (AC-6)

**Gate date:** 2026-05-08

**Criteria:** Daily Foundry spend <= $20/day for 7 consecutive days post-deploy.

| Date | Daily Spend | Pass/Fail |
|------|------------|-----------|
| 2026-05-01 | _pending_ | |
| 2026-05-02 | _pending_ | |
| 2026-05-03 | _pending_ | |
| 2026-05-04 | _pending_ | |
| 2026-05-05 | _pending_ | |
| 2026-05-06 | _pending_ | |
| 2026-05-07 | _pending_ | |

**If gate missed:** Dispatch a fix story to investigate which lever(s) failed and apply corrective action.

## Follow-ups

- [ ] 2026-05-08: Verify 7-day cost gate (manual check)
- [ ] Monitor daily_cost_alert.sh cron output for first week
- [ ] If Phase 6 Sonnet retry rate >20% within 48h, revert Phase 6 to OPUS_PHASES
