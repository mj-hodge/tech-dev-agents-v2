---
name: repo-onboard
description: >
  Onboard to a new repository - clone it, read the codebase, generate
  CLAUDE.md, set up SDLC scaffolding, and configure worktree isolation.
  Use when user says "onboard to [repo]", "set up [repo]", "clone and
  configure [repo]", or "add [repo] to my workspace".
category: software-development
---

# Repository Onboarding

Clones a repo, analyzes the codebase, generates CLAUDE.md and SDLC config,
and enables worktree isolation for parallel work.

## Prerequisites

- `gh` CLI authenticated (GITHUB_TOKEN set)
- `claude` CLI installed and authenticated
- SDLC framework at ~/.sdlc

---

## Step 1: Clone the Repository

```
terminal(command="gh repo clone [ORG/REPO] /home/hermes/dev/[ORG]/[REPO]", pty=false)
```

If the repo already exists, pull latest:
```
terminal(command="cd /home/hermes/dev/[ORG]/[REPO] && git pull", pty=false)
```

---

## Step 2: Analyze the Codebase

```
terminal(command="claude -p 'Read this entire codebase. Identify:
1. Language(s) and framework(s)
2. Project structure and architecture
3. Build system and test runner
4. Key configuration files
5. External dependencies and services
6. CI/CD setup
7. Existing documentation
Output a structured summary.' --max-turns 5 --output-format text 2>&1", workdir="/home/hermes/dev/[ORG]/[REPO]", pty=false)
```

---

## Step 3: Generate CLAUDE.md

If no CLAUDE.md exists:

```
terminal(command="claude -p 'Based on your analysis of this codebase, generate a CLAUDE.md file following this structure:

# CLAUDE.md

## Project Overview
[1-2 sentences]

## Tech Stack
[languages, frameworks, key deps]

## Build & Test
[how to build, test, lint]

## Architecture
[key directories and their purpose]

## Conventions
[coding style, naming, patterns used]

## Key Files
[important files to read first]

Write the file to CLAUDE.md in the project root.' --max-turns 5 --output-format text 2>&1", workdir="/home/hermes/dev/[ORG]/[REPO]", pty=false)
```

---

## Step 4: Set Up SDLC Scaffolding

Link the SDLC framework and initialize project tracking:

```
terminal(command="if [ ! -d .sdlc ]; then ln -s /opt/sdlc-framework .sdlc; fi && ls .sdlc/", workdir="/home/hermes/dev/[ORG]/[REPO]", pty=false)
```

Initialize .project if it doesn't exist:
```
terminal(command="claude -p 'Check if .project exists. If not, create it with the standard SDLC format: project name, current phase (none), status, and an empty backlog reference. Also create backlog.md if missing.' --max-turns 3 --output-format text 2>&1", workdir="/home/hermes/dev/[ORG]/[REPO]", pty=false)
```

Link skills into Claude Code:
```
terminal(command="mkdir -p /home/hermes/.claude/skills && cp -r /opt/sdlc-framework/skills/* /home/hermes/.claude/skills/ 2>/dev/null || true", pty=false)
```

---

## Step 5: Enable Worktree Isolation and Multi-Worker

**Critical: always enable these for parallel work safety.**

Check and set in the project's Claude Code config:
```
terminal(command="claude -p 'Check the project settings. Ensure worktree mode and multi-worker are enabled. If running config commands, use: claude config set worktreeMode true and claude config set multiWorker true.' --max-turns 3 --output-format text 2>&1", workdir="/home/hermes/dev/[ORG]/[REPO]", pty=false)
```

Verify config.yaml has multi_worker: true:
```
terminal(command="grep -r 'multi_worker\|worktree' .project config.yaml CLAUDE.md 2>/dev/null || echo 'Not found - needs configuration'", workdir="/home/hermes/dev/[ORG]/[REPO]", pty=false)
```

---

## Step 6: Report to User

Send a summary:
- Repo name and path
- Language/framework detected
- CLAUDE.md generated (yes/no)
- SDLC scaffolding set up (yes/no)
- Worktree isolation enabled
- Test runner detected
- Any issues or missing config

Ask: "Ready to work on this repo. What epic or story should I start with?"

---

## Parallel Work Check

After onboarding, always run:
```
terminal(command="claude -p 'Use the PM skill. Check Monday.com for this project. What stories are in Ready status? Which can be worked on in parallel without risk? Present as a table.' --max-turns 5 --output-format text 2>&1", workdir="/home/hermes/dev/[ORG]/[REPO]", pty=false)
```

Report parallelizable stories to the user. **Do not auto-start any work.**

---

## Error Handling

- Repo not found: check org/repo name, ask user
- Permission denied: check GITHUB_TOKEN has repo access
- Private repo: ensure token has `repo` scope
- No test runner: note in report, suggest adding one
