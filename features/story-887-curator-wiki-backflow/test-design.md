# Test Design — STORY-887: Curator Wiki-Backflow

## Suite Location

`tests/morris/test_curator_backflow.py`

## Test Cases

### Group A — `cluster_gaps` threshold filtering

| ID | Name | Description | Expected |
|----|------|-------------|----------|
| A1 | threshold=3 yields 1 cluster | 5-gap fixture (3 advertising-amazon variants + 2 unique) at default threshold=3 | Returns exactly 1 `GapCluster`; normalized gap contains "advertising amazon"; count==3; evidence has 3 PR numbers |
| A2 | threshold=1 yields 5 clusters | Same 5-gap fixture at threshold=1 | Returns exactly 5 `GapCluster` objects |

### Group B — `cluster_gaps` source-file immutability

| ID | Name | Description | Expected |
|----|------|-------------|----------|
| B1 | source file unchanged after run | Write fixture JSONL to tmp path, call `cluster_gaps`, read bytes before and after | Before-bytes == after-bytes (no modification) |
| B2 | empty JSONL → empty list | Pass a path to an empty file | Returns `[]`, no exception |
| B3 | missing JSONL → empty list | Pass a path that does not exist | Returns `[]`, no exception |

### Group C — `propose_wiki_path` routing

| ID | Name | Description | Expected |
|----|------|-------------|----------|
| C1 | system keyword match | `propose_wiki_path("advertising-amazon rate limit")` | `"wiki/systems/advertising-amazon.md"` |
| C2 | process keyword match | `propose_wiki_path("review process for medium PRs")` | `"wiki/processes/review.md"` |
| C3 | default slug | `propose_wiki_path("widget naming convention")` | `"wiki/standards/widget-naming-convention.md"` |
| C4 | system keyword case-insensitive | `propose_wiki_path("Fabric-Keepa ingestion details")` | `"wiki/systems/fabric-keepa.md"` |
| C5 | process keyword `deploy` | `propose_wiki_path("deploy rollback procedure unclear")` | `"wiki/processes/deploy.md"` |

### Group D — `mark_resolved` write semantics

| ID | Name | Description | Expected |
|----|------|-------------|----------|
| D1 | writes to resolved file | Call `mark_resolved` with one `Resolution`; read file | File contains exactly 1 JSON line with `gap_normalized`, `wiki_path`, `resolved_by_pr`, `resolved_at` |
| D2 | append semantics (no dedup) | Call `mark_resolved` twice with same input | File has 2 JSON lines (append, not deduplicated) — append-always is the documented contract |
| D3 | resolved_at is ISO format | `Resolution.resolved_at` field in written record | Parses as ISO datetime (no exception from `datetime.fromisoformat`) |

## Fixture Design

```jsonl
{"gap": "rate limit on advertising-amazon", "pr_number": 101, "repo": "tech-gc-knowledgebase", "claim": "no wiki page for advertising-amazon rate limits"}
{"gap": "Advertising amazon rate-limits!!", "pr_number": 102, "repo": "tech-gc-knowledgebase", "claim": "advertising-amazon rate limits not documented"}
{"gap": "advertising-amazon rate limits handling", "pr_number": 103, "repo": "tech-gc-knowledgebase", "claim": "handling of advertising-amazon rate limits unclear"}
{"gap": "deploy rollback procedure unclear", "pr_number": 104, "repo": "tech-gc-knowledgebase", "claim": "no rollback runbook found"}
{"gap": "code review meta", "pr_number": 105, "repo": "tech-gc-knowledgebase", "claim": "code review process not captured"}
```

Normalization of the three advertising-amazon entries must collapse to the same
normalized key: `"rate limit on advertisingamazon"` (or similar — exact key is an
implementation detail; what matters is that all 3 map to the same bucket).

## Test Infrastructure

- No live filesystem side-effects: all file I/O uses `tmp_path` pytest fixture
- No network calls
- No subprocess calls
- structlog is imported by the module under test; tests need not configure it
- Module imported via `sys.path.insert(0, ...)` pointing at repo root (same pattern
  as existing `tests/morris/` tests)

## RED State Verification

Before Phase 8 implementation:

```
pytest tests/morris/test_curator_backflow.py -x
```

Expected: `ImportError` or `ModuleNotFoundError` on
`tech_dev_agents.morris.curator_backflow` — confirms RED state.

## GREEN State Criterion

All 13 test cases pass. `pytest tests/morris/ tests/test_sdlc_framework_compliance.py -x`
exits 0.
