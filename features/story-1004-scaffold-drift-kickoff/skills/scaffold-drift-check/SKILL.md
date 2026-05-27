---
name: scaffold-drift-check
description: >
  Check whether a repo's SDLC scaffold has drifted from canonical state. Runs as a
  pre-Phase-6 gate: verifies CLAUDE.md, AGENTS.md, CODEX.md, GEMINI.md, config.yaml,
  .sdlc submodule, backlog.md, and .project all exist and have required structure.
  Use before starting Phase 6 design work, or any time you suspect a repo is stale.
triggers:
  - scaffold drift
  - canon drift
  - check scaffold
  - pre-phase-6 gate
  - drift check
  - scaffold check
---

# scaffold-drift-check — Scaffold Drift Gate

Verify this repo's SDLC scaffold is aligned with canon before entering Phase 6 design.
Catches missing config keys, broken symlinks, and stale `.sdlc` pointers that would
block a Phase 8 implementation.

## When to run

Run this skill at the start of every Phase 6 session (or any time a repo feels stale).

> **Phase 6 workers:** run `/scaffold-drift-check` before reading `seed.md`. If any
> item is FAIL or FIX, pause and resolve it before proceeding with design.

## Checks

| # | Item | Pass condition |
|---|------|---------------|
| 1 | `CLAUDE.md` present | File exists at repo root |
| 2 | `CLAUDE.md` has required sections | Contains "## SDLC Process" or "## Skill Discipline" |
| 3 | `AGENTS.md` present and readable | File exists (or symlink resolves) |
| 4 | `CODEX.md` present | File exists at repo root |
| 5 | `GEMINI.md` present | File exists at repo root |
| 6 | `config.yaml` — `project.name` field | `project.name:` present in config.yaml |
| 7 | `config.yaml` — `task_tracker.platform` field | `task_tracker.platform:` present |
| 8 | `.sdlc` submodule initialized | `.sdlc/skills/phase-1/SKILL.md` exists |
| 9 | `backlog.md` present | File exists at repo root |
| 10 | `.project` present | File exists at repo root |
| 11 | Git initialized | `.git/` directory exists |
| 12 | On a non-default branch | Current branch is not `main` or `master` (for feature work) |

## Steps

1. **Run each check** against the current working directory (repo root). For each item
   in the Checks table above:

   ```bash
   # Check 1 — CLAUDE.md present
   test -f CLAUDE.md && echo "PASS: CLAUDE.md present" || echo "FAIL: CLAUDE.md missing"

   # Check 2 — CLAUDE.md has required sections
   grep -q "SDLC Process\|Skill Discipline" CLAUDE.md \
     && echo "PASS: CLAUDE.md has required sections" \
     || echo "FIX: CLAUDE.md missing ## SDLC Process or ## Skill Discipline"

   # Check 3 — AGENTS.md readable
   test -r AGENTS.md && echo "PASS: AGENTS.md readable" \
     || echo "FAIL: AGENTS.md missing or symlink broken"

   # Check 4 — CODEX.md present
   test -f CODEX.md && echo "PASS: CODEX.md present" || echo "FAIL: CODEX.md missing"

   # Check 5 — GEMINI.md present
   test -f GEMINI.md && echo "PASS: GEMINI.md present" || echo "FAIL: GEMINI.md missing"

   # Check 6 — config.yaml project.name
   grep -q "project.name\|name:" config.yaml \
     && echo "PASS: config.yaml has project.name" \
     || echo "FAIL: config.yaml missing project.name"

   # Check 7 — config.yaml task_tracker.platform
   grep -q "task_tracker" config.yaml \
     && echo "PASS: config.yaml has task_tracker.platform" \
     || echo "FAIL: config.yaml missing task_tracker.platform"

   # Check 8 — .sdlc submodule initialized
   test -f .sdlc/skills/phase-1/SKILL.md \
     && echo "PASS: .sdlc submodule initialized" \
     || echo "FIX: .sdlc submodule not initialized — run: git submodule update --init --recursive"

   # Check 9 — backlog.md present
   test -f backlog.md && echo "PASS: backlog.md present" || echo "FAIL: backlog.md missing"

   # Check 10 — .project present
   test -f .project && echo "PASS: .project present" || echo "FAIL: .project missing"

   # Check 11 — git initialized
   test -d .git && echo "PASS: git initialized" || echo "FAIL: not a git repository"

   # Check 12 — branch
   BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
   echo "INFO: current branch = $BRANCH"
   ```

2. **Collect results** — count PASS items and FAIL/FIX items.

3. **Report summary** in this format:

   ```
   ── scaffold-drift-check ──────────────────────
   PASS   CLAUDE.md present
   PASS   CLAUDE.md has required sections
   PASS   AGENTS.md readable
   FAIL   CODEX.md missing
   FAIL   GEMINI.md missing
   PASS   config.yaml project.name
   PASS   config.yaml task_tracker.platform
   FIX    .sdlc submodule not initialized
   PASS   backlog.md present
   PASS   .project present
   PASS   git initialized
   INFO   branch = story-1004/scaffold-drift-kickoff

   Result: 8 PASS · 2 FAIL · 1 FIX
   ─────────────────────────────────────────────
   ```

4. **Gate decision:**
   - **All PASS:** Proceed to Phase 6 design.
   - **Any FAIL or FIX:** Stop. Fix each failing item before proceeding.

## Fixing common issues

| Issue | Fix |
|-------|-----|
| CLAUDE.md missing sections | Add `## SDLC Process` and `## Skill Discipline` from the SDLC template |
| AGENTS.md symlink broken | `ln -sf .sdlc/AGENTS.md AGENTS.md` (if using symlink pattern) |
| CODEX.md / GEMINI.md missing | Copy from `.sdlc/templates/` or the canonical repo skeleton |
| config.yaml missing fields | Add `project.name:` and `task_tracker.platform:` under their parent keys |
| .sdlc not initialized | `git submodule update --init --recursive` |
| backlog.md / .project missing | Copy from `.sdlc/templates/` — these are required SDLC tracking files |

## Notes

- This skill is a **pre-Phase-6 gate only**. It does not mutate any files.
- For Phase 8 or later, a stale scaffold is usually non-blocking — note it and proceed.
- If CODEX.md or GEMINI.md are intentionally absent (non-AI-integrated repo), mark
  those items as N/A and continue.
