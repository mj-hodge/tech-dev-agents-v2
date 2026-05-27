# STORY-1014 — Feature Spec (Phase 6)

**Story:** S7 telemetry scrubber inversion fix + P0-alert mechanism for 🔴 Active platform-gaps findings
**Date:** 2026-05-18
**Repo (primary):** `gc-data-v2`
**Branch:** `story-1014/s7-security-and-alerts`
**Phase path:** `1 → 6 → 7 → 8 → Done`

---

## 1. OTel wiring — where it lives, what's wrong, what fix lands

### 1.1 Discovery

The seed says S7 lives at `telemetry/otel.py`. Actual state in `gc-data-v2`:

| File | Status | Notes |
|---|---|---|
| `pipeline-template/src/pipeline_v2/telemetry/scrubber.py` | EXISTS | `SecretScrubbingProcessor` (correct, dual-interface SpanProcessor + logging.Filter) |
| `pipeline-template/src/pipeline_v2/telemetry/logging_setup.py` | EXISTS | `setup_logging()` wires scrubber into root logger |
| `pipeline-template/src/pipeline_v2/telemetry/otel.py` | **MISSING** | Referenced by `azure/function-app/pipeline_v2_runner/__init__.py:24` and by `pipeline-standard.md` § 9, but never authored |
| `walmart-supplier-v2/.../telemetry/otel.py` | EXISTS (downstream) | Has the inverted ordering per `pipeline-standard.md` § G5 |

**Reading.** `platform/pipeline-standard.md:359-368` is unambiguous: scrubber MUST be the FIRST processor on the `TracerProvider`; the exporter (`BatchSpanProcessor`) registers SECOND. `setup_otel()` MUST runtime-assert this and raise `RuntimeError` if `ScrubProcessor` is not at index 0. `platform/pipeline-standard.md:368` requires the gating test at `tests/security/test_scrubber_order.py`.

**Decision.** This story creates the canonical `otel.py` in `pipeline-template/` with the correct ordering and the runtime assertion. Downstream `*-v2` pipelines re-vendor via `canon-drift-check`. We do NOT touch `walmart-supplier-v2`'s copy (per epic boundary "Files to NOT modify").

### 1.2 Canonical `otel.py` shape

```python
def setup_otel(service_name: str) -> None:
    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)

    # SCRUBBER FIRST — must be at index 0 (canonical, gated by runtime assertion).
    provider.add_span_processor(SecretScrubbingProcessor())

    # EXPORTER SECOND — only registered if an OTLP endpoint is configured.
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if endpoint:
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))

    trace.set_tracer_provider(provider)

    # Runtime assertion (pipeline-standard.md § 9).
    processors = list(provider._active_span_processor._span_processors)  # type: ignore[attr-defined]
    if not processors or not isinstance(processors[0], SecretScrubbingProcessor):
        raise RuntimeError(
            f"ScrubProcessor must be at SpanProcessor index 0; saw {[type(p).__name__ for p in processors]}"
        )
```

**Why this order matters.** OTel's `SynchronousMultiSpanProcessor` / `ConcurrentMultiSpanProcessor` invokes `on_end` on registered processors in **registration order**. The scrubber mutates `span._attributes` in-place during `on_end`. If the exporter (`BatchSpanProcessor.on_end`) runs first, the span snapshot it queues for export captures the un-redacted attribute dict, and later mutation by the scrubber is invisible to the queued snapshot. Result: raw `Authorization: Bearer ...` headers ship to the collector.

### 1.3 Pipeline before/after

| | Before (bug) | After (fix) |
|---|---|---|
| Processor[0] | `BatchSpanProcessor(exporter)` OR `SecretScrubbingProcessor(inner=BatchSpanProcessor(...))` | `SecretScrubbingProcessor()` |
| Processor[1] | (none) OR scrubber wrapped inside [0] | `BatchSpanProcessor(OTLPSpanExporter())` (conditional on `OTEL_EXPORTER_OTLP_ENDPOINT`) |
| Export carries secrets | YES | NO — redacted to `[REDACTED]` |
| Runtime check | none | `RuntimeError` at startup if order wrong |

---

## 2. P0-alert mechanism — Option B (GitHub Actions daily)

### 2.1 Decision

**Recommend Option B** (GitHub Action daily) for STORY-1014, with the following rationale.

| Criterion | Option A (Morris cron) | Option B (GH Action) | Winner |
|---|---|---|---|
| Lives where the data lives | `tech-dev-agents` (separate repo) | `gc-data-v2` (same repo) | **B** — file + alert co-located, easier for data team to maintain |
| SLA (24h) achievable | Cron supports daily, but requires Morris VM up | `schedule: cron: '0 14 * * *'` — GH infra | Tie (B slightly more reliable) |
| Operational cost | Adds Morris load; needs `push-code.sh` deploys | Zero VM cost; GH-hosted minutes (negligible markdown parse) | **B** |
| Alerting channel | Teams (high-signal) | GitHub issue tagged `P0-active-finding` (visible to data team, links to file) | A is higher-signal in real-time, but B aligns with how the data team already triages |
| Dedup | Needs Morris-side state | `gh issue list -l P0-active-finding` is the dedup state — idempotent | **B** |
| Failure mode if mechanism breaks | Morris VM down → silent | Workflow failure → GH notifies repo admins | **B** |
| Cross-repo coupling | Morris must know about gc-data-v2 path | Self-contained in gc-data-v2 | **B** |

**Tradeoffs accepted with Option B:**
- GitHub issues are slower-signal than Teams (mitigated by tagging team and using `P0-active-finding` label that data team can wire to their own notifications).
- Option A remains a backstop the data team can add later if 24h SLA via issues proves insufficient. STORY-1014's responsibility is just to land a *working alert*; Option B fully meets the seed's SC-4 and SC-5.

### 2.2 Workflow design

**File:** `.github/workflows/active-findings-alert.yml`
**Schedule:** `cron: '0 14 * * *'` (10:00 ET daily)
**Triggers:** `schedule`, `workflow_dispatch` (with optional `fixture` input for testing)

**Behaviour:**

1. Checkout repo.
2. Run `python scripts/scan_platform_gaps.py` (with optional `--fixture <path>`).
3. The script parses `platform/platform-gaps.md` (or the fixture file), extracting every row in the table whose status cell contains `🔴` AND whose gap text or risk text contains the literal word `Active` (case-sensitive — matches the doc's vocabulary like "Active security finding").
4. For each such row, derive a stable identifier from the leading `**Sn**` / `**Dn**` / `**DAn**` / `**Mn**` cell.
5. Compare against current open GH issues with label `P0-active-finding` (filtered by a marker line in the issue body: `<!-- active-finding-id: S7 -->`).
6. For findings without an open issue → create one with title `P0 Active Finding: <ID> — <one-line summary>`, label `P0-active-finding`, body containing: finding ID, the raw markdown row, a link to the file at the row's line, and the marker comment.
7. For open issues whose corresponding row is no longer 🔴 → close the issue with a comment "Finding <ID> closed in `platform-gaps.md` — auto-closed by `active-findings-alert.yml`."
8. Output a job summary listing actions taken.

**Idempotence:** because identifier-to-issue mapping uses the HTML-comment marker in the issue body, the second run on the same state opens 0 issues and closes 0 issues.

**Auth:** uses the workflow's default `GITHUB_TOKEN` with `issues: write` permission. No extra secrets.

### 2.3 Parser script — `scripts/scan_platform_gaps.py`

CLI:
```
scan_platform_gaps.py [--file platform/platform-gaps.md] [--fixture <path>] [--dry-run] [--json]
```

Library functions (for unit tests, never call the GH API at import time):
- `parse_findings(path: Path) -> list[Finding]` — pure parser over the markdown
- `Finding(id: str, status: str, summary: str, risk: str, is_active: bool, line_no: int)` — dataclass
- `active_findings(path: Path) -> list[Finding]` — filter to 🔴 + "Active"

The GH issue creation lives in a separate `main()` so unit tests can target `parse_findings` without touching the network.

### 2.4 Files added/changed

| Path | Action |
|---|---|
| `pipeline-template/src/pipeline_v2/telemetry/otel.py` | NEW — canonical `setup_otel()` with correct ordering + runtime assertion |
| `pipeline-template/tests/security/test_scrubber_ordering.py` | NEW — RED-then-GREEN regression tests |
| `.github/workflows/active-findings-alert.yml` | NEW — daily scheduled alert workflow |
| `scripts/scan_platform_gaps.py` | NEW — markdown parser + GH issue manager |
| `scripts/tests/test_scan_platform_gaps.py` | NEW — unit tests over the parser |
| `scripts/tests/fixtures/active_findings_present.md` | NEW — fixture: extra 🔴 Active row |
| `scripts/tests/fixtures/no_active_findings.md` | NEW — fixture: all 🟢 |
| `platform/platform-gaps.md` | EDIT — only the S7 row (line 37) status flip + closure marker, and the P0 recommendation row (line 69) struck through |

---

## 3. Tests — Phase 7 RED design

### 3.1 Scrubber ordering test (`tests/security/test_scrubber_ordering.py`)

**Approach.** Use OTel's `InMemorySpanExporter` to inspect the *exported* payload — not the in-memory span. This is the only deterministic way to prove "did the scrubber run before the exporter snapshot?". Use `SimpleSpanProcessor` for synchronous flush in tests.

Five tests:

1. `test_setup_otel_registers_scrubber_at_index_zero` — calls `setup_otel("test")`, asserts the first processor is `SecretScrubbingProcessor`.
2. `test_setup_otel_raises_when_order_inverted` — manually swap processors and call the assertion helper; expect `RuntimeError`.
3. `test_authorization_header_is_redacted_in_exported_span` — build a test provider with scrubber + `SimpleSpanProcessor(InMemorySpanExporter)`, emit a span with `http.request.header.authorization = "Bearer sk-live-deadbeef"`, force-flush, inspect the in-memory exporter's `get_finished_spans()`, assert the exported `attributes["http.request.header.authorization"] == "[REDACTED]"`.
4. `test_non_sensitive_attribute_passes_through` — emit a span with a non-sensitive attribute, assert it's unchanged.
5. `test_regression_inverted_order_leaks_secret` — build a provider with the OPPOSITE order (exporter first, scrubber second), emit the same secret, assert the exported attribute is **still raw** — this proves the test methodology actually validates ordering (negative control).

RED state: tests 1, 2, 3 fail because `otel.py` doesn't exist. Test 5 also fails to import.

GREEN state after implementation: all five pass.

### 3.2 Active-findings scanner tests (`scripts/tests/test_scan_platform_gaps.py`)

Three tests:

1. `test_parser_finds_active_finding` — fixture has one row with `🔴` + "Active" → parser returns 1 Finding.
2. `test_parser_ignores_planned_or_closed` — fixture has 🟢 row + 🔴 row without "Active" → parser returns 0.
3. `test_parser_recognises_real_s7_row` — uses a copy of the actual `platform-gaps.md` line 37 → returns 1 Finding with `id == "S7"`.

After the S7 fix lands, the live `platform-gaps.md` has the S7 row marked 🟢, so the **production** workflow finds 0 active findings on first run after merge.

---

## 4. Boundaries — what stays untouched

- No `platform/*.md` doc is edited except the **two specific lines in `platform-gaps.md`** (S7 status row + P0 recommendation row).
- No downstream `*-v2` repo files are edited.
- `pipeline-standard.md` already documents the correct pattern in § 9 — no edit needed; we ship the code that lives up to the doc.
- `CHANGELOG.canon.md` (per STORY-1013) is not created yet — entry deferred to a follow-up commit once STORY-1013 lands.

---

## 5. Verification (post-merge expectations)

| Check | Command | Expected |
|---|---|---|
| Scrubber ordering enforced at import | `python -c "from pipeline_v2.telemetry.otel import setup_otel; setup_otel('test')"` | Exit 0; no `RuntimeError` |
| Regression test green | `pytest pipeline-template/tests/security/test_scrubber_ordering.py -v` | All 5 PASSED |
| Parser test green | `pytest scripts/tests/test_scan_platform_gaps.py -v` | All 3 PASSED |
| S7 row updated | `grep "S7" platform/platform-gaps.md` | Row shows 🟢 + "Closed 2026-05-18 by STORY-1014" |
| Workflow registered | `gh -R hpi-gorillacommerce/gc-data-v2 workflow list \| grep active-findings-alert` | Row present after merge |
| Manual workflow run on fixture | `gh workflow run active-findings-alert.yml -f fixture=scripts/tests/fixtures/active_findings_present.md` | Job succeeds; new issue opened with label `P0-active-finding` |
