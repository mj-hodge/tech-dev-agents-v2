"""STORY-727: Pattern Detector — aggregate findings and emit Pattern objects.

Reads adversarial-review.md files from features/*/adversarial-review.md,
detects recurring patterns above threshold, and respects the enabled=False kill switch.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Pattern:
    key: str
    count: int
    story_ids: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Internal helpers — adversarial-review.md parsing
# ---------------------------------------------------------------------------

def _parse_adversarial_review_date(content: str) -> date | None:
    """Extract review_date from adversarial-review.md content."""
    m = re.search(r"review_date:\s*(\d{4}-\d{2}-\d{2})", content)
    if m:
        try:
            return date.fromisoformat(m.group(1))
        except ValueError:
            pass
    return None


def _parse_finding_types(content: str) -> list[dict[str, str]]:
    """Extract all finding entries from the Findings section of an adversarial-review.md.

    Returns a list of dicts with 'severity' and 'finding_type' keys.
    """
    findings = []

    # Parse severity sections: ### CRITICAL, ### HIGH, etc.
    severity_pattern = re.compile(
        r"###\s+(CRITICAL|HIGH|MEDIUM|LOW)\s*\n(.*?)(?=###\s+(?:CRITICAL|HIGH|MEDIUM|LOW)|##\s+|\Z)",
        re.DOTALL | re.IGNORECASE,
    )

    for m in severity_pattern.finditer(content):
        severity = m.group(1).upper()
        section_text = m.group(2)

        if section_text.strip().lower() in ("none.", "none", ""):
            continue

        # Each finding looks like: - [C-1] [SEVERITY] finding_type
        # or: - [C-1] [CRITICAL] static-test-masquerading-as-behavioral
        finding_line_pattern = re.compile(
            r"-\s+\[[^\]]+\]\s+\[([^\]]+)\]\s+([^\n]+)"
        )
        for fm in finding_line_pattern.finditer(section_text):
            raw_severity = fm.group(1).strip().upper()
            raw_type = fm.group(2).strip()
            # Normalize finding_type: lowercase, spaces to dashes
            finding_type = raw_type.lower().replace(" ", "-")
            # Use the bracketed severity if it matches known values; else use section severity
            if raw_severity in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
                findings.append({"severity": raw_severity, "finding_type": finding_type})
            else:
                findings.append({"severity": severity, "finding_type": finding_type})

    return findings


# ---------------------------------------------------------------------------
# Stub for DB service access (patched in tests)
# ---------------------------------------------------------------------------

def _get_db_service():  # pragma: no cover
    """Return the improvement DB service. Patched in tests."""
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

PATTERN_KEY_STATIC_MASQUERADE = "adversarial.static_test_masquerading_as_behavioral"
PATTERN_KEY_SPEC_OMITTED = "adversarial.spec_requirement_omitted"
PATTERN_KEY_UNREALISTIC_FIXTURE = "adversarial.unrealistic_test_fixture"

FINDING_TYPE_TO_PATTERN_KEY = {
    "static-test-masquerading-as-behavioral": PATTERN_KEY_STATIC_MASQUERADE,
    "spec-requirement-omitted": PATTERN_KEY_SPEC_OMITTED,
    "unrealistic-test-fixture": PATTERN_KEY_UNREALISTIC_FIXTURE,
}

PATTERN_KEY_THRESHOLD_CLASS = {
    PATTERN_KEY_STATIC_MASQUERADE: "critical",
    PATTERN_KEY_SPEC_OMITTED: "critical",
    PATTERN_KEY_UNREALISTIC_FIXTURE: "high",
}


def detect_patterns(
    repo_root: Path,
    config: Any,
) -> list[Pattern]:
    """Aggregate adversarial findings and return patterns above threshold.

    When config.enabled is False, returns an empty list immediately (kill switch).
    """
    if not getattr(config, "enabled", True):
        logger.debug("improvement.enabled=False — pattern detection is a no-op (kill switch)")
        return []

    adversarial_window_days: int = getattr(config, "adversarial_window_days", 30)
    threshold_critical: int = getattr(config, "threshold_critical", 3)
    threshold_high: int = getattr(config, "threshold_high", 4)
    today = date.today()
    cutoff = today - timedelta(days=adversarial_window_days)

    # --- Scan features/*/adversarial-review.md ---
    features_dir = repo_root / "features"
    adversarial_files = list(features_dir.glob("*/adversarial-review.md"))

    # Accumulate: pattern_key -> list of contributing story_folders
    pattern_stories: dict[str, list[str]] = {
        PATTERN_KEY_STATIC_MASQUERADE: [],
        PATTERN_KEY_SPEC_OMITTED: [],
        PATTERN_KEY_UNREALISTIC_FIXTURE: [],
    }

    for path in adversarial_files:
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            logger.warning("Could not read %s", path)
            continue

        review_date = _parse_adversarial_review_date(content)
        if review_date is None:
            # Default to today if date not parseable
            review_date = today

        if review_date < cutoff:
            logger.debug("Skipping %s — review_date %s is outside %d-day window",
                         path, review_date, adversarial_window_days)
            continue

        story_folder = path.parent.name
        findings = _parse_finding_types(content)

        for finding in findings:
            finding_type = finding["finding_type"]
            severity = finding["severity"]

            pattern_key = FINDING_TYPE_TO_PATTERN_KEY.get(finding_type)
            if pattern_key is None:
                continue

            # For the static-masquerade pattern, spec says "CRITICAL severity" required
            if pattern_key == PATTERN_KEY_STATIC_MASQUERADE and severity != "CRITICAL":
                continue

            if story_folder not in pattern_stories[pattern_key]:
                pattern_stories[pattern_key].append(story_folder)

    # --- Evaluate thresholds and emit Pattern objects ---
    patterns: list[Pattern] = []

    for pattern_key, story_folders in pattern_stories.items():
        count = len(story_folders)
        threshold_class = PATTERN_KEY_THRESHOLD_CLASS[pattern_key]
        threshold = threshold_critical if threshold_class == "critical" else threshold_high

        if count >= threshold:
            patterns.append(Pattern(
                key=pattern_key,
                count=count,
                story_ids=list(story_folders),
                evidence={
                    "finding_type": pattern_key.split(".")[-1],
                    "window_days": adversarial_window_days,
                    "story_count": count,
                },
            ))
            logger.info("Pattern detected: %s (count=%d, threshold=%d)",
                        pattern_key, count, threshold)
        else:
            logger.debug(
                "Pattern %s below threshold: count=%d < %d (below threshold) — "
                "static_test_masquerading count=%d",
                pattern_key, count, threshold, count,
            )

    return patterns
