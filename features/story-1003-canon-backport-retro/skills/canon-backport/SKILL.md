---
name: canon-backport
description: Diff a closing story's lessons learned against gc-data-v2 and tech-gc-knowledgebase canon, drafting PRs when gaps are found.
triggers:
  - canon backport
  - backport to canon
  - canon gap
  - gap detection
  - backport lessons
---

# Canon Backport

Scans a closing story's lessons learned against `gc-data-v2/platform/*.md` (30 docs) and `tech-gc-knowledgebase/wiki/**/*.md`, drafting a PR when a gap is found.

## Usage

```
/canon-backport <STORY-ID>              # Run canon-backport for a specific story
/canon-backport <STORY-ID> --dry-run    # Emit results JSON without opening PRs
/canon-backport <STORY-ID> --target=gc-data-v2          # Only search gc-data-v2
/canon-backport <STORY-ID> --target=tech-gc-knowledgebase  # Only search wiki
```

## When to run

- **Automatically** at Phase 9 close (after the 3-question gate is answered), unless `--no-backport` flag was passed.
- **Manually** via `/canon-backport STORY-XXXX` at any time.
- **From `complete-story`** per epic SC-5 (STORY-1012 scope).

## Inputs

| Source | Purpose |
|--------|---------|
| `features/story-XXXX/seed.md` | Original scope and problem statement |
| `features/story-XXXX/refinement-report.md` | Lessons learned, edge cases, gap analysis |
| `features/story-XXXX/code-review.md` | Code review findings (if exists) |
| 3-question gate answers (from `refinement-report.md § 3-question gate`) | Canon-doc impact, scaffold backport, sibling sweep |

## Steps

```
1. CHECK idempotency
   - Run: gh pr list --repo hpi-gorillacommerce/gc-data-v2 --search "STORY-XXXX canon" --state all
   - Run: gh pr list --repo hpi-gorillacommerce/tech-gc-knowledgebase --search "STORY-XXXX canon" --state all
   - If existing PR found → print "canon-backport already ran for STORY-XXXX; existing PR: <url>" → STOP
   - This ensures rerunning on the same story never opens a duplicate PR

2. GATHER lessons learned
   - Read seed.md § Problem + § Lessons Learned
   - Read refinement-report.md (full — focus on edge cases, gap analysis, 3-question gate)
   - Read code-review.md § findings (if exists)
   - Extract keywords and key phrases from lessons

3. DETECT gaps (see gap-detection.md for algorithm)
   - Search gc-data-v2/platform/*.md (all 30 docs) for keyword + heading matches
   - Search tech-gc-knowledgebase/wiki/**/*.md for keyword + heading matches
   - Score each match (0.0–1.0 confidence)
   - A match with score >= 0.7 that has missing or incomplete content = gap

4. CATEGORIZE gaps
   - Platform/architectural canon gap → target repo: gc-data-v2
   - Business/process canon gap → target repo: tech-gc-knowledgebase
   - If a single lesson spans both → open TWO PRs, each linking the other in its body

5. DRAFT PR(s) (skip if --dry-run)
   - For gc-data-v2 gaps:
     - Branch: canon/story-XXXX-<slug>
     - Modify the matched platform/*.md (or propose a new section)
     - PR body uses template: skills/canon-backport/templates/pr-body-gc-data-v2.md
     - PR status: DRAFT (data team has merge authority)
   - For tech-gc-knowledgebase gaps:
     - Branch: wiki/story-XXXX-<slug>
     - Modify the matched wiki page (or propose new content)
     - PR body uses template: skills/canon-backport/templates/pr-body-tech-gc-knowledgebase.md
     - PR status: DRAFT

6. WRITE results file
   - Path: features/story-XXXX/canon-backport-results.json
   - Schema:
     {
       "matched_canon_docs": ["gc-data-v2/platform/failure-modes.md#section-slug"],
       "gaps_found": [{"section": "...", "type": "partial|missing", "missing": "..."}],
       "prs_drafted": [{"repo": "gc-data-v2", "number": 142, "url": "https://..."}]
     }
   - If no gaps found: gaps_found=[], prs_drafted=[], matched_canon_docs lists the docs that were searched

7. LOG to .project
   - Add canon_backport_invoked: true (or no_gap_found) with PR URL(s) if any
```

## Idempotency

Rerunning `/canon-backport STORY-XXXX` on the same story does NOT open a duplicate PR. The skill checks `gh pr list --search "STORY-XXXX canon"` before creating anything. If a PR already exists:
- Prints: `canon-backport already ran for STORY-XXXX; existing PR: <url>`
- Returns early without modifying any files
- Exits with success (exit 0) — this is NOT an error, it's the expected behavior on repeat runs

## Idempotency Horizon

If a story is closed → reopened → closed again, `canon-backport` re-runs if `.project` shows the story reopened since the last canon-backport timestamp. Otherwise it skips.

## Target Repos

| Target | When | Branch naming |
|--------|------|---------------|
| `hpi-gorillacommerce/gc-data-v2` | Gap touches platform/architectural canon | `canon/story-XXXX-<slug>` |
| `hpi-gorillacommerce/tech-gc-knowledgebase` | Gap touches business/process canon | `wiki/story-XXXX-<slug>` |

## Dry-Run Mode

`/canon-backport STORY-XXXX --dry-run` emits `canon-backport-results.json` without opening any PRs. Useful for:
- Retro analysis (what WOULD have been backported)
- CI testing (validate gap detection without side effects)
- Pre-flight checks before Phase 9 close

## PR Authorship

Drafted PRs carry `Co-Authored-By: Claude` markers (transparent). The story owner is listed as reviewer. Mark / data team has merge authority.

## Error Handling

| Error | Behavior |
|-------|----------|
| `gh` CLI not available | Emit `gh auth login` guidance and exit non-zero |
| Target repo not accessible | Warn and skip that target; continue with other targets |
| No refinement-report.md | Warn; attempt gap detection from seed.md alone |
| No lessons or gaps found | Return `no_gap_found` result; this is valid, not an error |

## Output Format

The skill writes two output files per story:

1. **`canon-backport-results.json`** — structured results (see Results Schema below)
2. **`canon-gap.yaml`** — per-story canon gap file listing all detected gaps in YAML format, consumed by `/retro` when building the `retro-proposal-gc-data-v2.yaml` companion proposal

Both files are written to `features/story-XXXX/`.

## Results Schema

```json
{
  "story_id": "STORY-XXXX",
  "timestamp": "2026-05-18T12:00:00Z",
  "matched_canon_docs": [
    "gc-data-v2/platform/failure-modes.md#author-time-gate-3"
  ],
  "gaps_found": [
    {
      "section": "Author-time gate 3: KV secret slot vs FA env-var reference",
      "type": "partial",
      "missing": "dual-slot provisioning anti-pattern",
      "confidence": 0.85,
      "source_paragraph": "Walmart token endpoint returned 400..."
    }
  ],
  "prs_drafted": [
    {
      "repo": "gc-data-v2",
      "number": 142,
      "url": "https://github.com/hpi-gorillacommerce/gc-data-v2/pull/142",
      "branch": "canon/story-T001-walmart-kv-placeholder",
      "status": "draft"
    }
  ]
}
```

## Boundaries

| Always Do | Never Do |
|-----------|----------|
| Open PRs as **draft** (data team has merge authority) | Force-push or auto-merge a canon-backport PR |
| Cite the exact paragraph from refinement-report.md | Modify gc-data-v2/platform/*.md from this story's branch directly |
| Check idempotency before creating PRs | Open duplicate PRs for the same story |
| Rate-limit to 1 backport PR per story per target repo | Flood the data team with PRs |
