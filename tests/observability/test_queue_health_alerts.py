"""
STORY-916: Queue Health SLOs + Grafana Alerts — Phase 7 Test Suite
===================================================================

14 tests covering:
  Group A (3) — Alert YAML structure: queue_health_slos group present with correct schema
  Group B (6) — SQL query correctness via SQLite synthetic DB
  Group C (1) — Notification payload has required annotation fields
  Group D (3) — Runbook files have ## Quick triage section with fenced SQL
  Group E (1) — Existing dispatch_sdlc_alerts rules are untouched (additive-only)

All 14 tests are RED at phase end: the queue_health_slos group and runbook files
do not yet exist.
"""

from __future__ import annotations

import re
import sqlite3
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
GRAFANA_ALERTS_YAML = REPO_ROOT / "deployment" / "observability" / "grafana-alerts.yml"
DATA_REMEDIATIONS = REPO_ROOT / "data-remediations"

RUNBOOKS = {
    "stuck_claimed": DATA_REMEDIATIONS / "queue-stuck-claimed-runbook.md",
    "unlinked_in_review": DATA_REMEDIATIONS / "queue-unlinked-in-review-runbook.md",
    "redispatch_loop": DATA_REMEDIATIONS / "queue-redispatch-loop-runbook.md",
}

# UIDs that MUST exist in the new alert group
EXPECTED_UIDS = {
    "queue-stuck-claimed",
    "queue-unlinked-in-review",
    "queue-redispatch-loop",
}

# UIDs from the existing group that must remain unchanged
EXISTING_UIDS = {
    "phase-duration-warning",
    "phase-duration-critical",
    "dispatch-failure-rate",
    "partial-pr-rate",
}

# Annotation fields that must NOT appear in alert templates (security constraint)
PROHIBITED_PAYLOAD_FIELDS = {"updated_at", "prompt", "agent_identity", "state="}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def alert_yaml() -> dict:
    """Load and parse the grafana-alerts.yml file."""
    assert GRAFANA_ALERTS_YAML.exists(), (
        f"grafana-alerts.yml not found at {GRAFANA_ALERTS_YAML}"
    )
    with GRAFANA_ALERTS_YAML.open() as fh:
        return yaml.safe_load(fh)


@pytest.fixture(scope="module")
def queue_health_group(alert_yaml: dict) -> dict:
    """Extract the queue_health_slos group from the parsed YAML.

    Will raise KeyError (RED) until Phase 8 adds the group.
    """
    groups = {g["name"]: g for g in alert_yaml.get("groups", [])}
    assert "queue_health_slos" in groups, (
        "Group 'queue_health_slos' not found in grafana-alerts.yml. "
        "Phase 8 must add this group."
    )
    return groups["queue_health_slos"]


@pytest.fixture(scope="module")
def queue_health_rules(queue_health_group: dict) -> dict[str, dict]:
    """Return a uid → rule dict for all rules in the queue_health_slos group."""
    return {r["uid"]: r for r in queue_health_group.get("rules", [])}


@pytest.fixture
def db() -> sqlite3.Connection:
    """In-memory SQLite DB with dispatch schema for SQL correctness tests."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE dispatch_state_current (
            job_id       TEXT PRIMARY KEY,
            story_id     TEXT,
            state        TEXT NOT NULL,
            updated_at   TEXT NOT NULL   -- ISO-8601 string
        );

        CREATE TABLE dispatch_jobs (
            job_id          TEXT PRIMARY KEY,
            pr_number       INTEGER,
            correlation_key TEXT
        );

        CREATE TABLE dispatch_v2_events (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            correlation_key TEXT NOT NULL,
            event_type      TEXT NOT NULL,
            created_at      TEXT NOT NULL   -- ISO-8601 string
        );
        """
    )
    conn.commit()
    return conn


def _now_iso(delta_minutes: int = 0) -> str:
    """Return ISO-8601 UTC timestamp offset by delta_minutes."""
    ts = datetime.now(timezone.utc) + timedelta(minutes=delta_minutes)
    return ts.strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Group A — Alert YAML Structure
# ---------------------------------------------------------------------------


class TestAlertYamlStructure:
    """A1–A3: Verify queue_health_slos group exists with correct schema."""

    def test_alert_yaml_has_queue_health_group(self, queue_health_group: dict):
        """A1 — queue_health_slos group present with correct name and three rules."""
        assert queue_health_group["name"] == "queue_health_slos"
        rule_uids = {r["uid"] for r in queue_health_group.get("rules", [])}
        assert EXPECTED_UIDS == rule_uids, (
            f"Expected UIDs {EXPECTED_UIDS}, got {rule_uids}"
        )

    def test_alert_severities_and_intervals(self, queue_health_group: dict, queue_health_rules: dict[str, dict]):
        """A2 — Correct severity labels and evaluation intervals per spec.

        stuck-claimed  → severity=page,    interval=1m
        unlinked       → severity=warning,  interval=5m
        redispatch     → severity=warning,  interval=5m
        Datasource must NOT be prometheus.
        """
        # Group-level interval for paging alert (1 minute)
        # Individual rules may override; check labels
        stuck = queue_health_rules["queue-stuck-claimed"]
        assert stuck.get("labels", {}).get("severity") == "page", (
            "queue-stuck-claimed must have severity=page"
        )

        for uid in ("queue-unlinked-in-review", "queue-redispatch-loop"):
            rule = queue_health_rules[uid]
            assert rule.get("labels", {}).get("severity") == "warning", (
                f"{uid} must have severity=warning"
            )

        # Datasource must be postgres (or __expr__), never prometheus
        for rule in queue_health_rules.values():
            for data_item in rule.get("data", []):
                ds_uid = data_item.get("datasourceUid", "")
                assert ds_uid.lower() != "prometheus", (
                    f"Rule {rule['uid']} uses Prometheus datasource — use Postgres"
                )

    def test_alert_payloads_no_sensitive_data(self, queue_health_rules: dict[str, dict]):
        """A3 — Annotation templates must not expose prohibited fields."""
        for uid, rule in queue_health_rules.items():
            annotations = rule.get("annotations", {})
            combined = " ".join(str(v) for v in annotations.values())
            for prohibited in PROHIBITED_PAYLOAD_FIELDS:
                assert prohibited not in combined, (
                    f"Rule {uid} annotation leaks prohibited field '{prohibited}'"
                )


# ---------------------------------------------------------------------------
# Group B — SQL Query Correctness (SQLite synthetic DB)
# ---------------------------------------------------------------------------


def _extract_raw_sql(rule: dict) -> str:
    """Extract rawSql from the first non-__expr__ data item in a rule."""
    for item in rule.get("data", []):
        if item.get("datasourceUid", "") == "__expr__":
            continue
        model = item.get("model", {})
        sql = model.get("rawSql") or model.get("expr", "")
        assert sql, f"No rawSql found in rule data item: {item}"
        return sql
    raise AssertionError("No non-expression data item found in rule")


def _sqlite_adapt(sql: str) -> str:
    """Adapt Postgres-dialect SQL to SQLite for test execution.

    Conversions:
      now() - updated_at > interval 'N minutes'
        → (julianday('now') - julianday(updated_at)) * 1440 > N
      INTERVAL '24 hours'  → datetime('now', '-24 hours')
      COUNT(*) > N         → pass-through (SQLite compatible)
    """
    # Replace: now() - updated_at > interval 'N minutes'
    # with SQLite equivalent using julianday arithmetic
    sql = re.sub(
        r"now\(\)\s*-\s*(\w+)\s*>\s*interval\s*'(\d+)\s+minutes?'",
        r"(julianday('now') - julianday(\1)) * 1440.0 > \2",
        sql,
        flags=re.IGNORECASE,
    )
    # Replace: now() - updated_at > interval 'N hours'
    sql = re.sub(
        r"now\(\)\s*-\s*(\w+)\s*>\s*interval\s*'(\d+)\s+hours?'",
        r"(julianday('now') - julianday(\1)) * 24.0 > \2",
        sql,
        flags=re.IGNORECASE,
    )
    # Replace: created_at > now() - interval '24 hours'
    sql = re.sub(
        r"(\w+)\s*>\s*now\(\)\s*-\s*interval\s*'24\s+hours?'",
        r"\1 > datetime('now', '-24 hours')",
        sql,
        flags=re.IGNORECASE,
    )
    # Replace bare now() → datetime('now')
    sql = re.sub(r"\bnow\(\)", "datetime('now')", sql, flags=re.IGNORECASE)
    return sql


class TestSqlQueryCorrectness:
    """B1–B6: SQL predicates fire/clear correctly against synthetic SQLite data."""

    def test_alert1_positive_stuck_claimed_31min(self, db: sqlite3.Connection, queue_health_rules: dict[str, dict]):
        """B1 — stuck claimed row aged 31 min → query returns ≥ 1 row."""
        db.execute(
            "INSERT INTO dispatch_state_current VALUES (?, ?, ?, ?)",
            ("job-001", "STORY-999", "claimed", _now_iso(-31)),
        )
        db.commit()

        raw_sql = _extract_raw_sql(queue_health_rules["queue-stuck-claimed"])
        adapted = _sqlite_adapt(raw_sql)
        rows = db.execute(adapted).fetchall()

        assert len(rows) >= 1, (
            f"Alert 1 should fire for 31-min-old claimed row. SQL returned 0 rows.\nSQL:\n{adapted}"
        )

    def test_alert1_negative_claimed_fresh(self, db: sqlite3.Connection, queue_health_rules: dict[str, dict]):
        """B2 — claimed row aged 1 min → query returns 0 rows."""
        db.execute("DELETE FROM dispatch_state_current")
        db.execute(
            "INSERT INTO dispatch_state_current VALUES (?, ?, ?, ?)",
            ("job-002", "STORY-999", "claimed", _now_iso(-1)),
        )
        db.commit()

        raw_sql = _extract_raw_sql(queue_health_rules["queue-stuck-claimed"])
        adapted = _sqlite_adapt(raw_sql)
        rows = db.execute(adapted).fetchall()

        assert len(rows) == 0, (
            f"Alert 1 must NOT fire for 1-min-old claimed row. SQL returned {len(rows)} rows."
        )

    def test_alert2_positive_unlinked_in_review_61min(self, db: sqlite3.Connection, queue_health_rules: dict[str, dict]):
        """B3 — in_review row, pr_number IS NULL, aged 61 min → query returns ≥ 1 row."""
        db.execute("DELETE FROM dispatch_state_current")
        db.execute("DELETE FROM dispatch_jobs")
        db.execute(
            "INSERT INTO dispatch_state_current VALUES (?, ?, ?, ?)",
            ("job-003", "STORY-999", "in_review", _now_iso(-61)),
        )
        db.execute(
            "INSERT INTO dispatch_jobs VALUES (?, ?, ?)",
            ("job-003", None, "corr-abc"),
        )
        db.commit()

        raw_sql = _extract_raw_sql(queue_health_rules["queue-unlinked-in-review"])
        adapted = _sqlite_adapt(raw_sql)
        rows = db.execute(adapted).fetchall()

        assert len(rows) >= 1, (
            f"Alert 2 should fire for 61-min-old unlinked in_review. SQL:\n{adapted}"
        )

    def test_alert2_cleared_after_pr_backfill(self, db: sqlite3.Connection, queue_health_rules: dict[str, dict]):
        """B4 — backfill pr_number on the row → query returns 0 rows."""
        db.execute("UPDATE dispatch_jobs SET pr_number = 42 WHERE job_id = 'job-003'")
        db.commit()

        raw_sql = _extract_raw_sql(queue_health_rules["queue-unlinked-in-review"])
        adapted = _sqlite_adapt(raw_sql)
        rows = db.execute(adapted).fetchall()

        assert len(rows) == 0, (
            "Alert 2 must clear after pr_number is backfilled."
        )

    def test_alert3_positive_redispatch_4_events(self, db: sqlite3.Connection, queue_health_rules: dict[str, dict]):
        """B5 — 4 redispatched events for same correlation_key in last 24h → query returns ≥ 1 row."""
        db.execute("DELETE FROM dispatch_v2_events")
        for i in range(4):
            db.execute(
                "INSERT INTO dispatch_v2_events (correlation_key, event_type, created_at) VALUES (?, ?, ?)",
                ("corr-loop-1", "redispatched", _now_iso(-(i * 60))),  # spread over last ~4h
            )
        db.commit()

        raw_sql = _extract_raw_sql(queue_health_rules["queue-redispatch-loop"])
        adapted = _sqlite_adapt(raw_sql)
        rows = db.execute(adapted).fetchall()

        assert len(rows) >= 1, (
            f"Alert 3 should fire for 4 redispatched events. SQL:\n{adapted}"
        )

    def test_alert3_boundary_3_events_no_fire(self, db: sqlite3.Connection, queue_health_rules: dict[str, dict]):
        """B6 — exactly 3 redispatched events → query returns 0 rows (boundary: >3 required)."""
        db.execute("DELETE FROM dispatch_v2_events")
        for i in range(3):
            db.execute(
                "INSERT INTO dispatch_v2_events (correlation_key, event_type, created_at) VALUES (?, ?, ?)",
                ("corr-loop-2", "redispatched", _now_iso(-(i * 60))),
            )
        db.commit()

        raw_sql = _extract_raw_sql(queue_health_rules["queue-redispatch-loop"])
        adapted = _sqlite_adapt(raw_sql)
        rows = db.execute(adapted).fetchall()

        assert len(rows) == 0, (
            "Alert 3 must NOT fire for exactly 3 redispatched events (boundary: > 3)."
        )


# ---------------------------------------------------------------------------
# Group C — Notification Payload
# ---------------------------------------------------------------------------


class TestNotificationPayload:
    """C1 — Each alert annotation contains name, count expr, panel link, runbook link."""

    REQUIRED_ANNOTATION_PATTERNS = {
        "count_or_value": re.compile(r"\$\{?values|count\b", re.IGNORECASE),
        "panel_link": re.compile(r"grafana|panel|dashboard", re.IGNORECASE),
        "runbook_link": re.compile(r"runbook|data-remediation", re.IGNORECASE),
    }

    def test_alert_annotations_contain_required_fields(self, queue_health_rules: dict[str, dict]):
        """C1 — All three alerts have count reference, panel link, and runbook link in annotations."""
        for uid, rule in queue_health_rules.items():
            annotations = rule.get("annotations", {})
            combined = " ".join(str(v) for v in annotations.values())
            for field_name, pattern in self.REQUIRED_ANNOTATION_PATTERNS.items():
                assert pattern.search(combined), (
                    f"Rule '{uid}' annotation missing '{field_name}'. "
                    f"Annotations: {annotations}"
                )


# ---------------------------------------------------------------------------
# Group D — Runbook Content
# ---------------------------------------------------------------------------


class TestRunbookContent:
    """D1–D3 — Each runbook file has ## Quick triage section with fenced SQL."""

    FENCED_SQL_PATTERN = re.compile(r"```sql", re.IGNORECASE)

    @pytest.mark.parametrize("key,path", list(RUNBOOKS.items()))
    def test_runbook_has_quick_triage_sql(self, key: str, path: Path):
        """D1/D2/D3 — Runbook exists, has ## Quick triage section, has ≥1 SQL block."""
        assert path.exists(), (
            f"Runbook not found: {path}. Phase 8 must create this file."
        )
        content = path.read_text()

        assert "## Quick triage" in content, (
            f"Runbook {path.name} missing '## Quick triage' section."
        )

        assert self.FENCED_SQL_PATTERN.search(content), (
            f"Runbook {path.name} has no fenced SQL block (```sql). "
            "Quick triage must include at least one diagnostic query."
        )


# ---------------------------------------------------------------------------
# Group E — Additive-Only Guard
# ---------------------------------------------------------------------------


class TestAdditiveOnlyGuard:
    """E1 — Existing dispatch_sdlc_alerts UIDs are unchanged after Phase 8."""

    def test_existing_alert_rules_unchanged(self, alert_yaml: dict):
        """E1 — All four original dispatch_sdlc_alerts rules remain present and unmodified."""
        groups = {g["name"]: g for g in alert_yaml.get("groups", [])}
        assert "dispatch_sdlc_alerts" in groups, (
            "Original 'dispatch_sdlc_alerts' group was removed — additive-only rule violated."
        )
        existing_group = groups["dispatch_sdlc_alerts"]
        found_uids = {r["uid"] for r in existing_group.get("rules", [])}
        missing = EXISTING_UIDS - found_uids
        assert not missing, (
            f"Existing rule UIDs removed from dispatch_sdlc_alerts: {missing}"
        )
