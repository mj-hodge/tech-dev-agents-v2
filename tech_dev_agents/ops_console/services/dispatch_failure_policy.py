"""Dispatch failure policy service — Q3 centralized retry/routing policy.

Epic-Queue-v2, Story Q3 — Centralized Retry/Failure Policy + DLQ.

This module provides:
  - POLICY_TABLE     — module-level dict mirroring dispatch_failure_policy rows
  - classify()       — map (failure_reason, exit_code, error_message) → failure_class
  - apply()          — look up policy for a failure_class and emit routing event
  - DispatchFailurePolicyService — class wrapping classify() + apply() with a pool

Circular-import constraint: this module MUST NOT import from dispatch_v2.py.
It may import from dispatch_v2_service.py only.

Design decisions:
  D3: POLICY_TABLE is a module-level constant so unit tests (Groups B, F, G, H)
      can run without a live DB connection.
  D6: Tests in Group B use patching of _lookup_policy and record_event to avoid
      requiring PostgreSQL in CI.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# POLICY_TABLE — module-level constant
#
# Mirrors dispatch_failure_policy DB rows.  This is the in-process fast-path
# for classify() and for unit tests that mock _lookup_policy.
#
# Keys: failure_class (str)
# Values: dict with keys: retryable, max_attempts, cooldown_sec, next_lane
# ---------------------------------------------------------------------------

POLICY_TABLE: dict[str, dict[str, Any]] = {
    "argparse_reject": {
        "retryable": False,
        "max_attempts": 0,
        "cooldown_sec": 0,
        "next_lane": "attention_queue",
    },
    "rate_limited": {
        "retryable": True,
        "max_attempts": 5,
        "cooldown_sec": 300,
        "next_lane": "work_queue",
    },
    "branch_setup_failed": {
        "retryable": True,
        "max_attempts": 3,
        "cooldown_sec": 60,
        "next_lane": "work_queue",
    },
    # STORY-859: rebase conflicts almost always need human resolution.
    # Non-retryable so a single dispatch doesn't burn 3 attempts each ~$2-5
    # of agent tokens before reaching attention queue. A duplicate key
    # (2026-05) silently overrode this to retryable/work_queue and caused
    # a redispatch storm — see test_policy_table_has_no_duplicate_keys.
    "git_rebase_failed": {
        "retryable": False,
        "max_attempts": 0,
        "cooldown_sec": 0,
        "next_lane": "attention_queue",
    },
    "needs_info_unanswered": {
        "retryable": False,
        "max_attempts": 0,
        "cooldown_sec": 0,
        "next_lane": "attention_queue",
    },
    "sdk_died_silent": {
        "retryable": True,
        "max_attempts": 3,
        "cooldown_sec": 120,
        "next_lane": "work_queue",
    },
    "phase_runner_crash": {
        "retryable": True,
        "max_attempts": 3,
        "cooldown_sec": 30,
        "next_lane": "work_queue",
    },
    # STORY-857a expansion: richer classes for real-world failure modes.
    # Order in _CLASSIFY_PATTERNS ensures these match before phase_runner_crash.
    "lease_lost": {
        "retryable": True,
        "max_attempts": 2,
        "cooldown_sec": 60,
        "next_lane": "work_queue",
    },
    "workspace_missing": {
        "retryable": False,
        "max_attempts": 0,
        "cooldown_sec": 0,
        "next_lane": "attention_queue",
    },
    "auth_expired": {
        "retryable": False,
        "max_attempts": 0,
        "cooldown_sec": 0,
        "next_lane": "attention_queue",
    },
    "quota_exceeded": {
        "retryable": True,
        "max_attempts": 1,
        "cooldown_sec": 1800,
        "next_lane": "work_queue",
    },
    "sigterm_shutdown": {
        "retryable": True,
        "max_attempts": 3,
        "cooldown_sec": 60,
        "next_lane": "work_queue",
    },
    "git_push_failed": {
        "retryable": True,
        "max_attempts": 2,
        "cooldown_sec": 60,
        "next_lane": "work_queue",
    },
    "code_test_red": {
        "retryable": False,
        "max_attempts": 0,
        "cooldown_sec": 0,
        "next_lane": "attention_queue",
    },
    "adversarial_block": {
        "retryable": False,
        "max_attempts": 0,
        "cooldown_sec": 0,
        "next_lane": "attention_queue",
    },
    "dependency_missing": {
        "retryable": False,
        "max_attempts": 0,
        "cooldown_sec": 0,
        "next_lane": "quarantined",
    },
    "unknown": {
        "retryable": False,
        "max_attempts": 0,
        "cooldown_sec": 0,
        "next_lane": "attention_queue",
    },
    # Q8 extension classes
    "agent_stuck_question_budget_exceeded": {
        "retryable": False,
        "max_attempts": 0,
        "cooldown_sec": 0,
        "next_lane": "attention_queue",
    },
    "agent_repetition": {
        "retryable": False,
        "max_attempts": 0,
        "cooldown_sec": 0,
        "next_lane": "attention_queue",
    },
    "phase_overrun": {
        "retryable": False,
        "max_attempts": 0,
        "cooldown_sec": 0,
        "next_lane": "attention_queue",
    },
    "agent_flapping": {
        "retryable": False,
        "max_attempts": 0,
        "cooldown_sec": 0,
        "next_lane": "attention_queue",
    },
    "dependency_unresolved_7d": {
        "retryable": False,
        "max_attempts": 0,
        "cooldown_sec": 0,
        "next_lane": "dead_letter",
    },
    # STORY-860: Phase-scoped failure classes for v2 orchestration.
    # These are set directly by the orchestrator (not the regex classifier)
    # based on which phase failed. The inner classifier (rate_limited, etc.)
    # takes precedence when it matches a specific pattern.
    "phase_1_seed_error": {
        "retryable": True,
        "max_attempts": 2,
        "cooldown_sec": 60,
        "next_lane": "work_queue",
    },
    "phase_2_research_error": {
        "retryable": True,
        "max_attempts": 2,
        "cooldown_sec": 60,
        "next_lane": "work_queue",
    },
    "phase_3_expansion_error": {
        "retryable": True,
        "max_attempts": 2,
        "cooldown_sec": 60,
        "next_lane": "work_queue",
    },
    "phase_4_analysis_error": {
        "retryable": True,
        "max_attempts": 2,
        "cooldown_sec": 60,
        "next_lane": "work_queue",
    },
    "phase_5_selection_error": {
        "retryable": True,
        "max_attempts": 2,
        "cooldown_sec": 60,
        "next_lane": "work_queue",
    },
    "phase_6_design_error": {
        "retryable": True,
        "max_attempts": 2,
        "cooldown_sec": 60,
        "next_lane": "work_queue",
    },
    "phase_7_test_red": {
        "retryable": False,
        "max_attempts": 0,
        "cooldown_sec": 0,
        "next_lane": "attention_queue",
    },
    "phase_8_impl_fail": {
        "retryable": True,
        "max_attempts": 2,
        "cooldown_sec": 60,
        "next_lane": "work_queue",
    },
    "phase_9_refinement_error": {
        "retryable": True,
        "max_attempts": 2,
        "cooldown_sec": 60,
        "next_lane": "work_queue",
    },
    "phase_10_ops_error": {
        "retryable": True,
        "max_attempts": 2,
        "cooldown_sec": 60,
        "next_lane": "work_queue",
    },
    "git_branch_setup_failed": {
        "retryable": True,
        "max_attempts": 3,
        "cooldown_sec": 60,
        "next_lane": "work_queue",
    },
    "git_workspace_dirty": {
        "retryable": False,
        "max_attempts": 0,
        "cooldown_sec": 0,
        "next_lane": "attention_queue",
    },
    # STORY-WATCHDOG-2026-05-25 — SDK progress watchdog classes.
    #
    # On 2026-05-25 the fleet wedged because long-running SDK subprocesses
    # stopped producing output (silent stalls) and in some cases the lease
    # was lost (409) but the SDK happily kept burning tokens until the 2h
    # proc.communicate timeout fired. The StuckAgentWatcher reclaim path
    # then re-fired the same poison stories on every agent in turn, costing
    # ~$XX in agent tokens before manual intervention.
    #
    # These four classes MUST be non-retryable: if a story triggers the
    # watchdog on one agent, retrying it on a different agent will almost
    # certainly trigger the same stall and burn more tokens. Route to
    # attention_queue so Mark can decide whether to rework, requeue, or
    # close.
    "watchdog_progress_stall": {
        "retryable": False,
        "max_attempts": 0,
        "cooldown_sec": 0,
        "next_lane": "attention_queue",
    },
    "watchdog_lease_lost": {
        "retryable": False,
        "max_attempts": 0,
        "cooldown_sec": 0,
        "next_lane": "attention_queue",
    },
    "watchdog_oom_guard": {
        "retryable": False,
        "max_attempts": 0,
        "cooldown_sec": 0,
        "next_lane": "attention_queue",
    },
    "watchdog_turn_max": {
        "retryable": False,
        "max_attempts": 0,
        "cooldown_sec": 0,
        "next_lane": "attention_queue",
    },
}

# ---------------------------------------------------------------------------
# STORY-872: policy_routed wrapper helper
#
# apply() writes failure_reason = "policy_routed: <failure_class>" when routing
# a job to attention_queue.  If that string later re-enters classify() (e.g. on
# re-dispatch), we recover the inner class rather than falling to 'unknown'.
# ---------------------------------------------------------------------------


def _extract_policy_routed_class(combined: str) -> str | None:
    """Unwrap 'policy_routed: <class>' failure_reasons written by apply().

    Returns the inner class if it is a recognised key in POLICY_TABLE, else None.
    Unrecognised inner classes fall through to the normal pattern loop.
    """
    m = re.search(r"policy_routed:\s*(\w+)", combined, re.I)
    if m:
        inner = m.group(1)
        if inner in POLICY_TABLE:
            return inner
    return None


# ---------------------------------------------------------------------------
# Pattern classifiers
#
# Order matters: more specific patterns first.
# ---------------------------------------------------------------------------

_CLASSIFY_PATTERNS: list[tuple[re.Pattern, str]] = [
    # STORY-WATCHDOG-2026-05-25 — watchdog trip classes must match FIRST.
    # The poller prefixes its output with "[watchdog_<trip>]" when the
    # progress watchdog fires; these patterns recover the trip class so
    # the failure routes non-retryable to attention_queue.
    (re.compile(r"watchdog_progress_stall", re.I), "watchdog_progress_stall"),
    (re.compile(r"watchdog_lease_lost", re.I), "watchdog_lease_lost"),
    (re.compile(r"watchdog_oom_guard", re.I), "watchdog_oom_guard"),
    (re.compile(r"watchdog_turn_max", re.I), "watchdog_turn_max"),
    (re.compile(r"argparse|argument\s+parser|unrecognized\s+argument", re.I), "argparse_reject"),
    (re.compile(r"rate\s*limit|429|too\s+many\s+requests|throttl", re.I), "rate_limited"),
    # STORY-857a — auth_expired must beat the generic patterns (401/403 etc.)
    (re.compile(r"401|403|invalid.*token|authentication.*failed|unauthorized", re.I), "auth_expired"),
    # STORY-857a — quota_exceeded covers spend/usage limits not caught by rate_limited above
    (re.compile(r"quota.*exceeded|spend.*limit|usage.*limit", re.I), "quota_exceeded"),
    # STORY-872 — Claude CLI quota ceiling: "You've hit your limit · resets <time>"
    # The dispatch poller already uses this string for pause-flag logic; the classifier
    # must also catch it so failures route correctly instead of falling to unknown.
    (
        re.compile(r"hit.{0,10}(your\s+)?limit|you.?ve\s+hit|resets\s+(at|in)\b", re.I),
        "quota_exceeded",
    ),
    # STORY-872 — SDK / API capacity exits: Anthropic 529, "overloaded", context-window.
    # These are transient capacity limits (not rate limits). Map to quota_exceeded
    # (retryable=True, cooldown_sec=1800, max_attempts=1) — correct recovery policy.
    (
        re.compile(
            r"529|api.*overload|overload.*api|anthropic.*overload"
            r"|context.{0,15}(window|length).{0,10}exceeded"
            r"|maximum.{0,20}context.{0,20}length"
            r"|context.{0,10}too.{0,10}long",
            re.I,
        ),
        "quota_exceeded",
    ),
    # STORY-857a — workspace_missing must precede the broad branch_setup pattern
    (re.compile(r"workspace.{0,20}not\s+found|no\s+such\s+file.*\.git|workspace.*missing|cannot.*cd.*workspace", re.I), "workspace_missing"),
    # STORY-857a — lease lost / restart victim
    (re.compile(r"lease.{0,20}(lost|stale|expired)|409.*lease|stale\s+lease\s+detected", re.I), "lease_lost"),
    # STORY-857a — git push failures (must precede branch_setup since both touch git)
    (re.compile(r"git\s+push.*fail|git\s+push.*reject|non-fast-forward|push.*conflict", re.I), "git_push_failed"),
    # STORY-859: git_rebase_failed must precede branch_setup_failed because rebase
    # conflict messages mention "branch" incidentally. The poller emits this class
    # directly; this regex covers stray log paths that still reach classify().
    (re.compile(r"git\s+rebase.*fail|conflict\s+\(content\)|rebase\s+aborted|merge\s+conflict|cannot\s+rebase", re.I), "git_rebase_failed"),
    (re.compile(r"branch.*setup|git.*checkout|git.*branch|setup.*branch|branch.{0,20}(setup|create).*fail|fatal:.*branch|cannot\s+create\s+branch", re.I), "branch_setup_failed"),
    (re.compile(r"needs.?info.*unanswered|needs.?info.*ttl|24h.*ttl|ttl.*exceeded", re.I), "needs_info_unanswered"),
    # STORY-857a — sigterm/graceful shutdown is more specific than sdk_died_silent
    (re.compile(r"sigterm|signal\s+15|terminated\s+by\s+signal|graceful\s+shutdown", re.I), "sigterm_shutdown"),
    (re.compile(r"sdk.*died|sdk.*silent|silent.*failure|process.*kill|oom|killed.*kernel", re.I), "sdk_died_silent"),
    (re.compile(r"phase.*runner.*crash|runner.*crash|unhandled.*exception", re.I), "phase_runner_crash"),
    (re.compile(r"test.*red|test.*fail|tests.*fail|pytest.*fail|assertion.*error", re.I), "code_test_red"),
    (re.compile(r"adversarial|safety.*block|blocked.*safety|content.*policy|refused", re.I), "adversarial_block"),
    (re.compile(r"dependency.*missing|missing.*dependency|blocked.*dependency|depends.*on", re.I), "dependency_missing"),
    # Q8 extension patterns
    (re.compile(r"question.*budget.*exceeded|budget.*exceeded.*question", re.I), "agent_stuck_question_budget_exceeded"),
    (re.compile(r"repetition.*loop|repeated.*same.*action|agent.*repeat", re.I), "agent_repetition"),
    (re.compile(r"phase.*overrun|overrun.*phase|budget.*ceiling|token.*ceiling", re.I), "phase_overrun"),
    (re.compile(r"agent.*flapping|flapping.*agent|oscillat", re.I), "agent_flapping"),
    (re.compile(r"dependency.*unresolved.*7d|7d.*unresolved|7.*day.*unresolved", re.I), "dependency_unresolved_7d"),
]


def classify(
    failure_reason: str,
    exit_code: int | None = None,  # migration-ci: ignore
    error_message: str | None = None,  # migration-ci: ignore
) -> str:
    """Classify a failure into a known failure_class.

    Parameters
    ----------
    failure_reason : str
        Primary failure description (from agent or poller).
    exit_code : int | None  # migration-ci: ignore
        Process exit code, if available.
    error_message : str | None  # migration-ci: ignore
        Additional error text (stderr, exception message).

    Returns
    -------
    str
        One of the keys in POLICY_TABLE. Falls back to 'unknown' if no
        pattern matches.
    """
    combined = " ".join(filter(None, [failure_reason, error_message or ""]))
    if not combined.strip():
        return "unknown"

    # STORY-872: unwrap "policy_routed: <class>" wrappers written by apply().
    # This must run before the pattern loop so the inner class is recovered
    # without needing a dedicated pattern entry per class.
    inner = _extract_policy_routed_class(combined)
    if inner:
        return inner

    for pattern, failure_class in _CLASSIFY_PATTERNS:
        if pattern.search(combined):
            return failure_class

    # STORY-857a: fall through is 'unknown' (NOT 'phase_runner_crash').
    # unknown is non-retryable on purpose (avoids 3-attempt waste on
    # genuinely opaque crashes). Previously, callers would default unmatched
    # output to 'phase_runner_crash' which is retryable=True, max_attempts=3 —
    # genuinely-broken jobs burned 3 retries before reaching attention_queue.
    return "unknown"


# ---------------------------------------------------------------------------
# Module-level record_event
#
# Delegates to dispatch_v2_service.DispatchV2Service.record_event() using
# the provided pool.  Defined as a top-level async function so tests can
# patch it at 'dispatch_failure_policy.record_event'.
# ---------------------------------------------------------------------------


async def record_event(
    job_id: str,
    event_type: str,
    event_data: dict[str, Any] | None = None,
    *,
    pool: Any = None,
    actor: str = "failure-policy",
) -> None:
    """Emit a dispatch_v2_events row via the service layer.

    Parameters
    ----------
    job_id : str
        UUID string of the job.
    event_type : str
        Event type to emit.
    event_data : dict | None
        Event payload.
    pool : asyncpg.Pool | None
        DB pool. If None, the event is logged but not written.
    actor : str
        Actor name for the event row.
    """
    if pool is None:
        logger.warning(
            "record_event called without pool — event dropped: job_id=%s event_type=%s",
            job_id, event_type,
        )
        return

    from tech_dev_agents.ops_console.services.dispatch_v2_service import DispatchV2Service

    svc = DispatchV2Service(pool)
    await svc.record_event(
        job_id,
        event_type,
        event_data or {},
        actor=actor,
    )


# ---------------------------------------------------------------------------
# _lookup_policy
#
# Async DB lookup for the policy row for a given failure_class.
# Falls back to POLICY_TABLE['unknown'] if the class is not in the DB.
# Defined as a top-level async function so tests can patch it.
# ---------------------------------------------------------------------------


async def _lookup_policy(
    failure_class: str,
    pool: Any = None,
) -> dict[str, Any]:
    """Look up the policy row for failure_class.

    Returns a dict with keys: failure_class, retryable, max_attempts,
    cooldown_sec, next_lane.

    Falls back to POLICY_TABLE if pool is None or DB lookup fails.
    """
    # Fast path: use module-level constant (avoids DB round-trip in tests)
    if failure_class in POLICY_TABLE:
        policy = dict(POLICY_TABLE[failure_class])
        policy["failure_class"] = failure_class
        return policy

    # Unknown class: fall back to 'unknown' policy
    policy = dict(POLICY_TABLE["unknown"])
    policy["failure_class"] = "unknown"
    logger.warning(
        "_lookup_policy: unknown failure_class %r — falling back to 'unknown' policy",
        failure_class,
    )
    return policy


# ---------------------------------------------------------------------------
# apply()
#
# Core routing function: look up policy for failure_class and emit the
# appropriate next event to move the job into the correct lane.
# ---------------------------------------------------------------------------


async def apply(
    job_id: str,
    failure_class: str,
    *,
    pool: Any = None,
) -> None:
    """Apply the failure policy for job_id.

    1. Look up the policy row for failure_class.
    2. Emit a state-transition event according to next_lane:
       - 'work_queue'      → requeued  (if retryable)
       - 'quarantined'     → quarantined
       - 'dead_letter'     → dead_lettered
       - 'attention_queue' → failed (state stays failed, lane = attention_queue)

    Parameters
    ----------
    job_id : str
        UUID string of the job.
    failure_class : str
        The classified failure class (output of classify()).
    pool : asyncpg.Pool | None
        DB pool to use for event recording.
    """
    policy = await _lookup_policy(failure_class, pool=pool)
    next_lane = policy.get("next_lane", "attention_queue")
    max_attempts = int(policy.get("max_attempts") or 0)

    logger.info(
        "apply: job_id=%s failure_class=%r next_lane=%r retryable=%s max_attempts=%d",
        job_id, failure_class, next_lane, policy.get("retryable"), max_attempts,
    )

    if next_lane == "work_queue" and policy.get("retryable"):
        # Count prior failed events for this job with the same failure_class.
        # If max_attempts already reached, escalate to attention_queue instead
        # of requeueing infinitely. (Without this guard, a deterministic crash
        # hot-loops — observed 2026-05-03: phase_runner_crash 980 retries
        # in 15 min with max_attempts=2.)
        attempts = 0
        if pool is not None:
            try:
                row = await pool.fetchrow(
                    """SELECT COUNT(*) AS attempts
                       FROM dispatch_v2_events
                       WHERE job_id = $1::uuid
                         AND event_type = 'failed'
                         AND event_data->>'failure_class' = $2""",
                    job_id, failure_class,
                )
                if row is not None:
                    attempts = int(row["attempts"] or 0)
            except Exception:
                logger.exception("apply: attempts count query failed for job_id=%s", job_id)

        if attempts >= max_attempts:
            logger.warning(
                "apply: max_attempts exhausted job_id=%s failure_class=%s attempts=%d max=%d — escalating to attention",
                job_id, failure_class, attempts, max_attempts,
            )
            await record_event(
                job_id,
                "failed",
                {
                    "failure_class": failure_class,
                    "failure_reason": f"max_attempts_exhausted: {failure_class} attempts={attempts}/{max_attempts}",
                    "actor": "failure-policy",
                    "policy_decision": "attention_queue_escalated",
                },
                pool=pool,
            )
            return

        # Emit requeued to move back to work_queue
        await record_event(
            job_id,
            "requeued",
            {
                "failure_class": failure_class,
                "actor": "failure-policy",
                "reason": "retryable_failure",
                "attempt": attempts + 1,
                "max_attempts": max_attempts,
            },
            pool=pool,
        )
    elif next_lane == "quarantined":
        # Emit quarantined to hold the job
        await record_event(
            job_id,
            "quarantined",
            {
                "failure_class": failure_class,
                "actor": "failure-policy",
                "reason": "dependency_blocked",
            },
            pool=pool,
        )
    elif next_lane == "dead_letter":
        # Emit dead_lettered to move to terminal state
        await record_event(
            job_id,
            "dead_lettered",
            {
                "failure_class": failure_class,
                "actor": "failure-policy",
                "reason": "policy_dead_letter",
            },
            pool=pool,
        )
    else:
        # attention_queue — the original 'failed' event (emitted by the caller)
        # already set state=failed, lane=attention_queue via the DB trigger.
        # STORY-873: do not emit a second 'failed' event here — it inflates
        # failure counts, obscures root cause, and causes the retry-counter
        # query (above) to double-count on subsequent calls.
        # Observability is preserved by the logger.info at the top of apply().
        logger.info(
            "apply: job_id=%s failure_class=%r routed to attention_queue — "
            "no additional event emitted (STORY-873)",
            job_id, failure_class,
        )


# ---------------------------------------------------------------------------
# DispatchFailurePolicyService — class interface
# ---------------------------------------------------------------------------


class DispatchFailurePolicyService:
    """Service wrapper for classify() and apply() with an injected pool.

    Usage::

        svc = DispatchFailurePolicyService(db_pool)
        failure_class = svc.classify(reason, exit_code=1)
        await svc.apply(job_id, failure_class)
    """

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    def classify(
        self,
        failure_reason: str,
        exit_code: int | None = None,  # migration-ci: ignore
        error_message: str | None = None,  # migration-ci: ignore
    ) -> str:
        """Classify failure → failure_class string."""
        return classify(failure_reason, exit_code=exit_code, error_message=error_message)

    async def apply(self, job_id: str, failure_class: str) -> None:
        """Apply policy for job_id + failure_class."""
        await apply(job_id, failure_class, pool=self._pool)
