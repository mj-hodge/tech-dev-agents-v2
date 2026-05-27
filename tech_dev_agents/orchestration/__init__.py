"""V2 dispatch orchestration — phase-aware execution with v2 lease/event contract.

STORY-860: Restores poller-side orchestration that was stripped during the v2
cutover (commit 89202f2a, 2026-05-02). Provides branch lifecycle, rework
threading, phase events, phase-scoped failure classes, and resume-aware retry.
"""
