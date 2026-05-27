# STORY-1006 — Morris `pre-dispatch-validate` skill + BLOCKING API-layer seed gate

**Story ID:** STORY-1006
**Scope:** Medium
**Epic:** STORY-1000 (Cross-Repo Canon Alignment)
**Work-Stream:** B — Morris canon enforcement
**Priority:** **P0 — ship first** (parallel with STORY-1005). **Highest-leverage story in the epic.**
**Repo touched:** `tech-dev-agents`
  - `deployment/vm/skills/morris/pre-dispatch-validate/SKILL.md` (NEW)
  - `tech_dev_agents/morris/pre_dispatch/` (NEW Python package)
  - `tech_dev_agents/ops_console/routes/dispatch.py` (MODIFY — v1 enqueue path)
  - `tech_dev_agents/ops_console/routes/dispatch_v2.py` (MODIFY — v2 enqueue path)
**Date:** 2026-05-18
**Reporter:** Mark Oreta
**Status:** Phase 1 — seed in progress
**Frontend:** false

---

## Problem

**The audit window's most expensive class of incident was incomplete seeds reaching agents.**

On 2026-05-12, three stories (STORY-738, STORY-766, STORY-802) shipped PRs that immediately required hand-written rework dispatches (`dispatch_795.py`, `dispatch_803.py`, `dispatch_804.py`) because the original seeds did not specify enough for agents to get it right the first time. Concrete evidence:

- `dispatch_795.py` (rework of STORY-766, PR #244) — contains six numbered fixes that should have been in the original seed:
  - `--no-merges` filter (test design gap: no verification that fixture commits would match)
  - DM delivery integration (Files-to-modify gap: integration dependency unnamed)
  - Test asserting `assert 48 in url or True` — **the "or True" anti-pattern that ships green but tests nothing**
  - First-run state file fallback (edge-case gap in Boundaries)
  - SKILL.md ordering (Files-to-modify too vague)
  - API-key guard (Verification-plan gap: no negative-path test)
- `dispatch_803.py` (rework of STORY-802) — three issues, all surfaceable by a seed gate:
  - Contract test asserts opposite of what was implemented (Test-design gap)
  - `LokiClient.query_cost_alerts()` referenced but doesn't exist (Verification-plan gap)
  - PR body missing required content (PR-template gap)
- `dispatch_804.py` (rework of STORY-738, PR #227) — partial-gate behavior was undocumented in the feature-spec; this is a Do-Not-Do gap.

**There is no enforcement layer.** `POST /api/dispatch/v2/enqueue` (see `tech_dev_agents/ops_console/routes/dispatch_v2.py:560-666`) accepts any payload with `repo`, `story_id`, `prompt`, etc. It does not check whether a `seed.md` exists on the story branch, let alone whether the seed contains the sections that prevent the incidents above. The audit's STORY-871 v2 mirror desync (epic seed line 22) is the same class — no queue-side gate.

Morris must own this. Morris is the manager. The queue is the place where bad work is cheap to refuse and expensive to undo.

## Goal

A **BLOCKING** validator that runs inside the dispatch API and refuses any enqueue whose seed is missing structural completeness. Returns HTTP 422 with a structured error naming every missing section. Surfaced two ways:

1. **API layer** (the true gate) — `tech_dev_agents/ops_console/routes/dispatch.py` (v1) and `dispatch_v2.py` (v2) call `validate_dispatch_seed(payload)` before any DB write. **No enqueue path may bypass it.**
2. **Morris skill** at `deployment/vm/skills/morris/pre-dispatch-validate/SKILL.md` — runs locally on the seed file before Morris ever crafts a dispatch payload. Catches the issue before token spend.

Both surfaces call the same `tech_dev_agents/morris/pre_dispatch/validator.py` module.

**Acceptance proof:** replay the three 2026-05-12 manual surgeries. All three reconstructed payloads (matching the original seeds at the time of dispatch) must return HTTP 422 with a structured error naming the missing section.

## Scope

1. **NEW Python package** at `tech_dev_agents/morris/pre_dispatch/`:
   - `__init__.py`
   - `validator.py` — pure functions, no HTTP:
     - `validate_seed_completeness(seed_text: str, scope: str, build_type: str | None = None) -> ValidationResult`
     - `extract_required_sections(seed_text: str) -> dict[str, str]`
     - `verify_referenced_paths_exist(seed_text: str, repo_root: Path) -> list[MissingPath]`
   - `models.py` — `ValidationResult(ok: bool, missing: list[str], warnings: list[str], structured: dict)`
   - `rules.py` — declarative rule set (REQUIRED_SECTIONS, PIPELINE_REQUIRED_REFS, etc.)
   - `tests/test_validator.py` — unit tests (≥12 cases incl. the three replays)
2. **API integration** — in `dispatch.py:200-…` (`enqueue_story`) and `dispatch_v2.py:560-666` (`enqueue`):
   - On entry, before any DB I/O, locate the candidate `seed.md` (algorithm: derive `features/story-<NNN>-…/seed.md` from `story_id`, then search `features/` glob for any folder starting with `story-<NNN>-`).
   - If no seed found and `scope ∈ {medium, large, new_project}` → 422 with `error="seed_missing"`.
   - If seed found → run `validate_seed_completeness(seed_text, scope, build_type)`; if `result.ok is False` → 422 with `error="seed_validation"` + `missing=[…]` + `structured` payload.
   - Bypass switch: `enqueued_by="morris-pre-validated"` skips re-validation (Morris's skill already ran it) **only when** the skill posted a passing-result fingerprint to a state file in the past 60 s.
3. **NEW Morris skill** at `deployment/vm/skills/morris/pre-dispatch-validate/SKILL.md` — invoked from `dispatch` skill before any `curl -X POST … /enqueue`. Reads the seed, runs the validator, reports missing sections, returns non-zero if invalid.
4. **Required sections (REQUIRED_SECTIONS)** — every seed must contain (case-insensitive heading match, non-empty body):
   - `Problem`
   - `Goal`
   - `Success criteria` (≥1 SC item)
   - `Files to modify`
   - `Files to NOT modify`
   - `Verification plan` (must include at least one `pytest`, `curl`, `gh`, or shell command)
   - `Boundaries` (must include `Always do`, `Ask first`, `Never do`)
   - `Done looks like`
   - `Escalation contract`
5. **Medium+ extras (MEDIUM_PLUS_REQUIRED)** — when `scope ∈ {medium, large, new_project}`:
   - `Do not do` list present OR `Out of scope` section present (either accepted)
   - Verification-plan command(s) reference real files / endpoints: if the command mentions `pytest <path>`, the path's parent directory must exist; if it mentions `curl … /api/<endpoint>`, the endpoint must exist in routes (best-effort grep).
6. **Pipeline build_type extras (PIPELINE_REQUIRED_REFS)** — when `build_type == "pipeline"` (set by STORY-1001; until then, infer from `repo` matching `*-v2` or `api-advertising-amazon`):
   - At least one reference to `gc-data-v2/sources/<src>/README.md` in the seed text.
   - At least one reference to `gc-data-v2/platform/*.md` in the seed text.
7. **Replay harness** — `tests/epic_1000/test_incident_replays.py` adds:
   - `test_replay_2a_manual_surgery_738_blocked`
   - `test_replay_2b_manual_surgery_766_blocked`
   - `test_replay_2c_manual_surgery_802_blocked`
   Each constructs the seed text as it would have existed before the manual surgery, calls the validator, asserts 422 + names the specific missing section.

## Out of scope

- Build-type auto-classification (STORY-1001 owns the `build_type` field; this story consumes it).
- Loading canon docs into the seed (STORY-1002 `load-canon`).
- Backporting findings to canon (STORY-1003 / STORY-1008 `canon-backport`).
- Modifying `seed.md` template itself (STORY-1001).
- Changing the dispatch DB schema.
- Validating seeds for Small-scope stories beyond the REQUIRED_SECTIONS set (small seeds are intentionally lighter).
- Semantic / LLM-based seed quality scoring (this is structural completeness only — fast, deterministic, no token spend).

## Files to modify

| Path | Action |
|---|---|
| `deployment/vm/skills/morris/pre-dispatch-validate/SKILL.md` | CREATE |
| `tech_dev_agents/morris/__init__.py` | CREATE if missing |
| `tech_dev_agents/morris/pre_dispatch/__init__.py` | CREATE |
| `tech_dev_agents/morris/pre_dispatch/validator.py` | CREATE |
| `tech_dev_agents/morris/pre_dispatch/models.py` | CREATE |
| `tech_dev_agents/morris/pre_dispatch/rules.py` | CREATE |
| `tech_dev_agents/morris/pre_dispatch/tests/__init__.py` | CREATE |
| `tech_dev_agents/morris/pre_dispatch/tests/test_validator.py` | CREATE |
| `tech_dev_agents/ops_console/routes/dispatch.py` | MODIFY — call validator at top of `enqueue_story` (line 201) |
| `tech_dev_agents/ops_console/routes/dispatch_v2.py` | MODIFY — call validator at top of `enqueue` (line 561) |
| `tests/epic_1000/test_incident_replays.py` | CREATE / EXTEND — add REPLAY-2a/2b/2c tests |
| `tests/epic_1000/fixtures/seed_738_pre_surgery.md` | CREATE — reconstructed seed |
| `tests/epic_1000/fixtures/seed_766_pre_surgery.md` | CREATE — reconstructed seed |
| `tests/epic_1000/fixtures/seed_802_pre_surgery.md` | CREATE — reconstructed seed |

## Files to NOT modify

- **`dispatch_795.py`, `dispatch_803.py`, `dispatch_804.py`** at repo root — these are historical evidence; reading them is fine but they are not edited.
- `tech_dev_agents/ops_console/routes/dispatch_v2.py` outside the entrypoint of `enqueue` (no schema changes, no event format changes).
- `tech_dev_agents/ops_console/services/dispatch_db_service.py` (validator does not touch the DB).
- Any existing `features/story-NNN/seed.md` (do not retroactively "fix" old seeds).
- `deployment/vm/skills/morris/dispatch-queue/SKILL.md` (out of scope; if it needs to call the validator, STORY-1007 owns that wiring).
- `.sdlc/skills/spec/SKILL.md` and `.sdlc/skills/start-story/SKILL.md` (framework side; STORY-1001/1008 own those).

## Verification plan

| SC | Command | Expected output |
|---|---|---|
| **SC-1** | `python -c "from tech_dev_agents.morris.pre_dispatch.validator import validate_seed_completeness; print(validate_seed_completeness)"` | Prints function repr; no ImportError. |
| **SC-2** | `pytest tech_dev_agents/morris/pre_dispatch/tests/ -v` | ≥12 GREEN tests covering: complete seed → ok; missing each REQUIRED_SECTIONS item (×9) → fail with that section named; medium scope missing Do-Not-Do → fail; pipeline build_type missing gc-data-v2 ref → fail; verification-plan with non-existent referenced file → warning (not fail) for non-pipeline / fail for pipeline. |
| **SC-3** | **REPLAY-2a (STORY-738):** `pytest tests/epic_1000/test_incident_replays.py::test_replay_2a_manual_surgery_738_blocked -v` | GREEN. Test posts a reconstructed STORY-738 seed (missing partial-gate documentation paths) to a mocked enqueue, asserts HTTP 422 with `missing` containing `do_not_do` (the `dispatch_804.py` fix was documenting partial-gate behavior — a Do-Not-Do gap). |
| **SC-4** | **REPLAY-2b (STORY-766):** `pytest tests/epic_1000/test_incident_replays.py::test_replay_2b_manual_surgery_766_blocked -v` | GREEN. Reconstructed STORY-766 seed has verification plan but no negative-path test for API-key guard, no first-run state fixture, no DM-delivery integration named in Files-to-modify. Validator returns 422 with `missing` containing `verification_plan_coverage` and `files_to_modify_completeness`. |
| **SC-5** | **REPLAY-2c (STORY-802):** `pytest tests/epic_1000/test_incident_replays.py::test_replay_2c_manual_surgery_802_blocked -v` | GREEN. Reconstructed STORY-802 seed references `LokiClient.query_cost_alerts()` in verification plan but the method does not exist; validator returns 422 with `missing` containing `verification_plan` and `structured.unverifiable_refs: ["LokiClient.query_cost_alerts"]`. |
| **SC-6** | **Live API gate (v2):** `curl -s -X POST -H "X-API-Key: $OPS" -H "Content-Type: application/json" --data '{"story_id":"STORY-9999","repo":"tech-dev-agents","scope":"medium","prompt":"x","title":"x","enqueued_by":"test"}' https://tech-dev-agents.gorillacommerce.ai/api/dispatch/v2/enqueue` (no seed file exists at `features/story-9999*`) | HTTP 422, body matches `{"error":"seed_missing","story_id":"STORY-9999","expected_path_glob":"features/story-9999-*/seed.md"}`. |
| **SC-7** | **Live API gate (v1):** same curl against `/api/dispatch` (no `/v2/`) | HTTP 422, same shape. |
| **SC-8** | **Structured error shape:** `curl … --data @bad_seed_payload.json` where the payload references an existing seed that is missing Verification plan + Do-Not-Do | HTTP 422, body `{"error":"seed_validation","story_id":"…","missing":["verification_plan","do_not_do"],"structured":{…}}`. |
| **SC-9** | **No false positive on Small:** enqueue a small-scope payload with a minimal seed (only Problem/Goal/Files/Verification) | HTTP 201 created. Small seeds aren't held to Medium+ rules. |
| **SC-10** | **Skill returns non-zero on invalid:** `claude-sdk -p "Run pre-dispatch-validate on features/story-9999-test/seed.md" -w /opt/agent ; echo $?` (with a deliberately incomplete seed) | Non-zero exit; stdout names missing sections. |
| **SC-11** | **Bypass token honored:** payload with `enqueued_by="morris-pre-validated"` and a fresh fingerprint in `/home/hermes/state/morris/pre-dispatch-fingerprints.json` | HTTP 201 created; logs show `pre_dispatch_bypass=true`. |
| **SC-12** | **Bypass token rejected when stale:** same payload, fingerprint >60s old | HTTP 422 (re-validates). |

## Boundaries

| Always do | Ask first | Never do |
|---|---|---|
| Run the validator at the API entrypoint before any DB write — no exceptions, no flags. | Add a new REQUIRED_SECTIONS rule (changes the bar for every dispatch). | Bypass the gate for "just this one urgent story" — every audit incident was an "urgent" exception. |
| Return structured JSON errors so Morris's `dispatch` skill can react programmatically (not just print a string). | Tune the `bypass` fingerprint TTL (currently 60 s). | Validate seeds *semantically* (LLM scoring) — gate must be deterministic and millisecond-fast. |
| Use case-insensitive heading match (`# problem`, `## Problem`, `### PROBLEM` all count). | Add a `force=true` query-param bypass. | Modify the v1 enqueue payload schema. |
| Cite the 2026-05-12 manual surgeries in commit messages and PR description so the *why* is preserved. | Validate Small-scope seeds against Medium+ rules. | Block legitimate small-scope enqueues by being too strict. |
| Add the three REPLAY tests as part of Phase 7 before any Phase 8 implementation. | Auto-create the missing sections in the seed file (out of scope; that is `start-story`). | Touch any other dispatch endpoint (claim / release / complete / fail). |
| Log every 422 with `story_id`, `repo`, `missing[]`, `scope` for fleet learning. | Re-validate already-enqueued jobs (this is enqueue-time only, not retroactive). | Retry past the validator if it returns ok=False — the gate is the source of truth. |

## Done looks like (terminal transcript)

```
$ pytest tech_dev_agents/morris/pre_dispatch/tests/ -v
tech_dev_agents/morris/pre_dispatch/tests/test_validator.py::test_complete_seed_passes PASSED
tech_dev_agents/morris/pre_dispatch/tests/test_validator.py::test_missing_problem_fails PASSED
tech_dev_agents/morris/pre_dispatch/tests/test_validator.py::test_missing_goal_fails PASSED
tech_dev_agents/morris/pre_dispatch/tests/test_validator.py::test_missing_success_criteria_fails PASSED
tech_dev_agents/morris/pre_dispatch/tests/test_validator.py::test_missing_files_to_modify_fails PASSED
tech_dev_agents/morris/pre_dispatch/tests/test_validator.py::test_missing_files_not_to_modify_fails PASSED
tech_dev_agents/morris/pre_dispatch/tests/test_validator.py::test_missing_verification_plan_fails PASSED
tech_dev_agents/morris/pre_dispatch/tests/test_validator.py::test_missing_boundaries_fails PASSED
tech_dev_agents/morris/pre_dispatch/tests/test_validator.py::test_missing_done_looks_like_fails PASSED
tech_dev_agents/morris/pre_dispatch/tests/test_validator.py::test_missing_escalation_contract_fails PASSED
tech_dev_agents/morris/pre_dispatch/tests/test_validator.py::test_medium_missing_donotdo_fails PASSED
tech_dev_agents/morris/pre_dispatch/tests/test_validator.py::test_pipeline_missing_gc_data_v2_ref_fails PASSED
tech_dev_agents/morris/pre_dispatch/tests/test_validator.py::test_small_minimal_seed_passes PASSED
============================ 13 passed in 0.18s ============================

$ pytest tests/epic_1000/test_incident_replays.py::test_replay_2a_manual_surgery_738_blocked tests/epic_1000/test_incident_replays.py::test_replay_2b_manual_surgery_766_blocked tests/epic_1000/test_incident_replays.py::test_replay_2c_manual_surgery_802_blocked -v
test_replay_2a_manual_surgery_738_blocked PASSED
test_replay_2b_manual_surgery_766_blocked PASSED
test_replay_2c_manual_surgery_802_blocked PASSED

$ curl -s -X POST -H "X-API-Key: $OPS" -H "Content-Type: application/json" \
    --data '{"story_id":"STORY-9999","repo":"tech-dev-agents","scope":"medium","prompt":"x","title":"x","enqueued_by":"test"}' \
    https://tech-dev-agents.gorillacommerce.ai/api/dispatch/v2/enqueue | jq .
{
  "error": "seed_missing",
  "story_id": "STORY-9999",
  "expected_path_glob": "features/story-9999-*/seed.md"
}
$ echo $?
0  # but HTTP status was 422 — check headers:
$ curl -s -o /dev/null -w "%{http_code}\n" -X POST … (same payload)
422
```

## Escalation contract

- **If the validator wrongly rejects a known-good seed (false positive),** Morris's skill must `needs_info` to Mark with the seed path + the rule that fired. Do not auto-relax the rule. Mark decides whether to amend the rule (new PR) or the seed.
- **If the validator's import fails inside the API process** (rare — code bug post-deploy), the API must fail-closed (return 503) rather than skip validation. Bug must be fixed before re-enabling. This is intentional — the cost of letting a bad dispatch through (token spend + rework) outweighs an enqueue outage.
- **If a dispatch caller cannot satisfy the gate after two attempts,** Morris escalates to Mark with the structured error; never patches around it.
- **Bypass abuse:** if `enqueued_by="morris-pre-validated"` is used by anything other than Morris's `pre-dispatch-validate` skill (audit logs in `dispatch_v2_events`), revoke the bypass mechanism immediately.


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

Medium scope, P0 rigor: `1 → 4 → 6 → 7 → 8 → 8b → Done`.

- Phase 1 (this seed): scope locked
- Phase 4 (analysis): API-layer vs middleware vs DB-trigger placement; fail-closed vs fail-open trade-off; bypass-token security
- Phase 6 (design): validator module API, rule precedence, structured-error JSON shape, fingerprint TTL, replay fixture format
- Phase 7 (test design): 13 unit tests + 3 REPLAY tests in `tests/epic_1000/`; RED before Phase 8
- Phase 8 (implementation): validator module → API integration (v1 + v2) → Morris skill → deploy via `push-code.sh morris` → live curl against staging endpoint
- Phase 8b (code review): security pass on bypass mechanism; fail-closed proof; correctness review of all three REPLAY fixtures vs the historical evidence

## Dependencies

- **Hard:** none. Ships first (P0). The validator works without STORY-1001 (it falls back to inferring `build_type` from repo name).
- **Soft:** STORY-1001 (real `build_type` field on enqueue request). When 1001 ships, the validator switches from inference to authoritative field.
- **Downstream consumers:** STORY-1007 (Morris `merge` gate), STORY-1009 (full `validate_dispatch_seed` API endpoint), STORY-1012 (`complete-story` 3-question gate).

## Why this story matters (read this if you're about to deprioritize it)

Every incident in the 2026-05-12 cluster cost Mark a manual rework cycle. Three stories, three hand-written dispatch scripts, three token-spends to repair work that was structurally unverifiable from its seed. A structural gate would have refused all three enqueues in <1 ms with a JSON error naming the missing section.

The validator does not need to be smart. It needs to be present.
