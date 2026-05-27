# Phase 11 Pre-Deploy Checks

This directory contains executable gate scripts for Phase 11 predeploy validation.

## Canonical Runner

Use this entrypoint from repo root:

```bash
bash tests/predeploy/run_all.sh
```

`run_all.sh` currently uses the `check_*.sh` script set plus `lib.sh`.

## Script Families

This folder includes two script families:

- Active/canonical:
  - `run_all.sh`
  - `check_*.sh`
  - `lib.sh`
- Legacy/story-specific (retained for traceability):
  - `01_*` through `08_*`
  - `common.sh`

If both families define similar checks, follow the canonical family above.

## Check Coverage (Canonical)

- `check_cve.sh` - container image CVE scan
- `check_deps.sh` - dependency audit
- `check_secrets.sh` - secrets scan
- `check_drift.sh` - infrastructure drift detection
- `check_monitoring.sh` - monitoring health
- `check_logs.sh` - container log emission and Log Analytics ingestion
- `check_adapters.sh` - adapter/integration connectivity
- `check_migrations.sh` - migration chain verification
- `check_smoke.sh` - smoke test dry-run
- `check_cicd.sh` - CI/CD gate verification

## Required Environment Variables

- `IMAGE_TAG` - required for image CVE scan
- `BASE_URL` - required for endpoint/monitoring checks
- `DATABASE_URL` - required for database adapter checks
- `REDIS_URL` - optional, used when cache checks are enabled
- `AZURE_CONTAINER_APP` - required for log ingestion check
- `LOG_ANALYTICS_WORKSPACE` or `LOG_ANALYTICS_WORKSPACE_ID` - required for log ingestion check

Optional infrastructure variables (if using Azure what-if style drift checks):

- `AZURE_RESOURCE_GROUP`
- `AZURE_TEMPLATE_FILE`
- `AZURE_PARAMETERS_FILE`

## Status Rules

- `PASS` - check ran and met criteria
- `FAIL` - check ran and found an issue
- `BLOCKED` - check could not run meaningfully due missing prerequisites

Any `FAIL` or `BLOCKED` means deployment is not approved.

## Useful References

- Local run/test guide: `docs/local-run.md`
- Azure setup guide: `docs/azure-predeploy-setup.md`
