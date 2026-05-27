# Test Design — STORY-015: Deploy Agent Dashboards & Wiring

## Test Strategy

All new modules use pure functions with injected dependencies. Tests verify business logic without HTTP or filesystem dependencies.

## Test Matrix

### health_api.py (SC-4, SC-5) — 8 tests
| # | Test | SC |
|---|------|----|
| 1 | `build_health_response` returns correct structure | SC-4 |
| 2 | `build_health_response` includes version field | SC-4 |
| 3 | `build_health_response` derives status from last_activity | SC-4 |
| 4 | `validate_api_key` accepts valid key | SC-5 |
| 5 | `validate_api_key` rejects missing key | SC-5 |
| 6 | `validate_api_key` rejects wrong key | SC-5 |
| 7 | `build_restart_response` returns success result | SC-5 |
| 8 | `build_restart_response` returns failure on error | SC-5 |

### cost_collector.py (SC-2) — 7 tests
| # | Test | SC |
|---|------|----|
| 1 | Parse single DONE line extracts cost | SC-2 |
| 2 | Parse multiple DONE lines sums correctly | SC-2 |
| 3 | Parse ignores non-DONE lines | SC-2 |
| 4 | Parse handles missing cost field gracefully | SC-2 |
| 5 | `format_cost_summary_log` produces correct format | SC-2 |
| 6 | `collect_costs_from_logs` with empty dir returns zero summary | SC-2 |
| 7 | `collect_costs_from_logs` with real log files aggregates | SC-2 |

### monday_hooks.py (SC-3) — 6 tests
| # | Test | SC |
|---|------|----|
| 1 | `on_story_start` returns In Progress transition | SC-3 |
| 2 | `on_story_complete` returns Done transition + comment | SC-3 |
| 3 | `on_phase_complete` builds correct PhaseComment | SC-3 |
| 4 | `on_story_start` with empty agent_name raises | SC-3 |
| 5 | `on_story_complete` includes summary in comment | SC-3 |
| 6 | `on_phase_complete` formats duration correctly | SC-3 |

### grafana dashboard (SC-1, SC-6) — 3 tests
| # | Test | SC |
|---|------|----|
| 1 | Dashboard JSON is valid JSON | SC-1 |
| 2 | Dashboard has required panels (cost, sessions, status) | SC-1 |
| 3 | Dashboard has restart link panel with URL template | SC-6 |

**Total: 24 tests**
