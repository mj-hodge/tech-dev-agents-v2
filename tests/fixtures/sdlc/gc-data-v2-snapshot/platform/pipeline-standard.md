# Pipeline Standard

Vendored fixture for STORY-1002 tests. Represents a minimal gc-data-v2/platform/pipeline-standard.md
with all 14 canonical axes. Used by `tests/sdlc/test_load_canon_skill.py` group B tests.

Pinned to: gc-data-v2 fictional SHA `a7c1bf2e0d9f` (fixture only — not a real commit).

---

## The 14 Axes

Every pipeline build must satisfy all 14 axes. An axis may be marked `n/a` with justification,
or `fail` with a Mark-approved deviation note. See `skills/phase-6/SKILL.md` for the advance gate.

---

## Axis 1: Repo Structure

Repository directory layout must match `gc-data-v2/pipeline-template/` scaffold.

Required directories:
- `airflow/dags/` — DAG definitions
- `dbt/` — dbt models, macros, tests
- `tests/contracts/` — contract test files
- `infra/` — Terraform or CloudFormation

---

## Axis 2: Layers

Data must flow through the canonical four-layer architecture:

1. **Landing** — raw files/events as received from source
2. **Bronze** — validated, typed, and deduplicated landing data
3. **Silver** — joined, enriched, business-logic-applied data
4. **Gold** — aggregated, query-optimised, consumer-ready data

Cross-layer skipping is not permitted without an approved deviation.

---

## Axis 3: Ingestion Patterns

Ingestion must use one of the approved patterns from `airflow-conventions.md`:

- **Batch file drop** — source writes files to S3 landing zone; DAG polls and processes.
- **API pull** — DAG calls source API; results written to landing zone before bronze.
- **CDC stream** — Debezium or Fivetran feeds; DAG triggers on Kafka or webhook.

Custom ingestion patterns require deviation approval.

---

## Axis 4: Materialiser

All Iceberg table writes must go through the canonical materialiser:

- `pipeline-template/src/materialiser/iceberg_writer.py` (or source-repo equivalent)
- Must use the standard schema registry lookup before each write
- Partition strategy must follow `iceberg-conventions.md` section 3

Direct Spark/Glue writes bypassing the materialiser are not permitted.

---

## Axis 5: Contracts

Data contracts must be defined for every external-facing silver/gold table:

- `entities.yaml` — canonical entity schema definition
- Tested by `tests/contracts/test_entities_yaml.py`
- Conformance verified by `tests/contracts/test_conformance.py`

---

## Axis 6: DAG Shape

DAGs must follow the naming and structural conventions in `airflow-conventions.md`:

- Name format: `<source>_<layer>_<frequency>` (e.g., `walmart_bronze_daily`)
- Max task depth: 5 levels
- Retry policy: 3 retries, exponential backoff, max 1h delay
- Alerting: all DAGs must have on-failure email/PagerDuty hook

---

## Axis 7: Auth

Authentication must use the approved patterns:

- **Service-to-service:** OIDC token from GitHub Actions / Azure AD workload identity
- **Secret storage:** Azure Key Vault only; no hardcoded credentials
- **Cross-account S3:** IAM role assumption with explicit trust policy

See `auth-patterns.md` for templates.

---

## Axis 8: Observability

Every pipeline must instrument:

- DAG-run success/failure Airflow metrics
- Task-level retry counter
- Iceberg materialiser duration (P50, P95)
- Landing-to-bronze freshness lag

Metrics must be exported to the shared Prometheus/Grafana stack.

---

## Axis 9: dbt

dbt models must follow:

- **Naming:** `<layer>_<source>_<entity>` (e.g., `silver_walmart_orders`)
- **Materialisation:** bronze = view, silver = incremental, gold = table
- **Tests:** every model must have at least `not_null` + `unique` tests on PK columns
- **Sources:** defined in `models/sources.yml`

See `dbt-conventions.md` for full conventions.

---

## Axis 10: CI

CI must enforce:

- **Gate 1:** `pytest tests/contracts/` — both contract tests pass
- **Gate 2:** `dbt test` — all dbt tests pass against staging data
- **Gate 3:** `pytest tests/` — all unit tests pass
- **Gate 4:** `sqlfluff lint` — no linting errors in SQL models

See `failure-modes.md` § CI gates for failure taxonomy.

---

## Axis 11: Deploy

Deployments must pass all five deploy gates from `failure-modes.md`:

1. OIDC token valid and not expired
2. All vendored dependencies pinned and hash-verified
3. Container base image GLIBC version matches pin
4. All Key Vault references resolve
5. Post-deploy smoke: DAG import succeeds, no import errors

---

## Axis 12: Runbook

Every pipeline must have runbooks covering:

- DAG run failure
- Landing zone stale (no new files)
- Contract test failure in CI
- Iceberg materialiser timeout
- Bronze freshness breach

Each runbook must cross-link the matching section in `failure-modes.md`.

---

## Axis 13: SLOs

SLO targets must be defined per tier (from `slo-targets.md`):

| SLI | Standard Tier | Premium Tier |
|-----|--------------|-------------|
| DAG success rate | ≥ 99% / 7d | ≥ 99.9% / 7d |
| Last-success age | < 2× schedule | < 1× schedule |
| Materialiser p95 duration | < 30 min | < 10 min |
| Landing-to-bronze lag | < 4h | < 1h |

---

## Axis 14: Security

Security requirements:

- No credentials in source code or DAG files
- All S3 buckets: encryption at rest (SSE-S3 or KMS), versioning enabled
- Iceberg tables: column-level access controls for PII columns
- DAG execution logs: retention ≥ 90 days
- Quarterly access review for all service accounts

See `auth-patterns.md` § Security posture for full checklist.
