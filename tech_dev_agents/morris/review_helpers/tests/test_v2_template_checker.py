"""Tests for v2_template_checker.py — STORY-1007 SC-7 through SC-9.

RED-first tests. Each test validates that the 3-question PR template checker
correctly enforces the `gc-data-v2/pipeline-template/pull_request_template.md`
contract for *-v2 and api-advertising-amazon repos.

Required headings:
  ### 1. Canon-doc impact
  ### 2. Scaffold backport
  ### 3. Sibling-pipeline sweep

Rules:
  - All 3 headings must be present.
  - Each section must have >= 1 line with a checked box `- [x]` or a
    non-N/A explanation.
  - "N/A" alone (bare token) is rejected; "N/A — <justification>" is accepted.
"""

from __future__ import annotations

import pytest

from tech_dev_agents.morris.review_helpers.v2_template_checker import (
    ThreeQResult,
    check_three_question_template,
    is_v2_repo,
)


# ---------------------------------------------------------------------------
# SC-7: Missing heading rejected
# ---------------------------------------------------------------------------


def test_missing_heading_rejected():
    """SC-7: A PR body missing one of the three required headings is rejected."""
    body = """\
### 1. Canon-doc impact
- [x] No canon-doc changes needed.

### 2. Scaffold backport
- [x] Scaffold is up to date.

<!-- Missing ### 3. Sibling-pipeline sweep entirely -->
"""
    result = check_three_question_template(body)

    assert not result.passed
    assert "Sibling-pipeline sweep" in result.reason


def test_all_headings_missing_rejected():
    """SC-7: A completely blank PR body (no headings at all) is rejected."""
    body = ""
    result = check_three_question_template(body)

    assert not result.passed
    assert "Canon-doc impact" in result.reason


def test_heading_typo_rejected():
    """SC-7: A heading with wrong text is treated as missing."""
    body = """\
### 1. Canon doc impacts
### 2. Scaffold backport
- [x] Done.
### 3. Sibling-pipeline sweep
- [x] All siblings checked.
"""
    result = check_three_question_template(body)

    assert not result.passed
    assert "Canon-doc impact" in result.reason


# ---------------------------------------------------------------------------
# SC-8: Bare N/A rejected
# ---------------------------------------------------------------------------


def test_bare_NA_rejected():
    """SC-8: All sections with bare N/A (no justification) are rejected."""
    body = """\
### 1. Canon-doc impact
N/A

### 2. Scaffold backport
N/A

### 3. Sibling-pipeline sweep
N/A
"""
    result = check_three_question_template(body)

    assert not result.passed
    assert "justification" in result.reason.lower() or "N/A" in result.reason


def test_bare_NA_with_dash_rejected():
    """SC-8: `- N/A` (checkbox-style bare N/A) is also rejected."""
    body = """\
### 1. Canon-doc impact
- N/A

### 2. Scaffold backport
- N/A

### 3. Sibling-pipeline sweep
- N/A
"""
    result = check_three_question_template(body)

    assert not result.passed


def test_empty_section_rejected():
    """SC-8 variant: heading present but section body is empty."""
    body = """\
### 1. Canon-doc impact

### 2. Scaffold backport
- [x] Done.

### 3. Sibling-pipeline sweep
- [x] All good.
"""
    result = check_three_question_template(body)

    assert not result.passed
    assert "Canon-doc impact" in result.reason


# ---------------------------------------------------------------------------
# SC-9: Justified N/A accepted
# ---------------------------------------------------------------------------


def test_justified_NA_accepted():
    """SC-9: N/A with a justification sentence passes G6."""
    body = """\
### 1. Canon-doc impact
N/A -- This PR only updates test fixtures, no canon-doc changes needed.

### 2. Scaffold backport
N/A -- Scaffold template v2.3 already includes this pattern.

### 3. Sibling-pipeline sweep
N/A -- Single-pipeline change; no sibling pipelines share this auth module.
"""
    result = check_three_question_template(body)

    assert result.passed, f"Justified N/A should pass but got: {result.reason}"


def test_justified_NA_with_dash_accepted():
    """SC-9: `N/A — <reason>` using em-dash also accepted."""
    body = """\
### 1. Canon-doc impact
- N/A \u2014 No canon impact; config-only change.

### 2. Scaffold backport
- N/A \u2014 Already present in scaffold v2.4.

### 3. Sibling-pipeline sweep
- N/A \u2014 Isolated to walmart-supplier-v2 only.
"""
    result = check_three_question_template(body)

    assert result.passed, f"Justified N/A with em-dash should pass: {result.reason}"


# ---------------------------------------------------------------------------
# Happy path: properly filled sections
# ---------------------------------------------------------------------------


def test_three_filled_sections_accepted():
    """All three sections properly filled with checked boxes passes."""
    body = """\
### 1. Canon-doc impact
- [x] Updated `platform/pipeline-standard.md` section 4.2 to reflect new auth flow.
- [x] PR #89 in gc-data-v2 linked.

### 2. Scaffold backport
- [x] Scaffold template updated in commit abc1234.

### 3. Sibling-pipeline sweep
- [x] walmart-supplier-v2: no impact (different auth module).
- [x] amazon-ads-v2: same auth module \u2014 sibling PR #45 opened.
"""
    result = check_three_question_template(body)

    assert result.passed, f"Properly filled template should pass: {result.reason}"


def test_mixed_filled_and_justified_na_accepted():
    """Mix of filled sections and justified N/A passes."""
    body = """\
### 1. Canon-doc impact
- [x] Updated `platform/observability.md` to add new metric.

### 2. Scaffold backport
N/A -- Observability changes are not scaffolded; they live in platform/ only.

### 3. Sibling-pipeline sweep
- [x] All 3 active v2 pipelines checked; none use this metric yet.
"""
    result = check_three_question_template(body)

    assert result.passed, f"Mixed filled+justified N/A should pass: {result.reason}"


# ---------------------------------------------------------------------------
# Kill switch: MORRIS_V2_GATES_ENABLED=0
# ---------------------------------------------------------------------------

_BARE_NA_BODY = """\
### 1. Canon-doc impact
N/A

### 2. Scaffold backport
N/A

### 3. Sibling-pipeline sweep
N/A
"""


def test_kill_switch_bypasses_bad_body(monkeypatch):
    """When MORRIS_V2_GATES_ENABLED=0, even a bare-N/A body passes."""
    monkeypatch.setenv("MORRIS_V2_GATES_ENABLED", "0")
    result = check_three_question_template(_BARE_NA_BODY)

    assert result.passed
    assert "disabled" in result.reason.lower()
    assert result.missing_headings == []
    assert result.empty_sections == []
    assert result.bare_na_sections == []


def test_kill_switch_false_value_bypasses(monkeypatch):
    """MORRIS_V2_GATES_ENABLED=false (string) also disables the gate."""
    monkeypatch.setenv("MORRIS_V2_GATES_ENABLED", "false")
    result = check_three_question_template(_BARE_NA_BODY)

    assert result.passed


def test_kill_switch_off_value_bypasses(monkeypatch):
    """MORRIS_V2_GATES_ENABLED=off disables the gate."""
    monkeypatch.setenv("MORRIS_V2_GATES_ENABLED", "off")
    result = check_three_question_template(_BARE_NA_BODY)

    assert result.passed


def test_kill_switch_is_v2_repo_returns_false(monkeypatch):
    """When kill switch is active, is_v2_repo returns False for v2 repos."""
    monkeypatch.setenv("MORRIS_V2_GATES_ENABLED", "0")

    assert is_v2_repo("gc-data-v2") is False
    assert is_v2_repo("api-advertising-amazon") is False


def test_kill_switch_enabled_by_default():
    """Gates are on by default (env var absent) — bad body is still rejected."""
    # Do NOT set MORRIS_V2_GATES_ENABLED; rely on the default.
    # If this env var happens to be set in the test environment, skip.
    import os

    if os.environ.get("MORRIS_V2_GATES_ENABLED", "1").lower() in {"0", "false", "off", "no"}:
        pytest.skip("MORRIS_V2_GATES_ENABLED is disabled in this environment")

    result = check_three_question_template(_BARE_NA_BODY)

    assert not result.passed


def test_kill_switch_is_v2_repo_still_works_when_enabled():
    """is_v2_repo returns True normally (gates enabled) for a v2 repo name."""
    import os

    if os.environ.get("MORRIS_V2_GATES_ENABLED", "1").lower() in {"0", "false", "off", "no"}:
        pytest.skip("MORRIS_V2_GATES_ENABLED is disabled in this environment")

    assert is_v2_repo("gc-data-v2") is True
    assert is_v2_repo("api-advertising-amazon") is True
    assert is_v2_repo("some-other-repo") is False
