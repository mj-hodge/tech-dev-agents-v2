# Site Reliability: STORY-007 Approval Flow

> Phase 10 — Site Reliability / Operations
> Date: 2026-03-31
> Story: STORY-007
> Scope: Medium

## Operational Profile

| Attribute | Value |
|-----------|-------|
| Runtime model | Library module consumed by agent process (no standalone service) |
| State storage | STORY-006 checkpoint files (JSON on disk) |
| External dependencies | Teams Bot (STORY-002) for message delivery, SDLC Engine (STORY-006) for checkpoint storage |
| Concurrency model | Single-threaded asyncio; no mutex required |
| Expected gate frequency | 3-8 gates per story execution (one per phase boundary) |
| Expected concurrent gates | 1-3 (single developer, parallel story batches) |

## Failure Modes

| Failure | Detection | Impact | Recovery |
|---------|-----------|--------|----------|
| Teams message delivery failure | Messenger raises exception after retries | Gate not visible to developer; story stuck in pending | Integration layer marks `delivery-failed`; developer notified via fallback channel |
| Checkpoint write failure | setApprovalState raises IOError | In-memory state continues; next write recovers | Process restart rehydrates from last successful checkpoint |
| Process crash during pending gate | Process exit | Timers lost; gate stuck until restart | rehydrateOnStartup rebuilds timers from checkpoint timeoutAt |
| Process crash during gate resolution | Process exit mid-write | Checkpoint may have stale `pending` status | rehydrateOnStartup re-sends recovery card; developer re-approves |
| Timer drift (long-running process) | Reminder/expiry fire slightly late | Developer gets late reminder or late timeout | Acceptable — timers are informational, not SLA-bound |
| Checkpoint file corruption | Malformed JSON on read | State lost for affected story | Treat as delivery-failed; developer can re-trigger story |

## Restart Behavior

### Startup Sequence (CRITICAL)

1. Initialize ApprovalFlowManager with dependencies
2. Call `rehydrateOnStartup()` **before** accepting inbound Bot Framework traffic
3. For each pending gate in checkpoint store:
   - If `timeoutAt` has passed → fire `handleTimeout` immediately
   - If downtime > `recoveryThresholdMinutes` (5 min) → send recovery card
   - Re-register reminder and expiry timers for remaining time
4. Begin accepting inbound traffic

### Ordering Guarantee

The SDLC Engine must re-register its callbacks before `rehydrateOnStartup` is called. Otherwise, timeout events during rehydration will fire with no callback registered (the engine won't know the gate resolved).

## Scaling Considerations

| Dimension | v1 Capacity | Scaling Path |
|-----------|------------|--------------|
| Concurrent pending gates | ~10 (in-memory dict) | Move to Redis/DB if >50 concurrent gates needed |
| Checkpoint I/O | 1 file per story, <1KB per gate | Move to database if >100 concurrent stories |
| Timer handles | OS-level timers via asyncio | Thousands of timers are fine for asyncio event loop |
| Message throughput | ~1 msg/gate (card + ack) | Teams rate limits are 50 msg/sec per bot; no concern |

## Monitoring Recommendations

| Metric | Source | Alert Threshold |
|--------|--------|----------------|
| Gate delivery success rate | Event sink `gate-resolved` vs `delivery-failed` | < 95% over 1 hour |
| Time-to-approve (p50, p95) | `resolvedAt - sentAt` from checkpoint | p95 > 30 min (informational) |
| Timeout rate | Event sink `gate-timeout` count | > 20% of gates in 24h |
| Recovery card sent | Event sink or log on rehydration | > 0 per day (indicates restarts during pending gates) |
| Checkpoint write latency | Instrumentation around setApprovalState | p95 > 100ms |

## Runbook: Common Scenarios

### Story stuck in "pending" with no Teams card visible

1. Check checkpoint file for the story: `cat .checkpoints/<story-id>.json | jq .approvalState`
2. If `status: "pending"` and `sentAt` is recent: bot may have failed to deliver. Check messenger logs.
3. If `status: "pending"` and `sentAt` is old: timeout should have fired. Check if process was down during timeout window.
4. Recovery: restart the agent process. `rehydrateOnStartup` will re-send a recovery card or fire timeout.

### Developer approved but story did not advance

1. Check checkpoint: `approvalState.status` should be `approved`. If still `pending`, the approval message was not processed.
2. Check if the reply was in the correct thread (thread_id must match).
3. Check if the bot was down when the developer replied (reply lost during downtime).
4. Recovery: developer sends "approve" again in the original thread.

### Gate timed out unexpectedly

1. Check `sentAt` and `timeoutAt` in checkpoint. Verify `timeoutMinutes` config value.
2. Check if process restarted during the timeout window (recovery card in thread indicates this).
3. Recovery: developer sends "resume" in the original thread to re-present the gate.

## Verdict

**APPROVED** — The approval flow module has well-defined failure modes with recovery paths. All critical failures are recoverable via process restart + rehydration. No operational blockers for v1 deployment.
