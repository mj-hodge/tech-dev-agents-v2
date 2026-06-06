# Backlog

This directory holds the **product backlog** — all work not yet assigned to a sprint.

## Structure

```
backlog/
  product-backlog.md    # Prioritized list of all groomed and ungroomed items
  epics/
    epic-XXX-slug.md    # Epic definition files
```

## Workflow

1. **Inbox → Groomed:** Morpheus takes raw requests, asks The Architect to run Phase 1 (seed), then writes a full story with acceptance criteria. Story moves to "Ready".
2. **Ready → Sprint:** At sprint planning, Skynet and the operator pull Ready stories into the current sprint's `backlog/` folder.
3. **Sprint → Archived:** When a story's PR is merged, Morpheus moves it to the Archived table in `product-backlog.md`.

## Epic Definitions

Each epic file (`epics/epic-XXX-slug.md`) contains:
- Problem statement and business goal
- Constituent stories (linked)
- Success criteria
- Estimated scope and timeline
