# Story Q1 — Seed

**Story:** Q1 — Schema + Event Log as Source of Truth
**Epic:** EPIC-Queue-v2
**Scope:** Medium (2 days)
**Branch:** epic-queue-v2/q1-schema
**Base:** feat/unified-queue-reliability

## Summary

Q1 introduces the v2 dispatch system's foundational data layer. Instead of
mutable status columns, job state is *derived* from an append-only event log
(`dispatch_v2_events`). A PostgreSQL trigger (`dispatch_state_apply_trg`)
maintains a projection table (`dispatch_state_current`) synchronously on every
event insert so read paths are O(1) lookups rather than fold-over-history.

This story is **additive only**: no existing tables or routes are modified.
All new objects live in migration 050 (intentional gap after 014).

## Deliverables

- `scripts/migrations/050_dispatch_v2_schema.sql` — tables, indexes, triggers
- `tech_dev_agents/ops_console/models/dispatch_v2.py` — Pydantic v2 models
- `tech_dev_agents/ops_console/services/dispatch_v2_service.py` — service layer

## Key Design Decisions

1. **Event log is authoritative.** `dispatch_state_current` is a cache;
   `replay_state()` can rebuild it from events at any time.
2. **Trigger enforces constraints.** `dispatch_failed_required_trg` prevents
   inserting a `failed` event without `failure_class` + `failure_reason` in
   `event_data`.
3. **State/lane mapping from architecture.md § 4** is the single source of
   truth for what each event type produces.
4. **`heartbeat` does not change state or lane** — it only updates
   `heartbeat_at`/`expires_at` on `dispatch_leases`.
5. **`needs_info` lane is disambiguated by `event_data.kind`** — `question`
   maps to `human_queue`, `attention` maps to `attention_queue`.
6. **`failed` lane is runtime-determined** — the policy table (Q3) maps
   `failure_class` to next lane. In Q1 we default to `attention_queue`.

## Acceptance Criteria (7 ACs)

- AC1: Tables + triggers exist after migration 050
- AC2: `record_event('failed', {})` raises (failure_class required)
- AC3: `record_event('leased', ...)` updates `dispatch_state_current` in same TX
- AC4: `replay_state(job_id)` matches `dispatch_state_current.state` for 1000-job seed
- AC5: Existing `dispatch_items`/`dispatch_events` untouched
- AC6: Contract test asserts `dispatch_state_current.state` is from canonical set
- AC7: SQL CHECK on `event_type` enforced (bad type raises)
