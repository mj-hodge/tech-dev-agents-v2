# STORY-XXX: [Verb + Noun Title]

> **Copy this file and rename it** `story-XXX-slug.md` when writing a real story.
> Delete this template file from the sprint backlog once the sprint starts.

---

| Field | Value |
|-------|-------|
| **Story ID** | STORY-XXX |
| **Scope** | Small \| Medium \| Large \| New |
| **Epic** | *(epic name or None)* |
| **Sprint** | sprint-01 |
| **Assigned To** | *(agent name)* |
| **Status** | Ready \| In Progress \| Blocked \| Complete |
| **Depends On** | *(STORY-YYY or None)* |

## Problem Statement

*(1-3 sentences: what problem does this solve and why does it matter?)*

## Acceptance Criteria

- [ ] *(Specific, testable criterion — Agent Smith must be able to verify each one)*
- [ ] *(Another criterion)*
- [ ] *(Edge case: what happens when X is null / missing / malformed?)*

## Input Parameters

> **Morpheus fills this section.** Agent Smith uses it to derive Tier 2 boundary tests.
> List every input the story touches — API fields, function arguments, form inputs, config values.

| Parameter | Type | Constraints | Nullable? |
|-----------|------|-------------|-----------|
| `field_name` | string | max 255 chars, valid email format | No |
| `field_name` | integer | 1–1000 | Yes |

## Error Paths

> List every error condition the spec defines. Smith writes a test for each one.

| Condition | Expected Response |
|-----------|------------------|
| *(e.g., email is malformed)* | 400 Bad Request — "Invalid email format" |

## Auth / Permission Rules

> Does this story involve auth? List the roles and their access.

| Role | Access |
|------|--------|
| *(e.g., unauthenticated)* | 401 Unauthorized |
| *(e.g., viewer role)* | 403 Forbidden |
| *(e.g., admin role)* | 200 OK |

## Implementation Notes

*(Key constraints, patterns to follow, files likely to be touched. Point to
`sprints/sprint-XX/features/story-XXX-slug/implementation-plan.md` for full detail once
The Architect completes Phase 6.)*

## Out of Scope

*(What is explicitly NOT included in this story — prevents scope creep)*

## SDLC Deliverables

All phase output lives in `sprints/sprint-01/features/story-XXX-slug/`:

| Phase | File | Status |
|-------|------|--------|
| 1 — Seed | `seed.md` | — |
| 6 — Design | `specification.md`, `implementation-plan.md` | — |
| 7 — Tests | `test-design.md` | — |
| 8 — Impl | *(code + PR)* | — |
| 8b — Review | `code-review.md` | — |
