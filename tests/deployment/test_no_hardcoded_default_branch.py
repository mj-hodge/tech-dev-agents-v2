"""Contract test: no hardcoded default-branch literals in dispatch git commands.

SC-1: Scans exactly three dispatch source files for git subprocess calls that
      pass a hardcoded branch name.
SC-2: Forbidden literals are {"main", "master", "develop", "trunk"}.
SC-3: Detection via AST (preferred); regex fallback documented.
SC-4: Allowlist of (file, line_range, reason) tuples — every entry needs a reason.
SC-5: Violation message names file, line, argv, and includes remediation hint.
SC-7: Real-source scan is xfail(strict=True) until STORY-759 merges.

Phase 7 RED state:
  - TestScannerConstants (4 tests): PASS — these validate data only, no scanner needed.
  - TestScannerBehaviour (5 tests): FAIL — _scan_source raises NotImplementedError.
  - test_real_source_no_violations: XFAIL (strict=True) — scanner not implemented
    AND violations exist at sdlc_phase_runner.py lines 2091, 2097, 2316.

Phase 8 step 1: delete the xfail decorator from test_real_source_no_violations,
  then implement _scan_source. If STORY-759 has merged, all tests will be GREEN.
  If tests are still RED after implementing the scanner, stop — STORY-759 is not
  yet on main. Do NOT allowlist STORY-759's unfixed lines.

When STORY-759 merges, add an allowlist entry for _resolve_default_branch's
  fallback chain (it legitimately tries "main" then "master" in subprocess calls).
  Entry format: ("deployment/hermes/sdlc_phase_runner.py", (NNNN, NNNN+20),
                  "STORY-759: _resolve_default_branch fallback chain").

STORY-760
"""

from __future__ import annotations

import ast
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Constants — SC-1, SC-2, SC-4
# ---------------------------------------------------------------------------

SCAN_FILES: list[str] = [
    "deployment/hermes/sdlc_phase_runner.py",
    "deployment/hermes/dispatch_poller.py",
    "tech_dev_agents/ops_console/services/dispatch_db_service.py",
]

FORBIDDEN_LITERALS: frozenset[str] = frozenset({"main", "master", "develop", "trunk"})

REMEDIATION_HINT = "→ use _resolve_default_branch(workdir) instead"

# Allowlist: list of (file_path, (line_lo, line_hi), reason) tuples.
# Each entry exempts all lines in the inclusive range [line_lo, line_hi] of
# the given file.  Adding a new entry REQUIRES a non-empty reason string.
ALLOWLIST: list[tuple[str, tuple[int, int], str]] = [
    (
        "deployment/hermes/sdlc_phase_runner.py",
        (2460, 2498),
        "STORY-759: _resolve_default_branch fallback chain — "
        "resolver probes 'main' then 'master' via ls-remote to discover default branch",
    ),
]


# ---------------------------------------------------------------------------
# Violation dataclass — SC-5, AC-11
# ---------------------------------------------------------------------------


@dataclass
class Violation:
    """A single detected hardcoded branch literal in a git subprocess call."""

    filename: str
    line: int
    argv: list[str]
    literal: str = field(default="")

    def message(self) -> str:
        """Return a machine-parseable diagnostic string.

        grep "violation" on pytest output will yield the (file, line, literal).
        """
        argv_repr = repr(self.argv)[:200]
        return (
            f"violation at {self.filename}:{self.line} — "
            f"literal {self.literal!r} found in git argv {argv_repr}. "
            f"{REMEDIATION_HINT}"
        )


# ---------------------------------------------------------------------------
# Scanner — Phase 8: implement this function
# ---------------------------------------------------------------------------


def _scan_source(source: str, filename: str) -> list[Violation]:
    """AST-parse *source* and return all Violation instances.

    Walks the AST for ``subprocess.run``, ``subprocess.Popen``, and any other
    ``subprocess.*`` call whose first positional argument is a list literal
    whose first element is the string ``"git"``.  If any other element in that
    list is a string matching FORBIDDEN_LITERALS, a Violation is recorded.

    No subprocess execution occurs — pure static analysis.
    """
    violations: list[Violation] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return violations

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        # Match subprocess.run / subprocess.Popen / subprocess.call / etc.
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "subprocess"
        ):
            continue

        # First positional arg must be a list literal starting with "git"
        if not node.args:
            continue
        first_arg = node.args[0]
        if not isinstance(first_arg, ast.List) or not first_arg.elts:
            continue
        first_elt = first_arg.elts[0]
        if not (isinstance(first_elt, ast.Constant) and first_elt.value == "git"):
            continue

        # Reconstruct argv for diagnostics
        argv_repr: list[str] = []
        for elt in first_arg.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                argv_repr.append(elt.value)
            else:
                argv_repr.append("<expr>")

        # Check remaining elements for forbidden literals
        for elt in first_arg.elts[1:]:
            if (
                isinstance(elt, ast.Constant)
                and isinstance(elt.value, str)
                and elt.value in FORBIDDEN_LITERALS
            ):
                violations.append(
                    Violation(
                        filename=filename,
                        line=node.lineno,
                        argv=argv_repr,
                        literal=elt.value,
                    )
                )

    return violations


def _is_allowlisted(filename: str, lineno: int) -> bool:
    """Return True if (filename, lineno) falls within an ALLOWLIST entry."""
    for f, (lo, hi), _ in ALLOWLIST:
        if f == filename and lo <= lineno <= hi:
            return True
    return False


# ---------------------------------------------------------------------------
# A. Scanner constants — GREEN in Phase 7 (no scanner invocation)
# ---------------------------------------------------------------------------


class TestScannerConstants:
    """Validate the test's own constants — SC-1, SC-2, SC-4.

    These tests do not invoke the scanner; they are GREEN in Phase 7.
    """

    def test_scope_covers_three_files(self):
        """SC-1: SCAN_FILES must name exactly the three dispatch source files."""
        assert len(SCAN_FILES) == 3, (
            f"Expected 3 scan targets, got {len(SCAN_FILES)}: {SCAN_FILES}"
        )
        assert "deployment/hermes/sdlc_phase_runner.py" in SCAN_FILES, (
            "SCAN_FILES must include sdlc_phase_runner.py"
        )
        assert "deployment/hermes/dispatch_poller.py" in SCAN_FILES, (
            "SCAN_FILES must include dispatch_poller.py"
        )
        assert (
            "tech_dev_agents/ops_console/services/dispatch_db_service.py" in SCAN_FILES
        ), "SCAN_FILES must include dispatch_db_service.py"

    def test_forbidden_literals_set(self):
        """SC-2/AC-3: FORBIDDEN_LITERALS must be exactly {main, master, develop, trunk}."""
        assert FORBIDDEN_LITERALS == frozenset({"main", "master", "develop", "trunk"}), (
            f"FORBIDDEN_LITERALS must be exactly {{main, master, develop, trunk}}, "
            f"got {sorted(FORBIDDEN_LITERALS)}"
        )

    def test_allowlist_entries_have_reasons(self):
        """SC-4/AC-4: Every ALLOWLIST entry must be a 3-tuple with non-empty reason."""
        for i, entry in enumerate(ALLOWLIST):
            assert len(entry) == 3, (
                f"ALLOWLIST[{i}] must be (file, (lo, hi), reason) — got {entry!r}"
            )
            filepath, line_range, reason = entry
            assert isinstance(filepath, str) and filepath, (
                f"ALLOWLIST[{i}] file must be a non-empty string"
            )
            assert (
                isinstance(line_range, tuple)
                and len(line_range) == 2
                and all(isinstance(n, int) for n in line_range)
            ), f"ALLOWLIST[{i}] line_range must be a (int, int) tuple"
            assert reason and reason.strip(), (
                f"ALLOWLIST[{i}] has an empty reason string. "
                "Every allowlist entry must explain WHY the literal is legitimate "
                '(e.g., "STORY-759: _resolve_default_branch fallback chain").'
            )

    def test_no_subprocess_run_in_test(self):
        """SC-3/AC-7: The test file itself must not call subprocess at runtime."""
        test_source = Path(__file__).read_text()
        tree = ast.parse(test_source)
        runtime_calls: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            # subprocess.run(...) / subprocess.Popen(...) etc.
            if (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "subprocess"
                and func.attr in ("run", "Popen", "check_output", "check_call", "call")
            ):
                runtime_calls.append(f"line {node.lineno}: subprocess.{func.attr}()")
        assert not runtime_calls, (
            "Test file must perform static analysis only — no subprocess calls. "
            f"Found: {runtime_calls}"
        )


# ---------------------------------------------------------------------------
# B. Scanner behaviour — inline fixtures (RED in Phase 7)
# ---------------------------------------------------------------------------


class TestScannerBehaviour:
    """Verify scanner logic using inline string fixtures.

    All tests in this class are RED in Phase 7 because _scan_source raises
    NotImplementedError.  They become GREEN in Phase 8 once the scanner is
    implemented.
    """

    # Source with a clear violation: subprocess.run(["git", ..., "checkout", "main"])
    VIOLATION_SOURCE = textwrap.dedent(
        """\
        import subprocess
        def switch_branch(workdir):
            subprocess.run(["git", "-C", workdir, "checkout", "main"],
                           capture_output=True, text=True, timeout=10)
        """
    )

    # Clean source: branch arg is a variable, not a literal
    CLEAN_SOURCE = textwrap.dedent(
        """\
        import subprocess
        def switch_branch(workdir, branch):
            subprocess.run(["git", "-C", workdir, "checkout", branch],
                           capture_output=True, text=True, timeout=10)
        """
    )

    def test_negative_case_inline_fixture(self):
        """SC-6/AC-5: Scanner detects literal 'main' in git checkout argv."""
        violations = _scan_source(self.VIOLATION_SOURCE, "fake_dispatch.py")
        assert violations, (
            "Expected ≥1 violation for subprocess.run(['git', ..., 'checkout', 'main']) "
            "— scanner returned none.  Ensure _scan_source inspects all string literals "
            "in list-literal git argv."
        )
        literals_found = {v.literal for v in violations}
        assert "main" in literals_found, (
            f"Violation must identify the literal 'main', got literals: {literals_found}"
        )

    def test_positive_case_inline_fixture(self):
        """Scanner must NOT flag git commands whose branch arg is a variable."""
        violations = _scan_source(self.CLEAN_SOURCE, "fake_dispatch.py")
        assert not violations, (
            f"Dynamic branch variable must NOT be flagged — got: {violations}"
        )

    def test_diagnostic_includes_file_line_argv_hint(self):
        """SC-5/AC-5: Violation.message() must contain file, line, argv, remediation hint."""
        violations = _scan_source(self.VIOLATION_SOURCE, "fake_dispatch.py")
        assert violations, "Expected a violation — scanner returned none"
        msg = violations[0].message()
        assert "fake_dispatch.py" in msg, (
            f"Diagnostic must name the file. Got:\n{msg}"
        )
        assert str(violations[0].line) in msg, (
            f"Diagnostic must include the line number. Got:\n{msg}"
        )
        assert "main" in msg, (
            f"Diagnostic must include the offending literal. Got:\n{msg}"
        )
        assert "_resolve_default_branch" in msg, (
            f"Diagnostic must include the remediation hint '{REMEDIATION_HINT}'. Got:\n{msg}"
        )
        # AC-11: machine-parseable — the word "violation" must appear for grep
        assert "violation" in msg.lower(), (
            f"Diagnostic must contain 'violation' for grep parseability. Got:\n{msg}"
        )

    def test_popen_also_detected(self):
        """AC-6: subprocess.Popen with git argv is also detected."""
        popen_source = textwrap.dedent(
            """\
            import subprocess
            def legacy_pull(workdir):
                subprocess.Popen(["git", "pull", "origin", "master"],
                                 stdout=subprocess.PIPE)
            """
        )
        violations = _scan_source(popen_source, "fake_popen.py")
        assert violations, (
            "subprocess.Popen(['git', 'pull', 'origin', 'master']) must be flagged. "
            "Ensure _scan_source handles both subprocess.run and subprocess.Popen."
        )
        assert any(v.literal == "master" for v in violations), (
            f"Expected violation with literal='master', got: {violations}"
        )

    def test_non_git_subprocess_not_flagged(self):
        """Scanner must NOT flag subprocess calls for non-git commands."""
        non_git_source = textwrap.dedent(
            """\
            import subprocess
            def install():
                subprocess.run(["pip", "install", "main-package"], check=True)
            """
        )
        violations = _scan_source(non_git_source, "fake_pip.py")
        assert not violations, (
            f"Non-git subprocess commands must not be flagged. Got: {violations}"
        )


# ---------------------------------------------------------------------------
# C. Real-source scan — xfail(strict=True) until STORY-759 merges
# ---------------------------------------------------------------------------


def test_real_source_no_violations() -> None:
    """SC-7: No un-allowlisted hardcoded branch literals in the three scan targets.

    This test is XFAIL (strict) in Phase 7.
    Phase 8: remove the xfail, implement _scan_source, ensure GREEN.
    """
    violations: list[Violation] = []
    for filepath in SCAN_FILES:
        p = Path(filepath)
        assert p.exists(), (
            f"Scan target {filepath!r} does not exist — verify SCAN_FILES paths "
            "are relative to the project root (where pytest is run from)."
        )
        source = p.read_text()
        found = _scan_source(source, filepath)
        for v in found:
            if not _is_allowlisted(v.filename, v.line):
                violations.append(v)

    if violations:
        lines = "\n".join(f"  {v.message()}" for v in violations)
        pytest.fail(
            f"Found {len(violations)} hardcoded default-branch literal(s) in "
            f"dispatch git commands:\n{lines}\n\n"
            f"Options:\n"
            f"  1. Fix the violation — replace the literal with "
            f"_resolve_default_branch(workdir).\n"
            f"  2. If the usage is legitimately correct, add an ALLOWLIST entry "
            f"with a non-empty reason string.\n"
            f"Run: grep -n 'violation' pytest.log  to extract (file, line, literal)."
        )
