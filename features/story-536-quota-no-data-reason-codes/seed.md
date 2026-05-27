# STORY-536: Quota no_data reason-code classification + telemetry

> Phase 1 | Scope: **Small** | Created: 2026-04-23
> Advance: auto (automated dispatch path — 1 → 7 → 8+PR → Done)

---

## Problem Statement

`LokiClient.query_agent_quota()` (STORY-513) collapses three very different failure modes into a single `source="no_data"` return value:

1. **`LokiError`** — Loki is unreachable / returned HTTP 5xx / request timed out. An infrastructure problem.
2. **Empty result** — Loki responded successfully but had zero matching entries for `{agent="<name>"} |= "[USAGE]"`. Either the agent has been offline for 5+ hours, the log shipper is broken on that VM, or the agent is brand-new.
3. **Parse miss** — Loki returned entries, but none of the log lines passed `_parse_usage_line` (every regex failed). Indicates a log-format drift between emitter (`claude_sdk_tool.py`) and parser.

From the outside, these three states are indistinguishable. The dashboard renders "no data" in all three cases and we have no way to know whether the cause is Loki being down, an offline agent, or a silent parser regression. That last case is the most dangerous: an agent can appear quota-idle forever while actually consuming tokens, because we failed to parse its `[USAGE]` lines.

We need internal classification + telemetry so operators can distinguish the three cases from logs and metrics — without breaking any external consumer that already treats `source="no_data"` as a single state.

---

## Target User

- **Mark** — ops operator who needs to triage "why is agent X's quota showing no data?" without grepping Loki directly
- **Morris** — fleet-vigilance needs to know the difference between "agent offline" (benign) and "parser broken" (incident)
- **Future us** — any time we change the `[USAGE]` log format, we need a canary signal that parsing has drifted

---

## Acceptance Criteria

1. **AC-1 — Internal reason enum.** Introduce a new internal enum `QuotaNoDataReason` with three members: `LOKI_ERROR`, `EMPTY_RESULT`, `PARSE_MISS`. Lives in `tech_dev_agents/ops_console/services/loki_client.py` (internal, not exported through the public response models). Do NOT add this to the `QuotaInfo` public schema.

2. **AC-2 — Classify each no_data branch.** `query_agent_quota` must tag each of its three current `return QuotaInfo(source=QuotaSourceEnum.NO_DATA)` sites with the corresponding reason:
   - `LokiError` caught → `LOKI_ERROR`
   - `not entries` → `EMPTY_RESULT`
   - `sessions == 0` (entries present but unparsed) → `PARSE_MISS`

3. **AC-3 — Structured telemetry.** Each no_data branch emits a `logger.warning` with structured fields `agent=<name>`, `reason=<reason_code>`, and `entries_seen=<int>` (0 for LOKI_ERROR and EMPTY_RESULT; actual count for PARSE_MISS). Log message format: `"quota no_data agent=%s reason=%s entries_seen=%d"`. Grep-friendly so Grafana/Loki dashboards can slice by reason.

4. **AC-4 — Counter hook.** Add a module-level `Counter`-style registry (plain `dict[str, int]` keyed by reason string is sufficient for Small scope — no Prometheus dependency) with a helper `get_no_data_counters() -> dict[str, int]` for tests and future `/health` wiring. Counters increment once per branch hit. Reset helper `_reset_no_data_counters()` for test isolation.

5. **AC-5 — Public API contract unchanged.** `QuotaInfo.source` continues to be `QuotaSourceEnum.NO_DATA` in all three cases. `QuotaSourceEnum` is NOT extended. No new fields on `QuotaInfo`. Response bodies for `GET /api/agents/{name}/quota` are byte-for-byte identical before and after this change. Existing tests in `tests/ops_console/test_routes_agents_quota_loki.py` and `tests/ops_console/test_loki_client_quota.py` pass without modification.

6. **AC-6 — Tests for every branch.** New test file (or additions to `test_loki_client_quota.py`) covers:
   - Loki raises `LokiError` → counter `loki_error` = 1, log contains `reason=loki_error`, response `source="no_data"`
   - Loki returns `[]` → counter `empty_result` = 1, log contains `reason=empty_result`, response `source="no_data"`
   - Loki returns 3 entries, none parseable → counter `parse_miss` = 1, log contains `reason=parse_miss entries_seen=3`, response `source="no_data"`
   - Happy path (populated quota) does NOT increment any counter
   - Each test resets counters via `_reset_no_data_counters()` in setup
   - Each test asserts the external `QuotaInfo.source == QuotaSourceEnum.NO_DATA` to prove public-contract compatibility

7. **AC-7 — No regression in quota route.** `tests/ops_console/test_routes_agents_quota_loki.py` still passes. The route handler is not touched in this story.

---

## Scope Classification

**Small** — additions are confined to `loki_client.py` (one enum, three `logger.warning` calls, one counter dict, one helper) plus one test file. No schema changes, no API changes, no config changes, no migrations, no cross-service coordination, no new dependencies.

Phase path (automated dispatch): **1 → 7 → 8+PR → Done**.

---

## Technical Notes

### Current code (`tech_dev_agents/ops_console/services/loki_client.py` lines 351–410)

Three `return QuotaInfo(source=QuotaSourceEnum.NO_DATA)` call sites exist today:

```python
except LokiError:
    logger.warning("Loki quota query failed for agent %s", agent_name, exc_info=True)
    return QuotaInfo(source=QuotaSourceEnum.NO_DATA)   # → LOKI_ERROR

if not entries:
    return QuotaInfo(source=QuotaSourceEnum.NO_DATA)   # → EMPTY_RESULT

# ... parsing loop ...
if sessions == 0:
    return QuotaInfo(source=QuotaSourceEnum.NO_DATA)   # → PARSE_MISS
```

Wrap each of these in a small helper:

```python
def _no_data(reason: QuotaNoDataReason, agent: str, entries_seen: int = 0) -> "QuotaInfo":
    _NO_DATA_COUNTERS[reason.value] += 1
    logger.warning(
        "quota no_data agent=%s reason=%s entries_seen=%d",
        agent, reason.value, entries_seen,
    )
    return QuotaInfo(source=QuotaSourceEnum.NO_DATA)
```

Each of the three existing return sites becomes one-liner call to `_no_data(...)`.

### Reason code string values

| Enum member | String value (used in logs + counter keys) |
|-------------|--------------------------------------------|
| `LOKI_ERROR` | `"loki_error"` |
| `EMPTY_RESULT` | `"empty_result"` |
| `PARSE_MISS` | `"parse_miss"` |

Keep strings snake_case so Loki label filters are consistent with existing conventions.

### Counter dict shape

```python
_NO_DATA_COUNTERS: dict[str, int] = {
    "loki_error": 0,
    "empty_result": 0,
    "parse_miss": 0,
}
```

Module-level, mutated by `_no_data()`, inspected by `get_no_data_counters()` (returns a shallow copy to prevent external mutation).

### Why not add to QuotaInfo?

- AC-5 requires zero public-contract change. The dashboard, Morris fleet-vigilance, and `tests/test_508_frontend.py` all consume `source="no_data"` today; changing the shape risks silent breakage.
- Reason codes are an ops/telemetry concern, not a user-facing one. They belong in logs + internal counters, not in the REST response.
- If future stories need the reason in the response, that's a separate contract change with its own review.

### Key files

| File | Change |
|------|--------|
| `tech_dev_agents/ops_console/services/loki_client.py` | Add `QuotaNoDataReason` enum, counter dict, `_no_data` helper, wire into 3 branches |
| `tests/ops_console/test_loki_client_quota.py` | Extend with 4 new test cases (3 branches + happy-path-no-increment) |
| `features/story-536-quota-no-data-reason-codes/test-design.md` | Phase 7 deliverable |

### Out of scope

- Exposing reason codes through `QuotaInfo` or any response model (future story if needed)
- Prometheus / OpenTelemetry metrics — local counter dict is sufficient for Small scope
- Grafana dashboard changes — telemetry is emitted, dashboard wiring is separate
- Changes to `claude_sdk_tool.py` or the `[USAGE]` log-line format
- Health endpoint exposure of counters (noted as a future lift; helper signature designed to support it)

---

## Success Measure

After deploy: ops can tail the ops-console VM log and grep for `quota no_data reason=parse_miss` to instantly spot parser drift. Unit tests assert all three branches increment their dedicated counter. Public `/api/agents/{name}/quota` response is byte-identical to pre-change for all three no_data scenarios. No changes visible to dashboard, Morris, or external consumers.

---

## Follow-ups (not this story)

- Wire `get_no_data_counters()` into `/health` response so Grafana can scrape it
- Add a Grafana alert rule: `parse_miss > 0 over 15m` → page (silent parser regression signal)
- Consider promoting reason codes into the public `QuotaInfo` schema once consumer demand justifies the contract change
