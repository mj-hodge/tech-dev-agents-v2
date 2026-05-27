# Retrospective: Agent Fleet Operations — 2026-04-02 to 2026-04-06

## Context
| Field | Value |
|-------|-------|
| Project | tech-dev-agents (Agent Fleet Operations) |
| Period | 2026-04-02 to 2026-04-06 (5 days) |
| Scope | Operational improvements, not a formal epic |
| Workers | 2 agents (Dan, Derrick) + 1 human (Mark) + 1 orchestrator (Claude Code Opus) |
| Key Events | Cost optimization, SOUL.md evolution (7 iterations), MCP tooling, group chat, dispatch/fleet/whats-next skills, token exhaustion protocol, handback workflow, STORY-018/019 dispatched |

## Metrics

| Metric | Value |
|--------|-------|
| Commits (Apr 2-6) | 17 |
| SOUL.md iterations | 7 (cost discipline → delegation → token exhaustion → story assignment → dispatch handling → handback → background processes) |
| Skills created (Mark's CLI) | 3 (`/dispatch`, `/fleet`, `/whats-next`) |
| Skills created (agent Hermes) | 2 (`claude-code`, `handback`) |
| Config changes deployed | 6 (model→Sonnet, delegation→Haiku, max_turns, compression, group chat, background notifications) |
| Agent incidents observed | 4 (native coding, story switching, SDK auth expired, background process polling) |
| All incidents resolved same-day | Yes |
| Infrastructure provisioned | 1 VM (vm-ops-console-dev, eastus2, B1s) |
| DNS records created | 1 (tech-dev-agents.gorillacommerce.ai) |
| Grafana access grants | 6 team members added |
| Stories dispatched via new tools | 4 (STORY-018, 019, 087, 088, 089, 090) |

---

## What Went Well

### 1. Cost optimization delivered immediately — Opus→Sonnet saved ~15x on orchestration
Switched Hermes main loop from `claude-opus-4-6` to `claude-sonnet-4-6` with delegation to `claude-haiku-4-5` for subtasks. Dan's session costs dropped from $1.75/session to $0.50/session. Compression summaries also moved to Sonnet. **Evidence:** Dan's Loki logs showed 6 turns/$0.12 vs previous 50 turns/$1.76 for similar tasks.

### 2. SOUL.md as a living document — 7 iterations in 5 days
Each agent behavior issue was traced to a SOUL.md gap, fixed, and deployed within the same conversation. The iterative approach (observe → diagnose → fix → deploy → verify) worked far better than trying to spec everything upfront. **Evidence:** Story switching caught and fixed with story assignment rules; native coding caught and fixed with cost discipline section; token exhaustion caught and fixed with cron retry protocol.

### 3. Dispatch pipeline went from copy-paste to one command
`/dispatch` skill builds structured prompts from seeds, presents for review, sends via Graph API in HTML format. Eliminated the manual Teams copy-paste workflow. **Evidence:** STORY-087, 088, 089, 090 all dispatched via skill.

### 4. Loki-based monitoring works without MCP
`/fleet` skill queries Loki directly for agent status — no dependency on the ops console API. This proved more reliable than the MCP `list_agents` tool (which had connectivity issues). **Evidence:** Used throughout the week to diagnose Dan's native coding, Derrick's SDK auth, and token exhaustion.

### 5. Token exhaustion protocol worked on first test
Derrick hit rate limits, stopped immediately, messaged Mark, set a cron to retry in 1 hour — exactly as designed in the SOUL.md. **Evidence:** Loki logs showed "Rate limit hit — resets at 10pm UTC. I'll schedule a cron."

### 6. Handback workflow creates accountability
Agents now use `skill_view("handback")` to create structured `## Mark TODO` checkboxes in PRs and seeds. Mark's `/whats-next` skill scans for these. Two-sided workflow: agents create, Mark discovers. **Evidence:** Dan's PR for STORY-082 included structured admin TODO items.

---

## What Went Wrong

### 1. Dan auto-switched stories (STORY-082 → STORY-037) without authorization
Dan was on STORY-082 when Mark asked him to "spec a ticket for admin tools." Dan interpreted this as a full story switch rather than a side task. Wasted time and violated the single-story rule. **Root cause:** SOUL.md lacked explicit rules for side tasks vs story switches. **Fixed:** Added "Story Assignment" section with "quick tasks return to your assigned story" rule.

### 2. Dan used native Hermes tools instead of Claude Code SDK
Logs showed Dan reading files with `cat`, `grep`, `sed` through Hermes terminal — 10:1 Hermes-to-SDK ratio when it should be ~1:1. He was "thinking" in Sonnet instead of dispatching to Claude Code. **Root cause:** SOUL.md said to delegate but didn't explicitly say "don't read code yourself." **Fixed:** Added delegation strategy section, cost discipline targets ($0.50 orchestration, $2.00 SDK).

### 3. Terminal guard had `import sys` bug — went undetected for days
The terminal guard crashed on every command with `name 'sys' is not defined` but was failing open (commands still executed). Deployed to both VMs but not verified. **Root cause:** Missing `import sys` in terminal_guard.py. The guard also wasn't being reloaded by Hermes after file update (Python module caching). **Fixed:** Added import, but Hermes caching means restarts are required for guard changes.

### 4. Hermes config pushed to VMs but not always picked up
Multiple config/SOUL.md pushes required Hermes restarts to take effect. Not all pushes included restarts, leading to stale behavior. **Root cause:** Hermes loads SOUL.md at session start (not per-message) and config at gateway start (not hot-reload). **Fixed:** Standardized push + restart pattern, but this is fragile.

### 5. MCP send_message never worked reliably through the ops console
The TeamsClient on the ops console VM couldn't resolve chat IDs due to httpx URL-encoding `$expand` as `%24expand`. Multiple fix attempts failed. Fell back to sending via Graph API directly from Mark's machine. **Root cause:** httpx encodes `$` in query params, Graph API requires literal `$`. **Status:** Workaround deployed (direct Graph API from local machine), server-side fix still pending.

### 6. Derrick's SDK auth expired repeatedly
OAuth tokens expire every ~60 minutes. Derrick hit expired tokens 3 times in one day. Each time required Mark to SSH in and re-auth. **Root cause:** No automatic token refresh mechanism for claude.ai OAuth on agent VMs. The `graph-token.sh` handles Graph API refresh, but Claude Code OAuth is different. **Status:** Manual SSH + `claude auth login` is the current process.

### 7. Agent registry format mismatch between code and deployment
The ops console agent_service.py expected `[{name, host, port}]` but the deployment registry used `{agents: [{name, ip, email}]}`. Caused crashes on deploy. **Root cause:** Two different teams (Mark vs Dan) created the registry in different formats, no schema validation. **Fixed:** Manually aligned formats, but no schema enforcement exists.

### 8. Duplicate terminal timeout in hermes-config.yaml
Config currently has `timeout: 600` AND `timeout: 300` under the terminal section. YAML uses last-wins, so 300 is active — defeating the intended 600s increase. **Status:** Bug, needs fix.

---

## Recurring Patterns

| Pattern | Occurrences | Root Phase | Fix |
|---------|-------------|------------|-----|
| SOUL.md gap → agent misbehavior → same-day fix | 7 | N/A (operational) | SOUL.md iteration is the process; needs pre-deployment validation |
| Hermes doesn't hot-reload config | 4 pushes | N/A (Hermes limitation) | Always restart after config push |
| SDK auth expires without auto-refresh | 3 times | N/A (claude.ai limitation) | Need auth refresh cron or longer-lived tokens |
| Agent reads code natively instead of using SDK | 2 agents | SOUL.md | Cost discipline + delegation rules fixed it |
| Registry/API format mismatches on deploy | 2 stories | Phase 6 (design) | Need schema validation for agent-registry.json |

---

## Phase-by-Phase Analysis

### SOUL.md / Agent Configuration (new category — not in standard SDLC)
**Finding:** Agent behavior is configured through SOUL.md, hermes-config.yaml, and Hermes skills. This is a new operational surface that the SDLC framework doesn't cover. Changes to agent behavior should follow a lightweight process: observe → diagnose → write test (what behavior do we expect?) → fix SOUL.md → deploy → verify from Loki.
- **Proposed change:** Add an "Agent Configuration" phase or checklist to the SDLC framework for projects that manage autonomous agents.

### Phase 1 (Seed)
**Finding:** The `/dispatch` skill is effectively a Phase 1 bypass — it builds structured prompts from seeds and sends them to agents without going through the full seed ceremony. This is appropriate for Small scope stories dispatched to agents who have the SDLC framework built in. But for Medium+ stories, the agent needs to run the full SDLC.
- **Proposed change:** `/dispatch` should explicitly state the phase to start from based on what's already done (seed exists? design exists?).

### Phase 6 (Design)
**Finding:** Agent registry schema mismatch between ops_console code and deployment files caused deploy failures. No schema definition existed.
- **Proposed change:** For projects with multi-component deployments, Phase 6 should include interface schema definitions for shared config files (registry, .env templates).

### Phase 8 (Implementation)
**Finding:** Agents using `claude` CLI directly instead of `claude_sdk_tool.py` means cost tracking (`[DONE] cost=$X`) is lost. The SDK wrapper logs costs to Grafana; the bare CLI doesn't.
- **Proposed change:** Add to Phase 8 agent: "All Claude Code invocations MUST go through the SDK wrapper to ensure cost tracking and approval logging."

### Phase 11 (Pre-Deploy Gate)
**Finding:** The pre-deploy gate doesn't verify agent configuration (SOUL.md, hermes-config.yaml, skills). STORY-018 deployed an ops console VM but agent config was pushed ad-hoc.
- **Proposed change:** For agent infrastructure stories, pre-deploy gate should include: SOUL.md content review, config.yaml validation, skill verification, terminal guard test.

---

## Operational Findings (New — Not in Standard SDLC)

### O-001: Agent SOUL.md Needs Version Control + Validation
**Evidence:** 7 iterations of SOUL.md in 5 days, with behavior regressions when sections conflicted or were too vague.
**Action:** Add a SOUL.md linting/validation step — at minimum, check for required sections (Cost Discipline, Story Assignment, SDK Mandate, Token Exhaustion, Handback).

### O-002: Config Push Needs Atomic Deploy + Restart
**Evidence:** 4 pushes without restarts led to stale behavior. Guard fix didn't take until restart.
**Action:** The `agent-push.sh` script should ALWAYS restart after config changes. Add a `--no-restart` flag for explicit override, but default to restart.

### O-003: Claude Code OAuth Needs Auto-Refresh
**Evidence:** 3 manual SSH sessions to re-auth Derrick in one day.
**Action:** Create a cron job on each agent VM that checks `claude auth status` hourly and alerts Mark if token is expired. Long-term: investigate if refresh tokens can be used programmatically.

### O-004: `/dispatch` Should Support Cross-Project Dispatch
**Evidence:** Mark dispatched STORY-087 (advertising-amazon) from the tech-dev-agents repo. The dispatch skill worked but seed resolution assumed the current repo.
**Action:** `/dispatch` should accept a `--repo` flag or detect the repo from the story ID.

### O-005: Graph API Token Refresh Should Be Automated
**Evidence:** Token expired 3+ times during the week. Each time required running `graph-token.sh` manually.
**Action:** Create a cron on Mark's machine (or a shared service) that refreshes the Graph token before it expires. The refresh token lasts ~90 days.

### O-006: Hermes `require_mention` Missing for Teams
**Evidence:** When added to group chats, agents respond to ALL messages, not just @mentions. No Teams-specific `require_mention` config exists in Hermes.
**Action:** File an issue or create a Hermes skill that filters group chat messages — only respond when the bot's name appears in the message text.

### O-007: Duplicate Terminal Timeout in Config
**Evidence:** `hermes-config.yaml` has `timeout: 600` and `timeout: 300` — YAML last-wins means 300 is active.
**Action:** Remove the duplicate. Fix immediately.

---

## Status Tracker

| ID | Finding | Category | Severity | Action | Target | Status |
|----|---------|----------|----------|--------|--------|--------|
| F-001 | Agent SOUL.md needs required section validation | Operations | High | Add linting for required sections | deployment/vm/ (project) | PENDING |
| F-002 | Config push must default to restart | Operations | High | Update agent-push.sh to restart by default | deployment/vm/agent-push.sh | PENDING |
| F-003 | Claude Code OAuth auto-refresh needed | Operations | High | Hourly auth check cron on agent VMs | deployment/vm/ (project) | PENDING |
| F-004 | `/dispatch` needs cross-project support | Skill | Medium | Add --repo flag to dispatch skill | skills/dispatch/SKILL.md | PENDING |
| F-005 | Graph token refresh automation | Operations | Medium | Cron on Mark's machine or shared service | tools/agent-ops-mcp/ | PENDING |
| F-006 | Group chat @mention filtering for Teams | Hermes | Medium | Hermes skill to filter non-@mention messages | deployment/vm/skills/ | PENDING |
| F-007 | Duplicate terminal timeout in config | Bug | High | Remove duplicate `timeout: 300` line | deployment/vm/hermes-config.yaml | IMPLEMENTED (ede1448) |
| F-008 | Agent registry schema validation | Phase 6 | Medium | JSON schema for agent-registry.json | agents/phase-6-design.md | IMPLEMENTED (6c519d9) |
| F-009 | SDK wrapper enforcement for cost tracking | Phase 8 | Medium | Mandate SDK wrapper in Phase 8 agent | agents/phase-8-implementation.md | IMPLEMENTED (6c519d9) |
| F-010 | Agent config verification in pre-deploy | Phase 11 | Medium | Add agent config checks to pre-deploy gate | agents/phase-11-predeploy.md | IMPLEMENTED (6c519d9) |
| F-011 | SOUL.md iteration process is working well | Operations | Low | No change — document as validated pattern | — | VALIDATED |
| F-012 | `/fleet` via Loki is more reliable than MCP | Operations | Low | No change — keep Loki as primary, MCP as secondary | — | VALIDATED |
| F-013 | Token exhaustion protocol works correctly | Operations | Low | No change — Derrick followed it perfectly first try | — | VALIDATED |
| F-014 | Handback skill creates two-sided accountability | Operations | Low | No change — validated workflow | — | VALIDATED |
