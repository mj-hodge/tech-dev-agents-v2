## Morris Code Review — PR #251: feat(M2): BSR dashboard lookup + BSB-3 batch scoring

**Size:** Large (16 files, +3078/−109) | **Verdict: REQUEST_CHANGES ⛔**

---

### BLOCKER: Migration Revision Collision

`alembic/versions/037_bsr_recommendations.py` uses `revision = "037"` with `down_revision = "036"`. Migration `037_search_term_report_rows` **already exists in production**. Running `alembic upgrade head` on any environment with `037_search_term_report_rows` applied will fail with a **multiple heads error** — database migrations broken.

**Do not merge PR #251.** Use **PR #261** instead — it renumbers this migration to `040` (chained from `039_keyword_analytics_daily`), resolving the collision. PR #261 includes all changes from this PR plus the fix.

---

### Other Findings

| Severity | Finding | Location |
|----------|---------|----------|
| MEDIUM | No test for cache behavior under transient SP-API 429 failure — `_sources_ok` guard prevents caching, but this path is not covered in `test_bsr_lookup_service.py`. | `test_bsr_lookup_service.py` |
| LOW | `import httpx as _httpx` duplicated at two lines inside the same exception handler — redundant but harmless. | `bsr_lookup_service.py` |

---

### What to Do

1. **Close this PR** — superseded by PR #261
2. All code corrections are addressed in PR #261
3. Before PR #261 is merged: address `groups_overage` test gap (see PR #256 review)
