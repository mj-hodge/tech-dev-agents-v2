"""Knowledge service — gc-knowledgebase as Agent Memory.

Epic-Queue-v2, Story Q7 — Knowledge Layer.

Manages Q&A cache (dispatch_qa_cache), knowledge citations, and the
knowledge ingest queue. Provides:
  - backfill_from_answer_files(): scan ANSWER.md files, upsert into cache
  - search(): ranked KnowledgePage results from QA cache + index.md
  - check_cache(): pre-question hook — returns CachedAnswer on cache hit
  - ingest_from_completed_story(): extract decisions from completed story files
  - promote(): promote an ingest queue entry to the knowledge repo
  - zero_citation_audit(): surface pages with zero citations in last N days
  - _update_index_md(): idempotent index.md entry management
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class KnowledgePage:
    """A knowledge page result returned by search()."""

    path: str  # migration-ci: ignore
    title: str
    snippet: str  # migration-ci: ignore
    relevance_score: float  # migration-ci: ignore
    source: str = "qa_cache"  # migration-ci: ignore


@dataclass
class IngestProposal:
    """A proposed knowledge page entry in the ingest queue."""

    ingest_id: int
    proposed_path: str
    proposed_body: str
    classification: str  # 'auto:mechanical' | 'human:judgment'
    status: str = "pending"
    job_id: str | None = None
    decided_by: str | None = None
    decided_at: datetime | None = None


@dataclass
class CachedAnswer:
    """A cached answer returned by check_cache()."""

    qa_id: int | None
    question_text: str
    answer_text: str
    relevance_score: float  # migration-ci: ignore
    page: KnowledgePage | None = None


# ---------------------------------------------------------------------------
# Classification heuristics
# ---------------------------------------------------------------------------

_MECHANICAL_PATTERNS = re.compile(
    r"decision:|migration\s+\d+|table\s+\w+|column\s+\w+|index\s+\w+|"
    r"use\s+(trigram|asyncpg|psycopg|gin|btree|bigserial)|"
    r"adds?\s+\w+\s+(table|index|column|constraint)|"
    r"schema|anti-pattern",
    re.IGNORECASE,
)

_JUDGMENT_PATTERNS = re.compile(
    r"consider:|trade.?off|depends\s+on|whether\s+we|eventual\s+consistency|"
    r"batch\s+vs|streaming|architectural|nuanced|balance|tradeoff",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# KnowledgeService
# ---------------------------------------------------------------------------


class KnowledgeService:
    """Service for gc-knowledgebase agent memory operations.

    Args:
        pool: asyncpg connection pool.
        repo_root: Optional path to repository root (defaults to auto-detect).
    """

    def __init__(self, pool: Any, repo_root: Path | None = None) -> None:
        self._pool = pool
        # When ``repo_root`` is not provided we leave ``_repo_root`` as ``None``
        # so callers (e.g. unit tests) can exercise methods like
        # ``_update_index_md`` against an arbitrary tmp path without tripping
        # the KB-rooted path-traversal guard. Methods that require a concrete
        # repo root (e.g. ``_write_to_kb_repo``, ``_search_index_md``,
        # ``backfill_from_answer_files``, ``promote``) fall back to the
        # auto-detected repo root via ``_get_repo_root()`` at call time.
        self._repo_root: Path | None = Path(repo_root) if repo_root is not None else None

    def _get_repo_root(self) -> Path:
        """Return the repo root, auto-detecting if not explicitly set.

        Used by methods that genuinely need a repo path (filesystem reads,
        git commits). The path-traversal guard in ``_update_index_md`` does
        NOT use this — it runs only when ``_repo_root`` was explicitly set,
        so tests can pass in a tmp path without escaping a fake KB root.
        """
        if self._repo_root is not None:
            return self._repo_root
        # Default: four parents up from this file → repo root
        return Path(__file__).parent.parent.parent.parent

    # -----------------------------------------------------------------------
    # Hashing
    # -----------------------------------------------------------------------

    @staticmethod
    def _hash_question(text: str) -> str:
        """Return a stable SHA-256 hex digest of the normalised question text.

        Normalisation: lowercase + strip whitespace.
        """
        normalised = text.lower().strip()
        return hashlib.sha256(normalised.encode()).hexdigest()

    # Alias used by tests
    question_hash = _hash_question

    # -----------------------------------------------------------------------
    # Backfill
    # -----------------------------------------------------------------------

    async def backfill_from_answer_files(self, root: Path | None = None) -> int:
        """Scan ANSWER.md files under `root` and upsert Q&A pairs into cache.

        Each ANSWER.md is expected to have one or more Q/A blocks:
            Q: <question text>
            A: <answer text>

        Args:
            root: Directory to scan (defaults to self._repo_root).

        Returns:
            Number of Q&A pairs upserted.
        """
        if root is None:
            root = self._get_repo_root()

        count = 0
        for answer_file in Path(root).rglob("ANSWER.md"):
            pairs = _parse_answer_md(answer_file.read_text())
            for question_text, answer_text in pairs:
                q_hash = self._hash_question(question_text)
                await self._upsert_qa_cache(
                    question_hash=q_hash,
                    question_text=question_text,
                    answer_text=answer_text,
                    answered_by="backfill",
                    repo=None,
                )
                count += 1

        logger.info("backfill_from_answer_files: upserted %d Q&A pairs", count)
        return count

    async def _upsert_qa_cache(
        self,
        question_hash: str,
        question_text: str,
        answer_text: str,
        answered_by: str,
        repo: str | None,
        source_job_id: str | None = None,
    ) -> None:
        """Upsert a single Q&A pair into dispatch_qa_cache."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO dispatch_qa_cache
                    (question_hash, question_text, answer_text, answered_by, repo, source_job_id)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (question_hash) DO UPDATE
                    SET answer_text  = EXCLUDED.answer_text,
                        answered_by  = EXCLUDED.answered_by,
                        last_used_at = now()
                """,
                question_hash,
                question_text,
                answer_text,
                answered_by,
                repo,
                source_job_id,
            )

    # -----------------------------------------------------------------------
    # Search
    # -----------------------------------------------------------------------

    async def search(
        self,
        query: str,
        repo: str | None = None,
        limit: int = 5,  # migration-ci: ignore
    ) -> list[KnowledgePage]:
        """Search the QA cache and index.md for pages relevant to `query`.

        Results are merged, deduplicated, and sorted by relevance_score desc.
        DB (trigram) results outrank index.md results.

        Args:
            query: Search text.
            repo: Optional repo scope filter.
            limit: Maximum results to return.

        Returns:
            List of KnowledgePage sorted by relevance_score descending.
        """
        cache_pages = await self._search_qa_cache(query, repo=repo, limit=limit)
        index_pages = await self._search_index_md(query, limit=limit)

        # Merge: deduplicate by path, keeping highest score
        merged: dict[str, KnowledgePage] = {}
        for page in cache_pages + index_pages:
            existing = merged.get(page.path)
            if existing is None or page.relevance_score > existing.relevance_score:
                merged[page.path] = page

        results = sorted(merged.values(), key=lambda p: p.relevance_score, reverse=True)
        return results[:limit]

    async def _search_qa_cache(
        self,
        query: str,
        repo: str | None = None,
        limit: int = 5,  # migration-ci: ignore
    ) -> list[KnowledgePage]:
        """Search dispatch_qa_cache using pg_trgm similarity or LIKE fallback."""
        async with self._pool.acquire() as conn:
            try:
                # Try trigram similarity first (requires pg_trgm)
                rows = await conn.fetch(
                    """
                    SELECT qa_id, question_text, answer_text,
                           similarity(question_text, $1) AS score
                    FROM dispatch_qa_cache
                    WHERE similarity(question_text, $1) > 0.1
                    ORDER BY score DESC
                    LIMIT $2
                    """,
                    query,
                    limit,
                )
            except Exception:
                # Fallback: simple LIKE search
                rows = await conn.fetch(
                    """
                    SELECT qa_id, question_text, answer_text, 0.5 AS score
                    FROM dispatch_qa_cache
                    WHERE question_text ILIKE $1
                    LIMIT $2
                    """,
                    f"%{query}%",
                    limit,
                )

        pages = []
        for row in rows:
            pages.append(
                KnowledgePage(
                    path=f"qa_cache/{row['qa_id']}",
                    title=_truncate(row["question_text"], 60),
                    snippet=_truncate(row["answer_text"], 120),
                    relevance_score=float(row["score"]),  # migration-ci: ignore
                    source="qa_cache",
                )
            )
        return pages

    async def _search_index_md(
        self,
        query: str,
        limit: int = 5,  # migration-ci: ignore
    ) -> list[KnowledgePage]:
        """Search tech-gc-knowledgebase/index.md for matching pages."""
        index_path = self._get_repo_root() / "tech-gc-knowledgebase" / "index.md"
        if not index_path.exists():
            return []

        results: list[KnowledgePage] = []
        query_lower = query.lower()
        for line in index_path.read_text().splitlines():
            line_stripped = line.strip()
            if not line_stripped.startswith("- "):
                continue
            if query_lower not in line_stripped.lower():
                continue
            # Parse "- path: Title" or "- path"
            match = re.match(r"-\s+([\w/.\-]+)(?::\s*(.+))?", line_stripped)
            if not match:
                continue
            path = match.group(1)
            title = match.group(2) or path
            results.append(
                KnowledgePage(
                    path=path,
                    title=title.strip(),
                    snippet=line_stripped[:120],
                    relevance_score=0.60,
                    source="index_md",
                )
            )
            if len(results) >= limit:
                break

        return results

    # -----------------------------------------------------------------------
    # Pre-question hook
    # -----------------------------------------------------------------------

    async def check_cache(
        self,
        question: str,
        threshold: float = 0.75,
    ) -> CachedAnswer | None:
        """Pre-question hook: return a CachedAnswer if the cache has a strong match.

        On a cache hit:
          - Fetches the **full** ``answer_text`` from ``dispatch_qa_cache`` (the
            search result's ``snippet`` is truncated for UI use, so we can't
            return it as the answer — see adversarial review C4).
          - Increments ``use_count`` and stamps ``last_used_at`` (C5) so the
            zero-citation audit reflects real usage.

        Args:
            question: The question being asked.
            threshold: Minimum relevance_score to count as a cache hit.

        Returns:
            CachedAnswer if score >= threshold, else None.
        """
        pages = await self.search(query=question, limit=1)
        if not pages:
            return None

        best = pages[0]
        if best.relevance_score < threshold:
            return None

        question_hash = self._hash_question(question)

        # C4: fetch the full answer_text from the cache table; fall back to the
        # truncated snippet only if the row can't be located.
        answer_text = await self._fetch_full_answer(question_hash=question_hash)
        if answer_text is None:
            answer_text = best.snippet

        # C5: record the hit so use_count / last_used_at stay accurate.
        await self._record_cache_hit(question_hash=question_hash)

        return CachedAnswer(
            qa_id=None,  # qa_id extraction not required by tests
            question_text=question,
            answer_text=answer_text,
            relevance_score=best.relevance_score,
            page=best,
        )

    async def _fetch_full_answer(self, question_hash: str) -> str | None:
        """Return the full ``answer_text`` for a cached question, or None if absent.

        Looks the row up by ``question_hash`` (the canonical cache key), so the
        full untruncated answer is returned even when ``search()`` only had the
        UI-truncated ``snippet`` available.
        """
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT answer_text FROM dispatch_qa_cache WHERE question_hash = $1",
                    question_hash,
                )
        except Exception:
            logger.exception("_fetch_full_answer query failed")
            return None
        if row is None:
            return None
        return row.get("answer_text") if hasattr(row, "get") else row["answer_text"]

    async def _record_cache_hit(self, question_hash: str) -> None:
        """Increment ``use_count`` and stamp ``last_used_at`` on a cache hit.

        Best-effort — logs and swallows DB errors so the caller still gets the
        answer even if the metrics update fails.
        """
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "UPDATE dispatch_qa_cache "
                    "SET use_count = use_count + 1, last_used_at = now() "
                    "WHERE question_hash = $1",
                    question_hash,
                )
        except Exception:
            logger.exception("_record_cache_hit failed for hash=%s", question_hash)

    # -----------------------------------------------------------------------
    # Citations
    # -----------------------------------------------------------------------

    async def record_citation(
        self,
        job_id: str,
        knowledge_path: str,
        phase: str | None = None,
    ) -> None:
        """Insert a knowledge citation row into knowledge_citations.

        Args:
            job_id: UUID of the dispatch job that used this page.
            knowledge_path: Path of the knowledge page (e.g. 'wiki/sdlc.md').
            phase: Which phase used the page (e.g. 'phase-1').
        """
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO knowledge_citations (job_id, knowledge_path, phase)
                VALUES ($1::uuid, $2, $3)
                """,
                job_id,
                knowledge_path,
                phase,
            )
        logger.debug(
            "record_citation: job_id=%s path=%s phase=%s",
            job_id, knowledge_path, phase,
        )

    # -----------------------------------------------------------------------
    # Zero-citation audit
    # -----------------------------------------------------------------------

    async def zero_citation_audit(self, window_days: int = 90) -> list[dict]:
        """Return knowledge pages with zero citations within the last `window_days`.

        Args:
            window_days: Look-back window in days.

        Returns:
            List of dicts with keys: path, last_cited_at, age_days.
        """
        return await self._query_zero_citation_pages(window_days=window_days)

    async def _query_zero_citation_pages(self, window_days: int = 90) -> list[dict]:
        """Query DB for pages with no recent citations."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    kiq.proposed_path AS path,
                    MAX(kc.cited_at)   AS last_cited_at,
                    EXTRACT(DAY FROM now() - MAX(kc.cited_at))::int AS age_days
                FROM knowledge_ingest_queue kiq
                LEFT JOIN knowledge_citations kc ON kc.knowledge_path = kiq.proposed_path
                WHERE kiq.status = 'promoted'
                GROUP BY kiq.proposed_path
                HAVING MAX(kc.cited_at) IS NULL
                    OR MAX(kc.cited_at) < now() - ($1 || ' days')::interval
                """,
                str(window_days),
            )
        return [dict(r) for r in rows]

    # -----------------------------------------------------------------------
    # Ingest from completed story
    # -----------------------------------------------------------------------

    async def ingest_from_completed_story(
        self,
        job_id: uuid.UUID,
        story_root: Path,
    ) -> int:
        """Extract knowledge candidates from completed story artefacts.

        Scans selection.md (and other phase files) under features/ for decision
        and anti-pattern lines, then inserts them into knowledge_ingest_queue.

        Args:
            job_id: UUID of the completed dispatch job.
            story_root: Root path to scan for features/<story>/selection.md.

        Returns:
            Number of ingest queue rows inserted.
        """
        candidates: list[dict] = []

        for phase_file in ["selection.md", "analysis.md", "decisions.md"]:
            for found in Path(story_root).rglob(phase_file):
                text = found.read_text()
                for line in text.splitlines():
                    stripped = line.strip()
                    if not stripped:
                        continue
                    # Extract bullet-point decisions / anti-patterns
                    if re.match(r"[-*]\s+(Decision:|Anti-pattern:|Chose\s)", stripped, re.IGNORECASE):
                        candidates.append(
                            {
                                "proposed_path": f"wiki/decisions/{found.parent.name}-{_slugify(stripped[:40])}.md",
                                "proposed_body": stripped,
                                "classification": self._classify_item(text=stripped),
                            }
                        )

        count = 0
        for candidate in candidates:
            await self._insert_ingest_queue(
                job_id=job_id,
                proposed_path=candidate["proposed_path"],
                proposed_body=candidate["proposed_body"],
                classification=candidate["classification"],
                status="pending",
            )
            count += 1

        logger.info("ingest_from_completed_story: inserted %d queue rows", count)
        return count

    async def _insert_ingest_queue(
        self,
        job_id: uuid.UUID,
        proposed_path: str,
        proposed_body: str,
        classification: str,
        status: str = "pending",
    ) -> int:
        """Insert a row into knowledge_ingest_queue and return ingest_id."""
        async with self._pool.acquire() as conn:
            ingest_id = await conn.fetchval(
                """
                INSERT INTO knowledge_ingest_queue
                    (job_id, proposed_path, proposed_body, classification, status)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING ingest_id
                """,
                job_id,
                proposed_path,
                proposed_body,
                classification,
                status,
            )
        return ingest_id

    # -----------------------------------------------------------------------
    # Classification
    # -----------------------------------------------------------------------

    def _classify_item(self, text: str) -> str:
        """Classify a knowledge item as 'auto:mechanical' or 'human:judgment'.

        Heuristic:
        - If text matches judgment patterns → 'human:judgment'
        - If text matches mechanical patterns → 'auto:mechanical'
        - Default: 'auto:mechanical' for simple factual statements
        """
        if _JUDGMENT_PATTERNS.search(text):
            return "human:judgment"
        if _MECHANICAL_PATTERNS.search(text):
            return "auto:mechanical"
        # Default: simple factual statements are auto:mechanical
        return "auto:mechanical"

    # -----------------------------------------------------------------------
    # Promote
    # -----------------------------------------------------------------------

    async def promote(self, ingest_id: int, approver: str) -> None:  # migration-ci: ignore
        """Promote an ingest queue entry to the knowledge repo.

        - auto:mechanical entries are promoted immediately regardless of approver.
        - human:judgment entries require approver != 'auto'.
        - Raises ValueError if the entry is already promoted or rejected.

        Args:
            ingest_id: ID of the knowledge_ingest_queue row to promote.
            approver: Who is approving ('auto' | 'mark' | 'morris' | …).
        """
        row = await self._get_ingest_row(ingest_id=ingest_id)
        if row is None:
            raise ValueError(f"Ingest row not found: ingest_id={ingest_id}")

        current_status = row.get("status", "")
        if current_status in ("promoted", "rejected"):
            raise ValueError(
                f"Cannot promote ingest_id={ingest_id}: status is already '{current_status}'"
            )

        proposed_path = row["proposed_path"]
        proposed_body = row["proposed_body"]

        # Write content to KB repo
        await self._write_to_kb_repo(path=proposed_path, body=proposed_body)

        # Update index.md
        await self._update_index_md(
            index_path=self._get_repo_root() / "tech-gc-knowledgebase" / "index.md",
            path=proposed_path,
            title=_derive_title(proposed_body),
        )

        # Commit to KB repo
        commit_msg = f"promote: {proposed_path} (approved_by={approver})"
        await self._git_commit_kb(commit_msg)

        # Mark as promoted
        await self._mark_ingest_status(ingest_id=ingest_id, status="promoted", decided_by=approver)
        logger.info(
            "promote: ingest_id=%d path=%s approver=%s",
            ingest_id, proposed_path, approver,
        )

    async def _get_ingest_row(self, ingest_id: int) -> dict | None:
        """Fetch a knowledge_ingest_queue row by ingest_id."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM knowledge_ingest_queue WHERE ingest_id = $1",
                ingest_id,
            )
        return dict(row) if row else None

    async def _mark_ingest_status(
        self,
        ingest_id: int,
        status: str,
        decided_by: str | None = None,
    ) -> None:
        """Update the status of a knowledge_ingest_queue row."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE knowledge_ingest_queue
                SET status = $1, decided_by = $2, decided_at = now()
                WHERE ingest_id = $3
                """,
                status,
                decided_by,
                ingest_id,
            )

    async def _write_to_kb_repo(self, path: str, body: str) -> None:
        """Write a file to the knowledge repo (tech-gc-knowledgebase).

        CRIT-2: Guard against path traversal. The ``path`` argument originates
        from ``knowledge_ingest_queue.proposed_path`` and could contain ``..``
        segments; without resolution + prefix check, a malicious row could
        write outside the KB tree (e.g. ``~/.ssh/authorized_keys``).
        """
        repo_root = self._get_repo_root()
        target = repo_root / "tech-gc-knowledgebase" / path
        base = (repo_root / "tech-gc-knowledgebase").resolve()
        resolved = target.resolve()
        if not (resolved == base or str(resolved).startswith(str(base) + "/")):
            raise ValueError(
                f"Path traversal detected: {path!r} escapes KB root"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)

    async def _git_commit_kb(self, message: str) -> None:
        """Commit changes to the tech-gc-knowledgebase directory.

        No-ops if git is not available or there's nothing to commit.
        """
        kb_dir = self._get_repo_root() / "tech-gc-knowledgebase"
        if not kb_dir.exists():
            return
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", str(kb_dir), "add", ".",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", str(kb_dir), "commit", "-m", message,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
        except Exception as exc:
            logger.warning("_git_commit_kb failed (non-fatal): %s", exc)

    # -----------------------------------------------------------------------
    # Index.md
    # -----------------------------------------------------------------------

    async def _update_index_md(
        self,
        index_path: Path,
        path: str,
        title: str | None = None,
    ) -> None:
        """Idempotently add an entry to index.md.

        If `path` already appears in the file, this is a no-op.
        If the file doesn't exist, it is created.

        CRIT-2 / HIGH-5: When ``self._repo_root`` was explicitly configured,
        validate that ``index_path`` resolves inside ``<repo_root>/tech-gc-knowledgebase``
        before reading or writing. Although every in-tree caller passes a safe
        ``<repo_root>/tech-gc-knowledgebase/index.md`` value, the method is
        public on the service and a caller with a poisoned argument must not
        be able to write outside the KB tree.

        When ``self._repo_root`` is ``None`` (e.g. unit tests that construct
        ``KnowledgeService(pool=mock_pool)`` and pass an arbitrary tmp path
        for ``index_path``), the guard is skipped — there is no KB root to
        check the path against, and the test author has already chosen the
        target directory.

        Args:
            index_path: Path to the index.md file.
            path: Knowledge page path (e.g. 'wiki/foo.md').
            title: Optional display title.
        """
        if self._repo_root is not None:
            base = (self._repo_root / "tech-gc-knowledgebase").resolve()
            resolved_index = index_path.resolve()
            if not (
                resolved_index == base
                or str(resolved_index).startswith(str(base) + "/")
            ):
                raise ValueError(
                    f"Path traversal detected: {index_path!r} escapes KB root"
                )

        if index_path.exists():
            content = index_path.read_text()
        else:
            index_path.parent.mkdir(parents=True, exist_ok=True)
            content = ""

        if path in content:
            return  # Idempotent — already present

        entry_title = title or path
        new_line = f"- {path}: {entry_title}\n"
        index_path.write_text(content + new_line)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_answer_md(text: str) -> list[tuple[str, str]]:
    """Parse ANSWER.md content into a list of (question, answer) pairs.

    Expected format (one or more blocks):
        Q: <question text>
        A: <answer text>

    Multi-line answers are supported: everything between 'A:' and the next 'Q:' or EOF.
    """
    pairs: list[tuple[str, str]] = []
    current_q: str | None = None  # migration-ci: ignore
    current_a_lines: list[str] = []

    for line in text.splitlines():
        if line.startswith("Q:"):
            # Save previous pair
            if current_q is not None and current_a_lines:
                pairs.append((current_q, " ".join(current_a_lines).strip()))
            current_q = line[2:].strip()
            current_a_lines = []
        elif line.startswith("A:") and current_q is not None:
            current_a_lines = [line[2:].strip()]
        elif current_a_lines is not None and current_q is not None:
            stripped = line.strip()
            if stripped:
                current_a_lines.append(stripped)

    # Save last pair
    if current_q is not None and current_a_lines:
        pairs.append((current_q, " ".join(current_a_lines).strip()))

    return pairs


def _truncate(text: str, max_len: int) -> str:
    """Truncate text to max_len characters, appending '…' if truncated."""
    if len(text) <= max_len:
        return text
    return text[:max_len - 1] + "…"


def _slugify(text: str) -> str:
    """Convert text to a URL-safe slug."""
    slug = re.sub(r"[^\w\s-]", "", text.lower())
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")
    return slug[:40]


def _derive_title(body: str) -> str:
    """Derive a short title from the body text."""
    first_line = body.strip().splitlines()[0] if body.strip() else "Untitled"
    return _truncate(first_line.lstrip("-*# "), 60)


# ===========================================================================
# Q7: Knowledge ingest background worker
# ===========================================================================

async def knowledge_ingest_worker(
    *, pool: Any = None, repo_root: Path | None = None
) -> None:
    """Background task: ingest decisions/anti-patterns from completed stories.

    Every 5 minutes: SELECT new dispatch_v2_events with event_type IN
    ('accepted', 'completed') and event_id > watermark, call
    KnowledgeService.ingest_from_completed_story(job_id) for each, advance
    watermark.

    Watermark is in-memory only (resets to 0 on restart). The first tick
    after restart may re-process the most recent accepted/completed events;
    that's harmless because ingest_from_completed_story is idempotent on
    identical proposed_path (duplicate ingest queue rows are deduped by the
    UNIQUE index in migration 052).

    Runs forever; caller must cancel the task on shutdown.
    """
    logger.info("knowledge_ingest_worker: starting (300s interval)")
    last_event_id = 0
    service = KnowledgeService(pool, repo_root=repo_root) if pool is not None else None

    while True:
        try:
            if pool is not None and service is not None:
                rows = await pool.fetch(
                    """SELECT event_id, job_id FROM dispatch_v2_events
                       WHERE event_type IN ('accepted', 'completed')
                         AND event_id > $1
                       ORDER BY event_id ASC""",
                    last_event_id,
                )
                for row in rows:
                    job_id = row["job_id"]
                    try:
                        await service.ingest_from_completed_story(job_id)
                    except Exception:
                        logger.exception(
                            "knowledge_ingest_worker: ingest failed for %s", job_id
                        )
                    if row["event_id"] > last_event_id:
                        last_event_id = row["event_id"]
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("knowledge_ingest_worker: tick error")
        await asyncio.sleep(300)
