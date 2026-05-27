# Research — STORY-724: Morris Queue Orchestrator Tier

**Story:** STORY-724
**Phase:** 2 (Research)
**Date:** 2026-04-26
**Author:** research subagent

---

## 1. What Morris Currently Does

### 1.1 Cron Schedule (on the Morris VM)

Morris runs the following automated jobs (source: `deployment/vm/MORRIS-CRITICAL-FUNCTIONS.md` §11):

| Schedule | Job | Purpose |
|---|---|---|
| `*/15 * * * *` | `morris-fleet-check.sh` | Fleet vigilance — queue + agent health |
| `*/15 * * * *` | Morris State Sync | Lightweight 15-min context capture |
| `*/30 * * * *` | Graph token refresh | Keep Microsoft Graph auth alive |
| `0 * * * *` | Cost monitor | Per-agent cost tracking |
| `0 */6 * * *` | Claude Code keepalive ping | Prevent OAuth token expiry |
| `0 4 * * *` | Morris Nightly Git Push | Commit + push state + KB |
| `0 5 * * *` | Morris Daily Conversation Synthesis | Deep 24h reconciliation |
| `0 6 * * *` | SDLC framework pull | Pull latest SDLC config from repo |
| `0 10 * * 1-5` | Morris Daily Standup | Morning briefing to Mark |
| `0 9 * * *` | Morris Daily Tech Research | Agentic dev research sprint |
| `0 14 * * 2` | Morris Weekly Backlog Review | Backlog grooming |
| `0 5 * * 1` | Monday InfoSec Review | Weekly security review |
| `0 5 * * 3` | Wednesday Code Quality Review | Weekly code quality |
| `0 5 * * 5` | Friday SRE Review | Weekly reliability review |
| `0 6 * * 0` (root) | Weekly OS + Claude Code patch | `weekly-patch.sh` |

**The `*/15` fleet-check is the closest existing analog to what STORY-724 needs**, but it does not take corrective action on the queue — it only observes and alerts.

### 1.2 The `morris-fleet-check.sh` Two-Stage Pattern

The existing fleet-vigilance wrapper (`deployment/vm/morris-fleet-check.sh`) establishes the canonical pattern Morris uses for automated observation:

- **Stage 1 (bash, ~5s):** Collect all fleet data natively — queue API call, SSH probes to Dan/Derrick, ghost-completion SQL query via SSH to the DB VM, open PRs from `gh pr list`, Morris VM disk/mem. Writes a consolidated JSON blob to `/tmp/fleet-data.XXXXXX.json`.
- **Stage 2 (claude SDK, ≤10 turns):** Reads the JSON blob once, applies the fleet-vigilance SKILL.md rules, writes the state file, DMs Mark on CRIT, and optionally remediates. Budget: 10 turns max.
- **Reentrancy guard:** `flock -n 200` on `/var/run/morris-fleet-check.lock` — second invocation exits cleanly if the first is still running.
- **Log:** `/var/log/morris-fleet-check.log`

This pattern — bash data collection → SDK reasoning/action — is the architectural template STORY-724 should follow.

### 1.3 Existing Morris Skills

Morris has the following modular skills in `deployment/vm/skills/morris/`:

| Skill | Purpose |
|---|---|
| `dispatch-queue` | View queue state, detect stale items, report queue health; pulls from Monday.com + git branches |
| `fleet-health` | Per-VM SSH probe; reports disk/mem/service status |
| `curator` | (unknown — not inspected) |
| `daily-standup` | Morning briefing: summarises yesterday, open PRs, fleet health, blockers |
| `merge` | Merge approved PRs; includes `gh pr view --json mergeable,mergeStateStatus,statusCheckRollup` |
| `reliability-check` | (unknown — not inspected) |
| `review-prs` | Review open PRs across repos |

The `merge` skill already uses `gh pr view --json mergeable,mergeStateStatus` — exactly the field set needed for conflict detection. The orchestrator can reuse this pattern.

### 1.4 Teams DM Mechanism

Morris sends Teams DMs via the Microsoft 365 CLI (`m365` command) + Graph API direct calls, managed by `teams_m365.py`. The live deployed version is `deployment/vm/teams_m365_deployed.py`, which uses `aiohttp` to call `POST /chats/{chatId}/messages` on the Graph API. The CLI handles auth; the graph call itself is ~200ms.

The `_get_m365_token()` helper in `teams_m365_deployed.py` extracts the token via `m365 util accessToken get --resource https://graph.microsoft.com`. This is fully operational on the Morris VM.

From the fleet-vigilance SKILL.md and the daily-standup skill, the current DM pattern is:
```
[CRIT/WARN/INFO/ACTION/APPROVAL-NEEDED] headline
- bullet 1
- bullet 2
```
This aligns with the severity prefix taxonomy specified in the seed.

### 1.5 What Morris Does Not Currently Do

1. **No queue intervention.** The `dispatch-queue` skill only reads and reports. There is no path where Morris releases a stale claim, re-routes a pending item, or calls `POST /api/dispatch/release/{story_id}`.
2. **No PR conflict detection loop.** The `merge` skill uses `gh pr view --json mergeable,mergeStateStatus` on-demand but does not run a polling loop across all repos looking for `CONFLICTING` PRs.
3. **No repeated-failure escalation.** The fleet-vigilance SKILL.md (Check 5) lists failed stories and DMs Mark, but does not classify repeated failures (same story, N times in 24h) and does not block re-enqueue.
4. **No load-balancing.** The dispatch queue is FIFO; there is no agent-specific routing. Agents claim by polling `/api/dispatch/next` — strictly first-come first-claimed.
5. **No needs_info decay surfacing.** No existing script queries for stories in `needs_info` status aging past a threshold and surfaces them to Mark.
6. **No morning briefing.** The daily standup (08:30 ET equivalent = 10:00 UTC) is a Teams message but is structured around yesterday's work / PRs / fleet health — not a queue-focused orchestrator briefing with pending/claimed/in_progress counts, failed stories, and recommended actions.
7. **No `deployment/morris/scripts/` directory.** The seed proposed this path but it does not exist. Current Morris scripts are co-located with the hermes deployment (`deployment/vm/`) and SDLC scripts (`scripts/`). The `scripts/fleet_review.py` and related files live in `scripts/`. **Phase 6 must decide: create `deployment/morris/scripts/` or co-locate in `scripts/`.**

---

## 2. Dispatch API Endpoints Available to Morris

Base URL: `https://tech-dev-agents.gorillacommerce.ai` (env var `OPS_CONSOLE_URL`)
Auth: `X-API-Key` header (env var `OPS_CONSOLE_API_KEY` from `/opt/agent/.env`)

### 2.1 Current Production Endpoints (main branch)

| Endpoint | Method | Purpose | Key fields returned |
|---|---|---|---|
| `GET /api/dispatch/queue` | GET | Active queue: pending + claimed items | `pending[]`, `claimed[]`, `total_pending`, `total_claimed`, `fetched_at` |
| `GET /api/dispatch/next` | GET | Oldest pending item; 204 if empty | `item`, `queue_depth` |
| `POST /api/dispatch` | POST | Enqueue a new story | `item`, `queue_depth` |
| `POST /api/dispatch/claim/{story_id}` | POST | Claim pending story | `story_id`, `claimed_by`, `claimed_at`, `item` |
| `DELETE /api/dispatch/queue/{story_id}` | DELETE | Cancel a pending story (NOT a claimed one) | `story_id`, `cancelled` |
| `POST /api/dispatch/complete/{story_id}` | POST | Mark claimed story completed (requires commit_sha) | `story_id`, `completed`, `item` |
| `POST /api/dispatch/fail/{story_id}` | POST | Mark claimed story failed | `story_id`, `failed`, `exit_code` |
| `GET /api/dispatch/history` | GET | Paginated history: completed/cancelled/failed | `items[]`, `total`, `limit`, `offset` |
| `POST /agents/register` | POST | Register an agent | `name`, `registered`, `is_new` |

**Key gap:** There is **no `POST /api/dispatch/release/{story_id}` endpoint in the current main branch**. The service layer has `recover_stale_claims()` which moves claimed items back to pending based on timeout, but it has no exposed HTTP endpoint. To release a single stale claim, Morris would need to either call the DB directly (bad) or trigger `recover_stale_claims` via an admin endpoint (which does not exist).

### 2.2 Endpoints Added by STORY-702 (pending merge)

STORY-702's worktree (`/tmp/wt-702`) adds several critical endpoints and service methods:

| Endpoint | Method | Purpose | Notes |
|---|---|---|---|
| `POST /api/dispatch/heartbeat/{story_id}` | POST | Update `claim_heartbeat_at = now()` for a claimed story | Idempotent; no-ops if not claimed |
| `POST /api/dispatch/release/{story_id}` | POST | Transition claimed → pending (any agent can call) | The missing primitive for stale-claim recovery |
| `POST /api/dispatch/force-release/{story_id}` | POST | Admin override: release from ANY non-terminal state | Requires `reason` body field; full audit log |

The `release` endpoint (`claimed → pending`) is exactly what the orchestrator needs for autonomous stale-claim release. **STORY-724 must land after STORY-702** or include a stub endpoint in Phase 6.

### 2.3 The `recover_stale_claims()` Method (STORY-702)

The upgraded `DispatchDBService.recover_stale_claims()` (in `/tmp/wt-702/tech_dev_agents/ops_console/services/dispatch_db_service.py`) implements three tiers of staleness detection:

1. **Tier 1 — Never-started claims** (`updated_at == claimed_at`): SDK died before doing any work. Released after `never_started_timeout_seconds` (default 300s). Catches the ghost-claim pattern.
2. **Tier 2a — Heartbeat-stale, count < 3** (`claim_heartbeat_at` is set and older than `heartbeat_timeout_seconds` / 900s): Agent was pinging but went silent. Released to pending, `stale_release_count` incremented, heartbeat cleared.
3. **Tier 2b — Heartbeat-stale, count >= 3**: After 3 stale-releases, transitioned to `failed` with `failure_reason='agent_died'`. No further re-attempt without human review.
4. **Tier 3 — Phase-active-but-stale** (`updated_at > claimed_at`, no heartbeat): Phase runner started but hasn't emitted a phase event in `timeout_seconds` (default 3600s). Released to pending.

**What STORY-724 adds on top of this:** The orchestrator acts as an active monitor that reads `claim_heartbeat_at` from the queue API response and calls `POST /api/dispatch/release/{story_id}` for stories with `now - claim_heartbeat_at > 15m`. This is the consumer-side complement to the DB-side `recover_stale_claims`.

The key fields added by STORY-702 to the dispatch row schema:
- `claim_heartbeat_at` — last heartbeat timestamp
- `stale_release_count` — how many times this claim has been auto-released
- `failure_reason` — why a failed story failed (e.g., `agent_died`)

### 2.4 Status Values in Use

Current statuses in the dispatch system (from STORY-702 service code):
`pending` | `claimed` | `in_progress` | `in_review` | `paused` | `needs_info` | `completed` | `cancelled` | `failed`

The orchestrator's queue observer needs to handle all of these, particularly:
- `claimed` → stale detection and release
- `needs_info` → decay surfacing
- `failed` → repeated-failure classification
- `pending` → load-imbalance re-routing (only pending items can be re-routed safely)

---

## 3. `recover_stale_claims()` — Summary

The STORY-702 method runs inside the DB service layer. It is NOT called on a schedule from any existing code — it is intended to be called by the ops-console itself (e.g., from a background task or an admin endpoint). The existing main-branch `recover_stale_claims` (line 339 of the main-branch `dispatch_db_service.py`) only uses `claimed_at` as the reference and has no heartbeat awareness.

**STORY-724 does not re-implement this logic.** Instead, Morris reads heartbeat timestamps from `GET /api/dispatch/queue` and calls `POST /api/dispatch/release/{story_id}` for rows that exceed the 15-minute threshold. The DB-side `recover_stale_claims` is a safety net that runs independently (either from a scheduled task in the ops-console or from the STORY-702 admin endpoint — TBD in Phase 6).

---

## 4. `gh pr list` JSON Fields for Conflict Detection

From analysis of existing usage across `deployment/vm/skills/morris/merge/SKILL.md`, `deployment/vm/skills/fleet-vigilance/SKILL.md`, and the STORY-722 seed:

### 4.1 Fields Needed for Conflict Detection

```
gh pr list --repo hpi-gorillacommerce/<repo> --state open \
    --json number,title,author,headRefName,baseRefName,mergeable,mergeStateStatus,createdAt,reviewDecision
```

| Field | Type | Values | Purpose |
|---|---|---|---|
| `number` | int | — | PR identifier for rebase invocation |
| `title` | string | — | Human-readable context for DMs |
| `author.login` | string | — | Detect agent-owned vs human-owned PRs |
| `headRefName` | string | e.g. `story-644/declarative-state-convergence` | Identify which branch is conflicting |
| `baseRefName` | string | usually `main` | Rebase target |
| `mergeable` | string | `MERGEABLE`, `CONFLICTING`, `UNKNOWN` | Primary conflict signal |
| `mergeStateStatus` | string | `CLEAN`, `DIRTY`, `BLOCKED`, `BEHIND`, `UNKNOWN`, `HAS_HOOKS`, `DRAFT` | More granular state |
| `createdAt` | ISO 8601 | — | Age detection |
| `reviewDecision` | string | `APPROVED`, `REVIEW_REQUIRED`, `CHANGES_REQUESTED`, `null` | For escalation decisions |

### 4.2 Agent vs Human Determination

The agent service account logins are known: `Bot Dan`, `Bot Derrick`, `Bot Morris` (GitHub display names). In the JSON, `author.login` would be the GitHub username. The existing fleet-check script passes `author` through but does not filter by agent vs human — the orchestrator will need to implement this check using a config-driven allowlist.

### 4.3 `UNKNOWN` State

GitHub returns `mergeable=UNKNOWN` immediately after PR creation while it computes mergeability. STORY-722's `_check_pr_conflicting` handles this with a 5-iteration polling loop with 2s backoff. The orchestrator's PR observer should similarly treat `UNKNOWN` as "check next cycle" rather than triggering an intervention.

---

## 5. Current Gap: What Monitoring/Intervention Is Missing

Mapping the 6 failure modes from the seed to the current codebase:

| Failure Mode | Current State | Gap |
|---|---|---|
| Stuck claims | `recover_stale_claims()` exists in DB service but is not scheduled or exposed via HTTP | No active monitor calling release; no `POST /release` endpoint in production |
| Conflicting PRs | STORY-722 adds per-story auto-rebase at poller time; fleet-check reports open PRs | No cross-repo polling loop looking for `CONFLICTING` PRs between dispatch cycles |
| Repeated failures | `_report_fail` writes a flag file after 3 failures; fleet-vigilance Check 5 lists failed stories | No logic classifying "same story, N failures in 24h" and blocking re-enqueue |
| Queue imbalance | None | Entirely missing; no per-agent pending-count tracking, no re-routing logic |
| `needs_info` decay | `needs_info` status exists; no age-based surfacing | No query for `needs_info` items older than threshold |
| Morning briefing | Daily standup exists (10:00 UTC) | Standup is work-log focused; no queue-health focused briefing with recommended actions |
| Approval gate | None | No mechanism for Morris to post an `[APPROVAL-NEEDED]` DM and wait for Mark to confirm before acting |

The most critical gaps for fleet health are: stale-claim release, repeated-failure escalation, and load-balancing. The most impactful for operator experience are: morning briefing and needs_info surfacing.

---

## 6. Subagent Invocation Pattern (from `dispatch_poller.py`)

The `start_story` function in `dispatch_poller.py` is the existing pattern for launching a Claude SDK subprocess. It:

1. Resolves the working directory from candidate paths.
2. Appends an SDLC compliance block to the prompt.
3. Sets `DISPATCHED_BY_POLLER=1` in the subprocess env.
4. Launches `claude_sdk_tool.py -p <prompt> -w <workdir>` as a `subprocess.Popen`.
5. Runs `proc.wait()` in a daemon thread; reports complete or fail when the process exits.

For the orchestrator, the relevant pattern is simpler — Morris needs one-shot subagent invocations that:
- Complete within 10 minutes (one cron cycle)
- Return stdout/exit-code to the orchestrator
- Do not participate in the dispatch queue (no claim/complete cycle)

The seed proposes a `subagent_runner.run()` abstraction. The most direct implementation is `subprocess.run()` (not `Popen`) on `claude_sdk_tool.py` with a timeout — blocking the orchestrator loop for up to `timeout_seconds`, then kill. This is simpler than the threaded `Popen` pattern used in `start_story` because the orchestrator is itself running in a cron context (not a long-lived daemon).

The `claude_sdk_tool.py` in `/opt/agent/` accepts:
- `-p <prompt>` — the prompt
- `-w <workdir>` — working directory
- `--max-turns N` — turn cap
- `--permission-mode bypassPermissions` — skip approval prompts (used in fleet-check)

The `--add-dir` flag used in `morris-fleet-check.sh` grants SDK access to directories outside the workdir — needed for any subagent that needs to touch git repos in `/home/hermes/dev/` while also reading Morris state in `/home/hermes/state/`.

---

## 7. Key Architecture Observations

### 7.1 No `deployment/morris/scripts/` Directory Exists

The seed's proposed location (`deployment/morris/scripts/`) does not exist today. All Morris scripts live in:
- `deployment/vm/` — VM-level scripts deployed to all agents (fleet-check, terminal guard, etc.)
- `deployment/vm/skills/morris/` — Morris-specific skill definitions (SKILL.md files)
- `scripts/` — standalone tools run from a dev machine or via SDK (fleet_review.py)

Phase 6 must decide the canonical location for the new orchestrator scripts. Options:
1. Create `deployment/morris/scripts/` — clean separation but requires new deploy tooling
2. Add to `scripts/` alongside `fleet_review.py` — least deploy friction (already synced)
3. Add to `deployment/vm/` as a new file — follows fleet-check convention, simpler deploy

### 7.2 The Two-Stage (bash + SDK) Pattern Is Battle-Tested

The `morris-fleet-check.sh` two-stage design (bash data collection + SDK reasoning) is explicitly documented as "token-efficient." Stage 1 collects all data natively (~5s, zero SDK tokens). Stage 2 reasons on the collected blob (≤10 turns, bounded cost). The orchestrator loop should follow this pattern: bash collects queue state + PR list, SDK classifies and acts.

### 7.3 The Lock Mechanism

`morris-fleet-check.sh` uses `flock -n 200` on a lock file to prevent overlapping invocations. The orchestrator must use the same or an equivalent mechanism (a different lock file to avoid conflicts with the fleet-check). The seed proposes a DB-level advisory lock as an alternative, but `flock` on a file is simpler and already proven.

### 7.4 Agent Identity Mapping

The agent service accounts are:
- `dan` → IP `20.228.224.243`
- `derrick` → IP `20.121.210.186`
- `morris` → IP `20.246.36.143` (self)

The dispatch queue rows carry `claimed_by` (the agent name). The PR author is identified by `author.login` (GitHub username). The mapping between `claimed_by` (e.g., `derrick`) and the GitHub bot login (e.g., `Bot Derrick`) must be explicit in the orchestrator's config — this is not currently centralized anywhere.

### 7.5 `needs_info` Status

The `needs_info` status is live in the dispatch system (gated by `OPS_DISPATCH_NEEDS_INFO_ENABLED` flag). The queue endpoint returns `needs_info` items in the response (added in STORY-702). The `updated_at` field on a `needs_info` row tracks when it last changed state. The orchestrator can detect decay by checking `now - updated_at > 4h` for rows in `needs_info` status.

### 7.6 No Existing Reassignment Endpoint

The dispatch API has no `POST /api/dispatch/reassign/{story_id}` endpoint. Load-balancing (re-routing `pending` rows between agents) would need to be implemented as: read pending rows with `claimed_by` set (if any — pending rows may not have a `claimed_by`), then re-enqueue after cancelling. **Or more precisely:** pending items in the queue have no claimed_by; load balancing is about influencing which agent claims next. The only way to preference an agent is to temporarily pause other agents' pollers — which is invasive. Phase 6 must address this design gap.

---

## 8. Summary Table: Research Findings vs Seed Assumptions

| Seed Assumption | Finding |
|---|---|
| `deployment/morris/scripts/` exists | Does NOT exist — scripts live in `deployment/vm/` and `scripts/` |
| `GET /api/dispatch/queue` returns heartbeat timestamps | In main branch: NO. In STORY-702: YES (`claim_heartbeat_at` field) |
| `POST /api/dispatch/release` exists | In main branch: NO. In STORY-702: YES |
| `GET /api/dispatch/history?since=<iso>` exists | NOT as described; actual endpoint is `GET /api/dispatch/history?limit=N&offset=M&status=failed` — no `since` parameter |
| `GET /api/dispatch/stats` exists | Does NOT exist — must compute client-side from queue response |
| Subagent invocation via direct claude SDK calls | Confirmed — `claude_sdk_tool.py` with `--max-turns` and `--permission-mode bypassPermissions` |
| Teams DM via m365 CLI | Confirmed — m365 + Graph API, fully operational on Morris VM |
| 10-minute cron is viable | Confirmed — existing `*/15` works; `*/10` is straightforward to add |
| Flock-based reentrancy guard | Confirmed as the established pattern |
| Morris has m365/Teams access | Confirmed |
| STORY-722 provides the rebase script | STORY-722 seed documents the design but Phase 8 has not run — the actual `_auto_rebase` helper may not be merged yet. Phase 6 must include a shim. |
