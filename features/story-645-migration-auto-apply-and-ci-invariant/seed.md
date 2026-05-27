# Seed: STORY-645 — Migration auto-apply on deploy + CI invariant for column references

## Overview

| Field | Value |
|-------|-------|
| Mode | new_feature |
| Scope | small |
| Frontend | false |
| Feature Name | ops-console deploy.sh runs all `scripts/migrations/*.sql` idempotently on container start; CI gate fails any PR that references a column without a corresponding migration |
| Phase Path | 1 (Seed) → 7 (Test Design) → 8 (Implementation) → Done |
| Repo | tech-dev-agents |
| Target Branch | `story-645/migration-auto-apply-and-ci-invariant` |
| Status | Seed written 2026-04-26 ~01:40 UTC |
| Priority | 100 — eliminates a recurring bug class observed multiple times this week |

---

## 1. Idea / Trigger

Two production 500 errors this week were caused by code referencing a database column that didn't exist:

1. **2026-04-25 ~20:30 UTC**: `/api/dispatch/review/STORY-700` 500'd with `column "review_started_at" of relation "dispatch_items" does not exist`. The Pydantic model and service code referenced `review_started_at` (added in STORY-496), but the migration was never written. We applied migration `010_review_started_at.sql` manually.

2. **2026-04-26 ~01:30 UTC** during STORY-700 ship: code references the `dispatch_events` table from `routes/dispatch.py`. Migration `010_dispatch_events.sql` (STORY-700) needed manual application via the same nested-SSH-to-postgres pattern.

Pattern: code lands referencing a DB object that the deploy doesn't auto-provision. Discovered only when the endpoint 500s in production.

## 2. Problem Statement

- **Recurring bug class** (3+ instances this month): code references a column/table that exists in `scripts/migrations/` but was never applied to the production DB.
- **Manual remediation required every time** — SCP + nested SSH + docker exec psql. 5+ minutes per incident.
- **No CI gate** to catch the case where a PR references a column that has no migration.
- **Deploy script is silent** — `deploy.sh` rebuilds the container but doesn't run `scripts/migrations/*.sql`. The DB volume persists; new schema doesn't.

## 3. Scope Classification

**Small.** Two parts, two files each:

Part A — Auto-apply on deploy:
- `deployment/ops-console/deploy.sh` — add migration step before health check
- `tests/deployment/test_deploy_migrations.py` (new) — verify the auto-apply behavior

Part B — CI invariant:
- `.github/workflows/migration-invariant.yml` (new) or extend existing CI workflow — the gate
- A small Python script `scripts/ci/check_migrations_match_columns.py` that does the analysis
- `tests/scripts/test_check_migrations_match_columns.py` (new) — unit tests for the gate

No schema changes, no API changes, no front-end. Phase path `1 → 7 → 8 → Done`.

## 4. Codebase Context

### Affected files

- **`deployment/ops-console/deploy.sh`** — already has a migration step that was added in STORY-495. Verify it's actually wired and runs. Looking at the file: there's a `MIGRATIONS_DIR=...` variable mentioned but unclear whether it's used end-to-end. If it's not running, fix it. If it is, ensure migrations run BEFORE the health check (so the new container can serve traffic that depends on the migration).

- **`scripts/migrations/`** — already has the existing migrations 001-010. The auto-apply step iterates this directory in order (sort by filename which encodes ordering: `001_*.sql`, `002_*.sql`, etc.). Migrations are idempotent (use `IF NOT EXISTS`).

- **CI workflow** — find existing `.github/workflows/*.yml`. The new migration invariant workflow should run on PR open/update, scan the diff for added column-references in code (anything matching `[\'"]column_name[\'"]` or Pydantic field names that look like DB columns), and verify a corresponding migration file in the same PR adds that column.

  This is HEURISTIC, not perfect. Intentionally so — we want to catch ~80% of the cases without being so strict it false-positives constantly. False positives can be silenced with a comment magic string `# migration-ci: ignore`.

### Migration file naming convention

- Three-digit prefix + descriptive slug: `010_review_started_at.sql`, `011_dispatch_events.sql`
- Must include `IF NOT EXISTS` clauses (idempotent)
- Must be valid PostgreSQL DDL only (no DML in migrations to keep them safe to rerun)

### Deploy.sh integration approach

```bash
# After: container starts, postgres is healthy
# Before: health check on /api/health

run_migrations() {
  log "Running migrations from $MIGRATIONS_DIR"
  for migration in "$MIGRATIONS_DIR"/*.sql; do
    [ -f "$migration" ] || continue
    log "  Applying $(basename "$migration")"
    docker cp "$migration" "$POSTGRES_CONTAINER:/tmp/$(basename "$migration")"
    docker exec "$POSTGRES_CONTAINER" psql -U ops_console -d ops_console \
      -f "/tmp/$(basename "$migration")" 2>&1 | tee -a "$DEPLOY_LOG"
  done
  log "Migrations applied"
}
```

The migration step MUST be idempotent — every deploy re-runs all migrations. `IF NOT EXISTS` clauses make this safe.

### CI invariant — heuristic check

Scan the PR diff:
1. For each added or modified `.py` file in `tech_dev_agents/ops_console/`, find tokens of the form `Field("column_name", ...)`, `row.get("column_name")`, or Pydantic model attribute declarations that look like DB columns (typed with `str`, `datetime`, `int | None`, etc.).
2. For each such column reference:
   - Look up the column in the most-recent dispatch_items schema (parse `\d dispatch_items` output from a fixture file `tests/fixtures/dispatch_items_schema.txt` checked into the repo OR query a CI postgres service).
   - If column not in the canonical schema, look for a migration in `scripts/migrations/*.sql` in the same PR diff that adds that column.
   - If neither: fail the check, list missing columns.

Comment-based opt-out for false positives:
```python
# migration-ci: ignore — this isn't a DB column, it's an in-memory dataclass
```

## 5. The Fix

### Change 1: Update deploy.sh to run migrations

Update `deployment/ops-console/deploy.sh` to add a `run_migrations` step between "container started" and "health check". Make it idempotent. Log each migration applied. Log a summary line.

### Change 2: New CI workflow + check script

Add `.github/workflows/migration-invariant.yml`:
```yaml
name: Migration invariant
on:
  pull_request:
    paths:
      - "tech_dev_agents/**"
      - "scripts/migrations/**"
      - ".github/workflows/migration-invariant.yml"

jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -r scripts/ci/requirements.txt 2>/dev/null || true
      - run: python3 scripts/ci/check_migrations_match_columns.py origin/main HEAD
```

Add `scripts/ci/check_migrations_match_columns.py` that does the analysis described above. Exit 0 if no missing migrations. Exit 1 if missing detected (with a clear error listing each unsubstantiated column reference).

### Change 3: Schema fixture for canonical column list

Add `tests/fixtures/dispatch_items_schema.txt` (and any other table schemas being checked). Auto-update via a developer command (`make schema-fixture` or similar) when migrations change. The CI gate compares against this snapshot.

## 6. Out of Scope

- Migration rollback — explicitly NOT solved here. If a migration fails or needs reverting, that's a manual ops task. (Industry doesn't have a great answer to this either; deferring is fine.)
- Refactoring the migration runner into a Python tool with `up`/`down` semantics — overkill for the pilot; bash + IF NOT EXISTS is sufficient.
- Auto-applying migrations to dev/staging environments not running through deploy.sh — every CI environment that uses Postgres should call the same migration script, but standardizing that is a separate story.
- Static analysis on Python code to find ALL DB column references (would need a real type-checker plugin) — heuristic regex catches the common cases.
- Asana/Monday integration — N/A.

## Test Criteria

Phase 7 produces `test-design.md` and RED tests covering:

| Test | Setup | Expectation |
|---|---|---|
| **Auto-apply: empty DB** | fresh postgres container | all migrations run in order; final schema matches expected |
| **Auto-apply: half-migrated DB** | DB with migrations 001-005 already applied | re-runs 001-005 (idempotent), applies 006-010 |
| **Auto-apply: idempotent rerun** | DB with all migrations applied | second deploy runs each migration again, no errors, schema unchanged |
| **Auto-apply: malformed migration** | one migration has a syntax error | deploy.sh logs error, exits non-zero, container does NOT receive traffic |
| **Auto-apply: ordering** | migrations 001, 002, 003 (003 depends on 002) | runs in numeric order, never out-of-order |
| **CI invariant: missing migration** | PR adds `Field("foo")` referencing a `foo` column with no migration in the same PR | check script exits 1, lists `foo` as unsubstantiated |
| **CI invariant: migration present** | PR adds both the field reference AND a migration adding `foo` | check script exits 0 |
| **CI invariant: existing column** | PR references `story_id` (which was in 001_dispatch_queue.sql) | check script exits 0 (no new column referenced) |
| **CI invariant: opt-out comment** | `# migration-ci: ignore` adjacent to the field declaration | check script exits 0 (skipped) |
| **CI invariant: false-positive matching** | code uses `data.get("color")` where `color` is a dict key not a DB column | exits 0 (heuristic should distinguish based on filename / surrounding context) |

All tests are unit-level (mock subprocess for deploy tests, mock git diff for CI tests). No live infrastructure required.

## Validation

After Phase 8 lands:

1. Manually trigger a deploy on the ops-console VM: `bash /opt/ops-console/deployment/ops-console/deploy.sh` — confirm migrations run and log an audit line.
2. Submit a synthetic PR adding a Pydantic field referencing a non-existent column WITHOUT a migration — confirm CI fails with the missing-migration message.
3. Submit a synthetic PR adding both — confirm CI passes.
4. Drop a column manually on the dev DB, redeploy — confirm migration re-applies (the `ADD COLUMN IF NOT EXISTS` form catches the case).

## 9. Dispatch Notes

- Repo: tech-dev-agents
- Branch: `story-645/migration-auto-apply-and-ci-invariant`
- Scope: small
- Priority: 100 (eliminates recurring bug class)
- Expected runtime: Phase 7 ~15 min, Phase 8 ~30 min
- Implementing agent should: (a) read existing `deploy.sh`, identify whether the migration step exists or needs adding, (b) write the CI heuristic carefully — false positives are worse than false negatives for a gate, (c) include the schema fixture mechanism so devs can regenerate it.
