"""Tests for tech_dev_agents/morris/curator_backflow.py — STORY-887 Phase 7 (RED).

Suite covers:
  - cluster_gaps: threshold filtering, source-file immutability, graceful no-ops
  - propose_wiki_path: system / process / default slug routing
  - mark_resolved: append semantics, ISO timestamp, no dedup

All I/O is isolated to tmp_path — no live filesystem side-effects.
No network or subprocess calls.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Import the module under test (fails in RED state — expected)
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tech_dev_agents.morris.curator_backflow import (  # noqa: E402
    GapCluster,
    Resolution,
    cluster_gaps,
    mark_resolved,
    propose_wiki_path,
)

# ---------------------------------------------------------------------------
# Shared fixture data
# ---------------------------------------------------------------------------

_FIXTURE_LINES = [
    {
        "gap": "rate limit on advertising-amazon",
        "pr_number": 101,
        "repo": "tech-gc-knowledgebase",
        "claim": "no wiki page for advertising-amazon rate limits",
    },
    {
        "gap": "Advertising amazon rate-limits!!",
        "pr_number": 102,
        "repo": "tech-gc-knowledgebase",
        "claim": "advertising-amazon rate limits not documented",
    },
    {
        "gap": "advertising-amazon rate limits handling",
        "pr_number": 103,
        "repo": "tech-gc-knowledgebase",
        "claim": "handling of advertising-amazon rate limits unclear",
    },
    {
        "gap": "deploy rollback procedure unclear",
        "pr_number": 104,
        "repo": "tech-gc-knowledgebase",
        "claim": "no rollback runbook found",
    },
    {
        "gap": "code review meta",
        "pr_number": 105,
        "repo": "tech-gc-knowledgebase",
        "claim": "code review process not captured",
    },
]


@pytest.fixture()
def fixture_jsonl(tmp_path: Path) -> Path:
    """Write the 5-entry fixture to a temp JSONL file and return its path."""
    p = tmp_path / "knowledge-gaps.jsonl"
    p.write_text("\n".join(json.dumps(row) for row in _FIXTURE_LINES) + "\n")
    return p


# ---------------------------------------------------------------------------
# Group A — cluster_gaps threshold filtering
# ---------------------------------------------------------------------------


def test_threshold3_yields_one_cluster(fixture_jsonl: Path) -> None:
    """A1: 5-gap fixture at threshold=3 → exactly 1 cluster."""
    clusters = cluster_gaps(fixture_jsonl, threshold=3)
    assert len(clusters) == 1, f"Expected 1 cluster, got {len(clusters)}: {clusters}"
    cluster = clusters[0]
    assert cluster.count == 3
    assert len(cluster.evidence) == 3
    # evidence PR numbers must be the three advertising-amazon PRs
    pr_numbers = {e["pr_number"] for e in cluster.evidence}
    assert pr_numbers == {101, 102, 103}
    # normalized gap must loosely reference advertising-amazon
    assert "amazon" in cluster.gap_normalized


def test_threshold1_yields_all_clusters(fixture_jsonl: Path) -> None:
    """A2: at threshold=1, ALL clusters are returned (none filtered out).

    The 5 fixture entries normalize to 3 distinct buckets because the three
    advertising-amazon rate-limit variants (entries 101, 102, 103) cluster
    together via word-set normalization.  The other two entries remain
    singleton buckets.  This test asserts that threshold=1 returns all
    buckets (no filtering) rather than a specific count.
    """
    clusters = cluster_gaps(fixture_jsonl, threshold=1)
    # All distinct normalized buckets must be present; at least 3
    assert len(clusters) >= 3, f"Expected at least 3 clusters at threshold=1, got {len(clusters)}"
    # The advertising-amazon cluster must be present with count >= 3
    adv_clusters = [c for c in clusters if "amazon" in c.gap_normalized]
    assert len(adv_clusters) == 1
    assert adv_clusters[0].count == 3
    # Singleton clusters must also be present
    counts = sorted(c.count for c in clusters)
    assert 1 in counts, "Singleton gaps must be included at threshold=1"


# ---------------------------------------------------------------------------
# Group B — source-file immutability
# ---------------------------------------------------------------------------


def test_source_file_unchanged_after_clustering(fixture_jsonl: Path) -> None:
    """B1: cluster_gaps must not modify the source JSONL."""
    before = fixture_jsonl.read_bytes()
    cluster_gaps(fixture_jsonl, threshold=3)
    after = fixture_jsonl.read_bytes()
    assert before == after, "cluster_gaps modified the source knowledge-gaps.jsonl"


def test_empty_jsonl_returns_empty_list(tmp_path: Path) -> None:
    """B2: empty JSONL file → empty list, no exception."""
    empty = tmp_path / "knowledge-gaps.jsonl"
    empty.write_text("")
    result = cluster_gaps(empty, threshold=1)
    assert result == []


def test_missing_jsonl_returns_empty_list(tmp_path: Path) -> None:
    """B3: missing JSONL path → empty list, no exception."""
    missing = tmp_path / "does-not-exist.jsonl"
    result = cluster_gaps(missing, threshold=1)
    assert result == []


# ---------------------------------------------------------------------------
# Group C — propose_wiki_path routing
# ---------------------------------------------------------------------------


def test_propose_wiki_path_system_keyword() -> None:
    """C1: gap containing system keyword → wiki/systems/<system>.md"""
    result = propose_wiki_path("advertising-amazon rate limit")
    assert result == "wiki/systems/advertising-amazon.md"


def test_propose_wiki_path_process_keyword() -> None:
    """C2: gap containing process keyword → wiki/processes/<keyword>.md"""
    result = propose_wiki_path("review process for medium PRs")
    # Must map to wiki/processes/review.md (first matching process keyword)
    assert result == "wiki/processes/review.md"


def test_propose_wiki_path_default_slug() -> None:
    """C3: no known keyword → wiki/standards/<slug>.md"""
    result = propose_wiki_path("widget naming convention")
    assert result == "wiki/standards/widget-naming-convention.md"


def test_propose_wiki_path_system_case_insensitive() -> None:
    """C4: system keyword match is case-insensitive."""
    result = propose_wiki_path("Fabric-Keepa ingestion details")
    assert result == "wiki/systems/fabric-keepa.md"


def test_propose_wiki_path_deploy_process() -> None:
    """C5: 'deploy' is a process keyword."""
    result = propose_wiki_path("deploy rollback procedure unclear")
    assert result == "wiki/processes/deploy.md"


# ---------------------------------------------------------------------------
# Group D — mark_resolved write semantics
# ---------------------------------------------------------------------------


def test_mark_resolved_writes_one_line(tmp_path: Path) -> None:
    """D1: mark_resolved appends a JSON line with all required fields."""
    resolved_path = tmp_path / "knowledge-gaps-resolved.jsonl"
    resolution = Resolution(
        gap_normalized="advertisingamazon rate limits",
        wiki_path="wiki/systems/advertising-amazon.md",
        resolved_by_pr=201,
        resolved_at="2026-05-05T12:00:00",
    )
    mark_resolved([resolution], resolved_path)

    lines = [l for l in resolved_path.read_text().splitlines() if l.strip()]
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["gap_normalized"] == "advertisingamazon rate limits"
    assert record["wiki_path"] == "wiki/systems/advertising-amazon.md"
    assert record["resolved_by_pr"] == 201
    assert "resolved_at" in record


def test_mark_resolved_appends_on_second_call(tmp_path: Path) -> None:
    """D2: calling mark_resolved twice appends (no dedup) — documented contract."""
    resolved_path = tmp_path / "knowledge-gaps-resolved.jsonl"
    resolution = Resolution(
        gap_normalized="advertisingamazon rate limits",
        wiki_path="wiki/systems/advertising-amazon.md",
        resolved_by_pr=201,
        resolved_at="2026-05-05T12:00:00",
    )
    mark_resolved([resolution], resolved_path)
    mark_resolved([resolution], resolved_path)

    lines = [l for l in resolved_path.read_text().splitlines() if l.strip()]
    assert len(lines) == 2, (
        "mark_resolved must append each call; dedup is NOT applied (append-always contract)"
    )


def test_mark_resolved_iso_timestamp(tmp_path: Path) -> None:
    """D3: resolved_at field in written record must parse as ISO datetime."""
    resolved_path = tmp_path / "knowledge-gaps-resolved.jsonl"
    resolution = Resolution(
        gap_normalized="some gap",
        wiki_path="wiki/standards/some-gap.md",
        resolved_by_pr=999,
        resolved_at="2026-05-05T09:30:00",
    )
    mark_resolved([resolution], resolved_path)

    lines = [l for l in resolved_path.read_text().splitlines() if l.strip()]
    record = json.loads(lines[0])
    # Must not raise
    datetime.fromisoformat(record["resolved_at"])
