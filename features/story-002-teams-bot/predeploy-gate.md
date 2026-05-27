# STORY-002 Pre-Deploy Gate

Run window: `2026-03-26T16:47:38Z` to `2026-03-26T16:47:45Z`

Harness executed once:

- `tests/predeploy/run_all.sh`
- Exit code: `1`

## Gate Summary

| # | Check | Status | Evidence | Remediation |
|---|---|---|---|---|
| 1 | Container Image CVE Scan | BLOCKED | `trivy` not installed; no `IMAGE_TAG` / local image reference was available | Install `trivy`, set the release image reference, and rescan |
| 2 | Dependency Audit | BLOCKED | `pip-audit` could not reach `pypi.org` due DNS/network failure during vulnerability lookup | Re-run in a network-enabled environment or with an offline vulnerability DB/cache |
| 3 | Secrets Scan | BLOCKED | Official scanners (`gitleaks` / `trufflehog`) were unavailable; fallback regex sweep found no obvious secret patterns | Install `gitleaks` or `trufflehog` and rerun the scan |
| 4 | Infrastructure Drift Detection | BLOCKED | No `infra/` config or drift tool was present in the worktree | Add the infra definition and run `terraform plan` or `az deployment group what-if` |
| 5 | Monitoring Health | BLOCKED | `curl` to `http://127.0.0.1:3978/health` failed to connect; no alert rules file was found | Start the service or point `BASE_URL` to a live environment, and add alert rules |
| 6 | Adapter / Integration Connections | BLOCKED | `DATABASE_URL` and `REDIS_URL` are unset; Monday API reachability failed with DNS/network error | Provide env vars and re-run connectivity checks from a networked runtime |
| 7 | Migration Chain Verification | BLOCKED | `alembic heads` failed: `No 'script_location' key found in configuration` | Add the Alembic config/migrations and verify a single head |
| 8 | Smoke Test Dry-Run | BLOCKED | `pytest -m smoke` collected no smoke tests; fallback full suite passed: `32 passed in 1.35s` | Add smoke-marked tests if this gate is required for deploy approval |

## Evidence

### 1. Container Image CVE Scan

Timestamp: `2026-03-26T16:47:38Z`

Command path: `tests/predeploy/check_cve_scan.sh`

Result:

```text
2026-03-26T16:47:38Z | 1/CVE_SCAN | BLOCKED: trivy is not installed in this environment
```

Blocker:

- No `trivy` binary is available in this environment.
- No release image reference was provided via `IMAGE_TAG` / `IMAGE_REF`.

### 2. Dependency Audit

Timestamp: `2026-03-26T16:47:39Z`

Command path: `tests/predeploy/check_dependency_audit.sh`

Result:

```text
2026-03-26T16:47:39Z | 2/DEPENDENCY_AUDIT | FAIL: pip-audit reported vulnerabilities (dependency groups: unknown); raw output: ... Failed to establish a new connection: [Errno -2] Name or service not known ... HTTPSConnectionPool(host='pypi.org', port=443) ...
```

Interpretation:

- The command did not complete a vulnerability lookup because the environment could not resolve or reach `pypi.org`.
- Operationally this is a gate blocker, not a product vulnerability finding.

Remediation:

- Re-run `pip-audit` from a network-enabled environment or provide an offline vulnerability feed/cache.

### 3. Secrets Scan

Timestamp: `2026-03-26T16:47:40Z`

Command path: `tests/predeploy/check_secrets_scan.sh`

Result:

```text
2026-03-26T16:47:40Z | 3/SECRETS_SCAN | BLOCKED: official secret scanners are unavailable; fallback regex sweep found no obvious secret patterns
```

Blocker:

- `gitleaks` and `trufflehog` were not installed.

### 4. Infrastructure Drift Detection

Timestamp: `2026-03-26T16:47:40Z`

Command path: `tests/predeploy/check_infra_drift.sh`

Result:

```text
2026-03-26T16:47:40Z | 4/INFRA_DRIFT | BLOCKED: no infrastructure config or drift tool is available in this worktree
```

Blocker:

- There is no `infra/` directory or equivalent IaC manifest in this worktree.

### 5. Monitoring Health

Timestamp: `2026-03-26T16:47:40Z`

Command path: `tests/predeploy/check_monitoring_health.sh`

Result:

```text
curl: (7) Failed to connect to 127.0.0.1 port 3978 after 0 ms: Couldn't connect to server
2026-03-26T16:47:40Z | 5/MONITORING_HEALTH | BLOCKED: curl failed for /health against http://127.0.0.1:3978: curl exit 7
```

Blocker:

- No running service was available on the local health endpoint.
- No alert rule file was found in the expected monitoring paths.

### 6. Adapter / Integration Connections

Timestamp: `2026-03-26T16:47:40Z`

Command path: `tests/predeploy/check_adapter_connections.sh`

Result:

```text
2026-03-26T16:47:40Z | 6/ADAPTER_CONNECTIONS | BLOCKED: DATABASE_URL is not set; REDIS_URL is not set; Monday API reachability failed: ... Failed to establish a new connection: [Errno -2] Name or service not known ... HTTPSConnectionPool(host='api.monday.com', port=443) ...
```

Blockers:

- Database and cache credentials are not configured in the environment.
- External API reachability to Monday could not be established from this runtime.

### 7. Migration Chain Verification

Timestamp: `2026-03-26T16:47:41Z`

Command path: `tests/predeploy/check_migration_chain.sh`

Result:

```text
2026-03-26T16:47:41Z | 7/MIGRATION_CHAIN | BLOCKED: alembic heads failed: FAILED: No 'script_location' key found in configuration.
```

Blocker:

- No Alembic configuration or migration tree is present in this worktree.

### 8. Smoke Test Dry-Run

Timestamp: `2026-03-26T16:47:45Z`

Command path: `tests/predeploy/check_smoke_tests.sh`

Result:

```text
2026-03-26T16:47:45Z | 8/SMOKE_TESTS | BLOCKED: no smoke tests were collected; full pytest suite passed as fallback: ... 32 passed in 1.35s ...
```

What this means:

- There are no smoke-marked tests in the current suite.
- The broader Python test suite passed, which is a useful sanity check, but it does not satisfy the smoke-gate requirement.

## Overall Status

**BLOCKED**

Reason:

- The worktree does not have the deploy-time infrastructure, image scan tooling, or runtime configuration required to complete the full Phase 11 gate.
- The only substantive executable verification that passed end-to-end was the fallback Python test suite.

## Remediation Plan

1. Provide the deployable image reference and install `trivy`, then rerun the CVE scan.
2. Re-run dependency audit in a network-enabled environment or with an offline vulnerability source.
3. Install `gitleaks` or `trufflehog` and rerun the secrets scan.
4. Add the missing IaC/configuration for the target environment and rerun drift detection.
5. Start or point the gate at a live service and verify `/health`, `/health/ready`, and `/metrics`.
6. Populate `DATABASE_URL`, `REDIS_URL`, and any other integration credentials, then rerun connectivity checks.
7. Add Alembic config/migrations and verify exactly one head.
8. Add smoke-marked tests if Phase 11 is meant to require them for this story.

## Notes

- `tests/predeploy/*.sh` were added as executable gate scripts.
- Per user instruction, `.project`, `backlog.md`, and `development-tasks.md` were not modified.

