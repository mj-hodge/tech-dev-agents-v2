# Sprint Summary & Deploy Handoff — 2026-04-26

**Sprint date:** 2026-04-26  
**Prepared by:** Derrick (session agent)  
**Status:** All PRs merged to main. Ready to deploy.  
**Last updated:** End-of-session — PR #124, PR #101, PR #163 all merged; CI fixes applied.

---

## What We Built Today

A full-day parallel sprint across 9 feature stories + 1 crash fix + security review.

### Merged to `main`

| PR | Story | What it does |
|----|-------|-------------|
| #148 | STORY-701 | Failure classification: `needs_info_unanswered`, `agent_died` taxonomy + DLQ triage on Morris |
| #149 | STORY-702 | Claim heartbeat (`claim_heartbeat_at` field) + auto-release after 15 min silence |
| #150 | STORY-720 | Codex review severity routing bug fix (logic was inverted) + debt log |
| #151 | STORY-722 | Auto-rebase on CONFLICTING PRs — Morris calls `auto_rebase_pr.sh` post-Phase-8 |
| #152 | STORY-725 | 4 queue reliability gaps: pre-flight guard, pause reset, `needs_info` SQL exclusion, `parse_seed_phase_path` |
| #153 | STORY-723 | Adversarial review gate — post-Phase-8 Sonnet subagent checks spec coverage; BLOCK prevents merge |
| #154 | STORY-721 | Focused per-phase prompts (`FOCUSED_PHASE_PROMPTS` env flag, off by default) |
| #155 | STORY-726 | Parallel agent coordination: jitter backoff on 409, scope-aware routing, durable completions |
| #159 | STORY-724 | Morris Queue Orchestrator: 6 detectors, 5 interventions, cron wiring |
| #160 | CI fix | Seed section headers + Playwright tag cleanup |

### Also merged (earlier PRs + end-of-session fixes)

| PR | Story | What it does |
|----|-------|-------------|
| #161 | Contact tracking | `contact_tracker.py`, `daily_contact_summary.py`, `hermes-log-sync.service` burst limit, F-03..F-09 security fixes |
| #162 | STORY-724 | Orchestrator null-safety guards, type hints, Class 701/720 test gaps |
| #158 | STORY-723 | Adversarial review: H-1 module import guard, H-3 atomic write, BLOCK integration test |
| #156 | STORY-727 | Continuous self-improvement loop: pattern detection → proposal → approval → apply → retrospective |
| #163 | STORY-730 | `hermes-log-sync.service` RuntimeDirectory= fix (replaces crash-loop workaround) |
| #124 | STORY-634 | Rebase STORY-560: `/complete` sends `pr_number`+`commit_sha`; retry+sidecar on failure |
| #164 | STORY-621 | Fix stale QUESTION.md needs_info loop (ping-pong prevention) |
| #101 | STORY-556 | 40 fleet reliability regression tests + phantom-claim guard + rate-limit retry fix |

**CI fixes (end-of-session, on main):**
- Added 20+ missing Pydantic models to `responses.py` that were imported but never defined — would have crashed ops-console on startup
- Added `[USAGE] total_tokens=/cost_usd=` emission to `claude_sdk_tool.py` result branch (quota dashboard was showing empty)
- Added `duration_seconds` + phantom-claim guard to `_report_fail` in `dispatch_poller.py`
- Added `Frontend:`/`## Test Criteria`/`## Validation` to 8 seed files missing them

---

## Security Fixes (in PR #161 — pending merge)

All from the 2026-04-26 security review (`features/story-728-log-sync-crash-fix/security-review-2026-04-26.md`):

| Fix | File | What changed |
|-----|------|-------------|
| F-03 | `deployment/vm/claude_sdk_tool.py` | Pre-execution deny-list fires even under `bypassPermissions` |
| F-05 | `tech_dev_agents/ops_console/auth.py` | Entra group membership actually enforced (removed "Allowing for now") |
| F-06 | `deployment/hermes/dispatch_poller.py` | `_validate_dispatch_fields()` validates repo/pr_branch/prompt before use |
| F-07 | `deployment/hermes/dispatch_poller.py` | `branch_slug` defined before use; subprocess list form (no shell injection) |
| F-08 | `deployment/vm/morris-fleet-check.sh` | API key passed via curl config file, not argv (no longer in `ps aux`) |
| F-09 | `deployment/vm/agent-push.sh` | `StrictHostKeyChecking=accept-new` (was `=no`) |

---

## Morris Cron Status

The cron installer is at `deployment/morris/scripts/install-orchestrator-cron.sh` (idempotent, uses marker guard).

**Currently installed on Morris VM (after running installer):**

```
# morris-orchestrator-724
*/10 * * * *      orchestrator_loop.py --config ...    # queue health check + interventions
30 13 * * 1-5     orchestrator_loop.py --briefing-only  # morning agent status DM to Mark
```

**Gap fixed in PR #161 (subagent adding now):**

```
# morris-contact-tracking
0 8 * * 1-5       daily_contact_summary.py --config ... # daily contacts briefing to Mark
```

After PR #161 merges: re-run `bash /opt/morris/install-orchestrator-cron.sh` to install the contact summary cron.

---

## Deploy Instructions

### TL;DR — Consolidated Script (run this first)

A single script handles everything except the two steps that need portal access:

```bash
# Run from Morris VM (or any host with SSH to all agent VMs):
cd /home/hermes/dev/hpi-gorillacommerce/tech-dev-agents
bash deployment/vm/deploy-sprint-2026-04-26.sh
```

**What it automates (all 6 steps in sequence):**
1. `git pull origin main` (verifies clean main branch)
2. `push-code.sh all` — poller + phase runner + SDK tool to all dev agents, restart + smoke test
3. `agent-push.sh all` — SOUL, hermes-config, teams adapter, SDLC skills pull
4. Deploy hardened `hermes-log-sync.service` to each dev agent (STORY-728/730), daemon-reload, restart
5. Deploy promtail config to each dev agent (derrick gets derrick-specific config)
6. Morris VM — rsync orchestrator scripts, pip install deps, `/var/log/morris` setup, install cron entries, dry-run

**What the script does NOT cover (manual steps, see sections below):**
- **MANUAL-1**: DB migration `012_claim_heartbeat.sql` on the ops-console host (needs DB access)
- **MANUAL-2**: MDE (Defender for Endpoint) onboarding via security.microsoft.com portal (needs portal access)

If agents are mid-story when you run it, use `SKIP_PUSH_CODE=1` to skip the poller restart:
```bash
SKIP_PUSH_CODE=1 bash deployment/vm/deploy-sprint-2026-04-26.sh
# Then run push-code.sh manually when agents are idle:
bash deployment/vm/push-code.sh all
```

---

### MANUAL-1: Ops-Console DB Migration + Restart

**Changes:** Dispatch DB service (scope routing), dispatch route (`?preferred_scope=`), Entra auth enforcement, new DB migration for `claim_heartbeat_at`.

```bash
# On ops-console host:
cd /opt/ops-console   # adjust path to where ops-console runs
git pull origin main

# Apply migration 012 (claim heartbeat) — idempotent, safe to re-run:
psql $DATABASE_URL -f scripts/migrations/012_claim_heartbeat.sql

# Restart service
sudo systemctl restart ops-console
sudo systemctl status ops-console
```

**Verify:**
```bash
# Health check
curl -H "X-Api-Key: $OPS_CONSOLE_API_KEY" https://<ops-console>/api/health

# Heartbeat field present on claimed stories
curl -H "X-Api-Key: $OPS_CONSOLE_API_KEY" \
  "https://<ops-console>/api/dispatch/queue?include_claimed=true" \
  | python3 -c "import json,sys; q=json.load(sys.stdin); print(q.get('claimed',[{}])[0].keys())"
```

---

### 1. Ops-Console

**Changes:** Dispatch DB service (scope routing), dispatch route (`?preferred_scope=`), Entra auth enforcement, new DB migration for `claim_heartbeat_at`.

```bash
# On ops-console host:
cd /opt/ops-console   # adjust path to where ops-console runs
git pull origin main

# Check for and apply migration 012 (claim heartbeat):
ls scripts/migrations/ | grep 012
# Should show: 012_claim_heartbeat.sql
# Apply:
psql $DATABASE_URL -f scripts/migrations/012_claim_heartbeat.sql
# (migration is idempotent — safe to re-run)

# Restart service
sudo systemctl restart ops-console
sudo systemctl status ops-console
```

**Verify:**
```bash
# Health check
curl -H "X-Api-Key: $OPS_CONSOLE_API_KEY" https://<ops-console>/api/health

# New preferred_scope param works
curl -H "X-Api-Key: $OPS_CONSOLE_API_KEY" \
  "https://<ops-console>/api/dispatch/next?preferred_scope=backend"

# Heartbeat field present on claimed stories
curl -H "X-Api-Key: $OPS_CONSOLE_API_KEY" \
  "https://<ops-console>/api/dispatch/queue?include_claimed=true" \
  | python3 -c "import json,sys; q=json.load(sys.stdin); print(q.get('claimed',[{}])[0].keys())"
```

---

### 2. All Agent VMs (Dan, Derrick, Daisy, Devon)

**Changes:** `dispatch_poller.py` (heartbeat, jitter, scope, completions, adversarial reviewer, field validation, branch_slug fix), `adversarial_reviewer.py` (new), `sdlc_phase_runner.py` (focused prompts), `claude_sdk_tool.py` (deny-list), `hermes-log-sync.service` (crash fix), `agent-push.sh` (StrictHostKeyChecking), `morris-fleet-check.sh` (API key in curl config).

```bash
# From Morris VM — push to all agents:
cd /home/hermes/dev/hpi-gorillacommerce/tech-dev-agents
bash deployment/vm/agent-push.sh --target all --action poller
# This rsync's deployment/hermes/ to each agent VM and restarts dispatch-poller
```

**Verify each agent:**
```bash
ssh -p 443 azureagent@<VM_IP> -i ~/.ssh/id_ed25519_derrick
sudo systemctl status dispatch-poller
journalctl -u dispatch-poller -n 20
```

**Log-sync crash fix — apply to each agent VM:**
```bash
# On each agent VM (or automate via agent-push.sh if you extend it):
sudo cp deployment/vm/hermes-log-sync.service /etc/systemd/system/hermes-log-sync.service
sudo systemctl daemon-reload
sudo systemctl restart hermes-log-sync
sudo systemctl status hermes-log-sync

# Confirm it runs as hermes user (not root):
ps aux | grep hermes-log-sync
# Should show: hermes  ...  /bin/bash ...hermes-log-sync.sh

# Confirm no crash-loop:
journalctl -u hermes-log-sync --since "5 min ago" | grep -c "Start request repeated"
# Should be 0
```

**New env var (optional — off by default):**
```bash
# STORY-721 focused prompts — only enable after smoke test:
echo "FOCUSED_PHASE_PROMPTS=0" >> /opt/agent/.env   # already the default

# STORY-726 scope routing — set to match each agent's specialty:
echo "AGENT_PREFERRED_SCOPE=backend" >> /opt/agent/.env   # or frontend, devops, etc.
```

---

### 3. Security Baseline — All Agent VMs

**New:** `install-security-baseline.sh` installs auditd (syscall logging), ufw (firewall), fail2ban (SSH brute force), unattended-upgrades.

```bash
# On EACH agent VM:
sudo bash /opt/agent/scripts/install-security-baseline.sh

# Verify:
sudo systemctl is-active auditd    # should be: active
sudo ufw status                     # should show: 443/tcp ALLOW
sudo systemctl is-active fail2ban   # should be: active
```

**MDE (Defender for Endpoint) — manual step:**
1. Go to `security.microsoft.com` → Settings → Endpoints → Onboarding
2. Select Linux → download `mdatp_onboard.json`
3. `scp mdatp_onboard.json azureagent@<VM>:/opt/agent/mdatp_onboard.json`
4. Re-run `install-security-baseline.sh`

---

### 4. Morris VM — Queue Orchestrator (STORY-724)

**New:** Orchestrator runs every 10 min. 6 detectors (stale claims, repeated failures, PR conflicts, needs-info decay). 5 interventions (DM Mark, release claim, rebase, surface needs-info, load imbalance alert).

```bash
# SSH to Morris VM
ssh -p 443 azureagent@20.246.36.143 -i ~/.ssh/id_ed25519_derrick

cd /home/hermes/dev/hpi-gorillacommerce/tech-dev-agents
git pull origin main

# Deploy orchestrator scripts
rsync -av deployment/morris/scripts/ /opt/morris/ --exclude='*.pyc'

# Install Python dependencies
/opt/morris/venv/bin/pip install pyyaml requests

# Create log directory
sudo mkdir -p /var/log/morris
sudo chown hermes:hermes /var/log/morris

# Install cron entries (idempotent)
bash /opt/morris/install-orchestrator-cron.sh

# Required env vars in /opt/morris/.env (or /opt/agent/.env):
# OPS_CONSOLE_URL=https://your-ops-console-url
# OPS_CONSOLE_API_KEY=<key>
# MORRIS_MARK_CHAT_ID=<teams-chat-id-from-existing-STORY-044-setup>

# Test dry-run
/opt/morris/venv/bin/python /opt/morris/orchestrator_loop.py \
  --config /opt/morris/orchestrator_config.yaml \
  --dry-run

# Verify cron entries
crontab -l | grep morris
# Should show 3 entries: */10, 30 13, and 0 8 (contact summary)
```

**Config gates to flip after verification:**
```yaml
# /opt/morris/orchestrator_config.yaml
interventions:
  rebase:
    enabled: false    # → flip to true once STORY-722 confirmed stable in prod (after 1 week)
  release:
    story_702_merged: false  # → flip to true once DB migration 012 confirmed in prod
```

---

### 5. Morris VM — Contact Tracking (PR #161)

**New:** Morris tracks everyone who messages him. New contacts get an immediate DM alert to Mark. Daily summary at 08:00 UTC Mon-Fri.

```bash
# After PR #161 merges:
git pull origin main

# Deploy contact tracking scripts
rsync -av deployment/morris/scripts/contact_tracker.py /opt/morris/
rsync -av deployment/morris/scripts/daily_contact_summary.py /opt/morris/

# Create data directory for contacts.json
mkdir -p /opt/morris/data

# Re-run cron installer to pick up the new daily summary entry
bash /opt/morris/install-orchestrator-cron.sh

# Verify all 3 crons installed
crontab -l | grep morris
```

**How it works:**
- `contact_tracker.py` is called by Morris on each Teams message received. GC tenant validation (`@gorillacommerce.co`). Atomic JSON writes to `/opt/morris/data/contacts.json`. Returns `True` if new contact → triggers immediate DM to Mark.
- `daily_contact_summary.py` runs at 08:00 UTC Mon-Fri via cron. Posts `[BRIEFING]` DM listing everyone who messaged Morris that day with counts.

---

### 6. Morris VM — Self-Improvement Loop (PR #156)

**New:** After each story completes, Morris detects patterns in failures across stories. Proposes and applies Tier-1 changes (prompt/config tweaks) autonomously; escalates Tier-2 (code changes) for Mark approval. Starts in shadow mode (no writes, logs only).

```bash
# After PR #156 merges:
git pull origin main
rsync -av deployment/morris/scripts/improvement/ /opt/morris/improvement/
```

**Config (in orchestrator_config.yaml or improvement/config.py):**
```python
enabled = False       # kill switch — flip to True to activate
shadow_mode = True    # True = detect + log only, no writes/DMs
tier1_auto_apply = False  # True = apply Tier-1 proposals without approval
```

Start with `enabled=True, shadow_mode=True` to observe before enabling real writes.

---

## Smoke Test Checklist

Run after deploy, in order:

```bash
# 1. Ops-console health
curl -H "X-Api-Key: $OPS_CONSOLE_API_KEY" https://<ops-console>/api/health

# 2. Queue API returns heartbeat field
curl -H "X-Api-Key: $OPS_CONSOLE_API_KEY" \
  "https://<ops-console>/api/dispatch/queue?include_claimed=true" \
  | python3 -c "import json,sys; q=json.load(sys.stdin); \
    claimed=q.get('claimed',[]); \
    print('heartbeat field:', 'claim_heartbeat_at' in (claimed[0] if claimed else {}))"

# 3. Agent picks up a story (watch one agent's poller log)
journalctl -u dispatch-poller -f   # on any agent VM

# 4. Morris orchestrator dry-run
/opt/morris/venv/bin/python /opt/morris/orchestrator_loop.py \
  --config /opt/morris/orchestrator_config.yaml --dry-run

# 5. Log-sync not crash-looping
journalctl -u hermes-log-sync --since "5 min ago" \
  | grep -c "Start request repeated"   # should be 0

# 6. Adversarial reviewer wired (check poller logs after a Phase 8 completion)
grep "adversarial" /var/log/hermes/dispatch-poller.log | tail -5

# 7. Contact summary cron installed on Morris
crontab -l | grep contact-summary   # should show 0 8 * * 1-5 entry
```

---

## Monitoring & Enablement Guide

This section describes how to validate each new system as it goes live, using test tickets and log observation, and how Morris progressively takes over monitoring so humans can step back.

---

### Phase 0 — Pre-Launch Validation (before any agents pick up real work)

Do this immediately after deploying ops-console and agent VMs but before re-enabling the queue.

**1. Verify the migration applied:**
```bash
psql $DATABASE_URL -c "\d dispatch_items" | grep -E "claim_heartbeat|stale_release"
# Should show both columns. If missing, re-run 012_claim_heartbeat.sql.
```

**2. Orchestrator dry-run on Morris VM:**
```bash
/opt/morris/venv/bin/python /opt/morris/orchestrator_loop.py \
  --config /opt/morris/orchestrator_config.yaml --dry-run
# Expected output: "[DRY-RUN] would: ..." lines — no HTTP calls, no DMs sent.
# Exit 0 = config valid, detectors wired correctly.
```

**3. Adversarial reviewer reachable:**
```bash
# On any agent VM, verify the reviewer can call the Claude API:
/opt/agent/venv/bin/python -c "
from adversarial_reviewer import run_adversarial_review
print(run_adversarial_review.__doc__)
"
# Should print docstring without ImportError.
```

---

### Phase 1 — Test Ticket Walkthrough (Day 1)

Create synthetic stories to exercise each new system end-to-end. Run these before releasing the queue to real agent work.

#### Test 1: Heartbeat auto-release (STORY-702)

```bash
# 1. Enqueue a test story via ops-console API:
curl -X POST -H "X-Api-Key: $OPS_CONSOLE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "story_id": "TEST-heartbeat-001",
    "title": "Heartbeat test — do not work",
    "prompt": "Echo the word HEARTBEAT_TEST and stop.",
    "repo": "hpi-gorillacommerce/tech-dev-agents",
    "pr_branch": "test/heartbeat-001",
    "scope": "devops"
  }' \
  https://<ops-console>/api/dispatch/enqueue

# 2. Manually claim it (simulate an agent):
curl -X POST -H "X-Api-Key: $OPS_CONSOLE_API_KEY" \
  https://<ops-console>/api/dispatch/claim/TEST-heartbeat-001

# 3. Do NOT send any heartbeats. Wait 20 minutes.

# 4. Run the orchestrator manually to trigger auto-release:
/opt/morris/venv/bin/python /opt/morris/orchestrator_loop.py \
  --config /opt/morris/orchestrator_config.yaml

# 5. Verify the story is no longer claimed:
curl -H "X-Api-Key: $OPS_CONSOLE_API_KEY" \
  https://<ops-console>/api/dispatch/queue \
  | python3 -c "import json,sys; q=json.load(sys.stdin); \
    [print(s['story_id'], s['status']) for s in q.get('items',[]) \
     if 'TEST-heartbeat' in s['story_id']]"
# Expected: TEST-heartbeat-001  pending  (auto-released back to queue)
```

**Also verify:** Morris DM'd Mark with an intervention notice ("stale claim released").

#### Test 2: Adversarial review gate (STORY-723)

```bash
# Watch an agent complete a Phase 8 naturally (or trigger one on a test story).
# After Phase 8, check the poller log on that agent VM:
grep -A5 "adversarial" /var/log/hermes/dispatch-poller.log | tail -20

# Expected outcomes:
#   verdict=APPROVE  → story proceeds to complete normally
#   verdict=BLOCK    → story goes to needs_info, poller logs "adversarial review blocked"
#   verdict=WARN     → story completes with a warning note in the log
```

#### Test 3: Scope routing (STORY-726)

```bash
# Enqueue a backend-scoped story:
curl -X POST -H "X-Api-Key: $OPS_CONSOLE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"story_id":"TEST-scope-001","scope":"backend","prompt":"test","repo":"hpi-gorillacommerce/tech-dev-agents","pr_branch":"test/scope-001","title":"Scope test"}' \
  https://<ops-console>/api/dispatch/enqueue

# Verify that an agent with AGENT_PREFERRED_SCOPE=backend picks it up first.
# Check on that agent's VM:
journalctl -u dispatch-poller -n 30 | grep "TEST-scope-001"
```

#### Test 4: Contact tracking (Morris)

```bash
# From your Teams account, send Morris a direct message (anything).
# Within ~1 minute:
cat /opt/morris/data/contacts.json | python3 -m json.tool
# Should show your Teams user ID with message count = 1.

# If this is the first time you've messaged Morris:
# Mark should receive an immediate Teams DM: "New contact: <your name>"

# Next morning at 08:00 UTC, Mark should receive the daily briefing.
# To force-trigger it now for testing:
/opt/morris/venv/bin/python /opt/morris/daily_contact_summary.py \
  --config /opt/morris/orchestrator_config.yaml
```

---

### Phase 2 — Week 1 Human Monitoring Checklist

While the systems are new, a human (or Morris himself via the morning briefing) should check these daily for the first week.

#### Daily checks (5 minutes):

```bash
# 1. Orchestrator ran and found no stuck items
tail -50 /var/log/morris/orchestrator.log | grep -E "ERROR|WARN|intervention"
# Green: no output or "DRY-RUN would:" lines only

# 2. No agent crash-looping
for vm in <dan-ip> <derrick-ip> <daisy-ip> <devon-ip>; do
  echo "=== $vm ===" 
  ssh -p 443 azureagent@$vm -i ~/.ssh/id_ed25519_derrick \
    "journalctl -u dispatch-poller --since '24h ago' | grep -c 'ERROR'" 2>/dev/null
done

# 3. Queue not growing unbounded (stories completing, not piling up)
curl -H "X-Api-Key: $OPS_CONSOLE_API_KEY" \
  https://<ops-console>/api/dispatch/queue \
  | python3 -c "import json,sys; q=json.load(sys.stdin); \
    print('pending:', len(q.get('pending',[])), \
          'claimed:', len(q.get('claimed',[])), \
          'failed:', len(q.get('failed',[])))"

# 4. Heartbeat field being updated (agents healthy)
curl -H "X-Api-Key: $OPS_CONSOLE_API_KEY" \
  "https://<ops-console>/api/dispatch/queue?include_claimed=true" \
  | python3 -c "
import json,sys,datetime
q=json.load(sys.stdin)
for s in q.get('claimed',[]):
    hb = s.get('claim_heartbeat_at','never')
    print(s['story_id'], '→ last heartbeat:', hb)"
```

#### Config gates to flip after Week 1 verification:

| Gate | How to verify before flipping | Command to flip |
|------|------------------------------|-----------------|
| `story_702_merged: true` | Confirm `claim_heartbeat_at` column exists in prod DB | Edit `/opt/morris/orchestrator_config.yaml` |
| `interventions.rebase.enabled: true` | Confirm STORY-722 auto-rebase worked cleanly on at least 3 real PRs | Edit config |
| `FOCUSED_PHASE_PROMPTS=1` | Run 1 story per agent with flag on, compare output quality to baseline | `echo "FOCUSED_PHASE_PROMPTS=1" >> /opt/agent/.env` + restart |
| Self-improvement `shadow_mode: false` | Review 1 week of shadow-mode logs — did proposed changes make sense? | Edit improvement config |

---

### Phase 3 — Morris Takes Over

Once the systems have been stable for a week, Morris operates autonomously with minimal human intervention.

**What Morris does automatically (no action needed after setup):**

| System | Frequency | Morris action | Human needs to |
|--------|-----------|--------------|----------------|
| Queue orchestrator | Every 10 min | Detects stale claims, failed stories, PR conflicts. DMs Mark if intervention needed | Review Morris DMs, approve/reject suggested interventions |
| Morning briefing | 08:00 UTC Mon-Fri | Posts queue health summary + any overnight incidents to Mark's 1:1 | Glance at Teams in the morning |
| Contact tracking | On every Teams message | Records contact, DMs Mark immediately on first contact from anyone new | Nothing — it's passive |
| Daily contact summary | 08:00 UTC Mon-Fri | Lists everyone who messaged Morris yesterday with message counts | Optionally note anyone unexpected |
| Heartbeat auto-release | Via orchestrator | Releases stuck claims after 15 min of silence | Nothing — orchestrator handles it |
| Self-improvement (after shadow off) | Weekly | Proposes prompt/config improvements based on failure patterns. Tier-1 applies automatically; Tier-2 needs Mark approval | Review Teams DMs for Tier-2 proposals |

**Escalation path (what Morris DMs Mark about):**
1. Stale claim detected and released → informational, no action needed
2. Story failed 3+ times with same error → review the failure and potentially create a fix story
3. PR conflict detected → Morris requests human to resolve if auto-rebase is off
4. New external contact → review who it is
5. Self-improvement Tier-2 proposal → approve or reject via Teams reply

**The "fully handed off" state looks like this:**
- Morris VM crons running: `crontab -l | grep morris` shows 3 entries
- Orchestrator config gates flipped: `rebase.enabled: true`, `story_702_merged: true`
- Self-improvement shadow mode off: `shadow_mode: false`, `enabled: true`
- MDE enrolled on all VMs
- Mark gets a morning Teams DM each day from Morris — that's the health signal
- If Morris goes quiet (no morning DM), something is wrong with the cron or Morris VM

**Verify Morris is running autonomously:**
```bash
# Check last orchestrator run
tail -5 /var/log/morris/orchestrator.log
# Should show a timestamp within the last 10 minutes during business hours

# Check contact data is being updated
ls -la /opt/morris/data/contacts.json
# mtime should be recent if anyone has talked to Morris today

# Check Morris VM crons are live
ssh -p 443 azureagent@20.246.36.143 -i ~/.ssh/id_ed25519_derrick "crontab -l"
```

---

## Things NOT Deploying Yet (Deferred)

| Item | Why deferred | Next step |
|------|-------------|-----------|
| ~~STORY-730 RuntimeDirectory fix~~ | **Done — merged as PR #163** | Deploy with `hermes-log-sync.service` step below |
| MDE onboarding | Needs `mdatp_onboard.json` from M365 Defender portal | Manual by Mark/admin |
| `FOCUSED_PHASE_PROMPTS=1` | Feature flag, off by default | Enable after smoke test |
| `interventions.rebase.enabled=true` | Needs prod verification first | Flip after 1 week in prod |
| `story_702_merged: true` | Needs prod DB verification | Flip after confirming heartbeat field |
| Self-improvement `shadow_mode=false` | Needs observation period | Enable after 1 week shadow |

---

## Key Files Reference

| Artifact | Path |
|----------|------|
| Orchestrator config | `deployment/morris/scripts/orchestrator_config.yaml` |
| Cron installer | `deployment/morris/scripts/install-orchestrator-cron.sh` |
| Contact tracker | `deployment/morris/scripts/contact_tracker.py` |
| Daily summary | `deployment/morris/scripts/daily_contact_summary.py` |
| Security baseline installer | `deployment/vm/install-security-baseline.sh` |
| Log-sync service file | `deployment/vm/hermes-log-sync.service` |
| Heartbeat migration | `scripts/migrations/012_claim_heartbeat.sql` |
| Security review | `features/story-728-log-sync-crash-fix/security-review-2026-04-26.md` |
| Quality review | `features/story-724-morris-queue-orchestrator/quality-review-2026-04-26.md` |
| STORY-730 spec | `features/story-730-log-sync-runtime-dir/seed.md` |
