"""STORY-1019 — Import closure tests: SC-1 through SC-5.

Verifies that:
  SC-1: build-artifact.sh uses Python AST to walk dispatch_v2.py imports.
  SC-2: deploy.sh has a pre-deploy import gate (Step 4c).
  SC-3: rollback.sh has a post-rollback import gate with CRITICAL message.
  SC-4: dispatch_v2.py has a clean import of morris.pre_dispatch (no try/except).
  SC-5: Running build-artifact.sh with a dispatch_v2.py that imports
        tech_dev_agents.morris.pre_dispatch produces an artifact that
        includes tech_dev_agents/morris/pre_dispatch.py.
"""

from __future__ import annotations

import ast
import os
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
BUILD_SCRIPT = REPO_ROOT / "deployment" / "ops-console" / "build-artifact.sh"
DEPLOY_SCRIPT = REPO_ROOT / "deployment" / "ops-console" / "deploy.sh"
ROLLBACK_SCRIPT = REPO_ROOT / "deployment" / "ops-console" / "rollback.sh"
DISPATCH_V2 = REPO_ROOT / "tech_dev_agents" / "ops_console" / "routes" / "dispatch_v2.py"


# ---------------------------------------------------------------------------
# SC-1: build-artifact.sh uses AST scanning
# ---------------------------------------------------------------------------


def test_sc1_build_artifact_has_ast_walker() -> None:
    """SC-1: build-artifact.sh must contain Python AST scanning logic."""
    assert BUILD_SCRIPT.exists(), f"[RED] {BUILD_SCRIPT} not found"
    content = BUILD_SCRIPT.read_text()
    assert "ast.parse" in content or "ast.walk" in content, (
        "SC-1 FAIL: build-artifact.sh does not contain AST scanning logic "
        "(expected 'ast.parse' or 'ast.walk')"
    )
    assert "tech_dev_agents" in content, (
        "SC-1 FAIL: build-artifact.sh does not reference 'tech_dev_agents' "
        "(import graph walk not implemented)"
    )


# ---------------------------------------------------------------------------
# SC-2: deploy.sh has a pre-deploy import gate
# ---------------------------------------------------------------------------


def test_sc2_deploy_has_pre_deploy_import_gate() -> None:
    """SC-2: deploy.sh must contain a pre-deploy import gate before container restart."""
    assert DEPLOY_SCRIPT.exists(), f"[RED] {DEPLOY_SCRIPT} not found"
    content = DEPLOY_SCRIPT.read_text()
    assert "from tech_dev_agents.ops_console.main import create_app" in content, (
        "SC-2 FAIL: deploy.sh does not contain the pre-deploy import gate "
        "('from tech_dev_agents.ops_console.main import create_app' not found)"
    )
    # Should abort with a FAILED message
    assert "FAILED" in content or "import gate FAILED" in content or "Pre-deploy import gate FAILED" in content, (
        "SC-2 FAIL: deploy.sh import gate does not include a FAILED abort message"
    )


# ---------------------------------------------------------------------------
# SC-3: rollback.sh has a post-rollback import gate with CRITICAL message
# ---------------------------------------------------------------------------


def test_sc3_rollback_has_post_rollback_import_gate() -> None:
    """SC-3: rollback.sh must log CRITICAL: post-rollback import failed on gate failure."""
    assert ROLLBACK_SCRIPT.exists(), f"[RED] {ROLLBACK_SCRIPT} not found"
    content = ROLLBACK_SCRIPT.read_text()
    assert "CRITICAL: post-rollback import failed" in content, (
        "SC-3 FAIL: rollback.sh does not contain "
        "'CRITICAL: post-rollback import failed'"
    )


# ---------------------------------------------------------------------------
# SC-4: dispatch_v2.py has a clean import (no try/except wrapper)
# ---------------------------------------------------------------------------


def test_sc4_dispatch_v2_clean_import_no_try_except() -> None:
    """SC-4: dispatch_v2.py must import morris.pre_dispatch without try/except."""
    assert DISPATCH_V2.exists(), f"[RED] {DISPATCH_V2} not found"
    content = DISPATCH_V2.read_text()

    assert "from tech_dev_agents.morris.pre_dispatch import" in content, (
        "SC-4 FAIL: dispatch_v2.py does not import from tech_dev_agents.morris.pre_dispatch"
    )

    # Parse the AST and verify the import is not inside a Try node
    tree = ast.parse(content)
    import_nodes_in_try: list[ast.ImportFrom] = []

    class TryImportVisitor(ast.NodeVisitor):
        def visit_Try(self, node: ast.Try) -> None:  # noqa: N802
            for child in ast.walk(node):
                if isinstance(child, ast.ImportFrom):
                    if child.module and "morris.pre_dispatch" in child.module:
                        import_nodes_in_try.append(child)
            self.generic_visit(node)

    TryImportVisitor().visit(tree)

    assert not import_nodes_in_try, (
        "SC-4 FAIL: the 'tech_dev_agents.morris.pre_dispatch' import is still "
        f"wrapped in a try/except block at line(s): "
        f"{[n.lineno for n in import_nodes_in_try]}"
    )


# ---------------------------------------------------------------------------
# SC-5: Integration — artifact bundles morris package when imported
# ---------------------------------------------------------------------------


def _make_source_tree_with_morris(base: Path) -> None:
    """Create a source tree where dispatch_v2.py imports tech_dev_agents.morris.pre_dispatch."""
    # routes/dispatch_v2.py — imports the morris package
    (base / "routes").mkdir(parents=True)
    (base / "routes" / "dispatch_v2.py").write_text(
        "# dispatch_v2\n"
        "from tech_dev_agents.morris.pre_dispatch import validate_dispatch_seed\n"
    )

    # migrations
    (base / "migrations").mkdir(parents=True)
    (base / "migrations" / "050_dispatch_v2_schema.sql").write_text("-- 050\n")

    # protocol_manifest.json
    (base / "protocol_manifest.json").write_text(
        '{"protocol_version": "2.0", "min_worker_version": "2.0", '
        '"migrations": ["050_dispatch_v2_schema.sql"]}\n'
    )

    # tech_dev_agents/morris package — must be bundled by build-artifact.sh
    morris_dir = base / "tech_dev_agents" / "morris"
    morris_dir.mkdir(parents=True)
    (morris_dir / "__init__.py").write_text("")
    (morris_dir / "pre_dispatch.py").write_text(
        '"""pre_dispatch stub for artifact bundling test."""\n'
        "def validate_dispatch_seed(seed):\n"
        "    return []\n"
    )
    # root __init__.py
    (base / "tech_dev_agents" / "__init__.py").write_text("")


@pytest.mark.skipif(
    not BUILD_SCRIPT.exists(),
    reason="build-artifact.sh not yet implemented (RED state)",
)
def test_sc5_artifact_bundles_morris_package(tmp_path: Path) -> None:
    """SC-5: build-artifact.sh must include tech_dev_agents/morris/ when dispatch_v2.py imports it."""
    src = tmp_path / "src"
    out = tmp_path / "out"
    src.mkdir()
    out.mkdir()

    _make_source_tree_with_morris(src)

    result = subprocess.run(
        ["bash", str(BUILD_SCRIPT)],
        cwd=str(src),
        env={**os.environ, "SOURCE_DIR": str(src), "OUTPUT_DIR": str(out)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"SC-5 FAIL: build-artifact.sh exited {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )

    tarballs = list(out.glob("*.tar.gz"))
    assert tarballs, f"SC-5 FAIL: No tarball produced in {out}"

    extract_dir = tmp_path / "extracted"
    extract_dir.mkdir()
    with tarfile.open(tarballs[0], "r:gz") as tf:
        tf.extractall(extract_dir)

    pre_dispatch_path = extract_dir / "tech_dev_agents" / "morris" / "pre_dispatch.py"
    assert pre_dispatch_path.exists(), (
        "SC-5 FAIL: tech_dev_agents/morris/pre_dispatch.py not found in artifact.\n"
        f"Artifact contents: {sorted(str(p.relative_to(extract_dir)) for p in extract_dir.rglob('*') if p.is_file())}"
    )
