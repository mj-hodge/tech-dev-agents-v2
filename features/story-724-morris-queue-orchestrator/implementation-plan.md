# Implementation Plan — STORY-724: Morris Queue Orchestrator Tier

**Phase:** 6 (Design — Implementation Plan)
**Date:** 2026-04-26

---

## Overview

6 tasks in strict dependency order. Tasks 1-4 produce production code. Task 5 specifies the 12 RED tests (Phase 7 implements). Task 6 wires cron on Morris VM.

**Estimated Phase 8 time:** 2-3 agent sessions.

---

## Task 1: `orchestrator_config.yaml`

**Files:** `deployment/morris/scripts/orchestrator_config.yaml` (NEW)

Full YAML per architecture.md §2. All sensitive values are `${ENV_VAR}` references.

**Key defaults:**
- `interventions.rebase.enabled: false` (STORY-722 gate)
- `interventions.release.story_702_merged: false` (STORY-702 gate)

**Acceptance criteria:**
- [ ] Parses with `yaml.safe_load()` without error.
- [ ] All required keys present (validated by `orchestrator_loop.py` on startup).
- [ ] No hardcoded secrets.

---

## Task 2: `detectors.py` — 6 pure detector functions

**Files:** `deployment/morris/scripts/detectors.py` (NEW)

Implement in order:
1. `detect_stale_never_started` — threshold: `never_started_minutes` (default 15)
2. `detect_stale_heartbeat` — threshold: `heartbeat_stale_minutes` (default 15)
3. `detect_stale_phase` — threshold: `phase_stale_minutes` (default 45)
4. `detect_repeated_failures` — threshold: `repeated_failure_count` (default 3), 24h window
5. `detect_pr_conflicts` — skip `mergeable=='UNKNOWN'`; set `agent_owned` from login map
6. `detect_needs_info_decay` — threshold: `needs_info_decay_hours` (default 4)

**Rules:**
- Zero imports of `requests`, `subprocess`, `os`.
- `now` always injectable — never call `datetime.utcnow()` inside a detector.
- All thresholds read from `config` dict, never hardcoded.

**Acceptance criteria:**
- [ ] `detect_stale_never_started` returns empty for `claimed_at=now-5min` (fresh claim).
- [ ] `detect_stale_heartbeat` returns empty for `claim_heartbeat_at=now-5min`.
- [ ] `detect_repeated_failures` counts only `status=='failed'` entries within 24h.
- [ ] `detect_pr_conflicts` skips `mergeable=='UNKNOWN'` rows.
- [ ] All 6 functions testable with plain `dict` inputs and no mocking of external I/O.

---

## Task 3: `interventions.py` — action execution

**Files:** `deployment/morris/scripts/interventions.py` (NEW)

1. `post_dm(severity, headline, bullets, session, config)` — Graph API POST
2. `release_claim(story_id, reason, session, config, dry_run=False)` — with STORY-702 shim
3. `invoke_rebase_subagent(pr, config, dry_run=False)` — subprocess + timeout guard
4. `post_approval_needed(story_id, reason, failures, session, config, dry_run=False)`
5. `post_needs_info_surface(records, session, config, dry_run=False)`
6. `post_load_imbalance_dm(queue, session, config, dry_run=False)`

**`release_claim` shim:**
```python
if config['interventions']['release']['story_702_merged']:
    url = f"{base}/api/dispatch/release/{story_id}"
else:
    url = f"{base}/api/dispatch/force-release/{story_id}"
    resp = session.post(url, ...)
    if resp.status_code == 404:
        post_dm("[INFO]", f"Cannot release STORY-{story_id} — endpoint unavailable", ...)
        return
```

**`invoke_rebase_subagent` timeout guard:**
```python
try:
    subprocess.run([...], timeout=config['interventions']['rebase']['timeout_seconds'], ...)
except subprocess.TimeoutExpired:
    post_dm("[INFO]", f"Rebase timed out for PR #{pr.pr_number}", ...)
    return
```

**`dry_run` contract:** Every function with `dry_run=True` makes zero external HTTP calls and zero subprocess invocations. Logs `[DRY-RUN] would: {action}`.

**Acceptance criteria:**
- [ ] `post_dm` tested with mock session; asserts correct Graph API URL and body.
- [ ] `release_claim(dry_run=True)` makes no HTTP calls.
- [ ] `release_claim` with `story_702_merged=False` and 404 response posts `[INFO]` DM and does not raise.
- [ ] `invoke_rebase_subagent` with 400s sleep is killed after `timeout_seconds` and posts `[INFO]` DM.

---

## Task 4: `orchestrator_loop.py` — main entry point

**Files:** `deployment/morris/scripts/orchestrator_loop.py` (NEW)

Skeleton:
```python
def main():
    args = parse_args()
    config = load_config(args.config)
    setup_logging(config, args.log_level)

    lock_fd = open(config['lock_path'], 'w')
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        logging.info("skip — previous invocation still running")
        sys.exit(0)

    session = build_session(config)

    if args.briefing_only:
        run_briefing(session, config, args.dry_run)
        return

    queue   = fetch_queue(session, config)
    history = fetch_history(session, config)
    prs     = fetch_prs(config)
    now     = datetime.now(timezone.utc)

    findings = classify_all(queue, history, prs, config, now)
    if args.check:
        findings = {k: v for k, v in findings.items() if k == args.check}

    execute_interventions(findings, queue, session, config, args.dry_run)
```

**`fetch_history` note:** Filter client-side to last 24h — no `since=` param exists.

**`fetch_prs` note:** `gh pr list --repo {repo} --state open --json ...` subprocess per repo; merge into one list annotated with `repo` field.

**Acceptance criteria:**
- [ ] `--dry-run` causes zero dispatch API mutations and zero Teams DMs (mocked session).
- [ ] `--check stale_heartbeat` skips all other detector functions.
- [ ] `--briefing-only` does not call any detector function.
- [ ] Two concurrent invocations: second exits 0 with "skip" log.
- [ ] Missing required config key raises descriptive error before any API call.

---

## Task 5: Test specification (Phase 7 implements)

**Files:** `tests/deployment/test_morris_orchestrator_724.py` (NEW — RED)

| TC | Function | Scenario | Assert |
|---|---|---|---|
| TC-1 | `detect_stale_never_started` | heartbeat=None, claimed_at=now-16min | 1 record returned; `release_claim` called once |
| TC-2 | `detect_stale_never_started` | heartbeat=None, claimed_at=now-5min | empty list; `release_claim` NOT called |
| TC-3 | `detect_pr_conflicts` + `invoke_rebase_subagent` | CONFLICTING, author=Bot Derrick, rebase.enabled=True | `invoke_rebase_subagent` called |
| TC-4 | `detect_pr_conflicts` | CONFLICTING, human author | `agent_owned=False`; rebase NOT called; `[INFO]` DM |
| TC-5 | `detect_repeated_failures` | 3 failures within 24h | `post_approval_needed` called; no re-enqueue |
| TC-6 | `post_load_imbalance_dm` | dan=3 pending, others=0 | `[INFO]` DM; no release calls |
| TC-7 | `detect_needs_info_decay` | status=needs_info, updated_at=now-5h | `post_needs_info_surface` called; status unchanged |
| TC-8 | `run_briefing` | 5 pending, 2 claimed, 1 failed | Single `[BRIEFING]` DM with counts |
| TC-9 | `main()` flock | Two concurrent calls | Second exits 0 "skip"; interventions fire exactly once |
| TC-10 | `invoke_rebase_subagent` | Subprocess sleeps 400s; timeout=300s | Killed; `[INFO]` DM with timeout message |
| TC-11 | `post_approval_needed` | 3 failures | DM contains story_id + all 3 failure entries; no re-enqueue |
| TC-12 | All interventions | `dry_run=True` | Zero HTTP calls; zero subprocess invocations; log entries present |

---

## Task 6: Cron wiring on Morris VM

**Files:**
- `deployment/morris/scripts/install-orchestrator-cron.sh` (NEW) — idempotent installer
- `deployment/vm/agent-push.sh` (EDIT) — add rsync target for `deployment/morris/scripts/`

**Cron installer (idempotent):**
```bash
#!/usr/bin/env bash
CRON_MARKER="# morris-orchestrator-724"
CRONTAB_CURRENT=$(crontab -l 2>/dev/null || echo "")
if echo "$CRONTAB_CURRENT" | grep -q "$CRON_MARKER"; then
    echo "Already installed."; exit 0
fi
(echo "$CRONTAB_CURRENT"; cat <<'EOF'

# morris-orchestrator-724
*/10 * * * * /opt/morris/venv/bin/python /opt/morris/orchestrator_loop.py --config /opt/morris/orchestrator_config.yaml >> /var/log/morris/orchestrator.log 2>&1
30 13 * * 1-5 /opt/morris/venv/bin/python /opt/morris/orchestrator_loop.py --briefing-only --config /opt/morris/orchestrator_config.yaml >> /var/log/morris/orchestrator.log 2>&1
EOF
) | crontab -
echo "Installed."
```

**`agent-push.sh` edit:** Add one rsync call:
```bash
rsync -av deployment/morris/scripts/ azureagent@${MORRIS_IP}:/opt/morris/ --exclude='*.pyc'
```

**Acceptance criteria:**
- [ ] Running installer twice produces exactly one set of cron lines.
- [ ] `python /opt/morris/orchestrator_loop.py --dry-run` exits 0 with audit log entry on Morris VM.
- [ ] `/var/log/morris/` exists and is writable by hermes user.

---

## Commit Sequence (Phase 8)

```
feat(story-724): Task 1 — orchestrator_config.yaml with thresholds and agent map
feat(story-724): Task 2 — detectors.py with 6 pure detector functions
feat(story-724): Task 3 — interventions.py with release, rebase, and DM actions
feat(story-724): Task 4 — orchestrator_loop.py main entry point
test(story-724): Task 5 — RED test suite (12 test cases)
feat(story-724): Task 6 — cron wiring and agent-push.sh rsync target
```

---

## Phase 8 Prerequisites

- [ ] STORY-702 merge status → set `interventions.release.story_702_merged` accordingly
- [ ] STORY-722 merge status → set `interventions.rebase.enabled` accordingly
- [ ] `/opt/morris/` exists on Morris VM (or create in Task 6)
- [ ] `/var/log/morris/` writable by hermes user
- [ ] `MORRIS_MARK_CHAT_ID` set in `/opt/agent/.env` on Morris VM
- [ ] `MORRIS_MARK_CHAT_ID` available from existing STORY-044 Teams DM setup
