# Test Design — STORY-1019: Build Artifact Bundle Imports

## Test File

`tests/deployment/test_story1019_import_closure.py`

## Test Strategy

All tests are static or integration (subprocess). No mocking of Docker or
production services.

## Test Cases

### SC-1: AST walker present in build-artifact.sh

**Type:** Static (content assertion)
**What:** Read `build-artifact.sh`, assert it contains `ast.parse` or
`ast.walk` and `tech_dev_agents`.
**Pass condition:** Both strings found.
**Fail indicates:** AST walker was removed or never implemented.

### SC-2: Pre-deploy import gate in deploy.sh

**Type:** Static (content assertion)
**What:** Read `deploy.sh`, assert it contains:
- `from tech_dev_agents.ops_console.main import create_app`
- The FAILED/abort log message for the gate
**Pass condition:** Both strings found.
**Fail indicates:** Step 4c was not added.

### SC-3: Post-rollback import gate in rollback.sh

**Type:** Static (content assertion)
**What:** Read `rollback.sh`, assert it contains
`CRITICAL: post-rollback import failed`.
**Pass condition:** String found.
**Fail indicates:** Step 6 was not added.

### SC-4: Clean import in dispatch_v2.py

**Type:** Static (content + AST assertion)
**What:** Read `dispatch_v2.py`, assert:
- `from tech_dev_agents.morris.pre_dispatch import` is present
- The import is NOT wrapped in a `try/except` block (AST check)
**Pass condition:** Import present, no enclosing try/except.
**Fail indicates:** Band-aid was not removed, or import was not added.

### SC-5: Integration — artifact bundles morris package

**Type:** Integration (subprocess)
**What:**
1. Create a temp source tree with:
   - `routes/dispatch_v2.py` that imports `tech_dev_agents.morris.pre_dispatch`
   - `tech_dev_agents/morris/__init__.py` and `tech_dev_agents/morris/pre_dispatch.py`
   - Required migrations and manifest
2. Run `build-artifact.sh` against the temp tree.
3. Extract the artifact tarball.
4. Assert `tech_dev_agents/morris/pre_dispatch.py` exists in the extracted
   tree.
**Pass condition:** File present in artifact.
**Fail indicates:** AST walker did not discover the import, or staging step
was skipped.

## RED State

Tests SC-1 through SC-5 all fail before implementation:
- SC-1: `ast.parse` not in `build-artifact.sh`
- SC-2: import gate not in `deploy.sh`
- SC-3: CRITICAL message not in `rollback.sh`
- SC-4: clean import not in `dispatch_v2.py`
- SC-5: `build-artifact.sh` does not bundle `tech_dev_agents/morris/`

## GREEN State

All 5 tests pass after:
1. `build-artifact.sh` extended with AST walker + staging
2. `deploy.sh` Step 4c added
3. `rollback.sh` Step 6 added
4. `pre_dispatch.py` created
5. `dispatch_v2.py` clean import added
