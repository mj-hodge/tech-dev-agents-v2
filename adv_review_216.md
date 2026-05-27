## Adversarial Review Addendum — STORY-740 PR #216

*Second-pass adversarial analysis — 2026-04-27*

Addendum to my [initial review](#issuecomment-4330788454). Three additional blockers found.

### New Blockers

**🔴 H-1 (new) — SQL regex matches commented-out DDL**

The contract test's SQL parser does not strip `--` or `/* */` comments before applying regexes. The "last match wins" loop means a comment block at the bottom of a migration file silently overrides real DDL. A "future-proof" commented line (e.g., a note about a planned state) could shadow the real `CHECK` constraint and make the contract test pass on the wrong set of states.

**Fix:** Strip `--` and `/* */` comment lines before running any regex against migration SQL.

**🔴 H-2 (new) — Constraint regex not table-scoped; any `status` column poisons the test**

The constraint-extraction regex matches `\w*status\w*` across the entire migration file. If any future migration on a _different_ table (e.g., `improvement_proposals.status_check`) lands in the migrations directory, it will match and silently corrupt the dispatch contract test's state extraction.

**Fix:** Anchor the regex to the `dispatch_items` table specifically.

**🔴 M-2 (new) — `TRANSITIONS` already ships incorrect at merge**

The canonical `dispatch_state.py` declares `TRANSITIONS["failed"] = frozenset()` (no outgoing edges). But `force_claim()` in the live codebase allows `failed → claimed`, and `resume_after_answer` performs `needs_info → pending` — neither is in TRANSITIONS. The module that claims to be the single source of truth disagrees with the runtime FSM on day one.

**Fix:** Audit `force_claim()`, `resume_after_answer`, and all other state-mutation paths; reconcile them against `TRANSITIONS` before merge. Either update TRANSITIONS to match reality, or fix the callers to respect it.

### Additional Medium Findings

- **M-1:** `TRANSITIONS` is never imported by any caller — dead code, no enforcement
- **M-3:** `DispatchNextResponse`, `ReviewResponse`, `DispatchPauseResponse`, `DispatchResumeResponse`, `DispatchNeedsInfoResponse` all carry `status: str` (untyped, not validated against `STATES`)
- **M-4:** `is_active()` / `is_terminal()` accept arbitrary strings and silently return `False` — wrong default for an FSM guard; should raise on unknown state
- **M-5:** Duplicate migration file prefixes already exist (`009_foundry…` / `009_rework…`) — "latest = lexicographic last" parsing is fragile

### Updated Verdict: REQUEST_CHANGES

**Block-merge list (all must fix before merge):**
1. Original H-1 (migration in-place edit) — add migration 009
2. New H-1 (SQL regex matches comments) — strip comments before parsing
3. New H-2 (regex not table-scoped) — anchor to `dispatch_items`
4. New M-2 (TRANSITIONS wrong at day 1) — reconcile with runtime FSM
5. CI green (seed.md sections)

M-1, M-3, M-4, M-5 are non-blocking polish but should be addressed to make the "canonical source of truth" claim credible.

*— Morris adversarial pass (2026-04-27)*
