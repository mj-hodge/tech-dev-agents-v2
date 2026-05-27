# Analysis Report — STORY-542
# Framework-Level Playwright Enforcement for Frontend Stories

## Summary
| Metric | Value |
|--------|-------|
| Approaches evaluated | 3 |
| Top recommendation | Approach B — Full Dual-Gate (Seed-Aligned) |
| Confidence | High |
| Divergent assessments | 1 |

---

## Context Weights

| Dimension | Weight | Rationale |
|-----------|--------|-----------|
| Technical Soundness | 25% | The seed's acceptance criteria are machine-verifiable contract tests — technical correctness is objectively measurable, not a judgment call. Approach violations are caught by the compliance test suite itself. |
| Future Flexibility | 20% | Cross-project submodule reuse (advertising-amazon + future projects) is a stated goal. Enforcement must be extensible to new file types/keywords without refactoring. |
| Business Value | 25% | Mark's directive is explicit and unambiguous: "playwright is part of the core SDLC." Partial enforcement does not satisfy the directive. |
| Implementation Effort | 15% | Medium scope with no deadline pressure. Effort matters but is not the constraint. |
| Risk Profile | 15% | CI blast-radius is real (hard gate + unvalidated condition = merge-blocking outage) but mitigable with a pre-committed smoke spec and a CI condition contract test. |

---

## Evaluation Agents

| Agent | Dimensions | Approaches Scored |
|-------|-----------|------------------|
| Technical | Soundness (1-5) + Flexibility (1-5) | A, B, C |
| Business | Value (1-5) + Effort (1-5) | A, B, C |
| Risk | Risk Profile (1-5) + Risk Register | A, B, C |

---

## Approaches

| ID | Name | Summary |
|----|------|---------|
| A | Minimal-Surface: Single Gate Only | `_is_frontend_story()` plugged into Phase 7 deliverable check only. CI soft gate (`continue-on-error: true`). No Acceptance Diff gate change. Root-level `playwright.config.ts`. |
| B | Full Dual-Gate (Seed-Aligned) | `_is_frontend_story()` plugged into BOTH `_verify_deliverable` (Phase 7) AND `_verify_acceptance_diff`. Hard CI gate. `e2e/playwright.config.ts` collocated. Detect-step output variable for CI condition reuse. |
| C | Dual Gates, Soft CI | Same dual gate as B. CI soft (`continue-on-error: true`). Root-level `playwright.config.ts`. No baseline screenshot mechanism. |

---

## Scoring Matrix

| Approach | Technical Soundness (×0.25) | Future Flexibility (×0.20) | Business Value (×0.25) | Implementation Effort (×0.15) | Risk Profile (×0.15) | **Weighted Total** |
|----------|----------------------------|---------------------------|----------------------|------------------------------|---------------------|-------------------|
| A | 2 → 0.50 | 2 → 0.40 | 2 → 0.50 | 5 → 0.75 | 4 → 0.60 | **2.75** |
| B | 5 → 1.25 | 5 → 1.00 | 5 → 1.25 | 2 → 0.30 | 2 → 0.30 | **4.10** |
| C | 3 → 0.75 | 3 → 0.60 | 3 → 0.75 | 3 → 0.45 | 3 → 0.45 | **3.00** |

---

## Divergent Assessment

**Approach A — Technical constraint vs. casual risk trade-off:**  
The Business evaluator scored Approach A's soft CI gate as an effort win (5/5 effort). The Technical evaluator identified a structural contradiction: the seed's `test_test_workflow_has_playwright_job_gating_frontend_changes` compliance test explicitly **asserts the absence of `continue-on-error`** in the workflow. Approach A cannot pass its own contract test. This is not a product risk trade-off — it is a build-time failure. Approach A is technically unsound independent of risk appetite.

---

## Top 3 Ranking

### 1. Approach B — Full Dual-Gate (Weighted: 4.10)
- **Why #1:** Only approach that satisfies all contract tests AND Mark's directive. Dual gates cover both Phase 7 path and the Acceptance Diff path (the lesson from STORY-528 is that the Acceptance Diff gate is the harder backstop — it fires even if a spec was present at Phase 7 but dropped before completion). Hard CI gate is the only configuration that satisfies `test_test_workflow_has_playwright_job_gating_frontend_changes`.
- **Key strength:** Technical completeness — both enforcement points covered, CI condition correct, config collocated with specs, detect-step output variable enables future conditional job reuse.
- **Key risk (R-B-1):** Hard gate on an empty `e2e/` directory will cause every PR to fail until a smoke spec is committed. Detect-step output variable pattern has no existing precedent in this repo's workflow; a malformed condition silently evaluates to false-negative.
- **Trade-off:** Highest implementation effort (2/5). Phase 6 must specify the detect-step CI pattern precisely and require a pre-committed smoke spec. Phase 8 must validate the `if:` condition with a test PR before enabling the hard gate.

### 2. Approach C — Dual Gates, Soft CI (Weighted: 3.00)
- **Why #2:** Correctly implements the dual Python gate (both `_verify_deliverable` and `_verify_acceptance_diff`) which is the most important runtime enforcement. Soft CI avoids the merge-blocking blast radius of Approach B.
- **Key strength:** Operationally safer than B for initial rollout; the dual Python gate closes the Acceptance Diff loophole that Approach A misses.
- **Key risk (R-C-1):** Soft CI means broken frontend tests never block a merge — the enforcement signal degrades over time and becomes ignored noise in the Actions tab. Also, `playwright.config.ts` at root creates a layout mismatch with the seed spec and skill guidance (both reference `e2e/` as spec home).
- **Trade-off:** Pays most of B's implementation cost but delivers weaker enforcement. An "awkward middle" — neither A's simplicity nor B's completeness. Requires a follow-on hardening story to achieve what B delivers now.

### 3. Approach A — Minimal-Surface: Single Gate Only (Weighted: 2.75)
- **Why #3:** Lowest effort, but technically unsound. The soft CI gate contradicts the seed's own compliance test assertion. The single Phase 7 gate leaves the Acceptance Diff path unprotected — a story can still Complete without a Playwright spec if the spec existed at Phase 7 but was dropped before Acceptance Diff verification.
- **Key strength:** Fastest to ship, zero CI blast radius risk.
- **Key risk (R-A-1):** Acceptance Diff gate stays silent on missing Playwright specs. The STORY-513 failure class is only partially addressed. Also: Approach A fails `test_test_workflow_has_playwright_job_gating_frontend_changes` by definition.
- **Trade-off:** Not a viable approach for this story as written — it cannot satisfy its own acceptance criteria.

---

## Risk Register Highlights (Top 3 Approaches)

| ID | Approach | Risk | Severity | Probability | Mitigation |
|----|----------|------|----------|-------------|-----------|
| R-B-1 | B | Hard-gate CI on empty `e2e/` — every PR fails until smoke spec is committed | High | Med | Pre-commit a minimal passing `e2e/smoke.spec.ts` in the same PR before enabling the hard gate |
| R-B-2 | B | Detect-step output variable malformed → `if:` silently false → gate never fires for any frontend PR | High | Med | Contract test in `test_sdlc_framework_compliance.py` YAML-parses the workflow and asserts the detect step writes to `GITHUB_OUTPUT` and the job's `if:` references the correct step output path |
| R-B-3 | B | `toHaveScreenshot()` baseline not in git → CI fails on first run with "missing baseline" | Med | High | Run `npx playwright test --update-snapshots` locally, commit `e2e/screenshots/` baselines in the same PR |
| R-C-1 | C | Soft CI — broken frontend tests never block a merge; signal degrades to noise | Med | Med | Document as intentional; schedule follow-on hardening story; add monitoring on Playwright CI job failure rate |
| R-C-3 | C | `playwright.config.ts` at root conflicts with seed spec and skill guidance both referencing `e2e/` | Med | Med | Move config to `e2e/playwright.config.ts` to co-locate with specs |
| R-A-1 | A | Acceptance Diff gate unmodified — frontend story can Complete without Playwright spec | High | Med | Unmitigable within Approach A's constraints; requires upgrading to B or C |

### Cross-Cutting Risk (All Approaches)

The `.sdlc` submodule skill patches are applied by Mark manually. If Mark applies patches to `.sdlc` but does not bump the submodule pointer in `advertising-amazon` (or future consuming repos), those projects get zero Playwright enforcement with no automated signal. No approach mitigates this automatically — it requires: (1) a clear `MANUAL-STEPS.md` handback, and (2) a submodule-currency contract test in each consuming repo. Phase 6 must specify both.

---

## Recommendation

**Implement Approach B (Full Dual-Gate, Seed-Aligned).**

Approach B is the only approach that:
1. Satisfies the seed's machine-verifiable contract tests (including `test_test_workflow_has_playwright_job_gating_frontend_changes`)
2. Closes both enforcement paths (Phase 7 deliverable gate AND Acceptance Diff gate)
3. Delivers the hard CI merge gate that Mark's directive requires
4. Collocates `e2e/playwright.config.ts` consistently with the spec home

The two elevated risks (R-B-1, R-B-2) are concrete and mitigable: Phase 6 must specify (a) a pre-committed smoke spec as Day-1 deliverable, and (b) the contract test that validates the CI detect-step output-variable pattern. Phase 8 must validate the `if:` condition with a real test PR before the story is marked complete.

What Approach B sacrifices: highest implementation effort (2/5). This is the correct trade-off for a foundational enforcement feature — the effort is a one-time cost; the absence of enforcement is a recurring tax on every frontend story.

---

## Decisions Locked

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| D-1 | Gate placement | Both `_verify_deliverable` (Phase 7) AND `_verify_acceptance_diff` | Dual-layer enforcement; Acceptance Diff is the harder backstop |
| D-2 | CI enforcement level | Hard gate (`continue-on-error: false`) | Seed's contract test requires absence of `continue-on-error`; soft gate fails compliance suite |
| D-3 | CI condition pattern | Detect-step → output variable → `if: ${{ steps.detect.outputs.is_frontend == 'true' }}` | Cleaner reuse; must be validated by YAML contract test |
| D-4 | Playwright config location | `e2e/playwright.config.ts` | Co-located with specs; consistent with seed spec and skill guidance |
| D-5 | Smoke spec pre-commit | Required in same PR | Prevents Day-1 CI breakage from empty `e2e/` directory |
