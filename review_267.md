## PR Review â EPIC-008 Seed: SP-API Notifications Archive (Raw Payload Landing)

**Size:** Medium (+669/-0, 1 file) | **Verdict: APPROVE â Phase 1 seed (do not merge until pre-merge checklist)**

*Review by Morris (Claude Code analysis â 2026-04-27)*

Note: `advertising-amazon` has branch protection â Morris cannot merge directly. Escalating to Mark for final call.

### CI: All 6 checks passing â

### SDLC Compliance

| Check | Result | Notes |
|-------|--------|-------|
| `features/epic-008-spapi-notifications-archive/seed.md` | â | Correct deliverable location |
| Phase 1 seed completeness | â | 18 sections: Problem, Inventory, Architecture, Storage, Stories, SC, OQs, Verification |
| Scope (Large/New with full phase path) | â | Correctly declared |
| `.project` updated for EPIC-008 | â Missing | No EPIC-008 reference on this branch |
| `backlog.md` updated | â Missing | No EPIC-008 entry |
| `development-tasks.md` updated | â Missing | No EPIC-008 entry |

Severity: WARN â non-blocking for a draft seed, but MUST resolve before merge per SDLC policy ("A phase is NOT complete until tracking docs are updated").

### Content Review (Phase 1 Seed Quality)

**Strengths:**
- Authoritative sourcing: every architectural claim cites canonical SP-API docs with verbatim quotes â grantless `createDestination`, FIFO restriction, single-EventBridge-destination-per-account hard limit, SP-API service principal ARN, KMS policy template
- Complete 22-type notification inventory (16 SQS / 6 EventBridge) with explicit post-hoc correction of `LISTINGS_ITEM_MFN_QUANTITY_CHANGE` after doc recheck â exceptional seed rigor
- Storage decision (S3 over Azure Blob) well-argued with explicit trade-offs; OQ-3 correctly tracks new AWS account bootstrap risk
- External API Write Safety (Â§13) correctly flags `createSubscription`/`createDestination` as MCP-write-adapter-required with Phase 7/8/11 verification steps
- Idempotency keys on `notificationId`, not `MessageId` â handles Path B (EventBridgeâSQS) re-routing correctly
- Boundary clause protects existing STORY-028 ORDER_CHANGE pipeline
- Volume-gating for high-volume types behind feature flag
- 11-story decomposition with scope tags lines up with dispatch workflow

**Concerns:**

| # | Severity | Finding |
|---|----------|---------|
| C-1 | WARN | `.project`, `backlog.md`, `development-tasks.md` not updated â required before merge |
| C-2 | Low | SC-2/SC-9 assume ACA Job runtime but OQ-11 (Lambda vs ACA) still open â soften to runtime-agnostic or add "pending OQ-11" note |
| C-3 | Low | EPIC-008-S04 (AWS infra bootstrap) marked Small â bucket + versioning + lifecycle + SSE + cross-account policy in new AWS account is closer to Medium; reconsider in Phase 4 |
| C-4 | Low | S3 key uses colon timestamps (`2026-04-27T14:22:01Z-...`) â some downstream tools (Athena, presigned URLs) struggle; consider ISO-8601 basic format or epoch in Phase 6 |
| C-5 | Low | `If-None-Match: "*"` for S3 conditional writes needs explicit boto3 param passthrough â confirm early in Phase 2 |
| C-6 | Low | OQ-9 (STORY-028 SP-API destination re-registration risk if new AWS account) â escalate to Phase 2 priority, not a regular OQ |

### Verdict: APPROVE (Phase 1 seed only)

High-quality, comprehensive, well-cited seed. Correctly identifies all External API Write Safety implications, captures OQs Phase 2 must resolve, decomposes the epic into appropriately-sized stories. All concerns are tracking-file housekeeping (C-1) or Phase 4/6 refinements â none invalidate the Phase 1 deliverable.

**Pre-merge checklist:**
1. **Required:** Update `.project`, `backlog.md`, `development-tasks.md` to register EPIC-008 (C-1)
2. **Recommended:** Runtime-agnostic SC-2/SC-9 until OQ-11 resolves (C-2)
3. **Recommended:** Re-size EPIC-008-S04 to Medium (C-3)
4. **Recommended:** Escalate OQ-9 to Phase 2 priority (C-6)

Do NOT merge until: Mark reviews seed, Phase 2-5 complete, and pre-merge checklist resolved (per PR body).

*â Morris (2026-04-27)*
