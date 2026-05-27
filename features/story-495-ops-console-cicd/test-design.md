# Test Design: STORY-495 CI/CD Pipeline for Ops Console

## Summary
| Metric | Value |
|--------|-------|
| Total tests | 36 |
| Test files | 1 (`tests/ops_console/test_story495_cicd.py`) |
| Coverage target | 60% (Medium scope) |
| RED state confirmed | Yes — 36/36 FAILED |

## Test Structure

```
tests/ops_console/
└── test_story495_cicd.py
    ├── TestHealthCommitSha (5 tests) — Python integration
    ├── TestDeployScript (10 tests) — Shell script validation
    ├── TestRollbackScript (8 tests) — Shell script validation
    ├── TestWorkflowYaml (10 tests) — YAML structure validation
    └── TestDeployBoundaries (3 tests) — Edge cases
```

## Test Categories

### Category 1: Health Endpoint commit_sha (5 tests)
| Test | What It Verifies |
|------|------------------|
| `test_health_includes_commit_sha_when_env_set` | commit_sha field matches DEPLOY_COMMIT_SHA env var |
| `test_health_commit_sha_null_when_not_set` | commit_sha is null (not missing) when env var unset |
| `test_health_commit_sha_varies_with_different_values` | Output-variance: different SHAs produce different responses |
| `test_health_no_auth_required_still_returns_commit_sha` | Public endpoint includes commit_sha without auth |
| `test_health_response_schema_includes_commit_sha_field` | New field coexists with all existing fields |

### Category 2: Deploy Script (10 tests)
| Test | What It Verifies |
|------|------------------|
| `test_deploy_script_exists` | File exists at expected path |
| `test_deploy_script_is_executable_or_has_shebang` | Has bash shebang |
| `test_deploy_script_never_runs_npm_install` | No npm install (OOM risk) |
| `test_deploy_script_never_runs_npm_build` | No npm build (OOM risk) |
| `test_deploy_script_creates_backup` | Creates backup before deploying |
| `test_deploy_script_uses_docker_cp` | Uses docker cp for backend |
| `test_deploy_script_restarts_container` | Restarts container after deploy |
| `test_deploy_script_checks_health` | Verifies health after restart |
| `test_deploy_script_has_nonzero_exit_on_failure` | Exits 1 on failure |
| `test_deploy_script_sets_errexit` | Uses set -e for safety |

### Category 3: Rollback Script (8 tests)
| Test | What It Verifies |
|------|------------------|
| `test_rollback_script_exists` | File exists at expected path |
| `test_rollback_script_has_shebang` | Has bash shebang |
| `test_rollback_script_restores_frontend` | Restores from backup |
| `test_rollback_script_restores_backend` | Uses docker cp to restore |
| `test_rollback_script_restarts_container` | Restarts after restore |
| `test_rollback_script_verifies_health` | Checks health after rollback |
| `test_rollback_script_checks_backup_exists` | Verifies backup exists first |
| `test_rollback_script_sets_errexit` | Uses set -e for safety |

### Category 4: Workflow YAML (10 tests)
| Test | What It Verifies |
|------|------------------|
| `test_workflow_file_exists` | YAML exists at expected path |
| `test_workflow_is_valid_yaml` | Parses as valid YAML |
| `test_workflow_triggers_on_push_to_main` | Triggers on main branch |
| `test_workflow_filters_on_correct_paths` | Path filters match spec |
| `test_workflow_has_feature_flag_check` | OPS_CONSOLE_CICD_ENABLED gate |
| `test_workflow_builds_frontend_on_github_runner` | npm build on ubuntu-latest |
| `test_workflow_uses_scp_or_ssh_for_deploy` | SCP/SSH deployment |
| `test_workflow_has_health_check_verification` | Post-deploy health check |
| `test_workflow_has_rollback_step` | Rollback on failure |
| `test_workflow_never_runs_npm_on_vm` | No npm via SSH |

### Category 5: Boundaries and Edge Cases (3 tests)
| Test | What It Verifies |
|------|------------------|
| `test_health_commit_sha_empty_string` | Empty string commit_sha handled |
| `test_deploy_script_does_not_touch_database` | No psql/alembic commands |
| `test_deploy_script_does_not_touch_nginx_config` | No nginx config changes |

## AC Coverage Map

| Acceptance Criterion | Tests |
|---------------------|-------|
| Workflow triggers on push to main with path filter | `test_workflow_triggers_on_push_to_main`, `test_workflow_filters_on_correct_paths` |
| Frontend builds in CI (7GB runner) | `test_workflow_builds_frontend_on_github_runner` |
| Built frontend deployed via SCP | `test_workflow_uses_scp_or_ssh_for_deploy`, `test_deploy_script_exists` |
| Backend deployed via docker cp | `test_deploy_script_uses_docker_cp` |
| Container restarts with health check | `test_deploy_script_restarts_container`, `test_deploy_script_checks_health` |
| Health check failure triggers rollback | `test_workflow_has_rollback_step`, `test_rollback_script_*` |
| No npm on VM | `test_deploy_script_never_runs_npm_install`, `test_deploy_script_never_runs_npm_build`, `test_workflow_never_runs_npm_on_vm` |
| Deployment status posted to Teams | `test_workflow_has_health_check_verification` (Teams notification tested via workflow structure) |

## Gates Addressed

- **Output-variance:** `test_health_commit_sha_varies_with_different_values`
- **Boundary conditions:** Empty string, null, missing field tests
- **Error observability:** deploy.sh/rollback.sh exit codes, health check verification
- **No external API writes:** Story does not write to external APIs (SSH/SCP to own VM only)
- **No database changes:** Confirmed by `test_deploy_script_does_not_touch_database`
- **No ORM models:** No migration gate needed
