# STORY-1011 — sdlc-framework version tagging + downstream canon-drift CI

**Story ID:** STORY-1011
**Scope:** Medium
**Epic:** STORY-1000 (Cross-Repo Canon Alignment)
**Work-Stream:** C — Queue validation & code-stability gates
**Repo touched:** `tech-dev-agents` (primary). Cross-repo coordination touch on `sdlc-framework` for the v1.0.0 tag + CHANGELOG.md, but no source-code changes there beyond the tag and an additive doc.
**Date:** 2026-05-18
**Reporter:** Mark Oreta
**Status:** Phase 1 — seed written, awaiting gate
**Frontend:** false

---

## Problem

The retro on 2026-05-04 captured 7 framework changes that "should ship" — none did. The reason is structural, not motivational: **the SDLC framework has no version surface.** Every downstream project (`tech-dev-agents`, `project-mapping`, the 11 v2 pipeline repos) consumes the framework via the `.sdlc/` git submodule. The submodule pointer is a commit SHA, not a semver tag. There is no `CHANGELOG.md` in `sdlc-framework`. There is no way for a downstream project to declare "I'm pinned to framework v1.2.0" and have CI fail if its `.sdlc/` content drifts from that tag.

Concrete evidence:

- `/mnt/c/Projects/tech-dev-agents/.sdlc/VERSION` contains `1.0.0` — but this is a single string with no surrounding contract. Nothing reads it, nothing enforces it, nothing in `sdlc-framework/` agrees that this is the canonical pinned version. The downstream pin is implicit (the submodule SHA) and the upstream tag does not exist.
- `git -C /mnt/c/Projects/sdlc-framework tag --list` lists no `v1.0.0` tag (verified by spot check during seed authoring).
- Symptom: when someone edits a skill in `.sdlc/skills/spec/SKILL.md` directly inside `tech-dev-agents` (bypassing the submodule update flow), the change persists, never propagates back to `sdlc-framework`, and quietly drifts. The 2026-05-04 retro's "ship 7 things" never happened because there was no mechanism to declare "framework v1.1.0 is now the baseline; here's what changed."
- The data team solved this exact problem in `gc-data-v2` with `canon-drift-check.yml` byte-diffing `pipeline-template/` against a pinned tag. The AI SDLC has nothing equivalent.

## Goal

1. Tag `sdlc-framework@v1.0.0` as the current state baseline. Add `CHANGELOG.md` to `sdlc-framework` and maintain it going forward.
2. Each downstream project pins a `sdlc-framework: v<semver>` version. For `tech-dev-agents`, the pin lives in `.sdlc/PINNED_VERSION` (a new file at the submodule root that does NOT live in `sdlc-framework` itself — it's a downstream-side declaration) **and** in the existing `.sdlc/VERSION` file (which currently exists as a string).
3. New CI workflow `.github/workflows/sdlc-drift-check.yml` in `tech-dev-agents` byte-diffs the local `.sdlc/` content against the pinned tag at `sdlc-framework@vX.Y.Z`. Fails CI on any drift.
4. Ship the same workflow as a template for downstream projects (in `templates/` under `sdlc-framework` — coordinated via a separate PR if needed, but the canonical version of the file lives in `tech-dev-agents` first and is copy-pasteable).

## Scope

**In scope (tech-dev-agents repo):**

- `.sdlc/PINNED_VERSION` — NEW file. One line: `v1.0.0`. This is the contract: "this project pins to framework v1.0.0."
- `.sdlc/VERSION` — already exists with `1.0.0`. Reformat to `v1.0.0` to match the tag naming and the PINNED_VERSION file. Confirm a single source of truth: either `VERSION` OR `PINNED_VERSION`, not both. **Decision in Phase 6:** keep `PINNED_VERSION` (downstream-managed) and DELETE `.sdlc/VERSION` (upstream-managed but never read). Document the rationale in the workflow file.
- `.github/workflows/sdlc-drift-check.yml` — NEW workflow:
  - Trigger: on PR (paths: `.sdlc/**`, `.github/workflows/sdlc-drift-check.yml`, `.sdlc/PINNED_VERSION`).
  - Steps: (1) read `.sdlc/PINNED_VERSION` → extract version tag, (2) clone `sdlc-framework` at that tag into `/tmp/sdlc-framework-pinned`, (3) byte-diff `.sdlc/` (excluding `.sdlc/.git`, `.sdlc/PINNED_VERSION` itself, and `.sdlc/CLAUDE.md.local` if it exists) against `/tmp/sdlc-framework-pinned/`, (4) fail the job if any byte-diff is non-empty, (5) print a summary listing the drifted files.
  - Use `gh repo clone hpi-gorillacommerce/sdlc-framework --ref <tag>` with auth via `${{ secrets.GITHUB_TOKEN }}`.
- A small Python helper `tools/sdlc_drift_check.py` (under `tech-dev-agents/tools/` — NEW directory if needed) that the workflow shells out to. Reason: keep the diff/exclusion logic testable. The workflow is a thin wrapper.
- Unit test `tests/tools/test_sdlc_drift_check.py` covering: (a) clean state passes, (b) modified `.sdlc/skills/spec/SKILL.md` is detected as drift, (c) excluded paths (`.git`, `PINNED_VERSION`) are ignored, (d) an unknown pinned version produces a clear error message.

**In scope (sdlc-framework repo — coordinated, minimal):**

- Tag commit `HEAD` of `sdlc-framework` `main` as `v1.0.0` (this happens in a separate small PR — out of the diff for this story but the gate depends on the tag existing).
- Add `CHANGELOG.md` to `sdlc-framework` with the v1.0.0 entry summarizing the current state. Keep brief — full audit is out of scope.

**Out of scope:**

- Pinning version in `project-mapping`, the 11 v2 pipeline repos, or any other downstream project. The template in this story is the artifact others copy; their PRs are separate work (potentially under STORY-1013 / STORY-1014's adjacent scope).
- Changing the `.sdlc/` submodule update workflow itself (how someone advances the pointer). Out of scope; orthogonal to drift detection.
- Auto-generating a CHANGELOG from commits — manually authored is fine for v1.0.0.
- Enforcing the workflow on the existing `main` branch — workflow runs on PR only; main-branch drift is detected the next time anyone opens a PR.
- Touching `tech_dev_agents/` Python code (this story is repo-config + a tooling helper).

## Success criteria

- **SC-1 — `v1.0.0` tag exists on `sdlc-framework`:** `git -C /mnt/c/Projects/sdlc-framework tag --list "v1.0.0"` returns `v1.0.0`. Verified by spot check after the coordination PR lands.
- **SC-2 — `CHANGELOG.md` exists in `sdlc-framework`:** `ls /mnt/c/Projects/sdlc-framework/CHANGELOG.md` succeeds; file contains a v1.0.0 entry.
- **SC-3 — Pin file in tech-dev-agents:** `cat /mnt/c/Projects/tech-dev-agents/.sdlc/PINNED_VERSION` outputs `v1.0.0`. `.sdlc/VERSION` is removed.
- **SC-4 — Drift check workflow exists and runs:** `.github/workflows/sdlc-drift-check.yml` exists. On a PR with no `.sdlc/` changes, the job conclusion is `success`. Verified by inspecting `gh run list --workflow=sdlc-drift-check.yml`.
- **SC-5 — Tampering with a skill fails CI:** modify `.sdlc/skills/spec/SKILL.md` in a feature branch, open a PR, observe the `sdlc-drift-check` job status = `failure` with output naming the drifted file. Verified by `gh pr checks <pr_number>`.
- **SC-6 — Bumping the pin fixes CI:** after intentionally bumping the framework, re-tag `sdlc-framework@v1.1.0`, bump `.sdlc/PINNED_VERSION` to `v1.1.0`, advance the submodule pointer to the new tag — drift check is GREEN. Verified by a second-PR demonstration in Phase 8.
- **SC-7 — Helper unit tests GREEN:** `pytest tests/tools/test_sdlc_drift_check.py -v` — all 4 cases pass.
- **SC-8 — Error message on unknown tag:** if `.sdlc/PINNED_VERSION` points to `v99.9.9` (does not exist), the workflow fails with `error: pinned version v99.9.9 not found in sdlc-framework — check tag exists upstream`. Verified by Phase 7 fault-injection test.

## Files to modify

- `/mnt/c/Projects/tech-dev-agents/.sdlc/PINNED_VERSION` — NEW.
- `/mnt/c/Projects/tech-dev-agents/.sdlc/VERSION` — DELETE.
- `/mnt/c/Projects/tech-dev-agents/.github/workflows/sdlc-drift-check.yml` — NEW.
- `/mnt/c/Projects/tech-dev-agents/tools/sdlc_drift_check.py` — NEW.
- `/mnt/c/Projects/tech-dev-agents/tools/__init__.py` — NEW (empty, if needed for pytest discovery).
- `/mnt/c/Projects/tech-dev-agents/tests/tools/__init__.py` — NEW (empty).
- `/mnt/c/Projects/tech-dev-agents/tests/tools/test_sdlc_drift_check.py` — NEW.
- (Coordination PR, separate diff) `/mnt/c/Projects/sdlc-framework/CHANGELOG.md` — NEW.
- (Coordination PR, separate diff) `sdlc-framework` git tag `v1.0.0` — created from the relevant commit.

## Files to NOT modify

- Any file under `.sdlc/skills/`, `.sdlc/agents/`, `.sdlc/templates/`, or `.sdlc/software-development-guidance.md` — those are framework content; this story is about *enforcing* their stability, not changing them.
- `.sdlc/CLAUDE.md` (framework-managed; downstream cannot edit it).
- The submodule pointer itself — the standard `git submodule update` flow remains the way to advance it. We don't change that.
- `tech_dev_agents/` Python source (no app-code changes).
- The existing CI workflows: `contract-critical.yml`, `deploy-ops-console.yml`, `migration-invariant.yml`, `test.yml`.
- Any file in `project-mapping/` or the 11 v2 pipeline repos.

## Verification plan

| SC | Command | Expected output |
|---|---|---|
| SC-1 | `git -C /mnt/c/Projects/sdlc-framework tag --list "v1.0.0"` | `v1.0.0` |
| SC-2 | `head -5 /mnt/c/Projects/sdlc-framework/CHANGELOG.md` | `# Changelog` + `## v1.0.0 — 2026-05-18` lines visible |
| SC-3 | `cat /mnt/c/Projects/tech-dev-agents/.sdlc/PINNED_VERSION && test ! -f /mnt/c/Projects/tech-dev-agents/.sdlc/VERSION && echo OK` | `v1.0.0` + `OK` |
| SC-4 | `gh workflow list --repo hpi-gorillacommerce/tech-dev-agents \| grep sdlc-drift-check` | matches the workflow name; `gh run list --workflow=sdlc-drift-check.yml --limit 1` shows `success` for the merged-state run |
| SC-5 | (manual demo PR) `git -C /mnt/c/Projects/tech-dev-agents checkout -b drift-demo && sed -i 's/^name:.*/name: tampered/' .sdlc/skills/spec/SKILL.md && git commit -am demo && git push -u origin drift-demo && gh pr create --title "drift demo" --body "demo" && gh pr checks <pr#> --watch` | `sdlc-drift-check ❌ failure`; job log contains `drifted: .sdlc/skills/spec/SKILL.md` |
| SC-6 | After bumping pin: `gh pr checks <new_pr#>` | `sdlc-drift-check ✅ success` |
| SC-7 | `pytest tests/tools/test_sdlc_drift_check.py -v` | `4 passed` |
| SC-8 | (manual demo PR) bump `.sdlc/PINNED_VERSION` to `v99.9.9`, open PR | `sdlc-drift-check ❌ failure`; log contains `pinned version v99.9.9 not found` |

## Boundaries

| Always Do | Ask First | Never Do |
|---|---|---|
| Use a real semver tag (`v1.0.0`) on `sdlc-framework` — not a branch, not a SHA | Cut a new framework version (`v1.1.0`, etc.) — only the framework maintainer decides version bumps | Force-push to `sdlc-framework` `main` or move an existing tag |
| Keep the drift check workflow logic in `tools/sdlc_drift_check.py` so it's unit-testable; the `.yml` shells out | Add the workflow to other downstream repos (other repos are owned by their teams; ship the template, let them adopt) | Skip the unit tests under `tests/tools/` — the byte-diff logic must be tested |
| Exclude `.sdlc/PINNED_VERSION` and `.sdlc/.git` from the diff (excluding `.sdlc/CLAUDE.md.local` if it exists for per-project overrides) | Expand the exclusion list beyond the documented three paths | Hard-code the framework repo URL — read from `.gitmodules` or use `gh repo clone` with the canonical org |
| Print the drifted file list verbatim on failure so the developer can see exactly what changed | Auto-generate a fix PR (downstream-side auto-repair) — out of scope here | Auto-bump the pinned version on the developer's behalf — that's an intentional act |

## Done looks like

```
$ git -C /mnt/c/Projects/sdlc-framework tag --list "v*"
v1.0.0

$ cat /mnt/c/Projects/tech-dev-agents/.sdlc/PINNED_VERSION
v1.0.0

$ ls /mnt/c/Projects/tech-dev-agents/.sdlc/VERSION 2>&1
ls: cannot access '/mnt/c/Projects/tech-dev-agents/.sdlc/VERSION': No such file or directory

$ gh workflow view sdlc-drift-check.yml --repo hpi-gorillacommerce/tech-dev-agents
sdlc-drift-check  active   .github/workflows/sdlc-drift-check.yml

$ gh pr checks 999  # demo-tamper PR
sdlc-drift-check    fail    1m23s    https://github.com/hpi-gorillacommerce/tech-dev-agents/actions/runs/...
[from job log:]
ERROR: drift detected against sdlc-framework@v1.0.0
  drifted: .sdlc/skills/spec/SKILL.md
hint: either revert the change or open a PR to sdlc-framework, then bump .sdlc/PINNED_VERSION

$ pytest tests/tools/test_sdlc_drift_check.py -v
test_clean_state_passes ............................................. PASSED
test_tampered_skill_detected ........................................ PASSED
test_excluded_paths_ignored ......................................... PASSED
test_unknown_pinned_version_clear_error ............................. PASSED

4 passed in 1.8s
```

## Escalation contract

Standard 60s directive guard. Escalate to Mark via `needs_info` when:

1. The `v1.0.0` tag on `sdlc-framework` has not yet been created when this story starts Phase 8 — block on the coordination PR; do not invent a placeholder tag.
2. `.sdlc/CLAUDE.md.local` or any per-project override file exists in this repo today — confirm it should be added to the exclusion list (don't silently exclude things that should be tracked).
3. Any existing `.sdlc/` file in `tech-dev-agents` already drifts from `sdlc-framework@HEAD` — escalate; we need to know whether to revert the drift in this repo or accept that the v1.0.0 baseline includes those local edits.
4. A user requests "auto-fix drift" capability — out of scope; escalate for follow-up story.


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
- Phase 6: `feature-spec.md` covering the pin contract, the workflow shape, the exclusion list, the helper module API, and the coordination PR shape.
- Phase 7: RED tests in `tests/tools/test_sdlc_drift_check.py` (the 4 cases above) + a workflow dry-run via `act` (or document why we can't).
- Phase 8: implementation; ensure the coordination PR (sdlc-framework v1.0.0 tag + CHANGELOG) is merged first; push, open the drift-demo PR to demonstrate SC-5, push the bump-fix PR to demonstrate SC-6, then submit the main PR with the workflow + helper.
- Done: PR merged, drift check workflow alive, demo PRs visible in the audit trail.

## Dependencies & sequencing notes

- **Coordination PR comes first:** `sdlc-framework@v1.0.0` tag + `CHANGELOG.md` must merge before this story's Phase 8 lands. Otherwise the workflow can't find the tag and fails on first run.
- **Required by STORY-1012:** the canon-pin footer that STORY-1012 injects into PR bodies reads `.sdlc/PINNED_VERSION`. STORY-1012 has a documented fallback (env var) if this story hasn't shipped, but cleanly STORY-1011 ships first.
- **No dependency on STORY-1009 or STORY-1010** — orthogonal CI infrastructure.

## Phase 6 — design deliverable breakdown

`features/story-1011-sdlc-framework-versioning/feature-spec.md` will include:

1. **Pin contract** — exact format of `.sdlc/PINNED_VERSION` (one line, `vX.Y.Z`, trailing newline). Decision rationale for deleting `.sdlc/VERSION`.
2. **Workflow shape** — full YAML of `.github/workflows/sdlc-drift-check.yml`. Trigger paths, permissions block (read-only access to `sdlc-framework`), Python setup, helper invocation, failure annotation format.
3. **Helper module API** — `sdlc_drift_check.run(local_sdlc_path: Path, pinned_version: str, *, exclude: Iterable[str]) -> list[Drift]` with a typed `Drift(path: str, kind: Literal["modified","added","removed"], local_sha: str, pinned_sha: str)` return.
4. **Exclusion list** — exactly three paths: `.git/`, `PINNED_VERSION`, `CLAUDE.md.local` (the latter optional, included for future-proofing per-project overrides).
5. **Error UX** — exact text for the four failure modes: drift detected, tag not found, network failure cloning framework, permission error.
6. **Coordination PR scope** — minimal: tag + CHANGELOG entry only; do NOT change framework content in that PR.

## Implementation watch-points

- The workflow runs against PR HEAD, not merge-commit. If `.sdlc/PINNED_VERSION` and `.sdlc/skills/x.md` change in the same PR (legitimate framework bump), the drift check should still pass — that's why the check uses the *new* pinned version, not the base-branch version.
- Submodule clone behaviour in GitHub Actions: ensure `actions/checkout@v4` with `submodules: recursive` and `fetch-depth: 0`, or skip submodule init and clone framework directly. Phase 6 picks one.
- Run time budget: cloning `sdlc-framework` at a tag + byte-diff against the local `.sdlc` should finish in under 30 seconds. If it goes over, cache the framework clone.

## Cross-references — evidence cited in this seed

- Current pin state (the disconnect this story fixes):
  - `/mnt/c/Projects/tech-dev-agents/.sdlc/VERSION` — single line `1.0.0`. Read by nothing. Verified via `cat` during seed authoring.
  - `/mnt/c/Projects/sdlc-framework/` — no `CHANGELOG.md`; `tag --list "v1.0.0"` returns empty per spot-check on 2026-05-18.
- Existing CI surface (this workflow joins the set):
  - `/mnt/c/Projects/tech-dev-agents/.github/workflows/contract-critical.yml`
  - `/mnt/c/Projects/tech-dev-agents/.github/workflows/deploy-ops-console.yml`
  - `/mnt/c/Projects/tech-dev-agents/.github/workflows/migration-invariant.yml`
  - `/mnt/c/Projects/tech-dev-agents/.github/workflows/test.yml`
  - All four are unchanged by this story; the new file is `sdlc-drift-check.yml`.
- Framework content (what gets byte-diffed):
  - `/mnt/c/Projects/sdlc-framework/skills/`, `agents/`, `templates/`, `software-development-guidance.md`, `AGENTS.md`, `CLAUDE.md`, `CODEX.md`, `GEMINI.md` — all mirrored under `tech-dev-agents/.sdlc/`.
- Prior-art model:
  - `gc-data-v2/canon-drift-check.yml` — the data team's working implementation of the same pattern (referenced in epic seed line 22). Not a file in this repo; treated as a design reference only.
- Epic seed: `/mnt/c/Projects/tech-dev-agents/features/story-1000-cross-repo-canon-alignment-epic/seed.md` — SC-8 (line 90) and Files-to-modify section (line 147) name this workflow.
