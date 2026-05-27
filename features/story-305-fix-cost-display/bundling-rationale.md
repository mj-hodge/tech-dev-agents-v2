# PR #35 Bundling Rationale — story-305/fix-cost-display

## Summary

PR #35 bundles five stories (STORY-227, STORY-228, STORY-229, STORY-253, STORY-305)
on a single branch. This document explains why they were shipped together and confirms
that the coupling is intentional and low-risk.

---

## Stories Bundled

| Story | Title | Scope | Phase Path |
|-------|-------|-------|-----------|
| STORY-227 | Managed Identity Azure Auth | Medium | 1→4→6→6b→6c→6d→7→8→8b→11→Done |
| STORY-228 | MSAL Graph Token Auto-Refresh | Small | 1→7→8→Done |
| STORY-229 | Bot Teams Messaging | Medium | 1→4→6→7→8→8b→11→Done |
| STORY-253 | Commit-Gated Dispatch Completion | Small | 1→7→8→Done |
| STORY-305 | Fix Cost Display (hotfix) | Small | 1→7→8→Done |

---

## Rationale for Bundling

### 1. Tight Dependency Chain (STORY-227 → STORY-228 → STORY-229)

These three stories form a credential and messaging stack that cannot ship partially:

- **STORY-227** replaces `AzureCostClient`'s hard-coded `ClientSecretCredential`
  with `DefaultAzureCredential` (managed identity). This establishes the credential
  injection pattern used by subsequent stories.
- **STORY-228** wires MSAL `ConfidentialClientApplication` into the v2 `TeamsClient`
  as an async token provider. It follows the same injection pattern introduced by
  STORY-227 and is a direct prerequisite for stable Teams messaging.
- **STORY-229** replaces the brittle M365 CLI invocation with MSAL client-credentials
  auth for sending Teams messages. It depends on STORY-228's `GraphTokenProvider`
  being present in `main.py`.

Shipping STORY-229 without STORY-228, or STORY-228 without STORY-227, would leave
the ops-console in a partially migrated state where some auth paths use managed
identity and others still use hard-coded secrets, creating a confusing and fragile
configuration surface.

### 2. Shared Configuration Surface

All three infrastructure stories touch the same files:
- `tech_dev_agents/ops_console/config.py`
- `deployment/ops-console/.env.example`
- `tech_dev_agents/ops_console/main.py`

Batching the changes avoids repeated conflict resolution across three separate PRs
and produces a clean, consistent configuration model in one merge.

### 3. STORY-253 — Sprint Co-Discovery

STORY-253 (commit-gated dispatch completion) was a dispatch-integrity fix surfaced
during testing of the same sprint. Because it touched the dispatch lifecycle rather
than the Teams/Azure layer, it could in principle have shipped separately — but it
was small (two commits: RED tests + implementation), complete, and ready. Batching
it avoided an extra PR review cycle for a trivial fix.

### 4. STORY-305 — Hotfix Found During QA

The `$0.00` cost display bug was discovered during QA of STORY-229 when the team
noticed agent cards were blank. The root cause was traced to a field name mismatch
introduced during the STORY-227 refactor (`azure_cost_usd` written, `foundry_cost_usd`
read). The fix was a single-commit change touching four files. Including it in the
same PR ensured the cost display was correct before STORY-229 shipped — users testing
the Teams integration would immediately see real costs on agent cards.

---

## Risk Assessment

- **Rollback scope:** If this PR is reverted, all five changes revert atomically.
  There is no partial-rollback concern because the stories share a dependency chain.
- **Review burden:** The PR is large in diff size but logically cohesive.
  Reviewers can read the four feature folders to understand each story independently.
- **Test coverage:** All five stories have RED→GREEN test suites. The combined test
  run validates the full integrated stack.

---

## Going Forward

Future sprints should avoid bundling unrelated stories (e.g., STORY-253 could have
been a separate PR). The STORY-227/228/229 dependency chain was a legitimate reason
to bundle; the others were opportunistic. For teams using worktrees, the preferred
approach is one worktree per story with a separate PR, merging in dependency order.
