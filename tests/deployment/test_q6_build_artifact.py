"""Q6 — AC1: build-artifact.sh produces a deterministic tarball.

Two runs with identical source inputs must produce tarballs with the same
SHA256. RED until deployment/ops-console/build-artifact.sh exists.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
BUILD_SCRIPT = REPO_ROOT / "deployment" / "ops-console" / "build-artifact.sh"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _make_source_tree(base: Path) -> None:
    """Create a minimal source tree that mirrors what build-artifact.sh packages."""
    (base / "routes").mkdir(parents=True)
    (base / "routes" / "dispatch_v2.py").write_text("# dispatch_v2\n")
    (base / "migrations").mkdir(parents=True)
    (base / "migrations" / "050_dispatch_v2_schema.sql").write_text("-- 050\n")
    (base / "migrations" / "051_dispatch_v2_dependencies.sql").write_text("-- 051\n")
    manifest_src = REPO_ROOT / "tech_dev_agents" / "ops_console" / "protocol_manifest.json"
    if manifest_src.exists():
        shutil.copy(manifest_src, base / "protocol_manifest.json")
    else:
        # Fallback for RED-state run (manifest not yet created)
        (base / "protocol_manifest.json").write_text(
            '{"protocol_version": "2.0", "min_worker_version": "2.0", '
            '"migrations": ["050_dispatch_v2_schema.sql", "051_dispatch_v2_dependencies.sql"]}\n'
        )


def test_build_script_exists() -> None:
    """RED: build-artifact.sh must exist before any other AC1 test can pass."""
    assert BUILD_SCRIPT.exists(), (
        f"[RED] {BUILD_SCRIPT} not found — implement deployment/ops-console/build-artifact.sh"
    )


@pytest.mark.skipif(
    not BUILD_SCRIPT.exists(),
    reason="build-artifact.sh not yet implemented (RED state)",
)
def test_build_artifact_deterministic(tmp_path: Path) -> None:
    """AC1: Two runs with identical inputs produce tarballs with the same SHA256."""
    src1 = tmp_path / "src1"
    src2 = tmp_path / "src2"
    out1 = tmp_path / "out1"
    out2 = tmp_path / "out2"
    src1.mkdir()
    src2.mkdir()
    out1.mkdir()
    out2.mkdir()

    _make_source_tree(src1)
    _make_source_tree(src2)

    def run_build(src: Path, out: Path) -> Path:
        result = subprocess.run(
            ["bash", str(BUILD_SCRIPT)],
            cwd=str(src),
            env={**os.environ, "OUTPUT_DIR": str(out)},
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, (
            f"build-artifact.sh exited {result.returncode}\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        tarballs = list(out.glob("*.tar.gz"))
        assert tarballs, f"No tarball produced in {out}"
        return tarballs[0]

    tarball1 = run_build(src1, out1)
    tarball2 = run_build(src2, out2)

    sha1 = _sha256(tarball1)
    sha2 = _sha256(tarball2)
    assert sha1 == sha2, (
        f"AC1 FAIL: tarballs differ across identical runs.\n  run1 sha={sha1}\n  run2 sha={sha2}"
    )


@pytest.mark.skipif(
    not BUILD_SCRIPT.exists(),
    reason="build-artifact.sh not yet implemented (RED state)",
)
def test_build_artifact_embeds_checksum(tmp_path: Path) -> None:
    """AC1-extra: tarball must include a checksum file so deploy.sh can verify."""
    src = tmp_path / "src"
    out = tmp_path / "out"
    src.mkdir()
    out.mkdir()
    _make_source_tree(src)

    result = subprocess.run(
        ["bash", str(BUILD_SCRIPT)],
        cwd=str(src),
        env={**os.environ, "OUTPUT_DIR": str(out)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0

    tarballs = list(out.glob("*.tar.gz"))
    assert tarballs

    # Extract and verify a CHECKSUM or .sha256 file is present
    extract_dir = tmp_path / "extracted"
    extract_dir.mkdir()
    subprocess.run(
        ["tar", "-xzf", str(tarballs[0]), "-C", str(extract_dir)],
        check=True,
    )
    checksum_files = list(extract_dir.rglob("*.sha256")) + list(extract_dir.rglob("CHECKSUM"))
    assert checksum_files, (
        "AC1 FAIL: tarball does not contain a checksum file (*.sha256 or CHECKSUM)"
    )
