# STORY-1014 — gc-data-v2 S7 security fix + P0-alert mechanism for 🔴 Active platform-gaps findings

**Story ID:** STORY-1014
**Scope:** Medium
**Epic:** STORY-1000 (Cross-Repo Canon Alignment)
**Work-Stream:** D — Knowledgebase curation
**Repos touched (cross-repo):**
- `gc-data-v2` (primary — telemetry fix, regression test, CI/alert workflow)
- `tech-dev-agents` (Morris cron task wiring — only if alerting mechanism A is chosen; SDLC artifacts always)
**Date:** 2026-05-18
**Status:** Phase 1 — seed draft, gated awaiting approval
**Frontend:** false

---

## Problem

**Two coupled problems:**

1. **Active security finding S7 has been documented in canon for 7 days and unfixed.**
   `gc-data-v2/platform/platform-gaps.md:37` reads:
   > **S7** | 🔴 | **Critical Gap G5 (walmart-v2 scrubber ordering bug)** — documented in canon but unfixed. Scrubber and exporter are inverted in `telemetry/otel.py`; secrets can leak to AppInsights. | **Active security finding, sitting in canon for 7 days.**

   The bug shape: the OpenTelemetry pipeline registers the AppInsights exporter **before** the secret-scrubber span processor. OTel runs span processors in registration order. So exports fire BEFORE scrubbing, meaning any span attribute that carries a credential (e.g. an Authorization header captured by an instrumentation library) lands in AppInsights raw.

2. **No mechanism alerts when a finding is 🔴 Active.**
   Platform-gaps.md is a static document. A 🔴 finding can sit untouched for 7+ days with nobody paged. There is no CI gate that watches the file. No skill scans it. The doc relies on a human reading it.

The epic's SC-1 calls these out: incident class "retro proposals not shipped" — S7 IS a retro/audit finding (recommended in § P0 of platform-gaps.md, line 69: "Fix **S7** scrubber ordering — 30 min — Active security finding") that hasn't shipped.

## Goal

1. **Fix S7.** Re-order the OTel pipeline so the secret-scrubber span processor runs **before** the AppInsights span exporter. Ship a regression test that injects a secret-shaped string into a span attribute and asserts the exported payload has it redacted.
2. **Build a P0-alert mechanism** that fires whenever a row in `gc-data-v2/platform/platform-gaps.md` is marked `🔴` AND its category is `Active` (vs. `Open`, `Planned`, or `🟢`). Mechanism choice TBD in Phase 6 (two candidates below). The mechanism must alert within 24h of a 🔴 Active row appearing.
3. **Update `platform-gaps.md` row S7** from `🔴` to `🟢` (or `~~struck through~~ → Closed YYYY-MM-DD by STORY-1014`) once the fix lands.

## Scope

**In:**
- Locate the OTel wiring chain. Phase 6 will determine the exact file. Candidates from the canon: `gc-data-v2/platform/observability.md` may reference a location; the actual implementation likely lives in `gc-data-v2/pipeline-template/src/<package>/telemetry/otel.py` (referenced by S7 wording "`telemetry/otel.py`"). The platform docs may also point to a per-pipeline copy in `walmart-supplier-v2` — but per the epic's "Files to NOT modify" rule, we change canon (the pipeline-template), and downstream consumers re-vendor via scaffold drift-check.
- Reorder span processor registration: scrubber FIRST, exporter SECOND.
- Add a regression test: build a `Tracer`, start a span, set an attribute `"http.request.header.authorization" = "Bearer sk-live-deadbeef"`, force-flush, inspect the exported payload (use an in-memory exporter for the test), assert the attribute value is redacted (e.g. `"<REDACTED>"` or matches the scrubber's replacement pattern).
- Mechanism choice (Phase 6 decision; two candidates):
  - **Option A — Morris weekly cron:** new task in `tech-dev-agents/deployment/vm/skills/morris/active-findings-scan/` invoked by Morris's crontab. Scans `platform-gaps.md`; if any row matches `🔴` AND status mentions "Active", post a high-severity Teams alert to ops channel.
  - **Option B — GitHub Actions daily:** new workflow `gc-data-v2/.github/workflows/active-findings-alert.yml` that runs on `schedule: cron: '0 14 * * *'` (10:00 ET), parses the markdown, opens a GitHub issue tagged `P0-active-finding` if any 🔴 Active row exists and no open issue already references that row's identifier (S1..S7, D1..D5, etc.). Closes the issue automatically when the row is resolved.
- RED tests for both the scrubber regression and the alert mechanism.

**Out:**
- Fixing other 🔴 findings (S1–S6, D1–D5, DA1–DA6, M2–M6). Those become alert-driven follow-ups.
- Building the SLO/freshness alert system (S1+S2). That's a P0 in platform-gaps.md but separately scoped.
- Editing the canon `platform-gaps.md` taxonomy or table structure (only the S7 status cell changes).

## Out of scope (explicit Do-Not-Do)

- Do NOT modify `gc-data-v2/platform/security.md`, `observability.md`, or any other `platform/*.md` doc beyond the S7 status flip in `platform-gaps.md`.
- Do NOT rewrite the scrubber regex/logic — only re-order the pipeline. The scrubber itself is assumed correct; S7 is wiring order, not algorithm.
- Do NOT touch any downstream `*-v2` pipeline repo's `telemetry/otel.py` — they re-vendor from `gc-data-v2/pipeline-template` via the scaffold drift-check workflow.
- Do NOT add new alert categories (yellow/orange) or change the 🔴/🟡/🟢 vocabulary.

## Success criteria

- **SC-1** — `pytest gc-data-v2/pipeline-template/tests/telemetry/test_scrubber_ordering.py -v` is GREEN: an injected secret-shaped string in a span attribute is `<REDACTED>` in the exported payload.
- **SC-2** — Inspection of the wired pipeline file (Phase 6 names it) shows the scrubber span-processor `add_span_processor()` call appears in source order **before** the AppInsights exporter `add_span_processor()` call.
- **SC-3** — `git -C gc-data-v2 grep -n "🔴.*Active security" platform/platform-gaps.md` returns no matches after the S7 fix is merged (the row's status cell has been updated).
- **SC-4** — The chosen alert mechanism (A or B) is wired and tested:
  - If A: a Morris cron entry exists (`crontab -l \| grep active-findings-scan` on Morris VM), the skill SKILL.md is present, dry-run posts to a test Teams channel.
  - If B: `gh -R hpi-gorillacommerce/gc-data-v2 workflow list \| grep active-findings-alert` returns the row; manually triggering the workflow with a test fixture (extra 🔴 Active row) opens a GitHub issue with label `P0-active-finding`.
- **SC-5** — End-to-end alert latency ≤ 24h: with the mechanism wired, inject a synthetic `🔴` row labelled "Active" into a fixture copy of `platform-gaps.md`, run the scan once, confirm the alert fires within the run (mechanism A: Teams message; mechanism B: GH issue).
- **SC-6** — Regression: removing the scrubber registration (or moving it back behind the exporter) re-RED's `test_scrubber_ordering.py` — proves the test actually validates ordering.
- **SC-7** — CHANGELOG.canon.md (added in STORY-1013) gets a `pipeline-template/v1.1.x` (or v1.2.0) entry naming this fix. Sequencing: STORY-1013 must merge first; STORY-1014 then bumps the patch/minor.

## Files to modify (grouped by repo)

### `gc-data-v2`
- `pipeline-template/src/<package>/telemetry/otel.py` (or wherever Phase 6 confirms the OTel wiring lives — exact path to be finalized in feature-spec.md).
- `pipeline-template/tests/telemetry/test_scrubber_ordering.py` — NEW regression test.
- `platform/platform-gaps.md` — row S7 status update **only** (cells: status `🔴 → 🟢`, gap text struck through with `~~...~~ → Closed 2026-XX-XX by STORY-1014`). The table structure stays untouched.
- `CHANGELOG.canon.md` (created by STORY-1013) — append entry under the appropriate `pipeline-template/v1.x.0` heading.

**If Option B (GitHub Actions alert):**
- `.github/workflows/active-findings-alert.yml` — NEW.
- `scripts/scan_platform_gaps.py` — NEW parser/issue-opener (or reuse the `check_sources_completeness.py` pattern from STORY-1013 as structural template).
- `tests/test_scan_platform_gaps.py` — NEW RED test set.

**If Option A (Morris cron):**
- (no file changes in `gc-data-v2`)
- See `tech-dev-agents` section below.

### `tech-dev-agents` (only if Option A is chosen)
- `deployment/vm/skills/morris/active-findings-scan/SKILL.md` — NEW skill.
- `deployment/vm/skills/morris/active-findings-scan/scan.py` — NEW scanner.
- `deployment/vm/morris-crontab.txt` (or wherever Morris's crontab is canonicalized) — add weekly entry.

### `tech-dev-agents` (always — SDLC artifacts)
- `features/story-1014-s7-security-and-alerts/seed.md` (this file)
- `features/story-1014-s7-security-and-alerts/feature-spec.md` (Phase 6 — includes mechanism A-vs-B decision)
- `features/story-1014-s7-security-and-alerts/test-design.md` (Phase 7)

## Files to NOT modify

- Any `*-v2` pipeline repo (e.g. `walmart-supplier-v2`, `spapi-reports-v2`). Canon fix only; downstream re-vendor via drift-check.
- `gc-data-v2/platform/observability.md`, `security.md`, `auth-patterns.md` — canon docs, referenced not edited.
- `gc-data-v2/platform/platform-gaps.md` rows other than **S7**.
- Other `🔴 Active` findings (S1–S6, etc.) — they get alerted on, not fixed in this story.

## Verification plan

| Check | Command | Expected output |
|---|---|---|
| Locate OTel wiring | `git -C /mnt/c/Projects/gc-data-v2 grep -ln "add_span_processor\|TracerProvider" -- pipeline-template/` | At least one file path |
| Scrubber registered before exporter (line order) | `python -c "import ast; ..."` or `grep -n add_span_processor <file>` shows scrubber line < exporter line | Scrubber on lower line number |
| RED test for ordering | `pytest /mnt/c/Projects/gc-data-v2/pipeline-template/tests/telemetry/test_scrubber_ordering.py -v` (Phase 7) | FAIL (RED) — `assert "<REDACTED>" in exported_payload` fails because raw secret leaks |
| GREEN after fix | Same command (Phase 8) | PASS |
| Negative-control: regression | Revert the ordering fix; rerun pytest | FAIL again (RED) — proves test is meaningful |
| S7 status flipped | `grep -c "S7.*🔴.*Active" /mnt/c/Projects/gc-data-v2/platform/platform-gaps.md` | `0` |
| S7 closure recorded | `grep -c "S7.*Closed.*STORY-1014" /mnt/c/Projects/gc-data-v2/platform/platform-gaps.md` | `1` |
| Alert mechanism A wired | `ssh morris@<vm> 'crontab -l \| grep active-findings-scan'` | One cron row, weekly cadence |
| Alert mechanism A fires on fixture | Run `python deployment/vm/skills/morris/active-findings-scan/scan.py --fixture tests/fixtures/extra-active.md --dry-run` | stdout names the synthetic 🔴 row; would-post message captured |
| Alert mechanism B wired | `gh -R hpi-gorillacommerce/gc-data-v2 workflow list \| grep active-findings-alert` | Row present |
| Alert mechanism B fires on fixture | `gh -R hpi-gorillacommerce/gc-data-v2 workflow run active-findings-alert.yml -f fixture=tests/fixtures/extra-active.md` then `gh issue list -l P0-active-finding` | New issue opened with link to fixture row |
| Existing-row de-dup | Run mechanism twice on same fixture; assert only ONE issue (or message) | No duplicate |
| Auto-close on green | Flip fixture row to 🟢; re-run mechanism; original issue/alert closed | Issue state = closed (mechanism B) or no re-alert (mechanism A) |

## Boundaries

| Always Do | Ask First | Never Do |
|---|---|---|
| Write the scrubber regression test BEFORE touching `otel.py` (RED first) | Choose between Mechanism A (Morris cron) vs B (GH Action) — Phase 6 surfaces this as a design decision; Mark approves | Modify the scrubber's regex or the exporter's destination |
| Use an in-memory OTel exporter for the regression test (no live AppInsights) | Add new severity categories (yellow/orange) to platform-gaps.md taxonomy | Touch other 🔴 findings — only S7 in this story |
| Reference the exact file/line in commit message (e.g. `pipeline-template/src/.../telemetry/otel.py:42`) | Tag a `pipeline-template/v1.x.0` bump for this fix | Send a real Teams alert from a dev/test run (must be dry-run with explicit `--dry-run` flag) |
| Confirm fix on a manual smoke trace before merging | Edit `platform-gaps.md` table structure | Leak the synthetic secret string used in tests into commit messages or logs |

## Done looks like

```
$ pytest /mnt/c/Projects/gc-data-v2/pipeline-template/tests/telemetry/test_scrubber_ordering.py -v
test_secret_in_authorization_header_is_redacted ............. PASSED
test_secret_in_arbitrary_attribute_is_redacted .............. PASSED
test_non_secret_attribute_passes_through .................... PASSED

$ grep "S7" /mnt/c/Projects/gc-data-v2/platform/platform-gaps.md
| **S7** | 🟢 | ~~Critical Gap G5 (walmart-v2 scrubber ordering bug)~~ → **Closed 2026-XX-XX by STORY-1014** | Resolved. |

# If mechanism B chosen:
$ gh -R hpi-gorillacommerce/gc-data-v2 workflow run active-findings-alert.yml -f fixture=tests/fixtures/extra-active.md
$ gh -R hpi-gorillacommerce/gc-data-v2 issue list -l P0-active-finding
#1234  P0 Active Finding: S99 — synthetic test row  open

# If mechanism A chosen:
$ ssh morris@<vm> 'crontab -l | grep active-findings'
0 14 * * 1  /opt/agent/skills/morris/active-findings-scan/scan.py >> /var/log/morris/active-findings.log 2>&1
```

## Escalation contract

- If Phase 6 cannot locate the OTel wiring file in `gc-data-v2/pipeline-template/`, file `needs_info` for Mark — the S7 description ("telemetry/otel.py") may be approximate; we need the canonical location before fix.
- If the regression test cannot be made deterministic (e.g. exporter is async-only), pause and ask: should we use `SimpleSpanProcessor` (synchronous) for the test, or pin a force-flush + timeout?
- Mechanism choice A-vs-B: surface in `feature-spec.md` with explicit pros/cons; Mark picks. Do NOT pick autonomously.
- If the fix uncovers additional security gaps (e.g. headers are captured before any scrubber sees them), STOP and file a new story; do not expand scope.


## Test Criteria

| Test | Validates |
|------|-----------|
| RED tests from Phase 7 (see `## Verification plan`) | Every SC has a literal test or shell command |
| Full pytest suite GREEN, zero regressions | No unrelated breakage |
| Incident-replay tests (where applicable) | The 2026-05-04..05-18 failure classes are catchable by the new gate |

See `## Success criteria` and `## Verification plan` above for SC-keyed predicates.


## Validation

After merge:

1. **Tests:** all RED tests turned GREEN; `pytest <story test paths>` exits 0.
2. **No regressions:** full suite passes; CI green on the PR.
3. **Per-SC verification:** every command in `## Verification plan` runs and produces the expected output.
4. **Tracking updated:** `.project` and `backlog.md` reflect Phase 8 completion; Monday.com task updated.

## Phase path

**Medium scope → `1 → 6 → 7 → 8 → Done`.**

Phase 6 (a) locates the OTel wiring file, (b) decides mechanism A vs B, (c) sketches the test harness. Phase 7 writes both RED tests (scrubber ordering + alert-fires-on-fixture). Phase 8 ships the reorder, regression test, mechanism wiring, and the `platform-gaps.md` S7 status flip in a single PR. Done = SC-1..SC-7 verified.

## Sequencing dependency

- **Requires STORY-1013 merged first** (so `CHANGELOG.canon.md` exists for the entry). If STORY-1013 stalls, STORY-1014 can ship the scrubber fix + test independently, and add the CHANGELOG entry as a follow-up commit.

## Mechanism A vs Mechanism B — Phase 6 decision matrix

Phase 6 must make this decision explicitly. Both are documented here so Phase 6 has the trade-off framing in hand at the start.

**Option A — Morris weekly cron (preferred-default)**

| Pro | Con |
|---|---|
| Lives in `tech-dev-agents`; reuses Morris's existing skill plumbing and crontab | Requires Morris VM to be up — single point of failure |
| Posts to Teams directly (high-signal channel) | Skill must be deployed via `push-code.sh` (see CLAUDE.md "Deploy requires restart" rule) |
| Easy to add other canon scans later (`gc-data-v2/sources/`, `platform/*.md` drift) | Adds load to Morris (negligible — markdown parse, weekly) |
| Cadence: weekly (configurable) | 24h SLA met only if cron run frequency ≥ daily; weekly is 7-day worst-case — Phase 6 may need to tighten cadence to daily |

**Option B — GitHub Action daily**

| Pro | Con |
|---|---|
| Self-contained in `gc-data-v2`; no Morris dependency | Alerts via GH issue (lower urgency than Teams) |
| Runs in GH-hosted infra; no VM operational cost | Adds CI minutes |
| 24h SLA easy (`schedule: cron: '0 14 * * *'`) | Issue spam risk if dedup logic is wrong |

**Recommendation:** Option A with **daily** cadence to meet SC-5's 24h SLA, plus Option B as a backstop on weekly cadence if Morris is offline > 48h. Phase 6 finalizes; do NOT bind to either before Phase 6.

## Reference — exact platform-gaps.md line being closed

`gc-data-v2/platform/platform-gaps.md` line 37 (current state):

```
| **S7** | 🔴 | **Critical Gap G5 (walmart-v2 scrubber ordering bug)** — documented in canon but unfixed. Scrubber and exporter are inverted in `telemetry/otel.py`; secrets can leak to AppInsights. | **Active security finding, sitting in canon for 7 days.** |
```

`gc-data-v2/platform/platform-gaps.md` line 69 (closes-this in P0 table):

```
| Fix **S7** scrubber ordering | 30 min | Active security finding |
```

Phase 8 must edit both rows: line 37 (status flip + strike-through gap text + closure marker) and line 69 (strike through the recommendation, since it's now closed).
