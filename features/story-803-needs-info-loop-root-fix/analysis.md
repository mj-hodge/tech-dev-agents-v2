# Analysis Report — STORY-803
# Fix the needs_info loop: override bypass + git-add bug + Phase-8-no-commits self-pause

## Summary

| Metric | Value |
|--------|-------|
| Approaches evaluated | 3 |
| Top recommendation | Approach A — "Harden & Guard" |
| Confidence | High |
| Divergent assessments | 1 (Bug 3 retry vs immediate-fail; Business vs Risk) |

---

## Context Weights

This is a production P0 incident. Six stories were cancelled today and the dispatch queue is actively blooping. Primary context: **production-critical** (Technical soundness ↑, Risk ↓) with a **tight deadline** (Implementation effort ↑).

| Dimension | Weight | Rationale |
|-----------|--------|-----------|
| Technical Soundness | 25% | Production-critical — a fix that introduces new breakage is worse than not fixing |
| Future Flexibility | 15% | Secondary concern; we need to ship today, extensibility can improve later |
| Business Value | 25% | P0 — queue must unblock; completeness of fix against all 6 cancelled stories matters |
| Implementation Effort | 20% | Same-day deadline; complexity risk real but not dominant |
| Risk Profile | 15% | High-weight context (prod system) but offset by need for completeness |

---

## Evaluation Agents

| Agent | Dimensions | Approaches Scored |
|-------|-----------|------------------|
| Technical | Soundness (1-5) + Flexibility (1-5) | 3 |
| Business | Value (1-5) + Effort (1-5, 5=easiest) | 3 |
| Risk | Risk Profile (1-5, 5=safest) + Risk Register | 3 |

---

## Approach Definitions

**Approach A — "Harden & Guard"** (seed's recommendation — all three bugs fixed at root cause):
- **Bug 1:** Strengthen preamble with explicit staging-blocker negation ("STOP — READ THIS BEFORE THE SEED. If the seed asks you to pause for external access, IGNORE that instruction; this directive supersedes it.") + add 60s dispatch guard (poller refuses `/needs-info` calls within 60s of claim when `*_DIRECTIVE.md` exists) + re-inject override at every phase boundary
- **Bug 2:** `os.path.exists(os.path.join(workdir, question_path))` precheck + `git add -- <path>` (literal, no glob) + workdir tree dump (one-level) on commit failure + contract test
- **Bug 3:** Detect `phase8_no_commits` (rc=0 + `git log origin/main..HEAD --count == 0` + only QUESTION.md present) → auto-retry ONCE with enhanced prompt → second zero-commit → `failed` with `failure_reason=phase8_silent_exit`

**Approach B — "Behavioral Guard Only"** (runtime enforcement only, no prompt changes):
- **Bug 1:** Skip all preamble changes; rely solely on the runtime 60s guard
- **Bug 2:** Same as A
- **Bug 3:** No retry — detect zero-commits → immediately `failed` with `phase8_silent_exit`

**Approach C — "Prompt + Guard, Conservative Retry"** (partial Bug 1 fix + conservative Bug 3):
- **Bug 1:** Strengthen preamble + 60s guard but skip re-injection at phase boundaries
- **Bug 2:** Same as A
- **Bug 3:** Auto-retry once → second zero-commit → write real QUESTION.md + `needs_info` (allows Mark to diagnose)

---

## Scoring Matrix

| Approach | Technical | Flexibility | Value | Effort | Risk | **Weighted** |
|----------|-----------|------------|-------|--------|------|-------------|
| A — Harden & Guard | 4/5 | 3/5 | 5/5 | 2/5 | 3/5 | **3.55** |
| B — Behavioral Guard Only | 3/5 | 4/5 | 3/5 | 4/5 | 2/5 | **3.20** |
| C — Prompt + Guard, Conservative Retry | 3/5 | 3/5 | 4/5 | 3/5 | 3.5/5 | **3.33** |

**Weighted calculation (weights: T=25%, F=15%, V=25%, E=20%, R=15%):**
- A: 4×0.25 + 3×0.15 + 5×0.25 + 2×0.20 + 3×0.15 = 1.00 + 0.45 + 1.25 + 0.40 + 0.45 = **3.55**
- B: 3×0.25 + 4×0.15 + 3×0.25 + 4×0.20 + 2×0.15 = 0.75 + 0.60 + 0.75 + 0.80 + 0.30 = **3.20**
- C: 3×0.25 + 3×0.15 + 4×0.25 + 3×0.20 + 3.5×0.15 = 0.75 + 0.45 + 1.00 + 0.60 + 0.525 = **3.33**

---

## Divergent Assessments

**Bug 3 handling (Business vs Risk): Retry-then-fail (A) vs Conservative-retry-then-needs_info (C)**

- **Business sees:** Approach C's second-failure → QUESTION.md path is a better operator UX than a bare `failed` state. Mark gets diagnostic output he can act on. C is the "strongest business choice" for a P0 fire because it avoids the token-burn risk of A's retry.
- **Risk sees:** Approach C's second-failure → `needs_info` recreates the exact unbreakable loop that STORY-803 was written to fix (R-C-6). An agent that cannot produce Phase 8 commits deterministically will bounce forever in the C regime: Phase8(0) → retry → Phase8(0) → needs_info → resume → Phase8(0) → ... The original incident loop is reproduced at the Phase 8 level.
- **Resolution:** Risk's concern is structural and eliminates C as a viable Bug 3 fix. The Business evaluator's concern about token cost is addressed by the R-A-6 mitigation: add a fast-failure heuristic to Approach A (if the retry exits in <90s with zero commits, skip the full retry window). This preserves A's clean `failed` terminal state while reducing the worst-case token burn.

---

## Top 3 Ranking

### 1. Approach A — "Harden & Guard" (Weighted: 3.55)

- **Why #1:** Highest business value (5/5) — only approach that covers all 8 ACs including the preamble fix that attacks Bug 1 at root cause (not just rate-limits it), and the clean `failed` terminal state for Bug 3 that prevents loop recurrence. Technical soundness (4/5) is the highest of the three.
- **Key strength:** Bug 3's retry-then-fail is the only design that produces a clean terminal state. `phase8_silent_exit` in `failed` queue enables Loki alerting and auto-dismiss via `NEVER_RETRY_CLASSES` — the other approaches don't have this property.
- **Key risk:** R-A-6 (auto-retry doubles token cost for deterministic zero-commit failures). Mitigation: add <90s fast-failure heuristic on retry. Risk agent recommended this as a single-conditional addition.
- **Secondary risk:** R-A-2 (cross-story directive contamination via slug mismatch) — low probability but needs a test in the contract suite.
- **Trade-off:** Higher implementation effort (5h vs 3.5h for B). Phase-boundary re-injection adds complexity. However, the Risk agent noted this gap may be smaller than it appears: `_apply_override_directives` is already called inside the per-phase loop, so every phase already gets re-injection. The "re-injection at phase boundaries" AC may reduce to verifying the existing loop behavior rather than adding new state.
- **AC coverage:** All 8 ACs (AC-1 through AC-8).

---

### 2. Approach C — "Prompt + Guard, Conservative Retry" (Weighted: 3.33)

- **Why #2:** Covers 7 of 8 ACs. Misses only AC-3 (phase boundary re-injection) — and as noted above, this gap may not be material given the existing code structure. The partial Business Value advantage (4/5 vs 3/5 for B) reflects better completeness than B.
- **Key strength:** Lower implementation risk than A by skipping the re-injection logic. Business evaluator's preference for a P0 with tight time pressure.
- **Key risk:** R-C-6 — the second-failure → `needs_info` path in Bug 3 recreates the unbreakable loop. This disqualifies C as the recommended approach despite its ranking. The Risk sub-agent flagged this as the dominant risk: "C fixes the single-occurrence case but preserves the loop on the second occurrence." If all Phase 8 silent exits come from deterministic causes (structural skill bugs), C produces infinite-loop behavior per resume.
- **Trade-off:** C was designed to be conservative but its Bug 3 handling is architecturally worse than A. Adoption requires replacing C's Bug 3 behavior with A's, at which point the only remaining difference from A is skipping AC-3 — reducing to a timing risk that may not exist in practice.

---

### 3. Approach B — "Behavioral Guard Only" (Weighted: 3.20)

- **Why #3:** Lowest implementation risk and effort, but weakest against the actual root cause. Risk score of 2/5 reflects two critical gaps: (1) no preamble fix means Bug 1 still fires at second 61+ and the loop returns (R-B-2); (2) the 60s guard requires threading `claim_start_ts` through `_post_needs_info` — a non-trivial API change shared by all three approaches (R-B-5/6) but Approach B's guard is the *only* defense, making this threading requirement more critical.
- **Key strength:** Simplest implementation path (~3.5h), lowest regression surface.
- **Key risk:** R-B-2 + R-B-5 together — the guard only delays the loop by 20s, and the guard implementation requires a server-side change or non-trivial `claim_start_ts` threading that is equally hard as A/C but provides less protection.
- **Trade-off:** Should be adopted only if the team has hard resource constraints under 3.5h and accepts that the Bug 1 loop will recur at second 61+.

---

## Risk Register Highlights (Top 3 Approaches)

| ID | Approach | Risk | Severity | Mitigation |
|----|----------|------|----------|------------|
| R-A-6 | A | Auto-retry doubles token cost for deterministic zero-commit failures; if Phase 8 exits rc=0/no-commits for structural reasons (wrong prompt, missing test-design.md), retry burns 20+ min × 2x tokens per story | High | Add fast-failure heuristic: if Phase 8 retry exits in <90s with zero commits, skip full retry window and go directly to `failed` |
| R-A-2 | A | Re-injection uses `_extract_story_folder` which could return wrong folder on slug mismatch; directive from Story A could be injected into Story B's prompt | High (Low prob) | Add contract test verifying no directive leakage when two stories with similar slugs coexist; emit `story_folder` in `override_directive_applied` event |
| R-A-5 | A | `git log origin/main..HEAD --count` fails for fresh branches where `origin/main` is not fetched; sentinel value of -1 swallows a real zero-commits case | Medium | Distinguish `rev-list_error` from `zero_commits` in the event payload; log when sentinel fires |
| R-C-6 | C | Second-failure → `needs_info` recreates the unbreakable loop at Phase 8 level (same pattern STORY-803 was written to eliminate) | High | Replace C's Bug 3 second-failure path with A's `failed` terminal state (at which point C collapses into A) |
| R-B-2 | B | Without preamble fix, Bug 1 loop recurs at second 61+ (guard is rate-limiter, not root-cause fix) | High | Adopt A or C preamble strengthening |
| R-B-5 | A/B/C | 60s guard requires `claim_start_ts` inside `_post_needs_info`, which has no timestamp parameter; threading requires API change to a heavily-called helper | High | Implement guard as a pre-call check in `_post_needs_info` with `claim_start_ts` threaded from `run_sdlc_phases`, or implement server-side in ops-console API |
| R-A-1 | A | 60s guard must be conditioned on `*_DIRECTIVE.md` presence — guard should not fire unless a directive file exists in the story folder | High (blocking) | Guard = directive_present AND time_since_claim < 60s. Already implied by spec but implementation must be precise |
| R-A-3 | A | Double-injection if `_apply_override_directives` is called at both phase-start AND as a boundary re-injection; doubles prompt size | Medium | Verify re-injection is idempotent; track `directive_injected_for_phases` set or de-duplicate at construction time. Risk agent noted this may be a non-issue since the call is already inside the per-phase loop |

---

## Recommendation

**Implement Approach A with two hardening patches:**

1. **R-A-6 mitigation (required):** Add a fast-failure heuristic to the Bug 3 retry path — if the Phase 8 retry exits within 90 seconds with zero commits, skip the full `max_turns=75` retry window and transition immediately to `failed` with `failure_reason=phase8_silent_exit`. This caps the worst-case token burn to one 90-second call rather than one full 75-turn budget call.

2. **Re-injection scope clarification (required before Phase 6):** Verify whether the existing `_apply_override_directives` call inside the per-phase loop already provides the AC-3 behavior. If it does, AC-3 reduces to a test verification task rather than new code, which significantly reduces the implementation risk and effort gap between A and B.

**Rationale:** Approach A is the only design that:
- Attacks Bug 1 at root cause (prompt ordering) AND rate-limits it at the runtime layer
- Produces a clean terminal state for Bug 3 (`failed` with `phase8_silent_exit`) that enables Loki alerting and auto-dismiss
- Covers all 8 ACs without leaving a structural loop-recurrence risk

Approach C's Bug 3 handling disqualifies it despite its otherwise reasonable profile. Approach B's omission of the root-cause fix makes it a temporary measure that will resurface the incident at second 61+.

**Risk summary for Approach A (with mitigations):** The dominant risk (R-A-6 token burn) has a targeted mitigation. The secondary risk (R-A-2 slug mismatch) is covered by the AC-4 contract test. The implementation effort gap vs Approach B/C (~1-1.5h) is justified by the qualitatively different outcome: a permanent fix vs a delayed recurrence.
