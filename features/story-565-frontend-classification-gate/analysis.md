# Analysis: STORY-565 — Frontend Classification + Playwright @smoke Gate

**Phase:** 4 (Analysis)
**Scope:** Medium
**Date:** 2026-04-24

---

## 1. Source-of-Truth Decision

The central design question is where the `frontend` classification lives. Three options evaluated:

### Option A: Parse `Frontend:` from seed.md (SELECTED)

Parse `**Frontend:** true|false` from the seed's Overview table (or a `## Frontend Classification` section) at gate time. The phase runner already has the worktree and reads seed.md for Acceptance Diff verification (`_read_seed_for_story`, line 1318).

**Pros:**
- Zero schema change — no migration, no DB column on `dispatch_items`.
- The phase runner already reads seed.md for `_verify_acceptance_diff` (line 1370-1374) and `_is_frontend_story_from_seed_text` (line 1219). Adding one more regex pass is trivial.
- Consistent with the existing grandfathering pattern: `_git_first_commit_timestamp` in `test_sdlc_framework_compliance.py` (line 306) handles cutoff enforcement.
- The seed is the single source of truth for story metadata — keeping `frontend` there avoids dual-write between seed and DB.

**Cons:**
- Not queryable/aggregatable from the DB (acceptable for now; a follow-on can add a column).
- Regex parsing is fragile if seed format changes (mitigated: the compliance test catches missing fields).

### Option B: Add `frontend BOOLEAN` column to `dispatch_items`

**Pros:** Clean queryable flag. Type-safe.
**Cons:** Requires a migration (out of scope per seed §5). Requires wiring the flag through dispatch.py → dispatch_db_service.py (both in must-NOT-contain). Dual-write risk with seed.md.

**Rejected:** Explicitly out of scope in seed §5 and must-NOT-contain in §10.

### Option C: Infer frontend-ness from git diff only (STORY-542 heuristic)

The existing `_is_frontend_story_from_seed_text` (line 1219) uses file-signal + keyword-signal heuristics.

**Pros:** No new field needed. Already implemented.
**Cons:** Too broad — catches seeds that mention "dashboard" in prose but aren't UI stories. Doesn't support explicit opt-out (`Frontend: false # rationale`). The whole point of STORY-565 is that heuristic detection misses cases; an explicit declaration is required.

**Rejected:** Heuristic detection is the problem this story solves. Explicit declaration is the fix.

### Decision: Option A — parse from seed.md

---

## 2. Enforcement Point

### Where the gate fires

The gate lives in **`deployment/hermes/sdlc_phase_runner.py`**, specifically in the post-all-phases completion section (after line 2107, alongside `_verify_acceptance_diff`).

**Why the phase runner, not `/complete`:**
- The phase runner has the worktree: `workdir` is a local checkout with the full git history.
- `git diff origin/main --name-only` is already called by `_verify_acceptance_diff` (line 1388-1391).
- Reading file contents (to check for `@smoke` tag) requires `git show` or filesystem access — the phase runner has both.
- The `/complete` endpoint (`dispatch.py`) has no filesystem access and no git worktree. Adding the gate there would require GitHub API calls to fetch file contents (scope creep) and threading authentication.
- The dispatch assignment explicitly places `dispatch.py` and `dispatch_db_service.py` in the must-NOT-contain list.

### Gate placement in `run_sdlc_phases`

```
Line 2085: ok, missing = _verify_acceptance_diff(workdir, story_id)
           ... (existing gate handles failure) ...
Line 2107: print("Acceptance Diff OK")
  >>> NEW: ok, errors = _verify_frontend_gate(workdir, story_id)
  >>>      if not ok: write sidecar, notify, return False
Line 2109: Create PR if one doesn't exist
```

The new `_verify_frontend_gate` runs **after** `_verify_acceptance_diff` passes and **before** the PR creation block. This ordering ensures:
1. The diff is already verified to contain the expected files.
2. We can read `@smoke` tag content from files we know are in the diff.
3. On failure, no PR is created (the story retries with the gate-failure context).

---

## 3. Gate Logic (`_verify_frontend_gate`)

```
def _verify_frontend_gate(workdir: str, story_id: str) -> tuple[bool, list[str]]:
    1. Read seed.md for story_id (reuse _read_seed_for_story)
    2. Parse `**Frontend:** true|false` via regex
       - If field missing: fail-open (the compliance test catches missing fields;
         the gate doesn't block — this handles pre-cutoff grandfathered seeds
         and the Case F regression)
    3. If `frontend: false` (with or without trailing comment): return (True, [])
       - This is the escape hatch (Case E)
    4. If `frontend: true`:
       a. Run `git diff origin/main --name-only` → collect changed files
       b. Filter for e2e/**/*.spec.ts paths (reuse _is_playwright_spec_path)
       c. For each matching spec file, read its content and check for `@smoke`
       d. If any spec contains `@smoke`: return (True, [])
       e. If no spec files at all: return (False, ["...e2e/**/*.spec.ts..."])
       f. If specs exist but none contain `@smoke`: return (False, ["...@smoke..."])
    5. Error message includes: the glob `e2e/**/*.spec.ts`, the `@smoke` tag rule,
       and the story-id for context (SC-7)
```

### Reading file content for `@smoke` check

Two approaches:
- **`git show HEAD:<path>`** — reads the file from the commit, works even if worktree is dirty.
- **Direct filesystem read** — simpler, works because the phase runner always has a clean checkout.

**Decision:** Use `git show HEAD:<path>` via subprocess, consistent with existing `git diff` usage in the module. Fall back to filesystem read if git show fails.

---

## 4. Sidecar Pattern for Gate Failure

On gate failure, write a diagnostic file to the sidecar directory:

```
/home/hermes/state/<agent>/phase-runner-gate-failed/<story_id>.md
```

This follows the existing `/home/hermes/state/<agent>/` pattern (line 358, 519 in sdlc_phase_runner.py). The `whats-next` skill reads `/home/hermes/state/<agent>/*` patterns (per PR note in dispatch), so gate-failure context is automatically surfaced.

Contents: structured markdown with the failure reason, the missing file glob, and remediation steps.

---

## 5. Compliance Test

`test_every_seed_dispatched_after_20260426_declares_frontend_classification` in `tests/test_sdlc_framework_compliance.py`:

- Pattern: identical to existing `test_every_seed_written_after_20260422_has_required_sections` (line 333).
- Cutoff: `2026-04-26T00:00Z` (assignment override; seed said 2026-04-25 but v2 corrected to 2026-04-26).
- Grandfathering: reuse `_git_first_commit_timestamp()` (line 306).
- Detection: regex for `**Frontend:** true|false` OR `## Frontend Classification` section header.

---

## 6. Test Cases (Cases A-F from seed §4)

| Case | Seed Field | Diff State | Expected | Gate Behavior |
|------|-----------|------------|----------|---------------|
| A | `frontend: true` | e2e spec with `@smoke` | PASS | Gate returns (True, []) |
| B | `frontend: true` | No e2e files | FAIL | Gate returns (False, [actionable msg]) |
| C | `frontend: true` | e2e spec WITHOUT `@smoke` | FAIL | Gate returns (False, [msg re @smoke]) |
| D | `frontend: false` | No e2e | PASS | Gate short-circuits at step 3 |
| E | `frontend: false # rationale` | No e2e | PASS | Gate short-circuits (trailing comment preserved) |
| F | No `Frontend:` field (pre-cutoff) | Any | PASS + warning | Gate fail-opens at step 2 |

---

## 7. Files Changed (Expected)

### Must-change:
- `deployment/hermes/sdlc_phase_runner.py` — add `_verify_frontend_gate`, call site after acceptance-diff gate
- `tests/test_sdlc_framework_compliance.py` — add compliance test for `Frontend:` field
- `tests/deployment/test_phase_runner_frontend_gate.py` (new) — RED tests for cases A-F

### Must-NOT-change:
- `tech_dev_agents/ops_console/services/dispatch_db_service.py`
- `tech_dev_agents/ops_console/routes/dispatch.py`
- Any migration under `migrations/` or `alembic/`

### SDLC deliverables:
- `features/story-565-frontend-classification-gate/analysis.md` (this file)
- `features/story-565-frontend-classification-gate/feature-spec.md` (Phase 6)
- `features/story-565-frontend-classification-gate/test-design.md` (Phase 7)

---

## 8. Risk Assessment

| Risk | Mitigation |
|------|-----------|
| Regex fragile if seed format changes | Compliance test catches missing fields; gate fail-opens on parse failure |
| `@smoke` tag in comment/string false-positive | Acceptable — presence of `@smoke` anywhere in a spec file is a strong signal; false positives are benign |
| git subprocess timeout | 15s timeout consistent with existing gates; fail-open on error |
| Pre-cutoff seeds blocked by gate | Gate explicitly fail-opens when `Frontend:` field is missing (Case F) |
| Existing STORY-542 heuristic conflicts | No conflict — STORY-542's `_is_frontend_story_from_seed_text` is heuristic-based and fires in different contexts (Phase 7 gate, Acceptance Diff gate). The new `_verify_frontend_gate` uses the explicit `Frontend:` field and fires only at completion. They are complementary. |
