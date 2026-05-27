## Morris Code Review — PR #261: release(batch): settings auth fix + BSR dashboard + BSB-3

**Size:** Very Large (28 files, +4778/−128) | **Verdict: REQUEST_CHANGES (pending conflict + CI) ⛔**

---

### This is the correct PR to merge (supersedes #251, #256, #255)

✅ Migration renumbered `037 → 040` (chained from `039_keyword_analytics_daily`) — collision resolved
✅ Settings auth fix included
✅ BSR SC-11 permission fix + CI guard included
✅ Nav reorganization included

---

### Blockers (must resolve before merge)

| Severity | Issue |
|----------|-------|
| **BLOCKER** | **CONFLICTING** — rebase onto current `main` required |
| **BLOCKER** | **No CI** — 28 files, 3 stories, CI is non-negotiable |
| **HIGH** | `_claim_names` groups overage test gap — Azure AD users with >200 groups silently get 403 even when admin. Add **SA-33 test** before merge. |
| MEDIUM | STORY-210 campaign allocation cron (~300 lines) not mentioned in PR body — update description so reviewers know what is included. |
| MEDIUM | No test for transient SP-API 429 cache bypass (inherited from PR #251). File as follow-up. |

---

### Merge Sequence for Mark

Once conflict resolved and CI green:
1. Add SA-33 test for `_claim_names` groups overage (HIGH — auth correctness)
2. Update PR body to mention STORY-210 campaign allocation scope explicitly
3. Push — CI must go fully green
4. Morris re-reviews and approves
5. Mark merges (branch protection)
6. Close PRs #251, #256, #255 as superseded
7. **Verify migration chain**: `039_keyword_analytics_daily` must be applied in production before `040_bsr_recommendations` runs
