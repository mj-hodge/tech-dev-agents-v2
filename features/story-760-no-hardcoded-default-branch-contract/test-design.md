# Test Design — STORY-760: No Hardcoded Default-Branch Contract

## Phase 7 Status

**RED state achieved.** 10 tests collect cleanly.
- 4 PASS (data-only, no scanner needed)
- 5 FAIL (scanner not implemented — `NotImplementedError`)
- 1 XFAIL strict (real-source scan — STORY-759 not yet on main)

```
$ python3 -m pytest tests/deployment/test_no_hardcoded_default_branch.py -v
5 failed, 4 passed, 1 xfailed in 0.13s
```

---

## Test File

**`tests/deployment/test_no_hardcoded_default_branch.py`** — new file, 10 tests.

---

## Architecture

The test file is entirely self-contained. It exports:

| Symbol | Role |
|--------|------|
| `SCAN_FILES` | Constant list of the three source files to scan |
| `FORBIDDEN_LITERALS` | `frozenset({"main", "master", "develop", "trunk"})` |
| `ALLOWLIST` | List of `(file, (lo, hi), reason)` exempt ranges |
| `Violation` | Dataclass: `filename`, `line`, `argv`, `literal`, `.message()` |
| `_scan_source(source, filename)` | **Phase 8 stub** — raises `NotImplementedError` |
| `_is_allowlisted(filename, lineno)` | Checks allowlist membership |

---

## Test Groups

### A. Scanner Constants (4 tests) — GREEN in Phase 7

These tests validate the test's own data and never invoke `_scan_source`.

| Test | SC/AC | What it checks |
|------|-------|----------------|
| `test_scope_covers_three_files` | SC-1/AC-2 | `SCAN_FILES` has exactly 3 entries, all named |
| `test_forbidden_literals_set` | SC-2/AC-3 | `FORBIDDEN_LITERALS == {"main","master","develop","trunk"}` |
| `test_allowlist_entries_have_reasons` | SC-4/AC-4 | Every ALLOWLIST entry is a `(str,(int,int),str)` 3-tuple with non-empty reason |
| `test_no_subprocess_run_in_test` | SC-3/AC-7 | AST-parses this test file itself; asserts no `subprocess.*` calls at runtime |

### B. Scanner Behaviour — Inline Fixtures (5 tests) — RED in Phase 7

All invoke `_scan_source`, which raises `NotImplementedError` until Phase 8.

| Test | SC/AC | What it checks |
|------|-------|----------------|
| `test_negative_case_inline_fixture` | SC-6/AC-5 | Fake source with `subprocess.run(["git", ..., "checkout", "main"])` → violation found, `v.literal == "main"` |
| `test_positive_case_inline_fixture` | SC-6 | Fake source with dynamic `branch` variable → no violations |
| `test_diagnostic_includes_file_line_argv_hint` | SC-5/AC-5/AC-11 | `v.message()` contains: file, line, `"main"`, `"_resolve_default_branch"`, `"violation"` |
| `test_popen_also_detected` | AC-6 | `subprocess.Popen(["git", "pull", "origin", "master"], ...)` → violation with `literal == "master"` |
| `test_non_git_subprocess_not_flagged` | SC-2 | `subprocess.run(["pip", "install", "main-package"])` → no violations |

### C. Real-Source Scan (1 test) — XFAIL strict

| Test | Decorator | Expected state |
|------|-----------|----------------|
| `test_real_source_no_violations` | `@pytest.mark.xfail(strict=True, reason="STORY-759 not yet on main…")` | XFAIL now; after STORY-759 merges + Phase 8 implements scanner → remove decorator → GREEN |

**Why xfail strict?** STORY-759 has not merged. Current violations exist at:
- `sdlc_phase_runner.py` ~L2091: `["git", "-C", workdir, "checkout", "main"]`
- `sdlc_phase_runner.py` ~L2097: `["git", "-C", workdir, "pull", "--ff-only", "origin", "main"]`
- `sdlc_phase_runner.py` ~L2316: `["git", "-C", workdir, "fetch", "origin", "main"]`

If the xfail were removed and the scanner were implemented, the test would FAIL on these lines. With `strict=True`, an unexpected pass (XPASS) would also fail CI — ensuring the Phase 8 agent notices when STORY-759 has merged.

---

## Scanner Implementation Notes (Phase 8)

Implement `_scan_source(source: str, filename: str) -> list[Violation]` using `ast`:

```python
tree = ast.parse(source)
for node in ast.walk(tree):
    if not isinstance(node, (ast.Expr, ast.Assign)):  # top-level call
        ...
    # Find Call nodes: isinstance(node, ast.Call)
    # Check func is subprocess.* attribute call
    # Check first arg is ast.List with first element ast.Constant == "git"
    # Walk remaining elements; record Violation for each that is
    # ast.Constant whose value is in FORBIDDEN_LITERALS
```

Key AST patterns:
- `subprocess.run([...])` → `ast.Call` with `func = ast.Attribute(value=ast.Name(id='subprocess'), attr='run')`
- First arg: `node.args[0]` if it's an `ast.List`
- Walk `node.args[0].elts` for `ast.Constant` nodes
- `node.lineno` gives the line number for the diagnostic

Regex fallback (if AST is brittle for dynamically-constructed lists): scan for lines matching `r'subprocess\.(run|Popen|check_call|check_output)\s*\(\s*\[' ` and then check the same line or continuation lines for the forbidden literals in a git context.

---

## Allowlist Policy

**Currently empty.** When STORY-759 merges and introduces `_resolve_default_branch`, that function legitimately passes `"main"` and `"master"` to subprocess as part of its fallback probe chain. Add:

```python
ALLOWLIST: list[tuple[str, tuple[int, int], str]] = [
    (
        "deployment/hermes/sdlc_phase_runner.py",
        (NNNN, NNNN + 25),  # replace with actual line range
        "STORY-759: _resolve_default_branch fallback chain — "
        "resolver probes 'main' then 'master' to discover default branch",
    ),
]
```

No entry should use a glob path or omit the reason string. SC-4 test enforces this.

---

## Phase 8 Checklist

1. **Delete the `@pytest.mark.xfail(...)` decorator** from `test_real_source_no_violations`.
2. **Implement `_scan_source`** using AST walk per notes above.
3. **Run tests**: if STORY-759 has merged, all 10 tests should be GREEN.
   - If `test_real_source_no_violations` fails with violations at the 3 known lines → STORY-759 is NOT on main. Stop and wait.
   - If it fails with violations at OTHER lines → a new regression; fix or allowlist with reason.
4. **Add allowlist entry** for `_resolve_default_branch`'s fallback chain (actual line numbers from the merged STORY-759 commit).
5. **Negative-case demo** for PR body: inject `subprocess.run(["git", "checkout", "main"])` into `dispatch_poller.py`, run test, show failure message, revert.
6. **Regression check**: `python3 -m pytest tests/ -x --ignore=tests/e2e -q` — all GREEN.

---

## Performance

Full test run: **0.13s** in Phase 7. Will remain < 1s in Phase 8 (pure CPU-bound AST parse of three small Python files).

---

## Coverage Targets

| Requirement | Test(s) | Phase 7 state |
|-------------|---------|---------------|
| SC-1: 3 files scanned | `test_scope_covers_three_files` | PASS |
| SC-2: forbidden set | `test_forbidden_literals_set`, `test_negative_case_inline_fixture`, `test_non_git_subprocess_not_flagged` | PASS / FAIL / FAIL |
| SC-3: no live subprocess in test | `test_no_subprocess_run_in_test` | PASS |
| SC-4: allowlist has reasons | `test_allowlist_entries_have_reasons` | PASS |
| SC-5: diagnostic format | `test_diagnostic_includes_file_line_argv_hint` | FAIL |
| SC-6: negative case | `test_negative_case_inline_fixture` | FAIL |
| SC-7: zero regressions / real-source | `test_real_source_no_violations` | XFAIL strict |
| AC-6: Popen detected | `test_popen_also_detected` | FAIL |
