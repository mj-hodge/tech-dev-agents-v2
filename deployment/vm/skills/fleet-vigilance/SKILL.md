---
name: fleet-vigilance
description: Every 30 minutes, audit the dispatch fleet for stuck agents, ghost completions, stale claims, quota exhaustion, PR review backlog, and auth drift. Remediate what's safe; escalate what isn't. Silent when everything's healthy. Keep Mark out of the loop as much as possible.
triggers:
  - fleet check
  - fleet vigilance
  - fleet health
  - check agents
  - agents stuck
  - queue backed up
  - fleet audit
---

# Fleet Vigilance — Morris's Operational Heartbeat

You are the manager. Dan and Derrick do the coding. Your job when this skill fires is to catch problems before Mark has to.

Today's date is whatever `date -u +%Y-%m-%d` says.

## Goal

Run through **every check below**. Log OK/WARN/CRIT per check. At the end:

1. Update `/home/hermes/state/morris/fleet-health.md` with a timestamped snapshot.
2. If any check is CRIT, **DM Mark in Teams** with a concise summary (one paragraph + action taken).
3. If any check is WARN, log it to the state file but don't DM unless multiple warnings compound into a story.
4. If everything's OK and nothing changed since last check, say nothing — silence is success.

## CRITICAL — No Git Commits

**NEVER commit fleet-health data to any git repository.** Not to Morris's workspace, not to Dan's workspace, not to Derrick's workspace. Fleet-health snapshots go to `/home/hermes/state/morris/fleet-health.md` ONLY — a plain file on the local filesystem, not tracked by git. Git commits on agent workspaces pollute story branches and PRs. On 2026-04-18, 30+ `fleet-health:` snapshot commits were found on Dan's story branch, drowning actual work commits. This is a hard rule.

## CRITICAL — Take Action, Don't Just Log

When you detect a problem, **fix it yourself** if the remediation is defined below. Don't just log "WARN: duplicate claim detected" and move on. The whole point of fleet vigilance is autonomous remediation. Only escalate to Mark when the remediation requires human access (e.g., interactive auth, Azure portal).

## Check 0: Token quota pacing (RUN FIRST — most important check)

**This is the #1 priority check.** On 2026-04-18/19, both agents burned their entire weekly token allotment in 2 days with no warning. Mark was blindsided. Never again.

**Two data sources — run both:**

### 0a. Current 5-hour billing block (real-time pacing)

```bash
for AGENT in dan:20.228.224.243 derrick:20.121.210.186 daisy:20.98.231.234 devon:20.186.26.130; do
  NAME=$(echo $AGENT | cut -d: -f1)
  IP=$(echo $AGENT | cut -d: -f2)
  echo "=== $NAME ==="
  ssh -p 443 -o StrictHostKeyChecking=no -o ConnectTimeout=8 azureagent@$IP \
    "sudo -u hermes ccusage blocks --json 2>/dev/null" || echo "unreachable"
done
```

Parse the active block (where `isActive: true`). Key fields:
- `totalTokens` — tokens consumed in this 5-hour window
- `costUSD` — estimated cost
- `startTime` / `endTime` — when the window opened and when it closes
- `entries` — number of SDK sessions in this window

**Pacing math:**
- Time elapsed in block = now - startTime
- Time remaining = endTime - now
- Burn rate = totalTokens / elapsed_hours
- Projected total = burn rate × 5 hours
- If projected total > previous block's total × 1.5 → agent is burning faster than usual

### 0b. Weekly summary (trend)

```bash
ssh -p 443 azureagent@$IP "sudo -u hermes ccusage --period weekly --json 2>/dev/null"
```

Sum `totalTokens` across all days this week. Compare day-over-day.

**Severity:**
- **CRIT**: An agent's current 5h block has >2x the tokens of their average block → runaway session. DM Mark immediately with the story ID and recommend pausing.
- **CRIT**: An agent's dispatch-poller service is disabled (rate limit hit) → they're out of tokens. DM Mark with when they exhausted and estimated reset time.
- **WARN**: An agent's current block is >50% of their average block with >2 hours remaining → burning fast. Log it.
- **WARN**: Daily session count > 60 (of 80 cap) → approaching daily cap.
- **OK**: Burn rate is sustainable.

**Actions (TAKE THESE, don't just log):**
1. If CRIT runaway: `ssh $IP "sudo systemctl stop dispatch-poller"` to stop the bleeding, then DM Mark
2. If CRIT exhausted: check `cat /var/run/dispatch-poller-paused-until` for reset time, report to Mark
3. If WARN fast burn: cancel large/medium stories from queue, keep only small scope
4. Track daily session count: `ssh $IP "cat /home/hermes/state/<agent>/sdk-sessions-today.count"`

**Include in every fleet health report:**
```
Token pacing:
- Dan: 45K tokens this block (avg 60K), 2.3h remaining — ON TRACK
- Daisy: 120K tokens this block (avg 60K), 3.1h remaining — WARN: 2x average
- Devon: DISABLED (rate limit hit 14:30, reset unknown)
```

## Check 0b: Agent status updates (relay to Mark)

Dev agents write status updates to `/home/hermes/state/<agent>/pending-teams-messages.md` during dispatch work. These include: story starts, completions, failures, PR details, blockers, and questions. **Morris's job is to read these, synthesize a summary, and DM Mark.**

```bash
for AGENT in dan derrick daisy devon; do
  FILE="/home/hermes/state/$AGENT/pending-teams-messages.md"  # On the agent's VM
  ssh -p 443 azureagent@<IP> "cat $FILE 2>/dev/null" || true
done
```

Alternatively, read from the collected JSON blob ($DATA) — the wrapper now includes agent state files.

**What to do with the updates:**
1. Read all pending messages from all agents
2. Synthesize into a single consolidated update for Mark
3. DM Mark the summary (not every individual message — Mark wants signal, not noise)
4. For QUESTIONS: include the full question text and which story it's for — Mark needs to answer these
5. For failures: include the story ID, phase, and a one-line reason
6. After relaying, clear the pending messages files so they don't re-send

**Format for Mark DM:**
```
Fleet Update (10:30 UTC):
- Dan: Completed STORY-450 (PR #115), working on STORY-451 Phase 7
- Derrick: STORY-452 Phase 4 failed (timeout), auto-retrying
- QUESTION from Dan on STORY-451: "The seed says 'integrate with billing API' — which module? src/billing/ or src/payments/?"
- Token pacing: Dan 45% used (on track), Derrick 52% used (on track)
```

## Check 0c: Error detection (catch what the agents can't)

SSH into each agent and scan for ANY errors in the last 30 minutes — not just dispatch patterns. On 2026-04-20, Devon had a NameError in the phase runner that went undetected because Morris only looked for `[DISPATCH] FAILED` patterns. The phase runner crashed before it could log a dispatch-level failure.

```bash
for AGENT in dan:20.228.224.243 derrick:20.121.210.186 daisy:20.98.231.234 devon:20.186.26.130; do
  NAME=$(echo $AGENT | cut -d: -f1)
  IP=$(echo $AGENT | cut -d: -f2)
  echo "=== $NAME ==="
  ssh -p 443 -o StrictHostKeyChecking=no -o ConnectTimeout=8 azureagent@$IP "
    # 1. Python errors in dispatch-poller journal
    sudo journalctl -u dispatch-poller --since '30 min ago' --no-pager -q 2>/dev/null | \
      grep -iE 'Error|Exception|Traceback|FAILED|ModuleNotFoundError|NameError|ImportError|denied|TIMEOUT' | tail -5

    # 2. Phase runner crashes (fall back to single-shot = broken)
    sudo journalctl -u dispatch-poller --since '30 min ago' --no-pager -q 2>/dev/null | \
      grep -c 'falling back to single-shot' || echo 0
    echo 'single-shot-fallbacks'

    # 3. Repeated failures on same story (stuck in retry loop)
    sudo journalctl -u dispatch-poller --since '60 min ago' --no-pager -q 2>/dev/null | \
      grep -oP 'STORY-\d+.*FAILED' | sort | uniq -c | sort -rn | head -3

    # 4. Rate limit churn (claiming while rate-limited)
    sudo journalctl -u dispatch-poller --since '60 min ago' --no-pager -q 2>/dev/null | \
      grep -c 'RATE LIMITED' || echo 0
    echo 'rate-limit-hits'

    # 5. Poller service state
    systemctl is-active dispatch-poller 2>/dev/null
    systemctl is-enabled dispatch-poller 2>/dev/null
  " || echo "UNREACHABLE"
done
```

**Severity:**
- **CRIT**: `ModuleNotFoundError` or `NameError` or `ImportError` → code deployment is broken. The phase runner has a bug. DM Mark immediately with the exact error.
- **CRIT**: `single-shot-fallbacks > 0` → the phase runner is crashing and falling back to the old legacy path. This means our streamlined 2/4-session runner is NOT being used. DM Mark.
- **CRIT**: `dispatch-poller` is `inactive` or `disabled` → agent is dead. Check why (rate limit? crash? manual stop?)
- **WARN**: Same story FAILED 3+ times in 60 min → stuck story. Cancel it from the queue.
- **WARN**: `rate-limit-hits > 3` in 60 min → pre-claim probe isn't working or quota is exhausted.
- **OK**: No errors, poller active and enabled.

**Actions:**
1. NameError/ImportError → DM Mark with the error. This requires a code fix and redeploy (`push-code.sh`).
2. Single-shot fallback → Same as above. The phase runner is broken.
3. Stuck story → Cancel it: `curl -X DELETE -H "X-API-Key: $KEY" https://tech-dev-agents.gorillacommerce.ai/api/dispatch/queue/STORY-XXX`
4. Poller disabled → Check `/var/run/dispatch-poller-paused-until` for rate limit. If rate limit, report to Mark with reset time. If no pause flag, re-enable: `ssh $IP "sudo systemctl enable --now dispatch-poller"`

## Check 0d: Unpushed work + missing PRs (CRITICAL)

On 2026-04-20, Daisy completed all 4 phases of the dashboard story but never committed Phase 7+8. The work was stranded on her local disk with no PR. Mark had no idea it was done.

```bash
for AGENT in dan:20.228.224.243 derrick:20.121.210.186 daisy:20.98.231.234 devon:20.186.26.130; do
  NAME=$(echo $AGENT | cut -d: -f1)
  IP=$(echo $AGENT | cut -d: -f2)
  echo "=== $NAME ==="
  ssh -p 443 -o StrictHostKeyChecking=no -o ConnectTimeout=8 azureagent@$IP "
    # 1. Uncommitted changes on any story branch
    cd /home/hermes/workspace/tech-dev-agents 2>/dev/null
    BRANCH=\$(sudo -u hermes git branch --show-current 2>/dev/null)
    if [ \"\$BRANCH\" != 'main' ] && [ -n \"\$BRANCH\" ]; then
      DIRTY=\$(sudo -u hermes git status --porcelain 2>/dev/null | grep -v __pycache__ | wc -l)
      echo \"  branch=\$BRANCH uncommitted=\$DIRTY\"
    fi

    # 2. Unpushed commits (local commits not on remote)
    UNPUSHED=\$(sudo -u hermes git log origin/main..HEAD --oneline 2>/dev/null | wc -l)
    echo \"  unpushed_commits=\$UNPUSHED\"

    # 3. Check all repos for same issues
    for REPO in /home/hermes/dev/hpi-gorillacommerce/*/; do
      RNAME=\$(basename \$REPO)
      RBRANCH=\$(sudo -u hermes git -C \$REPO branch --show-current 2>/dev/null)
      if [ \"\$RBRANCH\" != 'main' ] && [ -n \"\$RBRANCH\" ]; then
        RDIRTY=\$(sudo -u hermes git -C \$REPO status --porcelain 2>/dev/null | grep -v __pycache__ | wc -l)
        if [ \$RDIRTY -gt 0 ]; then
          echo \"  \$RNAME: branch=\$RBRANCH uncommitted=\$RDIRTY\"
        fi
      fi
    done
  " || echo "  UNREACHABLE"
done
```

**Severity:**
- **CRIT**: uncommitted > 0 on a story branch AND no SDK running → agent left uncommitted work.
  **DO NOT auto-commit.** STORY-507 AC-4 manages partial-PR creation via SIGTERM handler.
  Auto-committing here creates duplicate partial PRs. Instead: DM Mark with the agent name,
  branch, and count of uncommitted files. Mark will decide whether to re-dispatch the story.
- **CRIT**: unpushed_commits > 0 AND no SDK running → work is done but not pushed.
  **DO NOT auto-push.** STORY-507 AC-4's SIGTERM handler handles commit+push automatically.
  If work is genuinely stranded (no SIGTERM was sent), DM Mark with the story ID and branch.
- **WARN**: story branch exists with commits but no open PR → DM Mark with the story ID and branch.
  Do NOT create a PR automatically — STORY-507 AC-4 creates partial PRs on SIGTERM.

**After detecting:** DM Mark with what was found. "Found uncommitted files on Daisy's story-491 branch — SDK not running. Awaiting Mark's instructions. (STORY-507 AC-4 will handle partial PR if re-dispatched.)"

## Check 0e: Agent dependency health

On 2026-04-20, Devon was missing `claude-agent-sdk` pip package and `gh` auth — every story failed instantly for an hour.

```bash
for AGENT in dan:20.228.224.243 derrick:20.121.210.186 daisy:20.98.231.234 devon:20.186.26.130; do
  NAME=$(echo $AGENT | cut -d: -f1)
  IP=$(echo $AGENT | cut -d: -f2)
  echo "=== $NAME ==="
  ssh -p 443 -o StrictHostKeyChecking=no -o ConnectTimeout=8 azureagent@$IP "
    # SDK tool works?
    sudo -u hermes python3 -c 'from claude_agent_sdk import query; print(\"sdk: OK\")' 2>&1 || echo 'sdk: MISSING'

    # gh auth?
    sudo -u hermes gh auth status 2>&1 | grep -q 'Logged in' && echo 'gh: OK' || echo 'gh: NOT AUTHENTICATED'

    # ccusage installed?
    which ccusage >/dev/null 2>&1 && echo 'ccusage: OK' || echo 'ccusage: MISSING'

    # Log-sync running?
    systemctl is-active dispatch-log-sync 2>/dev/null | grep -q active && echo 'log-sync: OK' || echo 'log-sync: NOT RUNNING'
  " || echo "  UNREACHABLE"
done
```

**Severity:**
- **CRIT**: `sdk: MISSING` → `sudo pip3 install claude-agent-sdk --break-system-packages --ignore-installed typing_extensions`
- **CRIT**: `gh: NOT AUTHENTICATED` → Copy PAT from Dan: `ssh dan "sudo -u hermes cat /home/hermes/.git-credentials" | grep -oP '(?<=x-access-token:)[^@]+' | ssh $IP "sudo -u hermes gh auth login --with-token"`
- **WARN**: `ccusage: MISSING` → `sudo npm install -g ccusage@18.0.11`
- **WARN**: `log-sync: NOT RUNNING` → Install dispatch-log-sync and gateway-log-sync services (see NEW-AGENT-PROCESS.md)

## Check 0f: Story completion quality

After any story completes, verify it actually produced a PR with deliverables.

```bash
# Query recently completed stories
curl -s -H "X-API-Key: $KEY" "https://tech-dev-agents.gorillacommerce.ai/api/dispatch/history?limit=10&status=completed"
```

For each completed story:
- **CRIT**: `commit_sha` is null → ghost completion, no work shipped
- **CRIT**: no open PR for the story branch → work completed but invisible to Mark
- **WARN**: PR exists but has no test files in diff → Phase 7 may have been skipped
- **WARN**: PR exists but features/story-NNN/ has < 2 deliverables → phases were skipped

**Actions:**
1. Ghost completion → re-enqueue the story
2. No PR → create one: `gh pr create` from the story branch
3. Missing deliverables → comment on PR noting what's missing

## Check 1: Queue state + flow

Pull the central queue. For each item, evaluate.

```bash
curl -s -H "X-API-Key: $(sudo grep ^OPS_CONSOLE_API_KEY /opt/agent/.env | cut -d= -f2)" \
  https://tech-dev-agents.gorillacommerce.ai/api/dispatch/queue?include_claimed=true
```

- **CRIT:** `pending > 10` AND `claimed == 0` for ≥30 min → agents aren't claiming. Go to Check 3.
- **CRIT:** Any `claimed` item with `claimed_at > 60 min ago` AND no SDK process on that agent AND no rate-limit pause flag → **stuck claim**. See Remediation A.
- **Ghost claim (post-auto-retry): WARN after 5 min, CRIT after 10 min.** A story `claimed_by=X` while agent X's dispatch-poller log (last 5–10 min window, Loki `{agent="X", job="dispatch-poller"}`) contains only `[DISPATCH] queue empty` lines (no `Claimed STORY-*`, no `busy, skipping`, no `Starting SDK`) → **ghost claim produced by a retry path that marked the row claimed but never invoked the phase runner**. Auto-remediate by calling `POST /api/dispatch/fail/{story_id}?repo=<repo>` (releases the claim) then `POST /api/dispatch` to re-enqueue with the same prompt. Root cause was fixed in `fix/claim-after-retry-ghost-claim` — this detector is defense-in-depth.
- **WARN:** `pending > 20` → backlog forming. Consider cancelling obsolete items.

## Check 2: Ghost-completion audit (today's fraud pattern)

Query postgres on the ops-console VM for duplicate SHAs — the signature of stale-SHA fraud we saw on 2026-04-15.

```sql
SELECT commit_sha, count(*) as dupes, array_agg(story_id) as stories
FROM dispatch_items
WHERE commit_sha IS NOT NULL
  AND completed_at > now() - interval '2 hours'
GROUP BY commit_sha
HAVING count(*) > 1;
```

- **CRIT:** Any row returned → ghost completions happened in the last 2h. The STORY-253 guard should have prevented this; the fact that it didn't means either the poller's validation regressed OR there's a new bypass. DM Mark with the list + STOP any further dispatch until diagnosed.
- **OK:** Zero rows.

## Check 3: Per-agent SDK state

SSH into each dev agent (Dan + Derrick — skip Morris, you ARE Morris) and inspect:

```bash
for AGENT in dan:20.228.224.243 derrick:20.121.210.186; do
  NAME=$(echo $AGENT | cut -d: -f1)
  IP=$(echo $AGENT | cut -d: -f2)
  ssh -p 443 -o StrictHostKeyChecking=no -o ConnectTimeout=8 azureagent@$IP "
    echo -n '  sdk_count='; ps aux | grep -c '[c]laude_sdk_tool.py'
    echo -n '  paused_until='; [ -f /var/run/dispatch-poller-paused-until ] && cat /var/run/dispatch-poller-paused-until || echo 'none'
    echo -n '  local_queue='; sudo -u hermes python3 -c 'import sys; sys.path.insert(0,\"/opt/agent\"); from work_queue import WorkQueue; print(WorkQueue().list())'
    echo -n '  poller_active='; sudo systemctl is-active dispatch-poller
    echo -n '  claude_auth='; sudo -u hermes claude auth status 2>&1 | grep -oE 'loggedIn.: (true|false)' || echo 'unknown'
  "
done
```

For each agent, classify:

- **CRIT:** `poller_active != active` → poller died. `sudo systemctl restart dispatch-poller` and log it.
- **CRIT:** `claude_auth` shows `loggedIn: false` → OAuth token expired. This means the 6h keepalive cron isn't running. Check `sudo -u hermes crontab -l | grep 'claude -p ping'` — if missing, install it per `deployment/vm/NEW-AGENT-PROCESS.md`. DM Mark to re-auth interactively.
- **WARN:** `paused_until` non-empty → agent is rate-limited. This is self-healing (the poller auto-unpauses at reset time). Log when the reset will happen but don't DM unless Mark explicitly asked.
- **WARN:** `local_queue` has item but `sdk_count == 0` AND no pause flag → stale local queue entry. Remediation A.
- **OK:** sdk_count > 0 → actively working. Record the story_id and duration.

## Check 4: Stale PRs

PRs open > 6h without review comments = review backlog.

```bash
for REPO in tech-dev-agents advertising-amazon product-health-dashboard tech-datawarehouse tech-gc-knowledgebase; do
  gh pr list --repo hpi-gorillacommerce/$REPO --state open --json number,title,author,createdAt,reviewDecision --jq \
    '.[] | select((now - (.createdAt | fromdateiso8601)) > 21600) | "\(.number) \(.title) by \(.author.login) review=\(.reviewDecision // "none")"'
done
```

- **WARN** (not CRIT) for any PR > 6h without review. Review them yourself if you can (use the `github-code-review` skill). If review is gnarlier than you can handle, DM Mark the list.
- **CRIT** for any PR > 48h still unmerged. Mark wants to move fast; a 2-day unmerged PR is a process failure.

## Check 4b: SDLC deliverable audit on open PRs

For each open PR from Check 4, verify the agent actually followed the SDLC:

```bash
# For each open PR, check if features/story-NNN-*/ has required deliverables
gh pr view $PR_NUMBER --repo hpi-gorillacommerce/$REPO --json files --jq '.files[].path' | grep 'features/story-'
```

Required deliverables by scope (automated dispatch — Phases 8b/11 dropped, Morris PR review covers those):
- **Small**: `seed.md`, `test-design.md`
- **Medium**: `seed.md`, `analysis.md`, `feature-spec.md`, `test-design.md`
- **Large**: all of Medium + `research.md`

- **WARN** PR missing `seed.md` → agent skipped Phase 1 entirely (should never happen)
- **WARN** PR missing `test-design.md` → agent skipped Phase 7 (tests before code)
- **CRIT** PR has implementation code but zero test files in `tests/` → shipped untested code

**When you find missing deliverables:** comment on the PR with what's missing and request the agent redo those phases before merge. Do NOT approve PRs that skip the SDLC.

## Check 4c: needs_info stories — READ, CLASSIFY, AUTO-ANSWER OR ESCALATE

**Added 2026-04-24 after the STORY-505/528/529 loop** where stories kept
bouncing back to needs_info after `/resume` because nothing triaged the
question. Morris's job: read every needs_info question, classify it, act.

```bash
# Pull the needs_info bucket from ops-console
K=$(grep ^OPS_CONSOLE_API_KEY= /opt/agent/.env | cut -d= -f2-)
curl -s -H "X-API-Key: $K" http://tech-dev-agents.gorillacommerce.ai:8005/api/dispatch/queue \
  | python3 -c "import json,sys;[print(i['story_id'], i.get('repo',''), i.get('claimed_by',''), i.get('needs_info_path','')) for i in json.load(sys.stdin).get('needs_info', [])]"
```

For each needs_info story:

### Step 1 — Read the QUESTION.md from the claiming agent's VM

```bash
# AGENT_IP comes from deployment/vm/agent-registry.json
ssh -p 443 azureagent@$AGENT_IP "sudo cat /home/hermes/dev/hpi-gorillacommerce/$REPO/$QUESTION_PATH"
```

### Step 2 — Classify the question

Read the QUESTION.md and decide which bucket it belongs to. **Default is
DECIDE + ACT.** Mark's 2026-04-24 guidance, verbatim:

  > "if morris can answer the question he should resume not wait for me"
  > "he should only ask me if he's unsure"

Operating principle: **you are the engineering manager.** You own scope
calls, phase sequencing, dispatch framing, agent assignment, and every
call that doesn't reach outside `CLAUDE.md`, the seed, `backlog.md`, and
prior-merged stories. Do not escalate those — decide, write the answer
into QUESTION.md, and `/resume`.

**Only ask Mark when you are genuinely unsure.** The test is not "is this
important?" — it's "can I give a confident answer given what I know?" If
yes, act. If no, escalate. Err on the side of action when the downside is
a re-run; err on the side of escalation only when the downside is
production pain (security, data loss, irreversible deploy, broken
customer contract).

**A. Synthetic "missing-prior-phase" question** (auto-generated by the
phase runner). Signature: title `QUESTION — STORY-X / Phase N`, body says
"rc=0 but did not produce the expected deliverable", suggests "run earlier
phase first" or "re-dispatch with Large scope".

**Morris action:** auto-resume. PHASES_LARGE expansion handles the re-fill.
```bash
curl -s -X POST -H "X-API-Key: $K" http://tech-dev-agents.gorillacommerce.ai:8005/api/dispatch/resume/$STORY_ID
```
Log: `AUTO-RESUMED synthetic needs_info: STORY-X (phase N, stale-phase-skip question).`

**B. "Phase 8 ghost completion" question** (Phase 8 guard fired).
Signature: body says "Phase 8 returned rc=0 but 0 new commits vs origin/main".

**Morris action:** auto-answer with a firmer retry prompt, then resume.
Append the answer to the agent's QUESTION.md so the next claim sees intent,
then POST /resume:
```bash
# On the claiming agent's VM
ssh -p 443 azureagent@$AGENT_IP "sudo -u hermes bash -c 'cat >> $QUESTION_PATH <<EOF

---

## Answer (auto from Morris, $(date -u +%Y-%m-%dT%H:%M:%SZ))

Re-dispatch with explicit coding language. Next run must:
1. Read features/$STORY_FOLDER/seed.md and the existing test-design.md
2. Write the implementation to $BRANCH
3. git add, commit, git push
4. gh pr create against main

Do not exit without producing at least one new commit.
EOF'"
curl -s -X POST -H "X-API-Key: $K" http://tech-dev-agents.gorillacommerce.ai:8005/api/dispatch/resume/$STORY_ID
```
Log: `AUTO-ANSWERED+RESUMED Phase 8 ghost: STORY-X (wrote explicit-code directive).`

Escalate to Mark ONLY if the SAME story hits Phase 8 ghost **twice** after
auto-answer — that's a persistent-stuck, not a misunderstood prompt.

**C. Manager-scope real question** (Morris can decide).

These are questions Morris is qualified to answer as engineering manager:

- Scope reclassification (Small vs Medium vs Large): pick based on files-touched + new-deliverables in the seed.
- Phase sequencing / which phase to run next: follow CLAUDE.md's path.
- Which agent should pick it up (role=developer vs specific agent): based on workload and specialization.
- Retry/no-retry calls on transient failures.
- "Do I use the pattern from STORY-Y?" when STORY-Y is already merged and the pattern is obvious.
- "Is deliverable X the right output for Phase N?" per CLAUDE.md's phase table.
- Dispatch-prompt re-phrasing questions.

**Morris action:** append the answer to QUESTION.md, then POST /resume.
Format the answer clearly with `## Answer (auto from Morris, <TS>)` so the
agent sees intent on the next claim. Log:
`AUTO-ANSWERED+RESUMED manager-scope: STORY-X ({short-gist-of-answer}).`

**D. Escalate ONLY when you are genuinely unsure.**

Not "the question is important" — every question is important. The test:
**after reading the seed, `CLAUDE.md`, `backlog.md`, and related merged
stories, do you have a confident answer?** If yes → it's C (act). If no →
escalate. Typical patterns that survive the "unsure" bar:

- You have no codebase precedent for this decision (first of its kind).
- The seed's stated constraints contradict each other and you can't tell
  which takes priority.
- Customer/business intent is ambiguous and not inferrable from context.
- The downside of being wrong is irreversible (prod data loss, security
  posture change, breaking external contract).
- Cost of acting > cost of a one-hour delay waiting for Mark.

**Morris action on escalation:** DM Mark in Teams:
```
ESCALATION on {STORY_ID} ({REPO}) — genuinely unsure, need your call:
  path: {question_path}
  asker: {agent}
  question: {first 3 sentences of actual ask}
  why-I'm-stuck: {one line — what's missing that you'd have}
  my-best-guess: {the option you'd pick if forced; saves Mark typing}
Action: reply with pick, I'll write + /resume.
```
Do NOT auto-resume.

Re-read the Mark quote before every escalation:
  > "he should only ask me if he's unsure"
If you can rationalize a defensible answer in one paragraph, you are not
unsure — you are hesitating. Act.

### Step 3 — Detect + cancel ghost enqueues (no seed on disk)

If needs_info_path includes a bare `story-N/` path (no slug, e.g.
`features/story-531/QUESTION.md`), check whether `features/story-N-*/`
exists on origin/main. If NOT, the story is a ghost enqueue — no work to
do.

```bash
# On a dev box or via gh api
gh api repos/hpi-gorillacommerce/$REPO/contents/features?ref=main \
  --jq '.[] | select(.name | startswith("story-'$N'-")) | .name' | head -1
```

If that's empty → **DB-cancel the ghost entry:**
```bash
ssh -p 443 azureagent@tech-dev-agents.gorillacommerce.ai "sudo docker exec 94813f661cd7_ops-console-postgres psql -U ops_console -d ops_console -c \"UPDATE dispatch_items SET status='cancelled', cancelled_at=NOW() WHERE story_id='$STORY_ID' AND status='needs_info';\""
```

Log: `CANCELLED ghost enqueue: STORY-X (no seed on main, bare folder path).`

### Step 4 — Summary line at end of vigilance run

Append to `/home/hermes/state/morris/fleet-health.md`:
```
needs_info triage: {auto_resumed} auto-resumed, {cancelled} cancelled, {escalated} escalated to Mark.
```

**Do NOT ignore needs_info items.** Silent needs_info stories rot — agents
can't pick them up, the queue appears empty when it isn't, and Mark loses
visibility. Every vigilance cycle must terminate needs_info by one of the
three actions above.

## Check 4d: Open PRs with failing CI — auto-dispatch fix story

**Added 2026-04-24** so red CI on a PR doesn't have to wait for the
4-hour `Morris PR Review Cycle` — every 30-min heartbeat catches it.
Same logic as `review-prs` Step 3a, run standalone here.

For each open PR across the repos Morris watches (`tech-dev-agents`,
`advertising-amazon`, `tech-project-mapping`):

```bash
for REPO in tech-dev-agents advertising-amazon tech-project-mapping; do
  gh pr list --repo hpi-gorillacommerce/$REPO --state open \
    --json number,title,headRefName,statusCheckRollup \
    --jq '.[] | select((.statusCheckRollup // []) | map(.conclusion=="FAILURE") | any) | "\(.number) \(.headRefName) \(.title)"'
done
```

For every PR returned (one per line: `<number> <branch> <title>`):

### Step 1 — idempotency (don't pile fix-stories on the same PR)

```bash
EXISTING=$(curl -s -H "X-API-Key: $OPS_CONSOLE_API_KEY" \
  http://tech-dev-agents.gorillacommerce.ai:8005/api/dispatch/queue \
  | python3 -c "import json,sys;d=json.load(sys.stdin);print(any('PR #$PR_NUMBER' in (i.get('prompt') or '') for b in ('pending','in_progress','claimed','needs_info') for i in d.get(b, [])))")
[ "$EXISTING" = "True" ] && { echo "fix-story already in queue for PR #$PR_NUMBER"; continue; }
```

### Step 2 — pull failure logs so the dispatch prompt is specific

```bash
RUN_ID=$(gh pr view $PR_NUMBER --repo hpi-gorillacommerce/$REPO \
  --json statusCheckRollup --jq '.statusCheckRollup[0].detailsUrl' \
  | grep -oE 'runs/[0-9]+' | cut -d/ -f2)
FAILURES=$(gh api repos/hpi-gorillacommerce/$REPO/actions/runs/$RUN_ID/jobs \
  --jq '.jobs[] | select(.conclusion=="failure") | "\(.id) \(.name)"')
ERROR_BLOB=""
echo "$FAILURES" | while read JOB_ID JOB_NAME; do
  ERR=$(gh api repos/hpi-gorillacommerce/$REPO/actions/jobs/$JOB_ID/logs 2>&1 \
    | grep -E 'FAIL|Error|assert|Traceback' | tail -8)
  ERROR_BLOB="$ERROR_BLOB\n- $JOB_NAME:\n$ERR\n"
done
```

### Step 3 — pick next free story_id and enqueue

```bash
NEXT_ID=$(sudo docker exec 94813f661cd7_ops-console-postgres psql -U ops_console -d ops_console -tAc \
  "SELECT 'STORY-' || (max(substring(story_id from 'STORY-([0-9]+)')::int) + 1)::text FROM dispatch_items;" \
  | tr -d ' ')

cat > /tmp/fix-dispatch-$PR_NUMBER.json <<JSON
{
  "story_id": "$NEXT_ID",
  "repo": "$REPO",
  "scope": "small",
  "prompt": "$NEXT_ID — Fix CI failures on PR #$PR_NUMBER ($PR_TITLE). Checkout branch $BRANCH. Failing jobs:\n$ERROR_BLOB\nFix root causes. Commit to branch $BRANCH, push. Do NOT open a new PR — push to existing one. When CI is green, comment on PR #$PR_NUMBER confirming fixes.",
  "enqueued_by": "morris-vigilance-autofix",
  "cross_story_reference": true
}
JSON
curl -s -X POST -H "X-API-Key: $OPS_CONSOLE_API_KEY" \
  -H "Content-Type: application/json" \
  --data @/tmp/fix-dispatch-$PR_NUMBER.json \
  http://tech-dev-agents.gorillacommerce.ai:8005/api/dispatch
```

### Step 4 — leave a one-line comment on the PR

```bash
gh pr comment $PR_NUMBER --repo hpi-gorillacommerce/$REPO \
  --body "Heartbeat autofix dispatched as $NEXT_ID (in queue). The original
agent (or whoever claims) will push fixes to \`$BRANCH\`."
```

### Severity

- **WARN** for every red-CI PR detected — this is autonomous remediation,
  not an alert; just log the action in fleet-health.md.
- **CRIT** only if the SAME PR fails CI 3+ times after 3 separate autofix
  dispatches. That means the autofix isn't working — DM Mark with the
  pattern (`PR #N has had 3 fix-stories dispatched, still red. Last error:
  <last_error_blob>. Likely needs Mark's eyes on it.`)

End of cycle, append to fleet-health.md:
```
PR autofix: {N} PRs found red, {M} fix-stories dispatched (skipped {K} already in queue), {L} escalated.
```

## Check 5: Failed stories

Stories in `failed` status are agent-reported failures that haven't been retried.

```sql
SELECT story_id, claimed_by, completed_at::timestamp(0) FROM dispatch_items
 WHERE status = 'failed' AND completed_at > now() - interval '24 hours'
 ORDER BY completed_at DESC;
```

- **WARN** if any. Review each: if the failure looks transient (rate limit, network), re-pend it. If it looks like genuine agent struggle, DM Mark with the prompt text.

## Check 6: Graph API / Teams health

```bash
# Count recent Graph errors in Morris's own journal
sudo journalctl -u hermes-gateway --since "15 min ago" --no-pager 2>/dev/null | grep -cE '502|504|Bad Gateway|Gateway Timeout'
```

- **WARN** > 30 errors in 15 min = Graph is having a bad time (we saw this when chat membership changes today).
- **CRIT** > 100 errors = something is wrong on our end. Check if the OAuth token is still valid.

## Check 7: VM health

```bash
# On each agent VM
ssh azureagent@$IP "df -h / | awk 'NR==2 {print \$5}' | tr -d '%'"  # disk usage %
ssh azureagent@$IP "free -m | awk '/^Mem:/ {print int(\$3*100/\$2)}'"  # mem usage %
```

- **CRIT** disk > 85% or mem > 90%.
- **WARN** disk > 70% or mem > 80%.

## Check 8: Post-Merge Deploy + Re-Enqueue Sweep (STORY-795)

**Purpose:** Close the manual loop Mark had to do after every deploy-path merge (2026-04-29
incident: STORY-759 merged at 09:03Z → no auto-deploy → 11 stories stuck → Mark spent ~1h
doing push-code.sh + re-enqueue by hand). This check fires every heartbeat cycle and detects
merges to `deployment/hermes/*` or `deployment/morris/*` since the last run; if found, it
deploys, then re-enqueues eligible failed dispatch items.

**Implementation:** `deployment/morris/scripts/post_merge_sweep.py`
invoked via `heartbeat-collector.py:_run_post_merge_sweep()`.

**State file:** `/home/hermes/state/morris/last-fleet-vigilance-merge-sweep.txt`
Format: `<SHA> <ISO8601-timestamp>` — git-untracked, do NOT commit.

**Env vars:**
- `FLEET_SWEEP_LOOKBACK_HOURS` — lookback window for failed-item query (default 24)
- `FLEET_SWEEP_DEPLOY_TIMEOUT_SEC` — push-code.sh timeout in seconds (default 900 = 15 min)
- `OPS_CONSOLE_API_KEY` — **required**; raises `ValueError` and surfaces `error_unavailable` if unset

**Command (manual trigger):**
```bash
cd /home/hermes/workspace/tech-dev-agents
OPS_CONSOLE_API_KEY=$(sudo grep ^OPS_CONSOLE_API_KEY /opt/agent/.env | cut -d= -f2) \
  python3 deployment/morris/scripts/post_merge_sweep.py
```

**Merge detection (critical implementation note):**
Uses `git log --since=<last_ts> --pretty=format:%H %aI %s -- deployment/hermes/ deployment/morris/`
WITHOUT `--no-merges`. This repo lands features via merge commits; `--no-merges` would filter
out exactly the commits this check needs to detect.

**First-run behaviour:** On first run (no state file), writes HEAD SHA + current timestamp
to the state file and returns. The next cycle is the first real scan. This prevents a
flood of false-positive deploys from historical merges on initial deployment.

**Failed-item eligibility allowlist** (re-enqueue only if `failure_reason` starts with):
- `branch_setup_failed:` — branch setup race condition, safe to retry after redeploy
- `phase_progress_stalled:` — agent made no progress, safe to retry
- `sdk_died_no_phase_end:` — SDK crash with no clean phase end, safe to retry

Add new prefixes here as STORY-762 categorises additional failure classes.

**Severity:**
- **CRIT**: push-code.sh exits non-zero or times out. DM Mark. Re-enqueue sweep ABORTED.
- **WARN**: Deploy succeeded but some re-enqueues failed (requeued=0, skipped>0).
- **OK**: No new merges, OR deploy + re-enqueue completed successfully.
- **error_unavailable**: OPS_CONSOLE_API_KEY unset, or module import failure.

**DM template (CRIT):**
```
[CRIT] Check 8 Post-Merge Sweep: deploy FAILED after merge <SHA[:8]>.
push-code.sh: <last 200 chars of output>
Re-enqueue sweep ABORTED — manual deploy required.
```

**DM template (OK with re-enqueue):**
```
[FLEET-SWEEP] Post-merge deploy <SHA[:8]> + re-enqueue: N stories. Smoke: ✓. Merges: N. Skipped: N.
```

**Kill switch:** Delete `deployment/morris/scripts/post_merge_sweep.py`'s executable bit,
OR comment out `_run_post_merge_sweep()` in `heartbeat-collector.py`. Document the reason
in `fleet-health.md`.

**Audit trail:** Every sweep cycle appends `[POST-MERGE-SWEEP]` lines to
`/home/hermes/state/morris/fleet-health.md` with timestamps + outcome counts (AC-10).

**Status-line format:** `[FLEET-VIGILANCE Check 8] Post-Merge Deploy + Re-Enqueue Sweep: OK — [FLEET-SWEEP] ...`

---

## Checks 9–14: Fleet-Vigilance Blind-Spot Closures (STORY-767)

Added 2026-04-30 after the 2026-04-29/30 incident (Devon + Daisy VM outage, push-code.sh hangs,
code-drift, NULL failure_reason spike, zombie heartbeat, no-seed dispatch storm).

These checks run via `deployment/morris/scripts/blind_spot_checks.py`, invoked from
`heartbeat-collector.py` after existing checks 0–8.  Each check is independently
enable/disable-able via env var (all default enabled).

**State file:** DM suppression persists to `/home/hermes/state/morris/vigilance-dm-suppression.json`
(git-untracked).  Do NOT commit this file.

### Check 9: VM Reachability

**Purpose:** Detect when an agent VM is completely unreachable — the blind spot that caused
30+ minutes of undetected outage for Devon and Daisy.

**Command:**
```bash
for AGENT in dan:20.228.224.243 derrick:20.121.210.186 daisy:20.98.231.234 devon:20.186.26.130; do
  NAME=$(echo $AGENT | cut -d: -f1)
  IP=$(echo $AGENT | cut -d: -f2)
  ssh -p 443 -o StrictHostKeyChecking=no -o ConnectTimeout=8 azureagent@$IP "echo PONG" \
    && echo "$NAME: OK" || echo "$NAME: UNREACHABLE"
done
```

**Severity:**
- **CRIT**: 2+ agents unreachable (systemic). DM Mark with VM names + last successful ping.
- **WARN**: 1 agent unreachable. Log only.
- **OK**: All agents respond PONG.

**DM template:** `[CRIT] Check 9 VM Reachability: N agents unreachable: <names>. Check Azure VM status.`

**Remediation:** Azure portal → check VM power state → Start if stopped. If still unreachable after restart, escalate to Mark.

**Env var:** `FV_CHECK_9_VM_REACH=1` (default enabled)

**Status-line format:** `[Check 9 VM Reach] OK: dan ✓ derrick ✓ daisy ✓ devon ✓`

---

### Check 10: Stuck push-code.sh

**Purpose:** Detect when a deploy script hangs — the blind spot that caused >60 min of unresolved
deploys to Devon and Daisy while agents ran 6-hour-old code.

**Command:**
```bash
pgrep -f "push-code.sh"  # get PIDs
ps -o etime= -p <PID>    # check elapsed time
```

**Severity:**
- **CRIT**: Any push-code.sh process older than 15 min. DM Mark with PID + age + target VM.
- **OK**: No push-code.sh processes, or all are < 15 min old.

**DM template:** `[CRIT] Check 10: push-code.sh stuck! PID <pid> running >15 min. Kill it and verify deploy completed: kill -9 <pid>`

**Remediation:** `kill -9 <pid>`. Then manually verify each agent's `/opt/agent/dispatch_poller.py` mtime matches the latest merge. Re-run `push-code.sh` if needed.

**Env var:** `FV_CHECK_10_STUCK_DEPLOY=1`; threshold `FV_STUCK_DEPLOY_MIN=15`

**Status-line format:** `[Check 10 Stuck Deploy] OK: no push-code processes`

---

### Check 11: Code-Version Drift

**Purpose:** Detect when agents are running old code after a merge — the blind spot that caused
6+ hours of stale-code execution during the 2026-04-29/30 incident.

**Command:**
```bash
# 1. Get latest hermes/* merge timestamp
git log -1 --format=%ci -- deployment/hermes/

# 2. For each agent VM, get dispatch_poller.py mtime
ssh -p 443 -o ConnectTimeout=8 azureagent@$IP "stat -c %Y /opt/agent/dispatch_poller.py"
```

**Severity:**
- **CRIT**: 2+ agents drifted (mtime < merge_ts − 1h). DM Mark.
- **WARN**: 1 agent drifted. Log only.
- **OK**: All agents within 1h of latest hermes/* merge.

**DM template:** `[CRIT] Check 11: Code drift on <N> agents: <agent> (<Xh>), ... Run ./deployment/vm/push-code.sh all`

**Remediation:** `./deployment/vm/push-code.sh all` or target specific agents.

**Env var:** `FV_CHECK_11_CODE_DRIFT=1`; threshold `FV_DRIFT_THRESHOLD_SEC=3600`

**Status-line format:** `[Check 11 Code Drift] CRIT: drifted: daisy (7h), devon (7h)`

---

### Check 12: NULL failure_reason Spike

**Purpose:** Detect when dispatch failures are not recording their failure reason —
the blind spot that caused 11 stories to fail silently during the 2026-04-29/30 incident.

**Command (SQL on dispatch DB):**
```sql
SELECT count(*) AS count FROM dispatch_items
WHERE status = 'failed'
  AND failure_reason IS NULL
  AND failed_at > now() - interval '1 hour';
```

**Severity:**
- **CRIT**: count > 5 (data-collection broken). DM Mark.
- **WARN**: count 1–5. Log only.
- **OK**: count = 0.

**DM template:** `[CRIT] Check 12: <N> dispatch_items failed with NULL failure_reason in last hour. STORY-762 guard may be broken.`

**Remediation:** Check dispatch_poller.py error handling path. Verify STORY-762 failure_reason persistence. Query: `SELECT story_id, claimed_by, failed_at FROM dispatch_items WHERE failure_reason IS NULL AND status='failed' ORDER BY failed_at DESC LIMIT 10;`

**Env var:** `FV_CHECK_12_NULL_FAILURE=1`; thresholds `FV_NULL_FAILURE_WARN=1`, `FV_NULL_FAILURE_CRIT=5`

**Status-line format:** `[Check 12 NULL failure_reason] WARN: 3 NULL rows in last hour`

---

### Check 13: Zombie Heartbeat Detection

**Purpose:** Detect agents emitting heartbeats with no phase progression — the blind spot that
allowed STORY-010 to run for 4+ hours without completing during the 2026-04-29/30 incident.
(STORY-763 fixes this inside the agent; this check is the fleet-wide observer for cases the
agent itself missed.)

**Command:**
```bash
# Per agent VM:
journalctl -u dispatch-poller --since '2 hours ago' --no-pager -q 2>/dev/null | grep heartbeat | tail -10
journalctl -u dispatch-poller --since '12 hours ago' --no-pager -q 2>/dev/null | grep phase_end | tail -1
```

**Logic:** If heartbeat lines are present AND most recent phase_end timestamp is > 2× phase_timeout (default 4800s = 80min) ago → zombie.

**Severity:**
- **CRIT**: Any zombie detected. DM Mark with story_id + agent + last phase_end timestamp.
- **OK**: No zombies.

**DM template:** `[CRIT] Check 13: Zombie heartbeat: STORY-XXX@dan — heartbeat active, phase_end Nh ago. SSH in and check journalctl.`

**Remediation:** SSH to agent, `sudo journalctl -u dispatch-poller -f` to observe. If truly stuck: `sudo systemctl restart dispatch-poller` to release the claim.

**Env var:** `FV_CHECK_13_ZOMBIE_HB=1`; threshold `FV_PHASE_TIMEOUT_SEC=2400`

**Status-line format:** `[Check 13 Zombie Heartbeat] CRIT: STORY-010@dan heartbeat active, phase_end 6h ago`

---

### Check 14: No-Seed Dispatch Detection

**Purpose:** Detect active dispatch items where the agent's repo has no seed.md — the blind spot
that caused STORY-780–784 to repeatedly fail at the Phase 8 completion gate during the incident.

**Command:**
```sql
-- SQL: find active dispatch items from last hour
SELECT story_id, repo, claimed_by, status FROM dispatch_items
WHERE status IN ('pending', 'claimed', 'in_progress')
  AND enqueued_at > now() - interval '1 hour';
```
```bash
# For each row, SSH to claiming agent:
ssh -p 443 -o ConnectTimeout=8 azureagent@$AGENT_IP \
  "ls -la ~/dev/hpi-gorillacommerce/$REPO/features/story-$N*/seed.md 2>/dev/null"
```

**Severity:**
- **WARN**: Any active dispatch item missing seed.md. DM Mark with story_id + repo.
- **OK**: All active stories have seed.md.

(First detection = WARN; if the same story appears in multiple consecutive cycles without resolution, manually escalate to CRIT and DM Mark again.)

**DM template:** `[WARN] Check 14: STORY-XXX (advertising-amazon) dispatched without seed.md. Cancel it or run Phase 1 first.`

**Remediation:** Cancel the ghost enqueue: `POST /api/dispatch/cancel/{story_id}`. Then run Phase 1 to create seed.md, re-dispatch.

**Env var:** `FV_CHECK_14_NO_SEED=1`

**Status-line format:** `[Check 14 No-Seed Dispatch] WARN: STORY-784 (advertising-amazon) seed missing`

---

### Check 16: needs_info Pattern Detection

**Purpose:** Detect repeating QUESTION.md content across `needs_info` stories — the blind spot that
allowed the 2026-04-30 incident (11 stories with near-identical questions, system silent for 48 hours)
to go undetected. Content-clustering of needs_info questions is the highest-leverage signal for systemic bugs.

**Command:**
```sql
-- SQL: find needs_info stories from last 24 hours
SELECT story_id, repo, branch, status, needs_info_path, paused_at
FROM dispatch_items
WHERE status = 'needs_info'
  AND paused_at > now() - interval '24 hours';
```
```bash
# For each row, fetch QUESTION.md via gh API:
gh api repos/hpi-gorillacommerce/$REPO/contents/$NEEDS_INFO_PATH?ref=$BRANCH \
  --header "Accept: application/vnd.github.v3.raw"
```

**Algorithm:**
1. Normalize question text (strip markdown headers, dates, story IDs, phase numbers, backtick content).
2. Compute pairwise Jaccard similarity over word bigrams.
3. Scan for anchor phrases (known systemic-bug indicators).
4. Cluster stories: Jaccard ≥ 0.40 OR ≥ 2 shared anchor phrases → same cluster (union-find).
5. Severity by largest cluster size.

**Severity:**
- **CRIT**: Cluster size ≥ 3. DM Mark with cluster size, story IDs, oldest paused_at, 200-char sample.
- **WARN**: Cluster size = 2. Logged only, no DM.
- **OK**: No clusters (all stories have unique questions).

**DM template:** `[CRIT] Check 16 needs_info pattern: cluster of N stories share anchor "phase path does not include"`

**DM suppression:** 6-hour window per cluster signature (sha256 of sorted story IDs + dominant anchor phrase, first 12 hex chars). State in `/home/hermes/state/morris/needs-info-cluster-suppression.json`.

**Anchor phrases** (configurable via `~/.hermes/needs_info_anchors.txt`, one per line):
- `phase path does not include`
- `was dispatched but`
- `blocked on story-`
- `branch_setup_failed`
- `git checkout main failed`

**Remediation:** Investigate the shared content — it points to a systemic bug class. Fix the root cause (e.g., phase router, branch setup) rather than resolving individual stories.

**Env vars:**
- `FV_NEEDS_INFO_THRESHOLD=0.40` (Jaccard similarity threshold)
- `FV_NEEDS_INFO_SUPPRESS_HOURS=6` (DM suppression window)
- `FV_NEEDS_INFO_ANCHORS_FILE=~/.hermes/needs_info_anchors.txt` (custom anchor phrases)

**Status-line format:** `[FLEET-VIGILANCE Check 16] 5 scanned, 1 clusters, 1 CRIT, 0 suppressed`

---

### Checks 9–14: DM Throttling (AC-7)

All 6 checks share a 4-hour DM suppression window to prevent alert fatigue:
- If a check fires CRIT and a DM was sent within the last 4 hours → suppress the DM, still log `dm_suppressed=True`.
- State persisted in `/home/hermes/state/morris/vigilance-dm-suppression.json` (git-untracked).
- When a check returns OK, its suppression state is cleared (next CRIT will DM again).
- Override window: `FV_DM_SUPPRESS_SEC=14400` (seconds; default 4h).

---

### Checks 9–14: Invocation from heartbeat-collector.py

```python
from blind_spot_checks import run_all_checks

agents = [
    {"name": "dan",     "ip": "20.228.224.243", "ssh_port": 443},
    {"name": "derrick", "ip": "20.121.210.186",  "ssh_port": 443},
    {"name": "daisy",   "ip": "20.98.231.234",   "ssh_port": 443},
    {"name": "devon",   "ip": "20.186.26.130",   "ssh_port": 443},
]
blind_results = run_all_checks(agents=agents, fetch_fn=db_fetch)
for r in blind_results:
    print(r["status_line"])
```

**Error isolation (SC-7):** If any individual check raises an exception, `run_all_checks` catches it,
logs `error_unavailable: <ExceptionType>`, and continues to the remaining checks.
The whole vigilance run never aborts due to a single check failure.

---

---

## Check 15: Foundry auth credential expiry (STORY-771)

Run the Foundry auth health check to detect expiring/expired Azure AD SP credentials
before they cause HTTP 500 failures on Morris's Teams bridge.

```bash
python3 /home/hermes/dev/tech-dev-agents/deployment/morris/scripts/check_foundry_auth.py
```

- **OK** (rc=0): credential valid, >7 days until expiry. No action.
- **WARN** (rc=1): credential expires in ≤7 days. DM Mark: "Foundry SP credential expiring — rotate per state/morris/foundry-auth-runbook.md".
- **CRIT** (rc=2): credential expired or missing. DM Mark immediately. Morris Teams bridge is broken until credential is rotated.

If the script itself errors (e.g., Graph API unreachable), treat as **WARN** and note in fleet-health.md.

Runbook: `state/morris/foundry-auth-runbook.md`

---

## Remediation A: stuck claim

A dev agent has `claimed_by = X` on a story but no SDK process + no pause flag for > 60 min. Steps:

1. SSH into agent X, check if their local work queue has a stale entry: `sudo -u hermes python3 -c "import sys; sys.path.insert(0,'/opt/agent'); from work_queue import WorkQueue; print(WorkQueue().list())"`
2. If yes: clear it with `WorkQueue().complete(<story_id>)`. Then the poller will unstick on next tick.
3. Update the central queue: either fail the stale claim (so it can be requeued) or cancel+re-enqueue.
4. Log the story_id in `/home/hermes/state/morris/stuck-claims-log.md` with timestamp + resolution.
5. If the same story gets stuck twice in a row on the same agent, DM Mark with the prompt — the prompt itself might be broken.

## Remediation B: ghost completions detected

**STOP** — something bypassed the STORY-253 guard. Don't auto-remediate; this is a design breach.

1. Query the affected rows: full prompt, claimed_by, completed_at, commit_sha, pr_number.
2. Check if the commits cited actually exist and are the right shape (new work on a story-NNN branch).
3. DM Mark immediately with: (a) the duplicate SHA, (b) the stories involved, (c) your hypothesis on how the guard was bypassed.
4. Do not re-dispatch affected stories until Mark confirms a fix landed.

## Output format

Write to `/home/hermes/state/morris/fleet-health.md`:

```markdown
# Fleet Health — 2026-04-15T21:30:00Z

## Status: OK | WARN | CRIT

## Queue
- pending=3 claimed=2

## Agents
- **dan**: OK — working STORY-304 for 15m, sdk_count=1
- **derrick**: OK — idle, last activity 8m ago

## PRs
- PR #74 (tech-dev-agents) open 4h — waiting review
- PR #12 (knowledgebase) open 8h — review backlog (WARN)

## Incidents this cycle
- (none) | (list)

## Actions taken
- (none) | (bulleted list of what you auto-remediated)
```

Keep the state file to the last 24h of snapshots — older than that, rotate to `fleet-health-archive-YYYY-MM-DD.md`.

## When to DM Mark

**Always DM Mark (Teams, 1:1):**
- Any CRIT.
- Ghost completions of any kind.
- A PR open > 48h.
- An agent's Claude auth expires.

**Never DM Mark (handle silently):**
- Rate-limit pause flags (self-healing).
- Stale local queue entries when the agent is otherwise healthy (auto-fix + log).
- A single WARN.

**Your tone in DMs:** terse, action-oriented. State what you found, what you did about it, what remains for Mark. Mark has been firefighting all day; he wants signal not noise.

## What NOT to do

- **Don't claim dispatch items.** Your dispatch-poller is disabled (2026-04-15 evening). You are a manager, not a coder. If you find yourself wanting to fix code — spec a story and dispatch it to Dan/Derrick.
- **Don't burn Opus tokens on read-only checks.** Use native bash/curl for data collection. Only invoke the SDK when you need to write a doc, respond to Mark, or reason through a non-obvious incident.
- **Don't spam Mark.** Everyone's been burned by noisy monitoring. Silence when healthy, concise when alerting.
- **NEVER commit to git.** No `git add`, no `git commit`, no `git push` from fleet-health checks. Write to `/home/hermes/state/morris/` only. The 30+ noise commits on Dan's branch on 2026-04-18 are the cautionary tale. Fleet health data is ephemeral telemetry, not source code.
- **Don't re-collect data the wrapper already gathered.** When invoked by the cron wrapper, read `$DATA` once and work from it. Don't SSH into agents again or curl the queue API — the wrapper did that already.
- **If nothing changed since last check, do nothing.** Don't write a new snapshot if the state is identical. Only update the state file when a status transitions (OK→WARN, WARN→CRIT, etc.) or an action was taken.

## Invocation

This skill is triggered by:
- The cron `*/30 * * * * /opt/agent/morris-fleet-check.sh` (which in turn invokes this skill)
- Manual request from Mark in Teams: "fleet check" or "how's the fleet"
- After any auto-remediation attempt, so you can verify it worked

## Today's lessons encoded here (2026-04-15 incident digest)

These failure modes happened today — they're now baseline checks:
- **Ghost completions** (Check 2): 14 stories marked completed with 0 commits. Cause: poller's `origin/main..HEAD` returned stale commits from old branches; sent to the API guard which accepted them. Fix: poller now requires pushed remote branch + commits on that branch.
- **Stuck local queue** (Check 3, Remediation A): `clear_stale` method existed in repo source but the deployed copy on Dan/Derrick was stale (from 2026-04-08). Poller reconciler had been silently erroring for a week.
- **Rate-limit ghost completions** (Check 3 paused_until): when Claude Code returned "You've hit your limit", poller churned through claims at 1 ea/min marking complete with stale SHAs. Fixed: detect rate-limit → release → pause until reset.
- **Missing keepalive cron** (Check 3 claude_auth): Morris's deploy skipped `0 */6 * * * claude -p ping`. Auth expired silently within 24h.
- **Graph 502/504 storm** (Check 6): happens when chat membership changes; typically clears in ≤10 min.
- **dispatch-poller on manager** (the skill's own invocation context): disabled 2026-04-15 evening. If you see it active again, disable it immediately and alert Mark.
