# STORY-767 — Fleet-Vigilance Blind-Spot Closures (VM Reachability, Stuck Deploy, Code-Drift, NULL failure_reason, Zombie Heartbeat, No-Seed Dispatch)

## Overview
| Field | Value |
|-------|-------|
| Mode | feature_update |
| Scope | small |
| Feature Name | Fleet-vigilance blind-spot closures |
| Phase Path | 1 → 7 → 8 → Done |
| Repo | tech-dev-agents |
| Frontend | false |
| Soft dep | STORY-762 (failure_reason persistence — already shipped) provides the data Check 12 reads |

## Problem Statement

Morris's fleet-vigilance skill (`deployment/vm/skills/fleet-vigilance/SKILL.md`) currently runs Checks 0–8 every 30 minutes covering token quota, stuck claims, ghost completions, PR review backlog, and auth drift. **It missed every problem we hit during the 2026-04-29/30 incident**.

Concrete failure trace from that incident:

| Today's incident | Existing checks that should have caught it | Reality |
|---|---|---|
| Devon VM unreachable for ~30 min | None | Mark noticed manually after stories started failing |
| Daisy VM unreachable for hours | None | Same — discovered only when push-code.sh hung on her |
| `push-code.sh` hung 60+ min on devon, then daisy | None | Process sat in scp banner-exchange wait until killed manually |
| Agents running 6+ hour-old code after merges | None | grep on `/opt/agent/dispatch_poller.py` showed `Apr 29 11:36` long after `Apr 29 18:42` merge timestamps |
| 11 stories failed with `failure_reason=NULL` (STORY-007–STORY-017) | None | All 11 hit retry-cap silently |
| STORY-010 zombie heartbeat for 4 hours | Stuck-claim detector (Check 5) — but heartbeat was *present* | Existing check looks for absent heartbeat; presence-without-progress is a different signal |
| STORY-780–784 dispatched without seed.md → Phase 8 success → /complete 422 | None | Stories repeatedly failed at completion gate because no Phase 1 was run |

Every one of these is a 30-line Python check + a markdown section in the skill. None require new infra.

This story adds Checks 9–14 to fleet-vigilance, each with a concrete query, severity rubric, remediation step, and a one-line Teams DM template.

## Target User / Use Case

**User:** Morris's heartbeat cron + fleet-vigilance skill execution.
**Today:** entire classes of incident pass without alerting; Mark discovers them by hand.
**After this story:** Morris's 30-min vigilance loop catches each incident class within one cycle. CRIT cases DM Mark; WARN cases log to fleet-health.md without paging.

(Note: STORY-766's "Check 9: Post-Merge Deploy + Re-Enqueue Sweep" is being implemented separately. This story adds **Checks 10–15** so we don't conflict on numbering. If 766 hasn't shipped when 767 starts Phase 7, agent should bump to Checks 10–15 and document the offset.)

## Success Criteria

1. **SC-1 — Check 10: VM reachability ping.** Per cycle: `ssh -p 443 -o ConnectTimeout=8 azureagent@<ip> "echo PONG"` to each agent VM in agent-registry.json. Failure → CRIT, DM Mark with VM name + last successful ping. Idempotent — multiple consecutive failures result in one DM, not N.
2. **SC-2 — Check 11: Stuck deploy / push-code.sh.** Per cycle: `pgrep -f "push-code.sh"` on the local Morris machine; for each PID, check `etime` (wall-clock age). Any push-code.sh process older than 15 min → CRIT, DM Mark with PID + age + last known target VM (parse from process tree).
3. **SC-3 — Check 12: Code-version drift.** Per cycle: query `dispatch_items` for the most recent merge commit affecting `deployment/hermes/*` (read from git log). For each agent VM, SSH and check `/opt/agent/dispatch_poller.py` mtime. If any agent's mtime < merge commit timestamp - 1 hour, drift detected → WARN if 1 agent, CRIT if 2+ agents (suggests systemic deploy failure).
4. **SC-4 — Check 13: NULL failure_reason spike.** SQL: `SELECT count(*) FROM dispatch_items WHERE status='failed' AND failure_reason IS NULL AND failed_at > now() - interval '1 hour'`. > 0 in last hour → WARN; > 5 → CRIT (data-collection broken).
5. **SC-5 — Check 14: Zombie heartbeat detection.** Per agent VM: SSH and check `journalctl -u dispatch-poller --since '2 hours ago' | grep heartbeat | tail -10` and `journalctl ... | grep phase_end | tail -1`. If heartbeats present in last 30 min but most recent `phase_end` is > 2× the largest phase timeout (default 2400s) → CRIT, DM Mark with story_id + agent + last phase_end timestamp. STORY-763 fixes this in the agent itself; this is the fleet-wide observer for cases the agent missed.
6. **SC-6 — Check 15: No-seed dispatch detection.** SQL: `SELECT story_id, repo FROM dispatch_items WHERE status IN ('pending', 'claimed', 'in_progress') AND enqueued_at > now() - interval '1 hour'`. For each: SSH the claimed agent's VM (or any VM if unclaimed) and check `ls -la ~/dev/hpi-gorillacommerce/<repo>/features/story-<N>*/seed.md`. Missing → WARN, DM Mark with story_id + repo. Repeated detection across cycles → CRIT.
7. **SC-7 — Each check has a fail-safe.** If a check itself errors (e.g., SSH timeout, SQL connection failure), log it to fleet-health.md as `Check N: error_unavailable` and proceed to the next. Don't abort the whole vigilance run.
8. **SC-8 — Updates to fleet-health.md** are atomic per cycle. Each check appends a status line; the file is written once at the end of the run.
9. **SC-9 — Zero regressions** on existing Checks 0–9. Run order is preserved.

## Verification Plan

| SC | Command | Expected |
|----|---------|----------|
| SC-1 | `pytest tests/morris/test_fleet_vigilance_blind_spots.py::test_vm_reachability_check -v` | PASSED |
| SC-2 | `pytest tests/morris/test_fleet_vigilance_blind_spots.py::test_stuck_push_code_detection -v` | PASSED |
| SC-3 | `pytest tests/morris/test_fleet_vigilance_blind_spots.py::test_code_drift_detection -v` | PASSED |
| SC-4 | `pytest tests/morris/test_fleet_vigilance_blind_spots.py::test_null_failure_reason_spike -v` | PASSED |
| SC-5 | `pytest tests/morris/test_fleet_vigilance_blind_spots.py::test_zombie_heartbeat_detection -v` | PASSED |
| SC-6 | `pytest tests/morris/test_fleet_vigilance_blind_spots.py::test_no_seed_dispatch_detection -v` | PASSED |
| SC-7 | `pytest tests/morris/test_fleet_vigilance_blind_spots.py::test_check_failures_dont_abort_run -v` | PASSED |
| SC-9 | `pytest tests/morris/ -v` | All existing morris tests pass; zero regressions |

## Test Criteria

- **Tests use mocked subprocess + mocked DB connections.** No live SSH, no real DB writes during unit tests.
- **Each check tested in isolation** with explicit positive + negative fixtures.
- **Failure-of-a-check tested:** SC-7 asserts a check that raises an exception during execution doesn't abort the run.
- **DM template tested:** assert the Teams DM payload format matches expected template.
- All tests deterministic, < 2 seconds total.

## Validation

| Step | Command | Pass criterion |
|------|---------|----------------|
| 1 | `pytest tests/morris/test_fleet_vigilance_blind_spots.py -v` | All ≥ 7 tests GREEN |
| 2 | `pytest tests/ -x --ignore=tests/e2e -q` | Full suite GREEN |
| 3 | After deploy: simulate one of the incident classes (e.g., touch `/opt/agent/dispatch_poller.py` to bump mtime backward, or `INSERT` a NULL-failure_reason row) and verify the next vigilance cycle detects it. | Documented in PR body — observed detection + DM mock |

## Acceptance Criteria

- [ ] AC-1: New section in `deployment/vm/skills/fleet-vigilance/SKILL.md` titled "Checks 10–15" with the same structure as Checks 0–8 (purpose, command, severity rubric, remediation, DM template).
- [ ] AC-2: New helper `deployment/morris/scripts/blind_spot_checks.py` (or extend `heartbeat-collector.py`) implementing all 6 checks. Pure Python stdlib + asyncpg.
- [ ] AC-3: Each check is a discrete function — easy to test, easy to enable/disable individually via env var.
- [ ] AC-4: Each check writes a status line to fleet-health.md in a consistent format: `[Check N] <severity>: <message>`.
- [ ] AC-5: SSH commands have `-o ConnectTimeout=8` (matches existing pattern) so a hung VM doesn't stall vigilance.
- [ ] AC-6: SQL queries use the existing dispatch DB connection pool — no new DB credentials.
- [ ] AC-7: DM throttling — if a check has fired CRIT in the last 4 hours, suppress the DM and just log. Avoids alert fatigue. Reset suppression timer on next clean cycle.
- [ ] AC-8: Existing checks (0–9, including STORY-766's Check 9 if shipped) are unmodified. New checks are additive.
- [ ] AC-9: Zero regressions in existing morris tests.
- [ ] AC-10: Logging — every check logs `[FLEET-VIGILANCE Check N] <result>` to stdout (visible in Loki).
- [ ] AC-11: Error/logging AC — if a check raises, the exception is caught, logged with full traceback, and the check status is recorded as `error_unavailable: <exception type>`. No silent failures.

## Constraints
| Constraint | Value |
|------------|-------|
| Budget | small — additive checks + tests |
| Timeline | important — every day without these checks is a day Morris can't catch the next outage |
| Tech | Python 3.12 stdlib + asyncpg; no new deps |

## Performance Requirements
- Per cycle overhead total for all 6 new checks: < 30 seconds (most time spent on SSH calls — 4 VMs × ~3-5s each).
- Each check individually: < 10 seconds.

## Security Constraints
- [ ] No new auth surface; uses existing SSH keys + DB credentials.
- [ ] DM payloads MUST NOT contain credentials or tokens. Truncate command outputs to 200 chars.
- [ ] Failure logs in fleet-health.md MUST NOT include sensitive paths.

## Operational Lifecycle
- **Configuration:** Each check has an env var to enable/disable: `FV_CHECK_10_VM_REACH=1`, `FV_CHECK_11_STUCK_DEPLOY=1`, etc. Defaults all enabled.
- **Tuning:** thresholds (15 min for stuck deploy, 1 hr for drift, 5 for spike) are env-overridable.
- **Monitoring:** Loki picks up `[FLEET-VIGILANCE Check N]` lines. Spike in CRIT events = systemic issue.

## Boundaries

| Always Do | Ask First | Never Do |
|-----------|-----------|----------|
| Run each check independently; log failures don't abort the cycle | Whether a 7th check should be added (e.g., for orphan claude processes) | Block subsequent checks if one fails |
| Use existing SSH/DB connection patterns | Whether to send DM via Teams MCP vs direct webhook (current scope: existing MCP plumbing) | Introduce new DM channels |
| Throttle DMs to prevent alert fatigue (4-hour suppression per check) | Whether suppression should reset on operator action (manual `/dispatch/clear-vigilance-state`) | Page Mark every cycle on the same recurring issue |
| Document each check's threshold and severity rubric in SKILL.md | Whether to expose check status on the dashboard | Hardcode IPs (use agent-registry.json) |
| Keep checks fast (< 10s each) | Whether to parallelize SSH calls (probably yes; current scope: serial is fine if total < 30s) | Make a check that requires interactive input |

## Files to Modify

- `deployment/vm/skills/fleet-vigilance/SKILL.md` — add Checks 10–15 section.
- `deployment/morris/scripts/blind_spot_checks.py` — **new file**, 6 check functions + dispatcher.
- `deployment/morris/scripts/heartbeat-collector.py` — invoke the new helper after Checks 0–9.
- `tests/morris/test_fleet_vigilance_blind_spots.py` — **new file**, ≥ 7 tests.
- `state/morris/SETUP-CHECKLIST.md` — document the new env vars.
- `features/story-767-fleet-vigilance-blind-spots/test-design.md` — Phase 7 deliverable.
- `.project`, `backlog.md` — tracking.

## Files to NOT Modify

- Existing fleet-vigilance Checks 0–9 — preserve semantics.
- `agent-registry.json` — read-only; do not modify list of agents.
- The dispatch DB schema — checks read existing data, no migrations.
- STORY-766's Check 9 (post-merge deploy) — orthogonal; this story explicitly numbers from 10.
- Frontend — out of scope.

## Done Looks Like

```
$ pytest tests/morris/test_fleet_vigilance_blind_spots.py -v
test_vm_reachability_check PASSED
test_stuck_push_code_detection PASSED
test_code_drift_detection PASSED
test_null_failure_reason_spike PASSED
test_zombie_heartbeat_detection PASSED
test_no_seed_dispatch_detection PASSED
test_check_failures_dont_abort_run PASSED
test_dm_throttling_per_check PASSED
========= 8 passed in 0.74s =========

$ pytest tests/ -x --ignore=tests/e2e -q
ALL PASSED

# After deploy, fleet-health.md from a vigilance cycle:
[Check 10 VM Reach] OK: dan ✓ derrick ✓ daisy ✓ devon ✓
[Check 11 Stuck Deploy] OK: no push-code processes
[Check 12 Code Drift] OK: all agents within 1h of latest hermes/* merge
[Check 13 NULL failure_reason] WARN: 2 rows with NULL in last hour (STORY-783, STORY-781)
[Check 14 Zombie Heartbeat] OK: no zombies detected
[Check 15 No-Seed Dispatch] CRIT: STORY-784 (advertising-amazon) — seed missing
```

## Escalation Contract

1. **A check requires data not currently exposed** (e.g., agent uptime via API rather than SSH) — STOP, propose API addition as separate story. Don't add SSH if API would work better.
2. **Existing fleet-vigilance has its own state file format** that conflicts with appending — read the existing format, follow it, don't break parsing.
3. **STORY-766's Check 9 hasn't shipped yet** — start at Check 9 instead of 10. Document the renumbering choice in PR body.
4. **DM throttling state needs to persist across vigilance cycles** — use `/home/hermes/state/morris/vigilance-dm-suppression.json`. Same git-untracked rule as fleet-health.md.
5. **A check would require Mark-only credentials** (e.g., Azure CLI for VM info beyond reachability) — out of scope, document and escalate.

**Default if no story-specific rule fires:** if more than 1 turn is spent guessing, write QUESTION.md and pause.

## Codebase Context

| Aspect | Details |
|--------|---------|
| Affected files | `deployment/vm/skills/fleet-vigilance/SKILL.md` (Checks 10–15 added), new helper at `deployment/morris/scripts/blind_spot_checks.py` |
| Reference incident | 2026-04-29/30 — devon + daisy outages, deploy hangs, NULL failure_reason spike, STORY-010 zombie, STORY-780–784 no-seed batch |
| Architecture | Morris cron → fleet-vigilance markdown instructions → Python helper → SSH to agents + asyncpg to dispatch DB |
| Test pattern | Mock subprocess, mock asyncpg, tmp_path fixtures (existing pattern in `tests/morris/`) |

## Out of Scope

- Auto-remediation of detected blind-spots (this story is detection only — file follow-up STORY for remediation per check).
- Dashboard widget showing vigilance check status (separate frontend story).
- Cross-cluster checks (multi-tenant fleet) — out of scope; current scope is single ops-console fleet.

## Notes for Implementer

- **Reference incident playback:** look at this session's chat log for the specific failure modes. Each check should catch one of them.
- **Reference Check 0** (token quota) for the structural pattern. Same severity rubric, same fleet-health.md update conventions.
- **Throttling state file** (`vigilance-dm-suppression.json`) must be git-untracked. Existing fleet-vigilance SKILL.md has a CRITICAL section about this.
- **STORY-766 numbering** — when implementing, check whether STORY-766 has shipped; if so, start at Check 10. If not, start at Check 9 and document the offset.
- **Existing DM plumbing** uses `mcp__agent-ops__send_message`. Reuse, don't reinvent.
