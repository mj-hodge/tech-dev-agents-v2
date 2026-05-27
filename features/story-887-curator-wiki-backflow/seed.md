# Seed

## Overview
| Field | Value |
|-------|-------|
| Mode | feature_update |
| Scope | small |
| Criticality | important |
| Feature Name | curator-wiki-backflow |
| Frontend | false |

## Problem Statement

Morris's `review-prs` skill emits KB-GAP findings whenever a PR references a concept
that lacks wiki coverage. These findings are appended to
`~/state/morris/knowledge-gaps.jsonl` but Cole (Morris-as-curator) never reads that
file — the loop is open. Recurring gaps accumulate without ever becoming wiki page
proposals. This story wires the curator skill to read `knowledge-gaps.jsonl`, cluster
repeated gaps above a configurable threshold, and emit wiki page proposals directly
into the curation plan, closing the feedback loop between review and curation.

## Target User / Use Case

**Cole (Morris-as-curator):** Before building the weekly curation plan, Cole reads
`~/state/morris/knowledge-gaps.jsonl`, clusters normalized gap text, and converts any
cluster that meets the threshold into a bullet-point wiki page proposal. After a
proposed wiki PR merges, Cole appends a resolution record to
`~/state/morris/knowledge-gaps-resolved.jsonl` so the same gap is not re-proposed next
cycle.

**Mark:** Sees gap-derived proposals in the curator Teams message alongside
scratch-diff proposals. Can set `CURATOR_GAP_THRESHOLD` env var to tune sensitivity.

## Success Criteria

- [ ] `tech_dev_agents/morris/curator_backflow.py` module exists with `cluster_gaps`,
      `propose_wiki_path`, and `mark_resolved` functions
- [ ] `GapCluster` and `Resolution` dataclasses defined with correct fields
- [ ] `propose_wiki_path` routes system keywords → `wiki/systems/`, process keywords
      → `wiki/processes/`, default → `wiki/standards/`
- [ ] `cluster_gaps` normalizes text (lowercase, strip punctuation, collapse whitespace)
      and filters by threshold
- [ ] `mark_resolved` appends to `knowledge-gaps-resolved.jsonl` (source file untouched)
- [ ] Curator SKILL.md updated with Step 1b (gap backflow) and Step 1c (mark resolved)
- [ ] `tests/morris/test_curator_backflow.py` suite GREEN (8+ tests)
- [ ] Empty / missing JSONL → graceful empty list (no crash)
- [ ] All tests in `tests/morris/` and `tests/test_sdlc_framework_compliance.py` pass

## Constraints
| Constraint | Value |
|------------|-------|
| Budget | minimal |
| Timeline | 1 day |
| Scale | knowledge-gaps.jsonl < 10K lines typical |

## Security Constraints (Non-Negotiable)

- [ ] `knowledge-gaps.jsonl` is read-only — `cluster_gaps` must never modify it
- [ ] `mark_resolved` writes only to `knowledge-gaps-resolved.jsonl`, never to the
      source file
- [ ] No print() or stdlib logging — structlog-JSON only
- [ ] Python 3.12 idioms: f-strings, pathlib, type hints, dataclasses

## Operational Lifecycle

- `cluster_gaps` called during curator Step 1b (before curation plan build)
- `mark_resolved` called during curator Step 1c (after each wiki PR merge)
- Threshold default: 3; override via `CURATOR_GAP_THRESHOLD` env var
- Resolved JSONL grows unbounded — archival is out of scope for this story

## Codebase Context (Feature Updates Only)
| Aspect | Details |
|--------|---------|
| Affected files | `tech_dev_agents/morris/curator_backflow.py` (new), `deployment/vm/skills/morris/curator/SKILL.md` (edit) |
| Related components | `review-prs` skill (upstream producer of KB-GAP entries) |
| Current behavior | `knowledge-gaps.jsonl` accumulates entries; curator never reads it |
| Desired change | Curator Step 1b reads JSONL, clusters gaps, proposes wiki pages; Step 1c marks resolved |
| Architecture constraints | Pure additions — do NOT touch existing scratch→wiki diff logic |

## Test Criteria

1. `tests/morris/test_curator_backflow.py` — RED → GREEN suite covering:
   - 5-gap fixture at threshold=3 → exactly 1 cluster (advertising-amazon rate limit)
   - 5-gap fixture at threshold=1 → 5 clusters
   - `propose_wiki_path` routes system keyword → `wiki/systems/advertising-amazon.md`
   - `propose_wiki_path` routes process keyword → `wiki/processes/review.md`
   - `propose_wiki_path` defaults slug → `wiki/standards/widget-naming-convention.md`
   - `mark_resolved` writes to resolved file; double-run does not deduplicate (append semantics)
   - Source JSONL file unchanged after `cluster_gaps` run (read-only assertion)
   - Empty / missing JSONL → returns empty list

## Validation

- After deploy, confirm curator curation plan output contains gap-derived bullet proposals
  when `knowledge-gaps.jsonl` has entries above threshold.
- Confirm `~/state/morris/knowledge-gaps-resolved.jsonl` grows after a wiki PR merge.
- Confirm `~/state/morris/knowledge-gaps.jsonl` is not modified by any curator run.
- Confirm `CURATOR_GAP_THRESHOLD=1` includes all singleton gaps in proposals.
