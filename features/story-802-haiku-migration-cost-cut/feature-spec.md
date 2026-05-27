# Feature Spec — STORY-802: Haiku/Sonnet Migration Cost Cut

## Overview

Drive daily Foundry spend from ~$100/day (99.94% Opus) to ≤$20/day by completing the stalled Haiku/Sonnet migration. Layered rollout: pilot on derrick → batch remaining VMs → fix `OPUS_PHASES` in `sdlc_phase_runner.py` → wire daily $20 cost alert.

**Approach:** B — Layered Rollout (from analysis.md, weighted 4.00/5).

## Components

### Component 1: VM Hermes Config Audit & Fix

**Files:**
- `features/story-802-haiku-migration-cost-cut/audit.md` (new — deliverable)

**What:**
Audit and correct `/home/hermes/.hermes/config.yaml` on all 5 VMs (morris, dan, derrick, devon, daisy). Three settings per VM:

| Setting | Required Value | Current (assumed Opus) |
|---------|---------------|----------------------|
| `model.default` | `sonnet` | `opus` (config drift) |
| `auxiliary.compression.model` | `claude-haiku-4-5` | likely `opus` |
| `compression.threshold` | `≥ 0.85` | likely `0.5` (cascade trigger) |

**Per-job overrides** (`/home/hermes/.hermes/cron/jobs.json`): any job with `model: null` (inheriting default) whose runs show Opus gets an explicit `model: sonnet` override. Low-stakes summary jobs get `model: haiku`.

**Rollout order:**
1. **Day 1:** derrick only. Apply config. Restart hermes service. Verify config survives restart. Run a probe cron job and confirm Sonnet in `state.db` model column. 24h soak.
2. **Day 2:** dan, devon, daisy (batch). Same verification per VM.
3. **Day 3:** morris (last — after confirming auth rotation from 2026-04-30 incident is resolved).

**Restart persistence test (CRITICAL — R-B-1 mitigation):**
After applying config on derrick, run:
```bash
sudo systemctl restart hermes-gateway
sleep 5
grep "model:" /home/hermes/.hermes/config.yaml | head -3
```
If `model.default` reverts to `opus`, STOP and investigate the service startup script for config-reset behavior before proceeding to batch.

**Verification per VM:**
```bash
# Check config
grep -A1 "^model:" /home/hermes/.hermes/config.yaml
grep "compression" /home/hermes/.hermes/config.yaml

# Check running model after a cron job completes
sqlite3 /home/hermes/.hermes/state.db "SELECT model, created_at FROM sessions ORDER BY created_at DESC LIMIT 3;"
```

### Component 2: SDK Phase Model Selector Fix

**File:** `deployment/hermes/sdlc_phase_runner.py`

**Current code (line 1568):**
```python
OPUS_PHASES = {1, 6, 9, 10}  # Seed, Design, Refinement, Operations
```

**Change to:**
```python
OPUS_PHASES = {1, 9, 10}  # Seed, Refinement, Operations — Opus always
```

**Rationale:** Per `CLAUDE.md` Model Policy, Phase 6 (Design) uses Opus only for large scope. For small/medium scope (the majority of stories), Sonnet is acceptable and specified as tier-2 acceptable. Removing Phase 6 from `OPUS_PHASES` means:
- Small/medium stories: Sonnet for Phase 6 (saves ~$3-5 per design phase)
- Large stories: handled by the agent persona's Model Gate — tier-2 agents delegate to tier-1 sub-agents when needed

**No changes to `claude_sdk_tool.py`** — the `--model` argument contract is unchanged. The SDK tool already accepts `opus`, `sonnet`, `haiku` and passes them through. We only change *when* we pass `--model opus`.

**Log message update:**
```python
OPUS_PHASES = {1, 9, 10}  # Seed, Refinement, Operations — Opus always
model_flag = []
if phase_num in OPUS_PHASES:
    model_flag = ["--model", "opus"]
    print(f"[DISPATCH] Phase {phase_num} uses Opus (reasoning phase)", flush=True)
else:
    print(f"[DISPATCH] Phase {phase_num} uses Sonnet (execution phase)", flush=True)
```

### Component 3: Daily $20 Foundry Cost Alert

**File:** `deployment/ops-console/scripts/daily_cost_alert.sh` (new)

**What:** A cron script that runs at 18:00 UTC daily, queries the `foundry_cost_daily` table for today's total, and emits a `[COST_ALERT]` log line if the total exceeds $20.

**Implementation:**
```bash
#!/usr/bin/env bash
# daily_cost_alert.sh — Daily Foundry spend gate (AC-7)
# Cron: 0 18 * * * /opt/ops-console/scripts/daily_cost_alert.sh
set -euo pipefail

THRESHOLD="${FOUNDRY_DAILY_THRESHOLD:-20.00}"
LOG_TAG="[COST_ALERT]"
TODAY=$(date -u +%Y-%m-%d)

log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) ${LOG_TAG} $1"; }

# Query today's total from foundry_cost_daily (populated by refresh_foundry_cost.py every 2h)
TOTAL=$(psql "${DATABASE_URL}" -t -A -c \
  "SELECT COALESCE(opus_usd + sonnet_usd + haiku_usd + other_usd, 0)
   FROM foundry_cost_daily
   WHERE usage_date = '${TODAY}';" 2>/dev/null || echo "0.00")

if [ -z "$TOTAL" ] || [ "$TOTAL" = "" ]; then
    TOTAL="0.00"
fi

EXCEEDED=$(echo "${TOTAL} > ${THRESHOLD}" | bc -l 2>/dev/null || echo 0)

if [ "$EXCEEDED" -eq 1 ]; then
    log "ALERT foundry_daily_exceeded=true date=${TODAY} total=\$${TOTAL} threshold=\$${THRESHOLD} — PAGES MARK"
else
    log "OK foundry_daily_cost=\$${TOTAL} threshold=\$${THRESHOLD} date=${TODAY}"
fi
```

**Cron entry** (on ops-console VM):
```
0 18 * * * /opt/ops-console/scripts/daily_cost_alert.sh >> /var/log/ops-console/cost_alert.log 2>&1
```

**Alert routing:** The `[COST_ALERT]` tag is already picked up by the existing `AlertService` Loki query pattern (see `alert_service.py` which scans for `[COST_ANOMALY]`, `[SDK_HEALTH]`, `[TERMINAL_GUARD]`). Add `[COST_ALERT]` to the AlertService source list.

**File:** `tech_dev_agents/ops_console/services/alert_service.py`

**Change:** Add `[COST_ALERT]` to the Loki log pattern query so the dashboard surfaces daily threshold breaches alongside existing alert types.

### Component 4: Verification Deliverable

**File:** `features/story-802-haiku-migration-cost-cut/verification.md` (new — deliverable)

**What:** Post-rollout verification document confirming:
- Per-VM config state (model, compression.model, compression.threshold)
- Per-VM cron job model overrides applied
- `OPUS_PHASES` change deployed
- 24h soak results from derrick pilot
- Daily cost alert test (synthetic or real trigger)
- 7-day cost trend post-deploy

### Component 5: Follow-Up Cost Gate Task

**File:** Added as a note in `verification.md`

**What:** On 2026-05-08 (7 days post-deploy), verify daily Foundry spend ≤ $20/day. If gate missed, dispatch a fix story. This is a manual check, not automated — the daily alert (Component 3) provides ongoing monitoring.

**Follow-up ticket:** STORY-825 (queued) — verifies 7-day cost gate on 2026-05-08 and dispatches remediation if threshold exceeded.

## Rollout Sequence (Layered)

| Layer | Day | Scope | Changes | Verification |
|-------|-----|-------|---------|-------------|
| 1 | 1-2 | derrick only | Config: model→sonnet, compression→haiku, threshold→0.85, per-job overrides | Restart persistence test, state.db model check, 24h cost comparison |
| 2 | 2-3 | dan, devon, daisy, morris | Same config changes (morris last) | Same verification per VM |
| 3 | 3-4 | sdlc_phase_runner.py | `OPUS_PHASES = {1, 9, 10}` (remove Phase 6) | Run a small/medium Phase 6 on Sonnet, verify quality |
| 4 | 4-5 | ops-console | daily_cost_alert.sh cron, AlertService update | Trigger alert with low threshold, verify log + dashboard |

## Error Handling

| Error Condition | Behavior | Rationale |
|----------------|----------|-----------|
| Config reverts on VM restart | **Fail-closed**: STOP rollout, investigate startup script before proceeding | R-B-1 — batch deploy without persistence guarantee risks fleet-wide revert |
| Compression cascade after threshold change | **Fail-closed**: revert threshold to 0.5 on affected VM, raise $30/day rollback alert | R-B-2 — 5-10x cost multiplier if cascade triggers |
| Sonnet quality insufficient for Phase 6 | **Fail-open**: revert Phase 6 to OPUS_PHASES if retry rate >20% within 48h | R-B-3 — quality regression is observable via retry count |
| `foundry_cost_daily` table has no row for today | Alert script logs `$0.00` — no false alert | Data lag is normal (refresh runs every 2h); 18:00 UTC check ensures at least 9 refresh cycles |
| `DATABASE_URL` not set on ops-console | Alert script exits with error, cron logs failure | Standard cron failure pattern; existing monitoring catches cron failures |
| Morris auth rotation incomplete | **Fail-closed**: skip morris, proceed with other VMs, add blocker note | R-A-2 — auth rotation collision |

### What is NOT exposed to operators
- Internal file paths on agent VMs
- Database connection strings
- Azure credential details

### What IS logged on error
- Alert tag (`[COST_ALERT]`), agent name, date, dollar amounts, threshold
- Config drift detection tag (`[COST_MONITOR]`), current vs expected model

## Implementation Plan (Build Order)

### Commit 1: Audit script + audit.md template
- Create `features/story-802-haiku-migration-cost-cut/audit.md` with per-VM audit table (initially empty, filled during rollout)
- Document the verification commands for each VM

### Commit 2: `OPUS_PHASES` fix
- Change `deployment/hermes/sdlc_phase_runner.py` line 1568: `OPUS_PHASES = {1, 9, 10}`
- Update the inline comment to reflect the change

### Commit 3: Daily cost alert script
- Create `deployment/ops-console/scripts/daily_cost_alert.sh`
- Update `tech_dev_agents/ops_console/services/alert_service.py` to include `[COST_ALERT]` in Loki query patterns
- Add cron schedule documentation

### Commit 4: Verification deliverable
- Create `features/story-802-haiku-migration-cost-cut/verification.md` with post-rollout verification template
- Include 7-day cost gate follow-up task (2026-05-08)

## Acceptance Criteria Traceability

| AC | Component | Verification |
|----|-----------|-------------|
| AC-1 | Component 1 → audit.md | Per-VM model audit documented |
| AC-2 | Component 1 | config.yaml verified on all 5 VMs, state.db shows Sonnet |
| AC-3 | Component 2 | `OPUS_PHASES = {1, 9, 10}`, Phase 6 runs Sonnet for small/medium |
| AC-4 | Component 1 | `compression.threshold ≥ 0.85` on every VM, cascade test (<3 sub-sessions) |
| AC-5 | Component 1 | `jobs.json` audited, per-job `model:` overrides applied where needed |
| AC-6 | Component 5 | 7-day post-deploy cost gate ≤ $20/day (verified 2026-05-08) |
| AC-7 | Component 3 | daily_cost_alert.sh cron at 18:00 UTC, pages Mark on >$20 |

## Out of Scope

- Building a centralized model policy framework (Approach C — deferred)
- Changing `claude_sdk_tool.py` contract or argument parsing
- Provisioning Azure credentials on VMs (ops task, not code)
- Modifying the `refresh_foundry_cost.py` collection schedule
- Frontend dashboard changes for the new alert type (existing AlertService surfaces it)
