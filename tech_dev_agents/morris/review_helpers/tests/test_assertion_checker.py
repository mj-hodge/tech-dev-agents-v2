"""Tests for assertion_checker.py — STORY-1007 SC-4 through SC-6.

RED-first tests. Each test validates that the AST-based assertion checker
correctly identifies anti-patterns and accepts legitimate assertions.

Evidence: PR #244 (STORY-766) shipped `assert 48 in url or True` — see
dispatch_795.py:18. That test "exists" and "has assertions" but tests nothing.
"""

from __future__ import annotations

import pytest

from tech_dev_agents.morris.review_helpers.assertion_checker import (
    AntiPatternFinding,
    AntiPatternType,
    check_new_behavior_assertions,
)


# ---------------------------------------------------------------------------
# SC-4: The PR #244 `assert 48 in url or True` case (OR_TRUE_BYPASS)
# ---------------------------------------------------------------------------


def test_or_true_antipattern_caught():
    """SC-4: `assert 48 in url or True` must be flagged as OR_TRUE_BYPASS.

    This is the exact pattern from PR #244 / dispatch_795.py:18 that
    slipped through Morris's old "Has tests" check.
    """
    diff_text = '''\
diff --git a/tests/test_lookback.py b/tests/test_lookback.py
new file mode 100644
--- /dev/null
+++ b/tests/test_lookback.py
@@ -0,0 +1,10 @@
+import pytest
+
+def test_lookback_window_configurable(url):
+    """Test that lookback window is configurable."""
+    assert 48 in url or True
'''
    pr_files = ["tests/test_lookback.py"]
    findings = check_new_behavior_assertions(diff_text, pr_files)

    assert len(findings) >= 1
    or_true = [f for f in findings if f.anti_pattern == AntiPatternType.OR_TRUE_BYPASS]
    assert len(or_true) >= 1, f"Expected OR_TRUE_BYPASS finding, got: {findings}"
    assert "test_lookback_window_configurable" in or_true[0].function_name


# ---------------------------------------------------------------------------
# SC-5: Other anti-pattern variants
# ---------------------------------------------------------------------------


def test_assert_true_antipattern_caught():
    """SC-5: `assert True` must be flagged as ASSERT_CONSTANT."""
    diff_text = '''\
diff --git a/tests/test_feature.py b/tests/test_feature.py
new file mode 100644
--- /dev/null
+++ b/tests/test_feature.py
@@ -0,0 +1,8 @@
+def test_new_feature():
+    """Test new feature."""
+    result = do_something()
+    assert True
'''
    pr_files = ["tests/test_feature.py"]
    findings = check_new_behavior_assertions(diff_text, pr_files)

    assert len(findings) >= 1
    constant = [f for f in findings if f.anti_pattern == AntiPatternType.ASSERT_CONSTANT]
    assert len(constant) >= 1, f"Expected ASSERT_CONSTANT finding, got: {findings}"


def test_assert_constant_antipattern_caught():
    """SC-5: `assert 1 == 1` must be flagged as ASSERT_CONSTANT."""
    diff_text = '''\
diff --git a/tests/test_math.py b/tests/test_math.py
new file mode 100644
--- /dev/null
+++ b/tests/test_math.py
@@ -0,0 +1,8 @@
+def test_computation():
+    """Test some computation."""
+    x = compute_value()
+    assert 1 == 1
'''
    pr_files = ["tests/test_math.py"]
    findings = check_new_behavior_assertions(diff_text, pr_files)

    assert len(findings) >= 1
    constant = [f for f in findings if f.anti_pattern == AntiPatternType.ASSERT_CONSTANT]
    assert len(constant) >= 1, f"Expected ASSERT_CONSTANT for `assert 1 == 1`, got: {findings}"


def test_bare_pass_antipattern_caught():
    """SC-5: A test function with only `pass` (no assert) must be flagged as BARE_PASS."""
    diff_text = '''\
diff --git a/tests/test_stub.py b/tests/test_stub.py
new file mode 100644
--- /dev/null
+++ b/tests/test_stub.py
@@ -0,0 +1,6 @@
+def test_placeholder():
+    """Placeholder test."""
+    pass
'''
    pr_files = ["tests/test_stub.py"]
    findings = check_new_behavior_assertions(diff_text, pr_files)

    assert len(findings) >= 1
    bare = [f for f in findings if f.anti_pattern == AntiPatternType.BARE_PASS]
    assert len(bare) >= 1, f"Expected BARE_PASS finding, got: {findings}"


def test_or_one_antipattern_caught():
    """SC-5: `assert x or 1` must be flagged as OR_TRUE_BYPASS (same class as `or True`)."""
    diff_text = '''\
diff --git a/tests/test_check.py b/tests/test_check.py
new file mode 100644
--- /dev/null
+++ b/tests/test_check.py
@@ -0,0 +1,6 @@
+def test_check_value(val):
+    """Test that value is correct."""
+    assert val > 0 or 1
'''
    pr_files = ["tests/test_check.py"]
    findings = check_new_behavior_assertions(diff_text, pr_files)

    or_bypass = [f for f in findings if f.anti_pattern == AntiPatternType.OR_TRUE_BYPASS]
    assert len(or_bypass) >= 1, f"Expected OR_TRUE_BYPASS for `or 1`, got: {findings}"


# ---------------------------------------------------------------------------
# SC-6: Legitimate assertions must NOT be flagged
# ---------------------------------------------------------------------------


def test_legitimate_assertion_passes():
    """SC-6: A proper parametrized assertion referencing a PR-new symbol must pass."""
    diff_text = '''\
diff --git a/tests/test_api.py b/tests/test_api.py
new file mode 100644
--- /dev/null
+++ b/tests/test_api.py
@@ -0,0 +1,12 @@
+import pytest
+from mymodule import new_endpoint
+
+@pytest.mark.parametrize("input_val,expected", [(1, 200), (0, 400)])
+def test_new_endpoint_returns_correct_status(input_val, expected):
+    """Test that the new endpoint returns correct HTTP status."""
+    response = new_endpoint(input_val)
+    assert response.status_code == expected
+    assert response.json()["ok"] is True
'''
    pr_files = ["tests/test_api.py", "mymodule.py"]
    findings = check_new_behavior_assertions(diff_text, pr_files)

    assert len(findings) == 0, f"Legitimate assertion flagged as anti-pattern: {findings}"


def test_legitimate_assertion_with_variable_passes():
    """SC-6: An assertion using a computed variable (not a constant) passes."""
    diff_text = '''\
diff --git a/tests/test_calc.py b/tests/test_calc.py
new file mode 100644
--- /dev/null
+++ b/tests/test_calc.py
@@ -0,0 +1,8 @@
+from calculator import add
+
+def test_add_two_numbers():
+    result = add(2, 3)
+    assert result == 5
'''
    pr_files = ["tests/test_calc.py", "calculator.py"]
    findings = check_new_behavior_assertions(diff_text, pr_files)

    assert len(findings) == 0, f"Legitimate assertion flagged: {findings}"


def test_multiple_antipatterns_in_one_file():
    """Multiple anti-patterns in a single test file should all be caught."""
    diff_text = '''\
diff --git a/tests/test_mixed.py b/tests/test_mixed.py
new file mode 100644
--- /dev/null
+++ b/tests/test_mixed.py
@@ -0,0 +1,14 @@
+def test_good():
+    result = compute()
+    assert result == 42
+
+def test_bad_or_true():
+    assert something() or True
+
+def test_bad_pass():
+    pass
+
+def test_bad_constant():
+    x = run()
+    assert True
'''
    pr_files = ["tests/test_mixed.py"]
    findings = check_new_behavior_assertions(diff_text, pr_files)

    # Should find 3 anti-patterns (or_true, bare_pass, assert_constant) but not flag test_good
    assert len(findings) == 3, f"Expected 3 findings, got {len(findings)}: {findings}"
    flagged_funcs = {f.function_name for f in findings}
    assert "test_good" not in flagged_funcs
    assert "test_bad_or_true" in flagged_funcs
    assert "test_bad_pass" in flagged_funcs
    assert "test_bad_constant" in flagged_funcs
