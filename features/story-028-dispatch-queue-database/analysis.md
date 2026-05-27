# Phase 4: Analysis — Dispatch Queue Database Persistence & History

**Story:** STORY-028
**Phase:** 4 (Analysis)
**Date:** 2026-04-12
**Scope:** Medium
**Analyst:** Sonnet

---

## 1. Summary

This analysis evaluates three approaches for migrating the dispatch queue from JSON file persistence to PostgreSQL, adding lifecycle tracking (completed/cancelled states), history querying, and agent auto-registration. The evaluation scores each approach against 8 success criteria derived from the 15 acceptance criteria in the seed.

---

## 2. Success Criteria

| ID | Criterion | Weight | Source ACs |
|----|-----------|--------|-----------|
| SC-1 | Backward compatibility — all existing API response contracts preserved | Critical | AC-14 |
| SC-2 | Data integrity — ACID transactions, no double-claims, no duplicate active dispatches | Critical | AC-4, AC-9 |
| SC-3 | Lifecycle tracking — completed/cancelled terminal states with timestamps | High | AC-5, AC-6 |
| SC-4 | History queryability — paginated GET endpoint with filters | High | AC-7 |
| SC-5 | Agent auto-registration — upsert on first contact, Teams client refresh | High | AC-10, AC-11 |
| SC-6 | Migration safety — idempotent DDL, no data loss | High | AC-13 |
| SC-7 | Operational simplicity — minimal new dependencies, clear failure modes | Medium | AC-12 |
| SC-8 | Frontend integration — history tab with status badges and duration | Medium | AC-15 |

---

## 3. Approaches

### Approach A: Direct asyncpg with Raw SQL (Recommended)

**Description:** Replace `DispatchQueueService` with a new `DispatchDBService` that uses `asyncpg` directly with parameterized SQL queries. Connection pool managed in FastAPI lifespan. Raw SQL migration script for schema creation.

**Architecture:**
- `asyncpg.create_pool()` in lifespan → `app.state.db_pool`
- New `DispatchDBService(pool)` class with async methods mirroring current service interface
- Each method executes parameterized SQL via `pool.fetchrow()` / `pool.fetch()` / `pool.execute()`
- Routes swap `request.app.state.dispatch_service` → `request.app.state.dispatch_db_service`
- Stale recovery loop becomes a single `UPDATE ... WHERE status='claimed' AND claimed_at < now() - interval` query
- SQL migration in `scripts/migrations/001_dispatch_queue.sql`

**File changes:**
| File | Action | Est. Lines |
|------|--------|-----------|
| `scripts/migrations/001_dispatch_queue.sql` | Create | ~40 |
| `services/dispatch_db_service.py` | Create | ~200 |
| `routes/dispatch.py` | Modify | ~80 |
| `models/responses.py` | Modify | ~30 |
| `config.py` | Modify | ~5 |
| `main.py` | Modify | ~30 |
| `deployment/hermes/dispatch_poller.py` | Modify | ~20 |
| `frontend/src/components/DispatchQueue.tsx` | Modify | ~80 |
| `frontend/src/hooks/useDispatchQueue.ts` | Modify | ~20 |
| `frontend/src/types/api.ts` | Modify | ~15 |
| `tests/ops_console/test_dispatch_db_service.py` | Create | ~250 |
| `tests/ops_console/test_routes_dispatch.py` | Modify | ~50 |
| `pyproject.toml` | Modify | ~2 |
| **Total** | | **~820** |

**Evaluation:**

| SC | Score | Notes |
|----|-------|-------|
| SC-1 | 5/5 | Routes return same Pydantic models; only service layer swapped |
| SC-2 | 5/5 | PostgreSQL transactions + `SELECT ... FOR UPDATE` for claims; partial unique index prevents duplicate active dispatches |
| SC-3 | 5/5 | New `completed` and `cancelled` status values; `completed_at`/`cancelled_at` columns |
| SC-4 | 5/5 | Single `SELECT` with `WHERE status IN (...)`, `LIMIT/OFFSET`, `ORDER BY` |
| SC-5 | 5/5 | Upsert into `agents` table on claim/next; refresh Teams client on INSERT |
| SC-6 | 5/5 | `CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS` — fully idempotent |
| SC-7 | 5/5 | Single new dependency (asyncpg). Pool lifecycle is 10 lines in lifespan. DB down → 503. |
| SC-8 | 5/5 | New History tab, status badges for 4 states, duration computed from timestamps |
| **Total** | **40/40** | |

**Pros:**
- Fastest async PostgreSQL driver (C extension, binary protocol)
- Built-in connection pool — no external dependency
- No ORM overhead for a simple 2-table schema
- Matches seed architecture exactly — minimal design risk
- Raw SQL gives full control over `SELECT FOR UPDATE`, partial indexes, `RETURNING *`
- Service class mirrors existing interface — routes need minimal changes

**Cons:**
- No ORM migration tracking (raw SQL scripts only)
- SQL strings in Python (mitigated by parameterized queries, no injection risk)
- asyncpg-specific API (not swappable to another driver without changes)

**Risks:**
- asyncpg connection errors on startup → **Mitigated:** fail-fast with clear log message, health endpoint reports DB status
- Pool exhaustion → **Mitigated:** max=10 is way more than 2 agents polling at 60s intervals

---

### Approach B: SQLAlchemy Async + asyncpg Backend

**Description:** Use SQLAlchemy 2.0 async ORM with `asyncpg` as the dialect backend. Define models with `DeclarativeBase`, use `AsyncSession` for queries, manage connection pool via `create_async_engine`.

**Architecture:**
- `create_async_engine("postgresql+asyncpg://...")` in lifespan
- SQLAlchemy ORM models: `DispatchItemModel`, `AgentModel`
- `async_sessionmaker` for per-request sessions
- Alembic for migration management
- Routes use dependency-injected sessions

**File changes:**
| File | Action | Est. Lines |
|------|--------|-----------|
| `models/db_models.py` | Create | ~60 |
| `services/dispatch_db_service.py` | Create | ~250 |
| `alembic/` directory + config | Create | ~100 |
| `alembic/versions/001_*.py` | Create | ~50 |
| `routes/dispatch.py` | Modify | ~80 |
| `models/responses.py` | Modify | ~30 |
| `config.py` | Modify | ~5 |
| `main.py` | Modify | ~40 |
| `deployment/hermes/dispatch_poller.py` | Modify | ~20 |
| Frontend files (same as A) | Modify | ~115 |
| Tests | Create/Modify | ~300 |
| `pyproject.toml` | Modify | ~5 |
| **Total** | | **~1055** |

**Evaluation:**

| SC | Score | Notes |
|----|-------|-------|
| SC-1 | 5/5 | Same — routes return Pydantic models |
| SC-2 | 4/5 | ORM handles transactions well, but `SELECT FOR UPDATE` requires explicit `with_for_update()`. Partial unique index needs raw DDL in Alembic migration. |
| SC-3 | 5/5 | Same outcome, ORM column definitions |
| SC-4 | 5/5 | SQLAlchemy query builder is powerful for filtering |
| SC-5 | 4/5 | Upsert (`on_conflict_do_update`) is more verbose in SQLAlchemy |
| SC-6 | 5/5 | Alembic provides versioned migrations with upgrade/downgrade |
| SC-7 | 3/5 | Adds 3 dependencies (sqlalchemy, alembic, asyncpg). ORM introduces mapping complexity. Alembic requires `alembic.ini`, `env.py`, versions directory. |
| SC-8 | 5/5 | Same frontend work |
| **Total** | **36/40** | |

**Pros:**
- Industry-standard ORM — familiar to most Python developers
- Alembic provides migration versioning and rollback
- Query builder prevents SQL typos
- Future-proofs for more complex queries

**Cons:**
- 30% more code for the same outcome (ORM boilerplate, Alembic config)
- 3 new dependencies vs. 1
- Project has zero existing ORM patterns — introduces new paradigm
- `SELECT FOR UPDATE` and partial unique indexes require raw SQL escapes anyway
- Slower than raw asyncpg (ORM mapping overhead)
- Alembic setup is overkill for a 2-table schema

---

### Approach C: psycopg3 Async with Raw SQL

**Description:** Same as Approach A but using `psycopg[binary]` (v3) instead of `asyncpg`. psycopg3 has native async support, uses `libpq`, and follows DB-API 2.0 conventions more closely.

**Architecture:**
- `psycopg_pool.AsyncConnectionPool` in lifespan
- Same service class structure as Approach A
- Same raw SQL migration script
- Uses `%s` placeholders instead of `$1, $2`

**File changes:** Same as Approach A (~820 lines)

**Evaluation:**

| SC | Score | Notes |
|----|-------|-------|
| SC-1 | 5/5 | Same — routes return Pydantic models |
| SC-2 | 5/5 | Same transaction/lock support |
| SC-3 | 5/5 | Same |
| SC-4 | 5/5 | Same |
| SC-5 | 5/5 | Same |
| SC-6 | 5/5 | Same raw DDL approach |
| SC-7 | 4/5 | Requires `psycopg[binary]` + `psycopg_pool` (2 packages). Pool is external, not built-in. |
| SC-8 | 5/5 | Same frontend work |
| **Total** | **39/40** | |

**Pros:**
- DB-API 2.0 compliant — more standard Python DB interface
- Better error messages (uses libpq natively)
- Connection pool available but as separate package
- `%s` parameter style more familiar to Python developers

**Cons:**
- Slightly slower than asyncpg in benchmarks (asyncpg uses binary protocol directly)
- Connection pool is a separate package (`psycopg_pool`)
- Less common in FastAPI projects (asyncpg is the de facto standard)
- Seed already specifies asyncpg — deviating adds unnecessary design friction

---

## 4. Comparison Matrix

| Criterion | Weight | A: asyncpg raw | B: SQLAlchemy | C: psycopg3 |
|-----------|--------|:-:|:-:|:-:|
| SC-1: Backward compat | Critical | 5 | 5 | 5 |
| SC-2: Data integrity | Critical | 5 | 4 | 5 |
| SC-3: Lifecycle tracking | High | 5 | 5 | 5 |
| SC-4: History query | High | 5 | 5 | 5 |
| SC-5: Auto-registration | High | 5 | 4 | 5 |
| SC-6: Migration safety | High | 5 | 5 | 5 |
| SC-7: Operational simplicity | Medium | 5 | 3 | 4 |
| SC-8: Frontend integration | Medium | 5 | 5 | 5 |
| **Total** | | **40** | **36** | **39** |

---

## 5. Recommendation

**Approach A: Direct asyncpg with Raw SQL**

**Rationale:**
1. **Perfect score (40/40)** — meets all success criteria without compromise
2. **Minimal footprint** — 1 new dependency, ~820 lines of changes, no new paradigms
3. **Seed alignment** — matches the seed's architecture exactly, eliminating design risk
4. **Performance** — asyncpg is the fastest Python PostgreSQL driver; critical for a polling-based system
5. **Simplicity** — raw SQL for a 2-table schema is clearer than ORM boilerplate; the team has no existing ORM patterns to match
6. **FastAPI ecosystem standard** — asyncpg is the most common choice for FastAPI + PostgreSQL projects

Approach B (SQLAlchemy) adds unnecessary complexity for a simple schema. Approach C (psycopg3) is a viable alternative but contradicts the seed's explicit asyncpg choice and is marginally slower.

---

## 6. Implementation Strategy (Approach A)

### Order of Operations
1. **Database setup** — Create role/database, run migration DDL
2. **Service layer** — `DispatchDBService` with all methods (enqueue, claim, cancel, complete, history, recover_stale, auto-register)
3. **Model updates** — Add `COMPLETED`/`CANCELLED` to enum, add history response models
4. **Route updates** — Swap service, add `/complete`, `/history`, `/agents/register` endpoints
5. **Config + lifespan** — Add `database_url`, create pool, wire new service
6. **Agent-side** — `X-Agent-Name` header, completion callback in poller
7. **Frontend** — History tab, new status badges, duration column
8. **Tests** — Unit tests for service, integration tests for routes, migration idempotency test

### Critical Implementation Details
- **Claim atomicity:** `UPDATE ... SET status='claimed' WHERE status='pending' AND story_id=$1 RETURNING *` — single atomic statement, no separate SELECT needed
- **Partial unique index:** `CREATE UNIQUE INDEX ... ON dispatch_items (story_id) WHERE status IN ('pending', 'claimed')` — allows multiple completed/cancelled records for the same story
- **Timestamp serialization:** All timestamps stored as `TIMESTAMPTZ`, serialized to ISO 8601 strings in response models to preserve backward compatibility
- **Pool sizing:** min=2, max=10 — conservative for 2-agent fleet with 60s poll interval
- **Error handling:** DB errors → 503 Service Unavailable (no silent fallback to JSON)

---

## 7. Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| PostgreSQL down at startup | Low | High | Fail fast, clear error log, health endpoint reports DB status |
| Breaking existing API contracts | Low | Critical | Existing STORY-026 route tests run unchanged as regression suite |
| Data loss during migration | Low | High | One-time import script; JSON file preserved as backup |
| asyncpg pool exhaustion | Very Low | Medium | Max 10 connections, 2 agents at 60s intervals = ~0.03 connections/sec |
| Partial unique index not enforced | Very Low | High | Integration test: double enqueue → 409 |
| Frontend history tab breaks layout | Low | Low | Manual verification; existing components provide layout patterns |
