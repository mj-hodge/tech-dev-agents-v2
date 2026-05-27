# STORY-730: Deploy project_file.py — smoke test gap & VM availability

## Root Cause

Every `phase_end` on every agent logs:

```
Warning: .project update failed for STORY-XXX phase N: project_file not available on this VM
```

The phase runner (`sdlc_phase_runner.py`, lines 41-47) uses a dual-import
strategy for `project_file`:

```python
try:
    from deployment.hermes.project_file import update_story_status
except ImportError:
    try:
        from project_file import update_story_status  # flat layout on agent VMs
    except ImportError:
        update_story_status = None
```

When both imports fail, `update_story_status` is `None`. At phase-end
(lines 2124-2125) the code raises `ImportError("project_file not available
on this VM")`, caught by the outer try/except, producing the warning.

**STORY-524** already added `project_file.py` to the `push-code.sh` deploy
list (variable `PROJECT_FILE`, included in the scp copy loop and hash
verification). However:

1. **Smoke test gap**: Step 6 of `push-code.sh` only validates
   `from sdlc_phase_runner import ...`. It does **not** verify
   `from project_file import update_story_status`. A deploy where
   `project_file.py` is missing or corrupted passes the smoke test,
   leaving the phase runner unable to import it at runtime.
2. **Existing VMs**: VMs provisioned before STORY-524 merged never received
   `project_file.py`. The next `push-code.sh` run will deliver it, but only
   if the smoke test is extended to catch future regressions.

## Option A vs Option B

| Option | Description | Recommendation |
|--------|-------------|----------------|
| **A — Deploy & verify** | Ensure `project_file.py` reaches VMs via `push-code.sh` (already done by STORY-524) **and** extend the smoke test to verify it's importable post-deploy. | **Recommended** |
| **B — Silent skip** | Make the `.project` update optional — silent skip if `project_file` unavailable, suppress the warning. | Not recommended — `.project` is an active tracking file used by the ops console and dashboards. Deprecating it is a larger decision outside this story's scope. |

## Decision: Option A

`.project` tracking is a genuinely useful signal for ops visibility. The
phase runner is already structured to use it. The fix is to close the
smoke-test gap so future deploys guarantee the module is importable.

## Scope

Small. Changes:
- `deployment/vm/push-code.sh` — add `project_file` import to smoke test (Step 6).
- `tests/deployment/test_push_code_safety.sh` — add test case verifying smoke test covers `project_file`.
- Story deliverables: `seed.md`, `test-design.md`.

**Frontend:** false

## Test Criteria
- Validate story behavior with focused unit/integration tests for touched components.
- Verify no regressions in existing framework/contract checks.

## Validation
- [ ] Run required test suite(s) for this story scope.
- [ ] Confirm CI gates pass before merge.
