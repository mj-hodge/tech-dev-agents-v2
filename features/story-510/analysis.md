# STORY-510: Harden `/api/agents/{name}/quota` — Analysis

**Phase:** 4 (Analysis) — Medium scope
**Inputs:** `features/story-510/seed.md`, current implementation (STORY-496), observed failure modes on Morris fleet-check runs 2026-04-18 → 2026-04-21.

---

## Goal Recap

Make `GET /api/agents/{name}/quota` robust against every observed `ccusage`-related failure mode, add a second endpoint `GET /api/agents/{name}/quota/weekly` with the same graceful-fallback contract, and unify the data source so Morris, the dashboard, and the poller all read the same shape.

---

## Affected Files

### Route / HTTP layer

| File | Purpose | Change Type |
|------|---------|-------------|
| `tech_dev_agents/ops_console/routes/agents.py` | Agents routes (list/detail/cost/activity/**quota**/restart/pause) | MODIFY `get_agent_quota` + `_fetch_agent_quota`; ADD `get_agent_quota_weekly` + `_fetch_agent_quota_weekly`; EXTRACT an injectable `_SSHRunner` callable so tests can mock subprocess I/O; PRESERVE `agent_quota_enabled` 404 gate for **both** endpoints |
| `tech_dev_agents/ops_console/models/responses.py` | Pydantic response schemas | EXTEND `QuotaInfo` (new fields: `source`, `current_block_tokens`, `current_block_cost_usd`, `time_remaining_minutes`, `pacing_status`); ADD `QuotaDaily`, `QuotaWeeklyResponse`, `QuotaSourceEnum`, `PacingStatusEnum` |
| `tech_dev_agents/ops_console/cache.py` | TTL cache | NO CHANGE — reuse existing `TTLCache(300)` for both quota + weekly keys |

### Agent-side script (runs on each dev-agent VM)

| File | Purpose | Change Type |
|------|---------|-------------|
| `scripts/quota_ccusage.py` | **NEW** — ccusage-backed quota probe | ADD. Probes `shutil.which("ccusage")` first; on miss → emits `{"source": "unavailable", ...}` + exit 0. Runs `ccusage blocks --json` for current block; runs `ccusage --period weekly --format json` for weekly trend; tolerates empty stdout, non-JSON lines, and missing `activeBlock` key |
| `scripts/quota_check.py` | Existing JSONL-based probe (STORY-496) | **KEEP unchanged** as a hybrid fallback path. See "Decision: hybrid, not replacement" below |
| `deployment/vm/push-code.sh` | Deploy script | NO CODE CHANGE, but the new `scripts/quota_ccusage.py` will be picked up by the existing rsync rule (scripts are synced to `/opt/agent/`). Verified by reading the deploy-script layout. |

### Consumers (read/trigger only — **not modified in this story**)

| File | Role | Notes |
|------|------|-------|
| `deployment/hermes/dispatch_poller.py` (lines 870–890) | Pre-claim ccusage probe | Already has `try/except pass`. Not changed — but will continue to work because `ccusage blocks --json` is still the canonical source. |
| `deployment/vm/morris-fleet-check.sh` (line 88) | Shells out to `ccusage --period weekly --format json` directly | **Out of scope** per seed — tracked as follow-up story. Acceptance Criterion #8 explicitly marks migration as deferred. |
| `deployment/vm/skills/fleet-vigilance/SKILL.md` | Documents ccusage workflow | No change; doc follow-up only. |
| `frontend/src/**` | Dashboard quota bar + (new) weekly sparkline | Out of scope per seed. Frontend work will pick up once this endpoint is live. |

### Tests

| File | Purpose | Change Type |
|------|---------|-------------|
| `tests/ops_console/test_routes_agents_quota.py` | **NEW** — unit tests for both quota endpoints | ADD. Mocks the injectable SSH-runner. 9 test cases (see AC #7). |
| `tests/ops_console/conftest.py` | Shared fixtures | MINOR — add `agent_quota_enabled=True` to `test_settings` via an override fixture so the new tests can hit the endpoints. (Alternative: per-test `Settings` override; decided in Phase 7.) |
| `tests/scripts/test_quota_ccusage.py` | **NEW** — unit tests for the agent-side helper | ADD. Exercises the subprocess-runner layer with mocked `ccusage` output (healthy, empty, malformed, missing binary). |
| `tests/ops_console/fixtures/ccusage_*.json` | **NEW** — canned ccusage outputs | ADD 4 fixture files: `ccusage_blocks_healthy.json`, `ccusage_blocks_empty.json`, `ccusage_blocks_no_active.json`, `ccusage_weekly_healthy.json`. |

### Configuration

| File | Purpose | Change Type |
|------|---------|-------------|
| `tech_dev_agents/ops_console/config.py` | Settings | **No new settings.** Re-use `agent_quota_enabled` to gate both endpoints. Keeps the blast radius of a rollback to a single env var. |
| `deployment/ops-console/parameters.{uat,prod}.json` | Container App env wiring | NO CHANGE — `OPS_AGENT_QUOTA_ENABLED=false` in both is correct; operator flips after merge + deploy. |

---

## Current Behavior

### `GET /api/agents/{name}/quota` (today)

1. Checks `settings.agent_quota_enabled`; returns 404 when disabled.
2. Validates agent name against registry.
3. Calls `_fetch_agent_quota(agent_name)`:
   - Checks 5-minute `TTLCache`. Hit → return.
   - SSHes to `azureagent@{agent_name}-vm` with `ConnectTimeout=5`, runs `sudo -u hermes python3 /opt/agent/quota_check.py`, `asyncio.wait_for` timeout=10s.
   - `json.loads(stdout.decode())` into a `QuotaInfo`.
   - **Broad `except Exception`** → returns `QuotaInfo()` with **all fields null**.
4. Wraps the `QuotaInfo` in a `QuotaResponse`.

### `GET /api/agents/{name}/quota/weekly`

- **Does not exist.** Every consumer (Morris fleet-check, dashboard sparkline) re-implements its own `ccusage --period weekly --format json | tail -1 || echo '{}'` parser.

### Observed failure modes in the field (2026-04-18 → 2026-04-21)

| # | Trigger | Current symptom | Why it fails |
|---|---------|-----------------|--------------|
| 1 | `ccusage` not installed on agent VM | (Current quota endpoint uses JSONL; dispatch_poller hits `FileNotFoundError`, morris-fleet-check substitutes `{}`) | Future: when we switch to ccusage-backed, `shutil.which` returns None and current parser would crash |
| 2 | `ccusage` returns empty stdout (e.g. first-run cache miss under `sudo -u hermes`) | `json.loads("")` raises `JSONDecodeError` | Handler falls into `except Exception` → all-null → **frontend can't distinguish "unavailable" from "healthy, no usage"** |
| 3 | `ccusage blocks` prints a header line ("⠋ Loading...") before JSON | `json.loads` on combined stdout fails | Same silent-null |
| 4 | No active block (agent idle for >5h) | `ccusage` prints `{"blocks": [...], "activeBlock": null}` | Handler looks up `activeBlock.tokens` → `AttributeError` on None |
| 5 | SSH connect times out (agent VM unreachable) | `asyncio.TimeoutError` | Caught by broad except → all-null |
| 6 | `quota_check.py` missing on VM | `/opt/agent/quota_check.py: No such file or directory` on stderr, stdout empty | Same silent-null |
| 7 | p95 SSH latency spike (>10s) | Outer `wait_for(...)` timeout | Same silent-null |

**Root cause of all seven:** the handler conflates *"unavailable"* with *"healthy, zero usage"*. There is no `source` field and no distinct `pacing_status`, so the frontend renders a fake zero-progress bar, Morris trusts the zero and skips an alert, and the dashboard shows a green card for a rate-limited agent.

---

## Proposed Approach

### Decision 1: Hybrid source, not `ccusage`-only

**Seed proposed:** replace the JSONL parser entirely with `ccusage`.

**Analysis recommends:** keep `quota_check.py` as a fallback so that if `ccusage` is uninstalled **or broken**, the endpoint still returns a useful number derived from the JSONL logs.

Rationale:
- `ccusage` is an npm package (`ccusage@18.0.11`) installed via `sudo npm install -g`. It has broken twice in the last six weeks due to PATH issues under `sudo -u hermes`. Fleet-vigilance already flags this as a `WARN` check. Depending on it exclusively re-introduces the same class of outage we are fixing.
- The JSONL parser is pure Python stdlib, runs under the existing `sudo -u hermes python3 /opt/agent/...` pattern, and is already deployed. It can be wrong by a few percent compared to `ccusage` (because `ccusage` weighs cache tokens differently), but "slightly off but available" beats "unavailable but exact" for a dashboard progress bar.
- The hybrid strategy costs ~15 lines of Python in `quota_ccusage.py` (try ccusage → fall back to importing `quota_check.main`) and zero extra surface area in the route handler.

**Data source precedence (server-side, per request):**

```
1. ccusage blocks --json          → source = "ccusage"
2. quota_check.py (JSONL parse)   → source = "jsonl"
3. all else                       → source = "unavailable" (numeric fields null)
```

The `source` field is a string enum exposed to the frontend so the UI can show a badge ("ccusage" / "jsonl" / "unavailable") in a tooltip if we choose to, and unit tests can assert on it.

### Decision 2: Inject the SSH runner, don't monkey-patch it

Today `_fetch_agent_quota` calls `asyncio.create_subprocess_exec` directly. Testing it requires patching `asyncio.create_subprocess_exec` globally, which is fragile (tests accidentally catch subprocesses spawned by other code paths).

**Change:** introduce a module-level callable `_ssh_runner` with signature

```python
async def _ssh_runner(
    agent_name: str,
    remote_cmd: str,
    timeout_s: float = 10.0,
) -> tuple[int, bytes, bytes]:  # (returncode, stdout, stderr)
```

and have both `_fetch_agent_quota` and `_fetch_agent_quota_weekly` call it. Tests replace the module attribute (`monkeypatch.setattr(agents_mod, "_ssh_runner", fake_runner)`). No subprocess patching, no SSH in tests, full branch coverage of the parse/fallback logic.

Existing STORY-496 `_fetch_agent_quota` continues to work — we refactor it to delegate to `_ssh_runner` but keep the public surface identical.

### Decision 3: Response-shape strategy — extend, don't break

`QuotaInfo` (STORY-496) has 7 fields. STORY-510 adds 5 more. The AC says "plus the existing STORY-496 fields for backward compatibility." We keep every STORY-496 field and make the new ones **nullable** so a partial response (e.g. `ccusage` returns current-block but weekly has not yet been fetched) is still valid against the schema.

```
Existing (kept): percent_used, reset_in_minutes, block_start, block_end,
                 remaining_tokens, p90_limit, sessions_in_block
New:             source, current_block_tokens, current_block_cost_usd,
                 time_remaining_minutes, pacing_status
```

`time_remaining_minutes` is a new canonical name matching the seed AC; for STORY-496 back-compat we continue to populate `reset_in_minutes` with the same value.

### Decision 4: Pacing calculation on the server

Derive `pacing_status` server-side (in `_fetch_agent_quota`, after schema normalisation) using the formula from the seed:

```python
def derive_pacing(tokens: int | None, remaining_min: int | None, p90: int | None) -> str:
    if tokens is None or remaining_min is None or p90 is None or p90 <= 0:
        return "unknown"
    elapsed_fraction = 1 - (remaining_min / 300)
    if elapsed_fraction <= 0.05:
        elapsed_fraction = 0.05  # avoid divide-by-zero + ignore noise in first 15 min
    projected = tokens / elapsed_fraction
    ratio = projected / p90
    if ratio < 0.5:
        return "on_track"
    if ratio < 0.9:
        return "approaching_limit"
    return "exceeded"
```

Server-side is the right place because (a) Morris and the dashboard share the same classification without duplicating logic, and (b) we can unit-test it cheaply with table-driven fixtures.

### Decision 5: Observability — log once per availability-state flip

Per AC #5: log `[QUOTA] ccusage unavailable on <agent>` when the probe transitions from available → unavailable. Implementation:

- The agent-side helper writes `source` into its JSON output; the server records it in the cache entry.
- When a fresh fetch returns `source == "unavailable"` and the previous cache entry had `source in ("ccusage", "jsonl")`, log at WARNING level. The inverse transition logs at INFO.
- No per-request log spam — we already gate on the 5-min TTL.

### Decision 6: Weekly endpoint design

- Mirror `_fetch_agent_quota` structure: cache key `quota_weekly:{name}`, same 5-min TTL, same SSH runner, same `source` field.
- Agent-side: `scripts/quota_ccusage.py --weekly` prints `{"source": "...", "days": [...], "total_tokens": N, "total_cost_usd": F}` where `days` is a list of 7 objects `{"date": "YYYY-MM-DD", "tokens": N, "cost_usd": F, "blocks_used": N}`.
- Server parses into `QuotaWeeklyResponse(agent=..., source=..., days=[QuotaDaily, ...], total_tokens=..., total_cost_usd=...)`.
- If `ccusage` returns only 4 days of history (new agent), pad to 7 with `tokens=0` entries — keeps the sparkline fixed-width on the frontend.

### Decision 7: Backward compatibility for the `/quota` endpoint

Zero breaking changes:
- All existing fields preserved.
- `QuotaInfo()` (all-null, the current failure sentinel) is still a valid `source="unavailable"` response — we just also set `source` explicitly.
- Current frontend (if deployed) ignores unknown fields thanks to TypeScript's structural typing.

---

## Risks

| # | Risk | Likelihood | Impact | Mitigation |
|---|------|------------|--------|------------|
| R1 | `ccusage` output shape changes between versions (the seed flags "shape drift") | Medium (it's happened once on `ccusage@18.0.11` upgrade) | Medium — endpoint reverts to `source=unavailable` | The agent-side helper parses defensively: look up each key with `.get()`, `tail -1` on stdout, `try/except json.JSONDecodeError` per-line. Fixture `ccusage_blocks_malformed.json` exercises the parse path. |
| R2 | Frontend renders the new `source="unavailable"` state as a literal "null" | Low | Low — purely visual | We coordinate with the frontend follow-up story (out of scope here), but the backward-compat story is: `source` defaults to `"unavailable"` only when numerics are null, which matches the old behaviour (all-null → hidden card). |
| R3 | `p90_limit` computed from ccusage vs JSONL disagrees, creating flapping pacing_status | Low | Low | `pacing_status` is a coarse 3-state enum with a 40% gap (50% → 90%). A ~5% difference in `p90` rarely flips the bucket. We also record `source` so analysts can explain a flap. |
| R4 | SSH-runner extraction subtly changes timeout semantics | Low | Medium | Keep `timeout_s=10.0` default identical to STORY-496. Phase 7 test asserts a slow runner hits `source=unavailable` and the request completes within the 10s envelope. |
| R5 | Injectable runner introduces an easy-to-forget cache key collision (`quota:` vs `quota_weekly:`) | Low | Low | Use distinct TTL-cache instances per endpoint, or distinct prefixes — Phase 6 locks the choice. |
| R6 | Tests accidentally hit real SSH if runner patching is incomplete | Medium | High (CI flake + cost) | Add a conftest-level autouse fixture that rebinds `_ssh_runner` to a "fail loudly" default for every test in `test_routes_agents_quota.py`, overridden per-test. |
| R7 | Feature flag regression: turning `OPS_AGENT_QUOTA_ENABLED=true` exposes both endpoints, but ops wants to stage them separately | Low | Low | Accepted — two endpoints, one flag. Keeps rollback simple. If we ever need finer control, add `agent_quota_weekly_enabled` later; for now YAGNI. |
| R8 | Duplicate parsing logic between `quota_ccusage.py` (agent-side) and `_fetch_agent_quota` (server-side) drifts over time | Medium | Low | Agent-side does *all* parsing and normalisation. Server-side only validates + derives `pacing_status`. Drift is low because server never touches raw ccusage JSON. |
| R9 | `pacing_status` is wrong in the first 5% of a block (elapsed_fraction clamp) | Low | Low | Acceptable — pacing is a leading indicator, not a hard alert. Clamp at 0.05 documented in code + unit-tested. |

---

## Dependencies

### Upstream (must exist before this story merges)

- **STORY-496 baseline** — already merged. Provides `QuotaInfo`, `QuotaResponse`, `_fetch_agent_quota`, `TTLCache`, `agent_quota_enabled` setting, route wiring.
- **`scripts/quota_check.py`** — already deployed to `/opt/agent/quota_check.py` on every dev-agent VM. Used as the JSONL fallback source.
- **SSH key distribution** — azureagent can SSH to every dev-agent VM with `StrictHostKeyChecking=no`. Existing today; no change.

### Side-stream (optional; improves but not blocks)

- `ccusage` installed on agent VMs. Part of the acceptance is that we **do not depend** on this. If `ccusage` is missing on one/all VMs, the endpoint still returns valid JSON (via JSONL fallback, or `source=unavailable` if that also fails).

### Downstream (consumers of the new schema)

- Frontend quota progress bar (`frontend/src/components/AgentCard.tsx` — STORY-496) — no breaking change; it ignores the new fields.
- Frontend weekly sparkline (follow-up story) — will add later.
- Morris `morris-fleet-check.sh` migration (follow-up story, not in this scope per seed AC #8).

### No new dependencies

- No new Python packages.
- No new npm packages (agent-side).
- No new environment variables.
- No database schema changes.
- No new secrets.
- No new auth surface.

---

## Open Questions Resolved

1. **Q: ccusage-only vs hybrid with quota_check.py?**
   **A:** Hybrid. Decision 1 above.

2. **Q: Extract SSH runner or mock subprocess globally?**
   **A:** Extract. Decision 2 above.

3. **Q: Derive `pacing_status` on server or client?**
   **A:** Server. Decision 4 above.

4. **Q: One feature flag or two?**
   **A:** One (`agent_quota_enabled`). Decision 7 above.

5. **Q: Should `QuotaInfo()` (all-null) remain a valid response?**
   **A:** Yes. Extended with `source="unavailable"`. Decision 3 above.

No blocking unknowns remain. Ready for Phase 6.

---

## Exit Criteria (Phase 4 → 6)

- [x] Affected files enumerated with change type.
- [x] Current behaviour documented against all 7 observed failure modes.
- [x] Proposed approach covers every acceptance criterion in `seed.md`.
- [x] Risks enumerated with likelihood, impact, and mitigation.
- [x] Dependencies classified as upstream / side-stream / downstream.
- [x] No open technical questions blocking design.
