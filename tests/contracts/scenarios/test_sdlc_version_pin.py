"""Contract: SDLC framework version pin invariants.

STORY-1011: Verifies that the repo's .sdlc-pinned-version file is present,
valid, and consistent with the .sdlc submodule's VERSION file.

These invariants enforce the pin contract established by STORY-1011:
  - .sdlc-pinned-version must exist at the repo root
  - It must contain a valid semver tag (vX.Y.Z)
  - Its version (stripped of 'v') must match .sdlc/VERSION when the
    submodule is checked out

Contract tests run via contract-critical.yml:
  pytest tests/contracts/ -m contract_critical --tb=short -q --timeout=30
"""
from __future__ import annotations

from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Marker
# ---------------------------------------------------------------------------
pytestmark = pytest.mark.contract_critical

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSdlcVersionPinContract:
    """Contract-critical tests for the STORY-1011 version pin file.

    These tests validate live repository state, not synthetic fixtures.
    They protect the invariant that the repo always declares a valid,
    consistent framework version.
    """

    def test_pinned_version_file_exists(self) -> None:
        """SC-3: .sdlc-pinned-version must exist at the repo root."""
        pin_file = REPO_ROOT / ".sdlc-pinned-version"
        assert pin_file.exists(), (
            ".sdlc-pinned-version missing at repo root — "
            "run STORY-1011 setup to create it"
        )

    def test_pinned_version_is_valid_semver_tag(self) -> None:
        """SC-3: .sdlc-pinned-version must contain a valid vX.Y.Z tag."""
        pin_file = REPO_ROOT / ".sdlc-pinned-version"
        if not pin_file.exists():
            pytest.skip(".sdlc-pinned-version not yet created")

        content = pin_file.read_text().strip()
        assert content.startswith("v"), (
            f".sdlc-pinned-version should start with 'v', got: {content!r}"
        )
        parts = content[1:].split(".")
        assert len(parts) == 3, f"Expected vX.Y.Z, got: {content!r}"
        for part in parts:
            assert part.isdigit(), f"Non-numeric semver part in {content!r}: {part!r}"

    def test_sdlc_version_file_exists(self) -> None:
        """SC-1 proxy: .sdlc/VERSION must be non-empty when submodule is checked out."""
        version_file = REPO_ROOT / ".sdlc" / "VERSION"
        if not version_file.exists():
            pytest.skip(".sdlc/VERSION not populated — submodule not checked out in this env")
        content = version_file.read_text().strip()
        assert content, ".sdlc/VERSION is empty — check submodule checkout"

    def test_versions_consistent(self) -> None:
        """SC-3: Pinned version (stripped of 'v') must match .sdlc/VERSION."""
        pin_file = REPO_ROOT / ".sdlc-pinned-version"
        version_file = REPO_ROOT / ".sdlc" / "VERSION"

        if not pin_file.exists():
            pytest.skip(".sdlc-pinned-version not yet created")
        if not version_file.exists():
            pytest.skip(".sdlc/VERSION not populated — submodule not checked out in this env")

        pinned = pin_file.read_text().strip()
        actual = version_file.read_text().strip()

        pinned_bare = pinned.lstrip("v")
        assert pinned_bare == actual, (
            f"Version mismatch: .sdlc-pinned-version={pinned!r} "
            f"but .sdlc/VERSION={actual!r} — "
            f"bump .sdlc-pinned-version or update the submodule pointer"
        )
