# Sprints

All active and completed sprint work lives here. Each sprint gets its own numbered folder.

## Folder Naming

`sprint-XX` where XX is zero-padded (sprint-01, sprint-02, ..., sprint-10, sprint-11, ...)

## Sprint Structure

```
sprints/
  sprint-01/
    sprint.md              # Sprint overview — goal, dates, team, capacity
    backlog/               # Stories Morpheus has written for this sprint
      story-XXX-slug.md    # Story definition with acceptance criteria
      story-YYY-slug.md
    features/              # SDLC deliverables — one subfolder per story
      story-XXX-slug/
        seed.md            # Phase 1
        research.md        # Phase 2
        specification.md   # Phase 6
        implementation-plan.md
        test-design.md     # Phase 7
        code-review.md     # Phase 8b
      story-YYY-slug/
        ...
    ceremonies/
      planning.md          # Sprint planning notes and commitments
      review.md            # Sprint review / demo outcomes
      retro.md             # Sprint retrospective
```

## Agent Responsibilities

| Agent | Works In |
|-------|---------|
| Morpheus | `sprint-XX/backlog/` — writes and owns story files |
| The Architect | `sprint-XX/features/story-slug/` — phases 1-6 deliverables |
| Neo | `sprint-XX/features/story-slug/` — phases 7-8 deliverables + code |
| Agent Smith | `sprint-XX/features/story-slug/code-review.md` |
| Skynet | `sprint-XX/ceremonies/` — standup summaries, retro facilitation |
