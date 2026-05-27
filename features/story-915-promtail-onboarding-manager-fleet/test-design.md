# Test Design — STORY-915: Promtail Onboarding Manager Fleet

## Phase 7 Summary

Infrastructure story — tests are YAML lint + runtime verification scripts. No unit test suite required (no application code changed). Tests validate:
1. Config files are syntactically valid Promtail YAML
2. Deployed hosts are running Promtail with the new config
3. Log streams appear in Loki under the correct project labels
4. Severity parsing works correctly

---

## Test Groups

### Group A — Config Syntax (T01)
**File:** `deployment/promtail/tests/lint-configs.sh`
**Precondition:** `promtail` binary available on CI or test host
**What it does:** Runs `promtail --check-syntax --config.file=<file>` against every new config file.

| ID | Config File | Expected |
|----|-------------|----------|
| A-01 | `promtail-config-dan.yaml` | exit 0, no syntax errors |
| A-02 | `promtail-config-derrick.yaml` | exit 0, no syntax errors |
| A-03 | `promtail-config-daisy.yaml` | exit 0, no syntax errors |
| A-04 | `promtail-config-devon.yaml` | exit 0, no syntax errors |
| A-05 | `promtail-config-ops-console.yaml` | exit 0, no syntax errors |
| A-06 | `promtail-config-morris.yaml` | exit 0, no syntax errors |

**Pass criteria:** All 6 configs lint clean.

---

### Group B — YAML Schema Validation (static, no binary needed)
**File:** `deployment/promtail/tests/lint-configs.sh` (yamllint section)
**What it does:** `yamllint` on each config file to catch indentation/structure issues before deploying.

| ID | Check | Expected |
|----|-------|----------|
| B-01 | Each config has `server`, `positions`, `clients`, `scrape_configs` top-level keys | pass |
| B-02 | Each new `scrape_config` entry has `project` label | pass |
| B-03 | Each new `scrape_config` entry has `agent` label | pass |
| B-04 | Each new `scrape_config` entry has `service` label | pass |
| B-05 | `clients[0].url` is `https://grafana.gorillacommerce.ai/loki/api/v1/push` | pass |

---

### Group C — Additive-Only Guard (static)
**File:** `deployment/promtail/tests/lint-configs.sh` (diff section)
**What it does:** Verifies the existing pre-STORY-915 job blocks in `dan` and `derrick` configs are unmodified (mutation guard from seed SC).

| ID | Check | Expected |
|----|-------|----------|
| C-01 | `hermes-combined` job in dan config unchanged | pass |
| C-02 | `claude-code-files` job in dan config unchanged | pass |
| C-03 | `hermes-logs` job in dan config unchanged | pass |
| C-04 | `dispatch-poller` (file-based) job in dan config unchanged | pass |
| C-05 | Same 4 jobs in derrick config unchanged | pass |

---

### Group D — Runtime Host Verification (post-deploy, T02)
**File:** `deployment/promtail/tests/verify-deploy.sh`
**Precondition:** SSH access (port 443) to each target host
**What it does:** SSH to each host and assert `systemctl is-active promtail` returns `active`.

| ID | Host | Service Check |
|----|------|---------------|
| D-01 | dan VM | `systemctl is-active promtail` → `active` |
| D-02 | derrick VM | `systemctl is-active promtail` → `active` |
| D-03 | daisy VM | `systemctl is-active promtail` → `active` |
| D-04 | devon VM | `systemctl is-active promtail` → `active` |
| D-05 | ops-console host | `systemctl is-active promtail` → `active` |
| D-06 | morris VM | `systemctl is-active promtail` → `active` |

---

### Group E — Synthetic Log Ingestion (T03)
**File:** `deployment/promtail/tests/verify-deploy.sh` (loki query section)
**Precondition:** Post-deploy, Loki endpoint reachable
**What it does:** For each host, runs `logger -t test-onboarding "synthetic test STORY-915 <timestamp>"` via SSH, then polls Loki for 60s waiting for the line to appear under the correct project label.

| ID | Host | LogQL Query | Max Wait |
|----|------|-------------|----------|
| E-01 | dan | `{project="hermes", agent="dan"} \|= "STORY-915"` | 60s |
| E-02 | derrick | `{project="hermes", agent="derrick"} \|= "STORY-915"` | 60s |
| E-03 | daisy | `{project="hermes", agent="daisy"} \|= "STORY-915"` | 60s |
| E-04 | devon | `{project="hermes", agent="devon"} \|= "STORY-915"` | 60s |
| E-05 | ops-console | `{project="ops_console"} \|= "STORY-915"` | 60s |
| E-06 | morris | `{project="morris"} \|= "STORY-915"` | 60s |

---

### Group F — Label Enumeration (T04)
**File:** `deployment/promtail/tests/verify-deploy.sh`
**What it does:** `curl https://grafana.gorillacommerce.ai/loki/api/v1/label/project/values` and asserts response contains all three new project labels.

| ID | Expected value in response |
|----|---------------------------|
| F-01 | `hermes` |
| F-02 | `ops_console` |
| F-03 | `morris` |

---

### Group G — Service Startup Banner (T05)
**File:** `deployment/promtail/tests/verify-deploy.sh`
**What it does:** Query Loki for known startup strings emitted by each service on boot.

| ID | Project | LogQL | Expected |
|----|---------|-------|----------|
| G-01 | ops_console | `{project="ops_console"} \|~ "(?i)(started\|running\|uvicorn\|startup\|listening)"` | ≥1 result in last 24h |
| G-02 | morris | `{project="morris"} \|~ "(?i)(started\|running\|morris\|listening)"` | ≥1 result in last 24h |
| G-03 | hermes | `{project="hermes"} \|~ "(?i)(dispatch.poller\|started\|polling)"` | ≥1 result in last 24h |

---

### Group H — Severity Parsing (T07)
**File:** `deployment/promtail/tests/verify-deploy.sh`
**What it does:** Query Loki filtering by `severity` label to confirm pipeline extraction works.

| ID | Project | LogQL | Expected |
|----|---------|-------|----------|
| H-01 | hermes | `{project="hermes", severity="error"}` | Returns only lines containing ERROR/CRITICAL (not INFO/WARNING) |
| H-02 | ops_console | `{project="ops_console", severity="info"}` | Returns lines containing INFO |
| H-03 | morris | `{project="morris", severity="warning"}` | Returns lines containing WARNING (if any exist in last 24h) |

---

### Group I — Claim-Pattern Query (T06)
**File:** `deployment/promtail/tests/verify-deploy.sh`
**What it does:** If Dan claimed a job in the last hour, verify it appears in Loki.

| ID | LogQL | Expected |
|----|-------|----------|
| I-01 | `{project="hermes", agent="dan"} \|~ "(?i)claim"` | ≥1 result if Dan was active last hour (SKIP if no recent activity) |

---

## Test Execution Plan

### Pre-deploy (CI / local)
```bash
cd deployment/promtail/tests
./lint-configs.sh          # Groups A, B, C — pure static, no SSH needed
```

### Post-deploy (per rollout phase)
```bash
# Phase 1: ops_console
./verify-deploy.sh ops-console

# Phase 2: Morris
./verify-deploy.sh morris

# Phase 3: Hermes VMs
./verify-deploy.sh hermes
```

### Full suite
```bash
./verify-deploy.sh all
```

---

## Test State at End of Phase 7

- `lint-configs.sh`: **GREEN** — runs against new config files in repo (syntax-checked via `yamllint`; `promtail --check-syntax` requires binary on host)
- `verify-deploy.sh`: **scaffold written**, **RED** (requires deployed hosts + live Loki streams)
- Config files: written and present in `deployment/promtail/`
- Phase 8 turns verify-deploy RED → GREEN by deploying configs to hosts

---

## Acceptance Gate

Phase 8 is complete when:
1. `lint-configs.sh` exits 0
2. `verify-deploy.sh all` shows F-01, F-02, F-03 GREEN (label enumeration)
3. At least one E-0x test GREEN per host (synthetic ingestion)
4. At least one G-0x test GREEN per project (startup banner)
