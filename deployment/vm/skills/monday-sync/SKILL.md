---
name: monday-sync
description: >
  Sync Monday.com board status with local .project and backlog.md files.
  Pull assigned stories, update task status after work, and check what's
  available. Use when user says "sync monday", "what's on my board",
  "update monday", "what should I work on", or "check backlog".
category: project-management
---

# Monday.com Sync

Bridges Monday.com boards with local SDLC tracking files. Pulls assigned
work, updates status after completion, and helps the agent understand what
work is scoped and ready.

## Prerequisites

- Monday.com MCP server configured in Claude Code
- Project repos cloned locally with `.project` and `backlog.md`

## Important Behavior Rules

1. **Never work on unscoped tasks.** If a story lacks clear requirements, acceptance criteria, or a defined scope, ASK the user before starting.
2. **Only work on assigned stories.** Check Monday.com for stories assigned to Dan. Do not pick up unassigned work without asking.
3. **Stop between stories.** When a story is complete, report to the user and WAIT. Do not auto-start the next story.
4. **Use PM skill first.** Before starting any work, run the pm skill to see what's available and assigned.

---

## Pulling Current Board State

### List assigned stories

Use Claude Code with Monday MCP to query the board:

```
terminal(command="claude -p 'Use the Monday.com MCP tools to list all items assigned to me (Dan / tech-agent-dan) that are in status In Progress or Ready. For each item show: ID, name, status, group (epic), and any due dates. Format as a table.' --max-turns 5 --output-format text 2>&1", workdir="[PROJECT_DIR]", pty=false)
```

### Check what's available to work on

```
terminal(command="claude -p 'Use Monday.com MCP to show all items in Ready status that are not assigned. Group by epic. Show item ID, name, and epic.' --max-turns 5 --output-format text 2>&1", workdir="[PROJECT_DIR]", pty=false)
```

---

## Syncing to Local Files

### Update backlog.md from Monday

After pulling board state, update the local backlog:

```
terminal(command="claude -p 'Read the current Monday.com board state using MCP tools. Then read backlog.md in this repo. Update backlog.md to match Monday: add missing stories, update statuses, remove completed items. Keep the existing backlog.md format.' --max-turns 10 --output-format text 2>&1", workdir="[PROJECT_DIR]", pty=false)
```

### Update .project from current work

After completing a phase or story:

```
terminal(command="claude -p 'Read .project in this repo. Update the current phase status to reflect that [PHASE] is complete. Set the next phase. Also update Monday.com via MCP to move the item to the appropriate status column.' --max-turns 5 --output-format text 2>&1", workdir="[PROJECT_DIR]", pty=false)
```

---

## Updating Monday After Work

When the agent completes a task/phase:

1. Update the Monday.com item status:
```
terminal(command="claude -p 'Use Monday.com MCP to update item [ITEM_ID]: set status to [NEW_STATUS]. Add an update/comment: [SUMMARY_OF_WORK_DONE].' --max-turns 3 --output-format text 2>&1", workdir="[PROJECT_DIR]", pty=false)
```

2. Update local tracking:
```
terminal(command="claude -p 'Update .project and backlog.md to reflect that story [STORY] phase [PHASE] is now complete.' --max-turns 3 --output-format text 2>&1", workdir="[PROJECT_DIR]", pty=false)
```

---

## Work Selection Flow

When the user says "what should I work on" or "what's next":

1. Pull board state from Monday.com
2. Filter for items assigned to Dan in "Ready" or "In Progress" status
3. Present the list to the user with epic context
4. **Wait for the user to choose.** Do not auto-select.
5. Once user picks a story, confirm: "Working on [STORY_NAME] from [EPIC]. Proceeding?"

When the user says "work on [repo/epic/story]":

1. Confirm the specific story: "Found [STORY_NAME] in [EPIC]. Status: [STATUS]. Proceed?"
2. If ambiguous (multiple matches), list options and ask user to pick
3. If the story isn't scoped (no description, no acceptance criteria), say: "This story needs scoping first. Want me to help spec it, or should we pick a different story?"

---

## Periodic Sync (Cron)

If configured as a cron job, run a lightweight sync every morning:

1. Pull board state
2. Update local backlog.md
3. Send summary to user via Teams (uses daily-standup skill)

---

## Error Handling

- Monday MCP not configured: tell user to set it up in Claude Code
- Board not found: ask user for the board ID/name
- Permission denied: check Monday API token
- Story has no description: flag to user, do not start work
