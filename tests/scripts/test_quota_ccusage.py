"""Tests for scripts/quota_ccusage.py — agent-side ccusage quota helper.

Phase 7 (RED state) — tests will fail until Phase 8 implements scripts/quota_ccusage.py.

Contract under test
-------------------
scripts/quota_ccusage.py is a standalone Python script (not a package module)
that lives at /opt/agent/quota_ccusage.py on each agent VM. It is invoked via:

    sudo -u hermes python3 /opt/agent/quota_ccusage.py          # current block
    sudo -u hermes python3 /opt/agent/quota_ccusage.py --weekly  # 7-day trend

Requirements:
  - Always exits 0 (never crashes the SSH caller).
  - Prints exactly one line of valid JSON to stdout at the end.
  - JSON always contains a "source" field: "ccusage" | "jsonl" | "unavailable".
  - When source="unavailable", all numeric fields are null.
  - When --weekly given, JSON contains "days" list (7 entries, padded if short).
  - No SSH, no network; only subprocess (ccusage) + file I/O (JSONL fallback).
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Module loader (mirrors tests/scripts/conftest.py pattern for fleet_review)
# ---------------------------------------------------------------------------

_SCRIPTS_DIR = pathlib.Path(__file__).parent.parent.parent / "scripts"
_MODULE_PATH = _SCRIPTS_DIR / "quota_ccusage.py"


def _load_quota_ccusage() -> ModuleType:
    """Load scripts/quota_ccusage.py as a module.

    Raises ImportError with a clear message if the file does not yet exist
    (RED-state: tests are written before Phase 8 implementation).
    """
    if not _MODULE_PATH.exists():
        raise ImportError(
            f"scripts/quota_ccusage.py not found at {_MODULE_PATH}. "
            "Implement the script (Phase 8) to make these tests pass."
        )

    spec = importlib.util.spec_from_file_location("quota_ccusage", _MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("quota_ccusage", module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def quota_ccusage() -> ModuleType:
    """Session-scoped fixture: returns the quota_ccusage module."""
    return _load_quota_ccusage()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_SOURCES = {"ccusage", "jsonl", "unavailable"}
VALID_PACING = {"on_track", "approaching_limit", "exceeded", "unknown", None}


def _make_proc(stdout: str = "", returncode: int = 0, stderr: str = "") -> MagicMock:
    """Build a subprocess.CompletedProcess mock."""
    m = MagicMock(spec=subprocess.CompletedProcess)
    m.stdout = stdout
    m.stderr = stderr
    m.returncode = returncode
    return m


HEALTHY_CCUSAGE_BLOCKS = json.dumps({
    "blocks": [
        {
            "startTime": "2026-04-21T15:00:00Z",
            "endTime": "2026-04-21T20:00:00Z",
            "totalTokens": 412900,
            "totalCost": 1.87,
            "sessions": 7,
        }
    ],
    "activeBlock": {
        "startTime": "2026-04-21T15:00:00Z",
        "endTime": "2026-04-21T20:00:00Z",
        "totalTokens": 412900,
        "totalCost": 1.87,
        "sessions": 7,
    },
    "p90": 792000,
})

HEALTHY_CCUSAGE_WEEKLY = json.dumps({
    "daily": [
        {"date": "2026-04-15", "totalTokens": 712500, "totalCost": 3.12, "blocks": 3},
        {"date": "2026-04-16", "totalTokens": 821100, "totalCost": 3.61, "blocks": 3},
        {"date": "2026-04-17", "totalTokens": 903400, "totalCost": 3.98, "blocks": 3},
        {"date": "2026-04-18", "totalTokens": 612800, "totalCost": 2.71, "blocks": 2},
        {"date": "2026-04-19", "totalTokens": 770200, "totalCost": 3.40, "blocks": 3},
        {"date": "2026-04-20", "totalTokens": 888300, "totalCost": 3.91, "blocks": 3},
        {"date": "2026-04-21", "totalTokens": 702900, "totalCost": 2.68, "blocks": 3},
    ],
    "totalTokens": 5411200,
    "totalCost": 23.41,
})

NO_ACTIVE_BLOCK_OUTPUT = json.dumps({
    "blocks": [],
    "activeBlock": None,
    "p90": 792000,
})


def _assert_output_shape(payload: dict, weekly: bool = False) -> None:
    """Assert the mandatory fields are present and correctly typed."""
    assert "source" in payload, "output must contain 'source' field"
    assert payload["source"] in VALID_SOURCES, (
        f"source must be one of {VALID_SOURCES}, got {payload['source']!r}"
    )
    if not weekly:
        # current-block mode: numeric fields exist (may be null)
        for field in ("current_block_tokens", "current_block_cost_usd", "time_remaining_minutes"):
            assert field in payload, f"current-block output missing field: {field!r}"
    else:
        # weekly mode: days list exists
        assert "days" in payload, "weekly output missing 'days' field"
        assert isinstance(payload["days"], list), "'days' must be a list"


def _run_script(module: ModuleType, weekly: bool = False) -> dict:
    """Call module.main(weekly=weekly) and return the parsed output dict."""
    import io
    import contextlib

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        module.main(weekly=weekly)
    raw = out.getvalue().strip().splitlines()[-1]
    return json.loads(raw)


# ---------------------------------------------------------------------------
# TC-S01  ccusage not installed → source=unavailable, exits 0
# ---------------------------------------------------------------------------


class TestCcusageNotInstalled:
    """Tests when ccusage binary is absent from PATH."""

    def test_tc_s01_ccusage_missing_returns_unavailable(
        self, quota_ccusage: ModuleType
    ):
        """TC-S01: shutil.which('ccusage') returns None → emits source=unavailable."""
        with patch("shutil.which", return_value=None):
            # Also stub out quota_check import so the JSONL fallback also 'fails'
            with patch.object(quota_ccusage, "_jsonl_fallback_quota",
                              return_value=None, create=True):
                result = _run_script(quota_ccusage, weekly=False)

        _assert_output_shape(result)
        assert result["source"] == "unavailable"
        assert result["current_block_tokens"] is None

    def test_tc_s02_ccusage_missing_weekly_returns_unavailable(
        self, quota_ccusage: ModuleType
    ):
        """TC-S02: --weekly with ccusage missing → source=unavailable, days=[]."""
        with patch("shutil.which", return_value=None):
            with patch.object(quota_ccusage, "_jsonl_fallback_weekly",
                              return_value=None, create=True):
                result = _run_script(quota_ccusage, weekly=True)

        _assert_output_shape(result, weekly=True)
        assert result["source"] == "unavailable"
        assert result["days"] == []

    def test_tc_s03_ccusage_missing_never_raises(self, quota_ccusage: ModuleType):
        """TC-S03: Missing binary must not propagate an exception — exit 0 guaranteed."""
        with patch("shutil.which", return_value=None):
            try:
                result = _run_script(quota_ccusage, weekly=False)
            except Exception as exc:
                pytest.fail(
                    f"quota_ccusage.main() raised {type(exc).__name__} when ccusage "
                    f"is absent. It must never raise — only emit source=unavailable. "
                    f"Error: {exc}"
                )


# ---------------------------------------------------------------------------
# TC-S04  ccusage exits non-zero → JSONL fallback invoked
# ---------------------------------------------------------------------------


class TestCcusageNonZeroExit:
    """Tests when ccusage binary is present but exits non-zero."""

    def test_tc_s04_nonzero_exit_falls_back_to_jsonl(
        self, quota_ccusage: ModuleType
    ):
        """TC-S04: ccusage blocks --json exits 1 → falls back to JSONL source."""
        with patch("shutil.which", return_value="/usr/bin/ccusage"):
            with patch("subprocess.run", return_value=_make_proc(stdout="", returncode=1)):
                result = _run_script(quota_ccusage, weekly=False)

        _assert_output_shape(result)
        # Should not be ccusage (it failed); should be jsonl or unavailable
        assert result["source"] in {"jsonl", "unavailable"}

    def test_tc_s05_file_not_found_falls_back(self, quota_ccusage: ModuleType):
        """TC-S05: subprocess.run raises FileNotFoundError (race between which and exec) → fallback."""
        with patch("shutil.which", return_value="/usr/bin/ccusage"):
            with patch("subprocess.run", side_effect=FileNotFoundError("ccusage")):
                result = _run_script(quota_ccusage, weekly=False)

        _assert_output_shape(result)
        assert result["source"] in {"jsonl", "unavailable"}

    def test_tc_s06_timeout_falls_back(self, quota_ccusage: ModuleType):
        """TC-S06: ccusage exceeds timeout → falls back to JSONL or unavailable."""
        with patch("shutil.which", return_value="/usr/bin/ccusage"):
            with patch(
                "subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="ccusage", timeout=8),
            ):
                result = _run_script(quota_ccusage, weekly=False)

        _assert_output_shape(result)
        assert result["source"] in {"jsonl", "unavailable"}


# ---------------------------------------------------------------------------
# TC-S07  ccusage stdout has spinner prefix → _parse_last_json_line recovers
# ---------------------------------------------------------------------------


class TestCcusageSpinnerPrefix:
    """Tests for _parse_last_json_line robustness against non-JSON prefix lines."""

    def test_tc_s07_spinner_prefix_recovered(self, quota_ccusage: ModuleType):
        """TC-S07: stdout has spinner line then valid JSON → last-line parse succeeds."""
        spinner_output = "⠋ Loading...\n" + HEALTHY_CCUSAGE_BLOCKS
        with patch("shutil.which", return_value="/usr/bin/ccusage"):
            with patch(
                "subprocess.run",
                return_value=_make_proc(stdout=spinner_output, returncode=0),
            ):
                result = _run_script(quota_ccusage, weekly=False)

        _assert_output_shape(result)
        assert result["source"] == "ccusage"
        assert result["current_block_tokens"] == 412900

    def test_tc_s08_all_non_json_lines_returns_fallback(self, quota_ccusage: ModuleType):
        """TC-S08: stdout contains only non-JSON lines → JSONL or unavailable."""
        junk_output = "Loading...\nconnection closed\n"
        with patch("shutil.which", return_value="/usr/bin/ccusage"):
            with patch(
                "subprocess.run",
                return_value=_make_proc(stdout=junk_output, returncode=0),
            ):
                result = _run_script(quota_ccusage, weekly=False)

        _assert_output_shape(result)
        assert result["source"] in {"jsonl", "unavailable"}


# ---------------------------------------------------------------------------
# TC-S09  ccusage returns no active block
# ---------------------------------------------------------------------------


class TestCcusageNoActiveBlock:
    """Tests when ccusage runs OK but reports no activeBlock."""

    def test_tc_s09_no_active_block_returns_ccusage_null_numerics(
        self, quota_ccusage: ModuleType
    ):
        """TC-S09: activeBlock=null in ccusage output → source=ccusage, tokens=null."""
        with patch("shutil.which", return_value="/usr/bin/ccusage"):
            with patch(
                "subprocess.run",
                return_value=_make_proc(stdout=NO_ACTIVE_BLOCK_OUTPUT, returncode=0),
            ):
                result = _run_script(quota_ccusage, weekly=False)

        _assert_output_shape(result)
        assert result["source"] == "ccusage"
        assert result["current_block_tokens"] is None
        assert result["time_remaining_minutes"] is None


# ---------------------------------------------------------------------------
# TC-S10  Happy path — ccusage returns healthy current-block data
# ---------------------------------------------------------------------------


class TestCcusageHappyPath:
    """Tests for the happy-path ccusage current-block parse."""

    def test_tc_s10_healthy_ccusage_output_normalised(self, quota_ccusage: ModuleType):
        """TC-S10: ccusage returns valid blocks JSON → normalised schema emitted."""
        with patch("shutil.which", return_value="/usr/bin/ccusage"):
            with patch(
                "subprocess.run",
                return_value=_make_proc(stdout=HEALTHY_CCUSAGE_BLOCKS, returncode=0),
            ):
                result = _run_script(quota_ccusage, weekly=False)

        _assert_output_shape(result)
        assert result["source"] == "ccusage"
        assert result["current_block_tokens"] == 412900
        assert result["current_block_cost_usd"] == pytest.approx(1.87, abs=0.01)
        assert isinstance(result["time_remaining_minutes"], (int, type(None)))
        # p90_limit should be populated from ccusage output
        assert result.get("p90_limit") == 792000

    def test_tc_s11_output_contains_single_json_line(self, quota_ccusage: ModuleType):
        """TC-S11: main() prints exactly one JSON object on stdout (no extra lines)."""
        import io
        import contextlib

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            with patch("shutil.which", return_value="/usr/bin/ccusage"):
                with patch(
                    "subprocess.run",
                    return_value=_make_proc(stdout=HEALTHY_CCUSAGE_BLOCKS, returncode=0),
                ):
                    quota_ccusage.main(weekly=False)

        lines = [l for l in out.getvalue().splitlines() if l.strip()]
        assert len(lines) == 1, (
            f"Expected exactly 1 output line, got {len(lines)}: {lines}"
        )
        parsed = json.loads(lines[0])
        assert isinstance(parsed, dict)

    def test_tc_s12_no_exception_propagates(self, quota_ccusage: ModuleType):
        """TC-S12: main() never propagates exceptions — even on unexpected errors."""
        with patch("shutil.which", side_effect=Exception("unexpected")):
            try:
                result = _run_script(quota_ccusage, weekly=False)
            except SystemExit:
                pass  # exit(0) is acceptable
            except Exception as exc:
                pytest.fail(
                    f"quota_ccusage.main() must not propagate exceptions, got: {exc}"
                )


# ---------------------------------------------------------------------------
# TC-S13  Weekly mode — happy path
# ---------------------------------------------------------------------------


class TestCcusageWeeklyHappyPath:
    """Tests for --weekly mode ccusage parse."""

    def test_tc_s13_weekly_returns_seven_days(self, quota_ccusage: ModuleType):
        """TC-S13: ccusage returns 7-day data → days list has 7 entries."""
        with patch("shutil.which", return_value="/usr/bin/ccusage"):
            with patch(
                "subprocess.run",
                return_value=_make_proc(stdout=HEALTHY_CCUSAGE_WEEKLY, returncode=0),
            ):
                result = _run_script(quota_ccusage, weekly=True)

        _assert_output_shape(result, weekly=True)
        assert result["source"] == "ccusage"
        assert len(result["days"]) == 7

    def test_tc_s14_weekly_day_shape(self, quota_ccusage: ModuleType):
        """TC-S14: Each day entry has date, tokens, cost_usd, blocks_used."""
        with patch("shutil.which", return_value="/usr/bin/ccusage"):
            with patch(
                "subprocess.run",
                return_value=_make_proc(stdout=HEALTHY_CCUSAGE_WEEKLY, returncode=0),
            ):
                result = _run_script(quota_ccusage, weekly=True)

        for day in result["days"]:
            assert "date" in day, f"Day entry missing 'date': {day}"
            assert "tokens" in day, f"Day entry missing 'tokens': {day}"
            assert "cost_usd" in day, f"Day entry missing 'cost_usd': {day}"
            assert "blocks_used" in day, f"Day entry missing 'blocks_used': {day}"

    def test_tc_s15_weekly_totals_present(self, quota_ccusage: ModuleType):
        """TC-S15: Weekly output includes total_tokens and total_cost_usd."""
        with patch("shutil.which", return_value="/usr/bin/ccusage"):
            with patch(
                "subprocess.run",
                return_value=_make_proc(stdout=HEALTHY_CCUSAGE_WEEKLY, returncode=0),
            ):
                result = _run_script(quota_ccusage, weekly=True)

        assert "total_tokens" in result
        assert "total_cost_usd" in result
        assert result["total_tokens"] == 5411200


# ---------------------------------------------------------------------------
# TC-S16  Weekly mode — short history padding
# ---------------------------------------------------------------------------


class TestCcusageWeeklyPadding:
    """Tests that fewer-than-7-day history is padded to 7 entries."""

    def test_tc_s16_four_days_padded_to_seven(self, quota_ccusage: ModuleType):
        """TC-S16: ccusage returns 4 days → padded to 7 with zero-filled entries."""
        four_day_data = json.dumps({
            "daily": [
                {"date": "2026-04-18", "totalTokens": 612800, "totalCost": 2.71, "blocks": 2},
                {"date": "2026-04-19", "totalTokens": 770200, "totalCost": 3.40, "blocks": 3},
                {"date": "2026-04-20", "totalTokens": 888300, "totalCost": 3.91, "blocks": 3},
                {"date": "2026-04-21", "totalTokens": 702900, "totalCost": 2.68, "blocks": 3},
            ],
            "totalTokens": 2974200,
            "totalCost": 12.70,
        })
        with patch("shutil.which", return_value="/usr/bin/ccusage"):
            with patch(
                "subprocess.run",
                return_value=_make_proc(stdout=four_day_data, returncode=0),
            ):
                result = _run_script(quota_ccusage, weekly=True)

        _assert_output_shape(result, weekly=True)
        assert len(result["days"]) == 7
        zero_days = [d for d in result["days"] if d["tokens"] == 0]
        assert len(zero_days) >= 3, (
            f"Expected at least 3 zero-padded days, got {len(zero_days)}: {result['days']}"
        )

    def test_tc_s17_one_day_padded_to_seven(self, quota_ccusage: ModuleType):
        """TC-S17: ccusage returns only today's data → padded to 7 days."""
        one_day_data = json.dumps({
            "daily": [
                {"date": "2026-04-21", "totalTokens": 702900, "totalCost": 2.68, "blocks": 3},
            ],
            "totalTokens": 702900,
            "totalCost": 2.68,
        })
        with patch("shutil.which", return_value="/usr/bin/ccusage"):
            with patch(
                "subprocess.run",
                return_value=_make_proc(stdout=one_day_data, returncode=0),
            ):
                result = _run_script(quota_ccusage, weekly=True)

        assert len(result["days"]) == 7


# ---------------------------------------------------------------------------
# TC-S18  _parse_last_json_line unit tests
# ---------------------------------------------------------------------------


class TestParseLastJsonLine:
    """Tests for the _parse_last_json_line() helper."""

    def test_tc_s18_single_json_line(self, quota_ccusage: ModuleType):
        """TC-S18: Single JSON line → parsed correctly."""
        result = quota_ccusage._parse_last_json_line('{"source": "ccusage"}')
        assert result == {"source": "ccusage"}

    def test_tc_s19_junk_then_json(self, quota_ccusage: ModuleType):
        """TC-S19: Non-JSON lines before JSON → last JSON line returned."""
        text = "Loading...\nfoo bar\n{\"source\": \"ccusage\", \"tokens\": 100}"
        result = quota_ccusage._parse_last_json_line(text)
        assert result is not None
        assert result["source"] == "ccusage"
        assert result["tokens"] == 100

    def test_tc_s20_all_junk_returns_none(self, quota_ccusage: ModuleType):
        """TC-S20: No parseable JSON in stdout → returns None."""
        result = quota_ccusage._parse_last_json_line("Loading...\nconnection refused\n")
        assert result is None

    def test_tc_s21_empty_string_returns_none(self, quota_ccusage: ModuleType):
        """TC-S21: Empty string → returns None."""
        result = quota_ccusage._parse_last_json_line("")
        assert result is None
