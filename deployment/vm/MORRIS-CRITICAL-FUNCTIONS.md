# Morris — Critical Functions & Operating Model

Morris is a **manager agent** — he does not write code. Everything below describes what Morris does, how he does it, and what the terminal guard + toolset config must allow.

---

## Operating principle: SDK-first, native-minimal

Morris has two tool paths:
1. **Claude Code SDK** (via `python3 /opt/agent/claude_sdk_tool.py -p "..." -w <repo>` or `claude -p "..."`) — for ALL substantive work. SDK has its own Read/Write/Grep/Bash/WebSearch/MCP. Runs on the flat-rate Claude Code seat.
2. **Native Hermes tools** (terminal, memory, skills, cron, clarify, session_search, todo) — for orchestration ONLY. Terminal is gated by `terminal_guard.py`. Every native terminal call burns Opus tokens via Azure Foundry.

**Rule:** if something CAN be done via SDK, it MUST be done via SDK. Native terminal is a dispatch mechanism, not a work tool.

---

## Critical Functions

### 1. Fleet monitoring (every 15 min via cron)

**What:** Check dispatch queue, per-agent SDK process counts, rate-limit pause flags, Graph errors, disk/mem, stale claims, ghost completions, failed stories.

**How:** `/opt/agent/morris-fleet-check.sh` runs as cron. Stage 1 (bash) collects data via SSH probes to Dan/Derrick + ops-console API query. Stage 2 (SDK) reasons on the collected JSON, updates `/home/hermes/state/morris/fleet-health.md`, DMs Mark on CRIT.

**Guard requirements:**
- `bash /opt/agent/morris-fleet-check.sh` — allowed (ops-script carve-out)
- `ssh -p 443 azureagent@<ip> '<any remote command>'` — allowed (SSH carve-out)
- `cat /home/hermes/state/morris/fleet-health.md` — allowed (state-file carve-out)
- `tail /var/log/morris-fleet-check.log` — allowed (state-file carve-out)
- `curl -s -H "X-API-Key: ..."` — **NOT allowed natively** (use wrapper script Stage 1)

### 2. PR review + SDLC compliance enforcement

**What:** Review every open PR across 5 repos. Check for required SDLC deliverables per scope. Comment on non-compliant PRs. Approve Small PRs directly; flag Medium+ for Mark.

**How:** Use Claude Code SDK (`claude -p "review PR #N in repo X"`) which has its own Bash (gh pr view, gh pr review), Read (code diffs), and Write (comments).

**Guard requirements:**
- `gh pr list --repo <owner/repo> --jq '.[] | .title'` — allowed (gh allowlist + quote-aware splitter)
- `gh pr view <N> --repo <owner/repo>` — allowed
- `cd /home/hermes/dev/<repo> && claude -p "review PR #N"` — allowed (claude carve-out)

### 3. PR merge

**What:** Merge approved PRs. Small PRs: auto-merge after review. Medium+: wait for Mark's explicit approval.

**How:** SDK invocation (`claude -p "merge PR #N"`) which runs `gh pr merge`.

**Guard requirements:**
- `gh pr merge <N> --squash --repo <owner/repo>` — allowed (gh allowlist)

### 4. Dispatch queue management

**What:** Enqueue new stories, monitor queue state, requeue failed stories, cancel obsolete items.

**How:** The fleet-check wrapper queries the queue API in Stage 1 bash. Morris dispatches new stories via SDK which calls the API through its own Bash tool.

**Guard requirements:**
- `cat /tmp/fleet-data.*.json` — allowed (state-file carve-out for /tmp/)
- Queue API calls via SDK (not native terminal)

### 5. State file maintenance

**What:** Keep 6 persistent state files up to date across sessions:
- `active-projects.md` — agent stories, status, blockers
- `pr-tracker.md` — open PRs, review status, age
- `fleet-status.md` / `fleet-health.md` — agent health snapshots
- `decisions-log.md` — key decisions with rationale
- `objectives.md` — team KPIs

**How:** SDK writes (`claude -p "update active-projects.md with ..."`) or fleet-check cron (writes fleet-health.md directly via SDK Stage 2).

**Guard requirements:**
- `cat /home/hermes/state/morris/*.md` — allowed (state-file carve-out)
- `head -N /home/hermes/state/morris/*.md` — allowed
- Write operations → SDK only (no native write_file tool)

### 6. Knowledgebase review + updates

**What:** Review and update `tech-gc-knowledgebase` repo. Ensure project docs, wiki sections, infrastructure knowledge are current. Cross-reference with what agents discover during their work.

**How:** SDK invocation from the knowledgebase repo (`claude -p "review and update knowledgebase" -w ~/dev/hpi-gorillacommerce/tech-gc-knowledgebase`).

**Guard requirements:**
- `cd ~/dev/hpi-gorillacommerce/tech-gc-knowledgebase && claude -p "..."` — allowed (claude carve-out)
- `git -C ~/dev/hpi-gorillacommerce/tech-gc-knowledgebase pull` — allowed (git allowlist)

### 7. Daily standup (8 AM ET, cron)

**What:** Summarize yesterday's work, open PRs, fleet health, blockers. Post to Teams DM with Mark.

**How:** Cron invokes SDK which reads git logs, PR lists, state files, then composes the standup message.

**Guard requirements:**
- `hermes cron` commands — allowed
- SDK invocation — allowed

### 8. Cost/economics monitoring

**What:** Track Azure Foundry spend (SaaS meter), verify agents use Sonnet for implementation (not Opus), flag 15x overruns.

**How:** `hermes insights --days 1` on each agent VM (via SSH), cross-reference with model policy in CLAUDE.md.

**Guard requirements:**
- `ssh -p 443 azureagent@<ip> "sudo -u hermes hermes insights --days 1"` — allowed (SSH carve-out)

### 9. Spec writing (seeds)

**What:** Write seed.md files for stories Morris identifies or Mark requests. Classify scope. Set phase path.

**How:** SDK invocation (`claude -p "write seed for STORY-NNN"`) which uses Write tool to create `features/story-NNN-*/seed.md`.

**Guard requirements:**
- SDK only (no native write)

### 10. Agent coaching + SDLC enforcement

**What:** When agents skip SDLC phases, comment on their PRs, request missing deliverables, refuse to approve until compliant. Monitor model usage — flag Opus on Sonnet-default phases.

**How:** PR review via SDK (function 2 above) + fleet-vigilance Check 4b.

---

### 11. Context continuity — state sync + conversation synthesis (FOUNDATIONAL)

**This is the function that enables all others.** Without it, every session starts cold and Morris loses track of ongoing work, Mark's preferences, and fleet state.

**What:** Maintain 6+ persistent state files that act as institutional memory across session boundaries and context compactions. Two automated cycles + one manual discipline:

**A. 15-minute State Sync** (cron: `*/15 * * * *`, hermes job `Morris State Sync`):
- Reads the most recent Teams DM session file from `/home/hermes/.hermes/sessions/`
- Extracts Mark's messages from the last 60 minutes
- Diffs against current state files (current-focus.md, active-projects.md, pr-tracker.md, decisions-log.md, objectives.md, fleet-status.md)
- Updates ONLY what's new — minimal writes, under 2 min runtime
- If nothing new: outputs `[SILENT]` and exits
- This is the PRIMARY mechanism for persistence — not agent discipline (which has failed repeatedly)

**B. Daily Conversation Synthesis** (cron: `0 5 * * *`, hermes job `Morris Daily Conversation Synthesis`):
- Deep pass on ALL Teams messages from the last 24 hours
- Reconciles every state file — adds new items, removes stale info, corrects bad assumptions
- Special attention to: Mark's corrections (HIGH PRIORITY to persist), repeated instructions, direction changes
- "Ruthless about removing stale info — old state is worse than no state"
- Must capture: decisions (→ decisions-log.md with date + rationale), new priorities, research direction changes

**C. Interrupt Resilience** (skill: `interrupt-resilience`, invoked manually):
- Write-ahead pattern: update `current-focus.md` BEFORE every action (what you're about to do, what's paused, what's queued)
- Guards against context loss when Mark sends rapid-fire messages that interrupt mid-tool-call
- Format: Active Right Now / Paused-Interrupted / Queued Next / Key Context

**D. Nightly Git Push** (cron: `0 4 * * *`):
- Commits and pushes all state files + knowledgebase changes
- Ensures nothing is lost if the VM crashes overnight

**State files Morris maintains:**
| File | Purpose | Update frequency |
|---|---|---|
| `current-focus.md` | What Morris is doing RIGHT NOW + paused + queued | Every action (manual) + 15min (cron) |
| `active-projects.md` | Agent stories, status, blockers, PRs | 15min + daily synthesis |
| `pr-tracker.md` | Open PRs, review status, age | 15min + PR review cycles |
| `fleet-status.md` / `fleet-health.md` | Agent health snapshots | 15min (fleet-check cron) |
| `decisions-log.md` | Key decisions with date + rationale | Daily synthesis |
| `objectives.md` | Team KPIs, current priorities | Daily synthesis |
| `ops-runbook.md` | Failure modes, fix procedures | After incidents |
| `sdlc-audit-*.md` | SDLC compliance audits | On demand |

**How it feeds into the knowledgebase:** insights from state-file maintenance (patterns across projects, recurring failure modes, architecture decisions) get distilled into `tech-gc-knowledgebase/` during the daily research cron and weekly ops cycles. The state files are Morris's working memory; the knowledgebase is the organization's long-term memory.

**Guard requirements:**
- `cat /home/hermes/.hermes/sessions/sessions.json` — allowed (state-file carve-out)
- `cat /home/hermes/.hermes/sessions/session_*.json` — allowed
- `cat /home/hermes/state/morris/*.md` — allowed
- Write state files → SDK only (not native write_file)
- `git add /home/hermes/state/morris/` → allowed
- `git commit -m "state sync: ..."` → allowed
- `git push origin main` → allowed

**Existing hermes cron schedule (all enabled unless noted):**
| Schedule | Job | Purpose |
|---|---|---|
| `*/15 * * * *` | Morris State Sync | 15-min lightweight context capture |
| `0 5 * * *` | Morris Daily Conversation Synthesis | Deep 24h reconciliation |
| `0 4 * * *` | Morris Nightly Git Push | Commit + push state + KB |
| `0 10 * * 1-5` | Morris Daily Standup | Morning briefing to Mark |
| `0 9 * * *` | Morris Daily Tech Research | Agentic dev research sprint |
| `0 14 * * 2` | Morris Weekly Backlog Review | Backlog grooming with Mark |
| `0 5 * * 1` | Monday InfoSec Review | Weekly security review |
| `0 5 * * 3` | Wednesday Code Quality Review | Weekly code quality |
| `0 5 * * 5` | Friday SRE Review | Weekly reliability review |
| DISABLED | morris-heartbeat (every 15m) | Replaced by fleet-check + state-sync |
| DISABLED | Morris Fleet Health (4x/day) | Replaced by fleet-vigilance |

---

## What Morris must NOT do

- Write code (implementation, bug fixes, refactoring)
- Claim dispatch queue items (dispatch-poller is disabled)
- Use `read_file`, `write_file`, `web_search`, `browser`, `code_execution`, `delegation` native Hermes tools (removed from platform_toolsets)
- Use `curl`, `python3 -c`, `sed`, `awk -i` via terminal (denied by guard)
- Edit files on other agents' VMs directly (dispatch or SDK instead)

---

## Config summary

**Hermes platform_toolsets** (hermes-config.yaml):
```yaml
platform_toolsets:
  teams: [terminal, memory, skills, cronjob, clarify, session_search, todo]
  cron: [terminal, memory, skills, cronjob, clarify, session_search, todo]
```

**Terminal guard carve-outs** (for manager role):
- State-file reads: `/home/hermes/state/`, `/home/hermes/.hermes/`, `/var/log/`, `/tmp/`
- Ops scripts: `/opt/agent/*`
- SSH delegation: `ssh ` (inner command = remote guard's problem)
- Claude invocation: `claude -p`, `claude --*`

**Crons** (hermes user):
- `*/15 * * * *` — fleet-check
- `*/30 * * * *` — Graph token refresh
- `0 * * * *` — cost monitor
- `0 */6 * * *` — Claude keepalive ping
- `0 6 * * *` — SDLC pull

**System cron** (root):
- `0 6 * * 0` — weekly OS + Claude Code patch
