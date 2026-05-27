---
name: daily-standup
description: >
  Morning standup summary - what was done yesterday, what's blocked, what's
  next from Monday.com. Can run on a cron schedule or on demand. Use when
  user says "standup", "morning report", "daily update", "what happened",
  or "status report".
category: project-management
---

# Daily Standup

Generates a morning standup report by checking git activity, Monday.com
board state, and any blockers or incidents.

## Prerequisites

- Git repos cloned in /home/hermes/dev/
- Monday.com MCP configured (optional but recommended)
- Loki/Grafana access for incident detection (optional)

---

## Report Generation

### Step 1: Yesterday's Work

Check git activity across all repos in the last 24 hours:

```
terminal(command="for dir in /home/hermes/dev/*/*; do if [ -d \"$dir/.git\" ]; then echo \"=== $(basename $(dirname $dir))/$(basename $dir) ===\"; git -C $dir log --oneline --since='24 hours ago' --author='dan\\|hermes\\|Dan' 2>/dev/null | head -10; echo; fi; done", pty=false)
```

### Step 2: Current Board State

Pull from Monday.com:

```
terminal(command="claude -p 'Use Monday.com MCP to get:
1. Items I completed in the last 24 hours
2. Items currently In Progress (assigned to Dan)
3. Items in Ready status (assigned to Dan)
4. Any items marked as Blocked
Format each section as a short bullet list.' --max-turns 5 --output-format text 2>&1", workdir="/home/hermes/dev", pty=false)
```

### Step 3: Blocker Detection

Check for issues:

```
terminal(command="claude -p 'Check for blockers:
1. Any failing CI/CD pipelines in repos I work on (use gh CLI)
2. Any open PRs waiting on review for more than 24 hours
3. Any Monday items marked as blocked
Report blockers only if found.' --max-turns 5 --output-format text 2>&1", workdir="/home/hermes/dev", pty=false)
```

### Step 4: Error Check (Optional)

If Loki is configured, check for recent production errors:

```
terminal(command="curl -sG 'https://grafana.gorillacommerce.ai/loki/api/v1/query_range' --data-urlencode 'query={agent=\"dan\"} |~ \"(?i)error|exception\"' --data-urlencode 'start=$(date -d '24 hours ago' +%s)000000000' --data-urlencode 'limit=10' 2>/dev/null | python3 -c 'import sys,json; d=json.load(sys.stdin); results=d.get(\"data\",{}).get(\"result\",[]); print(f\"{sum(len(r.get(\"values\",[]))for r in results)} errors in last 24h\") if results else print(\"No errors\")' 2>/dev/null || echo 'Loki not reachable'", pty=false)
```

---

## Report Format

Send to the user in Teams:

```
**Daily Standup - Dan**
**Date:** [today's date]

**Yesterday:**
- [repo] Completed: [commit summaries]
- [repo] In progress: [what was worked on]

**Today:**
- [story/task from Monday] - [brief description]
- [story/task] - [brief description]

**Blockers:**
- [blocker description] (or "None")

**Parallel opportunities:**
- [stories that can run simultaneously]

**Health:**
- Errors (24h): [count]
- Open PRs: [count]
```

---

## Parallel Work Analysis

Always include at the end:

```
terminal(command="claude -p 'Look at the Ready stories on Monday.com. Which ones can be safely worked on in parallel using worktree isolation? Consider: do they touch the same files? Same services? Same database tables? List safe parallel sets.' --max-turns 5 --output-format text 2>&1", workdir="/home/hermes/dev", pty=false)
```

---

## Cron Schedule

To run automatically every morning at 8am:

```
hermes cron add "daily-standup" "0 8 * * 1-5" "Run the daily-standup skill and send results to Teams home channel"
```

---

## On-Demand

User can trigger anytime by saying "standup" or "daily report".
The agent should run the full report and send it.

---

## Behavior Rules

1. Keep the report concise - no fluff
2. Highlight blockers prominently
3. Always include parallel work opportunities
4. If Monday.com is unreachable, still report git activity
5. **Never auto-start work from the standup.** Report only. Wait for user direction.
