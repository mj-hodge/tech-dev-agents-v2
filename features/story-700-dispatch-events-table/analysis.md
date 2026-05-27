# Phase 4 Analysis — STORY-700: `dispatch_events` Log Table

## Routing Note

STORY-700's `seed.md` defined a phase path of `1 → 7 → 8 → Done`, skipping formal analysis. This phase was explicitly dispatched by the team lead, overriding the abbreviated path. The analysis validates — rather than invents — the seeded design. `QUESTION.md` is superseded; this deliverable is the routing answer.

---

## Approaches Evaluated

Three approaches were defined for parallel analysis:

| ID | Name | Summary |
|----|------|---------|
| A | Minimal-column JSONB (seeded) | `dispatch_events` table with 6 native columns + JSONB payload; `emit()` never raises |
| B | Wide-column (no JSONB) | Same table scope but all fields explicit native columns; no JSONB |
| C | Loki-only (no Postgres table) | Structured log lines ingested by Loki; no schema migration |

---

## Scoring Matrix

### Weights

| Dimension | Weight | Rationale |
|-----------|--------|-----------|
| Technical | 40% | Correctness and maintainability are primary for infrastructure stories |
| Business | 35% | Diagnostic velocity and downstream unblock are high-priority |
| Risk | 25% | Production queue safety is non-negotiable; weighted accordingly |

### Raw Scores (1–5, 5 = best)

| Criterion | A | B | C |
|-----------|---|---|---|
| **Technical** | | | |
| Schema evolution | 5 | 2 | 5 |
| Query flexibility | 4 | 5 | 2 |
| Durability / reliability | 5 | 5 | 2 |
| Operational complexity | 4 | 3 | 4 |
| Codebase alignment | 5 | 3 | 2 |
| _Technical avg_ | **4.6** | **3.6** | **3.0** |
| **Business** | | | |
| Debug velocity improvement | 5 | 5 | 3 |
| Downstream story unblock | 5 | 5 | 2 |
| Implementation effort | 4 | 2 | 5 |
| Operational value density | 5 | 3 | 2 |
| Risk to existing operations | 5 | 4 | 3 |
| _Business avg_ | **4.8** | **3.8** | **3.0** |
| **Risk** | | | |
| Production queue safety | 5 | 5 | 5 |
| Data loss risk | 3 | 2 | 1 |
| Migration risk | 4 | 2 | 5 |
| Rollback complexity | 4 | 3 | 5 |
| Scope creep risk | 3 | 4 | 2 |
| _Risk avg_ | **3.8** | **3.2** | **3.6** |

### Weighted Composite

| Approach | Technical (×0.40) | Business (×0.35) | Risk (×0.25) | **Weighted Score** |
|----------|-------------------|------------------|--------------|-------------------|
| A — Minimal-column JSONB | 1.84 | 1.68 | 0.95 | **4.47** |
| B — Wide-column | 1.44 | 1.33 | 0.80 | **3.57** |
| C — Loki-only | 1.20 | 1.05 | 0.90 | **3.15** |

---

## Top 3 Ranking

1. **Approach A — Minimal-column JSONB** (4.47) ✅ **SELECTED**
2. **Approach B — Wide-column** (3.57)
3. **Approach C — Loki-only** (3.15)

---

## Key Decisions Confirmed

These design decisions from the seed are validated by the analysis; they are not re-opened:

| Decision | Choice | Validation |
|----------|--------|------------|
| Storage backend | Postgres (not Loki) | C scores 3.15; SQL join semantics, durability, and downstream compatibility all favor Postgres |
| Schema style | Minimal columns + JSONB payload | B's 3.57 vs A's 4.47; fixed schema churn and migration risk outweigh JSONB query syntax cost |
| Mutation strategy | Events alongside `dispatch_items` (not replacing) | Zero blast radius; both tables independently consistent |
| emit() error handling | Never raises; log + continue | Mandatory — queue safety is non-negotiable (all three approaches score 5 on this) |
| Retention | 120-day batched DELETE (10k-row loop) | Prevents unbounded table growth; weekly cron is low-urgency |
| Indexes | `(story_id, repo, ts)`, `(event_type, ts)`, `(agent, ts) WHERE agent IS NOT NULL` | Covers all reference queries in seed §7; partial index on agent avoids sparse-value bloat |

---

## Risk Register

| # | Approach | Risk | L | I | Mitigation |
|---|----------|------|---|---|------------|
| 1 | A | Silent event loss on `emit()` failure (log-only, no retry) | Med | High | Structured error logging with alert threshold; consider bounded retry on transient DB errors in follow-up story |
| 2 | A | Retention DELETE misconfiguration drops events permanently | Low | High | Pre-test in staging; dry-run audit log before DELETE executes; weekly cron output logged for Morris to monitor |
| 3 | A | JSONB payload becomes unversioned dump if producers lack discipline | Med | Med | Document payload schema per event_type in service docstring; PR reviews enforce field list |
| 4 | B (not selected) | ALTER TABLE migrations compound across downstream stories 701/702/720 | High | High | Rejected; JSONB avoids this entirely |
| 5 | B (not selected) | NULL columns on unexpected payload shapes → silent data gaps | Med | High | Rejected; JSONB is free-form and handles partial data gracefully |
| 6 | C (not selected) | Loki pipeline downtime silences events; no Postgres fallback | Med | High | Rejected; durability requirement rules out Loki-only |
| 7 | C (not selected) | Downstream stories 701/702/720 must rearchitect for LogQL | High | Med | Rejected; SQL-first unblock is a hard requirement |

**Net risk for selected approach (A):** Low–Medium. The only material risk is silent event loss during transient DB failures — acceptable given the "observability is best-effort, queue is the primary concern" design contract.

---

## Approach A — Full Description (selected)

### Schema (`scripts/migrations/010_dispatch_events.sql`)

```sql
CREATE TABLE IF NOT EXISTS dispatch_events (
  id          BIGSERIAL    PRIMARY KEY,
  story_id    VARCHAR(20)  NOT NULL,
  repo        VARCHAR(200) NOT NULL,
  event_type  VARCHAR(40)  NOT NULL,
  agent       VARCHAR(50),
  phase_num   SMALLINT,
  payload     JSONB        NOT NULL DEFAULT '{}'::jsonb,
  ts          TIMESTAMPTZ  NOT NULL DEFAULT now(),
  CONSTRAINT valid_event_type CHECK (event_type ~ '^[a-z_]+$')
);

CREATE INDEX IF NOT EXISTS idx_de_story_repo_ts ON dispatch_events (story_id, repo, ts);
CREATE INDEX IF NOT EXISTS idx_de_event_type_ts ON dispatch_events (event_type, ts);
CREATE INDEX IF NOT EXISTS idx_de_agent_ts      ON dispatch_events (agent, ts) WHERE agent IS NOT NULL;
```

### Service (`tech_dev_agents/ops_console/services/dispatch_events.py`)

```python
async def emit(
    story_id: str,
    repo: str,
    event_type: str,
    *,
    agent: str | None = None,
    phase_num: int | None = None,
    payload: dict | None = None,
) -> None:
    """Best-effort event write. NEVER raises to caller.
    If events and dispatch_items disagree on state, events are source of truth.
    """
```

### Producer Call-sites

| File | Events | Count |
|------|--------|-------|
| `routes/dispatch.py` | enqueued, claimed, completed, failed, cancelled, released, needs_info_set, resumed | 8 |
| `sdlc_phase_runner.py` | phase_started, phase_ended (+duration_s in payload), sdk_invoke_started, sdk_invoke_ended, validation_passed, validation_failed | 6 |
| `dispatch_poller.py` | retry_enqueued, rate_limited (+reset_time in payload) | 2 |

### Retention

Weekly DELETE via Morris cron (Sunday 04:00 UTC). Batched 10k rows/loop to avoid long transactions. Target: `ts < now() - interval '120 days'`.

---

## Recommendation

**Proceed with Approach A as specified in the seed.** The analysis confirms all four major seed decisions (Postgres, JSONB, alongside-not-replacing, never-raises). No seed design choices are overturned.

The only open question is whether `emit()` should have a bounded retry (1–2 attempts with immediate backoff) for transient DB errors. The seed's "never raises" contract is satisfied either way. Recommend: **start with log-only** (simpler, lower scope), add retry in a follow-up hardening story if silent drops are observed in production monitoring.

**Confidence:** High (4.47/5 weighted score; nearest alternative 0.90 points below).

---

## Phase Path Update

This analysis was dispatched to fill the gap between Phase 1 (Seed) and Phase 7 (Test Design). Updated path:

> 1 (Seed) → **4 (Analysis)** → 7 (Test Design) → 8 (Implementation) → Done

No Phase 5 (Selection) needed — single winning approach with high confidence; no alternative merits formal selection deliberation.

---

## Follow-ups (not in scope for this story)

- Bounded retry for transient DB errors in `emit()` (hardening story)
- Read API at `/api/dispatch/events` (monitoring story post-alerts)
- Frontend timeline view (UI story after alerts shipped)
- `scripts/backfill_dispatch_events.py` (hand-run only; design exists in seed §4)
