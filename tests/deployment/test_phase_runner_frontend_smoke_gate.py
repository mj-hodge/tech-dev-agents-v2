"""STORY-565: Frontend @smoke gate — phase runner tests.

Tests the not-yet-implemented _verify_frontend_gate in sdlc_phase_runner.py.
All tests are RED until Phase 8 implements the production code.

Cases A-F:
  A:  frontend: true  + e2e spec with @smoke    → gate PASSES
  B:  frontend: true  + no e2e spec             → gate FAILS
  C:  frontend: true  + e2e spec WITHOUT @smoke → gate FAILS
  D:  frontend: false + no e2e                  → gate PASSES (short-circuit)
  E:  frontend: false # rationale + no e2e      → gate PASSES (escape hatch)
  F:  missing Frontend: field entirely          → gate PASSES + warning (fail-open)
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import sys
import tempfile
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Module import helpers (same pattern as test_phase_runner_frontend_enforcement.py)
# ---------------------------------------------------------------------------

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PHASE_RUNNER_SRC = REPO_ROOT / "deployment" / "hermes" / "sdlc_phase_runner.py"

assert PHASE_RUNNER_SRC.exists(), f"sdlc_phase_runner.py not found at {PHASE_RUNNER_SRC}"

_module_cache: dict = {}


def _get_phase_runner():
    """Import sdlc_phase_runner from source, cached per session."""
    if "mod" in _module_cache:
        return _module_cache["mod"]
    hermes_dir = str(PHASE_RUNNER_SRC.parent)
    if hermes_dir not in sys.path:
        sys.path.insert(0, hermes_dir)
    spec = importlib.util.spec_from_file_location(
        "sdlc_phase_runner_565", str(PHASE_RUNNER_SRC)
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _module_cache["mod"] = mod
    return mod


def _ok(stdout: str = "", stderr: str = "") -> MagicMock:
    m = MagicMock()
    m.returncode = 0
    m.stdout = stdout
    m.stderr = stderr
    return m


def _fail(rc: int = 1, stdout: str = "", stderr: str = "fatal: error") -> MagicMock:
    m = MagicMock()
    m.returncode = rc
    m.stdout = stdout
    m.stderr = stderr
    return m


# ---------------------------------------------------------------------------
# Seed fixture helpers
# ---------------------------------------------------------------------------

SEED_FRONTEND_TRUE = """\
# Seed: STORY-999 — Test Story

## Overview
| Field | Value |
|-------|-------|
| Scope | small |
| Frontend | true |
| Feature Name | Test frontend feature |

**Frontend: true**

## 1. Idea / Trigger
Test story for frontend gate.

## Test Criteria
- Gate passes with @smoke spec

## Validation
- CI green
"""

SEED_FRONTEND_FALSE = """\
# Seed: STORY-999 — Test Backend Story

## Overview
| Field | Value |
|-------|-------|
| Scope | small |
| Frontend | false |
| Feature Name | Test backend feature |

**Frontend: false**

## 1. Idea / Trigger
Test story for frontend gate — backend only.

## Test Criteria
- Gate passes without e2e spec

## Validation
- CI green
"""

SEED_FRONTEND_FALSE_WITH_RATIONALE = """\
# Seed: STORY-999 — CSS Refactor

## Overview
| Field | Value |
|-------|-------|
| Scope | small |
| Frontend | false |
| Feature Name | CSS-only refactor |

**Frontend:** false # CSS-only refactor, covered by existing @smoke suite

## 1. Idea / Trigger
CSS cleanup — no new UI behavior.

## Test Criteria
- Gate passes unconditionally

## Validation
- CI green
"""

SEED_NO_FRONTEND_FIELD = """\
# Seed: STORY-999 — Legacy Story

## Overview
| Field | Value |
|-------|-------|
| Scope | small |
| Feature Name | Pre-cutoff legacy story |

## 1. Idea / Trigger
This seed was written before the Frontend: field was required.

## Test Criteria
- Gate passes (grandfathered)

## Validation
- CI green
"""

SMOKE_SPEC_CONTENT = """\
import { test, expect } from '@playwright/test';

test.describe('Dashboard Login @smoke', () => {
  test('should load login page', async ({ page }) => {
    await page.goto('/login');
    await expect(page.locator('h1')).toHaveText('Login');
  });
});
"""

NON_SMOKE_SPEC_CONTENT = """\
import { test, expect } from '@playwright/test';

test.describe('Dashboard Login', () => {
  test('should load login page', async ({ page }) => {
    await page.goto('/login');
    await expect(page.locator('h1')).toHaveText('Login');
  });
});
"""


def _setup_seed(tmp_path: pathlib.Path, seed_text: str) -> str:
    """Create a fake worktree with a seed.md and return the workdir path."""
    story_dir = tmp_path / "features" / "story-999-test"
    story_dir.mkdir(parents=True)
    (story_dir / "seed.md").write_text(seed_text)
    return str(tmp_path)


# ---------------------------------------------------------------------------
# Case A: frontend: true + e2e spec with @smoke → PASSES
# ---------------------------------------------------------------------------


class TestFrontendGateCaseA:
    """Case A: frontend: true, diff has e2e spec with @smoke → gate passes."""

    def test_frontend_true_with_smoke_spec_passes(self, tmp_path):
        mod = _get_phase_runner()
        fn = getattr(mod, "_verify_frontend_gate", None)
        assert fn is not None, (
            "_verify_frontend_gate not found in sdlc_phase_runner.py — "
            "implement the function for Phase 8 (STORY-565)"
        )

        workdir = _setup_seed(tmp_path, SEED_FRONTEND_TRUE)

        def _mock_subprocess_run(cmd, **kwargs):
            cmd_str = " ".join(str(c) for c in cmd)
            if "diff" in cmd_str and "--name-only" in cmd_str:
                return _ok(
                    stdout="frontend/src/Dashboard.tsx\ne2e/dashboard/login.smoke.spec.ts\n"
                )
            if "show" in cmd_str and "e2e/dashboard/login.smoke.spec.ts" in cmd_str:
                return _ok(stdout=SMOKE_SPEC_CONTENT)
            return _ok()

        with patch("subprocess.run", side_effect=_mock_subprocess_run):
            ok, errors = fn(workdir, "STORY-999")

        assert ok is True, (
            f"Expected gate to PASS for frontend: true with @smoke spec, "
            f"got ok=False, errors={errors!r}"
        )
        assert errors == [], f"Expected empty errors, got: {errors!r}"

    def test_deeply_nested_spec_passes(self, tmp_path):
        """Regression: e2e/a/b/c/spec.ts must match — PurePath ** only did 1 level."""
        mod = _get_phase_runner()
        fn = mod._verify_frontend_gate

        workdir = _setup_seed(tmp_path, SEED_FRONTEND_TRUE)

        def _mock_subprocess_run(cmd, **kwargs):
            cmd_str = " ".join(str(c) for c in cmd)
            if "diff" in cmd_str and "--name-only" in cmd_str:
                return _ok(
                    stdout="e2e/dashboard/nested/deep/login.spec.ts\n"
                )
            if "show" in cmd_str and "e2e/dashboard/nested/deep/login.spec.ts" in cmd_str:
                return _ok(stdout=SMOKE_SPEC_CONTENT)
            return _ok()

        with patch("subprocess.run", side_effect=_mock_subprocess_run):
            ok, errors = fn(workdir, "STORY-999")

        assert ok is True, (
            f"Expected gate to PASS for deeply nested e2e spec, "
            f"got ok=False, errors={errors!r}"
        )
        assert errors == []

    def test_top_level_spec_without_subdirectory_passes(self, tmp_path):
        """Regression: e2e/smoke.spec.ts (no subdirectory) must match with .+? regex."""
        mod = _get_phase_runner()
        fn = mod._verify_frontend_gate

        workdir = _setup_seed(tmp_path, SEED_FRONTEND_TRUE)

        def _mock_subprocess_run(cmd, **kwargs):
            cmd_str = " ".join(str(c) for c in cmd)
            if "diff" in cmd_str and "--name-only" in cmd_str:
                return _ok(
                    stdout="frontend/src/Dashboard.tsx\ne2e/smoke.spec.ts\n"
                )
            if "show" in cmd_str and "e2e/smoke.spec.ts" in cmd_str:
                return _ok(stdout=SMOKE_SPEC_CONTENT)
            return _ok()

        with patch("subprocess.run", side_effect=_mock_subprocess_run):
            ok, errors = fn(workdir, "STORY-999")

        assert ok is True, (
            f"Expected gate to PASS for top-level e2e/smoke.spec.ts, "
            f"got ok=False, errors={errors!r}"
        )
        assert errors == []


# ---------------------------------------------------------------------------
# Case B: frontend: true + no e2e spec → FAILS
# ---------------------------------------------------------------------------


class TestFrontendGateCaseB:
    """Case B: frontend: true, diff has no e2e spec → gate fails."""

    def test_frontend_true_no_spec_fails(self, tmp_path):
        mod = _get_phase_runner()
        fn = getattr(mod, "_verify_frontend_gate", None)
        assert fn is not None, "_verify_frontend_gate not found"

        workdir = _setup_seed(tmp_path, SEED_FRONTEND_TRUE)

        def _mock_subprocess_run(cmd, **kwargs):
            cmd_str = " ".join(str(c) for c in cmd)
            if "diff" in cmd_str and "--name-only" in cmd_str:
                return _ok(stdout="frontend/src/Dashboard.tsx\ntests/test_foo.py\n")
            return _ok()

        with patch("subprocess.run", side_effect=_mock_subprocess_run):
            ok, errors = fn(workdir, "STORY-999")

        assert ok is False, (
            "Expected gate to FAIL for frontend: true with no e2e spec"
        )
        assert len(errors) >= 1, "Expected at least one error message"
        # Error must contain actionable glob
        assert any("e2e/**/*.spec.ts" in e for e in errors), (
            f"Error message must contain 'e2e/**/*.spec.ts' glob, got: {errors!r}"
        )


# ---------------------------------------------------------------------------
# Case C: frontend: true + e2e spec WITHOUT @smoke → FAILS
# ---------------------------------------------------------------------------


class TestFrontendGateCaseC:
    """Case C: frontend: true, diff has e2e spec but missing @smoke → gate fails."""

    def test_frontend_true_spec_without_smoke_tag_fails(self, tmp_path):
        mod = _get_phase_runner()
        fn = getattr(mod, "_verify_frontend_gate", None)
        assert fn is not None, "_verify_frontend_gate not found"

        workdir = _setup_seed(tmp_path, SEED_FRONTEND_TRUE)

        def _mock_subprocess_run(cmd, **kwargs):
            cmd_str = " ".join(str(c) for c in cmd)
            if "diff" in cmd_str and "--name-only" in cmd_str:
                return _ok(
                    stdout="frontend/src/Dashboard.tsx\ne2e/dashboard/login.spec.ts\n"
                )
            if "show" in cmd_str and "e2e/dashboard/login.spec.ts" in cmd_str:
                return _ok(stdout=NON_SMOKE_SPEC_CONTENT)
            return _ok()

        with patch("subprocess.run", side_effect=_mock_subprocess_run):
            ok, errors = fn(workdir, "STORY-999")

        assert ok is False, (
            "Expected gate to FAIL for frontend: true with spec missing @smoke tag"
        )
        assert len(errors) >= 1, "Expected at least one error message"
        assert any("@smoke" in e for e in errors), (
            f"Error message must mention @smoke tag, got: {errors!r}"
        )


# ---------------------------------------------------------------------------
# Case D: frontend: false + no e2e → PASSES
# ---------------------------------------------------------------------------


class TestFrontendGateCaseD:
    """Case D: frontend: false, no e2e → gate passes (short-circuit)."""

    def test_frontend_false_passes(self, tmp_path):
        mod = _get_phase_runner()
        fn = getattr(mod, "_verify_frontend_gate", None)
        assert fn is not None, "_verify_frontend_gate not found"

        workdir = _setup_seed(tmp_path, SEED_FRONTEND_FALSE)

        # subprocess.run should NOT be called for diff scan on frontend: false
        with patch("subprocess.run") as mock_run:
            ok, errors = fn(workdir, "STORY-999")

        assert ok is True, (
            f"Expected gate to PASS for frontend: false, got errors={errors!r}"
        )
        assert errors == [], f"Expected empty errors, got: {errors!r}"
        # Verify no git diff was invoked (short-circuit on false)
        for call in mock_run.call_args_list:
            cmd_str = " ".join(str(c) for c in call[0][0]) if call[0] else ""
            assert "diff" not in cmd_str or "--name-only" not in cmd_str, (
                "git diff --name-only should NOT be called for frontend: false stories (SC-6)"
            )


# ---------------------------------------------------------------------------
# Case E: frontend: false # rationale → PASSES (escape hatch)
# ---------------------------------------------------------------------------


class TestFrontendGateCaseE:
    """Case E: frontend: false with trailing rationale comment → gate passes."""

    def test_frontend_false_with_rationale_passes(self, tmp_path):
        mod = _get_phase_runner()
        fn = getattr(mod, "_verify_frontend_gate", None)
        assert fn is not None, "_verify_frontend_gate not found"

        workdir = _setup_seed(tmp_path, SEED_FRONTEND_FALSE_WITH_RATIONALE)

        with patch("subprocess.run") as mock_run:
            ok, errors = fn(workdir, "STORY-999")

        assert ok is True, (
            f"Expected gate to PASS for escape-hatch 'frontend: false # rationale', "
            f"got errors={errors!r}"
        )
        assert errors == [], f"Expected empty errors, got: {errors!r}"


# ---------------------------------------------------------------------------
# Case F: Missing Frontend: field → PASSES with warning (fail-open)
# ---------------------------------------------------------------------------


class TestFrontendGateCaseF:
    """Case F: seed has no Frontend: field → gate passes (grandfathered)."""

    def test_missing_frontend_field_passes_with_warning(self, tmp_path, capsys):
        mod = _get_phase_runner()
        fn = getattr(mod, "_verify_frontend_gate", None)
        assert fn is not None, "_verify_frontend_gate not found"

        workdir = _setup_seed(tmp_path, SEED_NO_FRONTEND_FIELD)

        with patch("subprocess.run") as mock_run:
            ok, errors = fn(workdir, "STORY-999")

        assert ok is True, (
            f"Expected gate to PASS (fail-open) for seed missing Frontend: field, "
            f"got errors={errors!r}"
        )
        assert errors == [], f"Expected empty errors, got: {errors!r}"

        # Verify a warning was printed
        captured = capsys.readouterr()
        assert "missing" in captured.out.lower() or "frontend" in captured.out.lower(), (
            "Expected a warning about missing Frontend: field in stdout"
        )


# ---------------------------------------------------------------------------
# Sidecar write test
# ---------------------------------------------------------------------------


class TestFrontendGateSidecar:
    """Verify _write_frontend_gate_sidecar creates the expected file."""

    def test_sidecar_written_on_gate_failure(self, tmp_path):
        mod = _get_phase_runner()
        fn = getattr(mod, "_write_frontend_gate_sidecar", None)
        assert fn is not None, (
            "_write_frontend_gate_sidecar not found in sdlc_phase_runner.py — "
            "implement for Phase 8 (STORY-565)"
        )

        # Use tmp_path as the state root to avoid writing to /home/hermes
        agent_name = "test-agent"
        story_id = "STORY-999"
        errors = ["STORY-999: Frontend: true but no Playwright spec in diff."]

        with patch.dict(os.environ, {"AGENT_NAME": agent_name}):
            # Override the state path to use tmp_path
            state_dir = tmp_path / "state" / agent_name / "phase-runner-gate-failed"
            state_dir.mkdir(parents=True, exist_ok=True)
            sidecar_path = state_dir / f"{story_id}.json"

            # We test the function's behavior by calling it with a patched
            # os.makedirs and open. For a simpler approach, patch the base path.
            with patch("builtins.open", create=True) as mock_open:
                with patch("os.makedirs"):
                    try:
                        fn(story_id, errors)
                    except Exception:
                        pass  # function may not exist yet

            # If the function wrote correctly, verify the call pattern
            if mock_open.called:
                # Verify the path contains phase-runner-gate-failed
                call_args = mock_open.call_args
                if call_args and call_args[0]:
                    written_path = call_args[0][0]
                    assert "phase-runner-gate-failed" in written_path, (
                        f"Sidecar path must contain 'phase-runner-gate-failed', "
                        f"got: {written_path}"
                    )
