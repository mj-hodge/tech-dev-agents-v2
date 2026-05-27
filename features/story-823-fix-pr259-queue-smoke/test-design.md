# STORY-823: Test Design — Fix PR #259 Review Findings

## Test Strategy

STORY-823 is a fix/rework story. Phase 7 delivers verification tests that assert each
Morris review finding is resolved. Tests are written against the *current* (broken)
state of `story-096/blocked-question` so they start RED; Phase 8 applies the fixes and
turns them GREEN.

**Test file:** `tests/test_story823_pr259_fixes.py`

---

## Test Cases

### F01: `.project` — No Conflict Markers (3 tests)

| Field | Value |
|-------|-------|
| Class | `TestProjectNoConflictMarkers` |
| Target | `.project` |
| RED reason | `.project` on this branch has `<<<<<<< HEAD` committed as content (artifact of a bad rebase) |

| Test | Assertion | RED? |
|------|-----------|------|
| `test_no_conflict_start_marker` | `<<<<<<<` not in `.project` | ✅ RED |
| `test_no_conflict_separator` | No line exactly `=======` | GREEN (separator absent) |
| `test_no_conflict_end_marker` | No line starting `>>>>>>>` | GREEN (end absent) |

**Phase 8 fix:** Rebase `story-096/blocked-question` onto current main; resolve `.project`
so it only appends the STORY-096 and STORY-823 multi-worker rows — no conflict markers.

---

### F02: `tests/conftest.py` — No Global `sys.path` Manipulation (2 tests)

| Field | Value |
|-------|-------|
| Class | `TestConftestNoGlobalSysPath` |
| Target | `tests/conftest.py` |
| RED reason | `conftest.py` currently has `sys.path.insert(0, str(_SCRIPTS_DIR))` at module level (lines 3–10) |

| Test | Assertion | RED? |
|------|-----------|------|
| `test_no_sys_path_in_global_conftest` | `"sys.path"` not in `tests/conftest.py` | ✅ RED |
| `test_smoke_marker_still_registered` | `"smoke"` still in `tests/conftest.py` | GREEN (smoke marker present) |

**Phase 8 fix:** Remove `import sys`, `_SCRIPTS_DIR`, and `sys.path.insert` block from
`tests/conftest.py`. Keep `pytest_configure` with the smoke marker.

---

### F03: `work_queue` Import — Localised Mechanism (1 test)

| Field | Value |
|-------|-------|
| Class | `TestWorkQueueImportLocalised` |
| Target | `tests/test_queue_smoke.py` and/or `scripts/conftest.py` |
| RED reason | `test_queue_smoke.py` imports `work_queue` at top-level without a local `sys.path` insert; it currently relies on the global conftest path (which Phase 8 removes) |

| Test | Assertion | RED? |
|------|-----------|------|
| `test_local_sys_path_or_scripts_conftest` | Either `tests/test_queue_smoke.py` contains `"sys.path"` OR `scripts/conftest.py` exists with `"sys.path"` | ✅ RED |

**Phase 8 fix:** Add a localised `sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))`
near the top of `tests/test_queue_smoke.py` (before `import work_queue`).

---

## RED State Verification

```
$ python3 -m pytest tests/test_story823_pr259_fixes.py -v

FAILED  TestProjectNoConflictMarkers::test_no_conflict_start_marker   (<<<<<<< HEAD in .project)
FAILED  TestConftestNoGlobalSysPath::test_no_sys_path_in_global_conftest (sys.path.insert present)
FAILED  TestWorkQueueImportLocalised::test_local_sys_path_or_scripts_conftest (no local path)
PASSED  TestProjectNoConflictMarkers::test_no_conflict_separator
PASSED  TestProjectNoConflictMarkers::test_no_conflict_end_marker
PASSED  TestConftestNoGlobalSysPath::test_smoke_marker_still_registered

3 failed, 3 passed
```

---

## Regression Baseline

The 5 existing smoke tests must remain GREEN throughout Phase 8:

```
$ python3 -m pytest tests/test_queue_smoke.py -v
5/5 PASSED
```

After Phase 8 removes `sys.path` from `tests/conftest.py`, the smoke tests must still
pass because the local import mechanism (F03 fix) provides the path.

---

## Acceptance Criteria Mapping

| SC | Criterion | Test |
|----|-----------|------|
| SC-2 | `.project` append-only, no conflict markers | F01 (`test_no_conflict_start_marker`) |
| SC-3 | No `sys.path` in `tests/conftest.py` | F02 (`test_no_sys_path_in_global_conftest`) |
| SC-3 | `work_queue` importable via local mechanism | F03 (`test_local_sys_path_or_scripts_conftest`) |
| SC-5 | 5 smoke tests GREEN | Regression baseline (`tests/test_queue_smoke.py`) |
| SC-6 | Zero regressions | `pytest tests/ -x --timeout=60` |

---

## Post-Phase 8 Expected State

```
$ python3 -m pytest tests/test_story823_pr259_fixes.py tests/test_queue_smoke.py -v
9/9 PASSED
```
