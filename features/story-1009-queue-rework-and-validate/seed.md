# STORY-1009 — `validate_dispatch_seed()` API gate + POST `/api/dispatch/v2/rework` endpoint

**Story ID:** STORY-1009
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

On 2026-05-12 we ran **three hand-written dispatch surgeries in a single day** because the queue had no way to refuse incomplete or one-off work:

| Script | Purpose | Why it existed |
|---|---|---|
| `dispatch_795.py` | Re-enqueue STORY-795 (fleet-vigilance PR #244 rework) | The seed was missing Do-Not-Do, the failing test `assert 48 in url or True` was never caught, and the rework had to be hand-rolled because the API had no `rework` semantics — only `enqueue` |
| `dispatch_803.py` | Re-enqueue STORY-803 (PR #256 contract test rename) | Same shape — push 3 corrective sub-tasks onto a PR; payload assembled by hand and POSTed to the v1 endpoint with `rework_of="STORY-802"` |
| `dispatch_804.py` | Re-enqueue STORY-804 (PR #227 partial-gate doc) | Same shape again — `rework_of="STORY-738"`, prompt hand-written, POSTed to v1 |

All three scripts are still in the repo root (`/mnt/c/Projects/tech-dev-agents/dispatch_795.py`, `dispatch_803.py`, `dispatch_804.py`) — load-bearing technical debt. They POST to **`/api/dispatch`** (v1), not the v2 enqueue endpoint, because the dispatch skill could not express "this is a rework of an existing job — drop these fixes onto the existing PR." They each duplicate ~30 lines of `.env` parsing, payload assembly, urllib boilerplate, and error handling.

The deeper failure: the queue accepts whatever payload is shoved at it. The v2 enqueue endpoint at `tech_dev_agents/ops_console/routes/dispatch_v2.py:560` runs an idempotency check (line 587) and the STORY-898 id-reuse gate (line 607) — but **nothing** validates that the seed Mark or Morris is dispatching is actually complete. No check for `do_not_do`, no check for `verification_plan`, no check for `red_test_paths`. The 3 manual surgeries each shipped a `prompt` blob with whatever instructions the human happened to type into the script.

This was the root cause flagged in the 2026-05-12 retrospective and is the exact incident class **REPLAY-2** in the epic acceptance tests.

## Goal

Make the queue **rework-preventive** and **rework-aware**:

1. Before any v2 enqueue persists a row, run `validate_dispatch_seed()` and return HTTP 422 if any required section is missing.
2. Replace the three hand-rolled `dispatch_NNN.py` scripts with a single **`POST /api/dispatch/v2/rework`** endpoint that knows the rework lineage shape natively. The dispatch skill and Morris call this endpoint instead of writing fresh Python every time something goes sideways.
3. Reuse the validation logic from STORY-1006's Morris `pre-dispatch-validate` skill — **one function, two callsites** (the skill imports the same `tech_dev_agents.morris.pre_dispatch` module that the API gate calls). No duplicate rule set.

## Scope

**In scope:**

- New module `tech_dev_agents/morris/pre_dispatch/` (created by STORY-1006; this story imports from it). If STORY-1006 has not landed yet, this story ships a minimal `validate_seed.py` stub in that module and STORY-1006 expands it.
- New function `validate_dispatch_seed(payload: dict) -> ValidationResult` — returns `(ok: bool, missing: list[str], errors: list[str])`. Required sections checked: `do_not_do`, `verification_plan`, `red_test_paths`, `acceptance_criteria`.
- Wire `validate_dispatch_seed()` into `tech_dev_agents/ops_console/routes/dispatch_v2.py` `enqueue()` (line 560+) and into the new `rework` endpoint. On `ok=False`, raise `HTTPException(422, detail={"error":"seed_validation","missing":[...]})`.
- New endpoint `POST /api/dispatch/v2/rework` in `dispatch_v2.py`:
  - Body: `{original_story_id: str, story_id: str, repo: str, scope: str, failure_list: list[str], fresh_implementation: bool, reason: str, enqueued_by: str}`
  - Behavior: looks up `original_story_id`'s most recent terminal job, generates the rework prompt from a template, sets `rework_of=original_story_id`, runs `validate_dispatch_seed()` on the assembled payload, persists via the existing v2 enqueue transaction (same `dispatch_jobs` INSERT path).
  - The prompt template lives in `tech_dev_agents/ops_console/templates/rework_prompt.md.j2` and folds in: original story summary, failure list (bulletized), `fresh_implementation` flag → "Do NOT reuse the old branch" line, and a mandatory "COMMIT AND PUSH" footer that mirrors the working text in `dispatch_795.py:21`.
- Replay tests for the 3 manual-surgery scenarios: replay STORY-795-rework, STORY-803-rework (from STORY-802), STORY-804-rework (from STORY-738). Each replay must produce a payload that passes `validate_dispatch_seed()` and yields a `dispatch_jobs` row identical in shape to what the old scripts produced.
- Delete `dispatch_795.py`, `dispatch_803.py`, `dispatch_804.py` at the end of Phase 8 (proof the new endpoint subsumes them).

**Out of scope:**

- Modifying the v1 `/api/dispatch` endpoint (`routes/dispatch.py`) — v1 is frozen, all new work goes through v2. We do NOT add `validate_dispatch_seed()` to v1.
- Changing the `dispatch_jobs` schema or `dispatch_v2_events` schema.
- Changing the STORY-898 id-reuse gate (line 607) — orthogonal.
- Touching Morris's `pre-dispatch-validate` skill itself — STORY-1006 owns that. We only import the function.
- Anything in `gc-data-v2` or `sdlc-framework` (those are other work-streams).
- Generating the rework PR — the agent that picks up the rework job creates the PR; this endpoint only enqueues.

## Success criteria

- **SC-1 — Validation gate live on enqueue:** `POST /api/dispatch/v2/enqueue` with a payload missing `do_not_do` returns HTTP 422 with `{"error":"seed_validation","missing":["do_not_do"]}`. Verified by `tests/ops_console/test_dispatch_v2_validate.py::test_enqueue_rejects_missing_do_not_do`.
- **SC-2 — Validation gate live on rework:** `POST /api/dispatch/v2/rework` with `failure_list=[]` (empty list — operator forgot to enumerate the failures) returns HTTP 422 with `missing=["failure_list"]`. Verified by `test_rework_rejects_empty_failure_list`.
- **SC-3 — Single source of truth:** `validate_dispatch_seed()` is defined exactly once. `grep -rn "def validate_dispatch_seed" tech_dev_agents/` returns exactly one match (in `tech_dev_agents/morris/pre_dispatch/validate_seed.py`).
- **SC-4 — Rework endpoint produces correct job row:** `POST /api/dispatch/v2/rework` with the STORY-795 replay payload creates a `dispatch_jobs` row with `rework_of='STORY-795'`, `enqueued_by='morris'`, `prompt` matching the template (verified by snapshot test).
- **SC-5 — All 3 manual scripts deletable:** `dispatch_795.py`, `dispatch_803.py`, `dispatch_804.py` are removed from the repo root. `git ls-files | grep ^dispatch_ | wc -l` returns 0 (excluding `dispatch_history.tmp.py` and `dispatch_pr298.py` which are not in scope).
- **SC-6 — REPLAY-2 scenario tests GREEN:** `pytest tests/epic_1000/test_incident_replays.py::test_replay_2a_manual_surgery_738_blocked -v` passes (along with 2b and 2c). The bad seeds these tests submit must be rejected with 422 listing `do_not_do` and `verification_plan` as missing.
- **SC-7 — No regression on existing enqueues:** Full `pytest tests/ops_console/ -k dispatch_v2` suite passes with no failures.

## Files to modify

- `/mnt/c/Projects/tech-dev-agents/tech_dev_agents/ops_console/routes/dispatch_v2.py` — add `validate_dispatch_seed()` call inside `enqueue()` before the idempotency check; add new `POST /rework` route handler.
- `/mnt/c/Projects/tech-dev-agents/tech_dev_agents/morris/pre_dispatch/__init__.py` — create or extend (coordinate with STORY-1006).
- `/mnt/c/Projects/tech-dev-agents/tech_dev_agents/morris/pre_dispatch/validate_seed.py` — `validate_dispatch_seed()` lives here.
- `/mnt/c/Projects/tech-dev-agents/tech_dev_agents/ops_console/templates/rework_prompt.md.j2` — NEW file (rework prompt template).
- `/mnt/c/Projects/tech-dev-agents/tests/ops_console/test_dispatch_v2_validate.py` — NEW test file (Phase 7).
- `/mnt/c/Projects/tech-dev-agents/tests/epic_1000/test_incident_replays.py` — extend with 2a/2b/2c replays.
- `/mnt/c/Projects/tech-dev-agents/dispatch_795.py` — DELETE in Phase 8.
- `/mnt/c/Projects/tech-dev-agents/dispatch_803.py` — DELETE in Phase 8.
- `/mnt/c/Projects/tech-dev-agents/dispatch_804.py` — DELETE in Phase 8.

## Files to NOT modify

- `tech_dev_agents/ops_console/routes/dispatch.py` (v1 — frozen).
- `tech_dev_agents/ops_console/services/dispatch_v2_service.py` (the service layer is not the right enforcement boundary; the API gate runs first).
- `tech_dev_agents/ops_console/models/dispatch_v2.py` (no schema changes).
- The STORY-898 id-reuse gate at `dispatch_v2.py:607` (do not touch — orthogonal feature).
- `dispatch_pr298.py` or `dispatch_history.tmp.py` (out of scope; separate cleanup story).
- `deployment/vm/skills/morris/pre-dispatch-validate/` — owned by STORY-1006.

## Verification plan

| SC | Command | Expected output |
|---|---|---|
| SC-1 | `curl -s -X POST https://tech-dev-agents.gorillacommerce.ai/api/dispatch/v2/enqueue -H "X-API-Key: $KEY" -H "Content-Type: application/json" -d '{"story_id":"TEST-001","repo":"tech-dev-agents","scope":"small","prompt":"do thing"}' -o /dev/stderr -w '%{http_code}\n'` | `422` + body `{"error":"seed_validation","missing":["do_not_do","verification_plan","red_test_paths"]}` |
| SC-2 | `curl -s -X POST .../api/dispatch/v2/rework -H "X-API-Key: $KEY" -d '{"original_story_id":"STORY-795","story_id":"STORY-795-rework","repo":"tech-dev-agents","scope":"small","failure_list":[],"reason":"test","fresh_implementation":true,"enqueued_by":"mark"}' -w '%{http_code}\n'` | `422` + body lists `failure_list` in `missing` |
| SC-3 | `grep -rn "def validate_dispatch_seed" /mnt/c/Projects/tech-dev-agents/tech_dev_agents/ \| wc -l` | `1` |
| SC-4 | `pytest tests/ops_console/test_dispatch_v2_validate.py::test_rework_creates_job_with_correct_lineage -v` | `1 passed` |
| SC-5 | `cd /mnt/c/Projects/tech-dev-agents && ls dispatch_795.py dispatch_803.py dispatch_804.py 2>&1 \| grep -c "No such file"` | `3` |
| SC-6 | `pytest tests/epic_1000/test_incident_replays.py -v -k "manual_surgery"` | `3 passed` (2a, 2b, 2c) |
| SC-7 | `pytest tests/ops_console/ -k dispatch_v2 -v` | `0 failed`; all previously passing tests still pass |

## Boundaries

| Always Do | Ask First | Never Do |
|---|---|---|
| Reuse `validate_dispatch_seed()` from `tech_dev_agents.morris.pre_dispatch` — one definition, two callers (API gate + skill) | Add a new required seed section beyond `do_not_do` / `verification_plan` / `red_test_paths` / `acceptance_criteria` (changes the validation contract — coordinate with STORY-1006) | Add the validation gate to v1 `routes/dispatch.py` — v1 is frozen; new gates only on v2 |
| Generate the rework prompt from `templates/rework_prompt.md.j2` so the format is reviewable in source control | Add a new column to `dispatch_jobs` (schema changes are epic-coordination work, not story-local) | Allow `/v2/rework` to bypass validation under any flag — the entire point of the endpoint is the gate |
| Delete `dispatch_795.py`, `dispatch_803.py`, `dispatch_804.py` once their replay tests are GREEN | Remove `dispatch_pr298.py` or `dispatch_history.tmp.py` (different shape; separate cleanup) | Modify the existing STORY-898 id-reuse gate at line 607 — orthogonal feature, don't disturb |
| Run the full `tests/ops_console/test_dispatch_v2*` suite before pushing | Bypass the regression suite even for a "small" change | Hand-write another `dispatch_NNN.py` script while this story is in flight |

## Done looks like

```
$ pytest tests/ops_console/test_dispatch_v2_validate.py tests/epic_1000/test_incident_replays.py -v -k "validate or manual_surgery"
test_enqueue_rejects_missing_do_not_do .............................. PASSED
test_enqueue_rejects_missing_verification_plan ...................... PASSED
test_enqueue_rejects_missing_red_test_paths ......................... PASSED
test_enqueue_passes_with_complete_seed .............................. PASSED
test_rework_rejects_empty_failure_list .............................. PASSED
test_rework_creates_job_with_correct_lineage ........................ PASSED
test_rework_idempotent_on_duplicate_story_id ........................ PASSED
test_replay_2a_manual_surgery_738_blocked ........................... PASSED
test_replay_2b_manual_surgery_766_blocked ........................... PASSED
test_replay_2c_manual_surgery_802_blocked ........................... PASSED

10 passed in 4.2s

$ grep -rn "def validate_dispatch_seed" tech_dev_agents/
tech_dev_agents/morris/pre_dispatch/validate_seed.py:14:def validate_dispatch_seed(payload: dict) -> ValidationResult:

$ ls dispatch_795.py dispatch_803.py dispatch_804.py 2>&1
ls: cannot access 'dispatch_795.py': No such file or directory
ls: cannot access 'dispatch_803.py': No such file or directory
ls: cannot access 'dispatch_804.py': No such file or directory

$ gh pr view --json title,state,checks
{"title":"STORY-1009: validate_dispatch_seed gate + /api/dispatch/v2/rework endpoint",
 "state":"OPEN","checks":[{"conclusion":"SUCCESS"}, ...]}
```

## Escalation contract

Standard 60s directive guard. Escalate to Mark via `needs_info` when:

1. STORY-1006's `pre_dispatch/validate_seed.py` shape is undecided when this story starts — block on the contract or stub it locally and reconcile in a follow-up. If stubbing, leave a `TODO(STORY-1006)` comment naming the canonical home.
2. The rework prompt template would need a section that the 3 historical scripts don't have — escalate to confirm we're not over-specifying.
3. Replaying STORY-795/803/804 produces a `dispatch_jobs` row that differs in any non-trivial field from the original — escalate before adapting the template; the original is the contract.
4. v2 enqueue traffic in prod is using fields we don't yet validate — escalate before extending the missing-fields list.


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
- Phase 6: `feature-spec.md` covers the validator contract, the rework template, the route signature, and the 3 replay scenarios.
- Phase 7: RED tests in `tests/ops_console/test_dispatch_v2_validate.py` and `tests/epic_1000/test_incident_replays.py` (the 3 manual-surgery replays).
- Phase 8: implementation, delete the 3 scripts, push, PR.
- Done: PR merged, all 10 listed tests GREEN, no `dispatch_NNN.py` scripts in repo root.

## Dependencies & sequencing notes

- **Depends on STORY-1006** (Morris `pre-dispatch-validate` skill) for the canonical `validate_dispatch_seed()` implementation. If STORY-1006 has not merged when this story enters Phase 8: ship a minimal validator stub in `tech_dev_agents/morris/pre_dispatch/validate_seed.py` covering exactly the four required sections (`do_not_do`, `verification_plan`, `red_test_paths`, `acceptance_criteria`). STORY-1006 then expands the rule set against the same module — no rewrite, no second copy.
- **No dependency on STORY-1011** (sdlc-framework versioning) — independent.
- **No dependency on STORY-1010** (fallback registry / PR-link gate) — orthogonal. Either can land first.

## Phase 6 — design deliverable breakdown

`features/story-1009-queue-rework-and-validate/feature-spec.md` will include:

1. **Validator contract** — function signature, `ValidationResult` dataclass, exact required-section list with example payloads (one passing, four failing — one per missing section).
2. **Rework endpoint contract** — request/response Pydantic models, status codes (200 normal, 409 on duplicate active job, 422 on validation failure), idempotency rules (re-POST with same `story_id` returns the existing job, like v2 `/enqueue`).
3. **Prompt template** — full Jinja2 source of `rework_prompt.md.j2` showing the variable interpolation: `{{ original_story_id }}`, `{{ failure_list | bulletize }}`, `{{ "Do NOT reuse the old branch." if fresh_implementation }}`, mandatory "COMMIT AND PUSH" footer.
4. **Replay matrix** — table mapping STORY-795 / STORY-803 / STORY-804 to the exact payload fields the new endpoint must accept to reproduce the original `dispatch_jobs` row.
5. **Migration plan** — order of operations to delete the 3 scripts: (a) replay tests GREEN, (b) push the new endpoint live, (c) one manual end-to-end smoke (Morris dispatches a rework via the new endpoint, agent picks it up, PR opens), (d) remove the scripts in a follow-up commit.

## Cross-references — evidence cited in this seed

- Manual surgery scripts (the load-bearing tech debt this story retires):
  - `/mnt/c/Projects/tech-dev-agents/dispatch_795.py` — STORY-795 rework dispatch; lines 9 (v1 endpoint URL), 21-26 (COMMIT/PUSH footer template), 29-37 (payload shape with `cross_story_reference=True`).
  - `/mnt/c/Projects/tech-dev-agents/dispatch_803.py` — STORY-803 rework of STORY-802; lines 17-26 (payload shape with `rework_of="STORY-802"`).
  - `/mnt/c/Projects/tech-dev-agents/dispatch_804.py` — STORY-804 rework of STORY-738; lines 19-28 (same shape with `rework_of="STORY-738"`).
- v2 enqueue route (where validation will be wired in):
  - `/mnt/c/Projects/tech-dev-agents/tech_dev_agents/ops_console/routes/dispatch_v2.py` — `enqueue()` at line 560; idempotency check at line 587; STORY-898 id-reuse gate at line 607; transaction block at line 640.
- Models referenced (no changes here, but Phase 6 reads them):
  - `/mnt/c/Projects/tech-dev-agents/tech_dev_agents/ops_console/models/dispatch_v2.py` — `EnqueueRequest`, `EnqueueResponse`.
- Epic seed: `/mnt/c/Projects/tech-dev-agents/features/story-1000-cross-repo-canon-alignment-epic/seed.md` — REPLAY-2 acceptance (lines 97-98).

## Post-deploy smoke check (Phase 8 exit gate)

After `push-code.sh` reports green and the ops-console restart is verified, run these three commands from a workstation. All three must succeed before the PR is marked ready-for-review:

```
# 1. Validator gate alive (bad seed -> 422)
curl -s -X POST -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  https://tech-dev-agents.gorillacommerce.ai/api/dispatch/v2/enqueue \
  -d '{"story_id":"SMOKE-1009","repo":"tech-dev-agents","scope":"small","prompt":"x"}' \
  -w '%{http_code}\n' | tail -1
# expected: 422

# 2. Rework endpoint reachable (well-formed payload -> 200 with job_id)
curl -s -X POST -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  https://tech-dev-agents.gorillacommerce.ai/api/dispatch/v2/rework \
  -d '{"original_story_id":"STORY-9999","story_id":"SMOKE-1009-rework","repo":"tech-dev-agents","scope":"small","failure_list":["one","two"],"reason":"smoke","fresh_implementation":true,"enqueued_by":"mark"}' \
  | jq '.job_id'
# expected: non-null uuid string

# 3. Old scripts gone from repo root
ls /mnt/c/Projects/tech-dev-agents/dispatch_795.py /mnt/c/Projects/tech-dev-agents/dispatch_803.py /mnt/c/Projects/tech-dev-agents/dispatch_804.py 2>&1 | grep -c "No such file"
# expected: 3
```

If any smoke check fails: do NOT mark the PR ready; investigate. The smoke checks assert the same SCs the unit tests do, but against the deployed environment — they catch deploy-time-only regressions (env var unset, route not registered, etc).
