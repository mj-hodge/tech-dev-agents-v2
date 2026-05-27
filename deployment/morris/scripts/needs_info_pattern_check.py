"""
deployment/morris/scripts/needs_info_pattern_check.py — STORY-773

Check 16: needs_info Pattern Detection for fleet-vigilance.

Detects repeating QUESTION.md content across needs_info stories using
bigram Jaccard similarity and anchor-phrase matching. CRITs when ≥3
stories in a cluster, WARNs at 2, suppresses duplicate DMs for 6 hours.

Public API:
  run_check(fetch_fn, run_fn=None, suppression_path=None) → dict
  normalize_question_text(text: str) → str
  bigram_jaccard_similarity(text_a: str, text_b: str) → float
  load_anchor_phrases(path: str | None) → list[str]
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import time
from collections import Counter
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CHECK_ID = 16
_DEFAULT_THRESHOLD = 0.40
_DEFAULT_SUPPRESS_HOURS = 6
_DEFAULT_ANCHORS_PATH = os.path.expanduser("~/.hermes/needs_info_anchors.txt")

_DEFAULT_ANCHOR_PHRASES = [
    "phase path does not include",
    "was dispatched but",
    "blocked on story-",
    "branch_setup_failed",
    "git checkout main failed",
]

_MAX_QUESTION_SIZE = 100 * 1024  # 100KB truncation limit (Escalation Contract §5)

# ---------------------------------------------------------------------------
# Text normalization
# ---------------------------------------------------------------------------

# Patterns to strip before similarity computation (AC-3)
_RE_MARKDOWN_HEADER = re.compile(r"^#+\s+.*$", re.MULTILINE)
_RE_STORY_ID = re.compile(r"STORY-\d+", re.IGNORECASE)
_RE_ISO_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[+-]\d{2}:?\d{2}|Z)?"
)
_RE_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_RE_BOLD_MARKERS = re.compile(r"\*\*[^*]+\*\*:?")
_RE_BOLD_LINE = re.compile(r"^\*\*[^*]+\*\*:?\s*.*$", re.MULTILINE)
_RE_BACKTICK_CONTENT = re.compile(r"`[^`]+`")
_RE_PHASE_NUMBER = re.compile(r"\bphase\s+\d+\b", re.IGNORECASE)
_RE_PHASE_PARENS = re.compile(
    r"\(\s*(?:research|analysis|feature spec|implementation|"
    r"code review|test design|design|planning)\s*\)",
    re.IGNORECASE,
)
_RE_POSSESSIVE = re.compile(r"'s\b")
_RE_PUNCTUATION = re.compile(r"[,;:—–\-\"'()]+")
_RE_WHITESPACE = re.compile(r"\s+")


def normalize_question_text(text: str) -> str:
    """Strip markdown headers, dates, ISO timestamps, story IDs before scoring.

    Returns lowercase, whitespace-collapsed text with markers replaced by tokens.
    """
    t = text
    t = _RE_MARKDOWN_HEADER.sub("", t)
    # Strip metadata lines like **Story:** STORY-008, **Phase:** 2 (Research), **Date:** ...
    t = _RE_BOLD_LINE.sub("", t)
    t = _RE_BOLD_MARKERS.sub("", t)
    # Remove backtick-enclosed content entirely (phase paths, code snippets)
    t = _RE_BACKTICK_CONTENT.sub("", t)
    t = _RE_ISO_TIMESTAMP.sub("", t)
    t = _RE_DATE.sub("", t)
    t = _RE_STORY_ID.sub("", t)
    t = _RE_PHASE_NUMBER.sub("<PHASE>", t)
    t = _RE_PHASE_PARENS.sub("", t)
    t = _RE_POSSESSIVE.sub("", t)
    t = _RE_PUNCTUATION.sub(" ", t)
    t = _RE_WHITESPACE.sub(" ", t).strip().lower()
    return t


# ---------------------------------------------------------------------------
# Bigram Jaccard similarity
# ---------------------------------------------------------------------------


def _word_bigrams(text: str) -> set[tuple[str, str]]:
    """Extract word-level bigrams from text."""
    words = text.split()
    if len(words) < 2:
        return set()
    return {(words[i], words[i + 1]) for i in range(len(words) - 1)}


def bigram_jaccard_similarity(text_a: str, text_b: str) -> float:
    """Compute Jaccard similarity over word bigrams.

    Returns 1.0 for identical, 0.0 for disjoint, float in between.
    """
    bigrams_a = _word_bigrams(text_a)
    bigrams_b = _word_bigrams(text_b)
    if not bigrams_a and not bigrams_b:
        return 1.0
    if not bigrams_a or not bigrams_b:
        return 0.0
    intersection = bigrams_a & bigrams_b
    union = bigrams_a | bigrams_b
    return len(intersection) / len(union)


# ---------------------------------------------------------------------------
# Anchor phrases
# ---------------------------------------------------------------------------


def load_anchor_phrases(path: str | None) -> list[str]:
    """Load anchor phrases from file (one per line) or return defaults.

    AC-8: Configurable via file. Falls back to built-in defaults.
    """
    if path is not None:
        try:
            p = Path(path)
            if p.exists():
                lines = [
                    line.strip()
                    for line in p.read_text().splitlines()
                    if line.strip()
                ]
                if lines:
                    return lines
        except Exception:
            pass

    # Also check env-var-configured path
    env_path = os.environ.get("FV_NEEDS_INFO_ANCHORS_FILE")
    if env_path:
        try:
            p = Path(env_path)
            if p.exists():
                lines = [
                    line.strip()
                    for line in p.read_text().splitlines()
                    if line.strip()
                ]
                if lines:
                    return lines
        except Exception:
            pass

    return list(_DEFAULT_ANCHOR_PHRASES)


def _find_anchor_matches(text: str, anchors: list[str]) -> list[str]:
    """Return which anchor phrases appear in the text (case-insensitive).

    Uses both exact substring matching on raw text and word-proximity
    matching on normalized text. A phrase matches if all its words appear
    within a sliding window (allows intervening noise like story IDs).
    """
    lower = text.lower()
    normalized_lower = normalize_question_text(text)
    norm_words = normalized_lower.split()
    matches = []
    for a in anchors:
        a_lower = a.lower()
        # Direct substring match on raw text
        if a_lower in lower or a_lower in normalized_lower:
            matches.append(a)
            continue
        # Word-proximity match: all anchor words appear in a sliding window
        a_words = a_lower.split()
        if len(a_words) < 2:
            continue
        window_size = len(a_words) + 3  # allow up to 3 intervening words
        found = False
        for start in range(len(norm_words) - len(a_words) + 1):
            window = norm_words[start : start + window_size]
            if all(w in window for w in a_words):
                found = True
                break
        if found:
            matches.append(a)
    return matches


# ---------------------------------------------------------------------------
# Suppression state
# ---------------------------------------------------------------------------

_DEFAULT_SUPPRESSION_PATH = "/home/hermes/state/morris/needs-info-cluster-suppression.json"


def _load_suppression(path: str | None) -> tuple[dict, str]:
    """Load suppression state from JSON file."""
    p = path or _DEFAULT_SUPPRESSION_PATH
    try:
        with open(p) as f:
            data = json.load(f)
            if not isinstance(data, dict):
                return {}, p
            return data, p
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        # Escalation Contract §4: corrupted file → treat as unsuppressed
        return {}, p


def _save_suppression(state: dict, path: str) -> None:
    """Persist suppression state to disk."""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            json.dump(state, f)
    except Exception:
        pass


def _cluster_signature(story_ids: list[str], dominant_anchor: str) -> str:
    """Compute cluster suppression key: sha256(sorted IDs + anchor)[:12]."""
    payload = "|".join(sorted(story_ids)) + "|" + (dominant_anchor or "")
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


def _is_cluster_suppressed(
    state: dict, signature: str, suppress_hours: float
) -> bool:
    """Check if a cluster signature is within the suppression window."""
    ts = state.get(signature)
    if ts is None:
        return False
    try:
        return (time.time() - float(ts)) < (suppress_hours * 3600)
    except (TypeError, ValueError):
        return False


def _record_cluster_crit(state: dict, path: str, signature: str) -> None:
    """Record that a CRIT DM was sent for this cluster signature."""
    state[signature] = time.time()
    _save_suppression(state, path)


# ---------------------------------------------------------------------------
# QUESTION.md fetching
# ---------------------------------------------------------------------------


def _fetch_question_content(
    row: dict, run_fn, repo: str | None = None
) -> str | None:
    """Fetch QUESTION.md content via gh API for a dispatch row.

    AC-11: If fetch fails, return None (don't abort).
    """
    try:
        story_repo = row.get("repo", repo or "tech-dev-agents")
        branch = row.get("branch", "")
        path = row.get("needs_info_path", "")

        if not branch or not path:
            return None

        # gh api repos/{owner}/{repo}/contents/{path}?ref={branch}
        cmd = [
            "gh", "api",
            f"repos/hpi-gorillacommerce/{story_repo}/contents/{path}",
            "-q", ".content",
            "--header", "Accept: application/vnd.github.v3.raw",
            "-H", f"X-GitHub-Api-Version: 2022-11-28",
        ]
        # Add ref as query param
        cmd = [
            "gh", "api",
            f"repos/hpi-gorillacommerce/{story_repo}/contents/{path}?ref={branch}",
            "--header", "Accept: application/vnd.github.v3.raw",
        ]

        result = run_fn(cmd, capture_output=True, text=True, timeout=30)

        if result.returncode != 0:
            logger.warning(
                f"[FLEET-VIGILANCE Check 16] Failed to fetch QUESTION.md for "
                f"{row.get('story_id')}: {getattr(result, 'stderr', '')}"
            )
            return None

        content = result.stdout or ""
        # Escalation Contract §5: truncate > 100KB
        if len(content) > _MAX_QUESTION_SIZE:
            logger.warning(
                f"[FLEET-VIGILANCE Check 16] QUESTION.md for {row.get('story_id')} "
                f"exceeds 100KB, truncating"
            )
            content = content[:_MAX_QUESTION_SIZE]

        return content if content.strip() else None

    except Exception as e:
        logger.warning(
            f"[FLEET-VIGILANCE Check 16] Exception fetching QUESTION.md for "
            f"{row.get('story_id', '?')}: {e}"
        )
        return None


# ---------------------------------------------------------------------------
# Clustering
# ---------------------------------------------------------------------------


def _build_clusters(
    questions: dict[str, str],
    threshold: float,
    anchors: list[str],
) -> list[dict]:
    """Build clusters from questions using bigram similarity + anchor phrases.

    AC-4: Jaccard ≥ threshold OR ≥ 2 anchor-phrase matches → same cluster.

    Returns list of cluster dicts, each with:
      story_ids, size, dominant_anchor, sample_text
    """
    story_ids = list(questions.keys())
    if not story_ids:
        return []

    # Normalize all texts
    normalized = {sid: normalize_question_text(txt) for sid, txt in questions.items()}

    # Find anchor matches per story
    anchor_matches = {
        sid: _find_anchor_matches(questions[sid], anchors)
        for sid in story_ids
    }

    # Build adjacency: which stories are similar enough to cluster?
    # Union-Find for clustering
    parent = {sid: sid for sid in story_ids}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i in range(len(story_ids)):
        for j in range(i + 1, len(story_ids)):
            sid_a, sid_b = story_ids[i], story_ids[j]

            # Signal 1: bigram Jaccard similarity
            sim = bigram_jaccard_similarity(normalized[sid_a], normalized[sid_b])
            if sim >= threshold:
                union(sid_a, sid_b)
                continue

            # Signal 2: shared anchor phrases (≥ 2 matches in common)
            shared_anchors = set(anchor_matches[sid_a]) & set(anchor_matches[sid_b])
            if len(shared_anchors) >= 2:
                union(sid_a, sid_b)
                continue

            # AC-4 also: if both have the same single anchor AND some similarity
            if shared_anchors and sim >= (threshold * 0.5):
                union(sid_a, sid_b)

    # Group by root
    groups: dict[str, list[str]] = {}
    for sid in story_ids:
        root = find(sid)
        groups.setdefault(root, []).append(sid)

    # Build cluster dicts (only clusters with size >= 2)
    clusters = []
    for members in groups.values():
        if len(members) < 2:
            continue

        # Find dominant anchor phrase
        anchor_counts: Counter = Counter()
        for sid in members:
            for a in anchor_matches[sid]:
                anchor_counts[a] += 1
        dominant_anchor = anchor_counts.most_common(1)[0][0] if anchor_counts else ""

        # Sample text: first 200 chars of the first member's original question
        first_question = questions[members[0]]
        sample = first_question[:200].strip()

        clusters.append({
            "story_ids": sorted(members),
            "size": len(members),
            "dominant_anchor": dominant_anchor,
            "sample_text": sample,
        })

    # Sort clusters by size descending
    clusters.sort(key=lambda c: c["size"], reverse=True)
    return clusters


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_check(
    fetch_fn,
    run_fn=None,
    suppression_path: str | None = None,
    anchors_path: str | None = None,
    **_kw,
) -> dict:
    """Check 16: needs_info Pattern Detection.

    Parameters
    ----------
    fetch_fn : callable
        (sql, params=None) → list[dict] — returns dispatch_items rows.
    run_fn : callable | None
        subprocess.run replacement (for testing). Defaults to subprocess.run.
    suppression_path : str | None
        Path to needs-info-cluster-suppression.json.
    anchors_path : str | None
        Path to custom anchor phrases file.

    Returns
    -------
    dict with check_id, severity, status_line, dm_payload, dm_suppressed, clusters
    """
    _run = run_fn if run_fn is not None else subprocess.run
    threshold = float(os.environ.get("FV_NEEDS_INFO_THRESHOLD", str(_DEFAULT_THRESHOLD)))
    suppress_hours = float(
        os.environ.get("FV_NEEDS_INFO_SUPPRESS_HOURS", str(_DEFAULT_SUPPRESS_HOURS))
    )

    try:
        # Step 1: Fetch needs_info rows from DB
        # Migrated 2026-05-03 to v2: read from state_current + the latest
        # needs_info event for occurred_at. branch + needs_info_path live in
        # event_data on v2 (set by the agent when emitting needs_info).
        rows = fetch_fn(
            "SELECT j.story_id, j.repo, "
            "       e.event_data->>'branch' AS branch, "
            "       sc.state AS status, "
            "       e.event_data->>'needs_info_path' AS needs_info_path, "
            "       e.occurred_at AS paused_at "
            "FROM dispatch_state_current sc "
            "JOIN dispatch_jobs j ON j.job_id = sc.job_id "
            "JOIN dispatch_v2_events e ON e.event_id = sc.last_event_id "
            "WHERE sc.lane = 'human_queue' "
            "  AND e.event_type = 'needs_info' "
            "  AND e.occurred_at > now() - interval '24 hours'",
        )
    except Exception as e:
        logger.error(f"[FLEET-VIGILANCE Check 16] DB fetch failed: {e}")
        return {
            "check_id": _CHECK_ID,
            "severity": "error_unavailable",
            "status_line": (
                f"[FLEET-VIGILANCE Check 16] error_unavailable: {type(e).__name__}: "
                + str(e)[:200]
            ),
            "dm_payload": None,
            "dm_suppressed": False,
            "clusters": [],
        }

    if not rows:
        status_line = (
            "[FLEET-VIGILANCE Check 16] 0 scanned, 0 clusters, 0 CRIT, 0 suppressed"
        )
        logger.info(status_line)
        return {
            "check_id": _CHECK_ID,
            "severity": "ok",
            "status_line": status_line,
            "dm_payload": None,
            "dm_suppressed": False,
            "clusters": [],
        }

    # Step 2: Fetch QUESTION.md content for each row
    questions: dict[str, str] = {}
    for row in rows:
        sid = row.get("story_id", "")
        content = _fetch_question_content(row, _run)
        if content:
            questions[sid] = content

    n_scanned = len(rows)

    if not questions:
        status_line = (
            f"[FLEET-VIGILANCE Check 16] {n_scanned} scanned, "
            "0 clusters, 0 CRIT, 0 suppressed"
        )
        logger.info(status_line)
        return {
            "check_id": _CHECK_ID,
            "severity": "ok",
            "status_line": status_line,
            "dm_payload": None,
            "dm_suppressed": False,
            "clusters": [],
        }

    # Step 3: Load anchor phrases
    anchors = load_anchor_phrases(anchors_path)

    # Step 4: Build clusters
    clusters = _build_clusters(questions, threshold, anchors)

    # Step 5: Determine severity
    max_cluster_size = max((c["size"] for c in clusters), default=0)

    if max_cluster_size >= 3:
        severity = "crit"
    elif max_cluster_size >= 2:
        severity = "warn"
    else:
        severity = "ok"

    # Step 6: Handle DM and suppression
    n_crit = sum(1 for c in clusters if c["size"] >= 3)
    dm_payload = None
    dm_suppressed = False
    n_suppressed = 0

    if severity == "crit":
        supp, supp_path = _load_suppression(suppression_path)

        # Check each CRIT-level cluster for suppression
        any_unsuppressed = False
        for cluster in clusters:
            if cluster["size"] < 3:
                continue
            sig = _cluster_signature(cluster["story_ids"], cluster["dominant_anchor"])

            if _is_cluster_suppressed(supp, sig, suppress_hours):
                n_suppressed += 1
            else:
                any_unsuppressed = True
                _record_cluster_crit(supp, supp_path, sig)

        if any_unsuppressed:
            # Build DM payload from the largest cluster (AC-6)
            big_cluster = max(clusters, key=lambda c: c["size"])
            dm_story_ids = big_cluster["story_ids"][:5]  # Truncate to 5

            # Find oldest paused_at
            oldest_paused = None
            for row in rows:
                if row.get("story_id") in big_cluster["story_ids"]:
                    pa = row.get("paused_at", "")
                    if pa and (oldest_paused is None or pa < oldest_paused):
                        oldest_paused = pa

            dm_payload = {
                "text": (
                    f"[CRIT] Check 16 needs_info pattern: cluster of "
                    f"{big_cluster['size']} stories share anchor "
                    f"\"{big_cluster['dominant_anchor']}\""
                ),
                "cluster_size": big_cluster["size"],
                "story_ids": dm_story_ids,
                "oldest_paused_at": oldest_paused or "",
                "sample_text": big_cluster["sample_text"][:200],
                "dominant_anchor": big_cluster["dominant_anchor"],
            }
            dm_suppressed = False
        else:
            dm_suppressed = True

    # Step 7: Build status line (AC-9)
    status_line = (
        f"[FLEET-VIGILANCE Check 16] {n_scanned} scanned, "
        f"{len(clusters)} clusters, {n_crit} CRIT, {n_suppressed} suppressed"
    )
    logger.info(status_line)

    return {
        "check_id": _CHECK_ID,
        "severity": severity,
        "status_line": status_line,
        "dm_payload": dm_payload,
        "dm_suppressed": dm_suppressed,
        "clusters": clusters,
    }
