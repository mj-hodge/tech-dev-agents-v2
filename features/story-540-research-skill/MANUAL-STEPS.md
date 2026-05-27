# STORY-540: Manual Steps for Mark

These steps copy the `/research` skill and Morris delegation patches into the `.sdlc` submodule and deploy to agent VMs.

## Prerequisites

- STORY-539 (research dispatch scope) must be merged first
- You must have push access to the `.sdlc` submodule remote

## Steps

1. **Copy skills/research/SKILL.md to .sdlc/skills/research/SKILL.md**

   ```bash
   mkdir -p .sdlc/skills/research
   cp features/story-540-research-skill/skills/research/SKILL.md .sdlc/skills/research/SKILL.md
   ```

2. **Apply the PM skill patch**

   ```bash
   cd .sdlc && git apply ../features/story-540-research-skill/skills/pm/SKILL.md.patch
   ```

   Verify: `grep "Research Delegation" .sdlc/skills/pm/SKILL.md` should show the new section.

3. **Apply the review-prs skill patch**

   ```bash
   cd .sdlc && git apply ../features/story-540-research-skill/skills/review-prs/SKILL.md.patch
   ```

   Verify: `grep "Research Delegation" .sdlc/skills/review-prs/SKILL.md` should show the new section.

4. **Commit and push inside the .sdlc submodule**

   ```bash
   git -C .sdlc add skills/research/SKILL.md skills/pm/SKILL.md skills/review-prs/SKILL.md
   git -C .sdlc commit -m 'feat: add /research skill + Morris delegation rules (STORY-540)'
   git -C .sdlc push
   ```

5. **Bump the submodule reference in tech-dev-agents and deploy**

   ```bash
   git add .sdlc
   git commit -m 'bump .sdlc submodule: /research skill (STORY-540)'
   git push
   ./deployment/vm/push-code.sh all
   ```

   This deploys to all agent VMs so their `.claude/skills/` symlink resolves the new `/research` skill.

## Verification

After deploying, test in a fresh Claude Code session:

```
/research "What are the tradeoffs between polling and webhooks for Grafana alerts?"
```

Expected: Claude reads the skill, derives the next STORY-ID, POSTs to `/api/dispatch`, shows the 201 confirmation.

## Rollback

If any step fails:

- **Patch conflict:** Manually add the `## Research Delegation` section to the end of the affected SKILL.md file. The patch content is in `features/story-540-research-skill/skills/pm/SKILL.md.patch` and `features/story-540-research-skill/skills/review-prs/SKILL.md.patch`.
- **Submodule push fails:** `git -C .sdlc reset HEAD~1 && git -C .sdlc checkout -- .` to undo local changes.
- **Deploy fails:** The skill is content-only — no runtime impact. Re-run `./deployment/vm/push-code.sh all` after fixing.
