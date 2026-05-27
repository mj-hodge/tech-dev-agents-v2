# STORY-860 Canary Set

## Selected Stories (5 of 18 from Cluster 1)

| # | Story | Repo | Failure Shape | Selection Rationale |
|---|-------|------|--------------|-------------------|
| 1 | STORY-839 | advertising-amazon | PR rework | Fix-PR dispatch with existing branch; tests branch checkout from claim.metadata |
| 2 | STORY-844 | tech-dev-agents | PR rebase | Rebase onto updated main; tests default-branch detection + fetch |
| 3 | STORY-845 | tech-dev-agents | Rework-of | Rework chain with rework_of metadata; tests AC-3 rework threading |
| 4 | STORY-847 | tech-dev-agents | Fix-PR rework | Playwright fix with branch context; tests structured resolver |
| 5 | STORY-854 | advertising-amazon | Large multi-PR rebase | Complex branch lifecycle; tests multi-phase + branch management |

## Coverage Matrix

| Failure Shape | Count | AC Tested |
|--------------|-------|-----------|
| PR rework | 1 | AC-1 (branch lifecycle), AC-3 (rework threading) |
| PR rebase | 1 | AC-1, AC-2 (default branch) |
| Rework-of | 1 | AC-3 (claim.metadata.rework_of) |
| Fix-PR rework | 1 | AC-3, AC-8 (859 supersession) |
| Large multi-PR rebase | 1 | AC-1, AC-4 (phase events), AC-11 (heartbeat) |

## Merge Gate

All 5 stories must reach `completed` state in `dispatch_state_current` after STORY-860 is deployed. Verification:

```sql
SELECT story_id, state, lane, failure_class
FROM dispatch_state_current dsc
JOIN dispatch_jobs dj ON dsc.job_id = dj.job_id
WHERE dj.story_id IN ('STORY-839', 'STORY-844', 'STORY-845', 'STORY-847', 'STORY-854')
ORDER BY dj.story_id;
-- Expected: all state = 'in_review' or 'completed', lane != 'attention_queue'
```

## Notes

- These 5 are re-dispatched AFTER STORY-860 merge (not before).
- The other 13 Cluster 1 stories are NOT required to pass for merge.
- Cluster 2 stories (restart victims) are handled by STORY-857 independently.
