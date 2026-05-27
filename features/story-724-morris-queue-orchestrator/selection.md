# Selection — STORY-724: Morris Queue Orchestrator Tier

**Phase:** 5 (Selection)
**Date:** 2026-04-26

---

## Selected Approach: A — Single Python Script (`orchestrator_loop.py`)

### Rationale

**Proven cron model.** All 14 existing Morris recurring jobs are crontab entries. `morris-fleet-check.sh` has been running since STORY-323 with zero operational incidents. Approach A is the direct extension of this model to the intervention layer.

**Testable without a live VM.** Each of the 6 detector functions accepts injected dependencies (API session, config dict, DM callable). The `--check <detector>` and `--dry-run` flags make individual detectors invocable from CI. Same testability as Approach B without 9-file overhead.

**Classification-before-action is structurally enforced.** `classify_all()` runs to completion on a frozen snapshot before `execute_interventions()` begins. Approach B requires explicit coordination discipline to achieve the same invariant.

**Minimal operational footprint.** Four new files and one crontab line. Disabling is one `crontab -e` edit.

---

## Design Constraints Carried Forward

1. **Flock reentrancy guard** — `/var/run/morris-orchestrator.lock`, separate from `morris-fleet-check.lock`.
2. **Classification-before-action** — all 6 detectors complete on a frozen snapshot before any intervention fires; priority: stale claims (1) > repeated failures (2) > PR conflicts (3) > needs_info surfacing (4) > load-imbalance DM (5).
3. **STORY-702 shim** — `POST /api/dispatch/release/{story_id}` may not be merged at Phase 8; Phase 6 must specify a fallback code path.
4. **Load-balancing v1 = informational DM only** — no `assigned_to` field; true re-routing deferred.
5. **STORY-722 config gate** — `interventions.rebase.enabled: false` by default.
6. **Every intervention emits a Teams DM** — no silent actions.

---

## What Is Deferred

- **True load-balancing**: requires `assigned_to` field on dispatch rows.
- **Reply-handling loop**: Mark's reply parsing (CANCEL/RETRY/SKIP tokens) deferred to a follow-on story. v1 posts the DM and logs; Mark acts via ops console.
- **STORY-722 rebase invocation**: config-gated off by default; flip once STORY-722 confirmed merged.
