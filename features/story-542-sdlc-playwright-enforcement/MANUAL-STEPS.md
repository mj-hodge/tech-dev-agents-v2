# Manual Steps — STORY-542 Skill Patch Application

These steps apply the Playwright enforcement language to the `.sdlc` submodule.
Apply after the STORY-542 PR merges to `tech-dev-agents`.

## Prerequisites

- You have write access to the `.sdlc` submodule repository
- You are on `main` in the `.sdlc` directory

## Step 1: Apply the three skill patches

From the root of `tech-dev-agents`:

```bash
git -C .sdlc apply features/story-542-sdlc-playwright-enforcement/skills/phase-1/SKILL.md.patch
git -C .sdlc apply features/story-542-sdlc-playwright-enforcement/skills/phase-7/SKILL.md.patch
git -C .sdlc apply features/story-542-sdlc-playwright-enforcement/skills/phase-8/SKILL.md.patch
```

If `git apply` fails (patch already applied or conflict), inspect manually and add the
text blocks by hand.

## Step 2: Commit and push the submodule

```bash
git -C .sdlc add skills/phase-1/SKILL.md skills/phase-7/SKILL.md skills/phase-8/SKILL.md
git -C .sdlc commit -m "feat(sdlc): add Playwright enforcement language (STORY-542)"
git -C .sdlc push origin main
```

## Step 3: Bump the submodule pointer in tech-dev-agents

```bash
git add .sdlc
git commit -m "chore: bump .sdlc submodule — Playwright enforcement (STORY-542)"
git push origin main
```

## Step 4: Replicate to other repos

For each project that uses `.sdlc` as a submodule (e.g., `advertising-amazon`):

```bash
cd /path/to/advertising-amazon
git submodule update --remote .sdlc
git add .sdlc
git commit -m "chore: bump .sdlc submodule — Playwright enforcement (STORY-542)"
git push origin main
```

## Step 5: Verify

1. Open a test PR that touches `frontend/src/components/AgentCard.tsx` but NOT `e2e/`.
   - The Acceptance Diff gate should refuse to mark complete with:
     `frontend story missing Playwright spec — add e2e/<feature>.spec.ts to Acceptance Diff.`
   - The CI `playwright-frontend-tests` job should run (but fail with no e2e/ if not present).

2. Open a PR that touches both `frontend/src/components/AgentCard.tsx` AND `e2e/dashboard.spec.ts`.
   - All gates pass.
   - CI runs Playwright smoke tests.

3. Open a pure backend PR (no `.tsx`/`.css`/`.html` files).
   - The `playwright-frontend-tests` CI job should skip (detect step outputs `is_frontend=false`).
