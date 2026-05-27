# STORY-1010 — Ops-skill fallback registry + PR-link assertion at merge gate

**Story ID:** STORY-1010
**Scope:** Medium
**Epic:** STORY-1000 (Cross-Repo Canon Alignment)
**Work-Stream:** C — Queue validation & code-stability gates
**Repo touched:** `tech-dev-agents` (only)
**Date:** 2026-05-18
**Reporter:** Mark Oreta
**Status:** Phase 1 — seed written, awaiting gate
**Frontend:** false

---

## Problem

Two related ops-resilience holes, both surfaced in the 2026-05-04..05-18 audit window:

### Hole 1 — Operator skill outages have no documented fallback (2026-05-14 stall)

On 2026-05-14 the `/requeue-failed` operator skill was unavailable for over 9 hours. The fleet had failed jobs piling up; nobody on call knew the SQL-direct path to requeue them by hand. Morris doesn't ship an ops-runbook section that says "when this skill is down, run THIS query." Each operator skill (`/requeue-failed`, `/dispatch-recovery`, `/release-stale-claim`, etc.) is a one-off — there's no central registry, no documented fallback, no automatic surfacing of the manual path. The result: a 9-hour queue stall that was 100% preventable with a documented SQL command. This is incident class **REPLAY-3** in the epic acceptance.

### Hole 2 — `pr_number` is not durable from PR creation (STORY-919 band-aid)

STORY-919 shipped `dispatch_pr_link_backfill_sweeper` at `tech_dev_agents/ops_console/services/self_healing.py:1440` which periodically scans `dispatch_state_current.state='in_review'` rows whose `dispatch_jobs.pr_number IS NULL` and tries to recover the PR number via two paths: (1) GitHub branch search by `story-NNN/` branch prefix, (2) regex fallback `re.compile(r"\bpulls?[/\s#]+(\d{1,6})\b", re.IGNORECASE)` against the title/prompt (`self_healing.py:1138-1142`).

This is a **band-aid**. The actual bug is that the agent-side workflow can transition a job to `in_review` **without** stamping the `pr_number` on `dispatch_jobs` first — the agent calls `git_workflow.open_pr()` (`tech_dev_agents/git_workflow.py:220`) which returns the PR URL as a *string from stdout*, then sometimes that string never makes it back into the `transition` API call. So the backfill sweeper exists to clean up the resulting mess every 5 minutes.

The right fix: at the **service-layer** (`dispatch_v2_service.py`), the `transition` to `in_review` must **reject** the transition if `pr_number` is NULL on the job row. This makes `pr_number` durable from the start. The regex backfill can then stay for backward compatibility with in-flight jobs that were dispatched before this gate landed — but **new** dispatches never need it.

## Goal

1. Build `tech_dev_agents/ops_console/ops_skill_fallback_registry.py` — every operator skill registers a documented SQL-direct fallback path. When the skill is unavailable (or the operator types `/requeue-failed --fallback`), the registry produces a copy-pasteable SQL command + a runbook URL within 60 seconds.
2. At the merge gate / `transition` service layer: assert that `dispatch_jobs.pr_number IS NOT NULL` before allowing a `in_review` transition for **new** dispatches (those enqueued after this gate is live, identified by a feature flag or by `dispatch_jobs.created_at > <rollout_timestamp>`). Existing in-flight jobs continue to be repaired by the `pr_link_backfill_sweeper` (no breaking change).

## Scope

**In scope:**

- New module `tech_dev_agents/ops_console/ops_skill_fallback_registry.py` with:
  - `register_fallback(skill_name: str, sql_template: str, runbook_url: str, description: str)` — module-level decorator/registration.
  - `get_fallback(skill_name: str) -> Fallback | None` — lookup.
  - `render_fallback(skill_name: str, **params) -> str` — produces the operator-facing block: copy-paste SQL + runbook link + parameter explanation.
  - Initial registrations for the 5 highest-leverage operator skills: `/requeue-failed`, `/dispatch-recovery`, `/release-stale-claim`, `/dead-letter-purge`, `/force-claim`.
- New API endpoint `GET /api/ops/fallback/{skill_name}` — returns the rendered fallback block (operator-friendly, JSON or text via `Accept` header).
- Service-layer `pr_number` assertion in `tech_dev_agents/ops_console/services/dispatch_v2_service.py` `transition()` method:
  - On `in_review` transition, if `dispatch_jobs.pr_number IS NULL` AND the job was enqueued after `OPS_PR_NUMBER_ENFORCEMENT_ROLLOUT` (env var, ISO-8601 timestamp), raise `HTTPException(422, detail="in_review requires pr_number — backfill via /api/dispatch/v2/{job_id}/set-pr-number")`.
  - The new helper endpoint `POST /api/dispatch/v2/{job_id}/set-pr-number` lets the agent stamp the PR number atomically before the transition (or the agent can include it in the `transition` payload — both paths supported).
- Keep `dispatch_pr_link_backfill_sweeper` running for in-flight jobs. Add a log line on every backfill: `pr_link_backfill: legacy_dispatch story_id=...` so we can watch the legacy traffic decay to zero over the next 30 days.
- Replay test for the 2026-05-14 stall: simulate `/requeue-failed` skill missing, assert `GET /api/ops/fallback/requeue-failed` returns a runnable SQL command within 60s.
- Replay test for STORY-919: a new dispatch attempting `in_review` without `pr_number` is rejected with 422.

**Out of scope:**

- Deleting `dispatch_pr_link_backfill_sweeper` or its regex fallback — kept for backward-compat, decommissioned in a follow-up after 30 days of zero hits.
- Writing the actual operator runbook pages (just the URLs they point at — the runbook content is owned by the Knowledgebase work-stream STORY-1015 / STORY-1016).
- Changing the `/requeue-failed` skill itself (skill lives in `deployment/vm/skills/morris/`; this story only adds the *fallback path* that runs when the skill is unavailable).
- Modifying `dispatch_jobs` schema. `pr_number` is already a column (see `self_healing.py:1037`); we just enforce it.

**Files to modify:**

- `/mnt/c/Projects/tech-dev-agents/tech_dev_agents/ops_console/ops_skill_fallback_registry.py` — NEW.
- `/mnt/c/Projects/tech-dev-agents/tech_dev_agents/ops_console/services/dispatch_v2_service.py` — extend `transition()` with the `pr_number` assertion.
- `/mnt/c/Projects/tech-dev-agents/tech_dev_agents/ops_console/routes/dispatch_v2.py` — add `POST /{job_id}/set-pr-number` route handler.
- `/mnt/c/Projects/tech-dev-agents/tech_dev_agents/ops_console/routes/__init__.py` (or `main.py`) — register new `/api/ops/fallback/{skill_name}` route.
- `/mnt/c/Projects/tech-dev-agents/tech_dev_agents/ops_console/main.py` — mount the new ops-fallback router.
- `/mnt/c/Projects/tech-dev-agents/tests/ops_console/test_ops_fallback_registry.py` — NEW.
- `/mnt/c/Projects/tech-dev-agents/tests/ops_console/test_dispatch_v2_pr_number_gate.py` — NEW.
- `/mnt/c/Projects/tech-dev-agents/tests/epic_1000/test_incident_replays.py` — extend with REPLAY-3 (skill-down) and a STORY-919-class replay.

## Files to NOT modify

- `tech_dev_agents/ops_console/services/self_healing.py` — the existing PR-link backfill sweeper stays exactly as-is for backward-compat. Adding a log-line distinguishing legacy traffic is one tiny edit; do NOT touch the regex (`self_healing.py:1142`) or the backfill loop logic.
- `tech_dev_agents/git_workflow.py` — out of scope (PR creation logic itself is fine; the bug was on the *return path*).
- Any `deployment/vm/skills/morris/` skill file — Morris-side changes belong to Work-Stream B.
- v1 `routes/dispatch.py` (frozen).
- `dispatch_jobs` schema or migrations (no DDL).
- The STORY-898 id-reuse gate at `routes/dispatch_v2.py:607`.

## Success criteria

- **SC-1 — Registry returns runnable SQL within 60s:** `curl -s -H "X-API-Key: $KEY" https://tech-dev-agents.gorillacommerce.ai/api/ops/fallback/requeue-failed` returns a JSON document with `sql` (runnable, parameterized), `runbook_url`, `description`, response time < 60s. Verified by `tests/ops_console/test_ops_fallback_registry.py::test_requeue_failed_fallback_renders`.
- **SC-2 — All 5 operator skills registered:** `python -c "from tech_dev_agents.ops_console.ops_skill_fallback_registry import REGISTRY; print(sorted(REGISTRY.keys()))"` outputs `['dead-letter-purge', 'dispatch-recovery', 'force-claim', 'release-stale-claim', 'requeue-failed']`.
- **SC-3 — New dispatches blocked at `in_review` without `pr_number`:** A dispatch enqueued AFTER `OPS_PR_NUMBER_ENFORCEMENT_ROLLOUT` that attempts `transition → in_review` without `pr_number` returns HTTP 422 with `detail="in_review requires pr_number..."`. Verified by `tests/ops_console/test_dispatch_v2_pr_number_gate.py::test_new_dispatch_blocked_without_pr_number`.
- **SC-4 — Legacy in-flight dispatches still backfill:** A dispatch enqueued BEFORE the rollout that hits `in_review` without `pr_number` is **not** rejected; the regex backfill sweeper still picks it up. Verified by `test_legacy_dispatch_uses_backfill_sweeper` + log scan for `pr_link_backfill: legacy_dispatch`.
- **SC-5 — Set-pr-number endpoint atomic:** `POST /api/dispatch/v2/{job_id}/set-pr-number {"pr_number": 123}` updates `dispatch_jobs.pr_number` and is idempotent (second call with same value returns 200; call with conflicting value returns 409).
- **SC-6 — REPLAY-3 GREEN:** `pytest tests/epic_1000/test_incident_replays.py::test_replay_3_requeue_failed_fallback_unblocks -v` passes — simulating skill unavailable, the fallback registry returns runnable SQL.
- **SC-7 — No regression on `pr_link_backfill_sweeper`:** Full `pytest tests/ops_console/ -k self_healing or backfill` passes with no failures.

## Verification plan

| SC | Command | Expected output |
|---|---|---|
| SC-1 | `time curl -s -H "X-API-Key: $KEY" https://tech-dev-agents.gorillacommerce.ai/api/ops/fallback/requeue-failed \| jq .sql` | non-empty SQL string; elapsed < 1s; jq prints a `UPDATE dispatch_state_current SET state='enqueued' WHERE ...` template |
| SC-2 | `python -c "from tech_dev_agents.ops_console.ops_skill_fallback_registry import REGISTRY; import json; print(json.dumps(sorted(REGISTRY.keys())))"` | `["dead-letter-purge","dispatch-recovery","force-claim","release-stale-claim","requeue-failed"]` |
| SC-3 | (with `OPS_PR_NUMBER_ENFORCEMENT_ROLLOUT=2026-05-18T00:00:00Z`) `curl -X POST .../api/dispatch/v2/transition -d '{"job_id":"<new_job>","to_state":"in_review"}'` | `422`; body includes `"in_review requires pr_number"` |
| SC-4 | (use a `dispatch_jobs.created_at < 2026-05-18`) same call as SC-3 | `200` (transition allowed); within 5 min, journal shows `pr_link_backfill: legacy_dispatch story_id=...` |
| SC-5 | `curl -X POST .../api/dispatch/v2/<job>/set-pr-number -d '{"pr_number":123}'` x2 → x1 with `{"pr_number":124}` | first `200`, second `200` (idempotent), third `409` |
| SC-6 | `pytest tests/epic_1000/test_incident_replays.py::test_replay_3_requeue_failed_fallback_unblocks -v` | `1 passed` |
| SC-7 | `pytest tests/ops_console/ -k "self_healing or backfill" -v` | `0 failed` |

## Boundaries

| Always Do | Ask First | Never Do |
|---|---|---|
| Surface fallback SQL via the registry API — operators copy-paste, never write from memory | Decommission `dispatch_pr_link_backfill_sweeper` — leave it running until legacy traffic is provably zero for 30 days | Delete the STORY-919 regex (`self_healing.py:1142`) in this story — it's load-bearing for in-flight jobs |
| Gate the `pr_number` assertion behind `OPS_PR_NUMBER_ENFORCEMENT_ROLLOUT` so legacy jobs aren't broken | Add a new operator skill to the registry not listed in the SC-2 set of 5 | Move the assertion to the v1 `routes/dispatch.py` — v1 is frozen |
| Log `pr_link_backfill: legacy_dispatch story_id=...` for every legacy backfill so we can watch decay | Lower the rollout timestamp to backdate enforcement to pre-existing jobs | Force `transition → in_review` to succeed when `pr_number` is missing on a NEW dispatch — defeats the gate |
| Register fallback runbook URLs that point at real pages (placeholder URLs OK if the page lives in STORY-1015/1016 work) | Add the registry to the v1 API surface | Allow `set-pr-number` to overwrite a non-null `pr_number` silently — must 409 on conflict |

## Done looks like

```
$ pytest tests/ops_console/test_ops_fallback_registry.py tests/ops_console/test_dispatch_v2_pr_number_gate.py tests/epic_1000/test_incident_replays.py -v -k "fallback or pr_number_gate or replay_3"
test_requeue_failed_fallback_renders ................................. PASSED
test_all_five_skills_registered ...................................... PASSED
test_render_fallback_unknown_skill_returns_404 ....................... PASSED
test_new_dispatch_blocked_without_pr_number .......................... PASSED
test_new_dispatch_allowed_with_pr_number ............................. PASSED
test_legacy_dispatch_uses_backfill_sweeper ........................... PASSED
test_set_pr_number_idempotent ........................................ PASSED
test_set_pr_number_conflict_returns_409 .............................. PASSED
test_replay_3_requeue_failed_fallback_unblocks ....................... PASSED

9 passed in 3.1s

$ curl -s -H "X-API-Key: $KEY" https://tech-dev-agents.gorillacommerce.ai/api/ops/fallback/requeue-failed | jq -r .sql
UPDATE dispatch_state_current
SET state = 'enqueued', updated_at = NOW()
WHERE state = 'failed'
  AND job_id IN (SELECT job_id FROM dispatch_jobs WHERE repo = $1 AND created_at > NOW() - INTERVAL '24 hours');

$ tail -50 /var/log/ops_console/app.log | grep "pr_link_backfill: legacy_dispatch" | wc -l
3   # 3 legacy jobs handled, no new dispatches needed the sweeper
```

## Escalation contract

Standard 60s directive guard. Escalate to Mark via `needs_info` when:

1. The 5 operator skills don't cleanly map to SQL-direct fallbacks (e.g. `/dispatch-recovery` requires multi-step decisions, not a single query) — escalate to discuss whether the runbook should be a procedure rather than a SQL block.
2. The `OPS_PR_NUMBER_ENFORCEMENT_ROLLOUT` cutover timestamp is contested — escalate before deploying.
3. Any pre-existing job in production currently has `state='in_review' AND pr_number IS NULL` — escalate before the gate goes live; we need a clean slate.
4. The `set-pr-number` endpoint's conflict semantics (409 vs allow overwrite) is contested for any concrete case — escalate before merging.


## Test Criteria

| Test | Validates |
|------|-----------|
| RED tests from Phase 7 (see `## Verification plan`) | Every SC has a literal test or shell command |
| Full pytest suite GREEN, zero regressions | No unrelated breakage |
| Incident-replay tests (where applicable) | The 2026-05-04..05-18 failure classes are catchable by the new gate |

See `## Success criteria` and `## Verification plan` above for SC-keyed predicates.


## Validation

After merge:

1. **Tests:** all RED tests turned GREEN; `pytest <story test paths>` exits 0.
2. **No regressions:** full suite passes; CI green on the PR.
3. **Per-SC verification:** every command in `## Verification plan` runs and produces the expected output.
4. **Tracking updated:** `.project` and `backlog.md` reflect Phase 8 completion; Monday.com task updated.

## Phase path

Medium → `1 → 6 → 7 → 8 → Done`.

- Phase 1: this seed.
- Phase 6: `feature-spec.md` covers the registry data model, the 5 initial entries (SQL + runbook URL + params per skill), the `transition` service-layer gate, the rollout flag semantics.
- Phase 7: RED tests in `tests/ops_console/test_ops_fallback_registry.py`, `tests/ops_console/test_dispatch_v2_pr_number_gate.py`, and the REPLAY-3 scenario.
- Phase 8: implementation, deploy via `push-code.sh all` (NEVER skip restart — see CLAUDE.md), verify smoke tests pass, push, PR.
- Done: PR merged, all 9 listed tests GREEN, deployed to dev fleet, legacy backfill log line live.

## Dependencies & sequencing notes

- **No hard dependency on STORY-1009** — orthogonal. The `pr_number` gate lives at the service-layer `transition()` path; the validation gate from STORY-1009 lives at the route-layer `enqueue()` path. Either ships first.
- **Coordination with STORY-919's existing sweeper:** the `pr_link_backfill_sweeper` in `tech_dev_agents/ops_console/services/self_healing.py:1440` stays running. Only edit needed: a new log line distinguishing legacy traffic. Decommission of the regex (`self_healing.py:1142`) happens in a future cleanup story after 30 days of zero legacy hits.
- **Rollout flag:** `OPS_PR_NUMBER_ENFORCEMENT_ROLLOUT` defaults to `2099-01-01T00:00:00Z` (effectively disabled). The Phase 8 deploy ships the code; a follow-up env-var change activates the gate when ops is ready.

## Phase 6 — design deliverable breakdown

`features/story-1010-queue-fallback-prlink/feature-spec.md` will include:

1. **Registry data model** — `Fallback` dataclass (`skill_name`, `sql_template`, `runbook_url`, `description`, `required_params: list[str]`). Registration mechanism (module-level dict populated at import time, or decorator pattern — pick one in Phase 6).
2. **The 5 initial entries** — for each of `/requeue-failed`, `/dispatch-recovery`, `/release-stale-claim`, `/dead-letter-purge`, `/force-claim`: the actual SQL template (parameterized), the runbook URL (placeholder OK), the parameter list, the description sentence.
3. **`transition()` gate semantics** — exact decision tree: if `to_state != 'in_review'` → pass through; else if `pr_number IS NOT NULL` on the job → pass; else if `dispatch_jobs.created_at > OPS_PR_NUMBER_ENFORCEMENT_ROLLOUT` → 422; else allow (legacy).
4. **`set-pr-number` endpoint contract** — payload, response, conflict rules.
5. **Observability** — Prometheus counters: `ops_fallback_render_total{skill}`, `pr_number_gate_blocked_total`, `pr_link_backfill_legacy_dispatch_total`. Loki log lines to watch.
6. **Test fixtures** — one "new" dispatch (`created_at = now()`), one "legacy" dispatch (`created_at < rollout`) — both used across the gate + sweeper tests.

## Cross-references — evidence cited in this seed

- STORY-919 band-aid (kept running for backward-compat):
  - `/mnt/c/Projects/tech-dev-agents/tech_dev_agents/ops_console/services/self_healing.py` — regex `r"\bpulls?[/\s#]+(\d{1,6})\b"` defined at line 1142; `_extract_pr_number_fallback()` at line 1231; `_pr_link_backfill_tick()` at line 1290; `dispatch_pr_link_backfill_sweeper()` at line 1440. Comment at line 1138 explicitly labels the regex as "STORY-919 fallback".
  - The SQL query the sweeper runs (line 1023): joins `dispatch_state_current` to `dispatch_jobs` filtering `state='in_review' AND pr_number IS NOT NULL` — exactly the inverse of the population we want to drain.
- PR creation site (where `pr_number` *should* be stamped but currently isn't, deterministically):
  - `/mnt/c/Projects/tech-dev-agents/tech_dev_agents/git_workflow.py` — `open_pr()` at line 220-242 returns the PR URL as a string from `gh pr create` stdout. The return value is the only place `pr_number` could be recovered without a backfill — and the caller chain doesn't always forward it.
- Service-layer transition target (where the new gate lives):
  - `/mnt/c/Projects/tech-dev-agents/tech_dev_agents/ops_console/services/dispatch_v2_service.py` — 1208 lines; `transition()` is one of the public methods; the gate hooks into the same code path that drives the existing 422 raises at `routes/dispatch_v2.py:839, 939, 943, 1023, 1055`.
- Epic seed: `/mnt/c/Projects/tech-dev-agents/features/story-1000-cross-repo-canon-alignment-epic/seed.md` — REPLAY-3 acceptance (line 99).

## Post-deploy smoke check (Phase 8 exit gate)

After `push-code.sh all` is GREEN and the ops-console restart is verified, run these three commands. All three must succeed before the PR is marked ready-for-review:

```
# 1. Fallback registry route alive
curl -s -H "X-API-Key: $KEY" https://tech-dev-agents.gorillacommerce.ai/api/ops/fallback/requeue-failed | jq -r '.sql' | head -1
# expected: starts with "UPDATE dispatch_state_current"

# 2. All five skills registered
curl -s -H "X-API-Key: $KEY" https://tech-dev-agents.gorillacommerce.ai/api/ops/fallback/ | jq -r '.skills | sort | join(",")'
# expected: dead-letter-purge,dispatch-recovery,force-claim,release-stale-claim,requeue-failed

# 3. set-pr-number endpoint reachable (idempotency check)
JOB_ID=$(curl -s -H "X-API-Key: $KEY" https://tech-dev-agents.gorillacommerce.ai/api/dispatch/v2/queue | jq -r '.jobs[0].job_id')
curl -s -X POST -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  "https://tech-dev-agents.gorillacommerce.ai/api/dispatch/v2/$JOB_ID/set-pr-number" \
  -d '{"pr_number":999999}' -w '%{http_code}\n' | tail -1
# expected: 200 (first call), 200 again (idempotent), 409 (with different pr_number)
```

If any smoke check fails: do NOT mark the PR ready; investigate. The legacy-decay log line (`pr_link_backfill: legacy_dispatch`) should appear in Loki within 5 minutes once a legacy job hits `in_review` — grep Loki for confirmation after the gate is rolled live.
