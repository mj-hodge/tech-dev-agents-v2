"""
STORY-1011: Unit tests for the sdlc-framework drift checker.

Tests validate four core scenarios:
  1. Clean state (no drift) passes
  2. Tampered/modified skill file is detected as drift
  3. Excluded paths (.git/, PINNED_VERSION, CLAUDE.md.local) are ignored
  4. Unknown/missing pinned version produces a clear error

These tests run against synthetic directory trees in tmp_path —
they do NOT require the real sdlc-framework repo or network access.
"""
from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers — build synthetic sdlc trees
# ---------------------------------------------------------------------------

def _make_tree(root: Path, files: dict[str, str]) -> None:
    """Create a directory tree from a dict of {relative_path: content}."""
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)


def _baseline_files() -> dict[str, str]:
    """Canonical set of framework files for the pinned version."""
    return {
        "VERSION": "1.0.0\n",
        "AGENTS.md": "# Agents\n",
        "software-development-guidance.md": "# Guidance\n",
        "skills/spec/SKILL.md": "name: spec\n",
        "skills/next/SKILL.md": "name: next\n",
        "agents/phase-1-seed/AGENT.md": "# Phase 1\n",
        "templates/config.yaml": "version: 1\n",
    }


# ---------------------------------------------------------------------------
# Test 1: Clean state passes
# ---------------------------------------------------------------------------

class TestCleanState:
    """When local .sdlc/ matches pinned baseline byte-for-byte, no drift."""

    def test_clean_state_passes(self, tmp_path: Path) -> None:
        local_sdlc = tmp_path / "local_sdlc"
        pinned_sdlc = tmp_path / "pinned_sdlc"
        _make_tree(local_sdlc, _baseline_files())
        _make_tree(pinned_sdlc, _baseline_files())

        from tools.sdlc_drift_check import run as drift_check

        drifts = drift_check(
            local_sdlc_path=local_sdlc,
            pinned_sdlc_path=pinned_sdlc,
            exclude=frozenset({".git", "PINNED_VERSION", "CLAUDE.md.local"}),
        )
        assert drifts == [], f"Expected no drift but got: {drifts}"


# ---------------------------------------------------------------------------
# Test 2: Tampered skill is detected
# ---------------------------------------------------------------------------

class TestTamperedSkillDetected:
    """Modifying a skill file in .sdlc/ is reported as drift."""

    def test_modified_skill_detected(self, tmp_path: Path) -> None:
        local_sdlc = tmp_path / "local_sdlc"
        pinned_sdlc = tmp_path / "pinned_sdlc"

        _make_tree(pinned_sdlc, _baseline_files())

        # Local copy with a tampered skill
        local_files = _baseline_files()
        local_files["skills/spec/SKILL.md"] = "name: tampered\n"
        _make_tree(local_sdlc, local_files)

        from tools.sdlc_drift_check import run as drift_check

        drifts = drift_check(
            local_sdlc_path=local_sdlc,
            pinned_sdlc_path=pinned_sdlc,
            exclude=frozenset({".git", "PINNED_VERSION", "CLAUDE.md.local"}),
        )
        assert len(drifts) == 1
        drift = drifts[0]
        assert drift.path == "skills/spec/SKILL.md"
        assert drift.kind == "modified"

    def test_added_file_detected(self, tmp_path: Path) -> None:
        """A file present locally but not in the pinned version = drift."""
        local_sdlc = tmp_path / "local_sdlc"
        pinned_sdlc = tmp_path / "pinned_sdlc"

        _make_tree(pinned_sdlc, _baseline_files())

        local_files = _baseline_files()
        local_files["skills/rogue/SKILL.md"] = "name: rogue\n"
        _make_tree(local_sdlc, local_files)

        from tools.sdlc_drift_check import run as drift_check

        drifts = drift_check(
            local_sdlc_path=local_sdlc,
            pinned_sdlc_path=pinned_sdlc,
            exclude=frozenset({".git", "PINNED_VERSION", "CLAUDE.md.local"}),
        )
        assert any(d.path == "skills/rogue/SKILL.md" and d.kind == "added" for d in drifts)

    def test_removed_file_detected(self, tmp_path: Path) -> None:
        """A file in the pinned version but missing locally = drift."""
        local_sdlc = tmp_path / "local_sdlc"
        pinned_sdlc = tmp_path / "pinned_sdlc"

        _make_tree(pinned_sdlc, _baseline_files())

        local_files = _baseline_files()
        del local_files["skills/next/SKILL.md"]
        _make_tree(local_sdlc, local_files)

        from tools.sdlc_drift_check import run as drift_check

        drifts = drift_check(
            local_sdlc_path=local_sdlc,
            pinned_sdlc_path=pinned_sdlc,
            exclude=frozenset({".git", "PINNED_VERSION", "CLAUDE.md.local"}),
        )
        assert any(d.path == "skills/next/SKILL.md" and d.kind == "removed" for d in drifts)


# ---------------------------------------------------------------------------
# Test 3: Excluded paths are ignored
# ---------------------------------------------------------------------------

class TestExcludedPathsIgnored:
    """Files matching exclusion patterns are not treated as drift."""

    def test_git_dir_excluded(self, tmp_path: Path) -> None:
        local_sdlc = tmp_path / "local_sdlc"
        pinned_sdlc = tmp_path / "pinned_sdlc"

        _make_tree(pinned_sdlc, _baseline_files())

        local_files = _baseline_files()
        local_files[".git/config"] = "rogue git config\n"
        _make_tree(local_sdlc, local_files)

        from tools.sdlc_drift_check import run as drift_check

        drifts = drift_check(
            local_sdlc_path=local_sdlc,
            pinned_sdlc_path=pinned_sdlc,
            exclude=frozenset({".git", "PINNED_VERSION", "CLAUDE.md.local"}),
        )
        assert drifts == []

    def test_pinned_version_excluded(self, tmp_path: Path) -> None:
        local_sdlc = tmp_path / "local_sdlc"
        pinned_sdlc = tmp_path / "pinned_sdlc"

        _make_tree(pinned_sdlc, _baseline_files())

        local_files = _baseline_files()
        local_files["PINNED_VERSION"] = "v1.0.0\n"
        _make_tree(local_sdlc, local_files)

        from tools.sdlc_drift_check import run as drift_check

        drifts = drift_check(
            local_sdlc_path=local_sdlc,
            pinned_sdlc_path=pinned_sdlc,
            exclude=frozenset({".git", "PINNED_VERSION", "CLAUDE.md.local"}),
        )
        assert drifts == []

    def test_claude_md_local_excluded(self, tmp_path: Path) -> None:
        local_sdlc = tmp_path / "local_sdlc"
        pinned_sdlc = tmp_path / "pinned_sdlc"

        _make_tree(pinned_sdlc, _baseline_files())

        local_files = _baseline_files()
        local_files["CLAUDE.md.local"] = "per-project override\n"
        _make_tree(local_sdlc, local_files)

        from tools.sdlc_drift_check import run as drift_check

        drifts = drift_check(
            local_sdlc_path=local_sdlc,
            pinned_sdlc_path=pinned_sdlc,
            exclude=frozenset({".git", "PINNED_VERSION", "CLAUDE.md.local"}),
        )
        assert drifts == []


# ---------------------------------------------------------------------------
# Test 4: Unknown pinned version produces clear error
# ---------------------------------------------------------------------------

class TestUnknownPinnedVersion:
    """read_pinned_version raises clear error for malformed input."""

    def test_missing_pinned_version_file(self, tmp_path: Path) -> None:
        """If .sdlc-pinned-version doesn't exist, raise FileNotFoundError."""
        from tools.sdlc_drift_check import read_pinned_version

        with pytest.raises(FileNotFoundError):
            read_pinned_version(tmp_path / ".sdlc-pinned-version")

    def test_empty_pinned_version_file(self, tmp_path: Path) -> None:
        """If the pin file is empty, raise ValueError with clear message."""
        pin_file = tmp_path / ".sdlc-pinned-version"
        pin_file.write_text("")

        from tools.sdlc_drift_check import read_pinned_version

        with pytest.raises(ValueError, match="empty"):
            read_pinned_version(pin_file)

    def test_valid_pinned_version(self, tmp_path: Path) -> None:
        pin_file = tmp_path / ".sdlc-pinned-version"
        pin_file.write_text("v1.0.0\n")

        from tools.sdlc_drift_check import read_pinned_version

        assert read_pinned_version(pin_file) == "v1.0.0"


# ---------------------------------------------------------------------------
# Contract-critical tests for repo-level invariants
# ---------------------------------------------------------------------------

@pytest.mark.contract_critical
class TestSdlcVersionPinContract:
    """Contract tests that validate the repo's .sdlc-pinned-version
    is consistent with .sdlc/VERSION.

    These run in CI via contract-critical.yml.
    """

    REPO_ROOT = Path(__file__).resolve().parent.parent.parent

    def test_pinned_version_file_exists(self) -> None:
        pin_file = self.REPO_ROOT / ".sdlc-pinned-version"
        assert pin_file.exists(), (
            ".sdlc-pinned-version missing at repo root — "
            "run STORY-1011 setup to create it"
        )

    def test_pinned_version_is_valid_semver_tag(self) -> None:
        pin_file = self.REPO_ROOT / ".sdlc-pinned-version"
        if not pin_file.exists():
            pytest.skip(".sdlc-pinned-version not yet created")
        content = pin_file.read_text().strip()
        assert content.startswith("v"), (
            f".sdlc-pinned-version should start with 'v', got: {content!r}"
        )
        # Basic semver check: vX.Y.Z
        parts = content[1:].split(".")
        assert len(parts) == 3, f"Expected vX.Y.Z, got: {content!r}"
        for part in parts:
            assert part.isdigit(), f"Non-numeric semver part in {content!r}"

    def test_sdlc_version_file_exists(self) -> None:
        version_file = self.REPO_ROOT / ".sdlc" / "VERSION"
        if not version_file.exists():
            pytest.skip(".sdlc/VERSION not populated (submodule not checked out)")
        assert version_file.read_text().strip(), ".sdlc/VERSION is empty"

    def test_versions_consistent(self) -> None:
        """The pinned version (minus 'v' prefix) must match .sdlc/VERSION."""
        pin_file = self.REPO_ROOT / ".sdlc-pinned-version"
        version_file = self.REPO_ROOT / ".sdlc" / "VERSION"

        if not pin_file.exists():
            pytest.skip(".sdlc-pinned-version not yet created")
        if not version_file.exists():
            pytest.skip(".sdlc/VERSION not populated (submodule not checked out)")

        pinned = pin_file.read_text().strip()
        actual = version_file.read_text().strip()

        # Strip 'v' prefix from pinned for comparison
        pinned_bare = pinned.lstrip("v")
        assert pinned_bare == actual, (
            f"Version mismatch: .sdlc-pinned-version={pinned!r} "
            f"but .sdlc/VERSION={actual!r}"
        )
