# STORY-1000 — Cross-Repo Canon Alignment (EPIC)

**Story ID:** STORY-1000
**Scope:** Epic (16 stories across 4 work-streams, 5 repos)
**Date:** 2026-05-18
**Reporter:** Mark Oreta
**Status:** Phase 1 — seed written, gated awaiting approval
**Frontend:** false

---

## Problem

The org has two coexisting canon systems that don't know about each other:

1. **`sdlc-framework`** — AI-agent SDLC (phase skills, spec/start-story/dispatch). The framework Morris and the dev-agent fleet use.
2. **`gc-data-v2`** — data-platform canon (30 platform/*.md docs, pipeline-template/, sources/, drift-check CI, 3-question PR gate). The data team uses this to keep 11 v2 pipeline repos in sync.

Symptoms of the disconnect, captured in a 2-week audit (2026-05-04 .. 2026-05-18):

| Date | Incident | Class | Root cause |
|---|---|---|---|
| 2026-05-04 | STORY-871 v2 mirror desync | architecture | No queue-side gate that v2 enqueues match `gc-data-v2/platform/pipeline-standard.md` |
| 2026-05-12 | 3 manual surgeries (STORY-738/766/802 → `dispatch_795/803/804.py`) | dispatch package integrity | No pre-dispatch seed-completeness gate |
| 2026-05-14 | Queue stall 9h+ (`/requeue-failed` unavailable) | ops resilience | No documented fallback for operator skills |
| Ongoing | 2026-05-04 retro's 7 framework changes never shipped | learning loop | No mechanism to convert retro findings into enforcing gates |

Morris has **zero references to `gc-data-v2` or `pipeline-standard`** in any skill. The framework has **no concept of `build_type`** — Phase 1 can't tell a data pipeline from a feature, so it can't load the right canon. The data team has the gate model the AI SDLC needs (`canon-drift-check.yml`, 3-question PR template, scaffold drift CI), but it's confined to one repo.

## Goal

A single canon-first, drift-checked, gate-enforced system across 5 repos. Every new build consumes the right knowledgebase up front and feeds discoveries back to canon on completion. The queue refuses incomplete dispatches before they waste tokens. Morris enforces canon alignment at PR review and merge.

## Scope — 4 work-streams, 16 stories

### Work-Stream A: SDLC framework knowledgebase integration (repo: `sdlc-framework`)

Goal: every build loads the right canon, records the version it loaded, and feeds discoveries back.

| Story | Title | Scope |
|---|---|---|
| STORY-1001 | `spec` + `phase-1` + `new-project`: build_type classifier + pipeline canon auto-load | Medium |
| STORY-1002 | NEW skill `load-canon` + phase-6/7/10 pipeline-aware modifications | Medium |
| STORY-1003 | NEW skill `canon-backport` + `retro` companion proposal + phase-9 3-question gate | Medium |
| STORY-1004 | NEW skills `scaffold-drift-check` + `pipeline-kickoff` | Small |

### Work-Stream B: Morris canon enforcement (repo: `tech-dev-agents`, `deployment/vm/skills/morris/`)

Goal: Morris enforces canon at PR review, merge, and dispatch time.

| Story | Title | Scope | Priority |
|---|---|---|---|
| STORY-1005 | NEW Morris skill `canon-check` (byte-diff v2 PRs vs `gc-data-v2/pipeline-template`) | Medium | **P0 — ship first** |
| STORY-1006 | NEW Morris skill `pre-dispatch-validate` (BLOCKING seed-completeness gate) | Medium | **P0 — ship first** |
| STORY-1007 | Morris `review-prs` + `merge` v2-PR gating | Medium | P1 |
| STORY-1008 | Morris `start-story` + `answer-needs-info` + `canon-backport` canon-aware mods | Medium | P1 |

### Work-Stream C: Queue validation & code-stability gates (repo: `tech-dev-agents`)

Goal: queue is rework-preventive, not rework-aware. All 4 audit incident classes catchable.

| Story | Title | Scope |
|---|---|---|
| STORY-1009 | `validate_dispatch_seed()` API gate + `POST /api/dispatch/v2/rework` endpoint | Medium |
| STORY-1010 | Ops-skill fallback registry + PR-link assertion at merge gate | Medium |
| STORY-1011 | sdlc-framework version tagging + downstream canon-drift CI | Medium |
| STORY-1012 | `complete-story` 3-question gate + new-behavior-assertion gate + canon-version pin in PR descriptions | Medium |

### Work-Stream D: Knowledgebase curation (repos: `gc-data-v2`, `tech-gc-knowledgebase`, `data-pipelines-runbooks`)

Goal: single discovery surface, freshness enforcement, no silos.

| Story | Title | Scope |
|---|---|---|
| STORY-1013 | `gc-data-v2`: scaffold versioning + `CHANGELOG.canon.md` + `sources/TEMPLATE.md` + structural-completeness CI | Medium |
| STORY-1014 | `gc-data-v2`: fix S7 security finding + P0-alert mechanism for 🔴 Active `platform-gaps.md` items | Medium |
| STORY-1015 | Retire `data-pipelines-runbooks` → merge into `gc-data-v2/platform/runbooks/` + cross-repo index in `tech-gc-knowledgebase` | Small |
| STORY-1016 | `tech-gc-knowledgebase`: freshness frontmatter + CI stale-page check + weekly Morris scan + `complete-story` auto-PR loop | Medium |

---

## Success criteria (epic-level)

1. **SC-1 — Audit incidents catchable:** all 4 incident classes from the 2026-05-04..05-18 audit (v2 mirror desync, manual surgeries, queue stall, retro proposals not shipped) are caught by gates introduced in this epic. Verified via replayed scenario tests.
2. **SC-2 — Build-type detection:** `spec` produces `build_type: pipeline|feature|bug|ops|infra` in `config.yaml`. Pipeline builds auto-load `gc-data-v2/platform/*.md` and record `gc_data_v2_commit:` in `.project`. Verified by running `/spec` against a pipeline change and inspecting `.project`.
3. **SC-3 — Pre-dispatch gate blocks incomplete seeds:** an enqueue call with a seed missing Do-Not-Do / Verification Plan / RED test paths returns 422 with a structured error naming the missing section. Verified by replaying the 3 manual-surgery dispatches from 2026-05-12.
4. **SC-4 — Morris canon-check on v2 PRs:** opening a PR in any `*-v2` repo with drifted canonical files (PR template, deploy workflow, GLIBC pin) results in a Morris comment listing the drift and `merge` refusing to auto-merge until the drift is resolved.
5. **SC-5 — Canon-backport loop:** when `complete-story` runs with a story whose seed/refinement-report flags a canon gap, a PR is auto-drafted against `gc-data-v2/platform/` (or `tech-gc-knowledgebase/wiki/` for business context) and linked from the closed story.
6. **SC-6 — Freshness enforcement:** any PR touching a `tech-gc-knowledgebase/wiki/**` page must update `last-reviewed:`. CI fails the PR otherwise. Weekly Morris scan opens "needs review" issues for pages >90 days old.
7. **SC-7 — Single discovery surface:** `tech-gc-knowledgebase/wiki/systems/data-platform-specs.md` exists and links every source to its `gc-data-v2/sources/{src}/` spec, relevant `platform/*.md`, and `failure-modes.md` section. `data-pipelines-runbooks` archived; stale reference in `wiki/processes/data-pipeline-operations.md:41` updated.
8. **SC-8 — Version pinning:** every PR description created by the dispatch service carries `gc-data-v2: <sha>` and `sdlc-framework: <tag>`. Downstream pipelines pin a `pipeline-template` version tag; CI fails on drift from the pinned tag.

## Acceptance — incident-replay tests (this is how we know we're done)

The epic merges only when these scenario tests are GREEN:

- **REPLAY-1 (2026-05-04 v2 mirror desync):** enqueue STORY-871-equivalent without v2 canon context → `pre-dispatch-validate` rejects with 422.
- **REPLAY-2 (2026-05-12 manual surgeries):** enqueue STORY-738/766/802-equivalents with seeds missing Do-Not-Do or verification scripts → `pre-dispatch-validate` rejects with 422 naming the missing section.
- **REPLAY-3 (2026-05-14 stall):** simulate `/requeue-failed` skill missing → ops-skill fallback registry produces a documented SQL-direct path; queue is unblocked within 15 min.
- **REPLAY-4 (retro→canon loop):** trigger a `retro` that finds a canon gap → `retro` emits both `retro-proposal.yaml` (framework) and `retro-proposal-gc-data-v2.yaml` (canon); both PRs draft automatically.

## Sequencing (ship order — by leverage, not by stream)

Highest leverage first. These two ship before anything else; they alone would have prevented every incident in the audit window.

1. **Week 1, P0:** STORY-1005 (canon-check) + STORY-1006 (pre-dispatch-validate). Parallel.
2. **Week 1, P1:** STORY-1001 (build_type classifier). Everything downstream depends on the classifier existing.
3. **Week 2:** STORY-1003 + STORY-1008 (canon-backport loop, both sides). STORY-1009 + STORY-1010 (queue gates). Parallel.
4. **Week 2-3:** STORY-1002, 1004, 1007, 1011, 1012. Parallel.
5. **Background (cron-scheduled):** STORY-1013..1016 (curation). High value, no urgency.

## Verification plan (epic level)

| SC | Verification command / artifact | Expected output |
|---|---|---|
| SC-1 | `pytest tests/epic_1000/test_incident_replays.py -v` | 4 GREEN scenario tests |
| SC-2 | `cat features/<test-pipeline-story>/seed.md \| grep build_type` + `grep gc_data_v2_commit .project` | Both fields populated |
| SC-3 | `curl -X POST /api/dispatch/v2/enqueue -d @bad_seed.json` | HTTP 422 with `{"error":"seed_validation","missing":["do_not_do","verification_plan"]}` |
| SC-4 | Open dummy PR in `walmart-supplier-v2` with drifted PR template → wait 5 min → `gh pr view --json comments` | Morris comment naming the drift; `mergeable=BLOCKED` |
| SC-5 | Run `complete-story` on a test story flagged `canon_gap: true` → `gh pr list --search "STORY-XXXX canon" --repo hpi-gorillacommerce/gc-data-v2` | 1 open PR linked to the closed story |
| SC-6 | Create PR touching a wiki page without bumping `last-reviewed:` → CI run | CI fails with `freshness-check` job RED |
| SC-7 | `ls tech-gc-knowledgebase/wiki/systems/data-platform-specs.md` + `gh repo view hpi-gorillacommerce/data-pipelines-runbooks --json isArchived` | File exists; repo archived=true |
| SC-8 | Inspect any PR created by dispatch service after rollout | Body contains `gc-data-v2: <40-char sha>` and `sdlc-framework: v<semver>` |

## Boundaries

| Always Do | Ask First | Never Do |
|---|---|---|
| Treat `gc-data-v2/platform/pipeline-standard.md` as the source of truth for pipeline conventions | Tag a new `pipeline-template@v1.x` release | Modify gc-data-v2 platform docs in this epic — those are *referenced*, not changed (except STORY-1014's S7 fix) |
| Each story produces RED tests in Phase 7 before Phase 8 implementation | Add a new canon doc to gc-data-v2 (do it via canon-backport, not in this epic) | Bypass `pre-dispatch-validate` for any story in this epic, including epic-internal stories — eat our own dogfood |
| Coordinate file moves between repos via PRs (don't `git mv` across submodule boundaries) | Archive `data-pipelines-runbooks` (irreversible — final call in STORY-1015) | Ship any P0/P1 story without the incident-replay test from SC-1 covering its slice |
| Wire `canon-version` pinning into every dispatch PR description | Change the Monday.com board structure | Force-push to any of the 5 repos' `main` branches |

## Files to modify (by repo)

**`sdlc-framework`:**
- `skills/spec/SKILL.md`, `skills/phase-1/SKILL.md`, `skills/new-project/SKILL.md`, `skills/phase-6/SKILL.md`, `skills/phase-7/SKILL.md`, `skills/phase-9/SKILL.md`, `skills/phase-10/SKILL.md`, `skills/retro/SKILL.md`
- NEW: `skills/load-canon/`, `skills/canon-backport/`, `skills/scaffold-drift-check/`, `skills/pipeline-kickoff/`
- `templates/` (new pipeline-aware templates)
- `software-development-guidance.md` (build_type section)

**`tech-dev-agents`:**
- `deployment/vm/skills/morris/review-prs/SKILL.md`, `merge/SKILL.md`, `start-story/SKILL.md`, `answer-needs-info/SKILL.md`
- NEW: `deployment/vm/skills/morris/canon-check/`, `canon-backport/`, `pre-dispatch-validate/`
- `tech_dev_agents/ops_console/routes/dispatch.py` (validate_dispatch_seed + rework endpoint)
- `tech_dev_agents/ops_console/dispatch_service.py` (ops-skill fallback registry; PR-link assertion)
- `tech_dev_agents/morris/` (canon-aware modifications)
- `.github/workflows/sdlc-drift-check.yml` (NEW)
- `features/story-1000../` through `features/story-1016../`

**`gc-data-v2`:**
- `CHANGELOG.canon.md` (NEW), version tags on `pipeline-template/`
- `sources/TEMPLATE.md` (NEW), structural-completeness CI under `.github/workflows/`
- `platform/runbooks/` (NEW — content from data-pipelines-runbooks)
- `platform/observability/scrubber.py` (S7 fix — order of scrubber vs exporter)
- `platform/platform-gaps.md` (active-finding alert hook)

**`tech-gc-knowledgebase`:**
- All `wiki/**/*.md` (add `last-reviewed:` frontmatter)
- `wiki/systems/data-platform-specs.md` (NEW cross-repo index)
- `wiki/processes/data-pipeline-operations.md:41` (fix stale `data-pipelines-runbooks` reference)
- `.github/workflows/freshness-check.yml` (NEW)

**`data-pipelines-runbooks`:**
- `runbooks/rb_template.md` → move to `gc-data-v2/platform/runbooks/`
- Archive the repo (final action in STORY-1015)

## Files to NOT modify

- Any `*-v2` pipeline repo's source code (they're consumers; we change the canon, not their instances)
- `gc-data-v2/platform/*.md` (referenced, not modified — STORY-1014's S7 fix is in `platform/observability/scrubber.py`, not the docs)
- `tech-dev-agents/.sdlc/` submodule pointer (managed by sdlc-framework story flow, not directly here)
- Monday.com board structure or column definitions

## Escalation contract

Per-story escalations follow the standard 60s directive guard + needs_info flow. **Epic-level escalations** that go to Mark:

1. Any P0 story (STORY-1005, STORY-1006) blocked >24h
2. Any cross-repo file move where target repo's CI fails after merge
3. Any `gc-data-v2` PR opened by `canon-backport` that gets rejected by the data team — pause `canon-backport` loop until alignment
4. Any incident-replay scenario test still RED after its owning story merges

## Done looks like

```
$ pytest tests/epic_1000/test_incident_replays.py -v
test_replay_1_v2_mirror_desync_blocked .............................. PASSED
test_replay_2a_manual_surgery_738_blocked ........................... PASSED
test_replay_2b_manual_surgery_766_blocked ........................... PASSED
test_replay_2c_manual_surgery_802_blocked ........................... PASSED
test_replay_3_requeue_failed_fallback_unblocks ...................... PASSED
test_replay_4_retro_emits_dual_proposals ............................ PASSED

$ gh pr list --search "STORY-100" --state merged --json number | jq length
17    # 1 epic coordination PR + 16 story PRs

$ gh repo view hpi-gorillacommerce/data-pipelines-runbooks --json isArchived
{"isArchived": true}
```


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

Epic decomposition → per-story SDLC for each of STORY-1001..STORY-1016 → epic-level integration tests (`tests/epic_1000/`) → retrospective.

Per-story phase paths declared in each story's seed.md (most Medium = `1 → 6 → 7 → 8 → Done`; P0 stories = `1 → 4 → 6 → 7 → 8 → 8b → Done` for extra rigor).
