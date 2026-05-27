# STORY-510: Harden `/api/agents/{name}/quota` Endpoint (ccusage-backed + Weekly Trend)

## Problem Statement

The ops-console endpoint `GET /api/agents/{name}/quota` (added in STORY-496, `tech_dev_agents/ops_console/routes/agents.py`) is brittle. Fleet-health checks and the per-agent quota progress bar on the dashboard receive broken or empty JSON, and in some cases the endpoint raises before producing a response at all, when the underlying usage data source (`ccusage`) is missing on the agent VM or returns empty output.

Two distinct failure modes have been observed in the field:

1. **`ccusage` not installed / wrong PATH under `sudo -u hermes`** — the underlying command exits non-zero with no stdout, `json.loads("")` raises `json.JSONDecodeError`, and the handler either returns 500 or (after the broad `except`) returns all-null fields that are indistinguishable from a healthy agent that has not used any tokens yet. `morris-fleet-check.sh` has the same bug (line 88: `sudo -u hermes ccusage --period weekly --format json 2>/dev/null | tail -1 || echo '{}'`).
2. **`ccusage blocks` output shape drift** — when `ccusage` prints a status header or no active block exists, the handler's call-site assumes a specific JSON shape and fails silently, dropping the active-block data.

There is also **no weekly-trend endpoint**. Morris and the dashboard weekly-usage chart currently shell out to `ccusage --period weekly` directly from Bash (via `morris-fleet-check.sh`), which means every consumer re-implements the same fragile parser. We need a single backend endpoint that returns a stable weekly-trend shape with the same graceful-fallback contract as `/quota`.

## Target User

- **Morris** (fleet-vigilance agent) — calls `/api/agents/{name}/quota` on every fleet-health sweep and must not be derailed by a missing `ccusage` binary on one VM.
- **Mark** (engineering manager) — views the per-agent quota progress bar on `ops.gorillacommerce.ai` and expects a usable number (or a clear "unavailable" state), never a broken card or a spinner that never resolves.
- **Dashboard frontend** (`frontend/**`) — consumes both `/quota` and (new) `/quota/weekly` to render the current-block progress bar and a 7-day usage sparkline per agent.

## Acceptance Criteria

- [ ] `GET /api/agents/{name}/quota` returns a valid `QuotaResponse` JSON (HTTP 200) under every observed failure mode: `ccusage` missing, `ccusage` returns empty stdout, `ccusage` returns malformed JSON, SSH timeout, agent VM unreachable, no active block exists yet.
- [ ] When `ccusage` is unavailable, the response sets a new `source: "unavailable"` field (or equivalent) and leaves the numeric fields null so the frontend can render the "unavailable" state instead of a fake zero.
- [ ] Response includes: `current_block_tokens`, `current_block_cost_usd`, `time_remaining_minutes` (until 5-hour block resets), `pacing_status` ∈ `{"on_track", "approaching_limit", "exceeded", "unknown"}`, plus the existing STORY-496 fields (`percent_used`, `reset_in_minutes`, `p90_limit`, etc.) for backward compatibility.
- [ ] New endpoint `GET /api/agents/{name}/quota/weekly` returns a weekly trend: a list of 7 daily entries with `date`, `tokens`, `cost_usd`, `blocks_used`, and a top-level `total_tokens` / `total_cost_usd` summary.
- [ ] A `ccusage` **availability probe** runs once per agent per cache window (5 min), not on every request, and its result is observable via logs (`[QUOTA] ccusage unavailable on <agent>`).
- [ ] The endpoint returns a valid JSON body within **the existing 10-second SSH timeout budget** (no regression in p95 latency).
- [ ] Unit tests in `tests/ops_console/test_routes_agents_quota.py` cover: happy path, empty stdout, malformed JSON, `ccusage` missing error, SSH timeout, no active block, and the weekly-trend shape. Tests mock the subprocess/SSH layer (no real SSH).
- [ ] `AGENT_QUOTA_ENABLED` feature-flag gating is preserved — when false, both endpoints return 404.
- [ ] Fleet-health integration: `morris-fleet-check.sh` (or the equivalent Python call site) consumes `/api/agents/{name}/quota` instead of shelling out to `ccusage` directly, eliminating duplicate fragile parsers. (Implementation can be deferred; tracked as a follow-up if the scope grows.)

## Scope Classification

**Medium** (per SDLC dispatch note).

Rationale:
- No database schema change, no new service, no new auth surface.
- Two endpoints (one modified, one new) inside an existing route module.
- Contained to `tech_dev_agents/ops_console/routes/agents.py` + the `QuotaInfo`/`QuotaResponse` models + a helper (likely `_fetch_agent_quota` + new `_fetch_agent_quota_weekly`) + an update to `scripts/quota_check.py` or a parallel `scripts/quota_ccusage.py`.
- Fits the Medium phase path: `1 → 4 → 6 → 7 → 8 → Done`.

Deliverables required by the dispatch: `seed.md`, `analysis.md`, `feature-spec.md`, `test-design.md`.

## Technical Notes

### Current implementation (as of 2026-04-21, from STORY-496)

- **Route:** `tech_dev_agents/ops_console/routes/agents.py::get_agent_quota` (lines 258–279) gated by `settings.agent_quota_enabled`.
- **Fetcher:** `_fetch_agent_quota` (lines 282–330) SSHes to `azureagent@{agent_name}-vm` with a 10s total timeout and runs `sudo -u hermes python3 /opt/agent/quota_check.py`.
- **Agent-side script:** `scripts/quota_check.py` reads `~/.claude/projects/**/*.jsonl` directly and computes 5-hour-block tokens + P90 limit. **It does not use `ccusage`.** Output shape: `{"active_block": {...}, "p90_limit": N, "total_blocks_analyzed": N}`.
- **Cache:** `TTLCache(300)` — 5-minute TTL per agent, keyed `quota:{agent_name}`.
- **Failure behavior today:** broad `except Exception` swallows everything and returns `QuotaInfo()` (all-null). This is where the "silent failure" class of bugs originates.

### Observed coupling to `ccusage`

- `deployment/vm/morris-fleet-check.sh` (line 88) shells out to `sudo -u hermes ccusage --period weekly --format json`.
- `deployment/hermes/dispatch_poller.py` references `ccusage`.
- `deployment/vm/skills/fleet-vigilance/SKILL.md` documents the `ccusage` workflow Morris uses.

The retry dispatch calls for the quota endpoint to align with this tooling: use `ccusage blocks` (or `ccusage --period weekly --format json`) as the source, with graceful fallback when absent. This is the direction we will take in Phase 4/6 unless analysis shows the current JSONL-parsing approach is strictly better.

### Proposed direction (to be confirmed in Phase 4 analysis)

- Add a new agent-side helper `scripts/quota_ccusage.py` (or extend `quota_check.py`) that:
  1. Probes `shutil.which("ccusage")`; if missing, emits `{"source": "unavailable", ...}` and exits 0 (never crashes the SSH caller).
  2. Runs `ccusage blocks --json` for the current-block view. Tolerates empty stdout and non-JSON lines (`tail -1` style filter + `try/except json.JSONDecodeError`).
  3. Runs `ccusage --period weekly --format json` for the weekly trend.
  4. Normalises both outputs into the stable schema documented in `feature-spec.md`.
- Update `_fetch_agent_quota` to consume the new schema and map `source == "unavailable"` to a `QuotaInfo` with null numerics + a `source` field.
- Add `_fetch_agent_quota_weekly` + route `GET /agents/{name}/quota/weekly` mirroring the same cache + SSH + graceful-fallback pattern.
- Add `QuotaWeeklyResponse` / `QuotaDaily` pydantic models in `tech_dev_agents/ops_console/models/responses.py`.
- Add `pacing_status` derivation: `on_track` (< 50% of P90 at current block pace), `approaching_limit` (50–90%), `exceeded` (> 90%), `unknown` (no P90 baseline or ccusage unavailable).

### Pacing calculation

`pacing_status` is derived on the server from `current_block_tokens`, `time_remaining_minutes`, and `p90_limit`:

```
elapsed_fraction = 1 - (time_remaining_minutes / 300)
projected = current_block_tokens / max(elapsed_fraction, 0.05)
ratio = projected / p90_limit
pacing = "on_track" if ratio < 0.5 else "approaching_limit" if ratio < 0.9 else "exceeded"
```

If `p90_limit is None` or `ccusage` unavailable → `pacing = "unknown"`.

### Test strategy (detail deferred to Phase 7)

Unit tests mock the `asyncio.create_subprocess_exec` call (or extract `_fetch_agent_quota` to accept an injectable "runner" callable) so the tests never touch SSH. Fixtures cover:
- `ccusage_blocks_healthy.json` — realistic 5-hour-block output.
- `ccusage_blocks_empty.json` — no active block.
- `ccusage_weekly_healthy.json` — 7-day trend.
- `ccusage_stdout_empty` — empty bytes.
- `ccusage_stdout_malformed` — `b"bash: ccusage: command not found\n"`.
- `ssh_timeout` — `asyncio.TimeoutError`.

## Dependencies

- **Existing:** `AGENT_QUOTA_ENABLED` setting wiring (STORY-496), `QuotaInfo`/`QuotaResponse` models (STORY-496), TTL cache helper (`tech_dev_agents/ops_console/cache.py`), SSH-to-VM pattern already in `_fetch_agent_quota`.
- **Agent VMs:** `ccusage` installed and on `sudo -u hermes` PATH. Part of the story is to **not depend** on this being true — but we should flag in `ops-review` follow-up that `ccusage` should be added to the agent VM bootstrap.
- **Frontend:** the dashboard quota progress bar (STORY-496) and a new weekly sparkline (follow-up story, not in scope here).
- **No new Python packages.** No new secrets. No database changes.

## Out of Scope

- Installing / bootstrapping `ccusage` on agent VMs (infrastructure story — file as a follow-up if `ops-review` confirms).
- Migrating `morris-fleet-check.sh` to consume `/api/agents/{name}/quota` (tracked as a follow-up; mentioned in acceptance as a stretch item).
- Frontend changes to render the new `pacing_status` or weekly sparkline (separate frontend story).
- Multi-agent fleet-wide quota rollups — this story is per-agent.
- Alerting on pacing status (will be a Morris policy change, not here).
- Backfilling historical quota data into a time-series store — weekly endpoint is live-derived from `ccusage`.

## Recommended Next Phase

**Phase 4 (Analysis)** — Medium scope. Analysis will:
1. Confirm `ccusage` is the right primary source vs. keeping `quota_check.py`'s JSONL parsing (or hybrid: `ccusage` when present, JSONL fallback when not).
2. Lock the response schema for both endpoints.
3. Decide on extracting an injectable SSH/subprocess runner for testability without a full refactor of `_fetch_agent_quota`.

Then Phase 6 (feature-spec.md), Phase 7 (test-design.md + RED tests), Phase 8 (implementation + PR).
