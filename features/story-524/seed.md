# STORY-524 — Seed

**Title:** Deploy `project_file.py` to agent VMs so `.project` updates work on `phase_end`

**Scope:** Small
**Assignee:** Devon (dispatched)
**Branch:** `story-524/story-524`
**Repo:** `tech-dev-agents`

---

## Problem Statement

Every `phase_end` on every agent VM logs:

```
[DISPATCH] Warning: .project update failed for STORY-XXX phase N: project_file not available on this VM
```

The phase runner (`deployment/hermes/sdlc_phase_runner.py`) tries to import `update_story_status` from `deployment.hermes.project_file` at module load time. When the import fails, `update_story_status` is set to `None` and the `.project` write path raises `ImportError("project_file not available on this VM")`, which is caught and logged as a warning.

**Root cause:** `project_file.py` is not in the `push-code.sh` deploy manifest. The file exists at `deployment/hermes/project_file.py` in the repo (410 LOC, provides `update_story_status()`), but `push-code.sh` only copies `sdlc_phase_runner.py`, `dispatch_poller.py`, `run_dispatch_poller.py`, `terminal_guard.py`, and `claude_sdk_tool.py` to `/opt/agent/`. So `from deployment.hermes.project_file import update_story_status` always fails on the VMs — `/opt/agent/` does not contain a `deployment/hermes/` package tree.

**Evidence:**

- `deployment/hermes/project_file.py` — present in repo, 410 LOC
- `deployment/vm/push-code.sh:82-90` — FILES list (`PHASE_RUNNER`, `DISPATCH_POLLER`, `POLLER_WRAPPER`, `TERMINAL_GUARD`, `SDK_TOOL`) — no `PROJECT_FILE`
- `deployment/hermes/sdlc_phase_runner.py:41-44` — `try: from deployment.hermes.project_file import update_story_status; except ImportError: update_story_status = None`
- `deployment/hermes/sdlc_phase_runner.py:1054-1073` — `if update_story_status is None: raise ImportError(...)` → caught → warning logged
- `deployment/vm/run_dispatch_poller.py:25` — `sys.path.insert(0, "/opt/agent")` — path is flat, so the package-style `deployment.hermes.*` import can never resolve on the VM even if we copy the file

---

## Decision: Option A (Deploy `project_file.py`) vs Option B (Silent skip)

| Option | Pros | Cons |
|--------|------|------|
| **A. Deploy `project_file.py`** | `.project` actually gets updated — the tracking file we rely on stays current. Restores the designed behaviour. Small, well-scoped change. | Requires touching `push-code.sh` manifest + a small import fallback in `sdlc_phase_runner.py` (because `/opt/agent/` is flat, not package-style). |
| **B. Silent skip (downgrade warning)** | One-line change, zero deploy risk. | Deprecates `.project` updates from VMs by stealth. Any future consumer of `.project` silently gets stale data. We lose the audit trail that phase runner was designed to produce. |

**Choice: Option A.** Dispatch recommendation was A, and it matches the phase runner's original design intent. `.project` is the single source of truth for phase progress on the main repo — silently abandoning it is a bigger decision than deploying one more file.

---

## Implementation Sketch (for Phase 7 / Phase 8)

Two coordinated edits:

1. **`deployment/vm/push-code.sh`** — add `project_file.py` to the deploy manifest:
   - Declare `PROJECT_FILE="$REPO_ROOT/deployment/hermes/project_file.py"` alongside the other file variables (lines ~82-90).
   - Include `"$PROJECT_FILE"` in the scp loop at line 103.
   - Update the banner `echo "Files: ..."` at line 320 to list `project_file.py`.

2. **`deployment/hermes/sdlc_phase_runner.py`** — fallback import for the flat `/opt/agent/` layout:
   ```python
   try:
       from deployment.hermes.project_file import update_story_status
   except ImportError:
       try:
           from project_file import update_story_status  # flat layout on agent VMs
       except ImportError:
           update_story_status = None
   ```
   Without this, copying the file alone is not enough — `sys.path` on the VM is `/opt/agent`, so the package-style import never resolves.

3. **`tests/deployment/test_push_code_safety.sh`** — extend the compliance check to assert `project_file.py` is in the deploy manifest (prevents future regression).

---

## Success Criteria

- `project_file.py` is copied to `/opt/agent/project_file.py` on every VM run of `push-code.sh`.
- `sdlc_phase_runner.py` successfully imports `update_story_status` on agent VMs (either via the package path in dev, or the flat path on the VM).
- After a fresh deploy, running one Small story end-to-end produces **zero** `project_file not available` warnings in `journalctl -u dispatch-poller`.
- The story's `.project` (repo root) shows a new phase-completion entry after the run.
- `tests/deployment/test_push_code_safety.sh` explicitly asserts `project_file.py` membership in the deploy manifest.

---

## Test Criteria (MANDATORY — carried into Phase 7)

Per dispatch:

1. **Unit test** — phase runner `.project` update path with `project_file` mocked: verify a new row is appended for the phase when `update_story_status` is available.
2. **Integration test** — run a fake phase on a test VM; verify `/opt/agent/project_file.py` exists AND `.project` gets a new entry.
3. **Compliance test** — `tests/deployment/test_push_code_safety.sh` must assert `project_file.py` is in the deploy manifest.

## E2E Validation (MANDATORY)

After deploy: run one Small story end-to-end. Zero `project_file not available` warnings in the `dispatch-poller` journal. Repo-root `.project` shows the phase completion entry.

---

## Risks / Considerations

- **Import-path mismatch:** Local dev uses the package-style import `deployment.hermes.project_file`; the VM has a flat `/opt/agent/` layout. The fallback import above handles both; tests must cover both.
- **Ownership:** push-code.sh already chowns copied files to `hermes:hermes` — no separate handling needed for `project_file.py`.
- **Backward compat:** `project_file.py` has no side effects at import time (it defines `update_story_status` and helpers), so copying it to a VM that is not yet running the updated phase runner is a no-op.
- **Smoke test coverage:** The existing smoke test imports `sdlc_phase_runner`, which transitively imports `project_file` once the fallback lands — so an import-time breakage in `project_file.py` would be caught automatically.

---

## Validation

After deploying via `push-code.sh all`:

1. `journalctl -u dispatch-poller -n 100` on each agent — zero occurrences of `project_file not available`.
2. Run one Small story end-to-end; `.project` (repo root) shows the phase-completion entry.
3. `ls /opt/agent/project_file.py` on each agent — file present.
4. All 11 pytest tests in `tests/deployment/test_phase_runner_project_file.py` GREEN.
5. All 14 bash tests in `tests/deployment/test_push_code_safety.sh` GREEN (TC-12/13/14 now GREEN).

---

## Out of Scope / Follow-ups

- Restructuring `/opt/agent/` into a package layout (would remove the need for the fallback but is a broader change — tracked for a future story).
- Deprecating the repo-root `.project` in favour of per-story `features/<story>/.project` (separate design decision, not this story).

---

## Next Phase

**Phase 7 — Test Design** (small scope path: 1 → 7 → 8 → Done).
