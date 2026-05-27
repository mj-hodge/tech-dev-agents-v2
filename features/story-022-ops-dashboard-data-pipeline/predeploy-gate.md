# Phase 11: Pre-Deploy Gate -- STORY-022 Ops Dashboard Data Pipeline Fix

**Date:** 2026-04-07
**Scope:** Medium (bug fix)
**Branch:** story-021/work-queue
**Verdict:** PASS

---

## Gate Checklist

### 1. Tests Pass

**Status: PASS**

- **Python:** 18/18 passed (test_story022_data_pipeline.py: 11 tests, test_cost_collector.py: 7 tests). 0.31s runtime.
- **TypeScript:** 21/21 passed (tools.test.ts). 531ms runtime.
- No skipped, xfailed, or erroring tests.

### 2. No Secrets in Code

**Status: PASS**

Searched all changed files for hardcoded secrets, API keys, passwords, tokens, and credentials:
- `loki_client.py` -- No hardcoded secrets. API key passed via constructor parameter, used only in Bearer header.
- `responses.py` -- Pure Pydantic models, no credentials.
- `agents.py` -- No secrets. API key auth handled by `require_api_key` dependency.
- `client.ts` -- API key read from `process.env.OPS_API_KEY`, never hardcoded.
- `cost-collector.service` / `cost-collector.timer` -- No secrets. References file paths only.
- `test_story022_data_pipeline.py` -- No secrets. Uses mocks and model constructors only.

### 3. Dependencies Audit

**Status: PASS**

No changes to dependency files (`requirements.txt`, `pyproject.toml`, `package.json`) on this branch vs main. Zero new dependencies introduced. All imports (`httpx`, `asyncio`, `re`, `pydantic`, `fastapi`) are pre-existing.

### 4. Type Safety

**Status: PASS**

- **TypeScript:** No `any` types in changed code. Envelope unwrapping uses typed generics (`{ agents: AgentSummary[] }`, `{ messages: MessageItem[] }`, `{ alerts: AlertItem[] }`). Defensive `Array.isArray()` guards on all unwrapped fields.
- **Python:** All function signatures are fully typed. `_safe_cost_total()` returns `float`, `_resolve_current_work()` returns `tuple[str | None, str | None]`, `_parse_done_line()` returns `dict[str, Any] | None`. Pydantic models enforce field types at runtime.
- **Note:** `Record<string, unknown>` on `getAgentCost()` and `getAgentActivity()` is pre-existing and not part of this change.

### 5. Error Handling

**Status: PASS**

- **`_safe_cost_total()` (agents.py:38-45):** Wraps `cost_service.get_cost_breakdown()` in try/except with graceful degradation to `0.0`. Logs warning with traceback.
- **`_resolve_current_work()` (agents.py:48-76):** Two-level try/except -- Monday.com call and Loki call each independently wrapped. Falls through to `(None, None)`.
- **`list_agents` cost fetch (agents.py:121-125):** try/except around `cost_service.get_today_cost()` with `0.0` fallback.
- **`get_agent_detail` (agents.py:167-176):** Uses `asyncio.gather()` with `_safe_cost_total` helpers for graceful degradation on cost fetches.
- **`client.ts` request() (line 65-77):** All HTTP calls go through `request<T>()` which throws `OpsApiError` on non-2xx. Callers (MCP tool handlers) catch at the tool level.
- **`LokiClient.is_reachable()` (loki_client.py:200-211):** Catches `httpx.HTTPError` and returns `False`.

### 6. Backwards Compatibility

**Status: PASS**

All response model changes are additive with defaults:
- `AgentSummary.busy: bool = False` -- new field, defaults to `False`
- `AgentDetailResponse.cost_7d: float = 0.0` -- new field, defaults to `0.0`
- `AgentDetailResponse.cost_30d: float = 0.0` -- new field, defaults to `0.0`
- `FleetAgentSummary.busy: bool = False` -- new field, defaults to `False`
- `FleetOverviewResponse.busy_agents: int` -- new field (no default, but this is a server-rendered response, not a request model)

Existing API consumers will receive these as new JSON keys, which is non-breaking. No fields were removed or renamed. The TypeScript client envelope unwrapping fixes mismatches that were already present (the backend always sent envelopes; the client was incorrectly expecting bare arrays).

### 7. Deployment Safety

**Status: PASS**

Systemd unit review:

**cost-collector.service:**
- `Type=oneshot` -- correct for a periodic batch job
- `User=hermes` -- matches all other agent services on the VM
- `ExecStart=/usr/bin/python3 /opt/agent/cost_collector.py` -- follows the standard `/opt/agent/` deployment convention used by `claude_sdk_tool.py`, `health_server.py`, and all other agent scripts
- `--log-dir /tmp/claude-sdlc-logs` -- matches existing log directory used by Claude SDK sessions
- `StandardOutput=journal` / `StandardError=journal` -- standard systemd logging
- `SyslogIdentifier=cost-collector` -- enables `journalctl -t cost-collector` filtering
- No `[Install]` section -- correct; the timer unit handles enablement

**cost-collector.timer:**
- `OnCalendar=*-*-* 23:55:00` -- runs daily at 23:55 UTC (end of day, before midnight rollover)
- `Persistent=true` -- catches up on missed runs after downtime
- `WantedBy=timers.target` -- standard timer enablement

**Deployment note:** The `cost_collector.py` script lives at `tech_dev_agents/cost_collector.py` in the repo and must be deployed to `/opt/agent/cost_collector.py` on VMs. This follows the same pattern as all other agent scripts (pushed via `agent-push.sh`).

### 8. Documentation

**Status: PASS**

- `features/story-022-ops-dashboard-data-pipeline/feature-spec.md` -- exists, covers all 3 change sets (MCP client envelope unwrapping, regex fix, agent detail cost enrichment)
- `features/story-022-ops-dashboard-data-pipeline/test-design.md` -- exists, includes AC-to-test mapping table, test strategy, and both Python/TypeScript test suites
- Additional deliverables present: `seed.md`, `analysis.md`, `security-review.md`, `ux-review.md`, `ops-review.md`, `code-review.md`

---

## Summary

| # | Check | Result |
|---|-------|--------|
| 1 | Tests pass | PASS (18 PY + 21 TS = 39 tests green) |
| 2 | No secrets in code | PASS |
| 3 | Dependencies audit | PASS (no new deps) |
| 4 | Type safety | PASS (no `any`, all signatures typed) |
| 5 | Error handling | PASS (all external calls wrapped) |
| 6 | Backwards compatibility | PASS (additive fields with defaults) |
| 7 | Deployment safety | PASS (systemd units follow conventions) |
| 8 | Documentation | PASS (feature-spec + test-design + 6 more) |

---

## Verdict: PASS

All 8 gate checks passed. STORY-022 is safe to deploy. No blocking issues, no conditional items.

**Deployment steps:**
1. Push `cost_collector.py` to `/opt/agent/` on all agent VMs via `agent-push.sh`
2. Install `cost-collector.service` and `cost-collector.timer` to `/etc/systemd/system/`
3. Run `systemctl daemon-reload && systemctl enable --now cost-collector.timer`
4. Deploy Python backend (ops console) and MCP server as usual
