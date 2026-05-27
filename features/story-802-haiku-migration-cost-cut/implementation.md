# Phase 8 Implementation — STORY-802: Haiku/Sonnet Migration Cost Cut

## Summary

All Phase 7 tests are GREEN (32/32). Three implementation components were delivered:

## Component 1: OPUS_PHASES Fix (AC-3)

**File:** `deployment/hermes/sdlc_phase_runner.py`

Changed `OPUS_PHASES` from `{1, 6, 9, 10}` to `{1, 9, 10}`. Phase 6 (Design) was removed
from the Opus-always set since small/medium stories (the majority) run acceptably on Sonnet.
The phase runner logs "Sonnet (execution phase)" for Phase 6 instead of "Opus (reasoning phase/scope)".

The scope-aware override `(phase_num == 6 and scope in ("medium", "large", "new"))` was also
removed — per CLAUDE.md Model Policy, Phase 6 uses Sonnet as default (Opus only for large
scope, handled by the agent persona's Model Gate, not the SDK caller).

## Component 2: Daily $20 Cost Alert Script (AC-7)

**File:** `deployment/ops-console/scripts/daily_cost_alert.sh`

New cron script that runs at 18:00 UTC daily:
- Queries `foundry_cost_daily` table via `psql`
- Emits `[COST_ALERT] ALERT` line if `opus_usd + sonnet_usd + haiku_usd + other_usd > $20`
- Emits `[COST_ALERT] OK` line on normal spend
- Handles empty `TOTAL` (returns `$0.00`, no false alert)
- Uses `FOUNDRY_DAILY_THRESHOLD` env var (default `20.00`)
- Uses `set -euo pipefail` strict mode

Cron entry: `0 18 * * * /opt/ops-console/scripts/daily_cost_alert.sh >> /var/log/ops-console/cost_alert.log 2>&1`

## Component 3: AlertService [COST_ALERT] Integration (AC-7)

**File:** `tech_dev_agents/ops_console/services/alert_service.py`

Added a 4th block in `_get_loki_alerts()` that calls `self._loki.query_cost_alerts()` and maps
results to `AlertItem` objects with:
- `type = "cost_alert"`
- `severity = "high"`
- `source = "loki"`
- Graceful degradation: Loki failures are logged as warnings, other alert sources continue

## Component 4: Operational Deliverables

**Files:**
- `features/story-802-haiku-migration-cost-cut/audit.md` — per-VM config audit table with verification commands
- `features/story-802-haiku-migration-cost-cut/verification.md` — post-rollout verification template + 7-day cost gate (2026-05-08)

## Test Results

| Test File | Tests | Result |
|-----------|-------|--------|
| `tests/deployment/test_story_802_opus_phases.py` | 13 | ✅ GREEN |
| `tests/deployment/test_story_802_daily_cost_alert_script.py` | 10 | ✅ GREEN |
| `tests/ops_console/test_story_802_cost_alert.py` | 9 | ✅ GREEN |
| `tests/deployment/test_sdk_model_cli_contract.py` | 12 | ✅ GREEN (STORY-741 contract updated) |
| `tests/test_sdlc_framework_compliance.py` | 25 | ✅ GREEN |
| **Total** | **69** | **✅ 69/69** |

## Acceptance Diff Verification

All files named in the spec are present in `git diff origin/main --name-only`:
- `deployment/hermes/sdlc_phase_runner.py` ✓
- `deployment/ops-console/scripts/daily_cost_alert.sh` ✓
- `tech_dev_agents/ops_console/services/alert_service.py` ✓
- `features/story-802-haiku-migration-cost-cut/audit.md` ✓
- `features/story-802-haiku-migration-cost-cut/verification.md` ✓

## PR

PR #256: `feat(STORY-802): drive Foundry spend ≤$20/day via Haiku/Sonnet migration`

## Follow-ups

- AC-6 cost gate: verify daily Foundry spend ≤ $20/day on 2026-05-08
- VM config rollout (AC-2, AC-4, AC-5): operational task — see `audit.md` for commands, `verification.md` for verification steps
- If cost gate missed on 2026-05-08, dispatch a fix story
