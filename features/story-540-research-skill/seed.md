# STORY-540 — `/research` Slash-Command Skill + Morris Delegation Rule

**Scope:** small
**Repo:** tech-dev-agents
**Target Branch:** main

**Depends on:** STORY-539 (research dispatch scope) — must be merged first. The skill dispatches via `scope=research`, which the queue only accepts after 539 lands.

## Context

Once STORY-539 ships, the research-dispatch mechanics work — but using them requires hand-crafting a `curl` payload with the right JSON body, scope, enqueued_by, and a made-up story_id. That's too much friction to actually change anyone's behavior. The real unlock is a single slash-command.

Two users for this skill:

1. **Mark** — types `/research "<question>"` in his Claude Code session when he wants to explore something without burning his own session quota. The skill picks the next story_id, POSTs to `/dispatch` with `scope=research`, and shows the confirmation.
2. **Morris** — his manager skills gain a decision rule: "if this request would be pure research (survey, audit, tradeoff analysis, codebase exploration) and estimated > ~10 turns, stop and dispatch via `/research` instead of doing it in-session." Saves Morris's quota for PR review.

The skill itself is identical for both users. The delegation rule for Morris is a prompt-engineering addition to his existing `pm` and `review-prs` skills, not new code.

## Acceptance Diff

The implementation MUST produce these files with the must-contain tokens below. Because `.sdlc/skills/` is a cross-org private submodule the dispatched agent cannot push to directly, the agent writes the skill files into the feature folder AND adds a "Manual Steps (Mark)" handback block. Mark copies them into the submodule as a one-time manual step.

- `features/story-540-research-skill/skills/research/SKILL.md` — must-contain `name: research`, must-contain `/research`, must-contain `scope=research`, must-contain `/api/dispatch`, must-contain `OPS_CONSOLE_API_KEY`
- `features/story-540-research-skill/skills/pm/SKILL.md.patch` — must-contain `## Research Delegation`, must-contain `dispatch via /research`, must-contain `> ~10 turns`. (A unified-diff patch against `.sdlc/skills/pm/SKILL.md` — Mark applies it manually when he copies the files in.)
- `features/story-540-research-skill/skills/review-prs/SKILL.md.patch` — must-contain `## Research Delegation`. (Same treatment — the review-prs skill gets the same delegation rule so Morris doesn't do research in the middle of a PR review.)
- `features/story-540-research-skill/MANUAL-STEPS.md` — must-contain `copy skills/research/SKILL.md to .sdlc/skills/research/SKILL.md`, must-contain `git -C .sdlc`, must-contain `submodule`
- `tests/skills/test_research_skill_contract.py` — must-contain `def test_research_skill_has_valid_frontmatter`, must-contain `def test_research_skill_uses_scope_research`, must-contain `def test_research_skill_derives_next_story_id`, must-contain `def test_pm_skill_patch_adds_research_delegation`

## Test Criteria

Every assertion below must have a pytest-level test that fails RED before implementation and passes GREEN after.

1. **Research skill file is well-formed**
   - `skills/research/SKILL.md` starts with YAML frontmatter containing `name: research` and a `description:` field.
   - Body includes a `## Usage` section showing `/research "<question>"` and `/research --story-id=STORY-N "<question>"` forms.
   - Body includes a `## Steps` section listing: (a) derive story_id, (b) derive story folder slug from question, (c) construct POST body with `scope="research"` and the question as `prompt`, (d) call `curl` to `$OPS_CONSOLE_URL/api/dispatch`, (e) display the HTTP 201 response + which agent claims it on the next tick.
   - Body documents how to override defaults (custom story_id, target specific agent via `target_agent`, custom output path).

2. **Story ID derivation**
   - The skill's steps MUST tell Claude to read `features/` to find the highest existing `STORY-NNN` folder and use NNN+1 as the default. Test: a fixture of `features/story-001/` through `features/story-540/` folders → the skill's logic proposes STORY-541. (This is tested by having the skill content include a clear "derive next ID" step with a shell snippet or instruction Claude can execute.)

3. **Scope + prompt contract**
   - The POST body the skill constructs MUST carry `"scope": "research"` — grep-level assertion in the skill file.
   - The user's question goes verbatim into `prompt`.
   - The skill must NOT construct a `seed.md` file — research scope explicitly skips the seed (per STORY-539 fix #4).

4. **Morris delegation rule**
   - The `skills/pm/SKILL.md.patch` file adds a `## Research Delegation` section that (a) defines research as "pure information retrieval, audit, tradeoff analysis, no code change committed," (b) sets the threshold at `> ~10 turns expected` (Morris estimates), (c) instructs him to stop and invoke `/research <question>` instead of proceeding.
   - The `skills/review-prs/SKILL.md.patch` file adds the same section, scoped to "research tangents that arise mid-review — dispatch them via `/research` and keep the review tight."
   - Both patches apply cleanly against the current upstream files — test runs `git apply --check` against a scratch copy.

5. **Manual steps handback**
   - `MANUAL-STEPS.md` contains step-by-step instructions for Mark:
     a. `cp -r features/story-540-research-skill/skills/research /path/to/.sdlc/skills/research`
     b. `cd .sdlc && git apply` the two `.patch` files
     c. `git -C .sdlc add . && git -C .sdlc commit -m '...' && git -C .sdlc push`
     d. `git -C /path/to/tech-dev-agents add .sdlc && git commit -m 'bump .sdlc submodule'`
     e. Deploy to agent VMs via `./deployment/vm/push-code.sh all` (so their `.claude/skills/` symlink resolves the new skill).
   - Tests validate the MANUAL-STEPS.md has all five steps numbered.

## Validation

Once the story ships and Mark completes the manual steps:

1. In a fresh Claude Code session on Mark's laptop:
   ```
   /research "How does our completion gate handle the case where the agent created the deliverable at a different path than the seed specified? STORY-538 hit this today — document the current behavior and what a robust gate would do. Write to features/story-541-…/research.md."
   ```
   Claude reads `.claude/skills/research/SKILL.md`, derives STORY-541, POSTs to `/dispatch`, shows the 201 response.

2. Within 60 seconds, daisy or devon claims STORY-541 with `scope=research`. Loki shows `[DISPATCH] Starting SDLC phases for STORY-541 (scope=research, 1 phase)`.

3. 2-3 minutes later, `features/story-541-…/research.md` exists on branch `story-541/story-541`. No PR is created (per 539 behavior). Teams notifies Mark with the branch link.

4. Test Morris's delegation: ask Morris (in a Teams conversation) "survey every place we parse a rate-limit reset string across the codebase, list the inconsistencies, recommend a unified parser." Morris's `pm` skill now carries the delegation rule — he estimates ~20 turns, invokes `/research` instead of doing it himself, shows Mark the dispatch. His token spend for that request drops from ~200K to ~5K (the dispatch call itself).

## Implementation Notes for the SDK Agent

- The skill file's content is what Claude reads at runtime when a user types `/research`. It's markdown-only — no Python, no binary. Write clear numbered steps; Claude follows them.
- The skill must use `$OPS_CONSOLE_URL` and `$OPS_CONSOLE_API_KEY` from environment (or config file). Do NOT hardcode the URL or key. Reference the existing `dispatch` skill for how it handles this.
- For the story-id-derivation step: use a shell snippet like `ls features/ | grep -E '^story-[0-9]+' | sort -V | tail -1` and increment. Include the snippet in the skill.
- For the folder-slug derivation: lowercase the first 5-7 words of the question, hyphenate, trim to 40 chars. Include an example in the skill.
- Morris's `pm` and `review-prs` skill patches should be minimal additions — just the `## Research Delegation` section, nothing else. Do NOT refactor existing skill content.
- The `MANUAL-STEPS.md` file should be checklist-friendly so Mark can tick items as he goes. Include rollback instructions in case a step fails.

Phase 7 writes the tests (RED). Phase 8 implements (GREEN). No deploy is required — skills are content, not runtime code. Mark's manual submodule step completes the deploy.

## Manual Steps (Mark)

Placeholder — the dispatched agent MUST write the actual `MANUAL-STEPS.md` in the feature folder. The steps above describe the contract the agent must meet; Mark executes them once he reviews the PR.
