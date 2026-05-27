# Business Context — tech-dev-agents

## What This Is

**tech-dev-agents** is the ops console and agent management platform for Gorilla Commerce's virtual AI development team. It defines, deploys, and orchestrates AI coding agents that execute engineering work autonomously — picking up stories from a dispatch queue, running Claude Code sessions on Azure VMs, and reporting progress through Microsoft Teams and Monday.com.

The system ships two interrelated things:
1. **Agent runtime configuration** — persona definitions, SDLC skill sets, and VM bootstrap scripts that turn a bare Azure VM into a fully operational AI engineer.
2. **Ops console** — a FastAPI backend + React dashboard that exposes a dispatch queue, fleet monitor, work history, and cost tracking for all agents.

## Who Uses It

| User | Role |
|------|------|
| **Mark Oreta** | Engineering lead — primary operator. Dispatches stories, reviews agent output, manages the Monday.com board, and approves PRs. |
| **Dan** | AI coding agent (Claude Code on Azure VM). Picks up stories from the dispatch queue, executes SDLC phases, opens PRs. |
| **Derrick** | AI coding agent (Claude Code on Azure VM). Same role as Dan; runs concurrently on separate stories. |
| **Morris** | AI engineering manager agent. Monitors the fleet, writes retrospectives, syncs state files, and handles ops runbook tasks. |

## Business Problems Solved

| Problem | Solution |
|---------|----------|
| Engineering bandwidth is a bottleneck | AI agents run in parallel on Azure VMs, executing full SDLC cycles without human intervention between phases. |
| No visibility into what agents are doing | Ops console dashboard shows live agent status, current story, queue depth, and activity feed. |
| Work falls through the cracks | Dispatch queue with PostgreSQL persistence, lease-based claiming, and a reconciler that detects and re-queues stuck items. |
| Cost of AI API usage is opaque | Agent cost dashboard tracks per-agent, per-story token spend against the Anthropic API in real time. |
| Context hand-off between sessions is lossy | Structured SDLC phase files (`seed.md`, `feature-spec.md`, etc.) in `features/` give agents a written record that survives session boundaries. |
| Agent communications are scattered | Microsoft Teams integration surfaces agent status, approval requests, and completion notices in the engineering channel. |

## Key Dependencies

| Dependency | Purpose |
|------------|---------|
| **Anthropic API (Claude Code)** | Powers all AI coding agent sessions; agents run Claude Code CLI under their `hermes` user on each VM. |
| **Azure VMs (Standard_D2as_v4)** | Agent compute — each agent runs on a dedicated Ubuntu 24.04 VM in `rg-tech-dev-agents-dev`. Sustained AMD CPU (no burstable credits). Fleet flipped from B2ms → D2as_v4 on 2026-04-26 after burstable wedge incident. |
| **Azure Container Apps / ACR** | Hosts the Hermes gateway container and ops console container; images pushed to `gorillaacr.azurecr.io`. |
| **Microsoft Teams / Graph API** | Agent presence management (Busy/Available), approval flows, and status notifications to the engineering team. |
| **GitHub (hpi-gorillacommerce)** | Code hosting, PR workflow, and CI/CD target; agents open PRs and request reviews through `gh` CLI. |
| **Monday.com** | Project management board (`boards/18405631030`); agents comment on tasks at each SDLC phase and update status. |
| **PostgreSQL 16** | Dispatch queue persistence — `dispatch_items` and `agents` tables back the ops console queue with full history. |
| **Grafana Loki** | Centralised log aggregation for agent VM and container logs. |

## Current Maturity

**Production — actively used daily.**

- **v1 (Autonomous Dev Agent epic)** shipped March 2026: container runtime, Teams bot, Claude Code runner, SDLC execution engine, Monday.com integration, git workflow, security hardening (11 stories, all GREEN).
- **v2 (Agent Platform epic)** shipped April 2026: ops console backend, dedicated ops VM, ops dashboard frontend, data pipeline fixes, Entra ID SSO in progress. 430+ tests GREEN across the platform.
- The dispatch queue runs continuously; Dan and Derrick pick up and execute stories without manual SSH intervention.

## Key Services

| Service | Description |
|---------|-------------|
| **Ops Console API** | FastAPI app on port 8005. Exposes `/api/dispatch` (enqueue/claim/cancel), `/api/agents` (fleet registry), `/api/work-history`, and `/api/health`. |
| **Dispatch Poller** | Each agent VM runs a polling loop against `/api/dispatch/next`. Claims a story, sets Teams presence to Busy, executes the Claude Code session, then releases the lease. |
| **Ops Dashboard** | React SPA served alongside the API. Shows queue depth, per-agent utilisation, recent completions, and cost roll-ups. |
| **Presence Manager** | Python script (`scripts/presence_manager.py`) that sets the agent's Microsoft Teams status to Busy/Available and refreshes it every 5 minutes during long sessions. |
| **Hermes Gateway** | Systemd-managed service on each VM running the Hermes agent runtime; accepts incoming webhook calls and relays them to Claude Code. |
| **Teams Bot Adapter** | Azure Bot Framework registration; routes Teams messages to the Hermes gateway and delivers agent replies back to the channel. |
| **CI/CD** | GitHub Actions workflows run the pytest suite on every PR; predeploy gate script (`tests/predeploy/run_all.sh`) runs security and readiness checks before Azure deployment. |
