# STORY-1011 — Feature Spec: sdlc-framework version tagging + drift CI

**Phase:** 6 — Design
**Date:** 2026-05-18

---

## 1. Pin Contract

**File:** `.sdlc-pinned-version` (repo root)

- One line: `vX.Y.Z` followed by a trailing newline.
- Lives at the **repo root** (not inside `.sdlc/`) because `.sdlc/` is a git submodule — files inside a submodule cannot be tracked by the parent repo.
- The existing `.sdlc/VERSION` file (upstream-managed, contains `1.0.0`) remains in the framework repo but is cross-checked against the pin file by the workflow and contract tests.

**Rationale:** `.sdlc/VERSION` lives inside the submodule (sdlc-framework repo) and cannot be managed by the downstream project. `.sdlc-pinned-version` is a downstream-side declaration that the parent repo owns and commits.

## 2. Workflow Shape

**File:** `.github/workflows/sdlc-drift-check.yml`

- **Trigger:** `pull_request` and `push` to `main` on paths `.sdlc/**`, `.sdlc-pinned-version`, the workflow file, and `tools/sdlc_drift_check.py`. Also supports `workflow_dispatch`.
- **Permissions:** `contents: read` (read-only).
- **Steps:**
  1. Checkout with `submodules: true` (uses `SDLC_FRAMEWORK_PAT` or `GITHUB_TOKEN`)
  2. Read `.sdlc-pinned-version` → extract version tag
  3. Read `.sdlc/VERSION` → verify consistency with pinned version
  4. Clone sdlc-framework at the pinned tag into `/tmp/sdlc-framework-pinned`
  5. Setup Python 3.12, run `tools.sdlc_drift_check` for byte-diff
- **Failure annotation format:** `ERROR: drift detected — N file(s) differ from pinned baseline` with per-file listing.
- **Concurrency:** grouped by `sdlc-drift-${{ github.ref }}`, cancel-in-progress.

## 3. Helper Module API

**File:** `tools/sdlc_drift_check.py`

```python
@dataclass(frozen=True)
class Drift:
    path: str                                          # relative to .sdlc/ root
    kind: Literal["modified", "added", "removed"]
    local_sha: str = ""                                # sha256 hex
    pinned_sha: str = ""                               # sha256 hex

DEFAULT_EXCLUDE: FrozenSet[str] = frozenset({".git", "PINNED_VERSION", "CLAUDE.md.local"})

def read_pinned_version(pin_file: Path) -> str: ...
    # raises FileNotFoundError or ValueError

def run(
    local_sdlc_path: Path,
    pinned_sdlc_path: Path,
    *,
    exclude: Iterable[str] = DEFAULT_EXCLUDE,
) -> list[Drift]: ...

# CLI: python -m tools.sdlc_drift_check <local_sdlc_path> <pinned_sdlc_path>
# Exit codes: 0 (no drift), 1 (drift detected), 2 (usage error)
```

## 4. Exclusion List

| Path | Reason |
|------|--------|
| `.git/` | Submodule metadata |
| `PINNED_VERSION` | Downstream-managed pin file (if present inside submodule) |
| `CLAUDE.md.local` | Per-project override (future-proofing) |

## 5. Error UX

| Failure Mode | Output |
|---|---|
| Drift detected | `ERROR: drift detected — N file(s) differ from pinned baseline` + file list + hint |
| Version mismatch | `Version mismatch: .sdlc-pinned-version=vX.Y.Z but .sdlc/VERSION=Y.Y.Y` |
| Tag not found | `error: pinned version vX.Y.Z not found in sdlc-framework — check tag exists upstream` |
| Pin file missing | `.sdlc-pinned-version file not found at repo root` |
| Pin file empty | `pinned version file is empty: ...` |

## 6. Test Coverage

**14 tests** across 4 categories:

1. **Clean state** (1 test): no drift when directories match
2. **Tamper detection** (3 tests): modified, added, and removed files
3. **Exclusion** (3 tests): .git/, PINNED_VERSION, CLAUDE.md.local
4. **Error handling** (3 tests): missing file, empty file, valid file
5. **Contract-critical** (4 tests): pin file exists, valid semver, VERSION exists, versions consistent

## 7. Coordination PR Scope

- Tag `v1.0.0` already exists on `sdlc-framework` (verified 2026-05-18).
- `CHANGELOG.md` creation in sdlc-framework is deferred to a separate coordination PR.
- No framework source-code changes in this story's PR.
