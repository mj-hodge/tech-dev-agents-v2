"""Payload-level seed validation used by the dispatch v2 API gate.

Single source of truth for `validate_dispatch_seed()` — imported by:
- `tech_dev_agents/ops_console/routes/dispatch_v2.py` (enqueue gate, rework gate)
- Morris's `pre-dispatch-validate` skill (via the package __init__).
"""

from __future__ import annotations

from typing import Any

from .models import ValidationResult
from .rules import (
    DISPATCH_PAYLOAD_REQUIRED_FIELDS,
    MEDIUM_PLUS_SCOPES,
    REWORK_PAYLOAD_REQUIRED_FIELDS,
)
from .validator import validate_seed_completeness


def _is_missing(value: Any) -> bool:
    """A field counts as missing when absent, None, or empty (str/list/dict)."""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


def validate_dispatch_seed(payload: dict[str, Any]) -> ValidationResult:
    """Validate a dispatch payload against the STORY-1009 contract.

    Required fields (always):
        do_not_do, verification_plan, red_test_paths, acceptance_criteria

    Extra required field when payload["_endpoint"] == "rework":
        failure_list

    When payload["seed_text"] is provided, the structural seed-text validator
    also runs (STORY-1006 path) and its missing[] are merged in.
    """
    missing: list[str] = []
    structured: dict[str, object] = {}

    scope = payload.get("scope")
    is_medium_plus = isinstance(scope, str) and scope.strip().lower() in MEDIUM_PLUS_SCOPES

    # The four canonical payload fields. We enforce them on Medium+ scopes; on
    # 'small' scope we accept their absence so the gate doesn't break legitimate
    # small-scope enqueues (per STORY-1006 SC-9).
    if is_medium_plus or payload.get("_endpoint") == "rework":
        for field in DISPATCH_PAYLOAD_REQUIRED_FIELDS:
            if _is_missing(payload.get(field)):
                missing.append(field)

    if payload.get("_endpoint") == "rework":
        for field in REWORK_PAYLOAD_REQUIRED_FIELDS:
            if _is_missing(payload.get(field)):
                missing.append(field)

    # Optional structural check — only if caller supplies seed_text.
    seed_text = payload.get("seed_text")
    if isinstance(seed_text, str) and seed_text:
        struct_result = validate_seed_completeness(
            seed_text,
            scope=scope,
            build_type=payload.get("build_type"),
            repo=payload.get("repo"),
        )
        for key in struct_result.missing:
            if key not in missing:
                missing.append(key)
        if struct_result.structured:
            structured.update(struct_result.structured)

    return ValidationResult(
        ok=not missing,
        missing=missing,
        warnings=[],
        structured=structured,
    )
