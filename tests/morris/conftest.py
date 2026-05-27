"""
conftest.py for tests/morris/

Loads deployment/morris/scripts/*.py modules via importlib so pytest can
discover them without requiring a package __init__.py in the scripts dir.

When a script does not exist yet (RED state), the fixture raises ImportError
-- tests using that fixture will ERROR until Phase 8 implements the script.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

# Path to deployment/morris/scripts/ from the project root
_SCRIPTS_DIR = (
    pathlib.Path(__file__).resolve().parent.parent.parent
    / "deployment"
    / "morris"
    / "scripts"
)


def _load_script(name: str):
    """Load a single script module by name.

    Raises ImportError with a clear message when the file is not yet
    implemented (expected during RED-state Phase 7 execution).
    """
    module_path = _SCRIPTS_DIR / f"{name}.py"
    if not module_path.exists():
        raise ImportError(
            f"{name}.py not found at {module_path}.\n"
            "Implement this script in Phase 8 to turn these tests GREEN."
        )
    spec = importlib.util.spec_from_file_location(name, module_path)
    module = importlib.util.module_from_spec(spec)
    # Register under both bare name and dotted name for cross-fixture imports
    sys.modules.setdefault(name, module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def codex_review_post():
    """Session-scoped fixture: returns the codex_review_post module."""
    return _load_script("codex_review_post")


@pytest.fixture(scope="session")
def codex_debt_autoclose():
    """Session-scoped fixture: returns the codex_debt_autoclose module."""
    return _load_script("codex_debt_autoclose")
