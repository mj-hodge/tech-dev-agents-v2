"""Loki logging handler for centralized log aggregation.

Pushes structured JSON logs to the Loki HTTP API with required labels
per the Gorilla Commerce logging guidance.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import requests

LOKI_PUSH_PATH = "/loki/api/v1/push"


class LokiHandler(logging.Handler):
    """Logging handler that buffers and pushes log entries to Loki."""

    def __init__(
        self,
        url: str | None = None,
        labels: dict[str, str] | None = None,
        buffer_size: int = 20,
        flush_interval: float = 5.0,
    ) -> None:
        super().__init__()
        base = url or os.environ.get("LOKI_URL", "http://localhost:3100")
        self.url = base.rstrip("/") + LOKI_PUSH_PATH
        self.labels = labels or {}
        self._buffer: list[tuple[dict[str, str], list[Any]]] = []
        self._buffer_size = buffer_size
        self._flush_interval = flush_interval
        self._last_flush = time.time()

    def emit(self, record: logging.LogRecord) -> None:
        ts = str(int(record.created * 1e9))
        labels = {**self.labels, "severity": record.levelname.lower()}

        # Promote run_id to a label if present
        if hasattr(record, "run_id"):
            labels["run_id"] = str(record.run_id)

        # Structured metadata for high-cardinality fields
        metadata: dict[str, str] = {}
        for key in (
            "trace_id",
            "request_id",
            "invocation_id",
            "record_count",
            "duration_ms",
            "error_code",
            "http_status",
            "table_name",
        ):
            if hasattr(record, key):
                metadata[key] = str(getattr(record, key))

        entry: list[Any] = [ts, self.format(record)]
        if metadata:
            entry.append(metadata)

        self._buffer.append((labels, entry))

        if (
            len(self._buffer) >= self._buffer_size
            or (time.time() - self._last_flush) > self._flush_interval
        ):
            self.flush()

    def flush(self) -> None:
        if not self._buffer:
            return

        # Group entries by label set
        streams: dict[str, dict[str, Any]] = {}
        for labels, entry in self._buffer:
            key = json.dumps(labels, sort_keys=True)
            if key not in streams:
                streams[key] = {"stream": labels, "values": []}
            streams[key]["values"].append(entry)

        payload = {"streams": list(streams.values())}
        try:
            requests.post(self.url, json=payload, timeout=5)
        except Exception:
            pass  # Don't crash the app if Loki is down
        finally:
            self._buffer = []
            self._last_flush = time.time()

    def close(self) -> None:
        self.flush()
        super().close()


def setup_loki_logging(
    logger_name: str = "tech-dev-agents",
    level: int = logging.INFO,
) -> logging.Logger:
    """Configure and return a logger with a LokiHandler attached.

    Reads configuration from environment variables:
      LOKI_URL, LOKI_PROJECT, LOKI_ENV, LOKI_SOURCE_SYSTEM
    """
    loki_url = os.environ.get("LOKI_URL")
    if not loki_url:
        # Loki not configured — return a plain logger
        return logging.getLogger(logger_name)

    handler = LokiHandler(
        url=loki_url,
        labels={
            "project": os.environ.get("LOKI_PROJECT", "tech-dev-agents"),
            "environment": os.environ.get("LOKI_ENV", "dev"),
            "component": "worker",
            "team": "gorilla-commerce-tech",
            "source_system": os.environ.get("LOKI_SOURCE_SYSTEM", "internal"),
        },
    )
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter("%(message)s"))

    logger = logging.getLogger(logger_name)
    logger.addHandler(handler)
    logger.setLevel(level)
    return logger
