# Seed

## Overview
| Field | Value |
|-------|-------|
| Mode | feature_update |
| Scope | small |
| Criticality | important |
| Feature Name | review-context-bundler |
| Frontend | false |

## Problem Statement

Morris's PR reviews lack persistent company knowledge — every review session starts cold with no awareness of what systems exist, how processes work, or what reliability patterns have been established. The `review-prs` skill needs a pre-built context bundle injected via `--append-system-prompt-file` so Morris can cite KB pages and patterns in review comments.

## Target User / Use Case

**Morris (engineering manager agent):** During every PR review cycle, Morris loads a pre-built Markdown bundle containing digested wiki pages, SDLC standards, runbook excerpts, recent incident summaries, and recurring knowledge-gap patterns. This gives every review session innate company knowledge without burning token budget re-reading raw wiki pages from scratch.

**Curator (Mark):** Can trigger an on-demand refresh after merging wiki PRs by running `/review-context-bundler`. The bundle is rebuilt daily at 06:00 UTC via cron.

## Success Criteria

- [ ] New skill at `.sdlc/skills/review-context-bundler/SKILL.md` (also deployed to `.claude/skills/`)
- [ ] Output file at `~/state/morris/review-context.md`, valid markdown, header line present
- [ ] Output ≤120K chars (1 sample run verified)
- [ ] Cron schedule registered for `0 6 * * *` via schedule-cron skill
- [ ] Bundle generated at cron runtime at `~/state/morris/review-context.md` (not committed — .gitignore'd)
- [ ] `review-prs` Step 0 loads bundle and emits `[KB: ...]` citations in findings

## Constraints
| Constraint | Value |
|------------|-------|
| Budget | minimal |
| Timeline | 1 day |
| Scale | ~76 wiki pages, ~15 runbooks, ~15 action logs |

## Security Constraints (Non-Negotiable)

- [ ] tech-gc-knowledgebase is read-only — never write to it from this skill
- [ ] Budget enforcement must be in code (≤120K chars), not a TODO comment
- [ ] Bundle generation must be idempotent (deterministic ordering, no random elements)
- [ ] No full wiki page bodies in output — titles + section heads + summaries only

## Operational Lifecycle
- Bundle rebuilt daily at 06:00 UTC by cron; on-demand via `/review-context-bundler`
- If budget exceeded, least-recently-modified wiki pages are dropped; dropped list logged to `/tmp/bundle-dropped.txt`
- Header line `<!-- review-context bundle: built=ISO8601 / wiki_pages=N / ... -->` allows staleness detection by review-prs Step 0

## Codebase Context (Feature Updates Only)
| Aspect | Details |
|--------|---------|
| Affected files | `.sdlc/skills/review-prs/SKILL.md`, `.claude/skills/review-prs/SKILL.md` |
| Related components | `review-prs` skill, `schedule-cron` skill |
| Current behavior | `review-prs` launches Claude Code sessions with no persistent company context |
| Desired change | Add Step 0 that loads `~/state/morris/review-context.md` via `--append-system-prompt-file` |
| Architecture constraints | Must not read wiki pages at review time (too slow/large); bundle must be pre-built |

## Test Criteria

1. `tests/test_story885_review_context_bundler.py` — RED → GREEN suite covering:
   - Bundle file generated at the configured path with the `<!-- review-context bundle: ... -->` header line.
   - Header populated with build timestamp + counts of wiki_pages / runbooks / incidents / gaps.
   - Total bundle size ≤ 120,000 chars; over-budget runs drop least-recently-modified wiki pages and write the dropped list to `/tmp/bundle-dropped.txt`.
   - Determinism: running the builder twice on identical inputs produces byte-identical output.
   - tech-gc-knowledgebase repo treated as read-only — no writes attempted (assert by mocking write-side filesystem ops).
   - Knowledge-gaps roll-up groups by normalized `gap` field, lists top-10 most-frequent with counts.
2. End-to-end smoke: a single bundler run inside a fixture wiki + runbook tree produces a bundle that the `review-prs` Step 0 successfully loads via `--append-system-prompt-file`.

## Validation

- After deploy, run `/review-context-bundler` on Morris VM; confirm `~/state/morris/review-context.md` is overwritten with a fresh header timestamp and non-zero counts.
- Trigger a `review-prs` cycle on a real open PR; verify Morris emits at least one finding tagged `[KB: wiki/...]` (citation rule from updated `review-prs` SKILL.md).
- Verify cron registration: `crontab -l` shows the `0 6 * * *` entry calling the bundler.
- Verify staleness detection: temporarily move the bundle file aside, run `review-prs`; confirm Step 0 emits the "missing/stale" warning and proceeds with the inline-SDLC fallback.
