# Specification — STORY-724: Morris Queue Orchestrator Tier

**Phase:** 6 (Design — Functional Specification)
**Date:** 2026-04-26

---

## 1. Overview

`orchestrator_loop.py` runs every 10 minutes via cron on the Morris VM. Each invocation:

1. Acquires a flock reentrancy guard.
2. Collects a frozen snapshot of queue state and open PRs.
3. Runs all 6 detectors against the snapshot (classification phase — no side effects).
4. Executes interventions in priority order (action phase).
5. Posts a Teams DM for every intervention taken.
6. Writes an audit log entry.

Additional modes:
- `--dry-run`: classification only; prints interventions without executing.
- `--check <detector>`: runs only the named detector.
- `--briefing-only`: posts morning briefing without running intervention checks.

---

## 2. Detector Specifications

All detectors are pure functions: accept frozen data dicts, injectable `now: datetime`, and a config dict. Return a typed list. No side effects, no I/O imports.

### 2.1 `detect_stale_never_started(queue, config, now) -> list[StaleClaimRecord]`

```
for row in queue where row['status'] == 'claimed':
    if row['claim_heartbeat_at'] is None:
        if (now - parse_dt(row['claimed_at'])) > timedelta(minutes=config['thresholds']['never_started_minutes']):
            yield StaleClaimRecord(story_id, reason='never_started', claimed_by, claimed_at)
```

**Intervention:** `release_claim()` — autonomous, posts `[ACTION]` DM.

### 2.2 `detect_stale_heartbeat(queue, config, now) -> list[StaleClaimRecord]`

```
for row in queue where row['status'] == 'claimed' and row['claim_heartbeat_at'] is not None:
    if (now - parse_dt(row['claim_heartbeat_at'])) > timedelta(minutes=config['thresholds']['heartbeat_stale_minutes']):
        yield StaleClaimRecord(story_id, reason='heartbeat_stale', last_heartbeat)
```

**Intervention:** `release_claim()` — autonomous if first/second occurrence; `post_approval_needed()` on third strike.

### 2.3 `detect_stale_phase(queue, config, now) -> list[StaleClaimRecord]`

```
for row in queue where row['status'] in ('claimed', 'in_progress'):
    if row['updated_at'] > row['claimed_at'] and row['claim_heartbeat_at'] is None:
        if (now - parse_dt(row['updated_at'])) > timedelta(minutes=config['thresholds']['phase_stale_minutes']):
            yield StaleClaimRecord(story_id, reason='phase_stale', last_updated)
```

**Intervention:** `release_claim()` — autonomous, posts `[ACTION]` DM.

### 2.4 `detect_repeated_failures(history, config, now) -> list[EscalateRecord]`

```
recent = [r for r in history if r['status'] == 'failed'
          and (now - parse_dt(r['completed_at'])) <= timedelta(hours=24)]
counts = Counter(r['story_id'] for r in recent)
for story_id, count in counts.items():
    if count >= config['thresholds']['repeated_failure_count']:
        yield EscalateRecord(story_id, failure_count=count, recent_failures=[...])
```

**Note:** `GET /api/dispatch/history?status=failed&limit=50` — no `since=` param. Client-side time filter required.

**Intervention:** `post_approval_needed()` — DM only, no re-enqueue.

### 2.5 `detect_pr_conflicts(prs, config) -> list[ConflictRecord]`

```
for pr in prs:
    if pr['mergeable'] == 'UNKNOWN':
        continue  # check next cycle
    if pr['mergeable'] == 'CONFLICTING':
        agent_owned = pr['author']['login'] in config['agents']['github_logins'].values()
        yield ConflictRecord(pr_number, repo, head, base, author, title, agent_owned)
```

**Intervention:**
- Agent-owned + `rebase.enabled=true`: `invoke_rebase_subagent()` — posts `[ACTION]` DM.
- Agent-owned + `rebase.enabled=false`: posts `[INFO]` DM.
- Human-owned: posts `[INFO]` DM, no action.

### 2.6 `detect_needs_info_decay(queue, config, now) -> list[NeedsInfoRecord]`

```
for row in queue where row['status'] == 'needs_info':
    age_hours = (now - parse_dt(row['updated_at'])).total_seconds() / 3600
    if age_hours > config['thresholds']['needs_info_decay_hours']:
        yield NeedsInfoRecord(story_id, updated_at, age_hours=round(age_hours, 1))
```

**Intervention:** `post_needs_info_surface()` — informational DM only; status never auto-changed.

---

## 3. Intervention Specifications

### 3.1 `release_claim(story_id, reason, session, config, dry_run=False)`

**Primary path (STORY-702 merged):**
```
POST /api/dispatch/release/{story_id}
Body: {"reason": reason, "released_by": "morris-orchestrator"}
```

**Shim path (STORY-702 not merged):**
```
POST /api/dispatch/force-release/{story_id}
# If 404: log warning, post [INFO] DM, skip release
```

**Detection:** On startup, call `GET /api/dispatch/queue?limit=1`. If `claim_heartbeat_at` absent from response fields → shim mode.

**Idempotency:** Releasing an already-released story returns `{"already_released": true}` — log and skip DM.

**DM:** `[ACTION] Released stale claim: STORY-{id}\n- Reason: {reason}\n- Claimed by: {agent}\n- Claimed at: {claimed_at}`

### 3.2 `invoke_rebase_subagent(pr, config, dry_run=False)`

```python
subprocess.run(
    ["/opt/agent/venv/bin/python", "/opt/agent/claude_sdk_tool.py",
     "-p", f"Rebase PR #{pr.pr_number} ({pr.head}) onto {pr.base} ...",
     "-w", config['workdir'],
     "--max-turns", "15",
     "--permission-mode", "bypassPermissions"],
    timeout=config['interventions']['rebase']['timeout_seconds'],
    capture_output=True, text=True
)
```

Timeout handling: kill process, post `[INFO]` DM `Rebase subagent timed out for PR #{pr.pr_number}`.

**Guard:** `config.interventions.rebase.enabled` must be `true`.

### 3.3 `post_approval_needed(story_id, reason, failures, session, config, dry_run=False)`

DM format:
```
[APPROVAL-NEEDED] Story {story_id} has failed {N} times in 24h

Failure history:
- {timestamp}: exit {code} — {failure_reason}
...

Recommended: respond CANCEL/RETRY/SKIP-{story_id} via ops console.
```

Note: v1 posts DM and logs; reply parsing deferred to follow-on story.

### 3.4 `post_needs_info_surface(records, session, config, dry_run=False)`

```
[INFO] {N} stories awaiting operator response (>{decay_hours}h)
- STORY-{id}: {title} — waiting {age_hours}h
```

### 3.5 `post_load_imbalance_dm(queue, session, config, dry_run=False)`

Trigger: one agent has `pending_count >= overload_pending_count` while ≥2 others have 0.

```
[INFO] Queue imbalance detected
Overloaded: {agent} — {N} pending
Idle: {agent_list}
No automatic re-routing (v1: no assigned_to field).
```

---

## 4. Classification-Before-Action Invariant

```python
# Phase 1: Collect (frozen snapshot)
queue   = fetch_queue(session, config)
history = fetch_history(session, config)
prs     = fetch_prs(config)

# Phase 2: Classify (all detectors, pure, no I/O)
findings = {
    'stale_never_started': detect_stale_never_started(queue, config, now),
    'stale_heartbeat':     detect_stale_heartbeat(queue, config, now),
    'stale_phase':         detect_stale_phase(queue, config, now),
    'repeated_failures':   detect_repeated_failures(history, config, now),
    'pr_conflicts':        detect_pr_conflicts(prs, config),
    'needs_info_decay':    detect_needs_info_decay(queue, config, now),
}

# Phase 3: Act (priority order — no more data collection)
# Priority 1: Release stale claims
for r in findings['stale_never_started'] + findings['stale_heartbeat'] + findings['stale_phase']:
    release_claim(r.story_id, r.reason, session, config, dry_run)

# Priority 2: Escalate repeated failures
for r in findings['repeated_failures']:
    post_approval_needed(r.story_id, r.reason, r.recent_failures, session, config, dry_run)

# Priority 3: Handle PR conflicts
for r in findings['pr_conflicts']:
    if r.agent_owned and config['interventions']['rebase']['enabled']:
        invoke_rebase_subagent(r, config, dry_run)
    else:
        post_dm("[INFO]", ..., session, config)

# Priority 4: Needs info decay
if findings['needs_info_decay']:
    post_needs_info_surface(findings['needs_info_decay'], session, config, dry_run)

# Priority 5: Load imbalance
post_load_imbalance_dm(queue, session, config, dry_run)
```

---

## 5. CLI Interface

```
orchestrator_loop.py [OPTIONS]

  --dry-run              Classify and print; no API calls, no DMs.
  --check DETECTOR       Run only named detector.
                         Names: stale_never_started, stale_heartbeat, stale_phase,
                                repeated_failures, pr_conflicts, needs_info_decay,
                                load_imbalance
  --briefing-only        Post morning briefing DM only; skip intervention checks.
  --config PATH          Path to orchestrator_config.yaml (default: same dir as script).
  --log-level LEVEL      DEBUG|INFO|WARNING|ERROR (default: INFO).
```

---

## 6. Teams DM Severity Prefixes

| Prefix | Trigger | Expected action |
|---|---|---|
| `[ACTION]` | Autonomous intervention executed | Log and monitor |
| `[APPROVAL-NEEDED]` | Requires Mark's decision | Mark acts via ops console |
| `[INFO]` | Observation; no action taken | Mark may act if desired |
| `[BRIEFING]` | Morning briefing | Review queue status |
| `[DRY-RUN]` | `--dry-run` mode | No response needed |

Every intervention — including dry-run — produces at least one DM. No silent actions.

---

## 7. Morning Briefing (`--briefing-only`)

```
[BRIEFING] Morris Orchestrator — {date} Morning Queue Summary

Queue status ({timestamp}):
  Pending:     {N}
  Claimed:     {N}
  In progress: {N}
  Needs info:  {N}
  Failed (24h): {N}

Failed stories (last 24h):
  - STORY-{id}: {title} — failed {N} times — last: {failure_reason}

In-progress stories:
  - STORY-{id}: {title} — claimed by {agent} — {elapsed}h elapsed

Recommended actions:
  - {item 1}
```

---

## 8. Audit Log

Appended to `/var/log/morris/orchestrator.log` per invocation:

```
2026-04-26T04:00:01Z === orchestrator START ===
2026-04-26T04:00:02Z [COLLECT] queue=12 claimed=3 history=47 prs=8
2026-04-26T04:00:02Z [CLASSIFY] stale_never_started=1 stale_heartbeat=0 repeated_failures=1 pr_conflicts=2
2026-04-26T04:00:03Z [ACTION] released STORY-644 reason=never_started claimed_by=derrick
2026-04-26T04:00:03Z [APPROVAL-NEEDED] DM posted for STORY-621 failure_count=3
2026-04-26T04:00:08Z [ACTION] rebase PR #142 exit=0 elapsed=5.1s
2026-04-26T04:00:08Z === orchestrator END cycle_time=7.2s interventions=3 ===
```

Loki labels: `service=morris-orchestrator`, `intervention={type}`.
