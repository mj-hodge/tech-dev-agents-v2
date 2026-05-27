# STORY-322 — Cole the Curator Skill (retry of STORY-304)

**Scope:** Medium
**Phase path:** `1 → 4 → 6 → 7 → 8 → 8b → Done`
**Type:** Feature — skill definition + supporting code + tests
**Depends on:** STORY-303 (schema migration)
**Blocks:** STORY-305 (Q&A delivery), STORY-306 (first curation run)

## Goal

Add a `curator` skill to Morris's persona. Morris-acting-as-Cole owns curation
of `tech-gc-knowledgebase/wiki/` from `scratch/` and `sources/`. Cole is a
personality mode, not a new agent — reuses Morris's VM, identity, and skill loader.

## Deliverables

1. `deployment/vm/skills/morris/curator/SKILL.md` — full skill definition
2. Curation plan JSON schema documented in SKILL.md
3. `SOUL-morris.md` updated with curator skill reference
4. Unit tests: `build_curation_plan()`, `format_questions_for_teams()`
5. Mocked integration test: scratch + sources → curation plan JSON

## Non-goals

- Teams Q&A delivery (STORY-305)
- First curation run (STORY-306)
- Changes to Morris's existing skills

## Source

See `features/story-304-cole-curator-skill/seed.md` for the original seed.
