# STORY-860 Phase 5 — Selection

## Selected Approach

**Approach C: Hybrid Wrapper** (25/30 in Phase 4 scoring)

## Rationale

1. All 13 ACs covered natively — no adapter shims or monkey-patches.
2. Within 3-5 day timeline target (estimated 4.5-5.5 days).
3. Lifts proven v1 git operations as standalone functions (no v1 module coupling).
4. Fixes the heartbeat starvation bug discovered in Phase 2 research.
5. SIGTERM coordination uses existing STORY-857 handler — zero conflict risk.
6. Feature-flaggable: `DISPATCH_V2_ORCHESTRATION=1` for safe rollback.

## MVP Scope

### In (Phase 8 implementation)

| Component | Description | Est. Lines |
|-----------|-------------|-----------|
| `tech_dev_agents/orchestration/__init__.py` | Package init | 5 |
| `tech_dev_agents/orchestration/git_ops.py` | Git operations: resolve_default_branch, ensure_branch, verify_clean_tree. Typed exceptions: GitBranchSetupError, GitRebaseError, GitWorkspaceDirtyError | ~150 |
| `tech_dev_agents/orchestration/branch_resolver.py` | Branch resolution: claim.metadata.branch > seed Target Branch > story-id default > 859 regex fallback | ~80 |
| `tech_dev_agents/orchestration/phase_defs.py` | PHASE_MAP + parse_seed_phase_path() lifted from v1 | ~120 |
| `tech_dev_agents/orchestration/v2_orchestrator.py` | `run_orchestrated(claim, session, headers)`: heartbeat thread, phase loop, event emission, resume query, SIGTERM check | ~300 |
| `dispatch_poller_v2.py` changes | Extend _ActiveClaim; replace _run_sdk call with run_orchestrated; feature flag | ~80 |
| `dispatch_failure_policy.py` changes | Add phase-scoped failure classes to POLICY_TABLE | ~30 |
| `scripts/migrations/059_phase_event_types.sql` | Index on (job_id, event_type, (event_data->>'phase')) | ~10 |
| Tests (3 files) | Full SDLC sim, branch lifecycle, resume, 859 compat, heartbeat, backward compat | ~400 |

**Total new/changed:** ~1175 lines

### Out (follow-up stories)

- QUESTION.md handling (v1 feature, not required by any AC)
- Ghost-completion guard (v1 Phase 8 check, can be added later)
- Acceptance diff gate (v1 post-loop check, separate concern)
- Frontend Playwright spec gate (v1 Phase 7 check, separate concern)
- PR creation fallback (v1 post-loop, SDK tool handles this)
- Teams notifications (v1 integration, separate story)
- Session resume across phases (STORY-511, v1 feature, not AC-scoped)
- `.project` file updates (v1 integration, not required)
- All-39 canary regression test (AC-9 requires 5-story canary only)
- Expanding phase events to phases not in PHASE_MAP (e.g., 6b, 8b variants)

## Key Decisions (Locked)

| Decision | Choice | Locked By |
|----------|--------|-----------|
| Approach | C (Hybrid Wrapper) | Phase 4 score: 25/30 |
| Resume mechanism | Events (primary) + deliverable (fallback) | Phase 4 Q2 |
| Phase events | event_type strings + JSONB payload | Phase 4 Q3 |
| Heartbeat | Single daemon thread, all phases | Phase 4 Q4 |
| Branch source priority | metadata > seed > story-id > regex | Phase 4 Q5 |
| Default branch | Per-claim re-detect | Phase 4 Q6 |
| 859 regex fallback | Keep as last resort | Phase 4 Q7 |
| Phase event granularity | All phases in scope's list | Phase 4 Q8 |
| Feature flag | DISPATCH_V2_ORCHESTRATION env var | Phase 5 |
| SIGTERM | No new handler; check _sigterm_received between phases | Phase 2 research |

## Phase 6 Input

Design the following:
1. `git_ops.py` API and exception hierarchy
2. `branch_resolver.py` resolution chain with fallback logic
3. `v2_orchestrator.py` function signature, phase loop, event emission, heartbeat thread
4. `_ActiveClaim` extension (new fields: branch, target_pr, correlation_key, parent_job_id)
5. Phase-scoped failure class mapping
6. Migration SQL for the event index
7. Feature flag integration in poll_loop
8. Canary set selection (5 stories from triage-2026-05-04.md)
