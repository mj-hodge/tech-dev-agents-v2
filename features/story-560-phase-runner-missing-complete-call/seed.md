# Seed: STORY-560 — Phase Runner Doesn't Call `/complete` After Successful Final Phase

## Overview
| Field | Value |
|-------|-------|
| Mode | feature_replace |
| Scope | small |
| Feature Name | Phase runner completion-reporting — call `/api/dispatch/complete` after the terminal phase produces a PR |
| Phase Path | 1 (Seed) → 7 (Test Design) → 8 (Implementation) → Done |
| Status | Seed complete 2026-04-24 — NOT DISPATCHED (parked for later) |

---

## 1. Idea / Trigger

On 2026-04-24 two critical-path stories (STORY-505 in `tech-project-mapping` and STORY-528 in `advertising-amazon`) completed their full SDLC phase sequences. Both agents:

1. Ran Phase 10 successfully — wrote `site-reliability.md`, updated `.project` (Completed Phases += STORY-X/10, Current Phase → Done), emitted `[DONE]` from the SDK.
2. Opened PRs via `gh pr create` from inside the phase runner's git-workflow path (PR #19 and PR #165 respectively).
3. Pushed the final phase commit.

But the ops-console dispatch DB rows for both stories STILL show:

```json
{
  "status": "claimed",
  "claimed_by": "devon" (or "daisy"),
  "pr_number": null,
  "commit_sha": null,
  "completed_at": null
}
```

The phase runner never called `POST /api/dispatch/complete/{story_id}` to transition the row. The PRs exist on GitHub, the work is done, but the queue view makes it look like both agents are still busy. In practice tonight this required operator intervention (Morris issuing manual `/complete` POSTs) to clear the rows and free the agents for new dispatches.

## 2. Problem Statement

- **Operator confusion:** The dispatch queue view shows long-running "claimed" rows for stories that are actually done. Morris fleet-vigilance cron may flag them as stale claims.
- **Agent bookkeeping:** The agent's `claimed_by` field stays set, blocking clean metrics on per-agent throughput and cost-per-story.
- **PR observability gap:** The `pr_number` column never gets populated, so the ops-console can't correlate PR merge events back to the story row (e.g., for auto-transitioning `in_review` → `completed` on PR merge).
- **No retry handling:** If the `/complete` call fails (network hiccup, ops-console 500, auth lapse), there's currently no retry — the row just sits. Tonight's failure mode is indistinguishable from "we never tried": zero log evidence in Loki under `{job="hermes-gateway", agent="devon"}` or `{agent="daisy"}` after the terminal [DONE] event shows a `POST /complete` attempt.

## 3. Scope Classification

**Small.** Single file (`deployment/hermes/sdlc_phase_runner.py` or, if the completion-report lives in `dispatch_poller.py`, that one) plus focused unit tests. No schema changes, no new endpoints, no cross-repo touches.

Phase path `1 → 7 → 8 → Done`. Skip 2-6 and 9-10.

## 4. Codebase Context

### Affected Files

- **`deployment/hermes/sdlc_phase_runner.py`** — Find the terminal-phase branch (where `Phase 10` completes successfully and the PR is already created). It currently exits silently after emitting `[DONE]`. Wire in a `/api/dispatch/complete/{story_id}?repo=<repo>` POST with `{"pr_number": <int>, "commit_sha": "<sha>"}` body. The `pr_number` is returned by `gh pr create` — capture it from the command output. The `commit_sha` is `git rev-parse HEAD` after the final push.
- **`deployment/hermes/dispatch_poller.py`** — If the completion-report is actually here (grep for `dispatch/complete` — I saw a `_report_complete` helper around line 248 earlier), verify it gets called on the terminal-phase path. If not called, add the call. If called but the HTTP response is ignored, add logging + retry (2 attempts, exponential backoff) and structured failure telemetry.
- Look for the existing `complete_r = session.post(...)` pattern; tonight's failure mode suggests either it's never reached OR it's silently erroring.

### Success criteria (expanded in Phase 6 / feature-spec, but the core):

- [ ] **SC-1:** After the terminal phase (Phase 10 for Large, Phase 8 for Small) succeeds AND a PR exists, the phase runner calls `POST /api/dispatch/complete/{story_id}?repo=<repo>` with `pr_number` and `commit_sha` in the body.
- [ ] **SC-2:** On HTTP 200: log `[DISPATCH] STORY-X complete-reported — pr=#N sha=abc1234` to the combined log so Loki captures it.
- [ ] **SC-3:** On non-2xx: retry once with 5-second backoff. If the retry also fails, emit a structured warning and write a sidecar flag file `/home/hermes/state/<agent>/complete-failed/<story_id>.json` so Morris fleet-vigilance can pick it up.
- [ ] **SC-4:** If `gh pr create` failed or no PR was opened, DO NOT call `/complete` (since it will reject). Instead log the missing-PR failure and leave the row in `claimed` — the operator can then manually trigger completion or re-dispatch.
- [ ] **SC-5:** Regression: existing terminal-phase behavior (deliverable verification, git push, PR creation) is unchanged.
- [ ] **SC-6:** Test fixture replays tonight's STORY-505 Phase 10 log pattern and asserts `_report_complete` would have been called with `pr_number=19` and the final commit SHA.

## 5. Out of Scope

- Automatic PR-merge detection → `in_review` to `completed` transition (separate story, needs a GitHub webhook or poll).
- Retroactively fixing tonight's stuck STORY-505 and STORY-528 DB rows — those get manually `/complete`d tonight as a one-shot.
- Fixing the related observation that `.project` and `backlog.md` updates land on the branch but sometimes don't get PR'd — separate story about phase-10 tracking-doc hygiene.

## 6. Related Stories

- **STORY-559 (parked):** Phase runner misclassifies SDK failures. Different symptom (phantom needs_info on auth failure) but same phase-runner file.
- **STORY-507 (in progress):** Resume-aware phase runner. Relevant if the terminal-phase completion-reporting needs to survive per-phase resume.
- **Track B (`fix/needs-info-idempotent-and-retry` branch):** Touched the same file. This story would stack on top of Track B after it lands.
- **Track A (`fix/claim-after-retry-ghost-claim` branch):** Fixed the auto-retry ghost-claim pattern. This story addresses the *inverse* ghost: agent succeeded but DB still thinks it's working.

## 7. Incident Log

### 2026-04-24 03:10Z — Two simultaneous stuck-completed states

- STORY-505 Phase 10 done at 02:15:53Z (devon), PR #19 open. DB still `claimed_by=devon`, `pr_number=null`, `completed_at=null` at 03:10Z (55 min elapsed).
- STORY-528 Phase 10 done at 03:04:35Z (daisy), PR #165 open MERGEABLE. DB still `claimed_by=daisy`, same null fields.
- Morris manually fired `/complete` POSTs for both to clear the queue view.
- Zero Loki evidence of a `/complete` HTTP attempt under either agent after the [DONE] line — either the phase runner never tries, or it tries and silently swallows the result.

## 8. Dispatch Notes (for future dispatcher)

- Target repo: `tech-dev-agents`.
- Branch convention: `story-560/story-560` (poller default) or `story-560/phase-runner-complete-call`.
- Target role: `developer`.
- Scope: `small`.
- Can dispatch independently — no hard dependency on Track A/B/etc., though landing it after those branches merge reduces merge friction in the same file.
- Expected runtime: Phase 7 ~15 min, Phase 8 ~25–30 min.

**Frontend:** false

## Test Criteria
- Validate story behavior with focused unit/integration tests for touched components.
- Verify no regressions in existing framework/contract checks.

## Validation
- [ ] Run required test suite(s) for this story scope.
- [ ] Confirm CI gates pass before merge.
