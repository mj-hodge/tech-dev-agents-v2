"""HTTP client for Grafana Loki LogQL query_range API."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

import httpx

logger = logging.getLogger(__name__)


# --- STORY-536: Internal no_data reason classification ---


class QuotaNoDataReason(str, Enum):
    """Internal reason codes for quota no_data outcomes.

    These are NEVER exposed in the public QuotaInfo response. They exist
    solely for telemetry (structured logs + counter dict) so operators can
    distinguish infrastructure failures from agent-offline from parse drift.
    """
    LOKI_ERROR = "loki_error"      # LokiError raised — Loki unreachable or HTTP 5xx
    EMPTY_RESULT = "empty_result"  # Loki reachable but zero matching entries
    PARSE_MISS = "parse_miss"      # Entries exist but none pass _parse_usage_line


_NO_DATA_COUNTERS: dict[str, int] = {
    "loki_error": 0,
    "empty_result": 0,
    "parse_miss": 0,
}


def get_no_data_counters() -> dict[str, int]:
    """Return a shallow copy of the no_data counter dict.

    Returns a copy to prevent callers from mutating internal state.
    Intended for test assertions and future /health endpoint wiring.
    """
    return dict(_NO_DATA_COUNTERS)


def _reset_no_data_counters() -> None:
    """Zero all no_data counters. Used for test isolation."""
    for key in _NO_DATA_COUNTERS:
        _NO_DATA_COUNTERS[key] = 0


def _no_data(reason: QuotaNoDataReason, agent: str, entries_seen: int = 0) -> "QuotaInfo":
    """Classify a no_data outcome: increment counter, emit structured log, return QuotaInfo.

    Args:
        reason: One of LOKI_ERROR / EMPTY_RESULT / PARSE_MISS.
        agent:  Agent name (used in the log message).
        entries_seen: Number of Loki entries that existed but weren't parseable.
                      0 for LOKI_ERROR and EMPTY_RESULT; actual count for PARSE_MISS.

    Returns:
        QuotaInfo(source=QuotaSourceEnum.NO_DATA) — public contract unchanged.
    """
    from tech_dev_agents.ops_console.models.responses import QuotaInfo, QuotaSourceEnum  # avoid circular

    _NO_DATA_COUNTERS[reason.value] += 1
    logger.warning(
        "quota no_data agent=%s reason=%s entries_seen=%d",
        agent, reason.value, entries_seen,
    )
    return QuotaInfo(source=QuotaSourceEnum.NO_DATA)


# --- STORY-496: Dispatch state regex patterns ---

_DISPATCH_PHASE_RE = re.compile(
    r'\[DISPATCH\]\s+Phase\s+(\d+)\s+\(([^)]+)\)\s+for\s+(STORY-\d+)'
)
_DISPATCH_RATE_LIMITED_RE = re.compile(
    r'\[DISPATCH\].*RATE\s+LIMITED'
)
_DISPATCH_BUSY_RE = re.compile(
    r'\[DISPATCH\].*busy,\s+skipping'
)
_DISPATCH_IDLE_RE = re.compile(
    r'\[DISPATCH\].*queue\s+empty'
)
_DISPATCH_COMPLETE_RE = re.compile(
    r'\[DISPATCH\]\s+(STORY-\d+)\s+COMPLETE'
)


@dataclass
class DispatchState:
    """Parsed dispatch state for an agent from Loki logs."""
    status: str = "idle"           # "working", "idle", "rate_limited", "unknown"
    story_id: str | None = None    # e.g. "STORY-447"
    phase_num: int | None = None   # e.g. 8
    phase_name: str | None = None  # e.g. "Implementation"
    started_at: str | None = None  # ISO timestamp of phase start
    rate_limit_until: str | None = None  # ISO timestamp when rate limit expires


class LokiError(Exception):
    """Raised when Loki returns a non-2xx response."""

    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"Loki error {status_code}: {message}")


@dataclass(frozen=True)
class LokiLogEntry:
    """A single log entry from Loki."""

    timestamp: str
    line: str
    labels: dict[str, str]


# Characters that could be used for LogQL injection
_LOGQL_UNSAFE = re.compile(r'[{}|=~!`"\\]')


def sanitize_label_value(value: str) -> str:
    """Sanitize a value for use in LogQL label matchers.

    Removes characters that could break out of label value context.
    """
    return _LOGQL_UNSAFE.sub("", value)


class LokiClient:
    """HTTP client for Grafana Loki query_range API."""

    _P90_CACHE_TTL = 3600  # 1 hour in seconds

    def __init__(
        self,
        base_url: str,
        api_key: str,
        http_client: httpx.AsyncClient,
    ):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._http = http_client
        # STORY-543: P90 cache keyed by agent name → (p90_value_or_None, monotonic_ts)
        self._p90_cache: dict[str, tuple[int | None, float]] = {}
        self._p90_cache_lock = asyncio.Lock()

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    async def query_range(
        self,
        query: str,
        start: str,
        end: str,
        limit: int = 1000,
    ) -> list[LokiLogEntry]:
        """Execute a LogQL query_range request.

        Returns parsed log entries sorted by timestamp.
        Raises LokiError on non-2xx response.
        """
        url = f"{self._base_url}/loki/api/v1/query_range"
        params = {
            "query": query,
            "start": start,
            "end": end,
            "limit": str(limit),
        }
        resp = await self._http.get(url, params=params, headers=self._headers(), timeout=8.0)
        if resp.status_code != 200:
            raise LokiError(resp.status_code, resp.text)

        data = resp.json()
        entries: list[LokiLogEntry] = []
        for stream in data.get("data", {}).get("result", []):
            labels = stream.get("stream", {})
            for ts, line in stream.get("values", []):
                entries.append(LokiLogEntry(timestamp=ts, line=line, labels=labels))

        entries.sort(key=lambda e: e.timestamp)
        return entries

    async def query_cost_summaries(
        self,
        agent_name: str,
        start_date: str,
        end_date: str,
    ) -> list[dict[str, Any]]:
        """Query [COST_SUMMARY] lines for an agent over a date range.

        Returns parsed cost fields from structured log lines.
        """
        safe_name = sanitize_label_value(agent_name)
        query = f'{{agent="{safe_name}"}} |= "[COST_SUMMARY]"'
        entries = await self.query_range(query, start_date, end_date)
        return [
            parsed
            for e in entries
            if (parsed := _parse_cost_summary_line(e.line)).get("cost") is not None
        ]

    async def query_done_lines(
        self,
        agent_name: str,
        start: str,
        end: str,
    ) -> list[dict[str, Any]]:
        """Query [DONE] lines for real-time cost data.

        Uses cost_collector regex pattern for parsing.
        """
        safe_name = sanitize_label_value(agent_name)
        query = f'{{agent="{safe_name}"}} |= "[DONE]"'
        entries = await self.query_range(query, start, end)
        parsed_entries: list[dict[str, Any]] = []
        for entry in entries:
            parsed = _parse_done_line(entry.line)
            if parsed:
                parsed["timestamp_ns"] = entry.timestamp
                parsed_entries.append(parsed)
        return parsed_entries

    async def query_anomalies(
        self,
        agent_name: str | None,
        start: str,
        end: str,
    ) -> list[dict[str, Any]]:
        """Query [COST_ANOMALY] lines."""
        if agent_name:
            safe_name = sanitize_label_value(agent_name)
            query = f'{{agent="{safe_name}"}} |= "[COST_ANOMALY]"'
        else:
            query = '{agent=~".+"} |= "[COST_ANOMALY]"'
        entries = await self.query_range(query, start, end)
        return [_parse_anomaly_line(e) for e in entries]

    async def query_sdk_health(
        self,
        agent_name: str | None,
        start: str,
        end: str,
    ) -> list[dict[str, Any]]:
        """Query [SDK_HEALTH] lines."""
        if agent_name:
            safe_name = sanitize_label_value(agent_name)
            query = f'{{agent="{safe_name}"}} |= "[SDK_HEALTH]"'
        else:
            query = '{agent=~".+"} |= "[SDK_HEALTH]"'
        entries = await self.query_range(query, start, end)
        return [{"timestamp": e.timestamp, "line": e.line, "labels": e.labels} for e in entries]

    async def query_terminal_guard(
        self,
        agent_name: str | None,
        start: str,
        end: str,
    ) -> list[dict[str, Any]]:
        """Query [TERMINAL_GUARD] DENIED lines."""
        if agent_name:
            safe_name = sanitize_label_value(agent_name)
            query = (
                f'{{agent="{safe_name}"}} '
                '|= "[TERMINAL_GUARD]" |= "DENIED"'
            )
        else:
            query = '{agent=~".+"} |= "[TERMINAL_GUARD]" |= "DENIED"'
        entries = await self.query_range(query, start, end)
        return [{"timestamp": e.timestamp, "line": e.line, "labels": e.labels} for e in entries]

    async def query_current_work(self, agent_name: str, lookback_hours: int = 24) -> str | None:
        """Return best-effort 'current work' text from recent Claude SDK logs."""
        safe_name = sanitize_label_value(agent_name)
        end_dt = datetime.now(timezone.utc)
        start_dt = end_dt - timedelta(hours=lookback_hours)
        start = start_dt.isoformat()
        end = end_dt.isoformat()

        guidance_query = f'{{agent="{safe_name}"}} |= "[Agent Guidance]"'
        guidance_entries = await self.query_range(guidance_query, start, end, limit=200)
        guidance = _extract_latest_marked_text(guidance_entries, "[Agent Guidance]")
        if guidance:
            return guidance

        start_query = f'{{agent="{safe_name}"}} |= "[START]"'
        start_entries = await self.query_range(start_query, start, end, limit=200)
        return _extract_latest_marked_text(start_entries, "[START]")

    async def get_agent_queue(self, agent_name: str) -> dict[str, Any] | None:
        """Query Loki for the latest [QUEUE] log line for an agent.

        Returns parsed queue state dict with keys: active, queued, total.
        Returns None if no [QUEUE] lines found in the last hour.
        """
        safe_name = sanitize_label_value(agent_name)
        end_dt = datetime.now(timezone.utc)
        start_dt = end_dt - timedelta(hours=1)
        query = f'{{agent="{safe_name}"}} |= "[QUEUE]"'

        entries = await self.query_range(
            query,
            start_dt.isoformat(),
            end_dt.isoformat(),
            limit=100,
        )
        if not entries:
            return None

        # Use the most recent entry (entries are sorted by timestamp)
        for entry in reversed(entries):
            parsed = _parse_queue_line(entry.line)
            if parsed is not None:
                return parsed

        return None

    async def get_last_activity(self, agent_name: str) -> str | None:
        """Return ISO timestamp of the most recent log entry for an agent.

        Queries the last 24h of logs and returns the timestamp of the newest
        entry, or None if no entries exist.  Used by AgentService to derive
        online/idle/offline status without an HTTP health endpoint.
        """
        safe_name = sanitize_label_value(agent_name)
        end_dt = datetime.now(timezone.utc)
        start_dt = end_dt - timedelta(hours=24)
        query = f'{{agent="{safe_name}"}}'

        entries = await self.query_range(
            query,
            start_dt.isoformat(),
            end_dt.isoformat(),
            limit=1,
        )
        if not entries:
            return None

        # Loki timestamps are nanoseconds since epoch (string).
        # Convert to ISO 8601 for classify_agent_status compatibility.
        latest = entries[-1]
        try:
            ts_ns = int(latest.timestamp)
            dt = datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc)
            return dt.isoformat()
        except (ValueError, TypeError, OSError):
            # Fallback: timestamp may already be ISO
            return latest.timestamp

    async def query_dispatch_state(
        self, agent_name: str, lookback_hours: int = 1
    ) -> DispatchState:
        """Query Loki for the latest [DISPATCH] log lines for an agent.

        STORY-496: Returns a DispatchState indicating whether the agent is
        working (running SDK), rate-limited, idle, or unknown.
        """
        safe_name = sanitize_label_value(agent_name)
        end_dt = datetime.now(timezone.utc)
        start_dt = end_dt - timedelta(hours=lookback_hours)
        # Query job=hermes-gateway with agent label filter
        query = f'{{job="hermes-gateway", agent="{safe_name}"}} |= "[DISPATCH]"'
        try:
            entries = await self.query_range(
                query,
                start_dt.isoformat(),
                end_dt.isoformat(),
                limit=100,
            )
        except LokiError:
            logger.warning("Loki dispatch query failed for agent %s", agent_name, exc_info=True)
            return DispatchState(status="unknown")

        if not entries:
            # Try without job filter (agent logs may use different labelling)
            query2 = f'{{agent="{safe_name}"}} |= "[DISPATCH]"'
            try:
                entries = await self.query_range(
                    query2,
                    start_dt.isoformat(),
                    end_dt.isoformat(),
                    limit=100,
                )
            except LokiError:
                return DispatchState(status="unknown")

        return _parse_dispatch_entries(entries)

    async def query_fleet_dispatch_state(
        self, agent_names: list[str]
    ) -> dict[str, DispatchState]:
        """Batch Loki query for dispatch state across all agents.

        STORY-496: Single query for all agents, more efficient than per-agent calls.
        Returns dict keyed by agent_name.
        """
        import asyncio
        tasks = {
            name: asyncio.create_task(self.query_dispatch_state(name))
            for name in agent_names
        }
        results: dict[str, DispatchState] = {}
        for name, task in tasks.items():
            try:
                results[name] = await task
            except Exception:
                logger.warning("Fleet dispatch state query failed for %s", name, exc_info=True)
                results[name] = DispatchState(status="unknown")
        return results

    async def query_historical_block_tokens(
        self, agent_name: str, days: int = 8
    ) -> list[int]:
        """Query Loki for [USAGE] lines over last `days` days, group by 5h block.

        STORY-543: Returns a list of per-block token totals (one int per block
        that had any usage). Used to compute P90 baseline.

        Returns empty list on LokiError or no data.
        """
        safe_name = sanitize_label_value(agent_name)
        end_dt = datetime.now(timezone.utc)
        start_dt = end_dt - timedelta(days=days)
        query = f'{{agent="{safe_name}"}} |= "[USAGE]"'

        try:
            entries = await self.query_range(
                query,
                start_dt.isoformat(),
                end_dt.isoformat(),
                limit=10000,
            )
        except LokiError:
            logger.warning("Historical block token query failed for %s", agent_name, exc_info=True)
            return []

        if not entries:
            return []

        # Group by 5h block and sum tokens
        block_totals: dict[str, int] = {}  # block_key → total_tokens
        for entry in entries:
            parsed = _parse_usage_line(entry.line)
            if not parsed:
                continue
            # Determine which 5h block this entry belongs to
            try:
                ts_ns = int(entry.timestamp)
                entry_dt = datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc)
            except (ValueError, TypeError, OSError):
                continue
            block_start, _ = _current_5h_block(entry_dt)
            block_key = block_start.strftime("%Y-%m-%d-%H")
            block_totals[block_key] = block_totals.get(block_key, 0) + parsed["total_tokens"]

        return list(block_totals.values())

    async def _get_p90_limit(self, agent_name: str) -> int | None:
        """Return cached P90 limit for agent, re-querying if cache is stale.

        STORY-543: Cache P90 with 1h TTL. Returns None if insufficient data.
        Uses asyncio.Lock to prevent concurrent cache stampedes.
        """
        async with self._p90_cache_lock:
            now = time.monotonic()
            cached = self._p90_cache.get(agent_name)
            if cached is not None:
                value, ts = cached
                if (now - ts) < self._P90_CACHE_TTL:
                    return value

            # Cache miss or expired — recompute
            block_totals = await self.query_historical_block_tokens(agent_name)
            p90 = _compute_p90(block_totals)
            self._p90_cache[agent_name] = (p90, time.monotonic())
            return p90

    async def query_agent_quota(self, agent_name: str) -> "QuotaInfo":
        """Aggregate [USAGE] log lines for agent from the last 5 hours → QuotaInfo.

        STORY-513 AC-2: Queries `{agent="<name>"} |= "[USAGE]"` and sums
        total_tokens / cost_usd across all matching entries.

        STORY-513 AC-5: Returns QuotaInfo(source="no_data") when:
          - Loki returns no entries for the agent
          - A LokiError occurs (graceful degradation)
          - Lines exist but none contain parseable [USAGE] fields

        STORY-543: Uses P90 from historical blocks (8 days) instead of
        hardcoded 200K ceiling. Falls back to 200K when < 3 blocks or error.
        """
        from tech_dev_agents.ops_console.models.responses import QuotaInfo, QuotaSourceEnum

        safe_name = sanitize_label_value(agent_name)
        end_dt = datetime.now(timezone.utc)
        # Use the actual block start, not a rolling 5h window. A rolling window
        # bleeds previous-block tokens into the current block's count whenever
        # now is less than 5h into a new block (which is most of the time).
        block_start_dt, block_end_dt = _current_5h_block(end_dt)
        query = f'{{agent="{safe_name}"}} |= "[USAGE]"'

        try:
            entries = await self.query_range(
                query,
                block_start_dt.isoformat(),
                end_dt.isoformat(),
                limit=5000,
            )
        except LokiError:
            return _no_data(QuotaNoDataReason.LOKI_ERROR, agent_name)

        if not entries:
            return _no_data(QuotaNoDataReason.EMPTY_RESULT, agent_name)

        total_tokens = 0
        total_cost = 0.0
        sessions = 0
        for entry in entries:
            parsed = _parse_usage_line(entry.line)
            if parsed:
                total_tokens += parsed["total_tokens"]
                total_cost += parsed["cost_usd"]
                sessions += 1

        if sessions == 0:
            return _no_data(QuotaNoDataReason.PARSE_MISS, agent_name, len(entries))

        remaining_minutes = max(
            0, int((block_end_dt - end_dt).total_seconds() / 60)
        )
        # STORY-543: Use P90 from historical blocks instead of hardcoded ceiling.
        # Falls back to 200K when fewer than 3 historical blocks or Loki error.
        p90_limit = await self._get_p90_limit(agent_name)
        block_token_limit = p90_limit if p90_limit is not None else 200_000
        remaining_tokens = max(0, block_token_limit - total_tokens)
        percent_used = round((total_tokens / block_token_limit) * 100, 2) if block_token_limit > 0 else 0.0
        return QuotaInfo(
            source=QuotaSourceEnum.LOKI,
            current_block_tokens=total_tokens,
            current_block_cost_usd=round(total_cost, 4),
            sessions_in_block=sessions,
            reset_in_minutes=remaining_minutes,
            remaining_tokens=remaining_tokens,
            p90_limit=p90_limit,
            percent_used=percent_used,
            block_start=block_start_dt.strftime("%H:%MZ"),
            block_end=block_end_dt.strftime("%H:%MZ"),
        )

    async def query_cost_alerts(
        self,
        agent_name: str | None,
        start: str,
        end: str,
    ) -> list[dict[str, Any]]:
        """Query [COST_ALERT] lines from daily_cost_alert.sh.

        STORY-802 AC-7: Surfaces daily threshold breach alerts so
        AlertService can include them on the dashboard.

        Args:
            agent_name: Optional agent filter. None queries all agents.
            start: ISO timestamp for query range start.
            end: ISO timestamp for query range end.

        Returns:
            List of dicts with keys: timestamp, line, agent_name.
        """
        if agent_name:
            safe_name = sanitize_label_value(agent_name)
            query = f'{{agent="{safe_name}"}} |= "[COST_ALERT]"'
        else:
            query = '{agent=~".+"} |= "[COST_ALERT]"'
        entries = await self.query_range(query, start, end)
        return [
            {
                "timestamp": e.timestamp,
                "line": e.line,
                "agent_name": e.labels.get("agent", "fleet"),
            }
            for e in entries
        ]

    async def is_reachable(self) -> bool:
        """Health check: GET /ready on Loki. Returns True/False."""
        try:
            resp = await self._http.get(
                f"{self._base_url}/ready",
                headers=self._headers(),
                timeout=5.0,
            )
            return resp.status_code == 200
        except httpx.HTTPError:
            logger.warning("Loki reachability check failed", exc_info=True)
            return False


# --- Internal parsers ---

_COST_SUMMARY_SDK_RE = re.compile(
    r"\[COST_SUMMARY\]\s+"
    r"agent=(\S+)\s+"
    r"date=(\S+)\s+"
    r"sdk_sessions=(\d+)\s+"
    r"sdk_cost=\$?([\d.]+)\s+"
    r"sdk_turns=(\d+)"
)

_COST_SUMMARY_LEGACY_RE = re.compile(
    r"\[COST_SUMMARY\]\s+"
    r"agent=(\S+)\s+"
    r"date=(\S+)\s+"
    r"sessions=(\d+)\s+"
    r"cost=\$?([\d.]+)\s+"
    r"turns=(\d+)"
)

# Order-independent field extraction for [DONE] lines (STORY-022 fix).
# The SDK outputs: [DONE] turns=X tools=Y cost=$Z ...
# Previously _DONE_RE required cost= before turns=, which never matched.
_DONE_COST_RE = re.compile(r"cost=\$?(?P<cost>[\d.]+)")
_DONE_TURNS_RE = re.compile(r"(?<!\w)turns=(?P<turns>\d+)")


def _parse_cost_summary_line(line: str) -> dict[str, Any]:
    """Parse a [COST_SUMMARY] log line into structured fields."""
    m = _COST_SUMMARY_SDK_RE.search(line)
    if not m:
        m = _COST_SUMMARY_LEGACY_RE.search(line)
    if m:
        return {
            "agent_name": m.group(1),
            "date": m.group(2),
            "sessions": int(m.group(3)),
            "cost": float(m.group(4)),
            "turns": int(m.group(5)),
        }
    return {"raw": line}


def _parse_done_line(line: str) -> dict[str, Any] | None:
    """Parse a [DONE] log line for cost data. Field-order agnostic.

    Handles both SDK format (turns before cost) and legacy (cost before turns).
    Skips lines without a valid numeric cost (e.g. cost=$? from auth failures).
    """
    if "[DONE]" not in line:
        return None
    cost_m = _DONE_COST_RE.search(line)
    if not cost_m:
        return None  # Cost is required; skip auth failures (cost=$?)
    turns_m = _DONE_TURNS_RE.search(line)
    return {
        "cost": float(cost_m.group("cost")),
        "turns": int(turns_m.group("turns")) if turns_m else 0,
    }


def _parse_anomaly_line(entry: LokiLogEntry) -> dict[str, Any]:
    """Parse a [COST_ANOMALY] log entry."""
    return {
        "timestamp": entry.timestamp,
        "line": entry.line,
        "agent_name": entry.labels.get("agent", "unknown"),
        "type": "cost_anomaly",
        "active": True,
    }


_QUEUE_RE = re.compile(
    r"\[QUEUE\]\s+active=(\S+)\s+queued=(\S+)\s+total=(\d+)"
)


def _parse_queue_line(line: str) -> dict[str, Any] | None:
    """Parse a [QUEUE] log line into structured fields.

    Returns dict with keys: active (str|None), queued (list[str]), total (int).
    Returns None if the line doesn't contain a valid [QUEUE] entry.
    """
    m = _QUEUE_RE.search(line)
    if not m:
        return None

    active_raw = m.group(1)
    queued_raw = m.group(2)
    total = int(m.group(3))

    active = None if active_raw == "none" else active_raw
    queued = [] if queued_raw == "none" else queued_raw.split(",")

    return {"active": active, "queued": queued, "total": total}


def _extract_latest_marked_text(entries: list[LokiLogEntry], marker: str) -> str | None:
    """Extract the latest non-empty payload after a marker token."""
    for entry in reversed(entries):
        line = entry.line.strip()
        if marker not in line:
            continue
        payload = line.split(marker, 1)[1].strip()
        if payload:
            return payload[:200]
    return None


# --- STORY-513: [USAGE] line parsers ---


def _compute_p90(block_totals: list[int]) -> int | None:
    """Compute the 90th percentile of per-block token totals.

    STORY-543: Returns None if fewer than 3 data points (insufficient history).
    Uses linear interpolation matching numpy's default percentile method.
    """
    if len(block_totals) < 3:
        return None
    sorted_vals = sorted(block_totals)
    n = len(sorted_vals)
    # P90 index using linear interpolation (matches numpy default)
    idx = 0.9 * (n - 1)
    lower = int(idx)
    upper = lower + 1
    if upper >= n:
        return sorted_vals[-1]
    frac = idx - lower
    result = sorted_vals[lower] + frac * (sorted_vals[upper] - sorted_vals[lower])
    return int(round(result))


_USAGE_TOKENS_RE = re.compile(r"total_tokens=(\d+)")
_USAGE_COST_RE = re.compile(r"cost_usd=([\d.]+)")


def _parse_usage_line(line: str) -> dict | None:
    """Parse a [USAGE] log line into total_tokens and cost_usd.

    Expected format: `[USAGE] total_tokens=50000 cost_usd=0.54`
    Returns None if the line does not contain a parseable [USAGE] entry.
    """
    if "[USAGE]" not in line:
        return None
    tokens_m = _USAGE_TOKENS_RE.search(line)
    if not tokens_m:
        return None
    cost_m = _USAGE_COST_RE.search(line)
    return {
        "total_tokens": int(tokens_m.group(1)),
        "cost_usd": float(cost_m.group(1)) if cost_m else 0.0,
    }


def _current_5h_block(now: datetime) -> tuple[datetime, datetime]:
    """Return (block_start, block_end) for the current 5-hour billing window.

    Blocks align to UTC hours: 00-05, 05-10, 10-15, 15-20, 20-01 (next day).
    """
    block_starts = [0, 5, 10, 15, 20]
    block_start_hour = max(h for h in block_starts if h <= now.hour)
    block_end_hour = block_start_hour + 5

    block_start = now.replace(
        hour=block_start_hour, minute=0, second=0, microsecond=0
    )
    if block_end_hour >= 24:
        block_end = (now + timedelta(days=1)).replace(
            hour=block_end_hour - 24, minute=0, second=0, microsecond=0
        )
    else:
        block_end = now.replace(
            hour=block_end_hour, minute=0, second=0, microsecond=0
        )
    return block_start, block_end


def _parse_dispatch_entries(entries: list[LokiLogEntry]) -> DispatchState:
    """Parse the most recent [DISPATCH] log entries into a DispatchState.

    STORY-496: Scans entries newest-first and returns the first match.
    Priority: rate_limited > working (phase line) > idle (queue empty) > unknown
    """
    for entry in reversed(entries):
        line = entry.line

        # Rate-limited check (highest priority — overrides working)
        if _DISPATCH_RATE_LIMITED_RE.search(line):
            return DispatchState(status="rate_limited", started_at=entry.timestamp)

        # Active phase (working)
        m = _DISPATCH_PHASE_RE.search(line)
        if m:
            try:
                ts_ns = int(entry.timestamp)
                dt = datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc)
                started_iso = dt.isoformat()
            except (ValueError, TypeError, OSError):
                started_iso = entry.timestamp
            return DispatchState(
                status="working",
                phase_num=int(m.group(1)),
                phase_name=m.group(2),
                story_id=m.group(3),
                started_at=started_iso,
            )

        # Idle — queue empty
        if _DISPATCH_IDLE_RE.search(line):
            return DispatchState(status="idle")

        # Busy / skipping (another task in progress but not dispatching new)
        if _DISPATCH_BUSY_RE.search(line):
            return DispatchState(status="working")

    # No matching lines found
    return DispatchState(status="unknown")
