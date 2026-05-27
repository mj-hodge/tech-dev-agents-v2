# Feature Spec: STORY-565 — Frontend Classification + Playwright @smoke Gate

**Phase:** 6 (Design)
**Scope:** Medium
**Date:** 2026-04-24

---

## 1. Overview

This spec details the concrete implementation of `_verify_frontend_gate()` in
`deployment/hermes/sdlc_phase_runner.py`. The gate enforces that any story
whose seed declares `Frontend: true` must include at least one `e2e/**/*.spec.ts`
file containing `@smoke` in the branch diff before the phase runner allows
completion.

A companion compliance test in `tests/test_sdlc_framework_compliance.py`
ensures every seed committed after `2026-04-26T00:00Z` declares the `Frontend:`
field.

---

## 2. Function: `_verify_frontend_gate`

### 2.1 Signature

```python
def _verify_frontend_gate(workdir: str, story_id: str) -> tuple[bool, list[str]]:
    """Check that frontend stories include a @smoke-tagged Playwright spec.

    Returns:
        (True, [])                — gate passed (or not applicable)
        (False, [error, ...])     — gate failed with actionable messages
    """
```

### 2.2 Algorithm

```
1. seed_text = _read_seed_for_story(workdir, story_id)
   - If None → return (True, [])  # no seed, don't block

2. Parse `Frontend:` field from seed_text using regex:
     FRONTEND_RE = re.compile(
         r'(?:^\|\s*Frontend\s*\|\s*|\*\*Frontend[:\s]*\*\*\s*)(true|false)\b',
         re.IGNORECASE | re.MULTILINE
     )
   This matches both:
     - Table row: `| Frontend | true |`
     - Bold field: `**Frontend:** true` or `**Frontend: true**`

3. If no match (field missing):
   - Print warning: "[DISPATCH] {story_id} seed missing Frontend: field — fail-open (pre-cutoff grandfathering)"
   - return (True, [])   # Case F — fail-open for grandfathered seeds

4. value = match.group(1).lower()

5. If value == "false":
   - return (True, [])   # Cases D and E — short-circuit, no diff scan
   - Note: trailing `# rationale` comments are irrelevant; regex stops at true|false

6. If value == "true":
   a. Run: git -C {workdir} diff origin/main --name-only
      - On subprocess error/timeout(15s): fail-open with warning, return (True, [])
   b. Filter changed files for paths matching e2e/**/*.spec.ts pattern:
      - path.startswith("e2e/") and path.endswith((".spec.ts", ".spec.tsx", ".spec.js"))
      - Reuse existing _is_playwright_spec_path() helper
   c. If no spec files found:
      - return (False, [
          f"{story_id}: Frontend: true but no Playwright spec in diff. "
          f"Add e2e/**/*.spec.ts with a @smoke tag to pass the gate."
        ])
   d. For each matching spec, read content via git show HEAD:{path}:
      - On read failure: skip file (don't block on git show errors)
   e. If any spec file contains "@smoke" → return (True, [])   # Case A
   f. If specs exist but none contain @smoke:
      - return (False, [
          f"{story_id}: Frontend: true — found Playwright spec(s) but none "
          f"contain @smoke tag. Add @smoke to at least one test in "
          f"e2e/**/*.spec.ts to pass the gate."
        ])
```

### 2.3 Call-site in `run_sdlc_phases`

Insert **after** the Acceptance Diff gate passes (line 2107) and **before**
the PR creation block (line 2109):

```python
    if missing == [] and _verify_acceptance_diff.__doc__:
        print(f"[DISPATCH] {story_id} Acceptance Diff ✓", flush=True)

    # >>> NEW: STORY-565 Frontend @smoke gate
    ok, errors = _verify_frontend_gate(workdir, story_id)
    if not ok:
        error_list = "\n  - ".join(errors)
        print(
            f"[DISPATCH] {story_id} Frontend @smoke gate FAILED:\n  - {error_list}\n"
            f"[DISPATCH] Refusing to report Complete. Add a @smoke-tagged "
            f"e2e/**/*.spec.ts and retry.",
            flush=True,
        )
        _notify_teams(
            f"Frontend @smoke gate FAILED for {story_id}:\n  - {error_list}"
        )
        _emit_event(
            "frontend_smoke_gate_fail",
            story_id=story_id,
            errors=errors,
        )
        # Write sidecar for Morris heartbeat
        _write_frontend_gate_sidecar(story_id, errors)
        return False, None, None
    print(f"[DISPATCH] {story_id} Frontend @smoke gate ✓", flush=True)

    # Create PR if one doesn't exist ...
```

---

## 3. Sidecar: `_write_frontend_gate_sidecar`

### 3.1 Signature

```python
def _write_frontend_gate_sidecar(story_id: str, errors: list[str]) -> None:
```

### 3.2 Behavior

Write a JSON file to:
```
/home/hermes/state/{agent_name}/phase-runner-gate-failed/{story_id}.json
```

Contents:
```json
{
  "story_id": "STORY-565",
  "gate": "frontend_smoke",
  "errors": ["...actionable message..."],
  "timestamp": "2026-04-24T12:00:00Z",
  "agent": "agent-3"
}
```

Pattern follows existing `_write_pending_message` (line 355): `os.makedirs(os.path.dirname(path), exist_ok=True)`, wrapped in try/except to never crash.

---

## 4. Seed Parsing Regex

The `Frontend:` field appears in seeds in two forms:

### Form 1: Overview table row
```markdown
| Frontend | true |
| Frontend | false |
```

### Form 2: Bold standalone field
```markdown
**Frontend: true**
**Frontend:** true
**Frontend:** false # CSS-only refactor
```

### Regex
```python
_FRONTEND_FIELD_RE = re.compile(
    r'(?:'
    r'^\|\s*Frontend\s*\|\s*'           # table row: | Frontend | value
    r'|'
    r'\*\*Frontend[:\s]*\*\*\s*'        # bold: **Frontend:** value or **Frontend: value**
    r'|'
    r'\*\*Frontend:\s*'                 # bold inline: **Frontend: value**
    r')'
    r'(true|false)\b',
    re.IGNORECASE | re.MULTILINE,
)
```

The escape hatch `Frontend: false # rationale` is handled implicitly: the
regex captures only `false` and ignores the trailing comment. The gate
short-circuits on `false` regardless.

---

## 5. Git-Diff Scan Logic

### 5.1 Get changed files
```python
result = subprocess.run(
    ["git", "-C", workdir, "diff", "origin/main", "--name-only"],
    capture_output=True, text=True, timeout=15,
)
```
Reuses the same pattern as `_verify_acceptance_diff` (line 1388).

### 5.2 Filter for Playwright specs
```python
spec_paths = [p for p in changed_files if _is_playwright_spec_path(p)]
```
Reuses existing `_is_playwright_spec_path` (line 1199).

### 5.3 Check `@smoke` in spec content
```python
for spec in spec_paths:
    try:
        show = subprocess.run(
            ["git", "-C", workdir, "show", f"HEAD:{spec}"],
            capture_output=True, text=True, timeout=10,
        )
        if show.returncode == 0 and "@smoke" in show.stdout:
            return True, []
    except Exception:
        continue  # skip unreadable files
```

---

## 6. Compliance Test

### 6.1 Function name
```
test_every_seed_dispatched_after_20260426_declares_frontend_classification
```

### 6.2 Location
`tests/test_sdlc_framework_compliance.py` — alongside the existing
`test_every_seed_written_after_20260422_has_required_sections`.

### 6.3 Logic
```python
def test_every_seed_dispatched_after_20260426_declares_frontend_classification():
    cutoff = datetime.datetime(2026, 4, 26, 0, 0, 0).timestamp()
    missing = []
    for seed in features_dir.glob("story-*/seed.md"):
        commit_ts = _git_first_commit_timestamp(seed)
        ts = commit_ts if commit_ts is not None else seed.stat().st_mtime
        if ts < cutoff:
            continue  # grandfathered
        text = seed.read_text(errors="replace")
        if not _FRONTEND_FIELD_RE.search(text):
            missing.append(str(seed.relative_to(REPO_ROOT)))
    assert not missing, (
        "Seeds committed after 2026-04-26 must declare a Frontend: field "
        "(true or false) in the Overview table:\n  - " + "\n  - ".join(missing)
    )
```

### 6.4 Regex (reusable module-level)
Same `_FRONTEND_FIELD_RE` as §4, defined at module scope in the test file.

---

## 7. Test Cases (A–F) — Fixture Design

All tests live in `tests/deployment/test_phase_runner_frontend_smoke_gate.py`.
Each test creates a temporary worktree layout with a fake seed, mocks
`subprocess.run` to control git-diff and git-show output, and calls
`_verify_frontend_gate` directly.

| Case | Seed `Frontend:` | Diff files | Spec has `@smoke`? | Expected |
|------|-------------------|------------|---------------------|----------|
| A | `true` | `e2e/dashboard/login.smoke.spec.ts` | Yes | PASS `(True, [])` |
| B | `true` | `frontend/src/Foo.tsx` only | N/A | FAIL `(False, [...])` |
| C | `true` | `e2e/dashboard/login.spec.ts` | No | FAIL `(False, [...])` |
| D | `false` | None | N/A | PASS `(True, [])` |
| E | `false # CSS-only refactor, covered by existing @smoke suite` | None | N/A | PASS `(True, [])` |
| F | *(missing)* | Any | N/A | PASS `(True, [])` + warning |

### Fixture seeds

**Case A seed:**
```markdown
| Field | Value |
|-------|-------|
| Frontend | true |
```

**Case B seed:** Same as A.

**Case C seed:** Same as A.

**Case D seed:**
```markdown
| Field | Value |
|-------|-------|
| Frontend | false |
```

**Case E seed:**
```markdown
**Frontend: false** # CSS-only refactor, covered by existing @smoke suite

**Frontend:** false
```

**Case F seed:** A valid seed with no `Frontend:` field at all.

---

## 8. Files Changed

### Must-change:
| File | Change |
|------|--------|
| `deployment/hermes/sdlc_phase_runner.py` | Add `_verify_frontend_gate`, `_write_frontend_gate_sidecar`, call-site after acceptance-diff gate |
| `tests/test_sdlc_framework_compliance.py` | Add `test_every_seed_dispatched_after_20260426_declares_frontend_classification` |
| `tests/deployment/test_phase_runner_frontend_smoke_gate.py` | New file — 6 test cases (A–F) |

### Must-NOT-change:
- `tech_dev_agents/ops_console/services/dispatch_db_service.py`
- `tech_dev_agents/ops_console/routes/dispatch.py`
- Any migration under `migrations/` or `alembic/`

### SDLC deliverables:
- `features/story-565-frontend-classification-gate/feature-spec.md` (this file)
- `features/story-565-frontend-classification-gate/test-design.md` (Phase 7)
