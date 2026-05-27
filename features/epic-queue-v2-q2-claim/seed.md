# Story Q2 — Atomic claim-next + Lease Token + Worker Version Contract

**Epic:** EPIC-Queue-v2
**Wave:** 2 (serial, must follow Q1)
**Scope:** Medium (2 days)
**Owner files:**
- `tech_dev_agents/ops_console/routes/dispatch_v2.py`
- `tech_dev_agents/ops_console/services/dispatch_v2_service.py` (additions only)
- `deployment/hermes/dispatch_poller_v2.py`

---

## Problem

The v1 dispatch surface has three structural defects that Q2 replaces:
1. Non-atomic claim: `/dispatch/next` + `/dispatch/claim/{id}` are two round-trips → race conditions, duplicate claims.
2. No lease token: workers can hold a claim indefinitely with no expiry enforcement, and stale-claim recovery is best-effort.
3. No protocol version contract: a stale poller running v1 code against a v2 server produces silent misbehaviour.

---

## What Q2 Delivers

### Atomic claim-next (AC1)
Single CTE using `FOR UPDATE SKIP LOCKED` — no two workers can claim the same job. The entire claim-plus-lease-plus-event write is a single database transaction.

### Lease token contract (AC2)
Every claim returns a `lease_token` (UUID). All subsequent mutations (`heartbeat`, `release`, `transition`) must present the matching token. Stale tokens return structured 409.

### Worker version middleware (AC3)
`X-Worker-Version` header on every v2 request. If the version is below `MIN_WORKER_VERSION` env var, return 426 with `{detail, min_required, got}`. Poller v2 exits with code 2 on 426.

### Manager-only claim-by-id (AC4)
`POST /api/dispatch/v2/claim-by-id` gated to `Role.MANAGER` for manual redispatch/rework flows.

### Eligibility in one place (AC6)
Role gating, quarantine exclusion, dependency satisfaction — all in the atomic CTE. No more poller-side dep extraction.

### Redispatch/rework contract (RT1–RT8)
`POST /api/dispatch/v2/redispatch` — idempotency key, correlation key uniqueness, parent-state guard, lineage endpoint.

---

## Endpoints

```
POST /api/dispatch/v2/claim-next
POST /api/dispatch/v2/heartbeat
POST /api/dispatch/v2/release
POST /api/dispatch/v2/transition
POST /api/dispatch/v2/claim-by-id   (MANAGER only)
POST /api/dispatch/v2/redispatch
GET  /api/dispatch/v2/queue
GET  /api/dispatch/v2/lineage/{job_id}
```

---

## Acceptance Criteria

- AC1: 100-thread fuzz, 0 duplicates over 10k claims
- AC2: stale lease token → 409 with structured body
- AC3: worker version below minimum → 426 with min_required field; poller exits 2 on 426
- AC4: claim-by-id works for MANAGER, 403 for AGENT
- AC5: v2 surface has no `/next`, `/claim/{id}`, `/release/{id}`, `/heartbeat/{id}` (v1 pattern)
- AC6: eligibility predicate <50ms p95 on 200-row queue

### Redispatch
- RT1: same idempotency_key 3x → 1 job
- RT2: concurrent redispatch same repo+pr → 1 active child, second gets 409
- RT3: PR head SHA mismatch → 422 + attention event
- RT4: child cancel/complete does not mutate parent terminal state
- RT5: claim-next not starved by duplicate correlation keys
- RT6: parent in non-terminal state → 409 unless force_cancel_parent=true AND MANAGER
- RT7: cross-repo dependency resolved on dep completion
- RT8: lineage endpoint returns ordered ancestor+descendant chain

---

## Gate B Criterion

100-thread claim fuzz 0 duplicates + RT1-RT8 all GREEN → PASS.
