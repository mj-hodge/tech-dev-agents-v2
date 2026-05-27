"""Data classes shared between the seed-text validator and the payload-level gate."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class MissingPath:
    """A path referenced in a seed's Verification Plan that doesn't exist on disk."""

    referenced_path: str
    cited_in_section: str
    repo_root: Path


@dataclass
class ValidationResult:
    """Outcome of a pre-dispatch seed / payload validation.

    Attributes:
        ok:           True when nothing required is missing.
        missing:      Canonical keys of required-but-missing sections.
        warnings:     Soft findings (referenced-path miss on non-pipeline build).
        structured:   Extra context returned to API callers (e.g. unverifiable refs).
    """

    ok: bool
    missing: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    structured: dict[str, object] = field(default_factory=dict)

    def as_error_payload(
        self,
        *,
        error_code: str = "seed_validation",
        story_id: str | None = None,
    ) -> dict[str, object]:
        body: dict[str, object] = {"error": error_code, "missing": list(self.missing)}
        if story_id is not None:
            body["story_id"] = story_id
        if self.warnings:
            body["warnings"] = list(self.warnings)
        if self.structured:
            body["structured"] = dict(self.structured)
        return body
