# STORY-494: Test Design — Dispatch Queue Claim/Status Sync

## Coverage

| AC | Description |
|----|-------------|
| AC1 | Queue status matches agent reality (claimed during retry) |
| AC2 | Completion guard retries SHA verification |
| AC3 | Re-enqueue blocked when story being worked on |
| AC4 | Fleet-vigilance can detect/fix queue mismatches |

## Test Cases

### Server-side: Reclaim Endpoint (AC1, AC3, AC4)

| ID | Description | Expected |
|----|-------------|----------|
| T494-FF-01 | POST /api/dispatch/reclaim/STORY-001 when DISPATCH_CLAIM_SYNC_ENABLED=false | 404 |
| T494-02 | Reclaim pending story (flag on) | 200, status=claimed |
| T494-03 | Reclaim failed story (flag on) | 200, status=claimed |
| T494-04 | Reclaim claimed story by different agent (flag on) | 200, re-claimed |
| T494-05 | Reclaim non-existent story | 404 |
| T494-06 | Reclaim completed story (terminal) | 409 |

### Server-side: SHA Retry (AC2)

| ID | Description | Expected |
|----|-------------|----------|
| T494-SHA-01 | SHA verifies on 2nd attempt (transient 404) | True after 2 calls |
| T494-SHA-02 | SHA verification exhausts all retries | False |
| T494-SHA-03 | SHA verifies immediately on 1st call | True, 1 call |

### Poller-side: Claim-After-Retry (AC1, AC3)

| ID | Description | Expected |
|----|-------------|----------|
| T494-P-01 | _report_fail with retry claims story after re-enqueue | claim POST sent |
| T494-P-02 | _report_fail proceeds when claim-after-retry returns 409 | no crash |
| T494-P-03 | _report_fail skips claim-after-retry for rate-limit failures (exit_code=429) | no claim call |

## Feature Flag Behavior
- `DISPATCH_CLAIM_SYNC_ENABLED=false` (default): POST /api/dispatch/reclaim/* returns 404
- `DISPATCH_CLAIM_SYNC_ENABLED=true`: endpoint operates normally
