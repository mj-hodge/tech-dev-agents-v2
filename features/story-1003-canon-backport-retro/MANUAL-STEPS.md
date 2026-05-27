# STORY-1003: Manual Deployment Steps

These steps deploy the canon-backport skill, retro dual-proposal patch, and phase-9 3-question gate into the `.sdlc` submodule.

**Prerequisites:** PR approved, all tests GREEN.

## Steps

1. Copy the canon-backport skill into the framework submodule:
   ```bash
   cp -r features/story-1003-canon-backport-retro/skills/canon-backport/ .sdlc/skills/canon-backport/
   ```

2. Copy the patched retro skill (dual-proposal flow) into .sdlc/skills:
   ```bash
   cp features/story-1003-canon-backport-retro/skills/retro/SKILL.md .sdlc/skills/retro/SKILL.md
   ```

3. Copy the patched phase-9 skill (3-question gate) into .sdlc/skills:
   ```bash
   cp features/story-1003-canon-backport-retro/skills/phase-9/SKILL.md .sdlc/skills/phase-9/SKILL.md
   ```

4. Copy shared templates:
   ```bash
   cp .sdlc/templates/three-question-gate.md .sdlc/templates/three-question-gate.md
   cp .sdlc/templates/retro-proposal-gc-data-v2.yaml .sdlc/templates/retro-proposal-gc-data-v2.yaml
   ```

5. Commit inside the submodule and bump the pointer:
   ```bash
   git -C .sdlc add skills/canon-backport/ skills/retro/SKILL.md skills/phase-9/SKILL.md templates/
   git -C .sdlc commit -m "feat(STORY-1003): canon-backport skill + retro dual-proposal + phase-9 3-question gate"
   git add .sdlc
   git commit -m "chore: bump .sdlc submodule for STORY-1003"
   ```

6. Verify tests pass after submodule bump:
   ```bash
   python3 -m pytest tests/skills/test_1003_canon_backport_retro.py -v
   python3 -m pytest .sdlc/tests/epic_1000/ -v
   ```
