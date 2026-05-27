# Morris Operational Runbook

## System Architecture

You manage 2 dev agents (Dan, Derrick) that pick up stories from a central dispatch queue and produce PRs.

### Agent VMs
| Agent | IP | SSH | Role |
|-------|-----|-----|------|
| Dan | 20.228.224.243 | `ssh -p 443 -o StrictHostKeyChecking=no azureagent@20.228.224.243` | Developer |
| Derrick | 20.121.210.186 | `ssh -p 443 -o StrictHostKeyChecking=no azureagent@20.121.210.186` | Developer |
| Morris (you) | 20.246.36.143 | localhost | Manager |

### Key Services Per VM
- `hermes-gateway` — Teams adapter, processes messages
- `dispatch-poller` — polls queue every 60s, claims stories, runs SDK
- `promtail` — ships logs to Loki/Grafana

### Dispatch Queue API
Base: `https://tech-dev-agents.gorillacommerce.ai`
Auth: `X-API-Key` header (value in `/opt/agent/.env` as `OPS_CONSOLE_API_KEY`)

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/dispatch` | POST | Enqueue a story |
| `/api/dispatch/queue` | GET | List pending + claimed |
| `/api/dispatch/next` | GET | Oldest pending (204 if empty) |
| `/api/dispatch/claim/{id}` | POST | Claim a story |
| `/api/dispatch/queue/{id}` | DELETE | Cancel pending story |

### Repos (all at ~/dev/hpi-gorillacommerce/ on each VM)
tech-dev-agents, advertising-amazon, product-health-dashboard, tech-datawarehouse, sourcing-warning-labels, tech-project-mapping, tech-dataimport-monday, tech-gc-knowledgebase, fabric-keepa

### GitHub
Shared PAT — `gh pr list/view/merge` all work. PRs show commit author (Bot Dan/Bot Derrick) not the PAT owner.

---

## Known Failure Modes (and how to fix them)

### 1. Stale local work queue (MOST COMMON)
**Symptom:** Poller logs `[DISPATCH] busy, skipping` but no SDK process is running.
**Cause:** SDK process died/finished but local WorkQueue wasn't cleared.
**Check:**
```
ssh -p 443 azureagent@<IP> "ps aux | grep '[c]laude_sdk_tool.py -p' | wc -l"
ssh -p 443 azureagent@<IP> "sudo -u hermes python3 -c 'import sys; sys.path.insert(0,\"/opt/agent\"); from work_queue import WorkQueue; print(WorkQueue().resume())'"
```
**Fix:** If 0 SDK processes but queue shows active:
```
ssh -p 443 azureagent@<IP> "sudo -u hermes python3 -c 'import sys; sys.path.insert(0,\"/opt/agent\"); from work_queue import WorkQueue; wq=WorkQueue(); [wq.complete(i[\"story_id\"]) for i in wq.list()]'"
```

### 2. Claude Code auth expired
**Symptom:** SDK sessions fail with "Not logged in" or 401.
**Check:** `ssh -p 443 azureagent@<IP> "sudo -u hermes claude auth status --json"`
**Fix:** Mark needs to re-login interactively. Message Mark: "Agent <name> Claude auth expired, needs re-login."

### 3. Stories complete without PRs
**Symptom:** Loki shows [DONE] error=False but no branch/PR exists.
**Cause:** Shared branch collision, git push failure, or gh auth issue.
**Check:** `gh pr list --repo hpi-gorillacommerce/<repo> --state all | grep STORY-XXX`
**Fix:** Re-dispatch the story with explicit branch instruction.

### 4. Azure Foundry 401 in SDK
**Symptom:** `"Access denied due to invalid subscription key"`
**Cause:** ANTHROPIC_BASE_URL leaking into SDK subprocess from .env
**Check:** This only affects dispatch poller sessions. The poller wrapper (`run_dispatch_poller.py`) should unset these vars.
**Fix:** Verify `/opt/agent/run_dispatch_poller.py` has `os.environ.pop("ANTHROPIC_BASE_URL", None)` at the top.

### 5. Graph API 502/504
**Symptom:** Teams polling errors in gateway log.
**Cause:** Microsoft Graph intermittent outages.
**Fix:** Nothing — the poll loop retries automatically. Only escalate if it persists >30 minutes.

---

## Agent Unstick Procedure (used by 15-min heartbeat)

### Quick Diagnosis (run for each agent)
```python
import subprocess

def check_agent(name, ip):
    """Returns: 'healthy', 'stuck', 'rate-limited', 'unreachable'"""
    
    # 1. Is VM reachable?
    r = subprocess.run(
        ["ssh", "-p", "443", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10",
         f"azureagent@{ip}", "echo ALIVE"],
        capture_output=True, text=True, timeout=15
    )
    if r.returncode != 0:
        return "unreachable"
    
    # 2. Is Claude Code running? (active work in progress)
    r = subprocess.run(
        ["ssh", "-p", "443", "-o", "StrictHostKeyChecking=no",
         f"azureagent@{ip}", "ps aux | grep '[c]laude' | grep -v grep | wc -l"],
        capture_output=True, text=True, timeout=15
    )
    sdk_count = int(r.stdout.strip() or "0")
    
    # 3. Is the local work queue occupied?
    r = subprocess.run(
        ["ssh", "-p", "443", "-o", "StrictHostKeyChecking=no",
         f"azureagent@{ip}",
         "sudo -u hermes python3 -c 'import sys; sys.path.insert(0,\"/opt/agent\"); from work_queue import WorkQueue; items=WorkQueue().list(); print(len(items))'"],
        capture_output=True, text=True, timeout=15
    )
    queue_count = int(r.stdout.strip() or "0")
    
    # 4. Check for rate limiting in recent logs
    r = subprocess.run(
        ["ssh", "-p", "443", "-o", "StrictHostKeyChecking=no",
         f"azureagent@{ip}",
         "sudo journalctl -u dispatch-poller --since '15 min ago' --no-pager 2>/dev/null | grep -c 'hit your limit'"],
        capture_output=True, text=True, timeout=15
    )
    rate_limited = int(r.stdout.strip() or "0") > 0
    
    if rate_limited:
        return "rate-limited"
    elif queue_count > 0 and sdk_count == 0:
        return "stuck"  # HAS work queued but NO process running
    elif sdk_count > 0:
        return "healthy"  # Actively working
    else:
        return "healthy"  # Idle, waiting for work

# Check both agents
for name, ip in [("Dan", "20.228.224.243"), ("Derrick", "20.121.210.186")]:
    status = check_agent(name, ip)
    print(f"{name}: {status}")
```

### Fix: Clear Stuck Queue
If agent is "stuck" (queued work but no running process):
```
ssh -p 443 azureagent@<IP> "sudo -u hermes python3 -c 'import sys; sys.path.insert(0,\"/opt/agent\"); from work_queue import WorkQueue; wq=WorkQueue(); [wq.complete(i[\"story_id\"]) for i in wq.list()]'"
```
This clears the local queue so the poller can claim new work on the next cycle (~60s).

### Fix: Rate-Limited Agent
Nothing to do — wait for the reset. Check the logs for the reset time:
```
ssh -p 443 azureagent@<IP> "sudo journalctl -u dispatch-poller --since '30 min ago' --no-pager | grep 'hit your limit' | tail -1"
```
Note the reset time and move on. Stories will be picked up when limits reset.

### Fix: Unreachable Agent
1. Check if the Azure VM is running (Mark may need to restart it from Azure portal)
2. Check the ops console health endpoint — if agents_reachable < agents_total, confirm which is down
3. Message Mark immediately

### Decision Tree
```
Agent Check → Reachable?
  NO  → Message Mark "Agent X unreachable"
  YES → Queue occupied?
    NO  → Healthy (idle or actively polling)
    YES → Claude Code running?
      YES → Healthy (actively working)
      NO  → Rate limited?
        YES → Note reset time, do nothing
        NO  → STUCK → Clear queue immediately
```

---

## Using Claude Code

Run Claude Code for anything requiring file analysis, code review, writing, or multi-step operations:
```
terminal(command="cd /home/hermes/dev/hpi-gorillacommerce/REPO && claude -p 'Your prompt' --max-turns 30", background=true)
```

- `background=true` keeps you responsive to Mark on Teams
- You get notified when it completes
- Add `--max-turns N` to cap session length
- Claude Code can read/write files, run tests, commit, push, create PRs

---

## Your State Files

Persist at `~/workspace/tech-dev-agents/state/morris/` (symlinked from `~/state/morris/` and `/home/hermes/state/morris/`).

**After EVERY state update:**
```
cd ~/workspace/tech-dev-agents && git add state/morris/ && git commit -m "morris: update state" && git pull --rebase && git push
```

---

## Failure Modes Discovered By Morris

### 6. Stories fall through on manual re-dispatch
**Symptom:** Mark closes a PR (wrong deliverable) and says "re-dispatching" but story never appears in queue.
**Cause:** Human forgot to actually POST to dispatch API after closing PR.
**Check:** After any PR is closed without merge, verify the story appears in dispatch queue within 5 minutes.
**Fix:** Morris dispatches the story himself with corrected requirements from the PR close comment.
**Prevention:** Story audit should catch these. Run weekly or after any PR closure.

### 7. Cron delivery silently fails with deliver=origin
**Symptom:** Cron output files exist at `~/.hermes/cron/output/{job_id}/` but Teams message never arrives.
**Cause:** `deliver=origin` resolves delivery target from active session context. Crons run asynchronously with no session → resolves to nothing.
**Check:** `last_delivery_error` on the job shows "no delivery target resolved for deliver=origin".
**Fix:** Always use explicit Teams chat ID: `deliver: teams:19:59586aa1-7b41-41f3-bbc7-3e4cb8d74ff5_a09d0834-c095-48c5-9603-e6dc7e6f6885@unq.gbl.spaces`. Local-only crons can use `deliver=local`.
**Prevention:** Verify every cron's deliver field. Never use `deliver=origin` for crons.
**Note:** As of 2026-04-16, the Friday Fleet Review cron (ca1946bb921a) STILL has `deliver=origin` and needs fixing.

### 8. Ghost completions — agents mark stories done in <30s with zero real work
**Symptom:** Story completed in Loki logs but no new commits, no PR, no deliverables.
**Cause:** Agent claimed story, didn't actually execute (auth issue, rate limit, etc.), but reported success.
**Check:** Compare `completed_at` vs `claimed_at` — legitimate work takes >5 minutes. Check git log for commits.
**Fix:** Redispatch the story. Commit gate now partially catches this but can be fooled by pre-existing commits.
**Prevention:** fleet-vigilance heartbeat should audit recent completions for suspiciously fast times.

### 9. Product-health-dashboard story churn
**Symptom:** STORY-035 (RBAC) failed 14+ times overnight in product-health-dashboard.
**Cause:** Likely missing test dependencies, DB setup issues, or CI configuration problems in this repo.
**Check:** Read the failed story logs in Loki for the specific error pattern.
**Fix:** Need to investigate root cause — may need repo-level CI/env fixes before dispatching more stories.
**Status:** Mark cancelled STORY-036-039 and re-dispatched STORY-035 only. Still problematic.

### 10. Agent builds wrong deliverable
**Symptom:** PR content doesn't match story spec (e.g., STORY-226 PR #46 built history UI instead of taxonomy matching).
**Cause:** Dispatch prompt was ambiguous or agent misinterpreted seed file.
**Fix:** Close PR, re-dispatch with explicit "do NOT build X" and crystal-clear requirements.
**Prevention:** Include specific deliverable checklist in dispatch prompts. Reference exact functions/files to modify.

## Safe Command Patterns (avoid security hook triggers)

NEVER pipe curl to python3 or bash. This triggers security approval prompts.

BAD: `curl https://example.com/api | python3 -c "import sys,json; ..."`
GOOD: `python3 -c "import urllib.request,json; d=json.load(urllib.request.urlopen('https://example.com/api')); print(d)"`

### Dispatch Queue Check (safe)
```
python3 -c "
import urllib.request, json, os
url = 'https://tech-dev-agents.gorillacommerce.ai/api/dispatch/queue'
key = os.environ.get('OPS_CONSOLE_API_KEY', '')
req = urllib.request.Request(url, headers={'X-API-Key': key})
d = json.load(urllib.request.urlopen(req, timeout=10))
print(f'Pending: {d[\"total_pending\"]}, Claimed: {d[\"total_claimed\"]}')
for i in d.get('pending',[]): print(f'  P: {i[\"story_id\"]}')
for i in d.get('claimed',[]): print(f'  C: {i[\"story_id\"]} by {i.get(\"claimed_by\")}')
"
```

### Enqueue Story (safe)
```
python3 -c "
import urllib.request, json, os
url = 'https://tech-dev-agents.gorillacommerce.ai/api/dispatch'
key = os.environ.get('OPS_CONSOLE_API_KEY', '')
data = json.dumps({'story_id':'STORY-XXX','repo':'repo-name','scope':'small','prompt':'...','enqueued_by':'morris'}).encode()
req = urllib.request.Request(url, data=data, headers={'X-API-Key': key, 'Content-Type': 'application/json'})
d = json.load(urllib.request.urlopen(req, timeout=10))
print(f'Enqueued {d[\"item\"][\"story_id\"]} (depth: {d[\"queue_depth\"]})')
"
```

### Fleet Health Check (safe)
```
python3 -c "
import urllib.request, json, os
url = 'https://tech-dev-agents.gorillacommerce.ai/api/health'
key = os.environ.get('OPS_CONSOLE_API_KEY', '')
req = urllib.request.Request(url, headers={'X-API-Key': key})
d = json.load(urllib.request.urlopen(req, timeout=10))
print(json.dumps(d, indent=2))
"
```

### GitHub PR List (safe — no pipe)
```
gh pr list --repo hpi-gorillacommerce/REPO --state open --json number,title,author --jq '.[] | "#\(.number) \(.author.login): \(.title)"'
```

---

## Self-Improvement Loop (STORY-727)

The improvement loop aggregates failure data and adversarial findings to surface
recurring patterns and generate prompt/code improvement proposals for Mark's approval.

### Architecture

| Component | Schedule | Role |
|-----------|----------|------|
| `pattern_detector.py` | Daily 06:00 ET | Reads DB failure_reason + adversarial-review.md files; emits Pattern objects above threshold |
| `proposal_generator.py` | Called by detector | Invokes Sonnet subagent per pattern; validates diff; inserts improvement_proposals row; posts [APPROVAL-NEEDED] DM in live mode |
| `approval_handler.py` | On webhook | Routes [APPROVE]/[REJECT] from Mark's Teams reply; applies Tier-1 diffs or opens Tier-2 PRs |
| `tracker.py` | Daily 07:00 ET | For proposals applied ≥14 days ago, recomputes expected_metric and inserts improvement_tracking row; DMs Mark on adverse movement |
| `retrospective.py` | Monday 09:00 ET | [BRIEFING] DM: top-3 patterns, recent decisions, tracking measurements, pending proposals |

### Configuration

`state/morris/config.toml` — `[morris.improvement]` section:

```toml
enabled = false       # Master kill switch
mode = "shadow"       # "shadow" (no DMs to Mark, daily summary only) | "live"
threshold_critical = 3
threshold_high = 4
reproposal_cooldown_days = 30
tracking_window_days = 14
```

### Shadow Mode Validation (before going live)

1. Flip `enabled = true` while keeping `mode = "shadow"`.
2. Run for 2 weeks. Each day a summary DM lists what proposals *would* have been posted.
3. Manually evaluate precision: useful proposals / total. Target ≥ 60%.
4. If precision ≥ 60%, flip `mode = "live"`.

### Two Trust Tiers

| Tier | Target files | On Mark approval |
|------|-------------|------------------|
| 1 (prose) | `tech_dev_agents/sdlc/phase_prompts/`, `CLAUDE.md`, `AGENTS.md`, `state/morris/` | Auto-apply diff, open PR with auto-merge, commit references proposal_id + STORY-727 |
| 2 (code) | `*.py`, `*.sql`, `deployment/`, `tests/` | Open PR, NO auto-merge — normal SDLC review |

### Kill Switch

```bash
# Immediate: disable detection, proposal generation, DMs
# Edit state/morris/config.toml:
# enabled = false
# Then commit + push. Cron becomes a no-op on next execution.
```

### Reverting an Applied Proposal

```bash
# Tier-1 proposals: find the applied_commit from improvement_proposals table
# then revert
git revert <applied_commit>
# Tier-2 proposals: close the open PR — no code was merged
```

### DB Schema

Tables: `improvement_proposals`, `improvement_tracking`
Migration: `scripts/migrations/013_improvement_proposals.sql`

Key indexes:
- `improvement_proposals_pending_unique` — prevents duplicate pending proposals for same (pattern_key, target_file)
- `improvement_proposals_check_back_idx` — supports daily tracker query

### Pattern Keys

| Key | Source | Threshold | Tier |
|-----|--------|-----------|------|
| `adversarial.static_test_masquerading_as_behavioral` | adversarial-review.md CRITICAL | 3 stories / 30d | 1 |
| `adversarial.spec_requirement_omitted` | adversarial-review.md CRITICAL | 3 stories / 30d | 1 |
| `adversarial.unrealistic_test_fixture` | adversarial-review.md HIGH | 4 stories / 30d | 1 |
| `failure.agent_died_preflight` | dispatch_items.failure_reason | 3 stories / 7d | 2 |
| `failure.unknown` | dispatch_items.failure_reason | >20% of all failures | informational |
| `retry_storm.short_duration` | dispatch transitions | 3 storms / 7d | 2 |
| `morris.repeated_intervention.*` | orchestrator.log JSONL | 5 interventions / 7d | informational |
