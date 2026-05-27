---
name: dispatch-queue
description: >
  Manage the work dispatch queue — view pending items, check priority,
  monitor assigned work, and report queue health. Use when "queue status",
  "what's in the queue", "dispatch status", "work queue", or on cron.
category: project-management
agent: morris
---

# Dispatch Queue

Manages the work dispatch queue — monitors pending stories, tracks
assignments, checks for stale items, and reports queue health.

## Prerequisites

- Monday.com MCP configured
- State directory: `/home/hermes/state/morris/`
- `gh` CLI authenticated

---

## Step 1: Pull Queue State

Get current board state from Monday.com:

```
terminal(command="claude-sdk -p 'Use Monday.com MCP to get all items from the dev board. Group by status: Ready, In Progress, Blocked, Done (last 7 days). For each item include: ID, title, assignee, priority, status, last update date. Format as a markdown table.' -w /home/hermes/dev --max-turns 5 --output-format text 2>&1", pty=false)
```

---

## Step 2: Cross-Reference with Git

Check if assigned stories have active branches and recent commits:

```
terminal(command="for dir in /home/hermes/dev/hpi-gorillacommerce/*/; do repo=$(basename $dir); branches=$(git -C $dir branch --list 'story-*' 2>/dev/null); if [ -n \"$branches\" ]; then echo \"=== $repo ===\"; for b in $branches; do last=$(git -C $dir log $b -1 --format='%ar — %s' 2>/dev/null); echo \"  $b: $last\"; done; echo; fi; done", pty=false)
```

---

## Step 3: Detect Issues

**Stale work (no commits in >2 hours during work hours):**
```
terminal(command="for dir in /home/hermes/dev/hpi-gorillacommerce/*/; do repo=$(basename $dir); for branch in $(git -C $dir branch --list 'story-*' 2>/dev/null); do last_epoch=$(git -C $dir log $branch -1 --format='%ct' 2>/dev/null); now_epoch=$(date +%s); diff=$(( (now_epoch - last_epoch) / 3600 )); if [ $diff -gt 2 ]; then echo \"STALE: $repo $branch — last commit ${diff}h ago\"; fi; done; done", pty=false)
```

**Orphaned branches (no matching Monday item):**
Check for story branches that don't map to an active Monday item.

**Queue depth warning:**
If more than 5 items in Ready status → flag capacity concern.

---

## Step 4: Update Active Projects Tracker

Write findings to `/home/hermes/state/morris/active-projects.md`:

```markdown
# Active Projects — Last Updated: [ISO date]

## Dan (Principal Engineer)
- **Current Story:** STORY-XXX — [title]
- **Status:** [phase] — [progress]
- **Branch:** story-xxx/slug in [repo]
- **Last Commit:** [hash] [message] — [time ago]
- **Blockers:** [details or "None"]

## Queue Summary
| Priority | Story | Scope | Assigned | Status | Age |
|----------|-------|-------|----------|--------|-----|
| P1 | STORY-XXX | Medium | Dan | In Progress | 2d |
| P2 | STORY-YYY | Small | Unassigned | Ready | 1d |

## Capacity
- Active agents: [N]
- In-progress stories: [N]
- Ready stories: [N]
- Estimated throughput: [N stories/week]
```

---

## Step 5: Report

### Routine check (cron):
Only message Mark if issues found:
> Queue alert: [N] stories stale, [N] blocked, queue depth [N]. Details in active-projects.md.

### On-demand check:
Post full queue status to requester:
```
**Dispatch Queue — [date]**

**In Progress:** [N]
- STORY-XXX (Dan) — Phase 8, last commit 30m ago
- ...

**Ready:** [N]
- STORY-YYY (unassigned) — Small scope
- ...

**Blocked:** [N]
- STORY-ZZZ — [reason]

**Queue Health:** [HEALTHY / STALE / OVERLOADED]
```

---

## Cron Schedule

Run every hour during work hours:
```
hermes cron add "morris-dispatch-queue" "0 8-18 * * 1-5" "Run dispatch-queue skill — check queue health and update trackers"
```

---

## Error Handling

- Monday.com unreachable: fall back to git branch analysis only
- No active stories: report "Queue empty — awaiting new work from Mark"
- Multiple agents on same story: flag as conflict, alert Mark immediately
