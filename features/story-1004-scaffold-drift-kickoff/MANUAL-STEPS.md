# STORY-1004: Manual Steps for Mark

These steps copy the `scaffold-drift-check` and `pipeline-kickoff` skills, plus the
`pipeline-kickoff-prompt.md` template, into the `.sdlc` submodule and deploy to agent VMs.

## Prerequisites

- You must have push access to the `.sdlc` submodule remote
- The PR for this story must be merged into `main` first
- Run all commands from the `tech-dev-agents` repo root

## Steps

1. **Copy scaffold-drift-check skill to .sdlc/skills/**

   ```bash
   mkdir -p .sdlc/skills/scaffold-drift-check
   cp features/story-1004-scaffold-drift-kickoff/skills/scaffold-drift-check/SKILL.md \
      .sdlc/skills/scaffold-drift-check/SKILL.md
   ```

   Verify: `cat .sdlc/skills/scaffold-drift-check/SKILL.md | head -5` should show the
   YAML frontmatter with `name: scaffold-drift-check`.

2. **Copy pipeline-kickoff skill to .sdlc/skills/**

   ```bash
   mkdir -p .sdlc/skills/pipeline-kickoff
   cp features/story-1004-scaffold-drift-kickoff/skills/pipeline-kickoff/SKILL.md \
      .sdlc/skills/pipeline-kickoff/SKILL.md
   ```

   Verify: `cat .sdlc/skills/pipeline-kickoff/SKILL.md | head -5` should show the
   YAML frontmatter with `name: pipeline-kickoff`.

3. **Copy pipeline-kickoff-prompt.md template to .sdlc/templates/**

   ```bash
   mkdir -p .sdlc/templates
   cp features/story-1004-scaffold-drift-kickoff/pipeline-kickoff-prompt.md \
      .sdlc/templates/pipeline-kickoff-prompt.md
   ```

   Verify: `ls .sdlc/templates/pipeline-kickoff-prompt.md` should show the file.

4. **Commit and push inside the .sdlc submodule**

   ```bash
   git -C .sdlc add \
     skills/scaffold-drift-check/SKILL.md \
     skills/pipeline-kickoff/SKILL.md \
     templates/pipeline-kickoff-prompt.md
   git -C .sdlc commit -m 'feat: add scaffold-drift-check + pipeline-kickoff skills (STORY-1004)'
   git -C .sdlc push
   ```

   This pushes the new skills into the `.sdlc` submodule remote so all agents pick them up.

5. **Bump the submodule reference in tech-dev-agents and deploy**

   ```bash
   git add .sdlc
   git commit -m 'bump .sdlc submodule: scaffold-drift-check + pipeline-kickoff skills (STORY-1004)'
   git push
   ./deployment/vm/push-code.sh all
   ```

   This updates the submodule pointer in tech-dev-agents so the new `.sdlc` commit is
   tracked, then deploys to all agent VMs so their `.claude/skills/` symlink resolves
   both new skills.

## Verification

After deploying, test in a fresh Claude Code session:

```
/scaffold-drift-check
```

Expected: Claude reads the skill and prints a PASS/FAIL/FIX report for each of the 12
scaffold checks. If the repo is fully aligned, all 12 should show PASS.

```
/pipeline-kickoff "test-pipeline" "shopify"
```

Expected: Claude reads the skill, locates
`tech-project-mapping/catalog/data-sources.yaml`, and either confirms shopify is
registered or shows the YAML block to add.

## Rollback

If any step fails:

- **Submodule push fails:** `git -C .sdlc reset HEAD~1 && git -C .sdlc checkout -- .`
  to undo local changes, then re-check your push access to the submodule remote.
- **Skills not resolving after deploy:** Re-run `./deployment/vm/push-code.sh all` —
  the skills are content-only and have no runtime impact if temporarily absent.
- **Wrong file copied:** Delete the wrong file from `.sdlc/skills/` and repeat the
  relevant cp command. Commit the correction inside `.sdlc` as a follow-up commit.
