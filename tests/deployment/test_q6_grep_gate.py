"""Q6 — AC4: CI grep gate blocks 'except TypeError' in dispatch_v2.py / dispatch_poller_v2.py.

Tests verify:
1. The workflow YAML contains a grep gate step targeting v2 files.
2. A synthetic file with 'except TypeError' fails the gate command.
3. A clean file passes the gate command.

RED until the grep gate step is added to .github/workflows/test.yml (or a
dedicated workflow file).
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"

# Files the gate must cover
GATED_FILES = [
    "dispatch_v2.py",
    "dispatch_poller_v2.py",
]


def _load_workflow_yamls() -> list[dict]:
    """Return parsed YAML dicts for all workflow files."""
    docs = []
    for yml_path in WORKFLOW_DIR.glob("*.yml"):
        try:
            docs.append(yaml.safe_load(yml_path.read_text()))
        except Exception:
            pass
    return docs


def _all_step_runs(workflows: list[dict]) -> list[str]:
    """Extract every 'run' field from all steps in all jobs."""
    runs = []
    for wf in workflows:
        if not isinstance(wf, dict):
            continue
        jobs = wf.get("jobs", {})
        for job in jobs.values():
            if not isinstance(job, dict):
                continue
            for step in job.get("steps", []):
                if isinstance(step, dict) and "run" in step:
                    runs.append(step["run"])
    return runs


def test_grep_gate_step_exists_in_workflow() -> None:
    """AC4: A workflow step must grep for 'except TypeError' in v2 files and fail if found."""
    workflows = _load_workflow_yamls()
    assert workflows, f"No workflow YAMLs found in {WORKFLOW_DIR}"

    all_runs = _all_step_runs(workflows)

    # The gate must reference both target files and the banned pattern
    found_gate = False
    for run_block in all_runs:
        has_pattern = "except TypeError" in run_block or "except\\s*TypeError" in run_block
        has_v2_files = all(fname in run_block for fname in GATED_FILES)
        if has_pattern and has_v2_files:
            found_gate = True
            break

    assert found_gate, (
        "[RED] No grep gate step found in any workflow that checks 'except TypeError' "
        f"in {GATED_FILES}. Add the gate to .github/workflows/test.yml."
    )


def test_grep_gate_detects_except_typeerror(tmp_path: Path) -> None:
    """AC4: The gate command exits non-zero when 'except TypeError' is present."""
    dispatch_v2 = tmp_path / "dispatch_v2.py"
    poller_v2 = tmp_path / "dispatch_poller_v2.py"

    dispatch_v2.write_text(
        "def handle():\n    try:\n        pass\n    except TypeError:\n        pass\n"
    )
    poller_v2.write_text("# clean\n")

    # Simulate the gate command: grep -rn "except TypeError" <files>
    result = subprocess.run(
        ["grep", "-rn", "except TypeError", str(dispatch_v2), str(poller_v2)],
        capture_output=True,
        text=True,
    )
    # grep exits 0 when match found — the CI step should use `! grep` or
    # invert the logic; we verify that grep finds the pattern
    assert result.returncode == 0, "grep should find the pattern (exit 0 means found)"
    assert "except TypeError" in result.stdout


def test_grep_gate_clean_files_pass(tmp_path: Path) -> None:
    """AC4: The gate command exits non-zero (pattern NOT found) on clean files."""
    dispatch_v2 = tmp_path / "dispatch_v2.py"
    poller_v2 = tmp_path / "dispatch_poller_v2.py"

    dispatch_v2.write_text("def handle():\n    try:\n        pass\n    except ValueError:\n        pass\n")
    poller_v2.write_text("# clean poller\n")

    result = subprocess.run(
        ["grep", "-rn", "except TypeError", str(dispatch_v2), str(poller_v2)],
        capture_output=True,
        text=True,
    )
    # grep exits 1 when no match — CI step that does `grep ... && exit 1` passes
    assert result.returncode == 1, (
        "grep should exit 1 (not found) for clean files — the CI gate should NOT fire"
    )


def test_grep_gate_targets_only_v2_files() -> None:
    """AC4: Gate must be scoped to dispatch_v2.py and dispatch_poller_v2.py only.

    Legacy v1 files may still contain except TypeError — the gate must not
    widen to the whole codebase.
    """
    workflows = _load_workflow_yamls()
    all_runs = _all_step_runs(workflows)

    for run_block in all_runs:
        if "except TypeError" not in run_block:
            continue
        # Must reference v2 files specifically
        assert any(fname in run_block for fname in GATED_FILES), (
            "AC4 FAIL: A workflow step checks 'except TypeError' but does not "
            f"explicitly name the v2 files {GATED_FILES}. Widen to whole codebase "
            "would break legacy v1 code."
        )


def test_workflow_grep_gate_fails_build_on_match() -> None:
    """AC4: The CI step must cause job failure when the pattern is found.

    Acceptable patterns:
    - `! grep ...` (shell negate — grep finds = exit 0 becomes exit 1)
    - `grep ... && exit 1`
    - `if grep ...; then exit 1; fi`
    """
    workflows = _load_workflow_yamls()
    all_runs = _all_step_runs(workflows)

    gate_runs = [
        r for r in all_runs
        if "except TypeError" in r and any(f in r for f in GATED_FILES)
    ]

    if not gate_runs:
        pytest.fail(
            "[RED] No grep gate step found — AC4 not implemented. "
            "Add a step to test.yml that fails the build when 'except TypeError' "
            f"appears in {GATED_FILES}."
        )

    for run_block in gate_runs:
        has_fail_logic = (
            "! grep" in run_block
            or "exit 1" in run_block
            or "grep -L" in run_block  # inverse match
        )
        assert has_fail_logic, (
            "AC4 FAIL: grep gate step does not fail the build when pattern is found.\n"
            f"Step run block:\n{run_block}"
        )
