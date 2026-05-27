"""Q6 — AC3: deploy.sh aborts when artifact checksum does not match source.

Modifying routes/dispatch_v2.py after building the artifact must cause
deploy.sh to exit non-zero.

RED until deployment/ops-console/build-artifact.sh and a checksum-verify
block in deploy.sh are implemented.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
BUILD_SCRIPT = REPO_ROOT / "deployment" / "ops-console" / "build-artifact.sh"
DEPLOY_SCRIPT = REPO_ROOT / "deployment" / "ops-console" / "deploy.sh"


def _make_source_tree(base: Path) -> None:
    (base / "routes").mkdir(parents=True)
    (base / "routes" / "dispatch_v2.py").write_text("# original dispatch_v2\n")
    (base / "migrations").mkdir(parents=True)
    (base / "migrations" / "050_dispatch_v2_schema.sql").write_text("-- 050\n")
    (base / "migrations" / "051_dispatch_v2_dependencies.sql").write_text("-- 051\n")
    manifest_src = REPO_ROOT / "tech_dev_agents" / "ops_console" / "protocol_manifest.json"
    if manifest_src.exists():
        shutil.copy(manifest_src, base / "protocol_manifest.json")
    else:
        (base / "protocol_manifest.json").write_text(
            '{"protocol_version": "2.0", "min_worker_version": "2.0", '
            '"migrations": ["050_dispatch_v2_schema.sql", "051_dispatch_v2_dependencies.sql"]}\n'
        )


def test_deploy_script_exists() -> None:
    """AC3: deploy.sh must exist (it does — this tests that the checksum block was added)."""
    assert DEPLOY_SCRIPT.exists(), f"[RED] deploy.sh not found at {DEPLOY_SCRIPT}"


def test_build_script_exists() -> None:
    """Prerequisite for AC3 tests."""
    assert BUILD_SCRIPT.exists(), (
        f"[RED] build-artifact.sh not found — implement deployment/ops-console/build-artifact.sh"
    )


@pytest.mark.skipif(
    not BUILD_SCRIPT.exists(),
    reason="build-artifact.sh not yet implemented (RED state)",
)
def test_deploy_aborts_on_checksum_mismatch(tmp_path: Path) -> None:
    """AC3: deploy.sh must abort (exit non-zero) when artifact SHA256 does not match source.

    Process:
    1. Build artifact from source tree A.
    2. Modify dispatch_v2.py (simulate partial deploy).
    3. Call deploy.sh with the original artifact against the modified source.
    4. Expect non-zero exit.
    """
    src = tmp_path / "src"
    out = tmp_path / "out"
    src.mkdir()
    out.mkdir()
    _make_source_tree(src)

    # Step 1: Build artifact
    result = subprocess.run(
        ["bash", str(BUILD_SCRIPT)],
        cwd=str(src),
        env={**os.environ, "OUTPUT_DIR": str(out)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"build failed: {result.stderr}"

    tarballs = list(out.glob("*.tar.gz"))
    assert tarballs, "No tarball produced"
    artifact = tarballs[0]

    # Step 2: Modify dispatch_v2.py (simulate tampering / partial deploy)
    (src / "routes" / "dispatch_v2.py").write_text("# TAMPERED dispatch_v2\n")

    # Step 3: Run deploy.sh in verify-only / dry-run mode against the artifact
    # The script must read the embedded checksum and compare to current source.
    verify_result = subprocess.run(
        ["bash", str(DEPLOY_SCRIPT), "--verify-only", str(artifact), str(src)],
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "VERIFY_ONLY": "1", "ARTIFACT": str(artifact), "SOURCE_DIR": str(src)},
    )

    assert verify_result.returncode != 0, (
        "AC3 FAIL: deploy.sh should abort on checksum mismatch but exited 0.\n"
        f"stdout: {verify_result.stdout}\nstderr: {verify_result.stderr}"
    )
    # The output must mention checksum / hash mismatch
    combined = verify_result.stdout + verify_result.stderr
    assert any(
        kw in combined.lower()
        for kw in ("checksum", "sha256", "mismatch", "hash", "abort", "differ")
    ), f"AC3 FAIL: deploy.sh exit was non-zero but output did not mention mismatch:\n{combined}"


@pytest.mark.skipif(
    not BUILD_SCRIPT.exists(),
    reason="build-artifact.sh not yet implemented (RED state)",
)
def test_deploy_accepts_valid_artifact(tmp_path: Path) -> None:
    """AC3-extra: deploy.sh --verify-only passes when artifact matches source."""
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
    artifact = tarballs[0]

    verify_result = subprocess.run(
        ["bash", str(DEPLOY_SCRIPT), "--verify-only", str(artifact), str(src)],
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "VERIFY_ONLY": "1", "ARTIFACT": str(artifact), "SOURCE_DIR": str(src)},
    )
    assert verify_result.returncode == 0, (
        f"AC3 FAIL: deploy --verify-only should pass on unmodified source.\n"
        f"stdout: {verify_result.stdout}\nstderr: {verify_result.stderr}"
    )
