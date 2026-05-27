"""Pure-Python byte-diff checker for `*-v2` PRs vs. `gc-data-v2/pipeline-template`.

This module is deliberately I/O free at the boundary: every external call
goes through an injectable fetcher (`scaffold_fetcher`, `pr_file_fetcher`).
Production callers use the default fetchers (urllib + gh CLI); tests pass
in-memory stubs.

Contracts (DO NOT CHANGE without coordinating with STORY-1007):
  - STATUS_CHECK_NAME = "morris/canon-check"
  - The set of watched files mirrors gc-data-v2's canon-drift-check.yml.
"""

from __future__ import annotations

import difflib
import fnmatch
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal, Optional

# ---------------------------------------------------------------------------
# Public type aliases & constants
# ---------------------------------------------------------------------------

DriftType = Literal["MODIFIED", "MISSING", "ASSERTION_FAILED"]
CheckStatus = Literal["SUCCESS", "FAILURE", "PENDING", "NA"]

V2_REPO_PATTERN = "*-v2"
EXTRA_WATCHED_REPOS = frozenset({"api-advertising-amazon"})

# Contract: STORY-1007 G7 greps for this exact string. Do not rename.
STATUS_CHECK_NAME = "morris/canon-check"

# Maximum lines of unified-diff to inline into a PR comment.
DIFF_MAX_LINES = 40

# Default location of the optional pin file owned by STORY-1013.
DEFAULT_PIN_FILE = "/home/hermes/state/morris/canon-pins.yaml"

# Raw GitHub URL template for scaffold files.
SCAFFOLD_RAW_TEMPLATE = (
    "https://raw.githubusercontent.com/hpi-gorillacommerce/gc-data-v2/"
    "{ref}/pipeline-template/{path}"
)


@dataclass(frozen=True)
class WatchedFile:
    """One canonical file we watch on every v2 PR.

    `mode == "byte_match"`: PR bytes must equal scaffold bytes.
    `mode == "content_assert"`: PR bytes must *contain* `assert_substring`.
    """

    path: str
    mode: Literal["byte_match", "content_assert"]
    assert_substring: Optional[str] = None
    # If False, a missing PR file is allowed (e.g. stub repos without deploy yet).
    required: bool = True


WATCHED_FILES: tuple[WatchedFile, ...] = (
    WatchedFile(".github/pull_request_template.md", "byte_match"),
    WatchedFile(".github/workflows/canon-drift-check.yml", "byte_match"),
    WatchedFile(".github/workflows/pr-canon-readback.yml", "byte_match"),
    WatchedFile(
        ".github/workflows/deploy-function-app.yaml",
        "content_assert",
        assert_substring="--python-platform x86_64-manylinux_2_17",
        required=False,
    ),
)


@dataclass(frozen=True)
class DriftedFile:
    path: str
    drift_type: DriftType
    scaffold_url: str
    diff_snippet: Optional[str]
    remediation: str


@dataclass(frozen=True)
class CanonCheckResult:
    repo: str
    pr_number: int
    head_sha: Optional[str]
    scaffold_ref: str
    drifted: tuple[DriftedFile, ...]
    status: CheckStatus
    skipped_reason: Optional[str] = None


# ---------------------------------------------------------------------------
# Repo detection
# ---------------------------------------------------------------------------


def is_v2_repo(repo_full_name: str) -> bool:
    """True if Morris should run canon-check on PRs in this repo.

    Matches `*-v2` (suffix only) and any name in `EXTRA_WATCHED_REPOS`.
    Substring matches in the middle of a name (e.g. `some-v2-mid-name`) do
    NOT count — we use suffix matching via `fnmatch` end-anchor.
    """
    name = repo_full_name.rsplit("/", 1)[-1]
    if name in EXTRA_WATCHED_REPOS:
        return True
    # fnmatch's `*-v2` already implies "anything then -v2 end-of-string"
    return fnmatch.fnmatchcase(name, V2_REPO_PATTERN)


# ---------------------------------------------------------------------------
# Pin file
# ---------------------------------------------------------------------------


def load_pinned_ref(pin_path: Optional[str] = None) -> str:
    """Return the gc-data-v2 ref to compare against.

    Precedence: env var `MORRIS_CANON_PIN` > `gc_data_v2:` in pin file > `"main"`.
    """
    env = os.environ.get("MORRIS_CANON_PIN")
    if env:
        return env.strip()
    path = Path(pin_path or DEFAULT_PIN_FILE)
    if not path.exists():
        return "main"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return "main"
    # Try yaml if present, else regex.
    try:
        import yaml  # type: ignore[import-not-found]

        data = yaml.safe_load(text) or {}
        if isinstance(data, dict) and data.get("gc_data_v2"):
            return str(data["gc_data_v2"]).strip()
    except ImportError:
        pass
    m = re.search(r"^\s*gc_data_v2\s*:\s*(\S+)\s*$", text, flags=re.MULTILINE)
    if m:
        return m.group(1).strip()
    return "main"


# ---------------------------------------------------------------------------
# Default fetchers (production)
# ---------------------------------------------------------------------------


def _default_scaffold_fetcher(url: str) -> Optional[bytes]:
    """Fetch a raw.githubusercontent.com URL. Returns None on 404 / network err."""
    import urllib.error
    import urllib.request

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "morris-canon-check/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.read()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        return None


def _default_pr_file_fetcher(
    repo: str, pr_number: int, head_sha: str, path: str
) -> Optional[bytes]:
    """Fetch a PR file's contents at `head_sha` via the GitHub raw API.

    Uses gh CLI for auth (works on private repos when the token has access).
    Returns None when the file does not exist in the PR head tree.
    """
    import subprocess

    # repo is "owner/name"
    endpoint = f"repos/{repo}/contents/{path}?ref={head_sha}"
    proc = subprocess.run(
        ["gh", "api", "-H", "Accept: application/vnd.github.raw", endpoint],
        capture_output=True,
        timeout=15,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout


# ---------------------------------------------------------------------------
# Core check
# ---------------------------------------------------------------------------


def _unified_diff_snippet(
    expected: bytes, actual: bytes, path: str, max_lines: int = DIFF_MAX_LINES
) -> str:
    """Produce a truncated unified diff (scaffold vs. PR)."""
    try:
        expected_lines = expected.decode("utf-8", errors="replace").splitlines(keepends=False)
        actual_lines = actual.decode("utf-8", errors="replace").splitlines(keepends=False)
    except Exception:  # pragma: no cover — defensive
        return "<binary or undecodable file — byte mismatch>"
    diff = list(
        difflib.unified_diff(
            expected_lines,
            actual_lines,
            fromfile=f"scaffold/{path}",
            tofile=f"pr/{path}",
            lineterm="",
        )
    )
    if not diff:
        return ""
    if len(diff) > max_lines:
        diff = diff[: max_lines - 1] + [f"... (truncated, {len(diff) - max_lines + 1} more lines)"]
    return "\n".join(diff)


def _curl_remediation(scaffold_url: str, dest_path: str) -> str:
    return f"curl -fsSL {scaffold_url} -o {dest_path}"


def check_pr_for_drift(
    repo: str,
    pr_number: int,
    head_sha: Optional[str] = None,
    scaffold_ref: str = "main",
    *,
    scaffold_fetcher: Optional[Callable[[str], Optional[bytes]]] = None,
    pr_file_fetcher: Optional[Callable[[str, int, str, str], Optional[bytes]]] = None,
    watched: tuple[WatchedFile, ...] = WATCHED_FILES,
) -> CanonCheckResult:
    """Compare a PR's canonical files against gc-data-v2/pipeline-template.

    Returns a CanonCheckResult with status:
      - "NA"      → repo is not in the watch set (no I/O performed)
      - "SUCCESS" → every watched file matches (or is non-required and absent)
      - "FAILURE" → at least one file drifts / is missing / fails assertion
      - "PENDING" → reserved for the caller (this function does not produce it)
    """
    if not is_v2_repo(repo):
        return CanonCheckResult(
            repo=repo,
            pr_number=pr_number,
            head_sha=head_sha,
            scaffold_ref=scaffold_ref,
            drifted=(),
            status="NA",
            skipped_reason="non_v2_repo",
        )

    sf = scaffold_fetcher or _default_scaffold_fetcher
    pf = pr_file_fetcher or _default_pr_file_fetcher
    # When the caller didn't tell us a head_sha, fall back to "HEAD" — the
    # default pr_file_fetcher will use gh's "ref=HEAD" semantics.
    effective_sha = head_sha or "HEAD"

    drifted: list[DriftedFile] = []
    for w in watched:
        url = SCAFFOLD_RAW_TEMPLATE.format(ref=scaffold_ref, path=w.path)
        scaffold_bytes = sf(url)
        pr_bytes = pf(repo, pr_number, effective_sha, w.path)

        # 1) Scaffold missing
        if scaffold_bytes is None:
            if w.required:
                drifted.append(
                    DriftedFile(
                        path=w.path,
                        drift_type="ASSERTION_FAILED",
                        scaffold_url=url,
                        diff_snippet=None,
                        remediation=(
                            f"Upstream scaffold fetch returned 404 / unreachable for {url}. "
                            "Investigate gc-data-v2/pipeline-template/main; do NOT post a "
                            "clean verdict on this PR until the scaffold is restored."
                        ),
                    )
                )
            # If !required and scaffold also missing, skip silently.
            continue

        # 2) PR file missing
        if pr_bytes is None:
            if not w.required:
                continue
            drifted.append(
                DriftedFile(
                    path=w.path,
                    drift_type="MISSING",
                    scaffold_url=url,
                    diff_snippet=None,
                    remediation=_curl_remediation(url, w.path),
                )
            )
            continue

        # 3) Compare based on mode
        if w.mode == "byte_match":
            if pr_bytes != scaffold_bytes:
                drifted.append(
                    DriftedFile(
                        path=w.path,
                        drift_type="MODIFIED",
                        scaffold_url=url,
                        diff_snippet=_unified_diff_snippet(scaffold_bytes, pr_bytes, w.path),
                        remediation=_curl_remediation(url, w.path),
                    )
                )
        elif w.mode == "content_assert":
            needle = (w.assert_substring or "").encode("utf-8")
            if needle and needle not in pr_bytes:
                drifted.append(
                    DriftedFile(
                        path=w.path,
                        drift_type="ASSERTION_FAILED",
                        scaffold_url=url,
                        diff_snippet=None,
                        remediation=(
                            f"Missing required pin `{w.assert_substring}` in {w.path}. "
                            "See `gc-data-v2/platform/failure-modes.md` § \"Deploy gate 2b\". "
                            f"Restore from scaffold: {_curl_remediation(url, w.path)}"
                        ),
                    )
                )

    status: CheckStatus = "FAILURE" if drifted else "SUCCESS"
    return CanonCheckResult(
        repo=repo,
        pr_number=pr_number,
        head_sha=head_sha,
        scaffold_ref=scaffold_ref,
        drifted=tuple(drifted),
        status=status,
        skipped_reason=None,
    )
