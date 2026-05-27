# Test Design — STORY-645: Migration Auto-Apply & CI Invariant

## Scope & Coverage

| Field | Value |
|-------|-------|
| Scope | Small |
| Coverage Target | 50% |
| Frontend | None |
| Test Framework | pytest |

## Test Categories

### Group A: Deploy Script Migration Integration (4 tests)

**File:** `tests/deployment/test_deploy_migrations_645.py`
**Level:** Unit (script source analysis + subprocess mocking)
**Purpose:** Verify deploy.sh wires migrations correctly end-to-end.

| Test | What It Verifies |
|------|------------------|
| `test_migrations_run_before_health_check` | Migration step appears before the curl health-check in deploy.sh |
| `test_migration_failure_aborts_deploy` | ON_ERROR_STOP=1 or `|| exit 1` ensures a bad migration halts the deploy |
| `test_migrations_sorted_numerically` | sort -z or equivalent ensures 001 runs before 010 |
| `test_no_migrations_dir_skips_gracefully` | The else-branch logs "skipping" and does not exit non-zero |

### Group B: CI Check Script — Column Detection (6 tests)

**File:** `tests/scripts/test_check_migrations_match_columns.py`
**Level:** Unit (pure function tests against the check script)
**Purpose:** Verify the heuristic column-reference detector in the CI check script.

| Test | What It Verifies |
|------|------------------|
| `test_missing_migration_exits_nonzero` | PR diff adds `Field("foo")` with no migration → exit 1, lists `foo` |
| `test_migration_present_exits_zero` | PR diff adds field ref AND migration adding `foo` → exit 0 |
| `test_existing_column_exits_zero` | PR references `story_id` (in 001 migration) → exit 0 |
| `test_opt_out_comment_skips_column` | `# migration-ci: ignore` next to field → exit 0 |
| `test_non_ops_console_file_ignored` | Column-like ref in `tests/` or `scripts/` file → not flagged |
| `test_output_variance_two_diffs` | Two different PR diffs produce different output (one pass, one fail) |

### Group C: CI Check Script — Schema Fixture (3 tests)

**File:** `tests/scripts/test_check_migrations_match_columns.py` (same file)
**Level:** Unit
**Purpose:** Verify the schema fixture loading and column extraction from migration files.

| Test | What It Verifies |
|------|------------------|
| `test_extract_columns_from_migrations` | Parses CREATE TABLE + ALTER TABLE ADD COLUMN from migrations → correct column set |
| `test_extract_columns_handles_empty_dir` | Empty migrations dir → empty set, no crash |
| `test_extract_columns_ignores_non_sql` | .txt or .bak files in migrations dir are skipped |

### Group D: CI Workflow Structural (2 tests)

**File:** `tests/scripts/test_check_migrations_match_columns.py` (same file)
**Level:** Unit (file existence + content checks)
**Purpose:** Verify the CI workflow YAML is wired correctly.

| Test | What It Verifies |
|------|------------------|
| `test_workflow_file_exists` | `.github/workflows/migration-invariant.yml` exists |
| `test_workflow_triggers_on_correct_paths` | Workflow triggers on `tech_dev_agents/**` and `scripts/migrations/**` |

## Test Matrix Summary

| Group | Tests | Level | File |
|-------|-------|-------|------|
| A — Deploy Migrations | 4 | Unit | `tests/deployment/test_deploy_migrations_645.py` |
| B — CI Column Detection | 6 | Unit | `tests/scripts/test_check_migrations_match_columns.py` |
| C — CI Schema Fixture | 3 | Unit | `tests/scripts/test_check_migrations_match_columns.py` |
| D — CI Workflow Structural | 2 | Unit | `tests/scripts/test_check_migrations_match_columns.py` |
| **Total** | **15** | | |

## RED State Expectations

- **Group A:** 4 FAIL — tests assert deploy.sh properties that already exist (should PASS on current deploy.sh) or assert properties of the new CI script that doesn't exist yet
- **Group B:** 6 FAIL — `scripts/ci/check_migrations_match_columns.py` doesn't exist; import fails → xfail or stub
- **Group C:** 3 FAIL — same module doesn't exist
- **Group D:** 2 FAIL — workflow file and check script don't exist yet

## Output-Variance Tests

- `test_output_variance_two_diffs` — sends two meaningfully different PR diffs (one with migration, one without) and asserts different exit codes + different output messages.

## LLM Error-Prone Coverage

| Category | Test(s) |
|----------|---------|
| Edge cases (empty input) | `test_extract_columns_handles_empty_dir`, `test_no_migrations_dir_skips_gracefully` |
| Boundary conditions | `test_migrations_sorted_numerically` (ordering matters) |
| Output format | `test_missing_migration_exits_nonzero` (verifies column name in output) |

## Gates

- ✅ No external API write paths → Gate 2a N/A
- ✅ No ORM model changes → Gate 8 (Migration Verification) N/A for this story
- ✅ No frontend → UI gates N/A
- ✅ No multi-tenant endpoints → Gate 6 N/A
- ✅ No file uploads → Gate 7 N/A
- ✅ Output-variance test included (Group B)
- ✅ Integration-path test included (Group B tests with real-shaped diff data)

## API Mock Verification

N/A — no Playwright tests, no route mocks.
