# Architecture — STORY-724: Morris Queue Orchestrator Tier

**Phase:** 6 (Design — Architecture)
**Date:** 2026-04-26

---

## 1. Module Layout

```
deployment/morris/scripts/          ← NEW directory (created by this story)
├── orchestrator_loop.py            ← main entry point + cron wiring (~150 LOC)
├── detectors.py                    ← 6 pure detector functions (~150 LOC)
├── interventions.py                ← action execution (~150 LOC)
└── orchestrator_config.yaml        ← thresholds, agent mapping, feature flags
```

Deployed to `/opt/morris/` on the Morris VM via `agent-push.sh` rsync.

**Why `deployment/morris/scripts/` (not `deployment/vm/`):** `deployment/vm/` co-mingles hermes and Morris scripts. Morris orchestration has no hermes analog. A separate directory sets the pattern for future Morris-only scripts.

---

## 2. File Responsibilities

### `orchestrator_loop.py`
- CLI arg parsing (`--dry-run`, `--check`, `--briefing-only`, `--config`, `--log-level`)
- Flock reentrancy guard
- Logging setup (`/var/log/morris/orchestrator.log`)
- Data collection: `fetch_queue()`, `fetch_history()`, `fetch_prs()`
- Orchestration: calls `detectors.*` then `interventions.*` in priority order
- Briefing mode: assemble + post morning briefing DM

### `detectors.py`
Six functions, all signature `detect_*(data, config, now=None) -> list[Record]`:

```python
def detect_stale_never_started(queue, config, now) -> list[StaleClaimRecord]: ...
def detect_stale_heartbeat(queue, config, now) -> list[StaleClaimRecord]: ...
def detect_stale_phase(queue, config, now) -> list[StaleClaimRecord]: ...
def detect_repeated_failures(history, config, now) -> list[EscalateRecord]: ...
def detect_pr_conflicts(prs, config) -> list[ConflictRecord]: ...
def detect_needs_info_decay(queue, config, now) -> list[NeedsInfoRecord]: ...
```

**No imports of `requests`, `subprocess`, or any I/O library.** Independently unit-testable with plain Python dicts.

Record types (dataclasses defined in `detectors.py`):
- `StaleClaimRecord`: `story_id, reason, claimed_by, claimed_at, last_heartbeat, last_updated`
- `EscalateRecord`: `story_id, failure_count, recent_failures`
- `ConflictRecord`: `pr_number, repo, head, base, author, title, agent_owned`
- `NeedsInfoRecord`: `story_id, updated_at, age_hours`

### `interventions.py`
```python
def post_dm(severity, headline, bullets, session, config) -> None: ...
def release_claim(story_id, reason, session, config, dry_run=False) -> None: ...
def invoke_rebase_subagent(pr, config, dry_run=False) -> None: ...
def post_approval_needed(story_id, reason, failures, session, config, dry_run=False) -> None: ...
def post_needs_info_surface(records, session, config, dry_run=False) -> None: ...
def post_load_imbalance_dm(queue, session, config, dry_run=False) -> None: ...
```

`post_dm` calls the Graph API `POST /chats/{chat_id}/messages` using the m365 token pattern from `deployment/vm/teams_m365_deployed.py`.

### `orchestrator_config.yaml`
```yaml
ops_console:
  url: "${OPS_CONSOLE_URL}"
  api_key: "${OPS_CONSOLE_API_KEY}"

teams:
  mark_chat_id: "${MORRIS_MARK_CHAT_ID}"

agents:
  github_logins:
    dan: "Bot Dan"
    derrick: "Bot Derrick"
    morris: "Bot Morris"
    daisy: "Bot Daisy"
    devon: "Bot Devon"
  pr_repos:
    - "hpi-gorillacommerce/tech-dev-agents"

thresholds:
  never_started_minutes: 15
  heartbeat_stale_minutes: 15
  phase_stale_minutes: 45
  needs_info_decay_hours: 4
  repeated_failure_count: 3
  overload_pending_count: 3

interventions:
  rebase:
    enabled: false            # flip once STORY-722 confirmed merged
    timeout_seconds: 300
    script_path: "/opt/agent/scripts/auto_rebase_pr.sh"
  release:
    enabled: true
    story_702_merged: false   # flip once STORY-702 confirmed merged

workdir: "/home/hermes/dev/hpi-gorillacommerce/tech-dev-agents"
log_path: "/var/log/morris/orchestrator.log"
lock_path: "/var/run/morris-orchestrator.lock"
```

---

## 3. Sequence Diagram — One Cycle

```
Morris VM cron (*/10)
       │
       ▼
orchestrator_loop.py::main()
       │
       ├─ flock /var/run/morris-orchestrator.lock
       │       └─ if locked: log "skip", exit 0
       │
       ├─ [COLLECT] (~2-3s)
       │   ├─ GET /api/dispatch/queue?include_claimed=true  → queue[]
       │   ├─ GET /api/dispatch/history?status=failed&limit=50 → hist[]
       │   └─ gh pr list --json ... (per repo) → prs[]
       │
       ├─ [CLASSIFY] (~0s, pure)
       │   ├─ detectors.detect_stale_never_started(queue, cfg, now)
       │   ├─ detectors.detect_stale_heartbeat(queue, cfg, now)
       │   ├─ detectors.detect_stale_phase(queue, cfg, now)
       │   ├─ detectors.detect_repeated_failures(hist, cfg, now)
       │   ├─ detectors.detect_pr_conflicts(prs, cfg)
       │   └─ detectors.detect_needs_info_decay(queue, cfg, now)
       │
       ├─ [ACT P1: Stale Claims]
       │   └─ release_claim() → POST /api/dispatch/release/{id} → [ACTION] DM
       │
       ├─ [ACT P2: Repeated Failures]
       │   └─ post_approval_needed() → [APPROVAL-NEEDED] DM
       │
       ├─ [ACT P3: PR Conflicts]
       │   ├─ agent-owned + rebase.enabled → invoke_rebase_subagent() → subprocess → [ACTION] DM
       │   └─ else → [INFO] DM
       │
       ├─ [ACT P4: Needs Info]
       │   └─ post_needs_info_surface() → [INFO] DM
       │
       ├─ [ACT P5: Load Imbalance]
       │   └─ post_load_imbalance_dm() → [INFO] DM (if triggered)
       │
       └─ [LOG] append to /var/log/morris/orchestrator.log
               release flock
```

---

## 4. Cron Wiring

```cron
# Orchestrator — every 10 minutes
*/10 * * * * /opt/morris/venv/bin/python /opt/morris/orchestrator_loop.py \
    --config /opt/morris/orchestrator_config.yaml \
    >> /var/log/morris/orchestrator.log 2>&1

# Morning briefing — 08:30 ET (13:30 UTC) Mon–Fri
30 13 * * 1-5 /opt/morris/venv/bin/python /opt/morris/orchestrator_loop.py \
    --briefing-only --config /opt/morris/orchestrator_config.yaml \
    >> /var/log/morris/orchestrator.log 2>&1
```

Flock guard ensures briefing and regular invocations don't overlap. The `*/10` cron fires at :00/:10/:20/:30/:40/:50. The briefing at :30 takes the lock (~5s) and releases before the next `:40` interval.

**Deploy path:** Files in `deployment/morris/scripts/` are rsync'd to `/opt/morris/` by `agent-push.sh` (one additional rsync target line needed).

**Rollback:** Comment the two cron lines. All actions are idempotent (release re-queues; rebase is reversible with `git revert`).

---

## 5. STORY-702 Shim

```python
def _detect_story_702(session, config) -> bool:
    """Returns True if claim_heartbeat_at field present in queue response."""
    resp = session.get(f"{base}/api/dispatch/queue", params={"limit": "1"})
    items = resp.json().get("claimed", [])
    if not items:
        return True  # can't determine; assume merged (safe default)
    return "claim_heartbeat_at" in items[0]
```

If STORY-702 not merged:
- `detect_stale_heartbeat` falls back to `claimed_at` as reference timestamp.
- `release_claim` tries `force-release`; if 404, posts `[INFO]` DM and skips.

Shim removed in cleanup commit once STORY-702 confirmed in production.

---

## 6. Dependency Graph

```
STORY-724
  ├── STORY-702 — heartbeat fields + release endpoint (shim until merged)
  ├── STORY-722 — auto-rebase script (config-gated off until merged)
  └── STORY-044 — Morris VM + Teams DM (already merged; provides m365 DM plumbing)
```

Phase 8 proceeds without STORY-702 or STORY-722 merged — shims and config flags handle both.
