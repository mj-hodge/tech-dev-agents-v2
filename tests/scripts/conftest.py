"""
conftest.py for tests/scripts/

Provides an import fixture for scripts/fleet_review.py, which lives outside
any Python package.  importlib.util is used so that pytest can discover and
run the module without requiring a package __init__.py in scripts/.
"""

import importlib.util
import pathlib
import sys

import pytest

_SCRIPTS_DIR = pathlib.Path(__file__).parent.parent.parent / "scripts"
_MODULE_PATH = _SCRIPTS_DIR / "fleet_review.py"


def _load_fleet_review():
    """Load scripts/fleet_review.py as a module object.

    Raises ImportError with a clear message if the file does not yet exist
    (RED-state: tests run before implementation).
    """
    if not _MODULE_PATH.exists():
        raise ImportError(
            f"scripts/fleet_review.py not found at {_MODULE_PATH}. "
            "Implement the script (Phase 8) to make these tests pass."
        )

    spec = importlib.util.spec_from_file_location("fleet_review", _MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("fleet_review", module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def fleet_review():
    """Session-scoped fixture: returns the fleet_review module."""
    return _load_fleet_review()
