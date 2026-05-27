# Seed: Orchestrator Agent — Engineering Manager for the Fleet

**Story:** STORY-044
**Date:** 2026-04-14
**Scope:** Large
**Phase Path:** 1 → 2 → 3 → 4 → 5 → 6 → [6b, 6c, 6d] → 7 → 8 → 8b → 11 → [9, 10] → Done

---

## Problem Statement

Mark spends hours daily on operational overhead that should be automated:
- Reviewing PRs across 8+ repos, checking quality, merging
- Re-dispatching failed/incomplete stories with better prompts
- Monitoring agent status (are they stuck? idle? burning tokens with no output?)
- Switching between Claude Code, Teams, GitHub, Monday.com, and Grafana to coordinate work
- Manually clearing stale work queues, recovering from auth failures, restarting services
- Deciding what to dispatch next based on project priorities

Mark wants to interact primarily via **Teams** — send a message like "review the knowledgebase PRs and merge what's good" or "what's the fleet status?" and get it done without opening Claude Code.

## Desired Outcome

A new agent ("Morris" — the Engineering Manager) that Mark interacts with via Teams. Morris:

1. **Reviews PRs** — reads diffs, checks for quality issues, runs sub-agent code review, merges or requests changes
2. **Manages the dispatch queue** — prioritizes stories, re-dispatches failures, ensures agents have work
3. **Monitors fleet health** — detects stuck agents, stale queues, auth failures, auto-heals when possible
4. **Reports status** — daily standups, weekly summaries, project health across all repos
5. **Coordinates with Mark** — asks for decisions only when needed, otherwise acts autonomously within guardrails

## Users

- **Mark** — interacts via Teams. Sends high-level directives ("review and merge the knowledgebase PRs", "what's blocked?", "dispatch the advertising epic to Dan"). Receives summaries, decisions-needed, and completion reports.
- **Dan/Derrick** — receive work from the dispatch queue (unchanged). Don't interact with Morris directly.
- **Morris (the orchestrator)** — receives Mark's directives via Teams, uses sub-agents and the dispatch queue API to execute.

## Architecture

### Morris — The Lead Agent (VM: vm-morris-agent-dev)

A hermes-gateway instance like Dan/Derrick, but with a different SOUL:
- **Role:** Engineering Manager / Tech Lead
- **Primary interface:** Teams chat with Mark
- **Does NOT code** — never runs Claude Code SDK for implementation
- **Orchestrates** — uses the dispatch queue API, GitHub API, Monday.com API, and Loki to manage the fleet

### Sub-Agent Capabilities (run as Claude Code SDK sessions)

Morris dispatches sub-agents for specialized work:

| Sub-Agent | Purpose | Trigger |
|-----------|---------|---------|
| **Code Reviewer** | Read PR diffs, check quality, identify issues, approve or request changes | Mark says "review PRs" or auto on PR creation |
| **Security Reviewer** | Run /phase-6b style security audit on PRs | Auto on Medium+ PRs, or Mark requests |
| **Ops Reviewer** | Check deployment readiness, health checks, monitoring | Auto on infra PRs |
| **Queue Manager** | Monitor fleet status, detect stuck agents, re-dispatch failures, prioritize backlog | Continuous (every 5 min) |
| **Merge Manager** | Merge approved PRs, resolve conflicts, update tracking | After code review passes |
| **Status Reporter** | Generate daily standup, weekly summary, project health | Scheduled (daily 9am EST) or on-demand |

### How Mark Interacts (Teams Examples)

```
Mark: "Review the knowledgebase PRs and merge what's good"
Morris: "Reviewing 10 PRs on tech-gc-knowledgebase..."
Morris: "8/10 approved and merged. 2 need attention:
       - PR #5 has a broken link in the markdown
       - PR #10 is missing the systems-overview rollup
       Re-dispatching both with fixes. Want me to merge when they come back?"
Mark: "Yes"

Mark: "What's the fleet doing?"
Morris: "Dan: STORY-096 (advertising-amazon) — active 12min, $1.20
       Derrick: STORY-229 (advertising-amazon) — active 3min, $0.40
       Queue: 8 pending, 2 claimed
       No blockers. 3 PRs awaiting review.
       Shall I review them?"

Mark: "Dispatch the remaining advertising epic stories"
Morris: "I see STORY-207, 208, 209, 224-227 are pending from EPIC-003.
       Dependencies: 208 needs 207 first, 209 needs 208.
       Dispatching 207+224 now (parallel, no deps).
       Will chain 208→209 after 207 completes.
       225-227 are independent — dispatching to Derrick."

Mark: "Run a security review on the last 5 advertising-amazon PRs"
Morris: "Running security sub-agent on PRs #33-#37..."
Morris: "Results:
       - #33: PASS (no secrets, no SQL injection)
       - #34: WARNING — hardcoded API URL, should use env var
       - #35-#37: PASS
       Filed STORY-230 for the #34 fix. Dispatch it?"
```

### Queue Manager (Continuous Background)

Runs every 5 minutes automatically:
1. Check fleet status (SSH live check + Loki)
2. Detect stuck agents (SDK running >30min with no log output) → auto-kill + re-enqueue
3. Detect stale work queues → auto-clear
4. Check for completed stories without PRs → auto-retry
5. Check for failed SDK sessions → analyze error, re-dispatch with fix
6. Report anomalies to Mark via Teams only if action needed

### Maintenance Schedule (Daily/Weekly)

Morris manages recurring VM maintenance:
- **Daily 5am UTC:** Update Claude Code on all VMs (`npm install -g @anthropic-ai/claude-code@latest`)
- **Daily 5:15am UTC:** Update Codex CLI if installed (`npm install -g @openai/codex@latest`)
- **Daily 6am UTC:** Pull latest `.sdlc` framework (already has cron — Morris verifies it ran)
- **Weekly Sunday 4am UTC:** `apt-get update && apt-get upgrade -y` on all VMs (security patches)
- **After each update:** Verify services still running (hermes-gateway, dispatch-poller, promtail)
- **Report:** Include in daily standup — "Updated Claude Code to vX.Y.Z on all VMs" or "Update failed on Derrick — investigating"

### Reliability Monitor (Continuous Background)

Runs every 10 minutes automatically:
1. **Cron health** — verify all expected crons are firing on all VMs (dispatch-poller, sdlc-pull, cost_monitor, hermes-log-sync, token keepalive). Query Loki for last execution time of each. Alert if any cron missed 2+ cycles.
2. **Service health** — check systemd services on all VMs (hermes-gateway, dispatch-poller, promtail, nginx). Alert if any are in failed/restarting state.
3. **Auth health** — verify Claude OAuth tokens are valid on all VMs (claude auth status --json). Alert if any show expired or about to expire (<2h remaining).
4. **Critical errors** — query Loki for ERROR/DENIED/FATAL/Blocked patterns in the last 10 min. Deduplicate and escalate new errors to Mark.
5. **Cost anomalies** — if any agent's daily spend exceeds $50, or total fleet exceeds $100, alert Mark.
6. **Disk/memory** — check VM disk usage and memory. Alert if >85% disk or OOM kills in dmesg.
7. **GitHub rate limits** — check remaining API quota. Alert if <500 remaining.
8. **Escalation rules:**
   - INFO: log to Loki only (cron fired, service healthy)
   - WARNING: log + include in daily standup (cron missed once, token expiring soon)
   - CRITICAL: log + **immediate Teams message to Mark** (service down, auth expired, agent stuck >1h, critical error pattern)

### Merge Manager

When Mark says "merge" or when code review passes:
1. Check PR is mergeable (no conflicts)
2. If conflicts: attempt rebase, or report to Mark
3. Squash merge
4. Update Monday.com status
5. Update local backlog.md
6. Pull latest on agent VMs so next stories have fresh code
7. Report: "Merged PR #X (STORY-XXX). Updated Monday."

### Status Reporter

Daily at 9am EST (auto, configurable):
- Stories completed yesterday (with cost)
- Stories in progress (agent, duration, estimated completion)
- Queue depth and priority
- Blocked items
- PRs awaiting review
- Total daily/weekly spend
- Fleet health score

## Deployment

### New VM: vm-morris-agent-dev
- Azure VM (B2ms like Dan/Derrick)
- hermes-gateway with Morris SOUL
- Teams via M365 CLI (same as Dan/Derrick — no separate app registration needed)
- Teams identity: tech-agent-morris@gorillacommerce.co
- Claude Code installed (for sub-agent dispatch)
- GitHub access (shared service account)
- Access to dispatch queue API
- Access to all project repos (read-only for review, push for tracking files)

### Morris's SOUL.md (Key Differences from Dan/Derrick)

- **Never codes directly** — all implementation delegated to Dan/Derrick via dispatch queue
- **Reviews and merges PRs** — uses gh CLI and sub-agents
- **Monitors fleet** — continuous background loop
- **Talks to Mark** — sends structured updates, asks targeted questions
- **Autonomous within guardrails** — can merge Small PRs that pass review, escalates Medium+ to Mark
- **Cost-conscious** — tracks fleet spend, alerts if daily spend exceeds threshold

### Guardrails (What Morris Can Do Autonomously)

| Action | Autonomous? | Needs Mark? |
|--------|------------|-------------|
| Merge Small PR that passes code review | Yes | No |
| Merge Medium+ PR | No | Yes — asks first |
| Re-dispatch failed story | Yes | No (logs it) |
| Kill stuck agent session | Yes | No (logs it) |
| Clear stale work queue | Yes | No (logs it) |
| Dispatch new story from backlog | No | Yes — proposes, waits |
| Change story priority | No | Yes |
| Create new story/spec | No | Yes |
| Deploy to production | No | Always Mark |

## Success Criteria

| ID | Criterion | Verification |
|----|-----------|-------------|
| AC-1 | Morris responds to Teams messages within 30s | Functional test |
| AC-2 | "Review PRs" triggers sub-agent code review on specified repo | End-to-end test |
| AC-3 | "Merge" merges approved PRs and updates Monday/backlog | End-to-end test |
| AC-4 | Fleet health check runs every 5 min, detects stuck agents | Loki logs show [MONITOR] lines |
| AC-5 | Stuck agent auto-healed (work queue cleared, story re-enqueued) | Integration test |
| AC-6 | Daily standup sent to Mark at 9am EST | Cron + Teams message test |
| AC-7 | "What's the fleet doing?" returns accurate real-time status | Functional test |
| AC-8 | Failed dispatch auto-retried with improved prompt | Queue + Loki test |
| AC-9 | Morris never runs implementation code directly | SOUL enforcement + audit |
| AC-10 | Medium+ PR merge requires Mark's explicit approval | Guardrail test |

## Dependencies

- Dispatch queue API (STORY-026/027) — complete
- Fleet status detection (fleet_status.py) — complete
- Post-completion validation (just deployed) — complete
- Agent VM provisioning (Mark does this — Azure, DNS, Teams app reg)
- Shared GitHub service account (STORY-043)

## Risks

| Risk | Mitigation |
|------|-----------|
| Morris becomes a bottleneck (all work flows through one agent) | Morris delegates, doesn't execute. Dan/Derrick continue to pick from queue independently. |
| Morris burns expensive Opus tokens on routine monitoring | SOUL enforces Sonnet for monitoring, Opus only for complex decisions |
| Morris merges a bad PR | Guardrails: only auto-merge Small scope. Medium+ needs Mark. Code review sub-agent runs first. |
| Teams message latency | Same architecture as Dan/Derrick — webhook-based, sub-second delivery |
| Morris and Dan/Derrick compete for queue items | Morris never claims from the queue. It only enqueues and monitors. |

## Open Questions

1. **VM provisioning** — Mark needs to create the VM, Teams app, DNS. Same process as Dan/Derrick setup.
2. **Claude Code license** — Does the Gorilla Commerce team plan include a 3rd agent seat?
3. **Sub-agent model** — Should sub-agents run on Morris's VM or on Dan/Derrick's VMs?
4. **Scope of autonomy** — Start conservative (ask Mark for everything) and relax guardrails over time?
5. **Name** — "Morris" is a placeholder. What should the orchestrator be called?
