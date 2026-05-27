# Fleet Health — 2026-05-18T14:00:11Z

## Status: CRIT

**Root blocker: SSH key auth failure on all 4 agent VMs — ongoing since ~May 14. No agents claiming work. No monitoring, no remediation, no deployments possible.**

## Token Pacing
- Dan: UNKNOWN (SSH rejected)
- Derrick: UNKNOWN (SSH rejected)
- Daisy: UNKNOWN (SSH rejected)
- Devon: UNKNOWN (SSH rejected)

## Queue
- pending=3, claimed=0, in_progress=0, needs_info=0
- STORY-823 (advertising-amazon, medium) — Campaign Health Monitor Phase 1, enqueued May 3 (15d stale)
- STORY-804 (tech-dev-agents, small) — PR 227 rework, enqueued May 5 (13d stale)
- STORY-641 (advertising-amazon, medium) — Competitor Data Loader Phase 4, enqueued May 5 (13d stale)
- **No agents are claiming work.** All 3 stories pending 13-15 days with 0 claims.

## Agents
- **Dan** (20.228.224.243): CRIT — SSH `Permission denied (publickey)`. Cannot assess poller/SDK/disk/auth.
- **Derrick** (20.121.210.186): CRIT — SSH `Permission denied (publickey)`. Cannot assess status.
- **Daisy** (20.98.231.234): CRIT — SSH `Permission denied (publickey)`. Cannot assess status.
- **Devon** (20.186.26.130): CRIT — SSH `Permission denied (publickey)`. Cannot assess status.

## PRs — tech-dev-agents (7 open)
- **PR #303** (STORY-871) — 14d old, CHANGES_REQUESTED, CI: `check` (migration invariant) FAILING. **CRIT (>48h + red CI).**
- **PR #316** (STORY-874) — 13d old, CHANGES_REQUESTED, CI: all passing. **CRIT (>48h).**
- **PR #321** (STORY-903) — 12d old, CHANGES_REQUESTED, no CI checks. **CRIT (>48h).**
- **PR #323** (Stall Detection) — 12d old, APPROVED, CI: `Python contract + unit tests` FAILING. **CRIT (>48h + red CI).**
- **PR #326** (STORY-902) — 10d old, CHANGES_REQUESTED, CI: all passing. **CRIT (>48h).**
- **PR #333** (STORY-917) — 4d old, no review, no CI checks. WARN (>6h).

## PRs — advertising-amazon (5 open)
- **PR #609** — 2.5d old, APPROVED, `Unit Tests` FAILING. **CRIT (>48h + approved but red CI blocks merge).**
- **PR #620** — 2d old, REVIEW_REQUIRED, `Unit Tests` FAILING. WARN.
- **PR #621** — 2d old, REVIEW_REQUIRED, `Unit Tests` + `UAT Critical Assertions` FAILING. WARN.
- **PR #637** — NEW (15min), Mark's CI revision health gate. `Unit Tests` FAILING. Active incident response.
- **PR #638** — NEW (just opened), Mark's prod crash-loop fix. CI still running. **Active prod incident.**
- PR #608 (Jack's hotfix) — MERGED on May 15. ✓

## PRs — api-nimbleway (7 open, ALL with failing CI — NOW CRIT)
- **PR #16** (pc-ui-002, grid polling) — 4d old, `backend-test` + `api test` FAILURE. **CRIT (>48h).**
- **PR #18** (pc-ui-003, answer notes) — 4d old, `backend-test` FAILURE. **CRIT (>48h).**
- **PR #19** (pc-ui-004, evidence drawer) — 4d old, `backend-test` FAILURE. **CRIT (>48h).**
- **PR #21** (pc-009-c, row evidence) — 4d old, `backend-test` + `frontend-build` FAILURE. **CRIT (>48h).**
- **PR #22** (pc-ui-005, history sparkline) — 4d old, `backend-test` FAILURE. **CRIT (>48h).**
- **PR #23** (pc-ui-006, polish UAT) — 4d old, `backend-test` FAILURE. **CRIT (>48h).**
- **PR #25** (pc-008-c, ads UI) — 4d old, `backend-test` FAILURE. **CRIT (>48h).**
- **⚠️ Pattern: ALL 7 PRs fail `backend-test` — systemic CI infra issue, not per-PR bugs.**
- **ESCALATED: These crossed 48h on ~May 16. No progress.**

## PRs — tech-project-mapping
- 0 open PRs.

## Ghost Completion Check
- All recent completed stories have null commit_sha — ongoing pattern. No new ghost completions.

## Failed Stories
- 166 total failed stories in history. No new failures since last check.

## Needs Info Triage
- 0 needs_info stories — nothing to triage.

## Stuck Claims
- None (0 claimed items).

## VM Health (Morris only — agent VMs inaccessible)
- Morris: disk=60%, mem=17% — OK.
- hermes-gateway: active.
- push-code.sh: no stuck processes.

## Checks 9-16 Summary
- [Check 9 VM Reach] CRIT: dan ✗ derrick ✗ daisy ✗ devon ✗ (SSH key rejected, all 4)
- [Check 10 Stuck Deploy] OK: no push-code processes
- [Check 11 Code Drift] UNKNOWN: cannot SSH to verify
- [Check 12 NULL failure_reason] UNKNOWN: cannot query DB directly
- [Check 13 Zombie Heartbeat] UNKNOWN: cannot SSH to verify
- [Check 14 No-Seed Dispatch] UNKNOWN: cannot SSH to verify
- [Check 15 Foundry Auth] UNKNOWN: blocked by terminal filter
- [Check 16 needs_info Pattern] OK: 0 needs_info stories

## Incidents This Cycle
1. **[CRIT] SSH key auth failure — ALL 4 agent VMs** — Ongoing since ~May 14 (~4.5 days). Morris cannot monitor, remediate, or deploy to any agent. Root blocker for all fleet operations.
2. **[CRIT] Queue stagnation — 13-15 days** — 3 stories pending 13-15 days, 0 agents claiming.
3. **[CRIT] 5 tech-dev-agents PRs older than 48h** — PR #303 (14d), #316 (13d), #321 (12d), #323 (12d), #326 (10d).
4. **[CRIT] api-nimbleway: 7 PRs older than 48h with systemic CI failure** — All 7 fail `backend-test`. Now 4 days old. Escalated from WARN → CRIT since last check.
5. **[CRIT] advertising-amazon PR #609** — APPROVED by Mark, 2.5d old, `Unit Tests` failing. Blocks merge.
6. **[NEW] Active prod incident** — Mark opened PR #637 (CI revision health gate) + PR #638 (non-blocking lifespan fix) to address crash-looping ca-mcp-server--0000488 in production.

## Actions Taken
- Verified SSH auth still rejected on all 4 agent VMs.
- Verified queue state unchanged (3 pending, 0 claimed).
- Confirmed PR #608 (Jack's hotfix) merged.
- Noted api-nimbleway PRs escalated WARN → CRIT (crossed 48h).
- Noted Mark is actively firefighting prod incident — deferring DM to avoid interrupting incident response.
- DM suppressed this cycle: Mark is visibly active on prod incident (PRs opened in last 15 min). Will DM with consolidated summary after incident stabilizes if issues remain.

needs_info triage: 0 auto-resumed, 0 cancelled, 0 escalated to Mark.
PR autofix: 0 dispatched (agents not claiming — no point dispatching fix stories to an idle fleet).
automation: review-prs=blocked (agents not claiming rework), needs-info-drain=done (0 items).
