# Seed

## Overview
| Field | Value |
|-------|-------|
| Mode | feature_update |
| Scope | small |
| Feature Name | Presence endpoint partial-failure resilience + ops runbook update |
| Rework Of | STORY-549, PR #99 |

## Problem Statement

PR #99 (STORY-549 — Unified Agent Presence) rewrites `GET /api/agents/presence` to derive state from fleet health probes instead of SSH. The new route handler calls `asyncio.gather(*tasks, return_exceptions=False)`, meaning a single agent probe failure (e.g. timeout, DNS resolution error, transient network blip) propagates as an unhandled exception and returns HTTP 500 for the entire endpoint. All agents' presence data is lost because of one agent's failure. Additionally, PR #99's deployment prerequisite (`Presence.ReadWrite.All` Graph application permission) is not documented in the ops runbook, so the next deploy will fail silently when Teams presence pushes get 403s.

## Target User / Use Case

- **Mark** — views the ops dashboard (`ops.gorillacommerce.ai`) to check agent presence. A single-agent transient failure should not blank the entire presence panel; partial results (N-1 agents showing state, 1 showing OFFLINE with a diagnostic detail) are far more useful than a 500 error.
- **Morris** — calls `/api/agents/presence` programmatically. A 500 blows up his retry logic; partial results let him make routing decisions for the agents that are healthy.
- **Ops deployer** — needs a documented prerequisite checklist so `Presence.ReadWrite.All` is granted before the ops-console container is redeployed with STORY-549 code.

## Success Criteria

- [ ] **AC-1 (gather resilience):** `asyncio.gather` in `GET /api/agents/presence` uses `return_exceptions=True`. When one or more agent probe tasks raise an exception, the endpoint still returns HTTP 200 with partial results: successful agents get their fleet-derived state, failed agents get `state=OFFLINE` with `detail` containing the exception type/message.
- [ ] **AC-2 (warning logging):** Each probe exception is logged at WARNING level with the agent name and exception message (not swallowed silently).
- [ ] **AC-3 (partial results test):** A new unit test verifies partial-result behavior: given N agents where one probe raises an exception, the response contains N `AgentPresence` entries — (N-1) with fleet-derived state and 1 with `state=OFFLINE` and a non-empty `detail`.
- [ ] **AC-4 (ops runbook):** The `docs/azure-predeploy-setup.md` ops runbook includes a section or checklist item documenting that `Presence.ReadWrite.All` **application** permission must be granted and admin-consented on the ops-console Graph app registration before deploying STORY-549.
- [ ] **AC-5 (rebase clean):** PR is rebased on `main` with no merge conflicts, CI green.
- [ ] **AC-6 (error handling — fail open):** The endpoint fails open: transient dependency failures degrade to OFFLINE for affected agents rather than returning 500. Probe exceptions are logged at WARNING level (AC-2). The system does NOT fail closed — partial data is always better than no data for an observability endpoint.

## Constraints
| Constraint | Value |
|------------|-------|
| Budget | $0 — code fix only |
| Timeline | < 1 day |
| Scale | 5-8 agents, < 1 req/s on this endpoint |

## Security Constraints (Non-Negotiable)

- [x] All database queries MUST use parameterized queries — N/A (no DB queries)
- [x] All user input MUST be validated and sanitized before use — N/A (no user input changes)
- [x] All API endpoints MUST require authentication — existing `require_auth` dependency preserved
- [x] Sensitive data (passwords, tokens, PII) MUST NOT appear in logs — exception details logged at WARNING contain only exception type and message, no credentials
- [x] All secrets MUST come from environment variables, never hardcoded — no new secrets
- [x] All file uploads MUST be validated for type and size — N/A
- [x] All API responses MUST NOT expose internal error details to clients — `detail` field contains only the exception class name and message (e.g. "fleet fetch failed: TimeoutError"), not stack traces

## Operational Lifecycle
- **What configuration might change?** No new configuration. Existing `OPS_PRESENCE_*` env vars from STORY-549 are unchanged.
- **How will operators make changes?** N/A for this fix. The runbook update (AC-4) is a one-time documentation addition.
- **What monitoring confirms it's working?** WARNING-level log entries when a probe fails (instead of ERROR-level 500s). The `/api/agents/presence` endpoint returns 200 with partial results instead of 500.

## Codebase Context (Feature Updates Only)
| Aspect | Details |
|--------|---------|
| Affected files | `tech_dev_agents/ops_console/routes/presence.py` (on STORY-549 branch), `docs/azure-predeploy-setup.md` |
| Related components | `routes/fleet.py` (`_fetch_agent_summary`), `services/presence_mapping.py`, `models/responses.py` (`AgentPresence`, `AgentPresenceListResponse`, `PresenceState`) |
| Current behavior (on PR #99 branch) | `asyncio.gather(*tasks, return_exceptions=False)` — single failure → 500 for all |
| Desired change | `asyncio.gather(*tasks, return_exceptions=True)` + filter results: exceptions → `AgentPresence(state=OFFLINE, detail=...)` + `logger.warning(...)` |
| Test coverage | PR #99 has 78 tests across 6 files; none cover the partial-failure path |
| Architecture constraints | Must preserve the existing `AgentPresenceListResponse` response shape; no new dependencies |

## Scope Classification

**Small** — single function fix (change `return_exceptions` flag + add exception-handling loop), one new test case, one runbook paragraph. No DB migration, no new modules, no config changes. Fits the Small phase path: `1 → 7 → 8 → Done`.

## Test Criteria

- Partial failure: mock one agent's probe to raise `TimeoutError`, verify response is 200 with all agents present (N-1 fleet-derived + 1 OFFLINE with detail)
- All-fail: mock all probes to raise, verify response is 200 with all agents OFFLINE
- Warning log: verify `logger.warning` is called for each failed probe with agent name

## Validation

- All existing STORY-549 tests (78) remain GREEN after the fix
- New partial-failure test passes
- `docs/azure-predeploy-setup.md` contains `Presence.ReadWrite.All` prerequisite
- PR rebases cleanly on main, CI green
