# Feature Spec — STORY-1019: Build Artifact Bundle Imports

## Summary

Extend the ops-console deploy pipeline to automatically bundle all
`tech_dev_agents/*` sub-packages referenced by `dispatch_v2.py`, and add
import gates to both `deploy.sh` and `rollback.sh` that fail loudly before
a broken import causes a production outage.

## Design

### 1. AST Import Walker (build-artifact.sh)

After locating source files and before computing the SHA256, the script runs
an inline Python snippet via `python3 -` heredoc. The snippet:

1. Calls `ast.parse()` on `dispatch_v2.py`.
2. Walks all nodes with `ast.walk()`.
3. Collects any `ImportFrom` or `Import` node whose module starts with
   `tech_dev_agents.`.
4. Extracts the second path component (the sub-package name, e.g. `morris`).
5. Prints each discovered sub-package name to stdout, one per line.

`mapfile` captures the output into `EXTRA_PKGS`. If Python exits non-zero
(syntax error, file not found), the script stops immediately.

Each discovered sub-package is looked up in `${SOURCE_DIR}/tech_dev_agents/`;
if the directory exists it is staged into the artifact under
`tech_dev_agents/<pkg>/`. The root `tech_dev_agents/__init__.py` is also
copied if present (namespace package support).

The `ALL_FILES` array is rebuilt to include all `.py` files from the extra
packages so they participate in the checksum.

### 2. Pre-Deploy Import Gate (deploy.sh Step 4c)

Inserted between Step 4b (migrations) and Step 5 (set commit SHA):

```
docker exec "${CONTAINER}" python3 -c \
    "from tech_dev_agents.ops_console.main import create_app"
```

If this fails (non-zero exit), deploy aborts before the container is
restarted, preserving the running healthy state.

### 3. Post-Rollback Import Gate (rollback.sh Step 6)

Appended after the Step 5 health check. Uses the same `docker exec` pattern.
On failure logs `CRITICAL: post-rollback import failed` and exits 1.

### 4. pre_dispatch.py

A new module `tech_dev_agents/morris/pre_dispatch.py` that exposes:

- `validate_dispatch_seed(seed: dict) -> list[str]` — returns a list of
  error strings; empty list = valid.
- `DISPATCH_V2_SEED_VALIDATION_ENABLED` — boolean flag from env var
  `DISPATCH_V2_SEED_VALIDATION_ENABLED` (default `"1"`, disable with `"0"`).

Required fields validated: `story_id`, `repo`, `task_name`.

### 5. dispatch_v2.py — Clean Import

Replaces the `try/except ImportError` band-aid with a clean top-level import:

```python
from tech_dev_agents.morris.pre_dispatch import (
    DISPATCH_V2_SEED_VALIDATION_ENABLED,
    validate_dispatch_seed,
)
```

The build system now guarantees this module is always in the artifact.

## Non-Goals

- Dynamic (runtime) import graph walking — static AST is sufficient and safer.
- Recursive transitive dependency walking — first-level sub-packages only.
- Changes to the v2 API surface or database schema.

## Risk

Low. The AST walker is purely additive: if `EXTRA_PKGS` is empty the artifact
is identical to before. The import gates only add pre-flight checks before
potentially dangerous restarts.
