# Test Design — STORY-324: Fleet Review Script

## Strategy
All 7 checks shell out to external tools (SSH, az CLI, gh CLI). Tests mock
`subprocess.run` to return realistic stdout/stderr and exercise the parsing,
classification, and report-formatting logic without network access.

## Test Categories

### 1. Report structure
- Full report contains all 7 check sections with status (OK/WARN/CRIT)
- Scorecard table is valid markdown
- Executive summary is present

### 2. Per-check classification
- **Guard alignment:** MD5 match → OK, mismatch → CRIT
- **SDLC compliance:** 100% seed.md → OK, missing seed.md → CRIT, <80% test-design → WARN
- **Model economics:** cost < $200 → OK, > $200 → CRIT
- **Agent health:** all services active → OK, unreachable → CRIT, disk >90% → WARN
- **Tool drift:** all match → OK, critical file stale → CRIT
- **Open PRs:** listed → OK (informational)
- **KB freshness:** commits this week → OK, zero commits → WARN

### 3. Error resilience
- SSH timeout on one agent doesn't crash the script
- `az rest` failure marks check as SKIP, doesn't block others
- `gh` CLI failure marks check as SKIP

### 4. CLI interface
- `--check guard` runs only guard alignment
- No args runs all 7 checks
- `--help` works

## Test file
`tests/test_fleet_review.py`
