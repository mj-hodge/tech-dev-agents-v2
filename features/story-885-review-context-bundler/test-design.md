# STORY-885 — Test Design (Phase 7)

## Test Strategy

Tests exercise `generate-bundle.py` functions with mocked filesystem paths (no real wiki/KB access needed).
All functions are importable via `importlib` from `.sdlc/skills/review-context-bundler/generate-bundle.py`.
Uses `tmp_path` and `monkeypatch` fixtures to isolate file I/O from production state.

## Test Cases

### Group A — Bundle Generation Smoke

#### A-01: `test_build_bundle_runs_without_error`
**What:** Call `build_bundle()` with minimal wiki fixtures and verify it returns a non-empty string without raising.
**Why:** SC-2 — output file must be valid markdown.
**How:** Create a `tmp_path/wiki/systems/` dir with one `.md` file. Monkeypatch `WIKI_ROOT`, `AGENTS_MD`, `REVIEW_PRS_SKILL`, `STATE_DIR_WORKSPACE`, `STATE_DIR_LOCAL`, `REPO_BASE` to tmp paths. Call `build_bundle()`.
**Key assertion:** `assert isinstance(result, str) and len(result) > 0`

### Group B — Header Line Format

#### B-01: `test_header_line_format`
**What:** Verify the first line matches `<!-- review-context bundle: built=ISO8601 / wiki_pages=N / runbooks=N / incidents=N / gaps=N -->`.
**Why:** SC-2 — header line format is the staleness-detection contract for review-prs Step 0.
**How:** Call `build_bundle()` with fixtures, split result by `\n`, check first line with regex.
**Key assertion:** `assert re.match(r'^<!-- review-context bundle: built=\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z / wiki_pages=\d+ / runbooks=\d+ / incidents=\d+ / gaps=\d+ -->$', lines[0])`

### Group C — Budget Enforcement

#### C-01: `test_output_under_120k_chars`
**What:** Verify output length is ≤ 120,000 characters.
**Why:** SC-3 — hard budget ceiling enforced in code.
**How:** Call `build_bundle()`, measure `len(result)`.
**Key assertion:** `assert len(result) <= 120_000`

### Group D — Idempotency

#### D-01: `test_idempotent_across_two_runs`
**What:** Two consecutive calls to `build_bundle()` with identical inputs produce identical output (ignoring timestamp).
**Why:** Security Constraint — idempotent, deterministic ordering.
**How:** Call `build_bundle()` twice. Strip the `built=...Z` timestamp from both results. Compare.
**Key assertion:** `assert strip_timestamp(run1) == strip_timestamp(run2)`
