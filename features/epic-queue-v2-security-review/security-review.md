# Security Review — Epic-Queue-v2 Q4-Q9
**Date:** 2026-05-02
**Reviewer:** Security subagent
**Branch:** feat/unified-queue-reliability
**Scope:** New API endpoints and service files from the Q4-Q9 implementation wave

---

## CRITICAL

### CRIT-1 — All knowledge and apprenticeship routes are completely unauthenticated
**File:** `tech_dev_agents/ops_console/routes/knowledge.py` (entire file), `tech_dev_agents/ops_console/routes/apprenticeship.py` (entire file)
**Lines:** knowledge.py:27–28, 70–148; apprenticeship.py:27–28, 47–119

**Description:** Neither router declares a `dependencies=[Depends(require_auth)]` at the router level, and no individual endpoint uses `Depends(require_auth)` or `Depends(require_role(...))`. Compare with `dispatch.py:68` which correctly sets `router = APIRouter(dependencies=[Depends(require_auth)])` at construction time. The knowledge and apprenticeship routers have no equivalent guard.

`main.py:321` mounts knowledge at `/api/knowledge` and the apprenticeship module comment claims it is "mounted at /api/apprenticeship/ by main.py" — but **the apprenticeship router is not registered in `main.py` at all** (zero occurrences of "apprenticeship" in main.py). This means the routes currently do nothing, but that is likely an oversight that will be fixed — and when it is, the routes will become live and unauthenticated.

**Attack scenario:**
- Any internet-accessible request to `POST /api/knowledge/promote` can promote arbitrary ingest entries to the knowledge repo without credentials.
- `POST /api/knowledge/backfill` triggers a full filesystem scan with no auth.
- `GET /api/apprenticeship/decisions` exposes the full dispatch_decisions table to anonymous callers.
- `POST /api/apprenticeship/rules/{rule_id}/approve` allows anyone to approve automation rules.

**Remediation:**
1. Add `dependencies=[Depends(require_auth)]` to both `APIRouter()` constructors.
2. Register the apprenticeship router in `main.py` with the appropriate prefix (e.g., `app.include_router(apprenticeship_routes.router, prefix="/api/apprenticeship")`).
3. Elevate destructive endpoints (`/promote`, `/backfill`, `/rules/{rule_id}/approve`) to `require_role(Role.MANAGER)`.

---

### CRIT-2 — Path traversal in `_write_to_kb_repo`: attacker can write arbitrary files
**File:** `tech_dev_agents/ops_console/services/knowledge_service.py`
**Lines:** 582–586

**Description:** `_write_to_kb_repo` constructs the target path as:
```python
target = self._repo_root / "tech-gc-knowledgebase" / path
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(body)
```
`path` comes from the `proposed_path` column of `knowledge_ingest_queue`, which is written either by `ingest_from_completed_story` (using f-strings built from scanned file text) or by a direct DB insert. There is no check that the resolved path stays within `tech-gc-knowledgebase/`. Python's `Path /` operator does not resolve traversal segments — `Path('/opt/agent/tech-gc-knowledgebase') / '../../etc/cron.d/evil'` resolves to `/opt/etc/cron.d/evil`.

**Verified:** Running `Path('/opt/agent') / 'tech-gc-knowledgebase' / '../../etc/cron.d/evil'` gives `/opt/etc/cron.d/evil`, which does not start with the expected base.

**Attack scenario:** An attacker who can insert a row into `knowledge_ingest_queue` (e.g., through the unauthenticated `POST /promote` — see CRIT-1) with `proposed_path = "../../home/hermes/.ssh/authorized_keys"` and `proposed_body = "<their pubkey>"` can write arbitrary SSH keys or cron entries to the server filesystem.

**Remediation:** After computing `target`, assert it is within the expected base:
```python
base = (self._repo_root / "tech-gc-knowledgebase").resolve()
resolved = target.resolve()
if not str(resolved).startswith(str(base) + "/"):
    raise ValueError(f"Path traversal detected: {path!r} escapes KB root")
```
Apply the same guard in `_update_index_md` for `index_path`.

---

### CRIT-3 — `approve_rule` hardcodes `approved_by="mark"` — any caller impersonates Mark
**File:** `tech_dev_agents/ops_console/routes/apprenticeship.py`
**Lines:** 102–107

**Description:**
```python
@router.post("/rules/{rule_id}/approve")
async def approve_rule(request: Request, rule_id: int) -> dict[str, Any]:
    svc = get_apprenticeship_service(request)
    await svc.approve_rule(rule_id=rule_id, approved_by="mark")
```
The `approved_by` field is hardcoded to `"mark"` regardless of who actually called the endpoint. Once authentication is added (CRIT-1), any user with credentials — including AGENT-role keys — can approve automation rules and the audit trail will falsely attribute every approval to "mark".

**Attack scenario:** An agent-scoped API key (or compromised account) calls `POST /rules/5/approve`. The rule fires for the next 90 days. The audit log shows `approved_by = mark`, masking the true origin. The approved rule could have a high-blast-radius `decision_kind` if the deny-list in `ApprenticeshipService.HIGH_BLAST_RADIUS_KINDS` was bypassed at proposal time.

**Remediation:**
1. Extract the authenticated identity from `request.state` or JWT claims and pass it as `approved_by`.
2. Require `Role.MANAGER` for this endpoint (an agent should not approve rules).

---

## HIGH

### HIGH-1 — `POST /promote`: `approver` field is client-supplied, enables audit poisoning
**File:** `tech_dev_agents/ops_console/routes/knowledge.py`
**Lines:** 53–55, 120–128; `services/knowledge_service.py:513`

**Description:** `PromoteRequest.approver` defaults to `"auto"` but is a free-form string supplied by the client. The route passes it directly to `KnowledgeService.promote()` which writes it to `decided_by` in `knowledge_ingest_queue` and embeds it in the git commit message: `f"promote: {proposed_path} (approved_by={approver})"`. An unauthenticated caller (CRIT-1) can pass `approver="mark"` or any arbitrary string.

**Remediation:** Resolve `approver` from the authenticated caller's identity server-side. Do not accept it from the request body.

---

### HIGH-2 — `GET /audit` with negative or extreme `window_days` — SQL interval inversion / resource exhaustion
**File:** `tech_dev_agents/ops_console/routes/knowledge.py:139–147`, `services/knowledge_service.py:404–408`

**Description:** `window_days` is typed `int` (FastAPI enforces this, blocking injection strings), but no minimum or maximum is enforced. The SQL query uses:
```sql
($1 || ' days')::interval
```
where `$1 = str(window_days)`. Passing `window_days=-1` produces `'-1 days'::interval`, inverting the threshold to a future time — every promoted page appears stale. Passing `window_days=999999` performs a 2739-year lookback, performing a full table scan. The trigram-based `GET /search` with no query length limit (see MED-1) compounds this.

**Remediation:** Enforce bounds: `window_days: int = Query(90, ge=1, le=365)` in the route signature.

---

### HIGH-3 — `GET /decisions` exposes full `dispatch_decisions` table with `SELECT *` — no column filtering or auth
**File:** `tech_dev_agents/ops_console/routes/apprenticeship.py:47–51`; `services/apprenticeship.py:252–264`

**Description:** `list_decisions` returns `SELECT * FROM dispatch_decisions` with no column allow-list. The `dispatch_decisions` table contains `inputs_json` (which may include agent prompts, secrets, or internal routing data) and `inputs_hash`. Without authentication (CRIT-1) this is fully public.

**Remediation:** Project only the columns safe for external consumers and add auth (CRIT-1). Remove `inputs_json` from the public response, or at minimum require MANAGER role.

---

### HIGH-4 — `GET /proposals` and `GET /rules` also perform `SELECT *` with no auth
**File:** `tech_dev_agents/ops_console/routes/apprenticeship.py:59–94`

**Description:** Both endpoints execute `SELECT * FROM dispatch_rules` with no column filtering. `dispatch_rules` contains `trigger_pattern` (a JSONB blob with `inputs_hash` that an attacker could replay) and internal routing fields. No auth guards (CRIT-1 applies).

**Remediation:** Require auth, scope to MANAGER role for proposals and rules list, project specific columns.

---

### HIGH-5 — `_update_index_md`: `index_path` parameter is not validated for path traversal
**File:** `tech_dev_agents/ops_console/services/knowledge_service.py:618–645`

**Description:** `_update_index_md(index_path, path, title)` accepts `index_path` as a `Path` argument and writes to it without checking that it stays within the expected repo tree. In `promote()` (line 542) the caller constructs the path as `self._repo_root / "tech-gc-knowledgebase" / "index.md"`, which is safe — but the method is `async` and public. Any future code path that supplies a different `index_path` could write to an arbitrary location.

**Remediation:** Harden the method to validate `index_path` is within `self._repo_root / "tech-gc-knowledgebase"` before reading or writing.

---

## MEDIUM

### MED-1 — `GET /search`: no length bound on `q` parameter enables DoS via trigram scan
**File:** `tech_dev_agents/ops_console/routes/knowledge.py:70–105`

**Description:** The `q` query parameter has no `max_length` constraint. The service sends it directly to `similarity(question_text, $1)` in a PostgreSQL trigram query, which is an O(n) operation proportional to both the table size and the trigram count in the query string. A very long `q` (e.g., 1 MB) can cause CPU-intensive full-table scans. Without rate limiting (see MED-3) and without authentication (CRIT-1), this is a trivially automatable DoS vector.

**Remediation:** Add `q: str = Query(..., max_length=500)` and the general auth guard from CRIT-1.

---

### MED-2 — `POST /cite`: `job_id` (UUID) and `knowledge_path` have no format or length validation
**File:** `tech_dev_agents/ops_console/routes/knowledge.py:46–49`, `services/knowledge_service.py:352–374`

**Description:** `CiteRequest.job_id` is typed `str`, not `uuid.UUID`. The service passes it to the DB as `$1::uuid`, which will raise a PostgreSQL error if the string is not a valid UUID. This error propagates as an unhandled 500 rather than a 422. `knowledge_path` has no length bound and is stored directly in `knowledge_citations.knowledge_path`; very long values could cause index bloat.

**Remediation:** Type `job_id` as `uuid.UUID` in the Pydantic model (FastAPI will validate it). Add `knowledge_path: str = Field(..., max_length=512)`.

---

### MED-3 — No rate limiting on any new endpoint
**File:** `main.py` (no rate limiting middleware), all new routes

**Description:** There is no rate-limiting middleware anywhere in the application (confirmed by grep). The endpoints added in Q4-Q9 perform DB queries (trigram search, audit query) and filesystem operations (promote/backfill) on every call. Without auth (CRIT-1) and without rate limiting, any caller can exhaust DB connections and CPU.

**Remediation:** Add `slowapi` or similar rate-limiting middleware to FastAPI. At minimum add per-IP limits on search and promote endpoints.

---

### MED-4 — `POST /backfill`: unauthenticated full filesystem scan
**File:** `tech_dev_agents/ops_console/routes/knowledge.py:131–136`; `services/knowledge_service.py:129–159`

**Description:** Calling `POST /backfill` triggers `Path(root).rglob("ANSWER.md")` across the entire repository root with no authentication (CRIT-1 applies). On large repos this is a CPU- and I/O-intensive operation. The `root` parameter defaults to `self._repo_root` (derived from `__file__`), but if `KnowledgeService` is ever constructed with a user-supplied `repo_root`, the same path traversal risk as CRIT-2 applies.

**Remediation:** Require MANAGER role for this endpoint. Validate that `root` is within an allowed set.

---

### MED-5 — `overrode_rule_id` in `log_decision` can be supplied by an internal caller to inflate override counts
**File:** `tech_dev_agents/ops_console/services/apprenticeship.py:76–125`

**Description:** `log_decision(overrode_rule_id=...)` is an internal service method, not exposed directly as an API endpoint. However, if it were ever exposed, or called with user-controlled input, passing an arbitrary `overrode_rule_id` would increment the `override_count` of the targeted rule, eventually triggering auto-disable (`check_and_disable_overridden_rules` disables rules with `override_count / fire_count > 0.10`). This allows an adversary to DoS legitimate automation rules.

**Remediation:** Ensure `overrode_rule_id` is never accepted from client input. Log a warning when `overrode_rule_id` targets a rule that was not matched in the current decision flow.

---

### MED-6 — `require_role` treats all Bearer (Entra ID) callers as ADMIN-equivalent
**File:** `tech_dev_agents/ops_console/auth.py:282–291`

**Description:**
```python
auth_header = request.headers.get("authorization", "").lower()
if auth_header.startswith("bearer "):
    return  # admin-equivalent, pass
```
Any valid Entra ID token grants ADMIN-level access regardless of the user's actual group membership or intended role. The comment acknowledges "Tighten with group-claim mapping as a follow-up." Until that tightening occurs, a low-privilege Entra user with a valid token can call MANAGER-gated endpoints (force-release, cancel, approve rule). This is an existing issue but is materially worsened by the new high-impact rule-approval endpoint in Q9.

**Remediation:** Map Entra ID groups to `Role` values in `require_role`. The Technology Agents group check in `require_auth` is a necessary but insufficient gate.

---

## LOW / Informational

### LOW-1 — Error from `svc.promote()` leaks `ValueError` text including DB row content
**File:** `tech_dev_agents/ops_console/routes/knowledge.py:124–128`

**Description:**
```python
except ValueError as exc:
    raise HTTPException(status_code=409, detail=str(exc)) from exc
```
The `ValueError` message includes `ingest_id`, current `status`, and `proposed_path` from the DB row (e.g., `"Cannot promote ingest_id=42: status is already 'rejected'"`). This is low risk but leaks internal state schema.

**Remediation:** Log the full exception server-side; return a generic 409 body: `{"detail": "Entry cannot be promoted in its current state"}`.

---

### LOW-2 — `GET /touch-rate` exposes Mark's weekly decision cadence with no auth
**File:** `tech_dev_agents/ops_console/routes/apprenticeship.py:115–119`

**Description:** Even if relatively innocuous, exposing how frequently a named human operator makes decisions is an organizational intelligence leak.

**Remediation:** Add auth (covered by CRIT-1 fix).

---

### LOW-3 — `_git_commit_kb` uses unvalidated `commit_msg` containing `proposed_path` and `approver`
**File:** `tech_dev_agents/ops_console/services/knowledge_service.py:588–612`

**Description:** The commit message is `f"promote: {proposed_path} (approved_by={approver})"` where both values are attacker-controlled (from DB or request body). Git commit messages are not executed, but a crafted message containing shell metacharacters could confuse downstream log parsers or Grafana queries. The `asyncio.create_subprocess_exec` call is safe from shell injection because it uses the exec form (no shell=True), but the message itself could contain newlines or special characters that break structured log parsing.

**Remediation:** Sanitize `proposed_path` and `approver` before embedding in the commit message (strip newlines, limit to 200 chars).

---

### LOW-4 — `list_decisions` and `list_proposals` have no upper bound on `limit`
**File:** `tech_dev_agents/ops_console/routes/apprenticeship.py:48`, `services/apprenticeship.py:252`

**Description:** `limit: int = 20` has no maximum. Passing `limit=1000000` would attempt to fetch the entire table into memory. Compare with `dispatch.py:1588` which uses `Query(50, ge=1, le=200)`.

**Remediation:** Change to `limit: int = Query(20, ge=1, le=200)`.

---

### LOW-5 — `ingest_from_completed_story` uses `found.parent.name` directly in `proposed_path`
**File:** `tech_dev_agents/ops_console/services/knowledge_service.py:445`

**Description:**
```python
"proposed_path": f"wiki/decisions/{found.parent.name}-{_slugify(stripped[:40])}.md"
```
`found.parent.name` is the directory name of the found file. If an agent creates a feature directory named `../../evil`, the resulting path `wiki/decisions/../../evil-<slug>.md` would traverse upward. This is later consumed by `_write_to_kb_repo` (CRIT-2). The `_slugify` call applies to `stripped` but not to `found.parent.name`.

**Remediation:** Apply `_slugify()` to `found.parent.name` before embedding it in the path, and rely on the traversal guard recommended in CRIT-2.

---

### LOW-6 — No CORS configuration covers the new `/api/knowledge` and `/api/apprenticeship` prefixes
**File:** `tech_dev_agents/ops_console/main.py:293–303`

**Description:** CORS is configured globally when `settings.cors_origins` is set. The middleware uses `allow_origins=origins` with explicit origin list. The new routes inherit this correctly — this is purely informational to confirm the CORS policy was not accidentally bypassed.

**Status:** No action needed; confirming existing middleware covers the new routes.

---

## Summary Table

| ID | Severity | Area | Status |
|----|----------|------|--------|
| CRIT-1 | Critical | Auth — all new routes unauthenticated | Open |
| CRIT-2 | Critical | Path traversal — arbitrary file write via `_write_to_kb_repo` | Open |
| CRIT-3 | Critical | Auth — `approve_rule` hardcodes `approved_by="mark"` | Open |
| HIGH-1 | High | Audit — client-supplied `approver` field | Open |
| HIGH-2 | High | Input — unbounded `window_days` inverts/exhausts DB query | Open |
| HIGH-3 | High | Info disclosure — `SELECT *` on `dispatch_decisions` | Open |
| HIGH-4 | High | Info disclosure — `SELECT *` on `dispatch_rules` | Open |
| HIGH-5 | High | Path traversal — `_update_index_md` `index_path` | Open |
| MED-1 | Medium | DoS — unbounded `q` param on trigram search | Open |
| MED-2 | Medium | Validation — `job_id` UUID not validated, path length unbounded | Open |
| MED-3 | Medium | DoS — no rate limiting on any new endpoint | Open |
| MED-4 | Medium | DoS — unauthenticated filesystem scan via `/backfill` | Open |
| MED-5 | Medium | Data integrity — `overrode_rule_id` can poison override counts | Open |
| MED-6 | Medium | Auth — Bearer callers treated as ADMIN in `require_role` | Open (pre-existing) |
| LOW-1 | Low | Info disclosure — `ValueError` text leaks DB row state | Open |
| LOW-2 | Low | Info disclosure — touch-rate exposes operator cadence | Open |
| LOW-3 | Low | Input — commit message contains raw attacker-controlled strings | Open |
| LOW-4 | Low | Resource — `limit` has no upper bound | Open |
| LOW-5 | Low | Path traversal — `found.parent.name` not slugified | Open |
| LOW-6 | Info | CORS coverage of new routes | No action |

---

## Verdict

**FAIL**

The Q4-Q9 wave introduces three critical defects that block merge:

1. **All new routes (knowledge + apprenticeship) are completely unauthenticated.** Any internet-accessible instance accepts these calls without credentials.
2. **Arbitrary file write via path traversal in `_write_to_kb_repo`.** An unauthenticated caller can write to any path on the server filesystem reachable from the process user.
3. **`approve_rule` hardcodes `approved_by="mark"`, poisoning the audit trail** and granting rule-approval authority to any credential.

The apprenticeship router is also missing from `main.py` entirely — it is not mounted and therefore dead code. This may be an oversight that, once corrected, would immediately expose all unauthenticated apprenticeship routes.

**Minimum required changes before merge:**
- Add `dependencies=[Depends(require_auth)]` to both new routers.
- Add `Depends(require_role(Role.MANAGER))` to `/promote`, `/backfill`, `/rules/{rule_id}/approve`.
- Add path traversal guards (CRIT-2, HIGH-5).
- Fix `approved_by` to use authenticated caller identity (CRIT-3).
- Register the apprenticeship router in `main.py`.
