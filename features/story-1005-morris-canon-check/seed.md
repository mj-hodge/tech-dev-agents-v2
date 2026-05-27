# STORY-1005 — Morris `canon-check` skill: byte-diff v2 PRs against `gc-data-v2/pipeline-template`

**Story ID:** STORY-1005
**Scope:** Medium
**Epic:** STORY-1000 (Cross-Repo Canon Alignment)
**Work-Stream:** B — Morris canon enforcement
**Priority:** **P0 — ship first** (parallel with STORY-1006)
**Repo touched:** `tech-dev-agents`
  - `deployment/vm/skills/morris/canon-check/SKILL.md` (NEW)
  - `tech_dev_agents/morris/canon_check/` (NEW Python package)
**Date:** 2026-05-18
**Reporter:** Mark Oreta
**Status:** Phase 1 — seed in progress
**Frontend:** false

---

## Problem

The `gc-data-v2/pipeline-template/` scaffold owns the canonical PR template, drift-check workflow, deploy workflow, and GLIBC pin for every `*-v2` pipeline repo. The data team's own `canon-drift-check.yml` (see `/mnt/c/Projects/gc-data-v2/pipeline-template/.github/workflows/canon-drift-check.yml`) byte-matches these files against `pipeline-template/main` and fails RED when they drift.

**That workflow only runs if the consuming repo already has it.** New v2 repos can be created without it. Repos can edit it. Repos can quietly remove the GLIBC pin from their deploy workflow and the drift check vanishes with it. Morris currently has **zero awareness** of any of this — `review-prs/SKILL.md` (lines 60–185) checks size, CI, security, and tests, but never asks "is the canonical scaffold intact?".

The 2026-05-04 v2 mirror-desync incident (epic seed line 22) was exactly this: a `*-v2` PR landed with drifted canonical files; nothing failed because Morris doesn't enforce the gate. The data team's `canon-drift-check.yml` is single-repo enforcement; Morris is the cross-repo enforcer that must close the loop.

## Goal

A new Morris skill `canon-check` that, on every open PR in any `hpi-gorillacommerce/*-v2` repo, byte-diffs the PR's canonical files against `gc-data-v2/pipeline-template/main` HEAD (or a pinned tag once STORY-1013 ships) and:

1. Comments on the PR listing each drifted file with a diff snippet and remediation (URL + `curl -o` command).
2. Posts a failing required-status-check named `morris/canon-check` so GitHub branch protection sets `mergeable=BLOCKED`.
3. Re-runs idempotently every poll cycle until the drift is fixed (or the PR closes).

This is the cross-repo analog of `gc-data-v2/.github/workflows/canon-drift-check.yml`, but it runs as Morris, not as a per-repo GitHub Action — so repos can't escape it by removing the workflow.

## Scope

1. **NEW Morris skill** at `deployment/vm/skills/morris/canon-check/SKILL.md` following the existing Morris skill format (cf. `review-prs/SKILL.md` and `merge/SKILL.md` for tone, frontmatter, `terminal(command=…)` blocks).
2. **NEW Python package** at `tech_dev_agents/morris/canon_check/` with:
   - `__init__.py`
   - `differ.py` — pure function `check_pr_for_drift(repo: str, pr_number: int, scaffold_ref: str = "main") -> CanonCheckResult`
   - `commenter.py` — `post_drift_comment(repo, pr_number, result)` and `update_status_check(repo, sha, state, description)`
   - `tests/test_differ.py` — unit tests with mocked GitHub API
3. **Repo detection** — pattern match `hpi-gorillacommerce/*-v2` (also include `api-advertising-amazon` per the 3-question template's sibling list at `pull_request_template.md:38`).
4. **Canonical files to compare** (mirror the set in `gc-data-v2/pipeline-template/.github/workflows/canon-drift-check.yml:39-42` + extras called out in the epic):
   - `.github/pull_request_template.md`
   - `.github/workflows/canon-drift-check.yml`
   - `.github/workflows/pr-canon-readback.yml`
   - `.github/workflows/deploy-function-app.yaml` (full byte-match if scaffold version exists; otherwise content assertion for `--python-platform x86_64-manylinux_2_17`)
   - `auth/*` pattern files if present in scaffold
5. **Output channels** — PR comment (gh CLI) + commit-status (gh api).
6. **Wire to existing `review-prs`** — `canon-check` is invoked as a sub-step inside the review loop OR scheduled as its own cron; do NOT duplicate the PR-discovery logic.
7. **Pinning hook** — read `gc-data-v2` ref from `/home/hermes/state/morris/canon-pins.yaml` (created by STORY-1013); fall back to `main` when missing.

## Out of scope

- Modifying any `*-v2` consumer repo (`walmart-supplier-v2` etc.). Canon-check is read-only on those repos.
- Auto-backporting drifted files (that is STORY-1003 / STORY-1008 `canon-backport`).
- The `gc-data-v2` `canon-drift-check.yml` itself (referenced, not changed).
- Build-type classifier (STORY-1001).
- Seed validation (STORY-1006).
- Modifying `merge/SKILL.md` to honor the failing status check (that is STORY-1007).

## Success criteria

| ID | Criterion | Pass / fail signal |
|---|---|---|
| **SC-1** | Skill file exists at `deployment/vm/skills/morris/canon-check/SKILL.md` with valid frontmatter (`name:`, `description:`, `category:`, `agent: morris`). | `ls deployment/vm/skills/morris/canon-check/SKILL.md` returns the path; `head -10` shows `agent: morris`. |
| **SC-2** | `tech_dev_agents/morris/canon_check/differ.py` exposes `check_pr_for_drift(repo, pr_number, scaffold_ref="main") -> CanonCheckResult` and is importable. | `python -c "from tech_dev_agents.morris.canon_check.differ import check_pr_for_drift; print(check_pr_for_drift)"` prints the function. |
| **SC-3** | Unit tests pass. | `pytest tech_dev_agents/morris/canon_check/tests/ -v` shows ≥6 GREEN tests covering: (a) matching scaffold → no drift, (b) drifted PR template → drift detected, (c) missing canonical file → drift detected, (d) missing GLIBC pin → drift detected, (e) scaffold fetch 404 → graceful skip + warning, (f) non-v2 repo → returns `n/a`. |
| **SC-4** | **Incident replay** — open a dummy PR in `walmart-supplier-v2` with a tampered `.github/pull_request_template.md` (delete the "Continuous-improvement checklist" section). Run the skill. Within 5 min: PR has a Morris comment naming `.github/pull_request_template.md` as drifted with the diff; `gh pr view --json statusCheckRollup` shows `morris/canon-check: FAILURE`. | Both observations true; documented as REPLAY-1.x in `tests/epic_1000/test_incident_replays.py`. |
| **SC-5** | **Idempotency** — re-running the skill on a still-drifted PR updates the existing Morris comment (does not stack new comments) and re-asserts the failing status. | `gh pr view --json comments \| jq '[.comments[] \| select(.author.login=="morris-bot")] \| length'` = 1 after 3 successive runs. |
| **SC-6** | **No false positives on non-v2 repos** — running the skill against an open PR in `tech-dev-agents` (this repo) is a no-op; no comment, no status. | After invoke, `gh pr view --json comments` for the test PR shows zero morris-bot comments tagged `canon-check`. |
| **SC-7** | **Pin file honored** — when `/home/hermes/state/morris/canon-pins.yaml` exists with `gc_data_v2: <sha>`, the comparison fetches `raw.githubusercontent.com/.../gc-data-v2/<sha>/pipeline-template/<file>` instead of `main`. | Stub the file with a known sha, run differ, mock asserts the URL contained that sha. |
| **SC-8** | **Skill discoverable** — `morris-bot` lists `canon-check` when running its skill inventory check (no broken frontmatter). | `claude-sdk -p "list available skills" -w /opt/agent` mentions `canon-check`. |

## Files to modify

| Path | Action |
|---|---|
| `deployment/vm/skills/morris/canon-check/SKILL.md` | CREATE |
| `tech_dev_agents/morris/__init__.py` | CREATE if missing (likely missing) |
| `tech_dev_agents/morris/canon_check/__init__.py` | CREATE |
| `tech_dev_agents/morris/canon_check/differ.py` | CREATE |
| `tech_dev_agents/morris/canon_check/commenter.py` | CREATE |
| `tech_dev_agents/morris/canon_check/tests/__init__.py` | CREATE |
| `tech_dev_agents/morris/canon_check/tests/test_differ.py` | CREATE |
| `tests/epic_1000/test_incident_replays.py` | CREATE skeleton with `test_replay_1_v2_mirror_desync_blocked` (full test wired in STORY-1009; this story stubs the v2-PR-drift slice) |

## Files to NOT modify

- `deployment/vm/skills/morris/review-prs/SKILL.md` — touched in STORY-1007, NOT here. (Only safe edit allowed: append a single line in Step 3 pointing to `canon-check`. If even that risks merge conflict with 1007, leave it for 1007.)
- `deployment/vm/skills/morris/merge/SKILL.md` — STORY-1007.
- Any `*-v2` consumer repo — read-only.
- `gc-data-v2/pipeline-template/**` — that is the source of truth; do not edit it.
- `tech_dev_agents/ops_console/routes/dispatch.py` — STORY-1006.
- The `pre-dispatch-validate` skill files — STORY-1006.

## Verification plan

| SC | Command | Expected output |
|---|---|---|
| SC-1 | `ls -la deployment/vm/skills/morris/canon-check/SKILL.md && head -10 deployment/vm/skills/morris/canon-check/SKILL.md` | File exists; frontmatter shows `name: canon-check` and `agent: morris`. |
| SC-2 | `python -c "from tech_dev_agents.morris.canon_check.differ import check_pr_for_drift, CanonCheckResult; print('ok')"` | `ok` |
| SC-3 | `pytest tech_dev_agents/morris/canon_check/tests/ -v` | ≥6 tests PASS, 0 fail |
| SC-4 | (a) Create test PR: `cd /tmp && git clone https://github.com/hpi-gorillacommerce/walmart-supplier-v2 && cd walmart-supplier-v2 && git checkout -b test/STORY-1005-drift && sed -i '/Continuous-improvement/,/Sibling-pipeline sweep/d' .github/pull_request_template.md && git commit -am 'test drift' && git push -u origin test/STORY-1005-drift && gh pr create --title 'STORY-1005 test drift' --body 'do not merge'`<br>(b) Invoke skill: `claude-sdk -p "Run canon-check on PR <N> in walmart-supplier-v2" -w /opt/agent`<br>(c) `gh pr view <N> --repo hpi-gorillacommerce/walmart-supplier-v2 --json comments,statusCheckRollup` | Comments array contains Morris comment with body matching `pull_request_template.md.*drifted`; statusCheckRollup contains `{"name":"morris/canon-check","conclusion":"FAILURE"}`. |
| SC-5 | Re-run step (b) above 3x. Then `gh pr view <N> --json comments --jq '[.comments[] \| select(.body \| contains("canon-check"))] \| length'` | `1` |
| SC-6 | Open a PR in `tech-dev-agents` (e.g. existing open PR). Invoke `canon-check`. Then `gh pr view <N> --repo hpi-gorillacommerce/tech-dev-agents --json comments --jq '[.comments[] \| select(.body \| contains("canon-check"))] \| length'` | `0` |
| SC-7 | `echo 'gc_data_v2: deadbeef1234' > /home/hermes/state/morris/canon-pins.yaml`; run differ with HTTP mock; assert mock saw URL containing `/deadbeef1234/pipeline-template/`. | Test in `test_differ.py::test_pin_sha_honored` PASSES. |
| SC-8 | `claude-sdk -p "What skills are available in deployment/vm/skills/morris/?" -w /opt/agent` | Output mentions `canon-check`. |

## Boundaries

| Always do | Ask first | Never do |
|---|---|---|
| Use byte-diff (`diff -u`) against `gc-data-v2/pipeline-template/main` for canonical-file comparison. | Add a new canonical file to the watch list beyond the 4 in the scope section. | Modify any consumer `*-v2` repo from this skill (it is read-only on those repos). |
| Post the diff snippet and a `curl -fsSL <URL> -o <FILE>` remediation in the PR comment so the author has a copy-paste fix. | Change the failing-status-check name `morris/canon-check` (other systems may grep for it). | Mark a PR as canon-clean when a canonical file is missing entirely — that is drift, not absence. |
| Skip PRs whose title contains `Partial` (per `review-prs/SKILL.md:27-41`). | Add scaffolding files beyond the 4 in scope. | Stack duplicate comments on a still-drifted PR (must update existing). |
| Set the `morris/canon-check` commit status to `FAILURE` when drift detected, `SUCCESS` when clean, `PENDING` while running. | Pin to a `pipeline-template` tag before STORY-1013 ships (default is `main`). | Edit `gc-data-v2/pipeline-template/**` from this skill — that is canon-backport's job. |
| Honor `/home/hermes/state/morris/canon-pins.yaml` if present. | Wire `canon-check` into `review-prs` step ordering (cross-story; coordinate with STORY-1007). | Use `gh api` to write to non-status, non-comment endpoints (no force-pushing, no branch edits). |

## Done looks like (terminal transcript)

```
$ pytest tech_dev_agents/morris/canon_check/tests/ -v
tech_dev_agents/morris/canon_check/tests/test_differ.py::test_matching_scaffold_no_drift PASSED
tech_dev_agents/morris/canon_check/tests/test_differ.py::test_drifted_pr_template_detected PASSED
tech_dev_agents/morris/canon_check/tests/test_differ.py::test_missing_canonical_file_is_drift PASSED
tech_dev_agents/morris/canon_check/tests/test_differ.py::test_missing_glibc_pin_detected PASSED
tech_dev_agents/morris/canon_check/tests/test_differ.py::test_scaffold_fetch_404_graceful PASSED
tech_dev_agents/morris/canon_check/tests/test_differ.py::test_non_v2_repo_returns_na PASSED
tech_dev_agents/morris/canon_check/tests/test_differ.py::test_pin_sha_honored PASSED
======================== 7 passed in 0.42s ========================

$ pytest tests/epic_1000/test_incident_replays.py::test_replay_1_v2_mirror_desync_blocked -v
test_replay_1_v2_mirror_desync_blocked PASSED

# Live PR check against walmart-supplier-v2 test-PR #N:
$ gh pr view N --repo hpi-gorillacommerce/walmart-supplier-v2 --json statusCheckRollup --jq '.statusCheckRollup[] | select(.name=="morris/canon-check") | .conclusion'
FAILURE

$ gh pr view N --repo hpi-gorillacommerce/walmart-supplier-v2 --json comments --jq '.comments[] | select(.body | contains("canon-check")) | .body' | head -5
## Morris canon-check — DRIFT DETECTED

Canonical files in this PR drift from `gc-data-v2/pipeline-template/main`:

- `.github/pull_request_template.md` — diff:
```

## Escalation contract

- If `gc-data-v2/pipeline-template/main` is itself broken / mid-refactor, the skill MUST detect the upstream 404 / parse failure and `needs_info` to Mark with the scaffold URL and HTTP response; never post a misleading "no drift" comment.
- If GitHub API rate-limit hits during a poll cycle: log + skip + retry next cycle; do not retry-storm.
- If three consecutive poll cycles fail to fetch the scaffold (network partition), DM Mark and pause the skill via state file.
- If a v2 consumer repo's `canon-drift-check.yml` workflow has been deleted, `canon-check` should still run from Morris's side — that is the entire point of having a cross-repo enforcer.


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

Medium scope: `1 → 6 → 7 → 8 → Done`.
(Per epic seed line 205, P0 stories get extra rigor: `1 → 4 → 6 → 7 → 8 → 8b → Done`.)

- Phase 1 (this seed): scope locked
- Phase 4 (analysis): byte-diff vs canonical-AST vs structural diff trade-off
- Phase 6 (design): module layout, GitHub API surface, state file format, status-check name, commenter idempotency strategy
- Phase 7 (test design): the 7 unit tests in SC-3 + REPLAY-1.x scaffold in `tests/epic_1000/`
- Phase 8 (implementation): RED → GREEN, deploy to Morris VM via `push-code.sh morris`, verify on live PR
- Phase 8b (code review): security pass on `gh api` writes (no token leakage in logs); idempotency proof

## Dependencies

- **Hard:** none. This ships first (P0).
- **Soft:** STORY-1013 (canon-pins.yaml schema). Until it lands, default ref is `main`.
- **Coordinates with:** STORY-1007 (review-prs/merge changes that consume the `morris/canon-check` status).

## Design preview (concretized in Phase 6)

### `CanonCheckResult` shape (suggested — locked in Phase 6)

```python
@dataclass
class DriftedFile:
    path: str                  # e.g. ".github/pull_request_template.md"
    drift_type: Literal["MODIFIED", "MISSING", "ASSERTION_FAILED"]
    scaffold_url: str          # raw.githubusercontent.com URL
    diff_snippet: str | None   # `diff -u` output, truncated to 40 lines for the comment
    remediation: str           # e.g. "curl -fsSL <url> -o <path>"

@dataclass
class CanonCheckResult:
    repo: str
    pr_number: int
    head_sha: str
    scaffold_ref: str          # "main" or a sha if pinned
    drifted: list[DriftedFile] # empty list => clean
    status: Literal["SUCCESS", "FAILURE", "PENDING", "NA"]  # NA for non-v2 repos
```

### Watched-file list (locked in Phase 6; matches `gc-data-v2/pipeline-template/.github/workflows/canon-drift-check.yml:39-42` plus deploy-workflow extras from the epic)

```
.github/pull_request_template.md            # byte-match
.github/workflows/canon-drift-check.yml     # byte-match (self-check)
.github/workflows/pr-canon-readback.yml     # byte-match
.github/workflows/deploy-function-app.yaml  # content-assert: must contain "--python-platform x86_64-manylinux_2_17"
auth/azure_function_auth.py                 # byte-match (if exists in scaffold)
```

### Comment format (locked in Phase 6)

```
## Morris canon-check — DRIFT DETECTED

Canonical files in this PR drift from `gc-data-v2/pipeline-template/<ref>`:

- `.github/pull_request_template.md` — diff:
  ```diff
  <truncated 40-line diff>
  ```
  Remediation: `curl -fsSL https://raw.githubusercontent.com/hpi-gorillacommerce/gc-data-v2/<ref>/pipeline-template/.github/pull_request_template.md -o .github/pull_request_template.md`

- `.github/workflows/deploy-function-app.yaml` — assertion failed:
  Missing required pin `--python-platform x86_64-manylinux_2_17`. See `gc-data-v2/platform/failure-modes.md` § "Deploy gate 2b".

---
*Posted by Morris (Engineering Manager). Re-runs on every poll cycle. Last checked: <ISO timestamp>.*
*Status check `morris/canon-check` will remain FAILURE until all drifts are resolved.*
```

### Cron / invocation cadence

- Invoked inline from `review-prs` poll loop (`*/30 8-18 * * 1-5` per `review-prs/SKILL.md:261`).
- Also runnable on demand: `claude-sdk -p "Run canon-check on PR <N> in <repo>" -w /opt/agent`.
- Scaffold fetch cached in-memory per poll cycle (don't refetch for every PR in the same loop).

## Why this story matters

The 2026-05-04 v2 mirror desync (epic seed line 22) cost Mark a recovery cycle and exposed that the data team's own `canon-drift-check.yml` is per-repo enforcement: it disappears the moment a repo removes the workflow. Morris-side enforcement is the only way to catch that class. The skill is small, the leverage is large.
