---
name: context-load
description: >
  Load relevant knowledge pages from gc-knowledgebase into agent context before
  starting a story phase. Queries the ops console knowledge search endpoint and
  injects matching pages as context. Use at the start of each phase to pre-fill
  known answers and avoid duplicate needs_info questions.
category: knowledge-management
---

# Context Load

Queries the ops console knowledge cache for pages relevant to the current story
or question, then injects the top results into the agent's working context.

## When to Use

- At the start of Phase 1 (Seed) — load project context and prior decisions
- Before asking a needs_info question — check if the cache already has the answer
- At the start of any phase when the story involves a known domain

## Prerequisites

- Ops console running and reachable (OPS_CONSOLE_URL env var set)
- `DISPATCH_API_KEY` set for authentication

## Step 1: Search for relevant pages

```
terminal(command="curl -s -H 'X-Api-Key: $DISPATCH_API_KEY' '$OPS_CONSOLE_URL/api/knowledge/search?q=<QUERY>&similarity=0.75&limit=5'", pty=false)
```

Replace `<QUERY>` with the current question or topic (URL-encoded).

## Step 2: Evaluate cache_hit

Parse the JSON response:
- If `cache_hit: true` — inject the best matching page's `snippet` into context and
  use the cached answer. **Do not emit a needs_info event.**
- If `cache_hit: false` — proceed with normal phase work or emit needs_info.

## Step 3: Record citation (if you used a page)

After using a knowledge page, record the citation so the weekly audit tracks usage:

```
terminal(command="curl -s -X POST -H 'X-Api-Key: $DISPATCH_API_KEY' -H 'Content-Type: application/json' -d '{\"job_id\": \"<JOB_ID>\", \"knowledge_path\": \"<PAGE_PATH>\", \"phase\": \"<PHASE_NAME>\"}' '$OPS_CONSOLE_URL/api/knowledge/cite'", pty=false)
```

## Response Shape

```json
{
  "cache_hit": true,
  "best_score": 0.91,
  "pages": [
    {
      "path": "wiki/processes/sdlc.md",
      "title": "SDLC Process",
      "snippet": "Use dispatch_jobs.job_id as the FK reference for all child tables.",
      "relevance_score": 0.91
    }
  ]
}
```

## SLO

- Target: ≥1 relevant page injected in 60%+ of story phases (SC-9)
- Similarity threshold: 0.75 (configurable via `?similarity=` query param)

## Error Handling

- Ops console unreachable: log a warning and continue without context injection
- No cache hit: proceed normally; consider whether a needs_info is truly required
- Score below threshold (< 0.75): treat as miss; do not inject weak matches
