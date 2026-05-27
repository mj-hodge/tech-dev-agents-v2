# STORY-773 — Morris Fleet-Vigilance Check 16: needs_info Volume + Content-Similarity Detection

## Overview
| Field | Value |
|-------|-------|
| Mode | feature_update |
| Scope | small |
| Feature Name | needs_info pattern detection in fleet-vigilance |
| Phase Path | 1 → 7 → 8 → Done |
| Repo | tech-dev-agents |
| Frontend | false |
| Hard Dep | STORY-767 (fleet-vigilance Checks 9-15) — STORY-773 adds Check 16 to the same skill |
| Related | STORY-727 (continuous self-improvement loop — broader pattern detection; this story is the targeted v1) |

## Problem Statement

The entire self-healing stack today is **failure-centric**. STORY-700, 701, 702, 723, 724, 741, 762, 763, 767, requeue-failed — every check looks at `status='failed'` rows or absent heartbeats. **Nothing watches for repeating QUESTION.md content across `needs_info` stories.**

2026-04-30 incident:
- 11 stories simultaneously in `needs_info` over a 24-48 hour window.
- Every QUESTION.md contained near-identical content: "Phase X was dispatched but my seed's Phase Path does not include X" or variants.
- Mark eyeballed the queue and noticed the pattern in 30 seconds.
- The fleet's automated stack noticed nothing for 48 hours.
- The actual root cause (phase router ignoring seed's Phase Path field, fixed by STORY-772) was a systemic bug affecting every dispatched story silently.

The blind spot: **content-clustering of needs_info questions is the highest-leverage signal for systemic bugs**, and it's not being captured.

## Target User / Use Case

**User:** Morris (fleet-vigilance heartbeat skill running every 30 min).
**Today:** Morris's checks are blind to needs_info patterns. The same question repeats N times before a human notices.
**After this story:** Morris computes a similarity score across needs_info QUESTION.md content and CRITs when ≥3 stories in the last 24h share ≥40% bigram overlap or contain shared anchor phrases. He DMs Mark with the clustered story IDs + a sample of the shared question text.

## Success Criteria

1. **SC-1 — Fetch all needs_info QUESTION.md content.** Morris queries `dispatch_items` for `status='needs_info'` rows whose `paused_at` (or equivalent timestamp) is within the last 24 hours. For each row, retrieves the QUESTION.md content via gh API (preferred) using `repo`, `branch` (derive from convention), `needs_info_path`. SSH fallback if gh API fails.
2. **SC-2 — Bigram-similarity scoring.** For each pair of fetched questions, compute Jaccard similarity over word bigrams (after stripping markdown headers, dates, story IDs). Cluster pairs with similarity ≥ 0.40 into groups.
3. **SC-3 — Anchor-phrase detection.** ALSO scan questions for known systemic-bug anchor phrases — initial set: "phase path does not include", "was dispatched but", "blocked on STORY-", "branch_setup_failed", "git checkout main failed". Multiple matches = same bug class. Anchor list is documented + extensible via env var `FV_NEEDS_INFO_ANCHORS_FILE`.
4. **SC-4 — CRIT threshold.** Cluster size ≥ 3 (after dedup) → CRIT. DM Mark with: cluster size, story IDs, oldest paused_at, sample of shared text (first 200 chars).
5. **SC-5 — DM throttling.** Same cluster signature shouldn't DM more than once per 6 hours. Suppression state in `/home/hermes/state/morris/needs-info-cluster-suppression.json`.
6. **SC-6 — Logging.** Every check cycle logs `[FLEET-VIGILANCE Check 16] needs_info: N stories scanned, M clusters detected, K CRIT`.
7. **SC-7 — Integration with existing fleet-vigilance.** Plugs into Check 16 slot per STORY-767's structure. Fails safe (if check itself errors, log + continue, don't abort the cycle).
8. **SC-8 — Zero regressions** on existing fleet-vigilance Checks 0-15.

## Verification Plan

| SC | Command | Expected |
|----|---------|----------|
| SC-1 | `pytest tests/morris/test_needs_info_pattern_check.py::test_fetch_needs_info_questions -v` | PASSED |
| SC-2 | `pytest tests/morris/test_needs_info_pattern_check.py::test_bigram_similarity_clustering -v` | PASSED |
| SC-3 | `pytest tests/morris/test_needs_info_pattern_check.py::test_anchor_phrase_detection -v` | PASSED |
| SC-4 | `pytest tests/morris/test_needs_info_pattern_check.py::test_crit_threshold_three_or_more -v` | PASSED |
| SC-5 | `pytest tests/morris/test_needs_info_pattern_check.py::test_dm_suppression_six_hours -v` | PASSED |
| SC-6 | Existing fleet-vigilance fleet-health.md output includes Check 16 line | Documented |
| SC-7 | `pytest tests/morris/test_fleet_vigilance_*.py -v` | All pass; existing checks 9-15 unmodified |
| SC-8 | `pytest tests/ -x --ignore=tests/e2e -q` | Zero regressions |

## Test Criteria

- **Tests use fixture QUESTION.md content** that mirrors the 2026-04-30 incident — 5 simulated needs_info questions, 4 of which contain "phase path does not include", and 1 unrelated.
- Assertions: 4 form a cluster, 1 is alone, CRIT triggered.
- **Mock subprocess + mock urllib + mock asyncpg.** No live SSH or gh API in tests.
- All deterministic, < 2 sec total.

## Validation

| Step | Command | Pass |
|------|---------|------|
| 1 | `pytest tests/morris/test_needs_info_pattern_check.py -v` | All ≥ 7 tests GREEN |
| 2 | `pytest tests/ -x --ignore=tests/e2e -q` | Full suite GREEN; zero regressions |
| 3 | After deploy: re-create the 2026-04-30 incident in a test fixture (5 needs_info stories sharing "phase path does not include"). Run Morris vigilance cycle. Verify CRIT DM was sent. | Documented in PR body |
| 4 | Verify negative case: 5 unrelated needs_info questions → no DM | Documented |

## Acceptance Criteria

- [ ] AC-1: New section "Check 16: needs_info Pattern Detection" in `deployment/vm/skills/fleet-vigilance/SKILL.md` matching Checks 9-15 structure.
- [ ] AC-2: New helper `deployment/morris/scripts/needs_info_pattern_check.py` with `run_check()` function callable from heartbeat-collector.
- [ ] AC-3: Bigram parser strips markdown headers, dates, ISO timestamps, story IDs (regex-replaced with `<STORY>`/`<DATE>` tokens) before similarity computation.
- [ ] AC-4: Jaccard similarity ≥ 0.40 OR ≥ 2 anchor-phrase matches in shared content → considered same cluster.
- [ ] AC-5: CRIT threshold = 3 stories in cluster. WARN threshold = 2 stories (logged, no DM).
- [ ] AC-6: DM payload includes: cluster size, story IDs (truncated to 5 if larger), oldest paused_at timestamp, 200-char sample of shared text.
- [ ] AC-7: 6-hour DM suppression per cluster signature (signature = sorted hash of cluster member story IDs + dominant anchor phrase).
- [ ] AC-8: Anchor phrases configurable via `/home/hermes/.hermes/needs_info_anchors.txt` (one per line). Default ships with the 5 listed in SC-3.
- [ ] AC-9: Logging — `[FLEET-VIGILANCE Check 16] N scanned, M clusters, K CRIT, L suppressed` per cycle.
- [ ] AC-10: Existing checks 0-15 unmodified. Tests pass with zero regressions.
- [ ] AC-11: Error/logging AC — when fetching a QUESTION.md fails (gh API 404, branch deleted), skip that story and log; don't abort the check.

## Constraints
| Constraint | Value |
|------------|-------|
| Budget | small — pure-Python text similarity + cron integration |
| Timeline | URGENT — every day without this check is a day systemic bugs hide in needs_info |
| Tech | Python 3.12 stdlib (re, collections.Counter, hashlib, math) + gh CLI; no new deps |

## Performance Requirements
- Scan time for 20 needs_info stories: < 5 seconds total (gh API calls + similarity matrix).
- Bigram clustering: O(N²) is acceptable since N typically < 30.

## Security Constraints
- [ ] Cluster signature uses hashed story IDs, not raw content (no PII / business sensitive content in suppression state).
- [ ] DM payload truncates question samples to 200 chars to avoid leaking sensitive content.
- [ ] No new auth surface; reuses existing API key + gh CLI auth.

## Operational Lifecycle
- **Configuration:** `FV_NEEDS_INFO_THRESHOLD` (default 0.40), `FV_NEEDS_INFO_SUPPRESS_HOURS` (default 6), `FV_NEEDS_INFO_ANCHORS_FILE` (default `~/.hermes/needs_info_anchors.txt`).
- **Tuning:** edit anchors file + restart heartbeat cron. Threshold changes via env var.
- **Monitoring:** Loki picks up Check 16 lines. Spike in cluster CRITs = systemic-bug indicator.

## Boundaries

| Always Do | Ask First | Never Do |
|-----------|-----------|----------|
| Fetch via gh API first, SSH fallback | Whether to use LLM clustering instead of bigrams (broader STORY-727 scope) | Run a live LLM call from a heartbeat cron |
| Cluster by bigram similarity AND anchor phrases (both signals) | Whether to add semantic embedding-based clustering as v2 | Use only one signal (would miss either repeated-phrasing OR same-anchor-different-words bugs) |
| Truncate samples in DMs to avoid leaking content | Whether to share full QUESTION.md content via DM (probably no — link to git instead) | Print credentials/secrets if accidentally in a question |
| Skip stories whose QUESTION.md can't be fetched | Whether to ALSO scan paused / failed rows for similar content | Block fleet-vigilance if a single fetch fails |
| Suppress duplicate DMs by cluster signature | Whether suppression should reset on operator action | Page Mark every 30 min on the same cluster |

## Files to Modify

- `deployment/vm/skills/fleet-vigilance/SKILL.md` — add Check 16 section.
- `deployment/morris/scripts/needs_info_pattern_check.py` — **new**, the check helper.
- `deployment/morris/scripts/heartbeat-collector.py` — invoke Check 16 after Check 15.
- `tests/morris/test_needs_info_pattern_check.py` — **new**, ≥ 7 tests with fixture content.
- `~/.hermes/needs_info_anchors.txt` — **new**, default anchor phrases (committed under `deployment/morris/config/needs_info_anchors.txt` and copied via push-code).
- `features/story-773-needs-info-pattern-detection/test-design.md` — Phase 7 deliverable.
- `.project`, `backlog.md` — tracking.

## Files to NOT Modify

- Existing Checks 0-15 — additive only.
- `dispatch_items` schema — no migrations.
- The /resume API — orthogonal (this story detects, doesn't auto-resume).
- Frontend — out of scope.

## Done Looks Like

```
$ pytest tests/morris/test_needs_info_pattern_check.py -v
test_fetch_needs_info_questions PASSED
test_bigram_similarity_clustering PASSED
test_anchor_phrase_detection PASSED
test_crit_threshold_three_or_more PASSED
test_dm_suppression_six_hours PASSED
test_phase_routing_incident_replay PASSED
test_unrelated_questions_no_cluster PASSED
========== 7 passed in 0.45s ==========

# After deploy, fleet-health.md from a vigilance cycle:
[Check 16 needs_info pattern] CRIT: cluster of 4 stories share anchor "phase path does not include"
  - STORY-008, STORY-009, STORY-014, STORY-015
  - Oldest paused_at: 2026-04-29T12:38:53Z
  - Sample: "Phase 2 (Research) was dispatched for STORY-008, but STORY-008's phase path..."
  - Cluster signature: a1b2c3 (suppressed for 6h)
  - DM sent to Mark
```

## Escalation Contract

1. **Cluster size ≥ 5 in last 1 hour** — escalate immediately (skip 6h suppression). Likely an active outage.
2. **Anchor phrase list grows beyond 20 entries** — that's a smell; we're chasing symptoms instead of fixing root causes. Refactor to use STORY-727 broader pattern detection.
3. **Performance regresses past 5s per cycle** — switch to TF-IDF + cosine instead of Jaccard bigrams.
4. **DM-suppression file gets corrupted** (parse error) — log WARN, ignore the file (treat all clusters as un-suppressed for this cycle), recreate file at end.
5. **A QUESTION.md fetch returns content > 100KB** — truncate to 100KB before scoring; log WARN.

**Default if no story-specific rule fires:** if more than 1 turn is spent guessing, write QUESTION.md and pause.

## Codebase Context

| Aspect | Details |
|--------|---------|
| Affected files | `deployment/vm/skills/fleet-vigilance/SKILL.md`, `deployment/morris/scripts/needs_info_pattern_check.py` (new), `deployment/morris/scripts/heartbeat-collector.py` (invoke new check) |
| Reference incident | 2026-04-30 — 11 needs_info stories from phase-routing bug; system silent for 48 hours. |
| Architecture | Morris cron → fleet-vigilance markdown → Python helper → gh API + asyncpg + bigram math + Teams MCP DM |
| Test pattern | mock subprocess + mock urllib + tmp_path fixtures + fixture QUESTION.md strings |

## Out of Scope

- LLM-based semantic clustering (STORY-727 scope).
- Auto-resolution of detected clusters (this story is detect-only — Mark or STORY-770 skill resolves).
- Cross-cluster pattern detection over weeks (broader STORY-727 scope).
- Slack/email notifications (Teams DM only for v1).

## Notes for Implementer

- The 2026-04-30 incident's QUESTION.md files are committed under `features/story-{008,009,011,014,015}-target-*/QUESTION.md` on their respective story branches. Use the actual content as test fixtures (anonymize timestamps).
- Bigram similarity is intentionally simple (Jaccard over word bigrams). Avoid heavy deps. We can upgrade later.
- Anchor phrases are "concrete bug indicators" — phrases that, if present, are evidence of a known bug class. Initial 5 chosen from observed patterns: routing bugs, dependency bugs, branch setup bugs.
- DM suppression key = sha256(sorted(story_ids) + dominant_anchor)[:12]. Consistent across cycles, changes when cluster membership changes.
- This is the v1 of the broader vision in STORY-727. Ship small + iterate.
