"""STORY-727: ImprovementConfig — typed configuration dataclass for the self-improvement loop.

Reads from state/morris/config.toml [morris.improvement] section.
All values are overridable via environment variables for testing.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class ImprovementConfig:
    """Typed configuration for the Morris self-improvement loop.

    Default values match the committed config.toml defaults.
    All fields can be overridden via environment variables of the form
    MORRIS_IMPROVEMENT_<FIELD_UPPER> (e.g. MORRIS_IMPROVEMENT_THRESHOLD_CRITICAL=1).
    """

    enabled: bool = False
    """Master kill switch. When False, all cron scripts become no-ops."""

    mode: str = "shadow"
    """'shadow' | 'live'. In shadow mode, proposals are generated and stored
    but no DM is posted to Mark — only a daily summary."""

    pattern_window_days: int = 7
    """DB failure-reason aggregation window in days."""

    adversarial_window_days: int = 30
    """adversarial-review.md scan window in days (longer than DB window
    because reviews are less frequent)."""

    threshold_critical: int = 3
    """Minimum number of stories per CRITICAL-class adversarial pattern
    before a proposal is generated."""

    threshold_high: int = 4
    """Higher bar for HIGH-class findings (less certain signal)."""

    taxonomy_gap_fraction: float = 0.20
    """Fraction of 'unknown' failures (out of total) that triggers the
    failure.unknown taxonomy-gap proposal."""

    retry_storm_window_seconds: int = 60
    """Max total elapsed time for 3+ retries to be classified as a storm."""

    retry_storm_min_stories: int = 3
    """Minimum number of stories in 7 days to trigger retry_storm pattern."""

    reproposal_cooldown_days: int = 30
    """Rejected proposals are suppressed for this many days before re-proposing."""

    tracking_window_days: int = 14
    """Check metric N days after a proposal was applied."""

    cost_ceiling_monthly_usd: float = 15.0
    """Alert Mark if Sonnet spend on proposals exceeds this per month."""

    @classmethod
    def from_env(cls) -> "ImprovementConfig":
        """Load config with environment variable overrides."""
        cfg = cls()
        env_map = {
            "MORRIS_IMPROVEMENT_ENABLED": ("enabled", lambda v: v.lower() in ("1", "true", "yes")),
            "MORRIS_IMPROVEMENT_MODE": ("mode", str),
            "MORRIS_IMPROVEMENT_PATTERN_WINDOW_DAYS": ("pattern_window_days", int),
            "MORRIS_IMPROVEMENT_ADVERSARIAL_WINDOW_DAYS": ("adversarial_window_days", int),
            "MORRIS_IMPROVEMENT_THRESHOLD_CRITICAL": ("threshold_critical", int),
            "MORRIS_IMPROVEMENT_THRESHOLD_HIGH": ("threshold_high", int),
            "MORRIS_IMPROVEMENT_TAXONOMY_GAP_FRACTION": ("taxonomy_gap_fraction", float),
            "MORRIS_IMPROVEMENT_RETRY_STORM_WINDOW_SECONDS": ("retry_storm_window_seconds", int),
            "MORRIS_IMPROVEMENT_RETRY_STORM_MIN_STORIES": ("retry_storm_min_stories", int),
            "MORRIS_IMPROVEMENT_REPROPOSAL_COOLDOWN_DAYS": ("reproposal_cooldown_days", int),
            "MORRIS_IMPROVEMENT_TRACKING_WINDOW_DAYS": ("tracking_window_days", int),
            "MORRIS_IMPROVEMENT_COST_CEILING_MONTHLY_USD": ("cost_ceiling_monthly_usd", float),
        }
        for env_key, (attr, coerce) in env_map.items():
            val = os.environ.get(env_key)
            if val is not None:
                try:
                    setattr(cfg, attr, coerce(val))
                except (ValueError, TypeError):
                    pass
        return cfg

    @classmethod
    def load(cls, toml_path: str | None = None) -> "ImprovementConfig":
        """Load config from TOML file (if available) then apply env overrides.

        Falls back to defaults + env overrides if TOML file is absent or
        tomllib is not available.
        """
        cfg = cls()

        if toml_path is not None:
            try:
                import tomllib  # Python 3.11+
                with open(toml_path, "rb") as f:
                    data = tomllib.load(f)
                improvement = data.get("morris", {}).get("improvement", {})
                for key, val in improvement.items():
                    if hasattr(cfg, key):
                        setattr(cfg, key, val)
            except (FileNotFoundError, ImportError, Exception):
                pass

        # Environment overrides take precedence over TOML
        return cls.from_env()
