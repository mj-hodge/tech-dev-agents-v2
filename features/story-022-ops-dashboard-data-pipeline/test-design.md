# Phase 7: Test Design — STORY-022 Ops Dashboard Data Pipeline Fix

**Date:** 2026-04-07
**Scope:** Medium
**State:** RED (tests written, expected to fail before implementation)

---

## Test Strategy

Two test suites covering the three bug categories:

1. **Python tests** (`tests/test_story022_data_pipeline.py`) — Loki regex fix, agent detail cost enrichment, cost collector scheduling
2. **TypeScript tests** (updates to `tools/agent-ops-mcp/tests/tools.test.ts`) — MCP client envelope unwrapping

### AC-to-Test Mapping

| AC | Test | Suite |
|----|------|-------|
| AC-1: list_agents envelope | `test_list_agents_unwraps_envelope` | TS |
| AC-1: get_alerts envelope | `test_get_alerts_unwraps_envelope` | TS |
| AC-1: read_messages envelope | `test_read_messages_unwraps_envelope` | TS |
| AC-1: empty envelope handling | `test_list_agents_empty_envelope`, `test_get_alerts_empty_envelope`, `test_read_messages_empty_envelope` | TS |
| AC-2: regex turns-before-cost | `test_parse_done_line_turns_before_cost` | PY |
| AC-2: regex cost-before-turns | `test_parse_done_line_cost_before_turns` | PY |
| AC-2: regex auth failure skip | `test_parse_done_line_skips_auth_failure` | PY |
| AC-2: regex missing turns | `test_parse_done_line_missing_turns_defaults_zero` | PY |
| AC-2: cost collector timer | `test_cost_collector_systemd_timer_exists` | PY |
| AC-3: agent detail cost_7d/30d | `test_agent_detail_includes_cost_7d_30d` | PY |
| AC-3: agent detail graceful degradation | `test_agent_detail_cost_failure_returns_zero` | PY |
| AC-4: context returns all fields | `test_context_returns_teams_link_and_blocker` | PY |

**Total: 13 new tests** (9 TypeScript, 4 Python) + 4 existing test updates

---

## Test Files

### Python: `tests/test_story022_data_pipeline.py`

Tests the Loki [DONE] line regex fix and agent detail cost enrichment.

### TypeScript: `tools/agent-ops-mcp/tests/tools.test.ts`

Updates existing tests to use envelope responses (the real API contract) and adds new envelope-specific tests.
