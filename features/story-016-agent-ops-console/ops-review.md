# Operations Review: Agent Operations Console

> Phase 6d — Operations Review
> Story: STORY-016
> Date: 2026-04-01
> Scope: Large
> Reviewer: Ops Persona

---

## Review Scope

This review evaluates the operational readiness of the Agent Operations Console design across deployment, monitoring, logging, health checks, scaling, rollback, backup, disaster recovery, and operational runbook requirements.

**Deployment target:** Azure VM (co-located with existing infrastructure), Nginx reverse proxy, systemd service.

---

## Findings Summary

| ID | Category | Severity | Status | Summary |
|----|----------|----------|--------|---------|
| OPS-01 | Deployment | Low | Verified | Single systemd service + Nginx is appropriate |
| OPS-02 | Health Checks | Low | Mitigate | /api/health needs external monitoring integration |
| OPS-03 | Logging | Medium | Mitigate | Structured logging needed for all services |
| OPS-04 | Monitoring | Medium | Mitigate | Need alerting on console-down and error rates |
| OPS-05 | Rollback | Low | Mitigate | Need rollback procedure for failed deploys |
| OPS-06 | Scaling | Low | Verified | Single process is sufficient for 1-user tool |
| OPS-07 | Dependency Health | Medium | Mitigate | Circuit breaker pattern for degraded upstreams |
| OPS-08 | Backup | Low | Verified | No persistent state to back up |
| OPS-09 | Resource Limits | Low | Mitigate | Memory/CPU limits in systemd |
| OPS-10 | Log Rotation | Low | Mitigate | Prevent disk fill from application logs |
| OPS-11 | TLS Renewal | Low | Verified | Certbot auto-renewal is standard |
| OPS-12 | Operational Runbook | Medium | Mitigate | Document common operational procedures |

---

## Detailed Findings

### OPS-01: Deployment Architecture (Low — Verified)

**Design:**
```
Internet → Nginx (443/TLS) → uvicorn (127.0.0.1:8000) → FastAPI
                                                          ├── React SPA (StaticFiles)
                                                          └── API endpoints
```

**Assessment:** Appropriate for a single-user internal tool. The architecture is simple, battle-tested, and matches existing agent VM deployment patterns.

**Deployment process:**
```bash
# 1. Build frontend
cd frontend && npm ci && npm run build

# 2. Copy artifacts to server
rsync -avz --delete \
  backend/ ops-console-server:/opt/ops-console/backend/
rsync -avz --delete \
  frontend/dist/ ops-console-server:/opt/ops-console/frontend/dist/

# 3. Install Python dependencies (if changed)
ssh ops-console-server "cd /opt/ops-console && pip install -r requirements.txt"

# 4. Restart service
ssh ops-console-server "sudo systemctl restart ops-console"

# 5. Verify
curl -s https://ops.gorillacommerce.ai/api/health | jq .status
```

**Estimated downtime per deploy:** <5 seconds (uvicorn restart).

---

### OPS-02: Health Checks (Low — Mitigate)

**Design:** `/api/health` endpoint returns console status including upstream reachability.

**Assessment:** The endpoint is well-designed but needs integration with external monitoring.

**Mitigation (Required):**

1. **Uptime monitoring:** Configure an external monitor (e.g., Grafana Cloud Synthetic Monitoring or UptimeRobot) to check `https://ops.gorillacommerce.ai/api/health` every 60 seconds.

2. **Alert on failure:** If health check fails 3 consecutive times (3 minutes), send alert to Teams channel.

3. **Health check response enrichment:** The `/api/health` endpoint should include upstream status:
   ```json
   {
     "status": "ok",
     "version": "0.1.0",
     "uptime_seconds": 3600,
     "checks": {
       "agents_reachable": {"count": 2, "total": 2, "healthy": true},
       "loki": {"reachable": true, "latency_ms": 150},
       "azure_cost_api": {"reachable": true, "latency_ms": 2500},
       "monday": {"reachable": true, "latency_ms": 300}
     },
     "checked_at": "2026-04-01T12:00:00Z"
   }
   ```

4. **Degraded status:** Return `"status": "degraded"` (still 200) when one or more upstreams are unreachable. Return `"status": "unhealthy"` (503) only when the console itself is failing.

---

### OPS-03: Structured Logging (Medium — Mitigate)

**Issue:** The feature spec does not specify a logging strategy. Without structured logging, debugging production issues requires SSH + log tailing.

**Mitigation (Required):**

1. **Use Python `structlog` or standard `logging` with JSON formatter:**
   ```python
   import logging
   import json

   class JSONFormatter(logging.Formatter):
       def format(self, record):
           return json.dumps({
               "timestamp": self.formatTime(record),
               "level": record.levelname,
               "logger": record.name,
               "message": record.getMessage(),
               "module": record.module,
               **getattr(record, "extra", {}),
           })
   ```

2. **Log categories and levels:**

   | Category | Level | Example |
   |----------|-------|---------|
   | Request lifecycle | INFO | `{"method": "GET", "path": "/api/agents", "status": 200, "duration_ms": 45}` |
   | Auth failure | WARNING | `{"event": "auth_failed", "source_ip": "10.0.1.1", "reason": "invalid_key"}` |
   | Upstream error | WARNING | `{"event": "upstream_error", "service": "loki", "error": "timeout"}` |
   | Agent restart | INFO | `{"event": "agent_restart", "agent": "dan", "reason": "stuck", "result": "success"}` |
   | Cache miss/hit | DEBUG | `{"event": "cache_miss", "key": "health:dan", "ttl": 30}` |
   | Application error | ERROR | `{"event": "unhandled_error", "error": "...", "traceback": "..."}` |

3. **Log destination:** stdout (captured by systemd journal). Optionally forward to Loki via Promtail for centralized querying.

4. **Request ID:** Generate a unique request ID per request (UUID4) and include in all log entries and response headers (`X-Request-ID`). Enables request tracing.

---

### OPS-04: Monitoring and Alerting (Medium — Mitigate)

**Issue:** The console monitors agents but nothing monitors the console itself.

**Mitigation (Required):**

1. **External health check** (see OPS-02) with alerting.

2. **Metrics to track** (via structured logs, queryable in Loki):
   | Metric | Source | Alert Threshold |
   |--------|--------|----------------|
   | Console uptime | Health check | Down >3 min |
   | Error rate (5xx) | Access logs | >10 errors/min |
   | API latency p95 | Request logs | >5s |
   | Agent health poll failures | Service logs | >50% agents unreachable for >5 min |
   | Loki query failures | Service logs | >3 consecutive failures |
   | Memory usage | systemd/cgroup | >500MB |

3. **Grafana dashboard for the console itself:** Create a basic Grafana dashboard with panels for:
   - Console uptime (up/down)
   - Request rate and error rate
   - API latency histogram
   - Upstream health (agents, Loki, Azure, Monday.com)

4. **Alert channel:** Teams webhook for critical alerts (console down, high error rate).

---

### OPS-05: Rollback Procedure (Low — Mitigate)

**Issue:** No rollback procedure is defined for failed deployments.

**Mitigation (Required):**

1. **Git-based rollback:** The deployment artifacts are built from a git commit. Rollback = deploy the previous commit.

2. **Rollback procedure:**
   ```bash
   # 1. Identify the last known-good commit
   git log --oneline -5

   # 2. Checkout and rebuild
   git checkout <good-commit>
   cd frontend && npm ci && npm run build

   # 3. Redeploy
   rsync -avz backend/ ops-console-server:/opt/ops-console/backend/
   rsync -avz frontend/dist/ ops-console-server:/opt/ops-console/frontend/dist/
   ssh ops-console-server "sudo systemctl restart ops-console"

   # 4. Verify
   curl -s https://ops.gorillacommerce.ai/api/health | jq .
   ```

3. **Keep previous build:** Before deploying, create a backup:
   ```bash
   ssh ops-console-server "cp -r /opt/ops-console /opt/ops-console.bak"
   ```
   Rollback is then:
   ```bash
   ssh ops-console-server "mv /opt/ops-console /opt/ops-console.failed && mv /opt/ops-console.bak /opt/ops-console && sudo systemctl restart ops-console"
   ```

4. **Estimated rollback time:** <2 minutes.

---

### OPS-06: Scaling (Low — Verified)

**Assessment:** A single uvicorn process with async request handling is sufficient for 1-2 concurrent users. The bottleneck is upstream services (Loki, agent VMs), not the console itself.

**If scaling is ever needed:**
- Add `--workers 2` to uvicorn for multi-process
- In-memory caches would need to be replaced with Redis (but this is very unlikely for an internal tool)

---

### OPS-07: Circuit Breaker for Upstream Services (Medium — Mitigate)

**Issue:** The console depends on 4 upstream services (agent VMs, Loki, Azure Cost API, Monday.com). If any upstream is slow or down, it should not cascade to make the entire console unresponsive.

**Mitigation (Required):**

1. **Timeouts on all upstream calls:**
   | Service | Timeout | Rationale |
   |---------|---------|-----------|
   | Agent VM health poll | 5s | Individual VMs may be unreachable |
   | Loki query_range | 30s | Large queries can be slow |
   | Azure Cost API | 15s | Azure APIs have variable latency |
   | Monday.com API | 10s | Generally fast but can spike |

2. **Graceful degradation:** When an upstream is down:
   - Agent VM: Return `status: "unknown"` with last-known data
   - Loki: Return empty cost/alert data with `"loki_available": false`
   - Azure Cost API: Return SDK-only costs (Loki data)
   - Monday.com: Return `current_story: null`

3. **Circuit breaker pattern (lightweight):**
   ```python
   class CircuitBreaker:
       def __init__(self, failure_threshold: int = 3, reset_timeout: float = 60):
           self.failures = 0
           self.threshold = failure_threshold
           self.reset_timeout = reset_timeout
           self.last_failure: float = 0
           self.state = "closed"  # closed, open, half-open

       def record_failure(self):
           self.failures += 1
           self.last_failure = time.time()
           if self.failures >= self.threshold:
               self.state = "open"

       def record_success(self):
           self.failures = 0
           self.state = "closed"

       def should_allow(self) -> bool:
           if self.state == "closed":
               return True
           if self.state == "open":
               if time.time() - self.last_failure > self.reset_timeout:
                   self.state = "half-open"
                   return True
               return False
           return True  # half-open: allow one request
   ```

4. **Apply to each upstream service** in the service layer. When circuit is open, immediately return cached/default data without attempting the call.

---

### OPS-08: Backup (Low — Verified)

**Assessment:** The console has no persistent state. All data comes from upstream services. The only state is:
- `agent-registry.json` (checked into git)
- `.env` file (secrets — backed up separately)
- In-memory caches (rebuilt on restart)

No backup procedure needed for the console itself.

---

### OPS-09: Resource Limits (Low — Mitigate)

**Mitigation:** Add resource limits to the systemd service:

```ini
[Service]
# Memory limit: 512MB (generous for a single Python process)
MemoryMax=512M
MemoryHigh=400M

# CPU: no hard limit, but accounting for monitoring
CPUAccounting=yes

# File descriptor limit
LimitNOFILE=4096

# Restart on OOM
OOMPolicy=restart
```

**Expected resource usage:**
- Memory: ~100-200MB (Python process + in-memory caches)
- CPU: <5% average (mostly waiting on I/O)
- Disk: ~50MB (Python venv + frontend build)

---

### OPS-10: Log Rotation (Low — Mitigate)

**Mitigation:**

1. **systemd journal:** Logs go to journald, which has built-in rotation. Verify journal is configured with size limits:
   ```
   /etc/systemd/journald.conf:
   SystemMaxUse=500M
   ```

2. **If using file-based logging:** Configure logrotate:
   ```
   /etc/logrotate.d/ops-console:
   /var/log/ops-console/*.log {
       daily
       rotate 14
       compress
       missingok
       notifempty
   }
   ```

---

### OPS-11: TLS Certificate Renewal (Low — Verified)

**Design:** Let's Encrypt via certbot with auto-renewal.

**Verification:**
- Certbot auto-renewal runs via systemd timer (standard on Ubuntu)
- Nginx reloads on certificate renewal (certbot hook or certbot-renew timer)
- Certificate expiry: 90 days, renewed at 60 days

**Recommendation:** Add certificate expiry check to monitoring (alert if cert expires in <14 days).

---

### OPS-12: Operational Runbook (Medium — Mitigate)

**Mitigation (Required):** Create a runbook section in the project README covering:

#### Common Operations

| Operation | Command |
|-----------|---------|
| Check console status | `sudo systemctl status ops-console` |
| View recent logs | `sudo journalctl -u ops-console -n 50 --no-pager` |
| Follow logs live | `sudo journalctl -u ops-console -f` |
| Restart console | `sudo systemctl restart ops-console` |
| Check health endpoint | `curl -s https://ops.gorillacommerce.ai/api/health \| jq .` |
| Check Nginx status | `sudo systemctl status nginx` |
| Test Nginx config | `sudo nginx -t` |
| Renew TLS cert | `sudo certbot renew --nginx` |

#### Troubleshooting

| Symptom | Likely Cause | Resolution |
|---------|-------------|------------|
| Console returns 502 | uvicorn process crashed | `sudo systemctl restart ops-console` |
| Console loads but no data | Upstream services unreachable | Check `/api/health` for upstream status |
| Agent shows "unknown" | Agent VM unreachable | SSH to agent VM, check health API |
| Cost data stale | Loki or Azure API down | Check Loki reachability in health endpoint |
| "Invalid API key" | Key mismatch or rotation needed | Verify `OPS_OPS_CONSOLE_API_KEY` in `.env` |
| High memory usage | Cache growth or memory leak | Restart service; if recurring, investigate with `py-spy` |
| TLS errors | Certificate expired | `sudo certbot renew --nginx && sudo systemctl reload nginx` |

#### Incident Response

1. **Console is down:**
   - Check: `sudo systemctl status ops-console`
   - If failed: `sudo journalctl -u ops-console -n 100` to see error
   - Restart: `sudo systemctl restart ops-console`
   - If repeated crashes: rollback to previous version (OPS-05)

2. **Agent VM unreachable from console:**
   - Check: `curl -s http://<vm-ip>:8080/health` from console server
   - If unreachable: check VM status in Azure portal
   - If VM is up but port closed: SSH to VM, check agent health API service

3. **Cost data incorrect:**
   - Check Loki directly: query `[COST_SUMMARY]` lines in Grafana
   - Check Azure Cost Management in Azure portal
   - Compare with console output
   - If mismatch: check cache TTL, force refresh by restarting console

---

## Deployment Checklist

### Pre-Deployment

- [ ] DNS record for `ops.gorillacommerce.ai` points to server IP
- [ ] Nginx installed and running on target server
- [ ] Let's Encrypt certificate issued for `ops.gorillacommerce.ai`
- [ ] Python 3.11+ installed on target server
- [ ] `ops-console` service user created (`useradd -r -s /bin/false ops-console`)
- [ ] `/opt/ops-console/` directory created, owned by `ops-console`
- [ ] `.env` file created with all required secrets, `chmod 600`
- [ ] `agent-registry.json` deployed to configured path
- [ ] Firewall allows inbound 443 (HTTPS) and outbound to Loki, Azure, Monday.com, agent VMs

### Deployment

- [ ] Frontend built (`npm ci && npm run build`)
- [ ] Backend and frontend artifacts copied to server
- [ ] Python dependencies installed (`pip install -r requirements.txt`)
- [ ] systemd service file installed and enabled
- [ ] Nginx site config installed and enabled
- [ ] Nginx config tested (`nginx -t`)
- [ ] Service started (`systemctl start ops-console`)

### Post-Deployment Verification

- [ ] `https://ops.gorillacommerce.ai/api/health` returns 200 with `"status": "ok"`
- [ ] Login page loads and accepts the configured API key
- [ ] Fleet overview displays agent data
- [ ] Agent cards show correct status (verify against direct VM health calls)
- [ ] Cost data displays (verify against Loki query)
- [ ] Alert panel loads (may be empty if no active alerts)
- [ ] Restart button works (test on a non-critical agent or in staging)
- [ ] External health check monitoring configured and reporting green

---

## Infrastructure Cost

| Resource | Monthly Cost | Notes |
|----------|-------------|-------|
| VM (co-located) | $0 | Sharing existing infrastructure VM |
| DNS record | $0 | Included in existing domain |
| TLS certificate | $0 | Let's Encrypt is free |
| Loki query volume | ~$0-5 | Minimal additional queries |
| Azure Cost API | $0 | Included in subscription |
| **Total** | **~$0-5/month** | |

---

## Verdict

**APPROVED with conditions.** The deployment architecture is appropriate and cost-effective. The following must be implemented:

1. External health check monitoring with Teams alerting (OPS-02, OPS-04)
2. Structured JSON logging with request ID tracing (OPS-03)
3. Upstream timeouts and graceful degradation for all 4 upstream services (OPS-07)
4. systemd resource limits (MemoryMax, LimitNOFILE) (OPS-09)
5. Operational runbook in project README (OPS-12)
6. Rollback procedure documented and tested (OPS-05)
