"""curator_backflow.py — KB-GAP backflow for Cole (Morris-as-curator).

Reads ~/state/morris/knowledge-gaps.jsonl produced by review-prs KB-GAP
findings, clusters recurring gaps, proposes wiki page paths, and marks
resolved gaps in knowledge-gaps-resolved.jsonl.

API
---
cluster_gaps(jsonl_path, threshold) -> list[GapCluster]
propose_wiki_path(gap)              -> str
mark_resolved(resolutions, resolved_path) -> None

Constraints
-----------
- knowledge-gaps.jsonl is READ-ONLY from this module's perspective.
- mark_resolved writes ONLY to knowledge-gaps-resolved.jsonl.
- structlog-JSON logging only — no print() or stdlib logging.
- Python 3.12: f-strings, pathlib, type hints, dataclasses.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# System keywords — order matters: longest/most-specific first
# ---------------------------------------------------------------------------

_SYSTEM_KEYWORDS: list[str] = [
    "advertising-amazon",
    "tech-datawarehouse",
    "tech-dataimport-monday",
    "fabric-keepa",
    "sourcing-warning-labels",
    "product-health-dashboard",
    "api-retail-target",
    "tech-gc-knowledgebase",
]

# Process keywords (single words; first match wins)
_PROCESS_KEYWORDS: list[str] = [
    "review",
    "deploy",
    "release",
    "runbook",
    "escalation",
    "on-call",
    "postmortem",
    "retro",
]


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class GapCluster:
    """A cluster of normalized knowledge-gap entries that exceed the threshold."""

    gap_normalized: str
    count: int
    sample_gap: str
    evidence: list[dict] = field(default_factory=list)


@dataclass
class Resolution:
    """A record that a gap cluster was addressed by a merged wiki PR."""

    gap_normalized: str
    wiki_path: str
    resolved_by_pr: int
    resolved_at: str  # ISO-8601 string


# ---------------------------------------------------------------------------
# Text normalization
# ---------------------------------------------------------------------------

# Common filler words that don't contribute to clustering signal
_STOP_WORDS: frozenset[str] = frozenset(
    {
        "on", "the", "a", "an", "for", "of", "in", "at", "to", "and", "or",
        "with", "by", "is", "are", "was", "handling", "unclear", "procedure",
        "details", "issue", "issues",
    }
)


def _normalize(text: str) -> str:
    """Normalize gap text for clustering.

    Steps:
    1. Lowercase
    2. Replace non-alphanumeric with spaces (strips hyphens, punctuation)
    3. Tokenize; drop stop words and single-character tokens
    4. Naive plural collapse: strip trailing 's' from words longer than 3 chars
    5. Sort tokens alphabetically (order-invariant matching)
    6. Rejoin with spaces

    This collapses minor variations like:
      "rate limit on advertising-amazon"
      "Advertising amazon rate-limits!!"
      "advertising-amazon rate limits handling"
    into the same normalized key.
    """
    text = text.lower()
    # Replace non-alphanumeric characters (including hyphens) with spaces
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    words = [w for w in text.split() if w not in _STOP_WORDS and len(w) > 1]
    # Naive plural stemming: strip trailing 's' from words > 3 chars
    words = [w.rstrip("s") if len(w) > 3 else w for w in words]
    words.sort()
    return " ".join(words)


def _slugify(text: str) -> str:
    """Convert gap text to a URL-safe slug preserving word order.

    Distinct from _normalize: words are NOT sorted so the slug is
    human-readable and matches the original phrasing.
    """
    text = text.lower()
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text.replace(" ", "-")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def cluster_gaps(jsonl_path: Path, threshold: int = 3) -> list[GapCluster]:
    """Read knowledge-gaps.jsonl and return clusters that meet *threshold*.

    The source file is NEVER modified.  Missing or empty files return ``[]``.

    Args:
        jsonl_path: Path to knowledge-gaps.jsonl (read-only).
        threshold:  Minimum number of occurrences for a cluster to be
                    included.  Defaults to 3; override with
                    ``CURATOR_GAP_THRESHOLD`` env var.
    """
    effective_threshold = int(os.environ.get("CURATOR_GAP_THRESHOLD", threshold))

    if not jsonl_path.exists():
        log.info(
            "knowledge_gaps_missing",
            path=str(jsonl_path),
            action="returning_empty_list",
        )
        return []

    raw_text = jsonl_path.read_text(encoding="utf-8")
    if not raw_text.strip():
        log.info("knowledge_gaps_empty", path=str(jsonl_path))
        return []

    # bucket: normalized_key -> list of raw entry dicts
    buckets: dict[str, list[dict]] = {}

    for lineno, line in enumerate(raw_text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            log.warning("knowledge_gaps_bad_line", lineno=lineno, line=line[:80])
            continue

        gap_text = entry.get("gap", "")
        normalized = _normalize(gap_text)
        if not normalized:
            continue

        buckets.setdefault(normalized, []).append(entry)

    clusters: list[GapCluster] = []
    for normalized_key, entries in buckets.items():
        if len(entries) >= effective_threshold:
            evidence = [
                {
                    "pr_number": e.get("pr_number"),
                    "repo": e.get("repo", ""),
                    "claim": e.get("claim", ""),
                }
                for e in entries
            ]
            clusters.append(
                GapCluster(
                    gap_normalized=normalized_key,
                    count=len(entries),
                    sample_gap=entries[0].get("gap", normalized_key),
                    evidence=evidence,
                )
            )

    log.info(
        "gap_clustering_complete",
        total_buckets=len(buckets),
        clusters_above_threshold=len(clusters),
        threshold=effective_threshold,
    )
    return clusters


def propose_wiki_path(gap: str) -> str:
    """Heuristic: map a gap string to a wiki page path.

    Rules (checked in order, first match wins):
    1. Gap contains a known system keyword  → ``wiki/systems/<system>.md``
    2. Gap contains a known process keyword → ``wiki/processes/<keyword>.md``
    3. Default                              → ``wiki/standards/<slug>.md``

    The slug is the normalized gap text with spaces replaced by hyphens.
    """
    lower_gap = gap.lower()

    for system in _SYSTEM_KEYWORDS:
        # Match the keyword regardless of surrounding hyphens/spaces
        pattern = system.replace("-", r"[-\s]?")
        if re.search(pattern, lower_gap):
            return f"wiki/systems/{system}.md"

    for process in _PROCESS_KEYWORDS:
        if process in lower_gap:
            return f"wiki/processes/{process}.md"

    # Default: slugify preserving word order (not sorted — human-readable path)
    slug = _slugify(gap)
    return f"wiki/standards/{slug}.md"


def mark_resolved(resolutions: list[Resolution], resolved_path: Path) -> None:
    """Append resolution records to knowledge-gaps-resolved.jsonl.

    This is an APPEND-ALWAYS operation — no deduplication is performed.
    The caller is responsible for idempotency if required.

    Args:
        resolutions:   List of Resolution dataclass instances.
        resolved_path: Path to knowledge-gaps-resolved.jsonl (created if absent).
    """
    resolved_path.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    for res in resolutions:
        record = {
            "gap_normalized": res.gap_normalized,
            "wiki_path": res.wiki_path,
            "resolved_by_pr": res.resolved_by_pr,
            "resolved_at": res.resolved_at,
        }
        lines.append(json.dumps(record))

    with resolved_path.open("a", encoding="utf-8") as fh:
        for line in lines:
            fh.write(line + "\n")

    log.info(
        "gaps_marked_resolved",
        count=len(resolutions),
        resolved_path=str(resolved_path),
    )
