---
name: claude-code-sdlc
description: >
  Autonomous SDLC loop using Claude Code CLI. Delegates coding tasks to the
  `claude` CLI, monitors execution, handles token exhaustion retries, and
  sends progress updates during long sessions. Use when user asks to
  "run claude code on this", "use claude to implement", "have claude fix",
  "start an sdlc loop", "autonomous coding session", or "delegate to claude".
category: software-development
---

# Claude Code SDLC Runner

Delegates coding work to the `claude` CLI and autonomously iterates through
plan, implement, test, fix, commit cycles. Handles token exhaustion
with automatic retries and sends progress updates during long-running sessions.

**CRITICAL: Visibility.** The user MUST be able to see what's happening at all times.
Every Claude Code invocation MUST log output to a file AND you MUST send
periodic Teams status updates. Silent execution is not acceptable.

## Prerequisites

- `claude` CLI must be installed and authenticated (`which claude` must succeed)
- Project must be a git repo (Claude Code requires this)
- Terminal toolset must be enabled

## Quick Check (run before starting)

```
terminal(command="which claude && claude --version && claude auth status 2>&1 | head -3", pty=false)
```

If `claude` is not found: `npm install -g @anthropic-ai/claude-code`
If auth fails: user must run `claude login` interactively (requires browser).

---

## Logging Setup (MANDATORY - do this FIRST)

Before ANY Claude Code invocation, set up the output log:

```
terminal(command="mkdir -p /tmp/claude-sdlc-logs && export CLAUDE_LOG=/tmp/claude-sdlc-logs/session-$(date +%Y%m%d-%H%M%S).log && echo \"Logging to: $CLAUDE_LOG\"", pty=false)
```

ALL `claude -p` commands MUST pipe through `tee` to this log:

```
claude -p '...' --max-turns 30 --output-format text 2>&1 | tee -a "$CLAUDE_LOG"
```

This ensures output is captured for Grafana/Loki monitoring.

---

## Progress Updates (MANDATORY)

Progress is tracked via log files shipped to Grafana/Loki and through Dan's active
supervision of Claude Code sessions. The user monitors via Grafana.

Only send Teams messages for story completion, key decisions, blockers, and epic milestones
(see Visibility section below).

### How to run Claude Code:

Interactive mode with PTY (Dan supervises):
```
terminal(command="claude 'Implement [TASK]'", workdir="[PROJECT_DIR]", pty=true, background=true)
```

Monitor and respond:
```
process(action="poll", pid=[PID])
process(action="submit", pid=[PID], input="y\n")
```

The claude wrapper automatically logs all output to `/tmp/claude-sdlc-logs/` for Grafana.

---

## CRITICAL: How to delegate to Claude Code

**NEVER give Claude Code an entire epic or multiple stories in one prompt.**
Claude Code is a tool — YOU (Hermes/Dan) are the agent making decisions.

The correct pattern:
1. YOU read the backlog and decide what stories to work on
2. YOU analyze parallelism
3. YOU launch Claude Code once PER STORY, PER PHASE
4. YOU review each result before moving on
5. YOU handle errors, retries, and escalation

Claude Code gets narrow, specific tasks:
- GOOD: "Implement the DataSource model with these 4 columns in app/models/sources.py"
- GOOD: "Write tests for the connector registry endpoint"
- GOOD: "Run pytest and fix any failures"
- BAD: "Finish Epic 1"
- BAD: "Complete all stories and commit"

---

## Phase 0: Epic Planning and Parallel Analysis

When given an epic, break it down YOURSELF before touching Claude Code.

1. Read the backlog and .project:

```
terminal(command="cat backlog.md && cat .project", workdir="[PROJECT_DIR]", pty=false)
```

2. Identify stories in the epic and their dependencies. Decide which can run in parallel.
   Consider: do they touch the same files, services, database tables, API endpoints?

3. Group into batches:
   - Batch 1 (parallel): independent stories
   - Batch 2 (after batch 1): stories that depend on batch 1

4. For each story in a batch, create a worktree:

```
terminal(command="git worktree add .worktrees/STORY-XXX -b story-xxx/main", workdir="[PROJECT_DIR]", pty=false)
```

5. Run each story through the phases below — one Claude Code call per phase.

6. After all stories in a batch complete:
   - Merge each branch to main
   - Run tests to verify no conflicts
   - Move to next batch

**Worktree isolation is MANDATORY for parallel work.** Each story gets its own worktree.

---

## Phase 1: Planning (per story)

1. **Tell the user** what you're about to do. Be specific.
2. `cd` into the project directory (or worktree for parallel work).
3. Create a git checkpoint branch before any changes:

```
terminal(command="git checkout -b hermes/sdlc-$(date +%Y%m%d-%H%M%S) 2>/dev/null || true", pty=false)
```

4. Run Claude Code in print mode for planning:

```
terminal(command="claude -p 'Read the codebase and the CLAUDE.md if it exists. Create a detailed implementation plan for: [TASK]. Output ONLY the plan as a numbered list. Do not implement yet.' --max-turns 3 --output-format text 2>&1 | tee -a /tmp/claude-sdlc-logs/current.log", workdir="[PROJECT_DIR]", pty=false)
```

5. Read the output and **send the plan to the user in Teams**.
6. Wait for user approval before proceeding. If the user says proceed, go to Phase 2.

---

## Phase 2: Implementation

Run Claude Code **interactively** with `pty=true`. Claude Code will ask for permission
before writing files, running commands, etc. YOU (Dan/Hermes) review each request and
approve or deny using `process(action="submit")`.

### Starting an interactive Claude Code session:

```
terminal(command="claude 'Implement STORY-XXX: [specific task description]'", workdir="[WORKTREE_DIR]", pty=true, background=true)
```

Note: NO `-p` flag. This is interactive mode.

### Monitoring and responding to Claude Code:

Poll for output regularly:
```
process(action="poll", pid=[PID])
```

When Claude Code asks for permission (shows a tool call and waits for approval):
- Read what it wants to do
- If safe and correct: `process(action="submit", pid=[PID], input="y\n")`
- If wrong or dangerous: `process(action="submit", pid=[PID], input="n\n")`
- To provide guidance: `process(action="submit", pid=[PID], input="no, instead do X\n")`

### Your role as the supervising agent:

- **Review every tool call** Claude Code wants to make
- **Approve** file writes, edits, and commands that align with the story
- **Deny** anything that looks wrong, out of scope, or dangerous
- **Redirect** if Claude Code is going down the wrong path
- **Escalate to the user** (via Teams) only if you hit a decision you can't make

### When to escalate to the user:

- Architectural decisions that affect other stories/epics
- Destructive operations (dropping tables, force push, deleting files)
- Anything that contradicts the spec or backlog
- If you're unsure after 2 failed attempts

### Committing:

After Claude Code finishes a logical unit:
```
terminal(command="git diff --stat", workdir="[WORKTREE_DIR]", pty=false)
```

Review the diff. If it looks good:
```
terminal(command="git add -A && git commit -m 'feat(STORY-XXX): [description]'", workdir="[WORKTREE_DIR]", pty=false)
```

### Monitoring loop (REQUIRED)

While Claude Code runs in background, you MUST poll and report:

1. Wait 2 minutes
2. Check progress:
```
terminal(command="tail -15 /tmp/claude-sdlc-logs/current.log 2>/dev/null", pty=false)
terminal(command="git -C [PROJECT_DIR] log --oneline -3 2>/dev/null && git -C [PROJECT_DIR] diff --stat 2>/dev/null", pty=false)
```
3. **Send the user a Teams update** summarizing what you see
4. Check if process is still running:
```
process(action="poll", pid=[PID])
```
5. If still running, go back to step 1
6. If finished, go to Phase 3

### Completion detection

When `process(action="poll")` shows the process has exited:
- Exit code 0: go to Phase 3 (Verification)
- Exit code non-zero: check output for token exhaustion. If found, go to Token Exhaustion Handling. Otherwise go to Fix Loop.

---

## Phase 3: Verification

**Tell the user**: "Claude Code finished. Verifying results..."

1. Check what changed:
```
terminal(command="git log --oneline -5 && echo '---' && git diff --stat HEAD~1 2>/dev/null || git diff --stat", workdir="[PROJECT_DIR]", pty=false)
```

2. Run the test suite:
```
terminal(command="if [ -f package.json ]; then npm test; elif [ -f pyproject.toml ] || [ -f setup.py ]; then python -m pytest -v; elif [ -f go.mod ]; then go test ./...; elif [ -f Cargo.toml ]; then cargo test; elif [ -f Makefile ] && grep -q 'test:' Makefile; then make test; else echo 'NO_TEST_RUNNER_FOUND'; fi", workdir="[PROJECT_DIR]", pty=false)
```

3. **Send results to the user in Teams**:
   - Files changed, lines added/removed
   - Test results (pass/fail counts)
   - Summary of what was implemented

4. If tests pass: go to Phase 4
5. If tests fail: go to Fix Loop
6. If NO_TEST_RUNNER_FOUND: ask user to review manually, then go to Phase 4

---

## Fix Loop

**Tell the user**: "Tests failing. Running fix attempt [N]/3..."

1. Capture the test failure output (last 50 lines).
2. Run Claude Code again:

```
terminal(command="claude -p 'The following tests are failing after your changes:\n\n[FAILURE_OUTPUT_LAST_50_LINES]\n\nFix the failing tests. Do not break passing tests. Run tests again to verify. Commit when fixed.' --max-turns 20 --output-format text 2>&1 | tee -a /tmp/claude-sdlc-logs/current.log", workdir="[PROJECT_DIR]", pty=false, background=true)
```

3. Monitor with the same polling/update pattern as Phase 2.
4. Repeat up to **3 fix attempts**. After 3 failures, **tell the user**:
   - "Fix loop exhausted after 3 attempts. Remaining failures: [OUTPUT]"

---

## Token Exhaustion Handling

### Detection

Token exhaustion when process exits non-zero AND output contains:
"token limit", "context window", "rate limit", "429", "exceeded",
"too many tokens", "Request too large", "max_tokens"

### Retry procedure

1. **Tell the user**: "Claude Code hit a token/rate limit. Waiting [N]s before retry [N]/5..."
2. Wait: 1st=30s, 2nd=60s, 3rd=120s
3. Check progress so far:

```
terminal(command="git log --oneline -5 && echo '---' && git diff --stat", workdir="[PROJECT_DIR]", pty=false)
```

4. Resume:

```
terminal(command="claude -p 'Continue where you left off. Check git log and git status. The original task was: [TASK]. Complete remaining work, run tests, and commit.' --max-turns 30 --output-format text 2>&1 | tee -a /tmp/claude-sdlc-logs/current.log", workdir="[PROJECT_DIR]", pty=false, background=true)
```

5. Maximum **5 retries**. After that, report and stop.
6. After 2 token-limit retries, suggest splitting the task.

---

## Phase 4: Wrap-up

**Send the user a final summary in Teams**:

```
STORY [NAME] Complete

Time: [duration]
Claude Code sessions: [count] (retries: [count])
Files changed: [git diff --stat]
Commits: [git log --oneline since start]
Tests: [pass/fail counts]

Next: [what's next from the backlog, or ask user]
```

If working through an epic with batches:
- Review the diff and test results
- Merge the completed story's worktree branch
- When all stories in the batch are done, run tests on main
- Move to the next batch
- Only stop and report to user when the entire epic is complete or a blocker is hit

If working on a single story:
- Ask the user: "Push to remote?" / "Open a PR?" / "Move to next story?"

Save a memory of what was accomplished.

---

## SDLC Framework Integration

When working in a project with `.sdlc/` or `CLAUDE.md`:

- Read `.project` and `backlog.md` for current phase context
- Phase-aware prompts for Claude Code:
  - Phase 7: "Write tests based on test-design.md. Achieve RED state."
  - Phase 8: "Implement until all tests pass. Follow implementation plan."
  - Phase 8b: "Review implementation against spec. Write code-review.md."
  - Phase 9: "Polish code. Handle edge cases. Improve test coverage."
- Update `.project` and `backlog.md` after completion
- If Monday MCP is configured, update task status

---

## Session State Tracking

Maintain in working memory:
- `task`: original request
- `project_dir`: working directory
- `phase`: current (plan/implement/verify/fix/retry/done)
- `fix_attempt`: 0-3
- `token_retry`: 0-5
- `start_time`: session start
- `log_file`: path to current log

If context compressed, recover from git log + git status + log file tail.

---

## Error Recovery

- `claude` not found: tell user to install
- Git dirty: offer to stash or commit first
- Claude login expired: tell user to run `claude login`
- No tests: skip test phase, ask user to review
- Network error: wait 30s, retry once

---

## Visibility

### Grafana/Loki (primary monitoring — ALL output goes here)

All `claude -p` output MUST pipe through `tee` to `/tmp/claude-sdlc-logs/`. This is how
the user monitors progress. Promtail ships these logs to Grafana automatically.

During execution, also periodically (every 2-3 min) write a status line to the log:

```
terminal(command="echo \"[STATUS $(date +%H:%M)] Story: [NAME] | Phase: [PHASE] | Files changed: $(git diff --stat --shortstat 2>/dev/null) | Commits: $(git log --oneline -1 2>/dev/null)\" >> /tmp/claude-sdlc-logs/current.log", pty=false)
```

### Teams (only important events — do NOT spam)

Only send Teams messages for:

1. **Story completed**: "STORY-XXX complete. [1-line summary]."
2. **Key decision**: Architectural choices or deviations from plan: "Decision: [what] because [why]."
3. **Blocker/error that needs user input**: "Blocked: [issue]. Need your input."
4. **Epic complete**: "Epic [N] done. [summary]."

Do NOT send routine progress updates to Teams. The user monitors progress via Grafana.
