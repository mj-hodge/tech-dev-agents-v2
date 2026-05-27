---
name: weekly-fleet-review
description: >
  Friday morning comprehensive audit of the entire agent fleet — guard alignment,
  SDLC compliance, model economics, agent self-improvement, tool drift, cross-pollination,
  and knowledge freshness. Produces a report for Mark and identifies action items.
  Replaces the manual firefighting pattern from 2026-04-15/16.
triggers:
  - weekly review
  - fleet review
  - friday review
  - team audit
---

# Weekly Fleet Review — Friday 5 AM ET (10:00 UTC)

You are Morris. This is the most important review you run each week. It covers
everything Mark and Claude spent 8 hours doing manually on 2026-04-15/16.
Your job is to catch drift before it becomes a crisis.

Run ALL checks below using Claude Code SDK. Collect data via SSH/bash in Stage 1,
reason in Stage 2. Write the report to `/home/hermes/state/morris/weekly-fleet-review-YYYY-MM-DD.md`.
DM Mark the executive summary (≤20 lines) in Teams.

---

## Check 1: Guard alignment

Verify the terminal guard on each VM matches the repo source and passes all tests.

```bash
# On each agent VM:
ssh -p 443 azureagent@<IP> "sudo md5sum /opt/agent/terminal_guard.py"
# Compare to repo:
md5sum deployment/vm/terminal_guard.py
```

Then run the test suite locally (or via SDK on a repo checkout):
```bash
cd ~/workspace/tech-dev-agents && python3 -m pytest tests/deployment/test_terminal_guard.py -q
```

**Report:** md5 match per VM (yes/no), test count + pass/fail.
**CRIT** if any VM has a stale guard or tests fail.

Also verify `MORRIS-CRITICAL-FUNCTIONS.md` still matches reality — are there new
functions Morris performs that aren't documented? Are there documented functions
Morris hasn't used in 7 days?

## Check 2: SDLC compliance

Query the dispatch DB for all completed stories this week:
```sql
SELECT story_id, repo, scope, commit_sha IS NOT NULL as has_sha,
       completed_at::date as completed
FROM dispatch_items WHERE status='completed'
  AND completed_at > now() - interval '7 days';
```

For each completed story, check GitHub for SDLC deliverables:
```bash
gh api repos/hpi-gorillacommerce/<repo>/contents/features/story-<NNN>-* \
  --jq '.[].name' 2>/dev/null
```

**Score:** % of stories with seed.md, % with test-design.md, % with code-review.md.
**CRIT** if any story completed without seed.md.
**WARN** if <80% have test-design.md.

Also check: completion guard rejection rate this week (how many 422s on /complete).
High rejection = agents trying to bypass. Zero rejection = guard might not be deployed.

## Check 3: Model economics

```bash
# On each agent VM:
ssh -p 443 azureagent@<IP> "sudo -u hermes hermes insights --days 7"
```

**Report per agent:** total tokens, sessions, avg session duration, model breakdown.

Then query Azure Cost Management for the SaaS/Claude meters:
```bash
az rest --method POST \
  --url "https://management.azure.com/subscriptions/<sub>/providers/Microsoft.CostManagement/query?api-version=2023-11-01" \
  --body '{"type":"ActualCost","timeframe":"Custom","timePeriod":{"from":"<monday>","to":"<friday>"},"dataset":{"granularity":"Daily","aggregation":{"totalCost":{"name":"Cost","function":"Sum"}},"grouping":[{"type":"Dimension","name":"MeterSubcategory"}]}}'
```

**Report:** weekly Foundry cost (Opus vs Sonnet split), trend vs last week.
**CRIT** if Opus spend > $200/week (Morris should be decreasing after SDK-first enforcement).
**WARN** if agents are running Opus on Phase 7/8 (should be Sonnet).

## Check 4: Agent self-improvement

What did each agent add or modify this week in terms of operational infrastructure?

```bash
# Check for new/modified crons, skills, state files
for agent in dan derrick morris; do
  ssh -p 443 azureagent@<IP> "
    echo '--- cron changes ---'
    sudo -u hermes crontab -l
    echo '--- skills modified this week ---'
    sudo find /home/hermes/.hermes/skills -name SKILL.md -mtime -7 -exec basename {} \;
    echo '--- state files modified today ---'
    sudo find /home/hermes/state/$agent -name '*.md' -mtime -1 | wc -l
  "
done
```

**Report:** new crons, new/modified skills, state-file freshness per agent.
**WARN** if any agent hasn't updated state files in 48h (state drift).
**WARN** if Morris's state-sync cron hasn't fired (check /var/log/morris-fleet-check.log timestamp).

## Check 5: Tool drift (stale deploys)

Compare deployed files on each VM against repo source:

```bash
FILES_TO_CHECK=(
  "dispatch_poller.py"
  "terminal_guard.py"
  "work_queue.py"
  "claude_sdk_tool.py"
  "cost_monitor.sh"
  "weekly-patch.sh"
  "morris-fleet-check.sh"
)
for f in "${FILES_TO_CHECK[@]}"; do
  REPO_MD5=$(md5sum <repo-path>/$f 2>/dev/null | cut -d' ' -f1)
  for agent in dan:20.228.224.243 derrick:20.121.210.186 morris:20.246.36.143; do
    VM_MD5=$(ssh ... "sudo md5sum /opt/agent/$f 2>/dev/null | cut -d' ' -f1")
    echo "$agent $f repo=$REPO_MD5 vm=$VM_MD5 match=$([ "$REPO_MD5" = "$VM_MD5" ] && echo yes || echo NO)"
  done
done
```

**CRIT** if any critical file (terminal_guard, dispatch_poller, work_queue) is stale.
Today's incident: work_queue.py was stale for 7+ days on Dan+Derrick, causing the
`clear_stale` AttributeError that left Dan stuck for hours.

## Check 6: Cross-pollination

Improvements discovered on one agent should propagate to all. Check:

- **Crons:** Does Dan have the keepalive cron? Does Derrick? (Morris's missing keepalive
  caused a silent auth expiry on 2026-04-15.)
- **Guard version:** All three VMs should have the same guard (unless manager-specific
  carve-outs are intentional).
- **Skills:** Skills developed for one agent that would benefit others.
- **Config:** platform_toolsets should be identical for Dan+Derrick (dev agents);
  Morris has a restricted set (manager-only).

**Report:** parity matrix — which files/crons/configs differ across agents and whether
the difference is intentional (manager vs dev) or drift.

## Check 7: Knowledge freshness

```bash
cd ~/dev/hpi-gorillacommerce/tech-gc-knowledgebase
git log --since="7 days ago" --oneline | wc -l
# Check which sections haven't been updated in 30+ days
find . -name "*.md" -not -path "./.git/*" -mtime +30 | head -20
```

**Report:** commits this week, sections stale >30d, gaps identified.
**WARN** if zero KB commits this week.
**INFO** list of sections that need refresh (sorted by staleness).

---

## Output format

Write to `/home/hermes/state/morris/weekly-fleet-review-YYYY-MM-DD.md`:

```markdown
# Weekly Fleet Review — YYYY-MM-DD

## Executive Summary
[3-5 sentences: overall fleet health, biggest risk, top action item]

## Scorecard
| Check | Status | Key Finding |
|-------|--------|-------------|
| Guard alignment | OK/WARN/CRIT | ... |
| SDLC compliance | OK/WARN/CRIT | ... |
| Model economics | OK/WARN/CRIT | ... |
| Agent self-improvement | OK/WARN/CRIT | ... |
| Tool drift | OK/WARN/CRIT | ... |
| Cross-pollination | OK/WARN/CRIT | ... |
| Knowledge freshness | OK/WARN/CRIT | ... |

## Details
[Per-check findings with data]

## Action Items
- [ ] Item 1 (owner, priority)
- [ ] Item 2
...

## Metrics vs Last Week
[Trend comparison if prior week's report exists]
```

## DM to Mark

After writing the report, DM Mark in Teams with the executive summary only
(≤20 lines). Link to the full report file path. Only DM on CRIT items or
significant trends — silence on a clean week is fine IF the scorecard is
committed and the report file exists.

## When something goes wrong during this review

If any check fails to execute (SSH timeout, API down, parse error):
- Log the failure in the report under that check
- Mark the check as "SKIP (reason)"
- Don't block the rest of the review — continue with other checks
- If >3 checks SKIP, DM Mark immediately (fleet may be in a bad state)
