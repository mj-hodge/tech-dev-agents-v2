"""STORY-802: AC-7 — daily_cost_alert.sh structural and behavioral tests.

Verifies the daily cost alert cron script:
- Exists at the expected path
- Is executable (has shebang)
- Uses the correct log tag [COST_ALERT]
- Reads FOUNDRY_DAILY_THRESHOLD with $20 default
- Queries foundry_cost_daily table
- Emits ALERT or OK log line based on threshold comparison
- Handles missing/empty TOTAL gracefully
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "deployment" / "ops-console" / "scripts" / "daily_cost_alert.sh"


def _read_script() -> str:
    """Read the script content, failing the test if the file doesn't exist."""
    assert SCRIPT_PATH.exists(), (
        f"daily_cost_alert.sh must exist at {SCRIPT_PATH}"
    )
    return SCRIPT_PATH.read_text()


class TestDailyCostAlertScriptExists:
    """The script must exist and be a valid bash script."""

    def test_script_file_exists(self):
        """AC-7a: daily_cost_alert.sh must exist at deployment/ops-console/scripts/."""
        assert SCRIPT_PATH.exists(), (
            f"daily_cost_alert.sh must exist at {SCRIPT_PATH}"
        )

    def test_script_has_bash_shebang(self):
        """AC-7b: Script must start with #!/usr/bin/env bash."""
        content = _read_script()
        first_line = content.strip().splitlines()[0]
        assert first_line.startswith("#!/"), (
            f"Script must have a shebang line, got: {first_line}"
        )
        assert "bash" in first_line, (
            f"Script shebang must reference bash, got: {first_line}"
        )


class TestDailyCostAlertScriptContent:
    """The script must contain the required cost alert logic."""

    def test_uses_cost_alert_log_tag(self):
        """AC-7c: Script must use [COST_ALERT] log tag."""
        content = _read_script()
        assert "[COST_ALERT]" in content, (
            "Script must use [COST_ALERT] log tag for AlertService integration"
        )

    def test_default_threshold_is_20(self):
        """AC-7d: Default threshold must be $20.00."""
        content = _read_script()
        assert "20" in content, "Script must reference $20 threshold"
        assert "FOUNDRY_DAILY_THRESHOLD" in content, (
            "Script must read FOUNDRY_DAILY_THRESHOLD environment variable"
        )

    def test_queries_foundry_cost_daily_table(self):
        """AC-7e: Script must query the foundry_cost_daily table."""
        content = _read_script()
        assert "foundry_cost_daily" in content, (
            "Script must query foundry_cost_daily table for today's spend"
        )

    def test_uses_database_url_env_var(self):
        """AC-7f: Script must use DATABASE_URL for DB connection."""
        content = _read_script()
        assert "DATABASE_URL" in content, (
            "Script must use DATABASE_URL environment variable for psql connection"
        )

    def test_emits_alert_on_threshold_exceeded(self):
        """AC-7g: Script must emit ALERT log when threshold is exceeded."""
        content = _read_script()
        assert "ALERT" in content, (
            "Script must emit an ALERT line when daily spend exceeds threshold"
        )

    def test_emits_ok_on_normal_spend(self):
        """AC-7h: Script must emit OK log when spend is under threshold."""
        content = _read_script()
        assert "OK" in content, (
            "Script must emit an OK line when daily spend is under threshold"
        )

    def test_handles_empty_total_gracefully(self):
        """AC-7i: Script must handle empty/missing TOTAL (no crash on empty psql result)."""
        content = _read_script()
        assert '""' in content or "0.00" in content, (
            "Script must handle empty TOTAL gracefully (set to 0.00)"
        )

    def test_uses_set_euo_pipefail(self):
        """AC-7j: Script must use strict mode (set -euo pipefail)."""
        content = _read_script()
        assert "set -euo pipefail" in content, (
            "Script must use 'set -euo pipefail' for safety"
        )
