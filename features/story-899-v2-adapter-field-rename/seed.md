# Seed

## Overview
| Field | Value |
|-------|-------|
| Mode | bug_fix |
| Scope | small |
| Criticality | high |
| Feature Name | v2-adapter-field-rename |
| Frontend | true |

## Problem Statement

The Dispatch Queue dashboard renders blank for "Claimed by", "Waiting since", and "Enqueued at" on every row fetched from `/api/dispatch/v2/queue`. The live v2 API returns rows with fields named `leased_by`, `leased_at`, and `created_at`, but the frontend's `DispatchItem` type (and all dashboard render code) expects `claimed_by`, `claimed_at`, and `enqueued_at`. The `normalizeLane` helper in `frontend/src/api/dispatch.ts` only set `status` from the bucket key — it did not rename the other fields. This was verified live on 2026-05-05: a claimed-bucket row's keys were `[..., leased_at, leased_by, ...]` with no `claimed_by`, `claimed_at`, or `enqueued_at` present.

## Target User / Use Case

**Mark / ops team:** Viewing the Dispatch Queue dashboard tab to monitor which agent has claimed which story, when it was claimed, and when it was originally enqueued. All three values appeared blank after the v2 migration.

## Success Criteria

- [x] `normalizeLane` maps `leased_by` → `claimed_by` (v2 wire shape)
- [x] `normalizeLane` maps `leased_at` → `claimed_at` (v2 wire shape)
- [x] `normalizeLane` maps `created_at` → `enqueued_at` (v2 wire shape)
- [x] v1-shape rows (already have `claimed_by`/`claimed_at`/`enqueued_at`) pass through unchanged
- [x] Mixed-shape rows: v1 field wins over v2 field (backward-compat)
- [x] Null `leased_by` (pending/unclaimed row) yields `claimed_by: null`, no crash
- [x] `status` still comes from the bucket key, not from `item.state`
- [x] Attention items still merge into paused with `status: 'paused'`
- [x] 13 new tests all GREEN; full suite 0 new failures

## Test Criteria

New test file: `frontend/src/api/__tests__/dispatch.adapter.test.ts`

| Case | Description | Result |
|------|-------------|--------|
| 1a | v2 shape: `leased_by` → `claimed_by` | GREEN |
| 1b | v2 shape: `leased_at` → `claimed_at` | GREEN |
| 1c | v2 shape: `created_at` → `enqueued_at` | GREEN |
| 2a | v1 shape: `claimed_by` preserved | GREEN |
| 2b | v1 shape: `claimed_at` preserved | GREEN |
| 2c | v1 shape: `enqueued_at` preserved | GREEN |
| 3a | Mixed: v1 `claimed_by` wins over `leased_by` | GREEN |
| 3b | Mixed: v1 `enqueued_at` wins over `created_at` | GREEN |
| 4  | Null `leased_by` → `claimed_by: null`, no crash | GREEN |
| 5a | `in_progress` bucket → `status: 'claimed'` regardless of item | GREEN |
| 5b | `pending` bucket → `status: 'pending'` regardless of item | GREEN |
| 6a | Attention item appears in `paused` bucket | GREEN |
| 6b | Attention item in `paused` has `status: 'paused'` | GREEN |

## Validation

Ran `cd frontend && npm test` before and after:
- Before fix: 3 new tests RED (cases 1a/1b/1c), 10 passing, 21 pre-existing failures (unrelated)
- After fix: 13/13 new tests GREEN, 190 total passing (was 177), still 21 pre-existing failures — zero regressions

## Constraints
| Constraint | Value |
|------------|-------|
| Scope | `normalizeLane` helper only — `adaptV2QueueResponse` unchanged |
| Backward compat | v1-shape rows must be unaffected |
| Type safety | TypeScript widening via intersection type, no `any` |
| Do not touch | `frontend/src/hooks/useDispatchQueue.ts` (Mark WIP) |
| Do not touch | Any dashboard component — fix is data-mapping layer only |

## Codebase Context
| Aspect | Details |
|--------|---------|
| Fix file | `frontend/src/api/dispatch.ts` — `normalizeLane` function |
| Test file | `frontend/src/api/__tests__/dispatch.adapter.test.ts` |
| Type reference | `frontend/src/types/api.ts` — `DispatchItem` interface |
| Root cause | v2 API uses `leased_*`/`created_at`; v1 DispatchItem uses `claimed_*`/`enqueued_at` |
