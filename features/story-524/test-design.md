# STORY-524 — Test Design

**Title:** Deploy `project_file.py` to agent VMs  
**Scope:** Small  
**Coverage target:** 50% (critical paths)  
**Test runner:** `pytest` (Python) + `bash` (compliance)

---

## Test Inventory

| ID | Group | File | State | What It Guards |
|----|-------|------|-------|----------------|
| TC-A1 | Import fallback | `test_phase_runner_project_file.py` | **RED** | Flat import added to sdlc_phase_runner.py |
| TC-A2 | Import fallback | `test_phase_runner_project_file.py` | **RED** | Full 3-step fallback chain present |
| TC-B1 | .project update | `test_phase_runner_project_file.py` | GREEN | update_story_status is called when available |
| TC-B2 | .project update | `test_phase_runner_project_file.py` | GREEN | Correct args forwarded (story_id, scope, phase, project_path, assignee) |
| TC-B3 | .project update | `test_phase_runner_project_file.py` | GREEN | Warning logged when update_story_status is None |
| TC-B4 | .project update | `test_phase_runner_project_file.py` | GREEN | Exception in update never aborts run (error resilience) |
| TC-C1 | Functional | `test_phase_runner_project_file.py` | GREEN | Real update_story_status appends Story Status row with correct phase/branch |
| TC-C2 | Functional | `test_phase_runner_project_file.py` | GREEN | Output variance — phase=1 and phase=7 produce different file content |
| TC-D1 | Manifest | `test_phase_runner_project_file.py` | **RED** | PROJECT_FILE variable declared in push-code.sh |
| TC-D2 | Manifest | `test_phase_runner_project_file.py` | **RED** | $PROJECT_FILE in scp copy loop |
| TC-D3 | Manifest | `test_phase_runner_project_file.py` | **RED** | project_file.py in deploy banner |
| TC-12 | Bash manifest | `test_push_code_safety.sh` | **RED** | `grep PROJECT_FILE push-code.sh` |
| TC-13 | Bash manifest | `test_push_code_safety.sh` | **RED** | `grep "$PROJECT_FILE" push-code.sh` |
| TC-14 | Bash integration | `test_push_code_safety.sh` | **RED** | Tracking scp records project_file.py in transfer list |

**RED (8) = will go GREEN after Phase 8 implementation.**  
**GREEN (6) = existing behaviour regression-protected.**

---

## Files

```
tests/deployment/
├── test_phase_runner_project_file.py   ← new (STORY-524)
└── test_push_code_safety.sh            ← extended: TC-12, TC-13, TC-14 added
```

---

## Group A — Import fallback chain

**Why:** `sys.path` on agent VMs is `['/opt/agent']`. The package-style import
`from deployment.hermes.project_file import update_story_status` always fails there
because no `deployment/hermes/` package tree exists. Phase 8 must add a nested
fallback that tries the flat layout `from project_file import update_story_status`.

### TC-A1 `test_source_contains_flat_import_fallback` [RED]

**Verifies:** `sdlc_phase_runner.py` source contains `from project_file import update_story_status`.

**Arrange:** Read `deployment/hermes/sdlc_phase_runner.py` source as text.  
**Act:** Check for the flat-path import string.  
**Assert:** `"from project_file import update_story_status" in source`.

**Why this matters:** Without this line, copying `project_file.py` to `/opt/agent/` has
no effect — the module attribute stays None and warnings continue.

### TC-A2 `test_source_has_nested_try_for_flat_fallback` [RED]

**Verifies:** Both the flat import AND the final None assignment are present.

**Assert:** `has_flat_import and has_none_fallback`.

**Why this matters:** Guards against a partial implementation where the flat import
is added but the None fallback is dropped (would break the exception handler).

---

## Group B — .project update path in `run_sdlc_phases`

**Why:** These test the update block at lines 1052–1073 of `sdlc_phase_runner.py`.
All heavy I/O is mocked; only the `update_story_status` module attribute is under test.
`PHASE_MAP` is overridden to run a single Seed phase (phase 1, deliverable `seed.md`).
`_verify_deliverable` uses `side_effect=[False, True]` so the resume check doesn't skip
and the post-phase check doesn't abort.

### TC-B1 `test_update_story_status_called_when_available` [GREEN]

**Verifies:** When `sdlc_phase_runner.update_story_status` is a Mock (not None),
`run_sdlc_phases` calls it.

**Assert:** `mock_update.called is True`.

### TC-B2 `test_update_story_status_correct_args` [GREEN]

**Verifies:** The call passes correct `story_id`, `scope`, `assignee`, `project_path`,
and `current_phase` keyword args.

**Assert:**
- `kwargs["story_id"] == "STORY-524"`
- `kwargs["scope"] == "small"`
- `kwargs["assignee"] == "dan"` (from `env={"AGENT_NAME": "dan"}`)
- `kwargs["project_path"] == str(tmp_path / ".project")`
- `kwargs["current_phase"] == "1"`

### TC-B3 `test_warning_logged_when_update_story_status_is_none` [GREEN]

**Verifies:** When the attribute is `None`, the warning `"project_file not available"`
appears in stdout.

**Assert:** `"project_file not available" in capsys.readouterr().out`.

### TC-B4 `test_update_exception_does_not_abort_phase_run` [GREEN]

**Verifies:** If `update_story_status` raises `RuntimeError`, `run_sdlc_phases`
still returns `(True, sha)`.

**Assert:** `success is True`.

---

## Group C — Functional: real `update_story_status` appends a row

**Why:** Confirms the actual function works end-to-end on a real `.project` file.
This is the **"new row appended for the phase"** criterion from the dispatch.
No mocking of the function under test.

### TC-C1 `test_update_story_status_appends_phase_row` [GREEN]

**Verifies:** Calling `update_story_status(story_id="STORY-524", current_phase="1", ...)`
on a temp `.project` file creates a parseable Story Status row with the correct data.

**Arrange:** Write `_PROJECT_SKELETON` (standard `.project` structure) to `tmp_path / ".project"`.  
**Act:** Call real `update_story_status(current_phase="1", ...)`.  
**Assert:**
- `read_project()` returns at least one story_status row.
- Row has `story_row["story"] == "STORY-524"`.
- Row has `story_row["current_phase"] == "1"`.
- Row has `story_row["branch"] == "story-524/story-524"`.

### TC-C2 `test_update_story_status_phase_varies_between_calls` [GREEN]

**Verifies:** Output variance — different `current_phase` args produce different file content.

**Arrange:** Two separate temp `.project` files.  
**Act:** Phase-1 call on file-1, phase-7 call on file-2.  
**Assert:** `content_1 != content_7`.

---

## Group D — Push-code.sh manifest compliance (Python)

**Why:** Prevents future regressions where a newly needed file is added to `deployment/hermes/`
but forgotten in the deploy manifest. Source-level checks catch this before runtime.

### TC-D1 `test_push_code_declares_project_file_variable` [RED]

**Assert:** `"PROJECT_FILE" in push_code_source`.

### TC-D2 `test_push_code_includes_project_file_in_scp_loop` [RED]

**Assert:** `'"$PROJECT_FILE"' in push_code_source`.

### TC-D3 `test_push_code_banner_mentions_project_file` [RED]

**Assert:** `"project_file.py"` appears in the source (the `echo "Files: ..."` line).

---

## Bash manifest / integration tests (`test_push_code_safety.sh` extension)

### TC-12: `PROJECT_FILE` variable declared [RED]

```bash
tc12_project_file_variable_declared() {
    grep -q "PROJECT_FILE" "$PUSH_CODE"
}
```

### TC-13: `$PROJECT_FILE` in copy loop [RED]

```bash
tc13_project_file_in_copy_loop() {
    grep -q '"$PROJECT_FILE"' "$PUSH_CODE"
}
```

### TC-14: scp tracking — project_file.py actually transferred [RED]

Uses a **tracking scp** mock that records source file paths to a log.
Runs `push-code.sh dan` in mock mode and asserts `project_file.py` appears in the
scp transfer log — this is the "fake VM integration test" criterion from the dispatch.

```bash
tc14_scp_copies_project_file_to_vm() {
    # ... setup tracking scp ...
    bash push-code.sh dan > /dev/null 2>&1
    grep -q "project_file.py" "$scp_log"
}
```

---

## Run commands

```bash
# Python tests (run from repo root)
python3 -m pytest tests/deployment/test_phase_runner_project_file.py -v

# Bash tests
bash tests/deployment/test_push_code_safety.sh
```

---

## RED state confirmed

```
python3 -m pytest tests/deployment/test_phase_runner_project_file.py
= 5 failed (TC-A1, TC-A2, TC-D1, TC-D2, TC-D3), 6 passed =

bash tests/deployment/test_push_code_safety.sh
= 3 failed (TC-12, TC-13, TC-14), 11 passed =
```

All failures are for the right reasons (missing source patterns, not import errors).
Tests import cleanly and produce clear actionable messages for Phase 8.

---

## Phase 8 implementation checklist (derived from RED tests)

| # | File | Change |
|---|------|--------|
| 1 | `deployment/hermes/sdlc_phase_runner.py` | Add flat-path fallback: `except ImportError: try: from project_file import update_story_status; except ImportError: update_story_status = None` |
| 2 | `deployment/vm/push-code.sh` | Declare `PROJECT_FILE="$REPO_ROOT/deployment/hermes/project_file.py"` |
| 3 | `deployment/vm/push-code.sh` | Add `"$PROJECT_FILE"` to the `for f in ...` scp loop |
| 4 | `deployment/vm/push-code.sh` | Update the `echo "Files: ..."` banner to include `project_file.py` |
| 5 | `deployment/vm/push-code.sh` | Run `push-code.sh all` (or relevant agents) and verify smoke test passes |

---

## Coverage check

| Gate | Status |
|------|--------|
| Happy path: update called when available | TC-B1 ✅ |
| Error case: None → warning logged | TC-B3 ✅ |
| Error case: exception → run continues | TC-B4 ✅ |
| Boundary: output variance for different phases | TC-C2 ✅ |
| Compliance: manifest includes project_file.py | TC-D1, TC-D2, TC-D3, TC-12, TC-13, TC-14 ✅ |
| Integration: file actually scp'd to VM | TC-14 ✅ |
| Import fallback: flat layout on VM | TC-A1, TC-A2 ✅ |
| No external API calls | N/A (no external APIs) |
| No DB migrations | N/A (no ORM changes) |
