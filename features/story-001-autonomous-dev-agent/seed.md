# Seed: Autonomous Dev Agent (v1)

> Phase 1 — Concept & Seed
> Date: 2026-03-26
> Scope: Epic

---

## Problem Statement

Building software as a solo developer is bottlenecked by execution capacity. Even with AI-assisted coding (Claude Code, Copilot), the developer must be hands-on for every task — reading context, running phases, committing code. There is no way to delegate a complete story to an autonomous agent that follows the established SDLC process, produces deliverables, and opens PRs — while the developer focuses on higher-value work like architecture, prioritization, and review.

## Target User

**Primary:** Solo developer (self) acting as engineering manager of a virtual dev team.

**Interaction model:** The user assigns work via Teams messages or Monday.com task assignments. The agent executes autonomously, checking in at phase gates for approval via Teams. The user reviews PRs and approves phase transitions.

## Vision (End State)

A fleet of autonomous developer agents, each with distinct personalities (system prompts, tool permissions, behavioral rules), working on assigned stories across multiple repos. The agents follow the SDLC at the appropriate depth, produce quality deliverables, and coordinate through Monday.com. This repo (`tech-dev-agents`) is the meta-repo that defines, manages, and iterates on the agent fleet.

**v1 focuses on one agent, proving the model works end-to-end.**

---

## Success Criteria (v1)

When this is done:

1. A bot exists with a Teams presence (name, avatar, email via Entra ID)
2. You can message the bot on Teams with a task (e.g., "Work on STORY-005 in repo X")
3. The bot reads the task context (from Monday.com and/or the message), classifies complexity, and messages you back with a proposed approach (scope classification, which SDLC phases to run)
4. After your approval, the bot executes the agreed phases autonomously in an isolated container environment
5. At phase gates (`gate`/`confirm`), the bot messages you on Teams and waits for approval before continuing
6. The bot produces SDLC deliverables (seed.md, feature-spec.md, code, tests, etc.) in the target repo's `features/` directory
7. The bot updates Monday.com with phase progress and status
8. The bot commits to feature branches and opens PRs — never pushes to main
9. The bot's persona (system prompt, tool permissions, behavioral config) is defined in this meta-repo and loaded at startup

## Acceptance Criteria

- [ ] Bot has Entra ID identity with email and Teams presence
- [ ] Bot responds to Teams messages within 30 seconds (acknowledgment)
- [ ] Bot can be assigned a GitHub repo + story and begins work
- [ ] Bot classifies task complexity and proposes SDLC scope path to user
- [ ] Bot waits for user approval before starting execution
- [ ] Bot executes SDLC phases appropriate to the agreed scope
- [ ] Bot pauses at phase gates and requests approval via Teams
- [ ] Bot produces phase deliverables in the target repo's `features/` directory
- [ ] Bot updates Monday.com task with phase completion comments
- [ ] Bot commits to feature branches (never main) and opens PRs
- [ ] Bot runs in an isolated container environment (not shared with other agents or host)
- [ ] Bot persona is loaded from config in this meta-repo (`tech-dev-agents`)
- [ ] User can grant the bot access to specific GitHub repos (explicit, not blanket)

---

## Scope Classification: Epic

**Why Epic (not Large):**
- Greenfield project with 8 decomposable stories
- 3+ distinct integration points buildable independently (Teams, Monday.com, container infra, Claude Code runner, persona system)
- Clear parallelism: stories 001, 004, 005 have zero dependencies on each other
- Natural E2E gates between infrastructure layer and integration layer
- Acceptance criteria cluster into separable groups (identity, communication, execution, integration)

**Epic phase path:** 1 → decompose → per-story SDLC → [E2E gate] → repeat → Retrospective → Done

---

## Story Decomposition

| Story | Name | Scope | Depends On |
|-------|------|-------|-----------|
| STORY-001 | Container Runtime & Identity | Medium | — |
| STORY-002 | Teams Bot Foundation | Medium | STORY-001 |
| STORY-003 | Claude Code Runner | Medium | STORY-001 |
| STORY-004 | Monday.com Integration | Small | — |
| STORY-005 | Persona System | Medium | — |
| STORY-006 | SDLC Execution Engine | Large | STORY-003, STORY-005 |
| STORY-007 | Approval Flow | Medium | STORY-002, STORY-006 |
| STORY-008 | Git Workflow | Small | STORY-003 |

### Story Descriptions

**STORY-001: Container Runtime & Identity**
As a developer, I want a containerized runtime with its own Entra ID identity so that the agent has an isolated, authenticated environment to operate in.
- Entra ID service account (email, Teams-enabled)
- Container image (Node.js 22 + git + Claude Code CLI)
- Azure provisioning (ACI or App Service container)
- Secrets in Key Vault with managed identity access

**STORY-002: Teams Bot Foundation**
As a developer, I want to message the agent on Teams so that I can assign work and receive updates conversationally.
- Bot Framework app registration + messaging endpoint
- Message handling (receive, parse, route)
- Conversation threading (track multi-turn exchanges per thread)
- Typing indicators and acknowledgment responses

**STORY-003: Claude Code Runner**
As a developer, I want the agent to execute Claude Code headlessly so that it can perform coding tasks autonomously inside its container.
- Headless CLI invocation (`claude -p` with flags)
- Output parsing (JSON result, cost, session ID)
- Session management (resume sessions across turns)
- Tool permission enforcement (read-only vs. write modes)
- Timeout and error handling

**STORY-004: Monday.com Integration**
As a developer, I want the agent to read and update Monday.com tasks so that work tracking stays in sync without manual updates.
- Read task assignments (poll or webhook)
- Update task status and phase progress
- Post phase completion comments
- Parse task descriptions for story context

**STORY-005: Persona System**
As a developer, I want to define agent personas in config files so that I can control how each agent behaves, what tools it can use, and how it communicates.
- Persona config format (markdown or YAML in meta-repo)
- System prompt template with variable injection
- Tool permission definitions per persona
- Persona loading at container startup

**STORY-006: SDLC Execution Engine**
As a developer, I want the agent to follow the SDLC at the appropriate depth so that it produces quality deliverables without over- or under-engineering.
- Task complexity classification (trivial/small/medium/large)
- Phase routing based on scope
- Phase gate detection (gate/confirm/auto)
- Deliverable production (write to `features/` directory)
- Phase state tracking (checkpoint/resume)

**STORY-007: Approval Flow**
As a developer, I want the agent to pause at phase gates and ask me for approval via Teams so that I stay in control of the development process.
- Gate detection triggers Teams notification
- Approval message format (summary + "approve/reject")
- Wait-for-reply mechanism (async, timeout-aware)
- Phase transition on approval, stop on rejection
- Multi-phase conversation threading

**STORY-008: Git Workflow**
As a developer, I want the agent to manage branches and PRs so that its code changes go through proper review before merging.
- Clone granted repo into container workspace
- Create feature branches (naming convention)
- Commit with structured messages
- Open PRs via GitHub CLI
- Never push to main/master

### Dependency Graph

```
        STORY-001 (infra)          STORY-004 (Monday.com)    STORY-005 (personas)
        /          \                      |                        |
  STORY-002    STORY-003              [E2E gate]                   |
  (Teams bot)  (Claude runner)            |                        |
       |         /       \                |                        |
       |   STORY-008     STORY-006 <------+------------------------+
       |   (git)         (SDLC engine)
       |                      |
       +------ STORY-007 -----+
              (approval flow)
                    |
              [E2E gate: full integration]
```

### Delivery Phases

| Phase | Stories | Parallelism |
|-------|---------|-------------|
| P1: Foundation | STORY-001, STORY-004, STORY-005 | All 3 in parallel |
| P1 E2E Gate | Verify infra boots, Monday.com reads/writes, persona loads | — |
| P2: Core Capabilities | STORY-002, STORY-003 | Both in parallel (depend on 001) |
| P2 E2E Gate | Verify Teams messaging + Claude Code execution work independently | — |
| P3: Integration | STORY-006, STORY-008 | Both in parallel (006 depends on 003+005, 008 depends on 003) |
| P3 E2E Gate | Verify SDLC phases execute and git workflow produces PRs | — |
| P4: End-to-End | STORY-007 | Sequential (depends on 002+006) |
| Final E2E Gate | Full flow: assign task on Teams → bot executes → PR opened | — |

### Cross-Cutting Concerns

| Pattern | Applies To | Shared Artifact |
|---------|-----------|-----------------|
| Secrets management (Key Vault) | STORY-001, 002, 003, 004 | Designed in STORY-001, shared access pattern |
| Error handling & retry | STORY-003, 004, 006, 008 | Shared utility designed in STORY-003 |
| Logging & observability | All stories | Shared logging config designed in STORY-001 |
| Teams message formatting | STORY-002, 007 | Shared message templates designed in STORY-002 |

---

## Key Constraints

| Constraint | Detail |
|-----------|--------|
| **Scale** | Single developer, 1 bot for v1, scaling to a small fleet (3-5) |
| **Budget** | Minimize fixed costs; prefer consumption-based (containers > VMs) |
| **Compute** | Claude Code is API-calling CLI — lightweight container (1-2 vCPU, 2-4GB RAM) is sufficient |
| **Security** | Each agent gets its own isolated container; explicit repo access grants; secrets in Key Vault |
| **Identity** | Entra ID service account per agent (email, Teams presence) |
| **Repos** | Bot works on repos you grant access to; this repo is meta-only (defines agents, not worked on by them) |
| **SDLC** | Follows existing `.sdlc/` framework; depth determined per-task by complexity assessment |
| **Approvals** | User approves scope and phase gates via Teams conversation |

---

## Architecture (Conceptual)

```
You (Teams) ──> Bot (Teams Bot Framework)
                    |
                    +-- Reads task from Monday.com
                    +-- Classifies complexity
                    +-- Proposes approach (Teams message)
                    |
                    v (after approval)
              Isolated Container
                    |
                    +-- Claude Code CLI (runs SDLC phases)
                    +-- Git (clone, branch, commit, PR)
                    +-- Target repo (granted access)
                    +-- .sdlc/ framework (from target repo)
                    |
                    v
              Outputs:
                +-- Phase deliverables in features/
                +-- Monday.com status updates
                +-- Teams messages at gates
                +-- Feature branch + PR
```

---

## Out of Scope (v1)

- Multi-bot fleet management UI or dashboard
- Engineering manager orchestrator agent
- Personality iteration system (A/B testing agent prompts)
- Bot-to-bot coordination or shared context
- Running tests that require external services (databases, APIs)
- Custom compute scaling (fixed container size for v1)
- Cost tracking dashboard (manual monitoring for v1)
- Working on this meta-repo autonomously

## Future Context (for Expansion agent)

- Fleet management: provisioning, monitoring, retiring agents
- Personality system: versioned personas, A/B testing, performance metrics per persona
- Orchestrator agent: engineering manager that triages backlog and assigns work to agents
- Cross-agent learning: sharing lessons/patterns between agents
- Self-improvement: agents propose SDLC improvements via retrospectives

---

## Assumptions

1. Claude Code CLI can run in a containerized environment (Node.js + git)
2. Anthropic API is the only external compute dependency (no local GPU/heavy compute)
3. Teams Bot Framework SDK handles message routing and auth
4. Monday.com MCP tools or API are sufficient for reading/updating tasks
5. One Entra ID app registration per agent is feasible within the tenant
6. The `.sdlc/` submodule in target repos provides the agent with phase guidance

---

## Risks

| Risk | Mitigation |
|------|-----------|
| Claude Code CLI may not support all needed operations headlessly | Test early in Phase 2; fall back to Agent SDK if needed |
| Long-running SDLC phases may exceed container/API timeouts | Checkpoint state between phases; resume on restart |
| Teams conversation threading may be complex for multi-phase workflows | Design simple approval UX in Phase 6c |
| Monday.com API rate limits during rapid phase transitions | Batch updates; add retry logic |
| Cost runaway from autonomous agent loops | Max-turn limits, daily budget caps, mandatory human gates |

---

## Real Data Samples

No real data applies — this is a tooling/infrastructure project. The "data" is the SDLC framework itself (`.sdlc/` directory), which is already in the codebase.

---

## Next Phase

Per-story SDLC begins. Each story follows its own scope path:
- **Small stories** (004, 008): 1 → 7 → 8 → Done
- **Medium stories** (001, 002, 003, 005, 007): 1 → 4 → 6 → [6b, 6c, 6d] → 7 → 8 → 8b → 11 → Done
- **Large stories** (006): 1 → 2 → 3 → 4 → 5 → 6 → [6b, 6c, 6d] → 7 → 8 → 8b → 11 → [9, 10] → Done

**Start with P1 (Foundation):** STORY-001, STORY-004, STORY-005 in parallel.
