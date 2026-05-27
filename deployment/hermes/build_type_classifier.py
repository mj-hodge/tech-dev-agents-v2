"""build_type_classifier — STORY-1001.

Classifies a free-text feature description into a build_type enum, validates
source slugs against gc-data-v2/sources.yaml, resolves gc-data-v2 commit SHAs,
and assembles the pipeline canon reading list for Phase 1.

All five public functions are tested in tests/sdlc/test_spec_classifier.py,
tests/sdlc/test_phase1_canon_load.py, and tests/sdlc/test_new_project_pipeline_scaffold.py.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Optional

try:
    import yaml
    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False

# ---------------------------------------------------------------------------
# Constants  (data-driven — retro can update keywords via build-type-classifier.md
# without touching this module in most cases, but these serve as the runtime source
# of truth consumed by classify_build_type() and validate_source())
# ---------------------------------------------------------------------------

PIPELINE_KEYWORDS: list[str] = [
    "pipeline",
    "dag",
    "airflow",
    "bronze",
    "silver",
    "gold",
    "ingest",
    "dbt",
    "etl",
    "elt",
    "warehouse",
    "medallion",
    # Source slugs also act as pipeline signals
    "amazon-sp-api",
    "amazon_sp_api",
    "amazon-ads-api",
    "amazon_ads_api",
    "walmart-supplier",
    "walmart_supplier",
    "walmart-ad-connect",
    "walmart_ad_connect",
    "shopify",
    "levanta",
    "toolio",
    "netsuite",
]

BUG_KEYWORDS: list[str] = [
    "bug",
    "fix",
    "regression",
    "broken",
    "error",
    "crash",
    "failure",
    "off-by-one",
    "typo",
]

INFRA_KEYWORDS: list[str] = [
    "infra",
    "terraform",
    "kubernetes",
    "k8s",
    "helm",
    "docker",
    "container",
    "deploy",
]

OPS_KEYWORDS: list[str] = [
    "ops",
    "alert",
    "monitor",
    "dashboard",
    "runbook",
    "oncall",
    "slo",
    "sla",
    "incident",
]

KNOWN_SOURCES: list[str] = [
    "amazon-sp-api",
    "amazon-ads-api",
    "walmart-supplier",
    "walmart-ad-connect",
    "shopify",
    "levanta",
    "toolio",
    "netsuite",
]

# Platform docs always loaded for pipeline builds (paths relative to gc-data-v2 root)
CANON_PLATFORM_DOCS: list[str] = [
    "platform/pipeline-standard.md",
    "platform/failure-modes.md",
    "platform/new-pipeline-repo.md",
]

# Default candidate paths for gc-data-v2 checkout (platform-aware)
GC_DATA_V2_PATHS: list[str] = [
    "/mnt/c/Projects/gc-data-v2",
    os.path.expanduser("~/test_projects/gc-data-v2"),
    "/opt/gc-data-v2",
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def classify_build_type(text: str) -> str:
    """Classify build type from free-text description.

    Uses keyword matching with precedence: pipeline > bug > infra > ops > feature.
    Matching is case-insensitive; source slugs are normalised before matching.

    Args:
        text: Feature description text.

    Returns:
        One of: 'pipeline', 'feature', 'bug', 'ops', 'infra'.
        Default when no keywords match: 'feature'.
    """
    normalised = text.lower()

    if _matches_any(normalised, PIPELINE_KEYWORDS):
        return "pipeline"
    if _matches_any(normalised, BUG_KEYWORDS):
        return "bug"
    if _matches_any(normalised, INFRA_KEYWORDS):
        return "infra"
    if _matches_any(normalised, OPS_KEYWORDS):
        return "ops"
    return "feature"


def validate_source(source_input: str, sources_yaml_path: Optional[str] = None) -> str:
    """Normalise and validate a source slug.

    Normalisation: lowercase, underscores and spaces replaced with hyphens.
    Validation: check against sources_yaml_path if given; otherwise check
    against the hardcoded KNOWN_SOURCES enum. The slug 'other' is always valid.

    Args:
        source_input: Raw user input (e.g. 'amazon_sp_api', 'Walmart Supplier').
        sources_yaml_path: Optional path to gc-data-v2/sources.yaml.

    Returns:
        Normalised kebab-case slug.

    Raises:
        ValueError: If slug is unknown and not 'other', with message:
            "Unknown source '<slug>' — not in gc-data-v2/sources.yaml.
             Use 'other' or add the source first."
    """
    slug = _normalise_slug(source_input)

    if slug == "other":
        return slug

    known = _load_known_sources(sources_yaml_path)
    if slug not in known:
        raise ValueError(
            f"Unknown source '{slug}' — not in gc-data-v2/sources.yaml. "
            f"Use 'other' or add the source first."
        )
    return slug


def resolve_gc_data_v2_commit(repo_paths: Optional[list[str]] = None) -> str:
    """Resolve HEAD SHA of gc-data-v2 checkout.

    Args:
        repo_paths: Candidate absolute paths to check (expands ~ automatically).
                    Default: GC_DATA_V2_PATHS constant.

    Returns:
        40-char lowercase hex SHA string.

    Raises:
        RuntimeError: If gc-data-v2 not found at any candidate path, with a
                      message that includes the clone command.
    """
    candidates = repo_paths if repo_paths is not None else GC_DATA_V2_PATHS
    expanded = [os.path.expanduser(p) for p in candidates]

    for path in expanded:
        if not Path(path).is_dir():
            continue
        try:
            result = subprocess.run(
                ["git", "-C", path, "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                check=True,
            )
            sha = result.stdout.strip()
            if len(sha) == 40 and re.fullmatch(r"[0-9a-f]{40}", sha):
                return sha
        except subprocess.CalledProcessError:
            continue

    checked = ", ".join(expanded)
    raise RuntimeError(
        f"gc-data-v2 not found at any of: {checked}.\n"
        f"Clone it first:\n"
        f"  git clone https://github.com/hpi-gorillacommerce/gc-data-v2 "
        f"{expanded[0] if expanded else '/mnt/c/Projects/gc-data-v2'}"
    )


def load_pipeline_canon(gc_data_v2_root: str, source_slug: str) -> dict:
    """Return canon reading list for a pipeline build.

    Resolves gc_data_v2_commit, assembles the 3 platform docs + optional
    source README, and returns warnings for missing files.

    Args:
        gc_data_v2_root: Absolute path to gc-data-v2 checkout.
        source_slug: Normalised source slug (e.g. 'walmart-supplier') or 'other'.

    Returns:
        dict:
            'docs':          list[str] — relative paths (from gc_data_v2_root)
            'source_readme': str | None — relative path or None if missing/other
            'warnings':      list[str] — human-readable warnings
            'commit':        str — 40-char SHA
    """
    root = Path(gc_data_v2_root)
    docs: list[str] = []
    warnings: list[str] = []

    commit = resolve_gc_data_v2_commit(repo_paths=[gc_data_v2_root])

    # Platform docs
    for rel in CANON_PLATFORM_DOCS:
        full = root / rel
        if full.exists():
            docs.append(rel)
        else:
            warnings.append(f"missing_canon_doc: {rel}")

    # Per-source README
    source_readme: Optional[str] = None
    if source_slug != "other":
        readme_rel = f"sources/{source_slug}/README.md"
        readme_full = root / readme_rel
        if readme_full.exists():
            docs.append(readme_rel)
            source_readme = readme_rel
        else:
            warnings.append(f"missing_source_readme: {readme_rel}")
    else:
        warnings.append("seed_warning: source_other")

    return {
        "docs": docs,
        "source_readme": source_readme,
        "warnings": warnings,
        "commit": commit,
    }


def scaffold_pipeline_project(target_dir: str) -> None:
    """Create standard pipeline project directory structure.

    Idempotent — safe to run multiple times on the same directory.
    Creates: models/, sources/, tests/ subdirectories.

    In production, this is called by new-project/SKILL.md when --type=pipeline
    is passed. The caller is responsible for copying gc-data-v2/pipeline-template/
    files and writing pipeline_template_commit to config.yaml.

    Args:
        target_dir: Absolute path to the new project directory.
    """
    root = Path(target_dir)
    for subdir in ["models", "sources", "tests"]:
        (root / subdir).mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _normalise_slug(raw: str) -> str:
    """Lowercase + replace underscores and spaces with hyphens."""
    return raw.strip().lower().replace("_", "-").replace(" ", "-")


def _matches_any(text: str, keywords: list[str]) -> bool:
    """Return True if any keyword appears (case-insensitively) in text."""
    text_lower = text.lower()
    for kw in keywords:
        kw_norm = kw.lower()
        if kw_norm in text_lower:
            return True
    return False


def _load_known_sources(sources_yaml_path: Optional[str]) -> list[str]:
    """Load known source slugs from sources.yaml or fall back to KNOWN_SOURCES."""
    if not sources_yaml_path:
        return KNOWN_SOURCES

    yaml_path = Path(sources_yaml_path)
    if not yaml_path.exists():
        return KNOWN_SOURCES

    if not _YAML_AVAILABLE:
        # PyYAML not installed — fall back to hardcoded list with warning
        return KNOWN_SOURCES

    try:
        data = yaml.safe_load(yaml_path.read_text())
        if isinstance(data, dict) and "sources" in data:
            return list(data["sources"].keys())
    except Exception:
        pass

    return KNOWN_SOURCES
