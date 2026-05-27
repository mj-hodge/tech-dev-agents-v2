"""Seed-text completeness validator.

Phase 8 implementation — turns the Phase 7 RED tests GREEN.

This module is deliberately small, deterministic, and dependency-free. It
parses a seed.md document into a heading→body map and reports which canonical
sections are missing. No LLM, no DB, no HTTP.
"""

from __future__ import annotations

import re
from pathlib import Path

from .models import MissingPath, ValidationResult
from .rules import (
    MEDIUM_PLUS_SCOPES,
    PIPELINE_REPO_PATTERNS,
    PIPELINE_REQUIRED_REFS,
    REQUIRED_SECTIONS,
    VERIFICATION_COMMAND_MARKERS,
)

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


def extract_required_sections(seed_text: str) -> dict[str, str]:
    """Parse seed_text into a `{canonical_key: body_text}` map.

    A canonical key is present only when at least one matching heading is found
    AND that heading has non-empty body text (anything between this heading and
    the next heading, stripped).

    Headings are matched case-insensitively against the aliases in
    rules.REQUIRED_SECTIONS. Substring match: a heading line "## Problem statement"
    matches the "problem" key.
    """
    if not seed_text:
        return {}

    # Build (line_start, heading_text) tuples in document order.
    matches = list(_HEADING_RE.finditer(seed_text))
    if not matches:
        return {}

    sections_in_order: list[tuple[int, int, str]] = []
    for m in matches:
        sections_in_order.append((m.start(), m.end(), m.group(2).strip()))

    # Walk pairwise to extract bodies.
    found: dict[str, str] = {}
    for idx, (_, body_start, heading_text) in enumerate(sections_in_order):
        body_end = (
            sections_in_order[idx + 1][0]
            if idx + 1 < len(sections_in_order)
            else len(seed_text)
        )
        body = seed_text[body_start:body_end].strip()
        if not body:
            continue
        normalized = heading_text.lower()
        for key, aliases in REQUIRED_SECTIONS.items():
            if key in found:
                continue
            for alias in aliases:
                if alias.lower() in normalized:
                    found[key] = body
                    break
    return found


def _scope_is_medium_plus(scope: str | None) -> bool:
    if not scope:
        return False
    return scope.strip().lower() in MEDIUM_PLUS_SCOPES


def _infer_pipeline_build_type(build_type: str | None, repo: str | None) -> bool:
    if build_type and build_type.strip().lower() == "pipeline":
        return True
    if not repo:
        return False
    lowered = repo.lower()
    return any(pat in lowered for pat in PIPELINE_REPO_PATTERNS)


def _verification_has_runnable_command(body: str) -> bool:
    haystack = body.lower()
    return any(marker.lower() in haystack for marker in VERIFICATION_COMMAND_MARKERS)


def _seed_text_has_pipeline_refs(seed_text: str) -> bool:
    return all(ref in seed_text for ref in PIPELINE_REQUIRED_REFS)


def _detect_donotdo_or_out_of_scope(seed_text: str) -> bool:
    """Either 'Do not do' style heading or an 'Out of scope' heading is acceptable.

    We don't require both — small seeds typically use 'Out of scope', longer
    seeds use a Boundaries-style 'Never do' column.
    """
    lowered = seed_text.lower()
    return (
        "do not do" in lowered
        or "do-not-do" in lowered
        or "out of scope" in lowered
        or "never do" in lowered  # boundaries-table column header
    )


def validate_seed_completeness(
    seed_text: str,
    scope: str | None = None,
    build_type: str | None = None,
    *,
    repo: str | None = None,
) -> ValidationResult:
    """Run the structural-completeness rule set against a seed.md body.

    Returns ValidationResult.ok=True only when every canonical section in
    rules.REQUIRED_SECTIONS is present with non-empty body, and the scope/
    build-type specific extras pass.
    """
    sections = extract_required_sections(seed_text)
    missing: list[str] = []
    warnings: list[str] = []
    structured: dict[str, object] = {}

    for key in REQUIRED_SECTIONS:
        if key not in sections:
            missing.append(key)

    if _scope_is_medium_plus(scope):
        if not _detect_donotdo_or_out_of_scope(seed_text):
            missing.append("do_not_do")
        verification_body = sections.get("verification_plan", "")
        if verification_body and not _verification_has_runnable_command(verification_body):
            missing.append("verification_plan_coverage")
        elif "verification_plan" not in missing and not verification_body.strip():
            # Defensive: extract_required_sections only stores non-empty bodies,
            # but keep the branch explicit.
            missing.append("verification_plan_coverage")

    if _infer_pipeline_build_type(build_type, repo):
        if not _seed_text_has_pipeline_refs(seed_text):
            missing.append("pipeline_canon_refs")
            structured["expected_pipeline_refs"] = list(PIPELINE_REQUIRED_REFS)

    return ValidationResult(
        ok=not missing,
        missing=missing,
        warnings=warnings,
        structured=structured,
    )


def verify_referenced_paths_exist(
    seed_text: str,
    repo_root: Path,
) -> list[MissingPath]:
    """Best-effort scan of `pytest <path>` references that don't exist on disk.

    Used by Medium+/pipeline rules; returns an empty list when nothing matches.
    """
    misses: list[MissingPath] = []
    if not seed_text or not repo_root:
        return misses

    for match in re.finditer(r"pytest\s+([\w./\-]+)", seed_text):
        ref = match.group(1)
        # Only investigate things that look like real paths.
        if "/" not in ref and not ref.endswith(".py"):
            continue
        candidate = repo_root / ref
        # Allow node patterns like tests/foo.py::test_bar
        head = ref.split("::", 1)[0]
        head_path = repo_root / head
        if not (candidate.exists() or head_path.exists() or head_path.parent.exists()):
            misses.append(
                MissingPath(
                    referenced_path=ref,
                    cited_in_section="verification_plan",
                    repo_root=repo_root,
                )
            )
    return misses
