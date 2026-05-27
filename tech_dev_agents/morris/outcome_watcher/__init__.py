"""outcome_watcher — STORY-886

Closes the feedback loop on Morris's findings ledger.  Scans recently-closed
PRs and updates `outcome: pending` entries with their true classification:

  validated      — the agent pushed a fix that addresses the finding before merge
  false_positive — the finding was rebutted in comments and the PR merged anyway
  false_negative — Morris said APPROVE but a revert/hotfix appeared afterwards
  unresolved     — PR closed without merge after a finding was raised

Usage (cron / skill):
    from tech_dev_agents.morris.outcome_watcher import run_once
    result = run_once()   # returns {"checked": N, "updated": N, "skipped": N}

Command-line (cron):
    python3 -m tech_dev_agents.morris.outcome_watcher
"""

from .watcher import run_once  # noqa: F401

__all__ = ["run_once"]
