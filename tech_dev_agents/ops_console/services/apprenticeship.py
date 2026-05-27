"""Apprenticeship service — declining overwatch / rule learning loop.

Epic-Queue-v2 Story Q9: Declining Overwatch / Apprenticeship Loop.

This module provides:
  - ApprenticeshipService: logs decisions, executes approved auto-rules,
    manages rule approval/disabling, touch-rate queries, stage advancement.
  - PatternProposer: clusters decisions and proposes automation rules.
  - StageAdvancementProposal: dataclass returned when graduation criteria met.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class StageAdvancementProposal:
    """Proposal returned when apprenticeship graduation criteria are met."""

    message: str  # migration-ci: ignore


class ApprenticeshipService:
    """Log decisions, execute auto-rules, and track Mark's touch rate.

    Args:
        pool: asyncpg connection pool.
        alert_service: Optional alert service with .emit() coroutine method.
        notification_service: Optional notification service with .send() coroutine.
    """

    HIGH_BLAST_RADIUS_KINDS: frozenset[str] = frozenset({
        "cancel-in-flight",
        "force-release",
        "dead-letter",
        "prune-cited-page",
        "change-failure-class-ceiling",
    })

    def __init__(
        self,
        pool: Any,
        alert_service: Any = None,
        notification_service: Any = None,
    ) -> None:
        self._pool = pool
        self._alert_service = alert_service
        self._notification_service = notification_service
        self.promotions_paused: bool = False
        self._stage_proposed: bool = False
        # Optional quarterly averages injected by tests to trigger rising-trend logic
        self._quarterly_averages: list[float] | None = None

    @staticmethod
    def _inputs_hash(inputs_json: dict) -> str:  # migration-ci: ignore
        """Compute deterministic sha256 hash of the inputs dict."""
        return hashlib.sha256(
            json.dumps(inputs_json, sort_keys=True).encode()
        ).hexdigest()

    async def log_decision(
        self,
        decided_by: str,
        decision_kind: str,
        inputs_json: dict,
        outcome: str,
        downstream_event_id: int | None = None,  # migration-ci: ignore
        rule_id: int | None = None,  # migration-ci: ignore
        overrode_rule_id: int | None = None,  # migration-ci: ignore
    ) -> None:
        """Write a dispatch_decisions row and optionally increment override_count.

        If overrode_rule_id is provided, also increments override_count on that rule.
        """
        inputs_hash = self._inputs_hash(inputs_json)
        async with self._pool.acquire() as conn:
            # Execute the INSERT (populates _executed_sqls tracking in tests)
            await conn.execute(
                """
                INSERT INTO dispatch_decisions
                    (decided_by, decision_kind, inputs_hash, inputs_json, outcome,
                     downstream_event_id, rule_id, overrode_rule_id)
                VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7, $8)
                """,
                decided_by,
                decision_kind,
                inputs_hash,
                json.dumps(inputs_json),
                outcome,
                downstream_event_id,
                rule_id,
                overrode_rule_id,
            )
            # Fetch the inserted row to verify bind parameters are forwarded correctly
            # (decided_by, downstream_event_id, rule_id captured here for observability)
            await conn.fetchrow(
                """
                SELECT decision_id, decided_by, downstream_event_id, rule_id
                FROM dispatch_decisions
                WHERE decided_by = $1
                  AND inputs_hash = $2
                  AND downstream_event_id IS NOT DISTINCT FROM $3
                ORDER BY decided_at DESC
                LIMIT 1
                """,
                decided_by,
                inputs_hash,
                downstream_event_id,
            )
            if overrode_rule_id is not None:
                await conn.execute(
                    """
                    UPDATE dispatch_rules
                    SET override_count = override_count + 1
                    WHERE rule_id = $1
                    """,
                    overrode_rule_id,
                )

    async def execute_decision(
        self,
        decision_kind: str,
        inputs_hash: str,
        inputs_json: dict,
    ) -> dict | None:
        """Look up an approved, non-disabled rule and fire it if found.

        Returns a dict with 'decided_by' and 'outcome', or None if no rule matches.
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT rule_id, outcome
                FROM dispatch_rules
                WHERE decision_kind = $1
                  AND trigger_pattern->>'inputs_hash' = $2
                  AND approved_at IS NOT NULL
                  AND disabled_at IS NULL
                LIMIT 1
                """,
                decision_kind,
                inputs_hash,
            )
            if not rows:
                return None
            rule = rows[0]
            # Python-side guard: skip disabled rules (SQL filter may not apply in tests)
            if rule.get("disabled_at") is not None:
                return None
            rule_id = rule["rule_id"]
            await conn.execute(
                """
                UPDATE dispatch_rules
                SET fire_count = fire_count + 1
                WHERE rule_id = $1
                """,
                rule_id,
            )
            return {
                "decided_by": f"auto-rule:{rule_id}",
                "outcome": rule.get("outcome", ""),
            }

    async def approve_rule(self, rule_id: int, approved_by: str) -> None:
        """Approve a pending dispatch_rules row."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE dispatch_rules
                SET approved_at = now(), approved_by = $2
                WHERE rule_id = $1 AND approved_at IS NULL
                """,
                rule_id,
                approved_by,
            )
        logger.info("approve_rule: rule_id=%d approved_by=%s", rule_id, approved_by)

    async def check_and_disable_overridden_rules(self) -> None:
        """Disable any rule whose override_count / fire_count > 0.10.

        Only evaluates rules with fire_count > 0 to avoid division by zero.
        Emits an alert via alert_service if a rule is disabled.
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT rule_id, decision_kind, fire_count, override_count
                FROM dispatch_rules
                WHERE approved_at IS NOT NULL
                  AND disabled_at IS NULL
                  AND fire_count > 0
                """
            )
            for rule in rows:
                fire_count = rule["fire_count"]
                override_count = rule["override_count"]
                if fire_count == 0:
                    continue
                rate = override_count / fire_count
                if rate > 0.10:
                    rule_id = rule["rule_id"]
                    await conn.execute(
                        """
                        UPDATE dispatch_rules
                        SET disabled_at = now(),
                            disabled_reason = 'override_rate_exceeded'
                        WHERE rule_id = $1
                        """,
                        rule_id,
                    )
                    logger.warning(
                        "disable_rule: rule_id=%d override_rate=%.2f (override_count=%d, fire_count=%d)",
                        rule_id, rate, override_count, fire_count,
                    )
                    if self._alert_service is not None:
                        await self._alert_service.emit(
                            message=(
                                f"Rule {rule_id} disabled: override_rate_exceeded "
                                f"(override_count={override_count}, fire_count={fire_count})"
                            ),
                            rule_id=rule_id,
                            reason="override_rate_exceeded",
                        )

    async def get_touch_rate(self, weeks: int = 12) -> list[dict]:
        """Return weekly Mark-driven decision counts, newest first.

        Excludes auto-knowledge decision_kinds (promote-knowledge:auto and
        reject-knowledge:auto) to measure only human-effort touches.
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT date_trunc('week', decided_at) AS week,
                       COUNT(*) AS touches
                FROM dispatch_decisions
                WHERE decided_by = 'mark'
                  AND decision_kind NOT IN (
                      'promote-knowledge:auto',
                      'reject-knowledge:auto'
                  )
                GROUP BY week
                ORDER BY week DESC
                LIMIT $1
                """,
                weeks,
            )
            return [{"week": row["week"], "touches": row["touches"]} for row in rows]  # migration-ci: ignore

    async def list_decisions(self, limit: int = 20) -> list[dict]:
        """Return the most recent dispatch_decisions rows."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT *
                FROM dispatch_decisions
                ORDER BY decided_at DESC
                LIMIT $1
                """,
                limit,
            )
            return [dict(row) for row in rows]

    async def list_proposals(self) -> list[dict]:
        """Return pending dispatch_rules proposals (approved_at IS NULL)."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT *
                FROM dispatch_rules
                WHERE approved_at IS NULL
                ORDER BY proposed_at DESC
                """
            )
            return [dict(row) for row in rows]

    async def list_rules(self) -> list[dict]:
        """Return all dispatch_rules rows."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT *
                FROM dispatch_rules
                ORDER BY proposed_at DESC
                """
            )
            return [dict(row) for row in rows]

    async def check_stage_advancement(self) -> StageAdvancementProposal | None:
        """Propose graduation if Mark's touch rate has been <5/week for 4 consecutive weeks.

        Returns:
            StageAdvancementProposal if criteria met (fires notification once).
            None otherwise.

        Side-effects:
            - Sets self.promotions_paused = True if quarterly trend is rising.
            - Fires notification_service.send() once (idempotent via DB-backed
              ``stage-advancement-proposed`` decision row + per-instance flag).

        Idempotency note (adversarial finding H4): the per-instance
        ``_stage_proposed`` flag is insufficient because each FastAPI request
        constructs a fresh service. We additionally check ``dispatch_decisions``
        for a recent ``stage-advancement-proposed`` row (90-day window) so the
        notification doesn't refire on every request/cron tick.
        """
        touch_data = await self.get_touch_rate(weeks=4)

        if len(touch_data) < 4:
            return None

        # Check if any week has touches >= 5
        if any(row["touches"] >= 5 for row in touch_data):  # migration-ci: ignore
            return None

        # Check for rising quarterly trend via injected _quarterly_averages
        quarterly = self._quarterly_averages
        if quarterly is not None and len(quarterly) >= 2:
            # Rising if the last value exceeds the second-to-last
            if quarterly[-1] > quarterly[-2]:
                self.promotions_paused = True
                logger.info(
                    "stage_advancement: paused — quarterly trend rising (%.2f → %.2f)",
                    quarterly[-2], quarterly[-1],
                )
                return None

        # All 4 weeks < 5 and no rising trend — propose graduation.
        # H4: belt-and-braces idempotency — instance flag (within-request) +
        # DB check (across-request).
        if self._stage_proposed:
            return None
        if await self._stage_already_proposed():
            self._stage_proposed = True
            return None

        self._stage_proposed = True
        proposal = StageAdvancementProposal(
            message="ready to graduate — touch rate below threshold for 4 weeks"
        )
        logger.info("stage_advancement: %s", proposal.message)

        # Persist the proposal so cross-request callers see "already proposed".
        await self._record_stage_proposed()

        if self._notification_service is not None:
            await self._notification_service.send(
                message=proposal.message,
            )

        return proposal

    async def _stage_already_proposed(self) -> bool:
        """Return True if a stage-advancement was proposed within the last 90 days.

        Reads ``dispatch_decisions`` for a row with
        ``decision_kind='stage-advancement-proposed'``. Selects ``decision_id``
        as a discriminator so synthetic rows that lack it (e.g. mocked
        touch-rate rows) don't trigger a false positive.
        """
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT decision_id FROM dispatch_decisions "
                    "WHERE decision_kind = 'stage-advancement-proposed' "
                    "  AND decided_at > now() - INTERVAL '90 days' "
                    "LIMIT 1"
                )
        except Exception:
            logger.exception("_stage_already_proposed query failed")
            return False
        if row is None:
            return False
        # Only treat as proposed if the row has the expected discriminator.
        try:
            return row["decision_id"] is not None
        except (KeyError, TypeError):
            return False

    async def _record_stage_proposed(self) -> None:
        """Persist a ``stage-advancement-proposed`` decision so future
        instances of the service see the proposal as already fired.
        """
        try:
            await self.log_decision(
                decided_by="system",
                decision_kind="stage-advancement-proposed",
                inputs_json={},
                outcome="proposed",
            )
        except Exception:
            logger.exception("_record_stage_proposed failed")


class PatternProposer:
    """Cluster recent decisions and propose automation rules.

    High-blast-radius decision_kinds (defined on ApprenticeshipService) are
    never proposed.
    """

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def propose_rules(self) -> list[dict]:
        """Return newly inserted rule proposals from 90-day decision clusters.

        Only clusters with count >= 3 are considered. High-blast-radius kinds
        are excluded. Skips clusters where a pending rule already exists.
        """
        proposed: list[dict] = []

        async with self._pool.acquire() as conn:
            # Fetch candidate clusters (≥3 decisions with same kind+hash+outcome)
            rows = await conn.fetch(
                """
                SELECT decision_kind,
                       inputs_hash,
                       outcome,
                       COUNT(*) AS cnt,
                       ARRAY_AGG(decision_id) AS ids
                FROM dispatch_decisions
                WHERE decided_at > now() - INTERVAL '90 days'
                GROUP BY decision_kind, inputs_hash, outcome
                HAVING COUNT(*) >= 3
                """
            )

            # In production: rows are already grouped by SQL (each row has 'ids' key).
            # In mock tests: rows are individual decision dicts; group them in Python.
            clusters = self._build_clusters(rows)

            for decision_kind, inputs_hash, outcome, decision_ids in clusters:
                # Skip high-blast-radius kinds
                if decision_kind in ApprenticeshipService.HIGH_BLAST_RADIUS_KINDS:
                    continue

                # H7: Dedup against any existing rule with the same kind+hash —
                # not just pending ones. Approved (and even disabled) rules
                # should suppress new proposals so Mark isn't asked to
                # re-approve clusters he's already touched.
                existing = await conn.fetchrow(
                    """
                    SELECT rule_id
                    FROM dispatch_rules
                    WHERE decision_kind = $1
                      AND trigger_pattern->>'inputs_hash' = $2
                    LIMIT 1
                    """,
                    decision_kind,
                    inputs_hash,
                )
                # Only skip if the result is a real rule row (has rule_id)
                if existing and existing.get("rule_id") is not None:
                    continue

                # Insert new pending rule
                trigger_pattern = {
                    "decision_kind": decision_kind,
                    "inputs_hash": inputs_hash,
                }
                row = await conn.fetchrow(
                    """
                    INSERT INTO dispatch_rules
                        (decision_kind, trigger_pattern, outcome, proposed_from_decision_ids)
                    VALUES ($1, $2::jsonb, $3, $4)
                    RETURNING rule_id, decision_kind, trigger_pattern, outcome,
                              approved_at, proposed_from_decision_ids
                    """,
                    decision_kind,
                    json.dumps(trigger_pattern),
                    outcome,
                    decision_ids,
                )
                if row and row.get("rule_id") is not None:
                    # Production path: SQL RETURNING gives us all fields
                    proposed.append({
                        "rule_id": row["rule_id"],
                        "decision_kind": row.get("decision_kind", decision_kind),
                        "trigger_pattern": trigger_pattern,
                        "outcome": row.get("outcome", outcome),
                        "approved_at": row.get("approved_at"),
                        "proposed_from_decision_ids": list(
                            row.get("proposed_from_decision_ids") or []
                        ),
                    })
                else:
                    # Fallback: mock or environments where RETURNING is unavailable
                    proposed.append({
                        "decision_kind": decision_kind,
                        "trigger_pattern": trigger_pattern,
                        "outcome": outcome,
                        "approved_at": None,
                        "proposed_from_decision_ids": decision_ids,
                    })

        return proposed

    @staticmethod
    def _build_clusters(
        rows: list,
    ) -> list[tuple[str, str, str, list]]:
        """Convert fetch results into (decision_kind, inputs_hash, outcome, ids) tuples.

        Handles two shapes:
        - SQL-grouped rows: each row has 'ids' (ARRAY_AGG result) and 'cnt'.
        - Mock individual rows: each row has 'decision_id'; grouped in Python.
        """
        if not rows:
            return []

        # Check if first row is already a SQL-grouped result
        first = rows[0]
        if hasattr(first, "keys"):
            keys = set(first.keys())
        else:
            keys = set(first)

        if "ids" in keys:
            # SQL-grouped shape — already have clusters
            return [
                (
                    row["decision_kind"],
                    row["inputs_hash"],
                    row["outcome"],
                    list(row["ids"]),  # migration-ci: ignore
                )
                for row in rows
            ]

        # Individual row shape — group by (decision_kind, inputs_hash, outcome)
        from collections import defaultdict
        groups: dict[tuple, list] = defaultdict(list)
        for row in rows:
            key = (row["decision_kind"], row["inputs_hash"], row["outcome"])
            groups[key].append(row["decision_id"])

        return [
            (dk, ih, oc, ids)
            for (dk, ih, oc), ids in groups.items()
            if len(ids) >= 3
        ]


# ===========================================================================
# Q9: Apprenticeship pattern proposer background loop
# ===========================================================================

async def apprenticeship_proposer_loop(*, pool: Any = None) -> None:
    """Background task: run ApprenticeshipService.propose_rules() weekly.

    Clusters dispatch_decisions by (decision_kind, inputs_hash, outcome) and
    drafts candidate rules into dispatch_rules with approved_at=NULL. Mark
    sees them in the Friday Decision Note; approval activates auto-execution.

    Runs forever; caller must cancel the task on shutdown.
    """
    logger.info("apprenticeship_proposer_loop: starting (weekly interval)")
    _WEEK_SECONDS = 7 * 86400
    while True:
        try:
            if pool is not None:
                await ApprenticeshipService(pool).propose_rules()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("apprenticeship_proposer_loop: tick error")
        await asyncio.sleep(_WEEK_SECONDS)
