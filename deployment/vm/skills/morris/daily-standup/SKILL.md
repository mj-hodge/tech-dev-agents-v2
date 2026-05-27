---
name: daily-standup
description: >
  Morning standup — summarize yesterday's work, open PRs, fleet health,
  blockers, and today's plan. Posts to Teams. Use when "standup",
  "morning report", "daily update", or on 8 AM weekday cron.
category: project-management
agent: morris
---

# Daily Standup (Morris)

Generates a comprehensive morning standup report from git activity,
Monday.com state, PR status, fleet health, and persistent project trackers.

## Prerequisites

- Git repos cloned in /home/hermes/dev/hpi-gorillacommerce/
- Monday.com MCP configured
- State directory: `/home/hermes/state/morris/`
- `gh` CLI authenticated

---

## Step 1: Restore Context

Read persistent state files first:

```
terminal(command="cat /home/hermes/state/morris/active-projects.md 2>/dev/null || echo '# Active Projects — No data yet'", pty=false)
```

```
terminal(command="cat /home/hermes/state/morris/pr-tracker.md 2>/dev/null || echo '# PR Tracker — No data yet'", pty=false)
```

---

## Step 2: Yesterday's Git Activity

Check commits across all repos in the last 24 hours:

```
terminal(command="for dir in /home/hermes/dev/hpi-gorillacommerce/*/; do if [ -d \"$dir/.git\" ]; then repo=$(basename $dir); commits=$(git -C $dir log --oneline --since='24 hours ago' 2>/dev/null); if [ -n \"$commits\" ]; then echo \"=== $repo ===\"; echo \"$commits\" | head -15; echo; fi; fi; done", pty=false)
```

---

## Step 3: Open PRs

```
terminal(command="for repo in /home/hermes/dev/hpi-gorillacommerce/*/; do repo_name=$(basename $repo); prs=$(gh pr list --repo hpi-gorillacommerce/$repo_name --state open --json number,title,createdAt,reviewDecision,statusCheckRollup --jq '.[] | \"  #\\(.number) \\(.title) | Review: \\(.reviewDecision // \"pending\") | CI: \\(.statusCheckRollup // \"unknown\")\"' 2>/dev/null); if [ -n \"$prs\" ]; then echo \"=== $repo_name ===\"; echo \"$prs\"; echo; fi; done", pty=false)
```

---

## Step 4: Monday.com Board State

```
terminal(command="claude-sdk -p 'Use Monday.com MCP to get:
1. Items completed in the last 24 hours
2. Items currently In Progress
3. Items in Ready status
4. Items marked as Blocked
Format as bullet lists. Be brief.' -w /home/hermes/dev --max-turns 3 --output-format text 2>&1", pty=false)
```

---

## Step 5: Fleet Health (Quick Check)

```
terminal(command="echo '=== Services ===' && for svc in hermes-agent hermes-teams-bot hermes-dispatch-poller; do status=$(systemctl is-active $svc 2>/dev/null || echo 'not found'); echo \"  $svc: $status\"; done && echo && echo '=== Resources ===' && df -h / --output=pcent | tail -1 | xargs -I{} echo \"  Disk: {}\" && free -h | awk '/Mem:/{printf \"  Memory: %s/%s (%.0f%%)\\n\", $3, $2, $3/$2*100}'", pty=false)
```

---

## Step 6: Blocker Detection

```
terminal(command="echo '=== Failing CI ===' && for repo in /home/hermes/dev/hpi-gorillacommerce/*/; do repo_name=$(basename $repo); failed=$(gh run list --repo hpi-gorillacommerce/$repo_name --limit 3 --json conclusion,name --jq '.[] | select(.conclusion == \"failure\") | .name' 2>/dev/null); if [ -n \"$failed\" ]; then echo \"  $repo_name: $failed\"; fi; done && echo && echo '=== Stale PRs (>24h) ===' && for repo in /home/hermes/dev/hpi-gorillacommerce/*/; do repo_name=$(basename $repo); gh pr list --repo hpi-gorillacommerce/$repo_name --state open --json number,title,createdAt --jq '.[] | select((.createdAt | fromdateiso8601) < (now - 86400)) | \"  #\\(.number) \\(.title)\"' 2>/dev/null; done", pty=false)
```

---

## Step 7: Update Trackers

Update `/home/hermes/state/morris/active-projects.md` with current state gathered above.

---

## Step 8: Post Standup to Teams

Send to the team channel:

```
**Daily Standup — Morris (Engineering Manager)**
**Date:** [today's date]

**Yesterday:**
- [repo] [commit summaries — grouped by story]
- PRs merged: [list]

**Agent Status:**
- Dan: [current story] — [phase/status]
- [other agents if applicable]

**Open PRs:**
- PR #[N] [repo]: [title] — [review status, age]

**Today's Plan:**
- [next items from Monday board]
- [PRs to review/merge]

**Blockers:**
- [blocker details] (or "None")

**Fleet Health:** [HEALTHY / DEGRADED]
- [any warnings]
```

Also send a copy to Mark's 1:1 chat.

---

## Cron Schedule

Run every weekday morning at 8:00 AM:
```
hermes cron add "morris-standup" "0 8 * * 1-5" "Run daily-standup skill for Morris — post morning report to Teams"
```

---

## Behavior Rules

1. Keep the report concise — no fluff
2. Highlight blockers prominently
3. Always include fleet health
4. If Monday.com is unreachable, still report git + PR activity
5. **Never auto-start work from the standup.** Report only.
6. Always update persistent trackers after generating the report
