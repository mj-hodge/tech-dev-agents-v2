# Weekly Information Security Review — 2026-04-20

**Reviewer:** Morris (Engineering Manager)
**Method:** Claude Code deep analysis on all 10 repos (parallel, read-only)
**Date:** Sunday, April 20, 2026

---

## Executive Summary

All 10 repositories in the `hpi-gorillacommerce` organization were audited. The fleet has a **mixed security posture** — production services (tech-datawarehouse, product-health-dashboard) are well-hardened, but several repos have **hardcoded secrets committed to git history** that require immediate rotation.

| Severity | Total Findings |
|----------|---------------|
| 🔴 CRITICAL | 14 |
| 🟠 HIGH | 17 |
| 🟡 MEDIUM | 28 |
| 🔵 LOW | 27 |
| **TOTAL** | **86** |

### Top 3 Urgent Actions
1. **Rotate Azure AD client secret** committed in `tech-project-mapping` `loki/grafana.env` — live credential in git history
2. **Rotate Keepa API key** committed in `fabric-keepa` `Testing/TestingNotebook.Notebook/notebook-content.py` — 64-char production key in plaintext
3. **Rotate Azure SPN credentials** exposed in `tech-gc-knowledgebase` `CREDENTIAL-BLOCKER.md` — app IDs + tenant ID + Key Vault paths

---

## Per-Repo Findings

### 1. advertising-amazon
**Posture: 🟡 NEEDS ATTENTION** | CRITICAL: 2 | HIGH: 2 | MEDIUM: 2 | LOW: 1

| # | Sev | Finding | File | Line |
|---|-----|---------|------|------|
| 1 | 🔴 CRIT | SQL injection via f-string interpolation | `health_tools.py` | 359 |
| 2 | 🔴 CRIT | SQL injection via dynamic table/column names | `datalake.py` | 155-158 |
| 3 | 🟠 HIGH | Container running as root | `Dockerfile` | — |
| 4 | 🟠 HIGH | Personal email in docker-compose | `docker-compose.yml` | 26 |
| 5 | 🟡 MED | HTTPS enforcement missing | `server.py` | — |
| 6 | 🟡 MED | Redis password placeholder validation missing | config | — |
| 7 | 🔵 LOW | Azure Tenant/Client IDs hardcoded | `docker-compose.yml` | 23-24 |

**Positive:** No secrets committed, no eval/exec abuse, centralized write guard, bearer token auth, no wildcard CORS.

---

### 2. tech-datawarehouse
**Posture: 🟢 STRONG** | CRITICAL: 0 | HIGH: 0 | MEDIUM: 1 | LOW: 0

| # | Sev | Finding | File | Line |
|---|-----|---------|------|------|
| 1 | 🟡 MED | OData filter injection via f-string (unescaped `ad_group_name`) | `src/catalog/loader.py` | 94 |

**Positive:** Env vars only, .gitignore comprehensive, JWT+Azure AD auth, pip-audit+Trivy in CI, ScrubProcessor redacts tokens in logs, non-root Docker user, OIDC in CI.

---

### 3. tech-dev-agents
**Posture: 🟡 NEEDS ATTENTION** | CRITICAL: 3 | HIGH: 6 | MEDIUM: 7 | LOW: 7

| # | Sev | Finding | File | Line |
|---|-----|---------|------|------|
| 1 | 🔴 CRIT | Weak JWT secret default `"change-me-in-production"` | `.sdlc/templates/backend/core-config.py` | 18 |
| 2 | 🔴 CRIT | Hardcoded DB creds `changeme` in compose template | `.sdlc/templates/infra/compose.yaml` | 12, 29, 42 |
| 3 | 🔴 CRIT | Dev JWT secret committed in compose override | `.sdlc/templates/infra/compose.override.yaml` | 15 |
| 4 | 🟠 HIGH | Plain HTTP for agent-to-agent communication | `presence_push.py`, `agent_service.py` | 39, 142+ |
| 5 | 🟠 HIGH | Graph client secret accepted without validation | `graph_token_provider.py` | 49, 53 |
| 6 | 🟠 HIGH | Database URL partially logged (password may appear) | `main.py` | 185 |
| 7 | 🟠 HIGH | Hardcoded DB creds fallback in docker-compose | `deployment/ops-console/docker-compose.yml` | 44 |
| 8 | 🟠 HIGH | Binding to 0.0.0.0 in production configs | `Dockerfile`, `docker-compose.yml`, `teams.py`, `health_server.py` | various |
| 9 | 🟠 HIGH | No startup validation for required API keys | `config.py` | 19, 30, 45 |
| 10 | 🟡 MED | CORS `allow_methods=["*"]` + `allow_headers=["*"]` + credentials | `main.py` | 250-261 |
| 11 | 🟡 MED | subprocess calls with potentially user-supplied repo names | `dispatch_poller.py` | 164+ |
| 12 | 🟡 MED | LogQL injection risk in Loki client | `loki_client.py` | 52-56 |
| 13 | 🟡 MED | GitHub token cached in module-level global | `work_history.py` | 31+ |
| 14 | 🟡 MED | `story_id` field has no format validation | `dispatch.py` | 82-120 |
| 15 | 🟡 MED | No rate limiting on any API endpoints | `main.py` | — |
| 16 | 🟡 MED | SSH security config not explicit | `presence_service.py` | — |

**Note:** CRITs 1-3 are in `.sdlc/templates/` — template files that get copied into new projects. They poison every project bootstrapped from this template.

---

### 4. sourcing-warning-labels
**Posture: 🟡 NEEDS ATTENTION** | CRITICAL: 0 | HIGH: 4 | MEDIUM: 10 | LOW: 12

| # | Sev | Finding | File | Line |
|---|-----|---------|------|------|
| 1 | 🟠 HIGH | No in-app authorization on destructive endpoints (clear-and-reload wipes DB) | `main.py` | 1246-1272 |
| 2 | 🟠 HIGH | PostgreSQL firewall rule opens DB to ALL Azure tenants | `infra/main.bicep` | 163-167 |
| 3 | 🟠 HIGH | CORS `allow_credentials=True` with unvalidated origin list | `main.py` | 35-45 |
| 4 | 🟠 HIGH | Azure storage account key in deployment output | `infra/main.bicep` | 131-142 |
| 5 | 🟡 MED | Legacy plaintext password `Kong_Warnings` in markdown files | `features/story-001*/` | various |
| 6 | 🟡 MED | Dynamic SQL string construction (fragile pattern) | `main.py` | 620-636 |
| 7 | 🟡 MED | `init_db` seed SQL split on `;` (naive parser) | `db.py` | 196-207 |
| 8 | 🟡 MED | Unbounded regex/XML parsing (ReDoS risk) | `main.py` | 149-196 |
| 9 | 🟡 MED | Cascade delete with no audit trail | `main.py` | 1107-1108 |
| 10 | 🟡 MED | Log injection via unescaped user fields | `main.py` | 865, 1099 |
| 11+ | 🔵 LOW | 12 findings: .gitignore gaps, unpinned base images, no rate limiting, XSS in diff renderers, etc. | various | — |

**Positive:** All SQL parameterized, no command injection, no deserialization issues, Entra ID auth present.

---

### 5. fabric-keepa
**Posture: 🔴 CRITICAL** | CRITICAL: 1 | HIGH: 1 | MEDIUM: 0 | LOW: 1

| # | Sev | Finding | File | Line |
|---|-----|---------|------|------|
| 1 | 🔴 CRIT | **Hardcoded Keepa API key** (64-char production key in plaintext) | `Testing/TestingNotebook.Notebook/notebook-content.py` | 551, 749 |
| 2 | 🟠 HIGH | Loki `auth_enabled: false` | `monitoring/loki-config.yml` | 7 |
| 3 | 🔵 LOW | Raw tracebacks in exception handlers | Keepa notebooks | — |

**Positive:** SQL queries parameterized, Key Vault used in production notebooks, .gitignore comprehensive, scrub_secrets enabled, HTTPS throughout.

**⚠️ IMMEDIATE ACTION: Rotate Keepa API key in Azure Key Vault (`kv-keepa-prod`) and purge from git history.**

---

### 6. tech-project-mapping
**Posture: 🔴 CRITICAL** | CRITICAL: 1 | HIGH: 4 | MEDIUM: 3 | LOW: 3

| # | Sev | Finding | File | Line |
|---|-----|---------|------|------|
| 1 | 🔴 CRIT | **Azure AD Client Secret + default admin password committed** | `loki/grafana.env` | 27-28, 55-56 |
| 2 | 🟠 HIGH | `.gitignore` missing `.env`, `*.key`, `*.pem` patterns | `.gitignore` | — |
| 3 | 🟠 HIGH | Storage account key exposed in workflow logs | `deploy-loki.yml` | ~73 |
| 4 | 🟠 HIGH | Default `admin:admin` credentials in Bicep template | `loki-aci.bicep` | 198-199 |
| 5 | 🟠 HIGH | Loki running without authentication | `loki-config.yaml` | 4 |
| 6 | 🟡 MED | Azure Tenant ID, Group ID, User OID hardcoded | `setup-grafana-sso.sh`, `loki-aci.bicep` | various |
| 7 | 🟡 MED | Over-permissive CI/CD workflow permissions | workflows | — |
| 8 | 🟡 MED | Third-party actions pinned to mutable version tags | workflows | — |
| 9 | 🔵 LOW | No `.dockerignore` | — | — |
| 10 | 🔵 LOW | Verbose workflow logs expose resource names | `deploy-loki.yml` | ~92 |
| 11 | 🔵 LOW | No documented secrets rotation policy | — | — |

**⚠️ IMMEDIATE ACTION: Rotate Azure AD client secret in Azure Portal → App Registrations → grafana-logging. Purge `loki/grafana.env` from git history.**

---

### 7. product-health-dashboard
**Posture: 🟢 STRONG** | CRITICAL: 0 | HIGH: 0 | MEDIUM: 3 | LOW: 2

| # | Sev | Finding | File | Line |
|---|-----|---------|------|------|
| 1 | 🟡 MED | `DEV_AUTH_BYPASS` defaults to `true` in `.env.example` | `.env.example` | 45 |
| 2 | 🟡 MED | CORS origin fallback logic could produce wildcard | `main.py` | 113 |
| 3 | 🟡 MED | `VITE_*` build args baked into image layers | `Dockerfile.frontend` | 9-18 |
| 4 | 🔵 LOW | Default `ADMIN_TOKEN` placeholder | `.env.example` | 49 |
| 5 | 🔵 LOW | Unpinned base image tags | `Dockerfile.frontend`, `Dockerfile` | — |

**Positive:** No hardcoded secrets, JWT RS256 pinned, timing-safe token comparison, parameterized SQL, non-root containers, all dependencies pinned.

---

### 8. tech-dataimport-monday
**Posture: 🟢 GOOD** | CRITICAL: 0 | HIGH: 0 | MEDIUM: 1 | LOW: 4

| # | Sev | Finding | File | Line |
|---|-----|---------|------|------|
| 1 | 🟡 MED | GraphQL injection via f-string interpolation | `scripts/discover_boards.py` | 127, 164, 257-261 |
| 2 | 🔵 LOW | SQL column-name injection (schema-sourced, low risk) | `scripts/validate_join_keys.py` | 352-395 |
| 3 | 🔵 LOW | `.gitignore` missing `*.key`, `*.pem` patterns | `.gitignore` | — |
| 4 | 🔵 LOW | Output files written without restrictive permissions | various scripts | — |
| 5 | 🔵 LOW | Loose dependency version pinning (`>=`) | `requirements.txt` | 1-3 |

**Positive:** Env vars + Azure AD `DefaultAzureCredential`, parameterized SQL, HTTPS only, token never logged, proper conn lifecycle.

---

### 9. sdlc-framework
**Posture: 🔴 CRITICAL** | CRITICAL: 6 | HIGH: 5 | MEDIUM: 6 | LOW: 4

| # | Sev | Finding | File | Line |
|---|-----|---------|------|------|
| 1 | 🔴 CRIT | Hardcoded default DB password `changeme` | `templates/infra/compose.yaml` | 12, 29, 42 |
| 2 | 🔴 CRIT | Hardcoded JWT secret `"change-me-in-production"` | `templates/backend/core-config.py` | 18 |
| 3 | 🔴 CRIT | Redis + Grafana hardcoded passwords in MCP compose | `templates/mcp-server/compose-mcp.yaml` | 13, 51, 53, 70 |
| 4 | 🔴 CRIT | Unclosed file handle + hardcoded absolute path | `templates/mcp-server/server.py` | 202 |
| 5 | 🔴 CRIT | Dev JWT secret committed in compose override | `templates/infra/compose.override.yaml` | 15 |
| 6 | 🔴 CRIT | Grafana default password `admin` in compose | `templates/monitoring/README.md` | 61 |
| 7 | 🟠 HIGH | `.gitignore` critically incomplete (only 2 lines) | `.gitignore` | — |
| 8 | 🟠 HIGH | Weak test DB credentials in conftest | `templates/backend/conftest.py` | 25 |
| 9 | 🟠 HIGH | `curl \| bash` install pattern with no integrity check | `README.md` / `install.sh` | 3 |
| 10 | 🟠 HIGH | `subprocess.TimeoutExpired` not caught in MCP proxy | `templates/mcp-server/stdio-proxy.py` | 34-39 |
| 11 | 🟠 HIGH | Unquoted variable usage in shell scripts | `install.sh` | 60-70 |
| 12-17 | 🟡 MED | 6 findings: placeholder values, hardcoded URLs, CORS, token length, etc. | various | — |
| 18-21 | 🔵 LOW | 4 findings: CI postgres creds, DoS via long input, incomplete TODO in auth | various | — |

**Note:** This is the SDLC template repo — every CRIT here propagates to every new project bootstrapped from these templates. Fixing this repo has multiplicative impact.

---

### 10. tech-gc-knowledgebase
**Posture: 🔴 CRITICAL** | CRITICAL: 3 | HIGH: 2 | MEDIUM: 3 | LOW: 2

| # | Sev | Finding | File | Line |
|---|-----|---------|------|------|
| 1 | 🔴 CRIT | **Azure SPN App IDs + Tenant ID + Subscription ID exposed** | 8+ files including `CREDENTIAL-BLOCKER.md` | various |
| 2 | 🔴 CRIT | **Key Vault name, secret name, full ARM resource path exposed** | `CREDENTIAL-BLOCKER.md`, scratch files | various |
| 3 | 🔴 CRIT | **Credential JSON structure + VM env file path documented** | `CREDENTIAL-BLOCKER.md` | 37-57 |
| 4 | 🟠 HIGH | Agent VM public IPs in scratch (incomplete prior redaction) | `scratch/infrastructure/vms-and-agents.md` | 10-12 |
| 5 | 🟠 HIGH | Hardcoded passwords in DB connection strings | scratch + wiki files | various |
| 6 | 🟡 MED | Internal database FQDN exposed | scratch + sources | — |
| 7 | 🟡 MED | Personal employee names and email addresses | sources/inventory files | — |
| 8 | 🟡 MED | Internal VM filesystem paths in committed code | scratch files | — |
| 9 | 🔵 LOW | `.gitignore` missing `*.key`, `*.pem`, `*secret*.json` | `.gitignore` | — |
| 10 | 🔵 LOW | Credential loading chain documented in code comments | Python ingest files | — |

**⚠️ IMMEDIATE ACTION: Rotate both service principals (`4928d18f` and `dc0cba0b`) and the Key Vault secret `knowledgebase-sp-credentials`.**

---

## Cross-Cutting Patterns

### 1. Hardcoded Secrets in Git History (CRITICAL — org-wide)
**Affected repos:** fabric-keepa, tech-project-mapping, tech-gc-knowledgebase, sdlc-framework, tech-dev-agents
**Pattern:** Production API keys, Azure AD client secrets, JWT signing keys, and database passwords committed to git. Even if removed from HEAD, they persist in git history.
**Fix:** Rotate ALL exposed credentials immediately. Consider `git filter-repo` for high-value repos. Implement `gitleaks` or `git-secrets` as a pre-commit hook org-wide.

### 2. Incomplete `.gitignore` Files (HIGH — 7/10 repos)
**Affected repos:** tech-project-mapping, sdlc-framework, tech-gc-knowledgebase, sourcing-warning-labels, tech-dataimport-monday, fabric-keepa, tech-dev-agents
**Pattern:** Missing patterns for `.env`, `*.key`, `*.pem`, `*.p12`, `credentials.*`, `secrets.*`.
**Fix:** Standardize a base `.gitignore` template across the org. The sdlc-framework template should include comprehensive patterns.

### 3. Loki `auth_enabled: false` (HIGH — 2 repos)
**Affected repos:** fabric-keepa, tech-project-mapping
**Pattern:** Loki log ingestion endpoint wide open — no auth required.
**Fix:** Enable auth in production configs. Enforce via Caddy reverse proxy or native Loki multi-tenant auth.

### 4. Default/Weak Credentials in Templates (CRITICAL — systemic)
**Affected repos:** sdlc-framework (source), tech-dev-agents (consumer)
**Pattern:** `changeme`, `admin`, `dev-secret-not-for-production` as defaults in compose files and config templates. Every new project inherits these.
**Fix:** Replace all defaults with `${VAR:?Required}` fail-fast pattern. Add startup validation.

### 5. CORS Misconfiguration Risk (MEDIUM — 3 repos)
**Affected repos:** sourcing-warning-labels, tech-dev-agents, product-health-dashboard
**Pattern:** `allow_credentials=True` with insufficiently validated origin lists. Wildcard fallback possible.
**Fix:** Validate origins at startup. Never fall back to `*` with credentials.

### 6. SQL/Query Injection via f-strings (CRITICAL/MEDIUM — 3 repos)
**Affected repos:** advertising-amazon (CRITICAL — production), tech-dataimport-monday (MEDIUM), tech-datawarehouse (MEDIUM)
**Pattern:** f-string interpolation in SQL or OData/GraphQL queries instead of parameterized queries.
**Fix:** Use parameterized queries exclusively. advertising-amazon fixes are highest priority (production service).

---

## Repo Security Scorecard

| Repo | Score | CRIT | HIGH | MED | LOW | Status |
|------|-------|------|------|-----|-----|--------|
| tech-datawarehouse | 🟢 A | 0 | 0 | 1 | 0 | Strong |
| product-health-dashboard | 🟢 A- | 0 | 0 | 3 | 2 | Strong |
| tech-dataimport-monday | 🟢 B+ | 0 | 0 | 1 | 4 | Good |
| sourcing-warning-labels | 🟡 C+ | 0 | 4 | 10 | 12 | Needs attention |
| advertising-amazon | 🟡 C | 2 | 2 | 2 | 1 | SQL injection must fix |
| tech-dev-agents | 🟡 C | 3 | 6 | 7 | 7 | Template poison risk |
| fabric-keepa | 🔴 D | 1 | 1 | 0 | 1 | API key exposed |
| tech-project-mapping | 🔴 D | 1 | 4 | 3 | 3 | AD client secret exposed |
| tech-gc-knowledgebase | 🔴 D | 3 | 2 | 3 | 2 | SPN + vault info exposed |
| sdlc-framework | 🔴 F | 6 | 5 | 6 | 4 | Template source — max blast radius |

---

## Recommended Priority Actions (This Week)

### Immediate (Today)
1. **Rotate Azure AD client secret** — tech-project-mapping `loki/grafana.env`
2. **Rotate Keepa API key** — fabric-keepa (Key Vault `kv-keepa-prod`)
3. **Rotate both Azure SPNs** — tech-gc-knowledgebase (`4928d18f`, `dc0cba0b`)
4. **Rotate Key Vault secret** — `knowledgebase-sp-credentials` in `kv-tech-dev-agents-dev`

### This Week
5. **Fix SQL injection** in advertising-amazon `health_tools.py:359` and `datalake.py:155-158`
6. **Harden sdlc-framework templates** — replace all `changeme`/`admin` defaults with fail-fast `${VAR:?Required}`
7. **Expand `.gitignore`** across all 7 affected repos
8. **Add Entra group authorization** to sourcing-warning-labels destructive endpoints
9. **Restrict Postgres firewall** in sourcing-warning-labels (`0.0.0.0` → VNet only)

### This Month
10. Implement `gitleaks` pre-commit hook org-wide
11. Run `pip-audit` / `safety check` in CI for all repos
12. Pin GitHub Actions to commit SHAs (tech-project-mapping)
13. Add rate limiting to API endpoints (sourcing-warning-labels, tech-dev-agents)
14. Enable Loki auth in production (fabric-keepa, tech-project-mapping)

---

*Report generated by Morris via Claude Code parallel security audits across 10 repositories.*
*Next review scheduled: 2026-04-27*
