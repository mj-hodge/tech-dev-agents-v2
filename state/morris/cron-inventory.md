# Morris Cron Inventory

<!-- generated: 2026-05-18T17:55:01+00:00 / source: cron_inventory.py -->

This file is the **authoritative view of Morris's scheduled jobs**.
Auto-regenerated every 5 minutes by `cron_inventory.py`.

**Read this — NOT `crontab -l` (blocked by terminal guard) and NOT just `cron-status.md` (bookkeeping with known silent-write gaps).**

## cron.service

- ActiveState: `active`  
- SubState: `running`  
- Started: `Sun 2026-04-26 18:38:03 UTC`  
- PID: `813`

## Active jobs (9 live, 0 paused)

### === ESCALATION CHECK (Claude Code — lightweight) ===

#### Morris State Sync (2h)

- **Schedule:** `0 12,14,16,18,20,22 * * *`
- **Command:** `/home/hermes/.hermes/scripts/run_cron.sh "Morris State Sync (2h)" /home/hermes/.hermes/scripts/sdk_state_sync.py`
- **Script:** `/home/hermes/.hermes/scripts/sdk_state_sync.py`
- **Last completion:** `2026-05-18T16:00:01Z` rc=0
- **Log:** `/var/log/morris/Morris_State_Sync_2h.log` (size 59566 bytes, mtime 2026-05-18T16:00:01.162459+00:00)
- **Last 24h firings:** 6 (newest first)
    - 2026-05-17T18:00:01+00:00
    - 2026-05-17T20:00:01+00:00
    - 2026-05-17T22:00:01+00:00

### === DAILY JOBS ===

#### Morris Daily Standup

- **Schedule:** `0 10 * * 1-5`
- **Command:** `/home/hermes/.hermes/scripts/run_cron.sh "Morris Daily Standup" /home/hermes/.hermes/scripts/standup-collector.py`
- **Script:** `/home/hermes/.hermes/scripts/standup-collector.py`
- **Last completion:** `2026-05-18T10:00:18Z` rc=0
- **Log:** `/var/log/morris/Morris_Daily_Standup.log` (size 200080 bytes, mtime 2026-05-18T10:00:18.480830+00:00)
- **Last 24h firings:** 1 (newest first)
    - 2026-05-18T10:00:01+00:00

#### Morris Nightly Git Push

- **Schedule:** `0 4 * * *`
- **Command:** `/home/hermes/.hermes/scripts/run_cron.sh "Morris Nightly Git Push" /home/hermes/.hermes/scripts/sdk_nightly_git_push.py`
- **Script:** `/home/hermes/.hermes/scripts/sdk_nightly_git_push.py`
- **Last completion:** `2026-05-18T04:00:03Z` rc=0
- **Log:** `/var/log/morris/Morris_Nightly_Git_Push.log` (size 44134 bytes, mtime 2026-05-18T04:00:03.239324+00:00)
- **Last 24h firings:** 1 (newest first)
    - 2026-05-18T04:00:01+00:00

### === WEEKLY JOBS ===

#### orchestrator_loop.py

- **Schedule:** `30 13 * * 1-5`
- **Command:** `set -a; . /opt/agent/.env; set +a; /opt/morris/venv/bin/python /opt/morris/orchestrator_loop.py --briefing-only --config /opt/morris/orchestrator_config.yaml >> /var/log/morris/orchestrator.log 2>&1`
- **Script:** `/opt/morris/orchestrator_loop.py`
- **Log:** none yet at `/var/log/morris/orchestrator_loop_py.log`
- **Last 24h firings:** 1 (newest first)
    - 2026-05-18T13:30:01+00:00

#### daily_contact_summary.py

- **Schedule:** `0 8 * * 1-5`
- **Command:** `set -a; . /opt/agent/.env; set +a; /opt/morris/venv/bin/python /opt/morris/daily_contact_summary.py --config /opt/morris/orchestrator_config.yaml >> /var/log/morris/orchestrator.log 2>&1`
- **Script:** `/opt/morris/daily_contact_summary.py`
- **Log:** none yet at `/var/log/morris/daily_contact_summary_py.log`
- **Last 24h firings:** 1 (newest first)
    - 2026-05-18T08:00:01+00:00

#### foundry_pace_check.py

- **Schedule:** `5 * * * *`
- **Command:** `set -a; . /opt/agent/.env; set +a; /opt/morris/venv/bin/python /opt/morris/foundry_pace_check.py >> /var/log/morris/foundry-pace.log 2>&1`
- **Script:** `/opt/morris/foundry_pace_check.py`
- **Log:** none yet at `/var/log/morris/foundry_pace_check_py.log`
- **Last 24h firings:** 24 (newest first)
    - 2026-05-17T18:05:01+00:00
    - 2026-05-17T19:05:01+00:00
    - 2026-05-17T20:05:01+00:00

#### state-commit.sh

- **Schedule:** `0 4 * * *`
- **Command:** `/opt/morris/state-commit.sh >> /var/log/morris/state-commit.log 2>&1`
- **Script:** `/opt/morris/state-commit.sh`
- **Log:** none yet at `/var/log/morris/state_commit_sh.log`
- **Last 24h firings:** 1 (newest first)
    - 2026-05-18T04:00:01+00:00

#### cron_inventory.py

- **Schedule:** `*/5 * * * *`
- **Command:** `/home/hermes/.hermes/scripts/cron_inventory.py >/dev/null 2>&1`
- **Script:** `/home/hermes/.hermes/scripts/cron_inventory.py`
- **Log:** none yet at `/var/log/morris/cron_inventory_py.log`
- **Last 24h firings:** 289 (newest first)
    - 2026-05-17T17:55:01+00:00
    - 2026-05-17T18:00:01+00:00
    - 2026-05-17T18:05:01+00:00

#### generate-bundle.py

- **Schedule:** `0 6 * * *`
- **Command:** `/home/hermes/.claude/skills/review-context-bundler/generate-bundle.py >> /var/log/morris/bundler.log 2>&1`
- **Script:** `/home/hermes/.claude/skills/review-context-bundler/generate-bundle.py`
- **Log:** none yet at `/var/log/morris/generate_bundle_py.log`
- **Last 24h firings:** 1 (newest first)
    - 2026-05-18T06:00:01+00:00

## Anomalies (jobs that should have fired but didn't in 24h)

None — every active job has fired in the last 24h.

## Recent cron-status.md (bookkeeping log) — last 30 entries

- 2026-05-18T14:00Z Morris State Sync (2h): [ok] collected_at=2026-05-18T14:00:01.158523+00:00 run_label=state_sync mode=claude_background launched=[1] commands=[0]
- 2026-05-18T12:00Z Morris State Sync (2h): [ok] collected_at=2026-05-18T12:00:01.635785+00:00 run_label=state_sync mode=claude_background launched=[1] commands=[0]
- 2026-05-18T10:00Z Morris Daily Standup: [ok] collected_at=2026-05-18T10:00:01.367085+00:00 collected_at_et=2026-05-18 06:00 AM ET git_activity_24h=[2] total_commits_24h=3 open_prs=[9] open_pr_count=9 merged_prs_24h=[0] dispatch_queue={1}
- 2026-05-18T04:00Z Morris Nightly Git Push: [ok] collected_at=2026-05-18T04:00:01.841151+00:00 run_label=nightly_git_push mode=shell launched=[0] commands=[2]
- 2026-05-17T22:00Z Morris State Sync (2h): [ok] collected_at=2026-05-17T22:00:01.774579+00:00 run_label=state_sync mode=claude_background launched=[1] commands=[0]
- 2026-05-17T20:00Z Morris State Sync (2h): [ok] collected_at=2026-05-17T20:00:02.032635+00:00 run_label=state_sync mode=claude_background launched=[1] commands=[0]
- 2026-05-17T18:00Z Morris State Sync (2h): [ok] collected_at=2026-05-17T18:00:02.071929+00:00 run_label=state_sync mode=claude_background launched=[1] commands=[0]
- 2026-05-17T16:00Z Morris State Sync (2h): [ok] collected_at=2026-05-17T16:00:01.420177+00:00 run_label=state_sync mode=claude_background launched=[1] commands=[0]
- 2026-05-17T14:00Z Morris State Sync (2h): [ok] collected_at=2026-05-17T14:00:01.566739+00:00 run_label=state_sync mode=claude_background launched=[1] commands=[0]
- 2026-05-17T12:00Z Morris State Sync (2h): [ok] collected_at=2026-05-17T12:00:01.332242+00:00 run_label=state_sync mode=claude_background launched=[1] commands=[0]
- 2026-05-17T04:00Z Morris Nightly Git Push: [ok] collected_at=2026-05-17T04:00:01.953186+00:00 run_label=nightly_git_push mode=shell launched=[0] commands=[2]
- 2026-05-16T22:00Z Morris State Sync (2h): [ok] collected_at=2026-05-16T22:00:01.991840+00:00 run_label=state_sync mode=claude_background launched=[1] commands=[0]
- 2026-05-16T20:00Z Morris State Sync (2h): [ok] collected_at=2026-05-16T20:00:01.220885+00:00 run_label=state_sync mode=claude_background launched=[1] commands=[0]
- 2026-05-16T18:00Z Morris State Sync (2h): [ok] collected_at=2026-05-16T18:00:01.992416+00:00 run_label=state_sync mode=claude_background launched=[1] commands=[0]
- 2026-05-16T16:00Z Morris State Sync (2h): [ok] collected_at=2026-05-16T16:00:01.979712+00:00 run_label=state_sync mode=claude_background launched=[1] commands=[0]
- 2026-05-16T14:00Z Morris State Sync (2h): [ok] collected_at=2026-05-16T14:00:01.772333+00:00 run_label=state_sync mode=claude_background launched=[1] commands=[0]
- 2026-05-16T12:00Z Morris State Sync (2h): [ok] collected_at=2026-05-16T12:00:02.069835+00:00 run_label=state_sync mode=claude_background launched=[1] commands=[0]
- 2026-05-16T04:00Z Morris Nightly Git Push: [ok] collected_at=2026-05-16T04:00:01.190315+00:00 run_label=nightly_git_push mode=shell launched=[0] commands=[2]
- 2026-05-15T22:00Z Morris State Sync (2h): [ok] collected_at=2026-05-15T22:00:01.189125+00:00 run_label=state_sync mode=claude_background launched=[1] commands=[0]
- 2026-05-15T20:00Z Morris State Sync (2h): [ok] collected_at=2026-05-15T20:00:01.606909+00:00 run_label=state_sync mode=claude_background launched=[1] commands=[0]
- 2026-05-15T18:00Z Morris State Sync (2h): [ok] collected_at=2026-05-15T18:00:02.030902+00:00 run_label=state_sync mode=claude_background launched=[1] commands=[0]
- 2026-05-15T16:00Z Morris State Sync (2h): [ok] collected_at=2026-05-15T16:00:01.883745+00:00 run_label=state_sync mode=claude_background launched=[1] commands=[0]
- 2026-05-12T20:00Z Morris State Sync (2h): [ok] collected_at=2026-05-12T20:00:01.984769+00:00 run_label=state_sync mode=claude_background launched=[1] commands=[0]
- 2026-05-12T18:00Z Morris State Sync (2h): [ok] collected_at=2026-05-12T18:00:01.977501+00:00 run_label=state_sync mode=claude_background launched=[1] commands=[0]
- 2026-05-12T16:00Z Morris State Sync (2h): [ok] collected_at=2026-05-12T16:00:01.593191+00:00 run_label=state_sync mode=claude_background launched=[1] commands=[0]
- 2026-05-12T14:00Z Morris State Sync (2h): [ok] collected_at=2026-05-12T14:00:01.643824+00:00 run_label=state_sync mode=claude_background launched=[1] commands=[0]
- 2026-05-12T12:00Z Morris State Sync (2h): [ok] collected_at=2026-05-12T12:00:01.782040+00:00 run_label=state_sync mode=claude_background launched=[1] commands=[0]
- 2026-05-12T10:00Z Morris Daily Standup: [ok] collected_at=2026-05-12T10:00:01.198476+00:00 collected_at_et=2026-05-12 06:00 AM ET git_activity_24h=[3] total_commits_24h=147 open_prs=[21] open_pr_count=21 merged_prs_24h=[10] dispatch_queue={1}
- 2026-05-12T04:00Z Morris Nightly Git Push: [ok] collected_at=2026-05-12T04:00:02.052198+00:00 run_label=nightly_git_push mode=shell launched=[0] commands=[2]
- 2026-05-11T22:00Z Morris State Sync (2h): [ok] collected_at=2026-05-11T22:00:01.944860+00:00 run_label=state_sync mode=claude_background launched=[1] commands=[0]

> ⚠ Newest cron-status.md entry is 235 min old. `morris_cron_state.py append` may be failing silently. Check `/home/hermes/state/morris/morris_cron_state.log` (after fix #9).

## Raw crontab

```
# Morris crontab — updated 2026-04-27 (low-usage mode, business hours boosted)
# Business hours: 8am-6pm ET = 12-22 UTC. Off overnight.
# Cron results → /home/hermes/state/morris/cron-status.md (NOT memory)
# Heartbeat writes fleet-status.md + pr-tracker.md directly
# Overnight = 1:00-9:59 UTC (9pm-6am ET) — 4x slower

# === DATA COLLECTORS (write to state files directly) ===

# Morris Heartbeat — every 1h business hours (12-22 UTC / 8am-6pm ET), off overnight
#PAUSED# 0 12-22 * * * /home/hermes/.hermes/scripts/run_cron.sh "Morris Heartbeat" /home/hermes/.hermes/scripts/heartbeat-collector.py

# === ESCALATION CHECK (Claude Code — lightweight) ===

# Morris State Sync — every 2h business hours (12-22 UTC / 8am-6pm ET), off overnight
0 12,14,16,18,20,22 * * * /home/hermes/.hermes/scripts/run_cron.sh "Morris State Sync (2h)" /home/hermes/.hermes/scripts/sdk_state_sync.py

# Foundry Pace Check — every 1h daytime (10-0 UTC), every 4h overnight (1-9 UTC)
#PAUSED# 5 10-23 * * * /home/hermes/.hermes/scripts/run_cron.sh "Foundry Pace Check" /home/hermes/.hermes/scripts/foundry_pace_check.py
#PAUSED# 5 0 * * * /home/hermes/.hermes/scripts/run_cron.sh "Foundry Pace Check" /home/hermes/.hermes/scripts/foundry_pace_check.py
#PAUSED# 5 4,8 * * * /home/hermes/.hermes/scripts/run_cron.sh "Foundry Pace Check (overnight)" /home/hermes/.hermes/scripts/foundry_pace_check.py

# === DAILY JOBS ===

# Morris Daily Standup — weekdays 10:00 UTC (6:00 AM ET)
0 10 * * 1-5 /home/hermes/.hermes/scripts/run_cron.sh "Morris Daily Standup" /home/hermes/.hermes/scripts/standup-collector.py

# Morris PR Review Cycle — every 2h business hours (12-22 UTC / 8am-6pm ET), off overnight
#PAUSED# 0 12,14,16,18,20,22 * * * /home/hermes/.hermes/scripts/run_cron.sh "Morris PR Review Cycle (2h)" /home/hermes/.hermes/scripts/sdk_pr_review_cycle.py

# Morris Daily Tech Research — 9:00 UTC
#PAUSED# 0 9 * * * /home/hermes/.hermes/scripts/run_cron.sh "Morris Daily Tech Research" /home/hermes/.hermes/scripts/sdk_daily_tech_research.py

# Morris Daily Conversation Synthesis — 5:00 UTC
#PAUSED# 0 5 * * * /home/hermes/.hermes/scripts/run_cron.sh "Morris Daily Conversation Synthesis" /home/hermes/.hermes/scripts/sdk_daily_conversation_synthesis.py

# Morris Nightly Git Push — 4:00 UTC
0 4 * * * /home/hermes/.hermes/scripts/run_cron.sh "Morris Nightly Git Push" /home/hermes/.hermes/scripts/sdk_nightly_git_push.py

# Nightly SDLC Compliance Audit — 5:00 UTC
#PAUSED# 0 5 * * * /home/hermes/.hermes/scripts/run_cron.sh "Nightly SDLC Compliance Audit" /home/hermes/.hermes/scripts/sdk_sdlc_audit.py

# === WEEKLY JOBS ===

# Morris Monday InfoSec Review — 5:00 UTC Monday
#PAUSED# 0 5 * * 1 /home/hermes/.hermes/scripts/run_cron.sh "Morris Monday InfoSec Review" /home/hermes/.hermes/scripts/sdk_infosec_review.py

# Morris Weekly Backlog Review — 14:00 UTC Tuesday
#PAUSED# 0 14 * * 2 /home/hermes/.hermes/scripts/run_cron.sh "Morris Weekly Backlog Review" /home/hermes/.hermes/scripts/sdk_weekly_backlog_review.py

# Morris Wednesday Code Quality Review — 5:00 UTC Wednesday
#PAUSED# 0 5 * * 3 /home/hermes/.hermes/scripts/run_cron.sh "Morris Wednesday Code Quality Review" /home/hermes/.hermes/scripts/sdk_code_quality_review.py

# Morris Friday SRE Review — 5:00 UTC Friday
#PAUSED# 0 5 * * 5 /home/hermes/.hermes/scripts/run_cron.sh "Morris Friday SRE Review" /home/hermes/.hermes/scripts/sdk_sre_review.py

# Morris Friday Fleet Review — 10:00 UTC Friday
#PAUSED# 0 10 * * 5 /home/hermes/.hermes/scripts/run_cron.sh "Morris Friday Fleet Review" /home/hermes/.hermes/scripts/sdk_fleet_review.py

# Cole Curator Weekly — 13:00 UTC Sunday
#PAUSED# 0 13 * * 0 /home/hermes/.hermes/scripts/run_cron.sh "cole-curator-weekly" /home/hermes/.hermes/scripts/sdk_cole_curator_weekly.py

# morris-orchestrator-724
#PAUSED# */10 * * * * set -a; . /opt/agent/.env; set +a; /opt/morris/venv/bin/python /opt/morris/orchestrator_loop.py --config /opt/morris/orchestrator_config.yaml >> /var/log/morris/orchestrator.log 2>&1
30 13 * * 1-5 set -a; . /opt/agent/.env; set +a; /opt/morris/venv/bin/python /opt/morris/orchestrator_loop.py --briefing-only --config /opt/morris/orchestrator_config.yaml >> /var/log/morris/orchestrator.log 2>&1

# morris-contact-tracking
0 8 * * 1-5 set -a; . /opt/agent/.env; set +a; /opt/morris/venv/bin/python /opt/morris/daily_contact_summary.py --config /opt/morris/orchestrator_config.yaml >> /var/log/morris/orchestrator.log 2>&1

# morris-foundry-pace-585
5 * * * * set -a; . /opt/agent/.env; set +a; /opt/morris/venv/bin/python /opt/morris/foundry_pace_check.py >> /var/log/morris/foundry-pace.log 2>&1

# morris-state-commit
0 4 * * * /opt/morris/state-commit.sh >> /var/log/morris/state-commit.log 2>&1

# Refresh cron-inventory.md every 5min — Morrisreads this for crontab visibility
*/5 * * * * /home/hermes/.hermes/scripts/cron_inventory.py >/dev/null 2>&1

# Daily review-context bundle refresh — feeds Morris review-prs skill Step 0
0 6 * * * /home/hermes/.claude/skills/review-context-bundler/generate-bundle.py >> /var/log/morris/bundler.log 2>&1
```

## How Morris uses this file

1. **To know what jobs exist:** read this file. Do NOT run `crontab -l` (terminal guard blocks it).
2. **To diagnose a 'job died' suspicion:** check `Last completion` and `Last 24h firings` for that job here. If both are recent, the job is fine — even if `cron-status.md` looks stale.
3. **To make changes:** call `~/.hermes/scripts/manage-crontab.sh` with subcommands `list`, `add`, `disable`, `enable`, `remove`, `history`. Every change snapshots the prior crontab to `~/state/morris/crontab-history/<ts>.crontab` and appends to `~/state/morris/crontab-changes.jsonl`.
4. **NEVER edit the crontab directly** with `crontab -e` from inside an agent session — there is no audit trail and rollback is manual.
