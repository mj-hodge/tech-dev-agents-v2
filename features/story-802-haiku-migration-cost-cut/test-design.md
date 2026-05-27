# Test Design — STORY-802: Haiku/Sonnet Migration Cost Cut

## Overview

| Field | Value |
|-------|-------|
| Story | STORY-802 |
| Scope | Medium |
| Coverage Target | 60% |
| Test Files | 3 |
| Total Tests | 32 (17 RED, 15 PASS regression guards) |

## Test Structure

```
tests/
├── deployment/
│   ├── test_story_802_opus_phases.py        # AC-3: OPUS_PHASES = {1, 9, 10}
│   └── test_story_802_daily_cost_alert_script.py  # AC-7: daily_cost_alert.sh
└── ops_console/
    └── test_story_802_cost_alert.py         # AC-7: AlertService [COST_ALERT]
```

## Component Coverage

### Component 1: VM Config Audit & Fix
Not testable in code — operational task verified by `audit.md` and `verification.md` deliverables (AC-1, AC-2, AC-4, AC-5).

### Component 2: OPUS_PHASES Fix (AC-3)
**File:** `tests/deployment/test_story_802_opus_phases.py`

| Test | What It Verifies | Status |
|------|------------------|--------|
| `test_opus_phases_is_1_9_10` | Source contains `OPUS_PHASES = {1, 9, 10}` | RED |
| `test_opus_phases_does_not_contain_6` | Phase 6 not in the set | RED |
| `test_phase_6_excludes_model_flag` | Phase 6 cmd has no `--model` | RED |
| `test_phase_6_log_says_sonnet` | Phase 6 logs "Sonnet/execution phase" | RED |
| `test_opus_phase_includes_model_opus[1]` | Phase 1 still gets `--model opus` | PASS |
| `test_opus_phase_includes_model_opus[9]` | Phase 9 still gets `--model opus` | PASS |
| `test_opus_phase_includes_model_opus[10]` | Phase 10 still gets `--model opus` | PASS |
| `test_sonnet_phase_excludes_model_flag[2-8]` | Phases 2-5,7,8 have no `--model` | PASS (7 tests) |
| `test_phase_1_and_phase_7_produce_different_model_flags` | Output variance: Opus ≠ Sonnet cmd shapes | PASS |

**RED reasons:** OPUS_PHASES currently `{1, 6, 9, 10}` — Phase 6 is still included. Removing 6 from the set makes all 4 RED tests GREEN.

### Component 3: Daily Cost Alert (AC-7)

#### Shell Script — `tests/deployment/test_story_802_daily_cost_alert_script.py`

| Test | What It Verifies | Status |
|------|------------------|--------|
| `test_script_file_exists` | Script exists at expected path | RED |
| `test_script_has_bash_shebang` | Valid bash shebang | RED |
| `test_uses_cost_alert_log_tag` | Contains `[COST_ALERT]` | RED |
| `test_default_threshold_is_20` | $20 default + FOUNDRY_DAILY_THRESHOLD env | RED |
| `test_queries_foundry_cost_daily_table` | Queries foundry_cost_daily | RED |
| `test_uses_database_url_env_var` | Uses DATABASE_URL | RED |
| `test_emits_alert_on_threshold_exceeded` | ALERT log on exceeded | RED |
| `test_emits_ok_on_normal_spend` | OK log on normal | RED |
| `test_handles_empty_total_gracefully` | Empty TOTAL → $0.00 | RED |
| `test_uses_set_euo_pipefail` | Strict bash mode | RED |

**RED reason:** Script doesn't exist yet — all 10 tests fail.

#### AlertService Integration — `tests/ops_console/test_story_802_cost_alert.py`

| Test | What It Verifies | Status |
|------|------------------|--------|
| `test_cost_alert_surfaced_in_get_alerts` | [COST_ALERT] appears in merged alerts | RED |
| `test_cost_alert_has_correct_severity` | severity='high' | PASS (no cost_alerts to check) |
| `test_cost_alert_message_contains_threshold_info` | Message has threshold info | RED |
| `test_cost_alert_source_is_loki` | source='loki' | PASS (vacuously) |
| `test_filter_by_cost_alert_type` | type filter works | PASS (vacuously) |
| `test_no_cost_alerts_returns_empty` | No alerts → empty | PASS |
| `test_loki_cost_alert_failure_does_not_crash` | Graceful degradation | PASS |
| `test_cost_alert_and_cost_anomaly_are_different_types` | Output variance: two distinct types | RED |

**RED reason:** AlertService doesn't query `query_cost_alerts` yet — `cost_alert` type never appears in merged results.

### Components 4-5: Verification / Follow-Up
Documentation deliverables — not testable in code.

## Defensive Gates

### Gate 2b: External API Degradation
- `test_loki_cost_alert_failure_does_not_crash` — Loki query failure handled gracefully

### Gate 10: Error Observability
- Alert script logs both ALERT and OK states with structured tags
- AlertService wraps Loki queries in try/except with `logger.warning`

## Output-Variance Tests
- `test_phase_1_and_phase_7_produce_different_model_flags` — Opus vs Sonnet produce different cmd shapes
- `test_cost_alert_and_cost_anomaly_are_different_types` — cost_alert ≠ cost_anomaly

## LLM Error-Prone Coverage
| Category | Test |
|----------|------|
| Boundary conditions | Phase 6 boundary (was in set, now removed) |
| Edge cases | Empty TOTAL in shell script, missing Loki results |
| Output format | Alert severity, type, source fields |
| Library API misuse | Loki query method existence (query_cost_alerts) |

## Notes for Phase 8 Implementer

1. **OPUS_PHASES change** is a 1-line edit in `sdlc_phase_runner.py` line 1568
2. **Existing STORY-741 test** `test_phase_6_includes_model_opus` in `test_sdk_model_cli_contract.py` will break — update it to assert Phase 6 EXCLUDES `--model` (or delete it, as our new test covers this)
3. **AlertService** needs a new `query_cost_alerts` method on LokiClient + a 4th block in `_get_loki_alerts()` following the same pattern as the existing 3 blocks
4. **Shell script** is already fully specified in feature-spec.md Component 3 — copy and commit

## RED State Summary

```
17 FAILED, 15 passed, 0 errors in 0.18s
```

All 17 failures are for the right reasons:
- 4 fail because OPUS_PHASES still contains 6
- 10 fail because daily_cost_alert.sh doesn't exist
- 3 fail because AlertService doesn't surface [COST_ALERT] yet
