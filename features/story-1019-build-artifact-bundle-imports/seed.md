# STORY-1019: Build Artifact Bundle Imports
**Frontend:** false

## Background

On 2026-05-18 the ops-console crashed because PR #335 added an import
`tech_dev_agents.morris.pre_dispatch` in `dispatch_v2.py` that the deploy
artifact didn't carry. `build-artifact.sh` only ships `dispatch_v2.py` +
migrations + manifest — not other `tech_dev_agents` sub-packages.

The hotfix (commit 6a682ae1) wrapped the import in `try/except` as a
band-aid. This story removes that band-aid and fixes the root cause.

## Root Cause

The build artifact was a partial bundle: it included `dispatch_v2.py` but did
not walk its import graph. Any `tech_dev_agents.*` package imported by that
module was silently absent from the artifact. When the container restarted with
the new code it failed on first import, causing a 15-minute outage.

## Success Criteria

| ID   | Description | Verification |
|------|-------------|--------------|
| SC-1 | `build-artifact.sh` uses Python AST to walk `dispatch_v2.py`'s imports and includes every `tech_dev_agents/*` sub-package in the artifact | Static: script contains `ast.parse`/`ast.walk` |
| SC-2 | `deploy.sh` runs a pre-deploy import gate before container restart; aborts with FAILED message if the gate fails | Static: script contains `from tech_dev_agents.ops_console.main import create_app` + FAILED abort message |
| SC-3 | `rollback.sh` runs a post-rollback import gate; logs `CRITICAL: post-rollback import failed` on failure | Static: script contains that exact string |
| SC-4 | `dispatch_v2.py` imports `validate_dispatch_seed` from `tech_dev_agents.morris.pre_dispatch` without a `try/except` wrapper | Static: import present, no try/except guard |
| SC-5 | Building an artifact whose `dispatch_v2.py` imports `tech_dev_agents.morris.pre_dispatch` results in a tarball that contains `tech_dev_agents/morris/pre_dispatch.py` | Integration: run build-artifact.sh, extract, verify file present |

## Deliverables

1. `deployment/ops-console/build-artifact.sh` — extended with AST import graph walker
2. `deployment/ops-console/deploy.sh` — Step 4c pre-deploy import gate
3. `deployment/ops-console/rollback.sh` — Step 6 post-rollback import gate
4. `tech_dev_agents/morris/pre_dispatch.py` — `validate_dispatch_seed` function
5. `tech_dev_agents/ops_console/routes/dispatch_v2.py` — clean import (no try/except)
6. `tests/deployment/test_story1019_import_closure.py` — SC-1 through SC-5

## Scope

Medium — no schema changes, no new endpoints, no frontend changes. Pure
build-system + deployment hardening.

## Implementation Notes

- The AST scanner runs at build time (no import side-effects), using
  `ast.parse` + `ast.walk` to collect `tech_dev_agents.*` imports statically.
- The pre-deploy gate in `deploy.sh` runs BEFORE the container restart (Step 6),
  preventing downtime from import failures.
- The post-rollback gate in `rollback.sh` catches corrupt backups early.
- `validate_dispatch_seed` validates required fields on dispatch seed dicts
  and is controlled by `DISPATCH_V2_SEED_VALIDATION_ENABLED` env var.

## Test Criteria

| Test | Validates |
|------|-----------|
| RED tests from Phase 7 (see `## Verification plan`) | Every SC has a literal test or shell command |
| Full pytest suite GREEN, zero regressions | No unrelated breakage |
| Incident-replay tests (where applicable) | The 2026-05-04..05-18 failure classes are catchable by the new gate |

See `## Success criteria` and `## Verification plan` above for SC-keyed predicates.

## Validation

After merge:

1. **Tests:** all RED tests turned GREEN; `pytest <story test paths>` exits 0.
2. **No regressions:** full suite passes; CI green on the PR.
3. **Per-SC verification:** every command in `## Verification plan` runs and produces the expected output.
4. **Tracking updated:** `.project` and `backlog.md` reflect Phase 8 completion; Monday.com task updated.

