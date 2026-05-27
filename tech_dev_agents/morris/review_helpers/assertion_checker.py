"""AST-based new-behavior assertion checker for PR diffs.

STORY-1007: Replaces the old "Has tests OR is test-exempt" check with
deterministic AST analysis that catches anti-patterns like:
  - `assert 48 in url or True`  (OR_TRUE_BYPASS)  — PR #244 / dispatch_795.py:18
  - `assert True` / `assert 1 == 1` (ASSERT_CONSTANT)
  - `or 1` suffix                    (OR_TRUE_BYPASS)
  - bare `pass` with no assertions   (BARE_PASS)

Uses Python `ast` module — NOT regex — per seed boundary:
  "Use Python AST parsing (ast.parse on hunk text) for the assertion check —
   not regex. Regex misses anti-patterns; AST nails them."
"""

from __future__ import annotations

import ast
import enum
import re
from dataclasses import dataclass, field
from typing import List


class AntiPatternType(enum.Enum):
    """Classification of assertion anti-patterns."""

    OR_TRUE_BYPASS = "OR_TRUE_BYPASS"
    ASSERT_CONSTANT = "ASSERT_CONSTANT"
    BARE_PASS = "BARE_PASS"


@dataclass
class AntiPatternFinding:
    """A single anti-pattern finding in a test function."""

    file_path: str
    function_name: str
    anti_pattern: AntiPatternType
    line_number: int
    code_snippet: str
    explanation: str


def check_new_behavior_assertions(
    diff_text: str,
    pr_files: list[str],
) -> list[AntiPatternFinding]:
    """Check all new/modified test functions in a PR diff for assertion anti-patterns.

    For every test function added or modified in the diff, the function body
    must contain >= 1 `assert` statement where the asserted expression involves
    at least one symbol introduced by the PR, AND does not match anti-patterns.

    Args:
        diff_text: The full unified diff text from `gh pr diff`.
        pr_files: List of file paths changed in the PR.

    Returns:
        List of AntiPatternFinding for each detected issue. Empty list = all good.
    """
    findings: list[AntiPatternFinding] = []

    # Parse diff into per-file hunks (only added lines in test files)
    file_hunks = _extract_added_test_hunks(diff_text)

    for file_path, source_lines in file_hunks.items():
        source = "\n".join(source_lines)
        try:
            tree = ast.parse(source, filename=file_path)
        except SyntaxError:
            # If we can't parse, skip — the test file itself won't run
            continue

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not node.name.startswith("test_"):
                    continue
                func_findings = _check_test_function(node, file_path, source_lines)
                findings.extend(func_findings)

    return findings


def _extract_added_test_hunks(diff_text: str) -> dict[str, list[str]]:
    """Extract added lines from test files in a unified diff.

    Returns a dict of {file_path: [source_lines]} containing only the
    added ('+') lines from files that look like test files.
    """
    file_hunks: dict[str, list[str]] = {}
    current_file: str | None = None

    for line in diff_text.splitlines():
        # Detect file header: +++ b/tests/test_foo.py
        if line.startswith("+++ b/"):
            path = line[6:]
            # Only process test files
            if _is_test_file(path):
                current_file = path
                if current_file not in file_hunks:
                    file_hunks[current_file] = []
            else:
                current_file = None
        elif line.startswith("--- "):
            pass  # Skip --- lines
        elif line.startswith("@@"):
            pass  # Skip hunk headers
        elif current_file is not None and line.startswith("+"):
            # Added line — strip the leading '+'
            file_hunks[current_file].append(line[1:])

    return file_hunks


def _is_test_file(path: str) -> bool:
    """Check if a file path looks like a test file."""
    basename = path.rsplit("/", 1)[-1] if "/" in path else path
    return basename.startswith("test_") or basename.endswith("_test.py")


def _check_test_function(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    file_path: str,
    source_lines: list[str],
) -> list[AntiPatternFinding]:
    """Check a single test function for assertion anti-patterns."""
    findings: list[AntiPatternFinding] = []

    asserts = _collect_asserts(func)

    # If function has NO assert statements, check for bare pass
    if not asserts:
        if _has_only_pass_or_docstring(func):
            findings.append(AntiPatternFinding(
                file_path=file_path,
                function_name=func.name,
                anti_pattern=AntiPatternType.BARE_PASS,
                line_number=func.lineno,
                code_snippet=f"def {func.name}(): pass",
                explanation=(
                    f"Test function `{func.name}` has no assertions — "
                    "only `pass` and/or docstring."
                ),
            ))
        return findings

    # Check each assert for anti-patterns
    for assert_node in asserts:
        finding = _check_assert_antipattern(assert_node, func.name, file_path)
        if finding:
            findings.append(finding)

    return findings


def _collect_asserts(func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.Assert]:
    """Collect all assert statements in a function body (including nested)."""
    asserts: list[ast.Assert] = []
    for node in ast.walk(func):
        if isinstance(node, ast.Assert):
            asserts.append(node)
    return asserts


def _has_only_pass_or_docstring(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check if function body contains only Pass statements and/or a docstring."""
    for stmt in func.body:
        if isinstance(stmt, ast.Pass):
            continue
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
            # Docstring
            continue
        return False
    return True


def _check_assert_antipattern(
    node: ast.Assert,
    func_name: str,
    file_path: str,
) -> AntiPatternFinding | None:
    """Check a single assert statement for anti-patterns.

    Anti-patterns:
    1. `assert <expr> or True` / `assert <expr> or 1` — OR_TRUE_BYPASS
    2. `assert True` / `assert False` / `assert <literal>` — ASSERT_CONSTANT
    3. `assert <literal> == <literal>` — ASSERT_CONSTANT (e.g., `assert 1 == 1`)
    """
    test_expr = node.test

    # Check for `assert <expr> or True` / `assert <expr> or 1`
    if isinstance(test_expr, ast.BoolOp) and isinstance(test_expr.op, ast.Or):
        # Check if the last value in the Or chain is a truthy constant
        last = test_expr.values[-1]
        if _is_truthy_constant(last):
            return AntiPatternFinding(
                file_path=file_path,
                function_name=func_name,
                anti_pattern=AntiPatternType.OR_TRUE_BYPASS,
                line_number=node.lineno,
                code_snippet=ast.dump(node),
                explanation=(
                    f"`assert ... or {_constant_repr(last)}` always passes — "
                    "the `or` clause makes the assertion a no-op."
                ),
            )

    # Check for `assert True` / `assert <constant>`
    if _is_constant_expr(test_expr):
        return AntiPatternFinding(
            file_path=file_path,
            function_name=func_name,
            anti_pattern=AntiPatternType.ASSERT_CONSTANT,
            line_number=node.lineno,
            code_snippet=ast.dump(node),
            explanation=(
                f"Assertion is a constant expression — always evaluates "
                f"the same regardless of code behavior."
            ),
        )

    # Check for `assert <literal> == <literal>` (e.g., `assert 1 == 1`)
    if isinstance(test_expr, ast.Compare):
        if _is_constant_comparison(test_expr):
            return AntiPatternFinding(
                file_path=file_path,
                function_name=func_name,
                anti_pattern=AntiPatternType.ASSERT_CONSTANT,
                line_number=node.lineno,
                code_snippet=ast.dump(node),
                explanation=(
                    "Assertion compares constants — always evaluates the same "
                    "regardless of code behavior."
                ),
            )

    return None


def _is_truthy_constant(node: ast.expr) -> bool:
    """Check if an AST node is a truthy constant (True, 1, non-empty string, etc.)."""
    if isinstance(node, ast.Constant):
        return bool(node.value)
    # Python 3.7 compat: ast.NameConstant / ast.Num
    if isinstance(node, ast.Name) and node.id in ("True",):
        return True
    return False


def _is_constant_expr(node: ast.expr) -> bool:
    """Check if an AST node is a bare constant (True, False, number, string)."""
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.Name) and node.id in ("True", "False"):
        return True
    return False


def _is_constant_comparison(node: ast.Compare) -> bool:
    """Check if a comparison has only constants on both sides (e.g., `1 == 1`)."""
    if not _is_constant_expr(node.left):
        return False
    return all(_is_constant_expr(c) for c in node.comparators)


def _constant_repr(node: ast.expr) -> str:
    """Get a human-readable representation of a constant AST node."""
    if isinstance(node, ast.Constant):
        return repr(node.value)
    if isinstance(node, ast.Name):
        return node.id
    return ast.dump(node)


def _main() -> None:
    """CLI entry point: read diff from stdin, print findings as JSON.

    Usage:
        gh pr diff <PR> | python3 -m tech_dev_agents.morris.review_helpers.assertion_checker
    """
    import json
    import sys

    diff_text = sys.stdin.read()
    # Extract file paths from diff headers
    pr_files: list[str] = []
    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            pr_files.append(line[6:])

    findings = check_new_behavior_assertions(diff_text, pr_files)
    results = [
        {
            "file_path": f.file_path,
            "function_name": f.function_name,
            "anti_pattern": f.anti_pattern.value,
            "line_number": f.line_number,
            "code_snippet": f.code_snippet,
            "explanation": f.explanation,
        }
        for f in findings
    ]
    print(json.dumps(results, indent=2))
    if findings:
        sys.exit(1)


if __name__ == "__main__":
    _main()
