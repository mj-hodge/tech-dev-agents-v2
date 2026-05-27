#!/usr/bin/env bash
# Q6 — build-artifact.sh
# Packages dispatch_v2 routes, v2 migrations, and protocol_manifest.json into
# a deterministic tarball with an embedded SHA256 checksum.
#
# Outputs:
#   $OUTPUT_DIR/ops-console-artifact.tar.gz  — the artifact tarball
#   $OUTPUT_DIR/ops-console-artifact.tar.gz contains CHECKSUM  — embedded hash
#
# Environment variables:
#   SOURCE_DIR   — root of the source tree to package (default: $PWD)
#   OUTPUT_DIR   — where to write the artifact (default: $PWD)
#
# Determinism guarantees:
#   - Files are sorted alphabetically before hashing / packing.
#   - Tarball uses --sort=name and a fixed --mtime to suppress filesystem
#     timestamps.
#   - No embedded build timestamps.
#
# Usage:
#   bash build-artifact.sh
#   OUTPUT_DIR=/tmp/out SOURCE_DIR=/path/to/src bash build-artifact.sh

set -euo pipefail

SOURCE_DIR="${SOURCE_DIR:-$PWD}"
OUTPUT_DIR="${OUTPUT_DIR:-$PWD}"

log() { echo "[build-artifact] $*" >&2; }

# ---------------------------------------------------------------------------
# Locate source files
# ---------------------------------------------------------------------------

# Resolve files relative to SOURCE_DIR.
# Mirrors the real-repo layout: routes/dispatch_v2.py, migrations/05*.sql,
# protocol_manifest.json.  Tests use the same layout in their tmp dirs.
DISPATCH_V2="${SOURCE_DIR}/routes/dispatch_v2.py"
MANIFEST="${SOURCE_DIR}/protocol_manifest.json"

# Collect 05* migrations, sorted
mapfile -t MIGRATION_FILES < <(
    find "${SOURCE_DIR}/migrations" -maxdepth 1 -name '05*.sql' -type f \
    | sort
)

# Validate required files
if [ ! -f "${DISPATCH_V2}" ]; then
    log "ERROR: dispatch_v2.py not found at ${DISPATCH_V2}"
    exit 1
fi
if [ ! -f "${MANIFEST}" ]; then
    log "ERROR: protocol_manifest.json not found at ${MANIFEST}"
    exit 1
fi
if [ "${#MIGRATION_FILES[@]}" -eq 0 ]; then
    log "ERROR: No 05*.sql migrations found in ${SOURCE_DIR}/migrations/"
    exit 1
fi

log "Source directory: ${SOURCE_DIR}"
log "Migrations found: ${#MIGRATION_FILES[@]}"

# ---------------------------------------------------------------------------
# Walk import graph of dispatch_v2.py — collect tech_dev_agents/* packages
# ---------------------------------------------------------------------------

log "Scanning import graph of dispatch_v2.py for tech_dev_agents/* dependencies..."

TECH_DEV_ROOT="${SOURCE_DIR}/tech_dev_agents"
EXTRA_PKG_DIRS=()

# Use Python AST to find top-level tech_dev_agents sub-packages referenced
# by dispatch_v2.py (static walk — deterministic, no import side-effects).
mapfile -t EXTRA_PKGS < <(
python3 - "${DISPATCH_V2}" <<'PYEOF'
import ast, sys
from pathlib import Path

def scan(filepath):
    try:
        tree = ast.parse(Path(filepath).read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    found = set()
    for node in ast.walk(tree):
        module = None
        if isinstance(node, ast.ImportFrom) and node.module:
            module = node.module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("tech_dev_agents."):
                    module = alias.name
        if module and module.startswith("tech_dev_agents."):
            parts = module.split(".")
            if len(parts) >= 2:
                found.add(parts[1])
    for pkg in sorted(found):
        print(pkg)

scan(sys.argv[1])
PYEOF
)

# shellcheck disable=SC2181
if [ $? -ne 0 ]; then
    log "ERROR: Import graph scan failed"
    exit 1
fi

log "tech_dev_agents sub-packages detected: ${EXTRA_PKGS[*]:-none}"

for pkg in "${EXTRA_PKGS[@]:-}"; do
    [ -z "${pkg}" ] && continue
    pkg_dir="${TECH_DEV_ROOT}/${pkg}"
    if [ -d "${pkg_dir}" ]; then
        log "  bundling extra package: tech_dev_agents/${pkg}/"
        EXTRA_PKG_DIRS+=("${pkg_dir}")
    else
        log "  WARNING: referenced package tech_dev_agents.${pkg} not found at ${pkg_dir}"
    fi
done

# ---------------------------------------------------------------------------
# Compute SHA256 over all tracked files (deterministic: sorted order)
# ---------------------------------------------------------------------------

# Collect extra package files for checksum
EXTRA_FILES=()
for pkg_dir in "${EXTRA_PKG_DIRS[@]:-}"; do
    [ -z "${pkg_dir}" ] && continue
    while IFS= read -r -d '' pyf; do
        EXTRA_FILES+=("${pyf}")
    done < <(find "${pkg_dir}" -name "*.py" -type f -print0 | sort -z)
done
if [ "${#EXTRA_FILES[@]}" -gt 0 ]; then
    ALL_FILES=("${DISPATCH_V2}" "${MANIFEST}" "${MIGRATION_FILES[@]}" "${EXTRA_FILES[@]}")
else
    ALL_FILES=("${DISPATCH_V2}" "${MANIFEST}" "${MIGRATION_FILES[@]}")
fi
# Sort the full file list to ensure order is independent of glob expansion
mapfile -t ALL_FILES_SORTED < <(printf '%s\n' "${ALL_FILES[@]}" | sort)

log "Computing SHA256 over ${#ALL_FILES_SORTED[@]} file(s)..."

SHA_INPUT=""
for f in "${ALL_FILES_SORTED[@]}"; do
    file_hash=$(sha256sum "${f}" | awk '{print $1}')
    relative_path="${f#${SOURCE_DIR}/}"
    SHA_INPUT+="${file_hash}  ${relative_path}"$'\n'
    log "  ${relative_path}: ${file_hash}"
done

COMBINED_SHA=$(echo -n "${SHA_INPUT}" | sha256sum | awk '{print $1}')
log "Combined SHA256: ${COMBINED_SHA}"

# ---------------------------------------------------------------------------
# Stage files for tarball
# ---------------------------------------------------------------------------

STAGE_DIR="$(mktemp -d)"
trap 'rm -rf "${STAGE_DIR}"' EXIT

mkdir -p "${STAGE_DIR}/routes"
mkdir -p "${STAGE_DIR}/migrations"

cp "${DISPATCH_V2}" "${STAGE_DIR}/routes/dispatch_v2.py"
cp "${MANIFEST}" "${STAGE_DIR}/protocol_manifest.json"
for mig in "${MIGRATION_FILES[@]}"; do
    cp "${mig}" "${STAGE_DIR}/migrations/$(basename "${mig}")"
done

# Stage extra tech_dev_agents packages found via import graph walk
if [ "${#EXTRA_PKG_DIRS[@]}" -gt 0 ]; then
    mkdir -p "${STAGE_DIR}/tech_dev_agents"
    # Copy root __init__.py if present (namespace package support)
    if [ -f "${TECH_DEV_ROOT}/__init__.py" ]; then
        cp "${TECH_DEV_ROOT}/__init__.py" "${STAGE_DIR}/tech_dev_agents/__init__.py"
    fi
    for pkg_dir in "${EXTRA_PKG_DIRS[@]}"; do
        pkg_name="$(basename "${pkg_dir}")"
        cp -r "${pkg_dir}" "${STAGE_DIR}/tech_dev_agents/${pkg_name}"
        log "  staged extra package: tech_dev_agents/${pkg_name}/"
    done
fi

# Write embedded checksum file
printf '%s\n' "${COMBINED_SHA}" > "${STAGE_DIR}/CHECKSUM"

# ---------------------------------------------------------------------------
# Build deterministic tarball
# ---------------------------------------------------------------------------

mkdir -p "${OUTPUT_DIR}"
ARTIFACT="${OUTPUT_DIR}/ops-console-artifact.tar.gz"

# --sort=name   — alphabetical file order (deterministic across machines)
# --mtime       — suppress filesystem mtime from archive entries
# --owner/group — prevent UID/GID differences between build environments
tar \
    --create \
    --gzip \
    --file "${ARTIFACT}" \
    --sort=name \
    --mtime='2026-01-01 00:00:00 UTC' \
    --owner=0 \
    --group=0 \
    --numeric-owner \
    -C "${STAGE_DIR}" \
    .

log "Artifact written to: ${ARTIFACT}"
log "Combined SHA256: ${COMBINED_SHA}"
printf '%s\n' "${COMBINED_SHA}"
