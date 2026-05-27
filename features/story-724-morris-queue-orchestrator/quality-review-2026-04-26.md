# Deep Quality Review — 2026-04-26

**Reviewer:** Bot Derrick (Opus orchestrator)
**Scope:** All Phase 8 implementations completed today across STORY-723, 724, 725, 726, 727
**Frame:** Compare against yesterday's failure classes (701 — implemented-but-untested; 720 — logic inversion / fake mocks)

---

## Executive Summary

Today's stories ship 85+ passing tests across five worktrees. Most behaviors are exercised end-to-end. **Three Class 701 gaps** and **two Class 720 risks** were identified, plus **one CRITICAL adversarial-reviewer self-bypass** that lets the gate fail silently when the Anthropic API is unreachable or the API key is missing.

| Area | Verdict | Notes |
|---|---|---|
| STORY-723 adversarial reviewer parsing | APPROVE | parse_adversarial_review well-covered |
| STORY-723 reviewer integration in poller | **BLOCK** | No test exercises the BLOCK path through dispatch_poller |
| STORY-723 fail-open on API error | **BLOCK** | API error / missing key → returns APPROVE; gate silently bypassed |
| STORY-724 orchestrator detectors | APPROVE_WITH_CAVEATS | detect_stale_phase and detect_stale_heartbeat have NO test coverage |
| STORY-724 interventions | APPROVE_WITH_CAVEATS | post_needs_info_surface only tested in dry_run; DM body never asserted |
| STORY-725 parse_seed_phase_path | APPROVE | All 4 variants tested |
| STORY-726 jitter backoff | APPROVE_WITH_CAVEATS | Test asserts `>= 0.5` but doesn't verify randomness |
| STORY-726 durable completion guard | APPROVE | T6+T8 cover write+read+poller-skip end-to-end |
| STORY-727 self-improvement loop | APPROVE | T1–T10 cover detect → propose → approve → apply → track |

---

## Section 1 — Class 701 Gaps Found (implemented but untested)

### 1.1 [CRITICAL] `detect_stale_phase` has zero test coverage
- **Location:** `deployment/morris/scripts/detectors.py:141-170`
- **Evidence:** `grep detect_stale_phase tests/deployment/test_morris_orchestrator_724.py` returns nothing.
- **Risk:** The function applies a different threshold (`phase_stale_minutes` = 45) and a different precondition (`updated_at > claimed_at` AND `heartbeat is None`). None of these branches is exercised. A bug here (e.g. comparing against `claimed_at` instead of `updated_at`) would never be caught by tests but would mis-fire releases against in-progress stories.
- **Spec source:** seed lists this as a P1 detector; classify_all() invokes it on every cycle.
- **Required fix:** Add behavioral test that constructs a row with `updated_at = now-46min`, `claimed_at = now-2h`, `heartbeat = None`, and asserts a single `StaleClaimRecord(reason="phase_stale")` is returned. Also a NEGATIVE test where `updated_at = claimed_at` (no progress made — should NOT fire because `detect_stale_never_started` handles that case).

### 1.2 [CRITICAL] `detect_stale_heartbeat` has zero test coverage
- **Location:** `deployment/morris/scripts/detectors.py:110-133`
- **Evidence:** No reference to `detect_stale_heartbeat` in test_morris_orchestrator_724.py.
- **Risk:** A claimed story with a stale heartbeat (>15min since last beat) is the most common stuck-claim signal in production. If this detector silently returns `[]` due to a bug, no stale claims are ever released and the dispatch queue fills up.
- **Required fix:** Add test with `claim_heartbeat_at = now-16min`, assert one record with `reason="heartbeat_stale"`. Symmetric negative test at `now-5min`.

### 1.3 [HIGH] `post_needs_info_surface` DM body never asserted
- **Location:** `deployment/morris/scripts/interventions.py:266-289`
- **Evidence:** Only test (TC-12) is `dry_run=True` — it asserts zero HTTP calls. The `dry_run=False` branch — which formats the DM body containing `STORY-{r.story_id}` and `r.age_hours` — is never invoked under assertion.
- **Risk:** The implementation could omit the story_id, the age_hours, the `>{decay_hours}h` threshold prefix, or the `[INFO]` severity tag, and tests would still pass.
- **Required fix:** Add a `dry_run=False` test that posts a `NeedsInfoRecord(story_id=500, age_hours=5.0)` and asserts the DM body contains "STORY-500", "5.0", "[INFO]", and "4h".

### 1.4 [HIGH] Adversarial reviewer integration with dispatch_poller is untested
- **Location:** `deployment/hermes/dispatch_poller.py:998-1019` (the `_adv_enabled` block)
- **Evidence:** `test_adversarial_reviewer_723.py` covers `parse_adversarial_review` and `run_adversarial_review` in isolation, but no test calls into `dispatch_poller` or simulates a BLOCK verdict and verifies `_report_complete` is **not** called.
- **Risk:** This is the EXACT integration that was built today. A bug in the gating code (e.g. `if _adv_result.get("dispatch_fix_task"):` checking the wrong key, or `success = False` not propagating to the `_report_complete` guard) silently lets a BLOCKED story complete on the server. STORY-723's whole purpose is defeated.
- **Required fix:** Add an integration test that mocks `run_adversarial_review` to return `{"verdict": "BLOCK", "dispatch_fix_task": True, ...}` and asserts that `_report_complete` is NOT called.

### 1.5 [MEDIUM] `run_briefing` count assertion is weak
- **Location:** `tests/deployment/test_morris_orchestrator_724.py:574-603` (TC-8b)
- **Evidence:** TC-8b asserts `"pending" in call_body.lower() or "2" in call_body`. The literal word "Pending:" appears unconditionally in the briefing template, so this OR-branch passes regardless of actual count. The "2" branch is also weak — any timestamp containing "2" satisfies it.
- **Required fix:** Build a queue with exactly N pending items and assert "Pending: {N}" appears in the body. Negative test: with 0 pending, assert "Pending: 0" not "Pending: 2".

---

## Section 2 — Class 720 Risks Found (logic-inversion-resistant?)

### 2.1 [HIGH] TC-2b accepts both sides of the threshold boundary
- **Location:** `tests/deployment/test_morris_orchestrator_724.py:162-181`
- **Evidence:** The test docstring says "exactly at threshold (15min) is NOT stale (strictly greater than)" — but the assertion is only `assert isinstance(result, list)`. Both `count==0` and `count==1` pass this assertion.
- **Risk:** If the implementation uses `>=` instead of `>` (or vice versa), the test will not catch it. The 15-min boundary is the exact line that `detect_stale_never_started` checks at; an off-by-one here means stuck claims go undetected for one extra cycle (or fresh claims get released early).
- **Required fix:** Pick one side of the strictly-greater behavior the implementation uses (`age > threshold`, line 93 of detectors.py — strict greater) and write the assertion to match: at exactly 15min, `result == []`.

### 2.2 [MEDIUM] Jitter backoff doesn't assert randomness
- **Location:** `tests/deployment/test_parallel_coordination_726.py:99-102`
- **Evidence:** `assert sleep_calls[0] >= 0.5`. If the implementation degenerated to `delay = 0.5` (constant, no jitter), this test would still pass and two agents would still thunder-herd at exactly the same delay.
- **Risk:** Class 720 — the test passes a deterministic implementation that violates the spec ("jitter").
- **Required fix:** Run the function 10 times and assert at least 5 distinct delay values are observed (proves randomness), OR patch `random.uniform` and assert it was called.

### 2.3 [LOW] TC-5b indirect-only verification of 24h-window exclusion
- **Location:** `tests/deployment/test_morris_orchestrator_724.py:345-377`
- **Evidence:** Test passes 2 recent + 1 old failure; threshold=3 → expected `[]`. If the implementation included the old one (count=3), the test would FAIL — so it does catch the bug. **Verdict: actually adequate.** No fix needed.

### 2.4 [LOW] dry_run zero-call assertions are correct
- **Location:** TC-12 family
- **Evidence:** Tests use `assert session.post.call_count == 0`. This is a strong assertion that catches both "raised exception" and "made calls" bugs equivalently. **No fix needed.**

---

## Section 3 — Adversarial Reviewer Gate Bypass Risks

### 3.1 [CRITICAL] API failure → APPROVE_WITH_CAVEATS (gate bypassed silently)
- **Location:** `deployment/hermes/adversarial_reviewer.py:251-253`
- **Evidence:**
  ```python
  except Exception as exc:
      logging.warning("adversarial_reviewer: API call failed: %s", exc)
      return f"## Verdict\nAPPROVE_WITH_CAVEATS\n\n## Findings\n\n### HIGH\n- [H-1] Review skipped due to API error: {exc}\n..."
  ```
  An API timeout, 429, network error, or any other Exception causes the reviewer to return APPROVE_WITH_CAVEATS, which makes `should_merge=True`. The gate has been bypassed without any operator notification.
- **Risk:** This is the precise failure mode that classes 701/720 were established to prevent. A dead Anthropic key, network blip, or API-level rate-limit silently disables the gate. Any future BLOCK-worthy regression slips through.
- **Required fix:** On API exception, return `verdict=ERROR` and surface as `should_merge=False` (fail-closed). Only allow `--allow-fail-open` flag for explicit operator overrides.

### 3.2 [CRITICAL] Missing API key → APPROVE silently
- **Location:** `deployment/hermes/adversarial_reviewer.py:228-230`
- **Evidence:**
  ```python
  if not api_key:
      return "## Verdict\nAPPROVE\n\n## Findings\n\n### CRITICAL\nNone.\n..."
  ```
  If `ANTHROPIC_API_KEY` is unset on the agent VM, every story silently passes the gate. The misconfiguration is invisible and the parser sees a clean APPROVE.
- **Risk:** Same as 3.1 but worse — there is no warning log even at WARNING level (only an info "skipped — no ANTHROPIC_API_KEY" comment in the coverage table).
- **Required fix:** When key is missing, return `verdict=ERROR` with `should_merge=False`, AND emit a WARNING log so journalctl can surface "agent X has no ANTHROPIC_API_KEY". A misconfigured agent must NOT be allowed to ship code.

### 3.3 [HIGH] Cached review SHA-collision is not verified
- **Location:** `deployment/hermes/adversarial_reviewer.py:288-296`
- **Evidence:** The SHA check is `if f"sha:{current_sha}" in cached:`. If the cached file has any text matching `sha:abc123`, the substring match passes. A malicious or accidental "Saw sha:abc123 in commit log" paragraph in the cached body would cause re-runs to be skipped on a different SHA.
- **Required fix:** Match against the structured header: `if cached.startswith(f"<!-- sha:{current_sha} -->"):`. The unit test `test_tc11_cached_review_returned_without_api_call` already writes the header in this format so the fix is already well-structured — only the parser is loose.

### 3.4 [MEDIUM] Adversarial reviewer logs but does not gate on UNKNOWN
- **Location:** `parse_adversarial_review` line 143-145
- **Evidence:** `should_merge = verdict in ("APPROVE", "APPROVE_WITH_CAVEATS")`. UNKNOWN correctly fails-closed (`should_merge=False`). **Verdict: actually adequate.**

---

## Section 4 — Recommended Fixes (priority-ordered)

### MUST-FIX before next deploy

1. **Adversarial reviewer fail-closed on API error/missing key** (Section 3.1, 3.2)
2. **Add integration test for BLOCK→skip-_report_complete** (Section 1.4)
3. **Add tests for `detect_stale_phase` and `detect_stale_heartbeat`** (Section 1.1, 1.2)

### SHOULD-FIX this week

4. **Tighten TC-2b boundary assertion** (Section 2.1)
5. **Add `post_needs_info_surface` body assertion** (Section 1.3)
6. **Strengthen jitter randomness assertion** (Section 2.2)
7. **Tighten run_briefing count assertion** (Section 1.5)
8. **SHA-header strict match in cached review parser** (Section 3.3)

### NICE-TO-HAVE

9. (None this round.)

---

## Section 5 — Tests Added by This Review

The following test additions are committed alongside this report. Each test is a behavioral test that catches a specific gap or inversion:

- `tests/deployment/test_morris_orchestrator_724.py`:
  - `TestStalePhaseCoverage::test_phase_stale_fires_after_threshold` (Gap 1.1)
  - `TestStalePhaseCoverage::test_phase_stale_silent_when_fresh` (Gap 1.1, boundary)
  - `TestStalePhaseCoverage::test_phase_stale_silent_when_no_progress_since_claim` (Gap 1.1)
  - `TestStaleHeartbeatCoverage::test_heartbeat_stale_fires` (Gap 1.2)
  - `TestStaleHeartbeatCoverage::test_heartbeat_fresh_silent` (Gap 1.2)
  - `TestNeedsInfoSurfaceBody::test_dm_body_contains_story_id_and_age` (Gap 1.3)
  - `TestNeedsInfoSurfaceBody::test_dm_body_contains_severity_and_threshold` (Gap 1.3)
  - `TestNeedsInfoSurfaceBody::test_empty_records_does_not_post` (Gap 1.3 negative)
  - `TestRunBriefingExactCounts::test_briefing_body_contains_exact_pending_count` (Gap 1.5)
  - `TestStaleNeverStartedBoundary::test_exactly_at_threshold_does_not_fire` (Gap 2.1)

- `tests/deployment/test_parallel_coordination_726.py`:
  - `TestJitterBackoffRandomness::test_jitter_delays_are_distinct` (Gap 2.2)

- `tests/deployment/test_adversarial_reviewer_723.py`:
  - `TestApiFailureFailsClosed::test_missing_api_key_returns_error_verdict` (Gap 3.2)
  - `TestApiFailureFailsClosed::test_api_exception_returns_error_verdict` (Gap 3.1)
  - `TestPollerIntegration::test_block_verdict_skips_report_complete` (Gap 1.4)

These tests are RED until the corresponding implementation fixes land. They are NOT skipped — they will fail and block CI until the bypass paths are closed.

---

## Section 6 — Out-of-scope Observations

- **Cross-story consistency:** The five stories were all merged today via separate worktrees. No integration test exercises 723's gate alongside 727's improvement loop. Recommend an end-to-end smoke that posts a BLOCK verdict, confirms _report_complete is skipped, and confirms the failure flows into 727's pattern_detector corpus.
- **Operator-visible signal for gate bypass:** Even after fixing 3.1/3.2, operators have no dashboard tile for "stories shipped without adversarial review." Recommend an `adversarial_review_skipped_total` counter exported via the metrics endpoint.
