"""
STORY-885: review-context-bundler smoke tests.

Tests exercise generate-bundle.py functions with mocked filesystem paths.
Groups A–D from test-design.md.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Import generate-bundle.py by filepath (it lives in the .sdlc submodule)
# ---------------------------------------------------------------------------

BUNDLE_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / ".sdlc"
    / "skills"
    / "review-context-bundler"
    / "generate-bundle.py"
)


def _load_bundle_module():
    """Dynamically import generate-bundle.py as a module."""
    if not BUNDLE_SCRIPT.exists():
        pytest.skip(f"generate-bundle.py not found at {BUNDLE_SCRIPT}")
    spec = importlib.util.spec_from_file_location("generate_bundle", BUNDLE_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def bundle_mod():
    return _load_bundle_module()


@pytest.fixture()
def wiki_env(tmp_path, bundle_mod, monkeypatch):
    """Set up a minimal wiki environment for build_bundle()."""
    # Create minimal wiki structure
    wiki_root = tmp_path / "wiki"
    systems_dir = wiki_root / "systems"
    systems_dir.mkdir(parents=True)
    processes_dir = wiki_root / "processes"
    processes_dir.mkdir(parents=True)

    # Add a sample wiki page
    (systems_dir / "sample-system.md").write_text(
        "# Sample System\n\nA sample system for testing.\n\n"
        "## Architecture\nMicroservice-based.\n\n"
        "## Deployment\nDeployed via CI/CD.\n",
        encoding="utf-8",
    )

    (processes_dir / "sample-process.md").write_text(
        "# Sample Process\n\nA standard operating procedure.\n\n"
        "## Steps\n1. Step one.\n2. Step two.\n",
        encoding="utf-8",
    )

    # Create empty state dirs
    state_ws = tmp_path / "state_ws" / "morris"
    state_ws.mkdir(parents=True)
    state_local = tmp_path / "state_local" / "morris"
    state_local.mkdir(parents=True)

    # Create dummy AGENTS.md and review-prs SKILL.md
    agents_md = tmp_path / "AGENTS.md"
    agents_md.write_text("# AGENTS\n\n## Models\n\nOpus for complex.\n", encoding="utf-8")

    review_prs_skill = tmp_path / "review-prs-SKILL.md"
    review_prs_skill.write_text("# review-prs\n\nStub skill.\n", encoding="utf-8")

    repo_base = tmp_path / "repos"
    repo_base.mkdir()

    # Monkeypatch module-level constants
    monkeypatch.setattr(bundle_mod, "WIKI_ROOT", wiki_root)
    monkeypatch.setattr(bundle_mod, "AGENTS_MD", agents_md)
    monkeypatch.setattr(bundle_mod, "REVIEW_PRS_SKILL", review_prs_skill)
    monkeypatch.setattr(bundle_mod, "STATE_DIR_WORKSPACE", state_ws)
    monkeypatch.setattr(bundle_mod, "STATE_DIR_LOCAL", state_local)
    monkeypatch.setattr(bundle_mod, "REPO_BASE", repo_base)
    monkeypatch.setattr(bundle_mod, "KNOWLEDGE_GAPS_PATHS", [
        state_ws / "knowledge-gaps.jsonl",
        state_local / "knowledge-gaps.jsonl",
    ])

    return bundle_mod


# ---- Group A: Bundle Generation Smoke ----


class TestGroupA:
    """A-01: build_bundle() runs without error and returns non-empty string."""

    def test_build_bundle_runs_without_error(self, wiki_env):
        result = wiki_env.build_bundle()
        assert isinstance(result, str)
        assert len(result) > 0


# ---- Group B: Header Line Format ----


HEADER_RE = re.compile(
    r"^<!-- review-context bundle: "
    r"built=\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z / "
    r"wiki_pages=\d+ / "
    r"runbooks=\d+ / "
    r"incidents=\d+ / "
    r"gaps=\d+ -->$"
)


class TestGroupB:
    """B-01: Header line matches expected format."""

    def test_header_line_format(self, wiki_env):
        result = wiki_env.build_bundle()
        first_line = result.split("\n")[0]
        assert HEADER_RE.match(first_line), f"Header mismatch: {first_line!r}"


# ---- Group C: Budget Enforcement ----


class TestGroupC:
    """C-01: Output is under 120K chars."""

    def test_output_under_120k_chars(self, wiki_env):
        result = wiki_env.build_bundle()
        assert len(result) <= 120_000, f"Bundle is {len(result)} chars (budget: 120,000)"


# ---- Group D: Idempotency ----


def _strip_timestamp(text: str) -> str:
    """Remove the built=...Z timestamp so two runs can be compared."""
    return re.sub(r"built=\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", "built=STRIPPED", text)


class TestGroupD:
    """D-01: Two consecutive runs produce identical output (modulo timestamp)."""

    def test_idempotent_across_two_runs(self, wiki_env):
        run1 = wiki_env.build_bundle()
        run2 = wiki_env.build_bundle()
        assert _strip_timestamp(run1) == _strip_timestamp(run2)
