"""
STORY-916: Queue Health Alerts — ops_console test entry point.

The authoritative test suite lives at:
    tests/observability/test_queue_health_alerts.py

This module re-exports those tests so the ops_console test tree also covers
the queue health alert rules. Alert UIDs tested:
  dispatch-stuck-claimed
  dispatch-unlinked-in-review
  dispatch-redispatch-loop
"""

# Re-export all tests from the authoritative location so pytest discovers them
# when running tests/ops_console/ directly.
from tests.observability.test_queue_health_alerts import (  # noqa: F401
    TestAdditiveOnlyGuard,
    TestAlertYamlStructure,
    TestNotificationPayload,
    TestRunbookContent,
    TestSqlQueryCorrectness,
    alert_yaml,
    db,
    queue_health_group,
    queue_health_rules,
)
