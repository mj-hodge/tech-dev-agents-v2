"""
STORY-1011: sdlc-framework drift checker.

Compares local .sdlc/ content against a pinned baseline directory
and reports any byte-level drift (modified, added, or removed files).

Usage (from workflow):
    python -m tools.sdlc_drift_check <local_sdlc_path> <pinned_sdlc_path>

Usage (as library):
    from tools.sdlc_drift_check import run, read_pinned_version
    drifts = run(local_path, pinned_path, exclude={".git", "PINNED_VERSION"})
"""
from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import FrozenSet, Iterable, Literal


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Drift:
    """A single drifted file."""
    path: str
    kind: Literal["modified", "added", "removed"]
    local_sha: str = ""
    pinned_sha: str = ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

DEFAULT_EXCLUDE: FrozenSet[str] = frozenset({".git", "PINNED_VERSION", "CLAUDE.md.local"})


def read_pinned_version(pin_file: Path) -> str:
    """Read and validate the pinned version from a file.

    Args:
        pin_file: Path to the .sdlc-pinned-version file.

    Returns:
        The version string (e.g., "v1.0.0").

    Raises:
        FileNotFoundError: If pin_file does not exist.
        ValueError: If pin_file is empty or malformed.
    """
    if not pin_file.exists():
        raise FileNotFoundError(
            f"pinned version file not found: {pin_file}"
        )
    content = pin_file.read_text().strip()
    if not content:
        raise ValueError(
            f"pinned version file is empty: {pin_file}"
        )
    return content


def run(
    local_sdlc_path: Path,
    pinned_sdlc_path: Path,
    *,
    exclude: Iterable[str] = DEFAULT_EXCLUDE,
) -> list[Drift]:
    """Compare local .sdlc/ against pinned baseline and return drifts.

    Args:
        local_sdlc_path: Path to the local .sdlc/ directory.
        pinned_sdlc_path: Path to the pinned sdlc-framework checkout.
        exclude: Set of top-level names (files or dirs) to skip.

    Returns:
        List of Drift objects. Empty list means no drift.
    """
    exclude_set = frozenset(exclude)
    drifts: list[Drift] = []

    local_files = _collect_files(local_sdlc_path, exclude_set)
    pinned_files = _collect_files(pinned_sdlc_path, exclude_set)

    all_paths = sorted(set(local_files) | set(pinned_files))

    for rel_path in all_paths:
        in_local = rel_path in local_files
        in_pinned = rel_path in pinned_files

        if in_local and not in_pinned:
            local_sha = _file_sha(local_sdlc_path / rel_path)
            drifts.append(Drift(
                path=rel_path,
                kind="added",
                local_sha=local_sha,
                pinned_sha="",
            ))
        elif in_pinned and not in_local:
            pinned_sha = _file_sha(pinned_sdlc_path / rel_path)
            drifts.append(Drift(
                path=rel_path,
                kind="removed",
                local_sha="",
                pinned_sha=pinned_sha,
            ))
        else:
            local_sha = _file_sha(local_sdlc_path / rel_path)
            pinned_sha = _file_sha(pinned_sdlc_path / rel_path)
            if local_sha != pinned_sha:
                drifts.append(Drift(
                    path=rel_path,
                    kind="modified",
                    local_sha=local_sha,
                    pinned_sha=pinned_sha,
                ))

    return drifts


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _collect_files(root: Path, exclude: FrozenSet[str]) -> set[str]:
    """Recursively collect relative file paths, skipping excluded top-level entries."""
    files: set[str] = set()
    if not root.exists():
        return files
    for item in root.rglob("*"):
        if not item.is_file():
            continue
        rel = item.relative_to(root)
        parts = rel.parts
        # Skip if top-level component matches any exclusion
        if parts[0] in exclude:
            continue
        # Skip if the filename itself is in the exclude set (for top-level files)
        if len(parts) == 1 and parts[0] in exclude:
            continue
        files.add(str(rel))
    return files


def _file_sha(path: Path) -> str:
    """Return the SHA-256 hex digest of a file's contents."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# CLI entry point (used by the GitHub Actions workflow)
# ---------------------------------------------------------------------------

def main() -> int:
    """CLI: compare local .sdlc/ against pinned baseline.

    Usage:
        python -m tools.sdlc_drift_check <local_sdlc_path> <pinned_sdlc_path>

    Exit codes:
        0 — no drift
        1 — drift detected
        2 — usage error
    """
    if len(sys.argv) != 3:
        print(
            "usage: python -m tools.sdlc_drift_check "
            "<local_sdlc_path> <pinned_sdlc_path>",
            file=sys.stderr,
        )
        return 2

    local_path = Path(sys.argv[1])
    pinned_path = Path(sys.argv[2])

    if not local_path.is_dir():
        print(f"error: local path does not exist: {local_path}", file=sys.stderr)
        return 2
    if not pinned_path.is_dir():
        print(f"error: pinned path does not exist: {pinned_path}", file=sys.stderr)
        return 2

    drifts = run(local_path, pinned_path)

    if not drifts:
        print("sdlc-drift-check: OK — no drift detected")
        return 0

    print(f"ERROR: drift detected — {len(drifts)} file(s) differ from pinned baseline")
    for d in drifts:
        print(f"  {d.kind}: {d.path}")
    print(
        "\nhint: either revert the change or open a PR to sdlc-framework, "
        "then bump .sdlc-pinned-version"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
