# STORY-540 Test Design — `/research` Slash-Command Skill + Morris Delegation Rule

**Scope:** Small
**Coverage target:** 50%+ (content-validation tests on skill markdown files)
**Test file:** `tests/skills/test_research_skill_contract.py`

## Test Structure

```
tests/
└── skills/
    ├── __init__.py
    └── test_research_skill_contract.py    # 29 tests across 7 classes
```

## Test Categories

### A. Research Skill Frontmatter (6 tests) — `TestResearchSkillFrontmatter`

| Test | What It Verifies |
|------|------------------|
| `test_research_skill_exists` | SKILL.md file exists at expected path |
| `test_research_skill_has_valid_frontmatter` | Starts with `---`, contains `name: research` |
| `test_research_skill_has_description_field` | Frontmatter has `description:` |
| `test_research_skill_has_usage_section` | Body has `## Usage` with `/research` invocation |
| `test_research_skill_has_steps_section` | Body has `## Steps` for dispatch workflow |
| `test_research_skill_documents_override_options` | Override options documented (custom story_id) |

### B. Story ID Derivation (2 tests) — `TestResearchSkillStoryIdDerivation`

| Test | What It Verifies |
|------|------------------|
| `test_research_skill_derives_next_story_id` | Shell snippet scans `features/` for highest STORY-NNN |
| `test_research_skill_increments_story_id` | Logic increments found ID by 1 |

### C. Scope + Prompt Contract (5 tests) — `TestResearchSkillScopeContract`

| Test | What It Verifies |
|------|------------------|
| `test_research_skill_uses_scope_research` | POST body carries `scope=research` |
| `test_research_skill_posts_to_dispatch_api` | References `/api/dispatch` endpoint |
| `test_research_skill_uses_ops_console_api_key` | References `OPS_CONSOLE_API_KEY` env var |
| `test_research_skill_does_not_create_seed` | No instructions to create seed.md |
| `test_research_skill_sends_question_as_prompt` | Question goes into `prompt` field |

### D. PM Skill Patch (5 tests) — `TestPmSkillPatch`

| Test | What It Verifies |
|------|------------------|
| `test_pm_patch_exists` | Patch file exists |
| `test_pm_skill_patch_adds_research_delegation` | Contains `## Research Delegation` |
| `test_pm_patch_mentions_dispatch_via_research` | Mentions `dispatch via /research` |
| `test_pm_patch_mentions_10_turns_threshold` | References `~10 turns` threshold |
| `test_pm_patch_applies_cleanly` | `git apply --check` succeeds against upstream |

### E. Review-PRs Skill Patch (3 tests) — `TestReviewPrsSkillPatch`

| Test | What It Verifies |
|------|------------------|
| `test_review_prs_patch_exists` | Patch file exists |
| `test_review_prs_patch_has_research_delegation` | Contains `## Research Delegation` |
| `test_review_prs_patch_applies_cleanly` | `git apply --check` succeeds against upstream |

### F. Manual Steps Handback (6 tests) — `TestManualSteps`

| Test | What It Verifies |
|------|------------------|
| `test_manual_steps_exists` | MANUAL-STEPS.md exists |
| `test_manual_steps_has_copy_instruction` | Copy research skill to `.sdlc/skills/research/` |
| `test_manual_steps_has_git_submodule_reference` | References `git -C .sdlc` |
| `test_manual_steps_mentions_submodule` | Mentions `submodule` |
| `test_manual_steps_has_five_numbered_steps` | At least 5 numbered steps |
| `test_manual_steps_has_deploy_instruction` | References `push-code.sh` or deploy |

### G. Folder Slug + URL (2 tests) — `TestResearchSkillFolderSlugDerivation`

| Test | What It Verifies |
|------|------------------|
| `test_research_skill_has_slug_derivation` | Describes folder-slug derivation from question |
| `test_research_skill_uses_ops_console_url_env` | References `OPS_CONSOLE_URL` env var |

## RED State Summary

- **4 FAIL** — existence checks for files not yet created
- **25 SKIP** — content checks that depend on existence (will run after Phase 8)
- **0 ERROR** — all tests import and collect cleanly

## Must-Contain Token Cross-Reference

| File | Required Token | Test |
|------|---------------|------|
| `skills/research/SKILL.md` | `name: research` | `test_research_skill_has_valid_frontmatter` |
| `skills/research/SKILL.md` | `/research` | `test_research_skill_has_usage_section` |
| `skills/research/SKILL.md` | `scope=research` | `test_research_skill_uses_scope_research` |
| `skills/research/SKILL.md` | `/api/dispatch` | `test_research_skill_posts_to_dispatch_api` |
| `skills/research/SKILL.md` | `OPS_CONSOLE_API_KEY` | `test_research_skill_uses_ops_console_api_key` |
| `skills/pm/SKILL.md.patch` | `## Research Delegation` | `test_pm_skill_patch_adds_research_delegation` |
| `skills/pm/SKILL.md.patch` | `dispatch via /research` | `test_pm_patch_mentions_dispatch_via_research` |
| `skills/pm/SKILL.md.patch` | `> ~10 turns` | `test_pm_patch_mentions_10_turns_threshold` |
| `skills/review-prs/SKILL.md.patch` | `## Research Delegation` | `test_review_prs_patch_has_research_delegation` |
| `MANUAL-STEPS.md` | `copy skills/research/SKILL.md to .sdlc/skills/research/SKILL.md` | `test_manual_steps_has_copy_instruction` |
| `MANUAL-STEPS.md` | `git -C .sdlc` | `test_manual_steps_has_git_submodule_reference` |
| `MANUAL-STEPS.md` | `submodule` | `test_manual_steps_mentions_submodule` |

## Notes for Phase 8 Implementer

- All deliverables go into `features/story-540-research-skill/` — never into `.sdlc/` directly (that's a submodule Mark copies into manually)
- The research skill is markdown-only — no Python runtime code. Claude reads it at `/research` invocation time
- Use `$OPS_CONSOLE_URL` and `$OPS_CONSOLE_API_KEY` from environment, matching the existing dispatch skill pattern
- Patches must be unified-diff format that `git apply` can consume
- The folder-slug derivation should lowercase first 5-7 words, hyphenate, trim to ~40 chars
