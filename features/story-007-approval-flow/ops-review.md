# Ops Review: Approval Flow (STORY-007)

> Phase 6d — Ops Review
> Date: 2026-03-26
> Story: STORY-007

---

## 1. Timer Reliability Across Restarts

**Finding:** Timers are `NodeJS.setTimeout` handles — volatile in-memory state. `rehydrateOnStartup` rebuilds them from checkpoint `timeoutAt` and `sentAt` values. If the restart takes longer than the remaining window, the gate is immediately timed out on startup.

**Risk:** A container restart during a short timeout window (e.g., 5 minutes remaining when deployment begins) silently times out the gate before the developer is notified.

**Actions:**
- During rolling restarts, the new process fires `handleTimeout` immediately if `remainingMs <= 0`. Ensure the resulting timeout card is sent before accepting traffic (the startup sequence already enforces this via `rehydrateOnStartup` ordering — confirm this in integration tests).
- Add a structured log event on rehydration: `{ event: "gate_rehydrated", storyId, remainingMs, timedOutOnStartup: bool }`. This makes restart-induced timeouts visible in Application Insights without querying checkpoints.

---

## 2. Checkpoint Storage Durability

**Finding:** Checkpoints are JSON files on disk (inherited from STORY-006). The spec acknowledges (section 9.2) that a crash between a failed write and a successful retry loses gate state.

**Risk:** In a container environment, the disk is ephemeral unless mounted to persistent storage. If the volume is not mounted, a container replacement (not a restart) loses all checkpoint data, including pending gates.

**Actions:**
- Verify that the checkpoint directory is on a persistent volume mount in the container spec. Add a startup assertion that the path is writable and on the expected mount.
- Consider adding a `CHECKPOINT_DIR` env var check at startup that fails fast with a clear error if the directory is not on a known persistent path.
- Document the mount requirement in the deployment runbook (ops dependency on STORY-006 infra).

---

## 3. Heartbeat Overhead

**Finding:** Each pending gate writes to disk every 60 seconds via `startHeartbeat`. With N concurrent pending gates, this is N writes/minute. The spec stores the heartbeat interval reference by piggybacking on the `timers` map ("a third entry"), but the implementation detail is deferred.

**Risk:** Interval handle leak if the gate is resolved between heartbeat ticks and the `clearInterval` call is missed. Also, concurrent heartbeat writes to the same checkpoint file could corrupt state if the `CheckpointStore` does not serialize writes.

**Actions:**
- Implement heartbeat cleanup explicitly: when `clearTimers` is called, also call `clearInterval` on the heartbeat handle. Add a test asserting no active intervals remain after gate resolution.
- Ensure `CheckpointStore.setApprovalState` uses file locking or a write queue to prevent concurrent write corruption. Note this as a hard requirement on the STORY-006 interface.
- Consider reducing heartbeat frequency to 5 minutes (matching `recoveryThresholdMinutes`) to cut I/O by 5x while retaining recovery accuracy.

---

## 4. Timeout Edge Cases During Restart Window

**Finding:** The recovery decision matrix (section 5.2) handles the case where `reminderMs <= 0` but `expiryMs > 0` (missed reminder). However, there is no handling for the case where the process restarts multiple times within the reminder window, potentially sending multiple reminder cards.

**Risk:** If the process restarts twice before the reminder fires, `rehydrateOnStartup` is called twice. The second call reads `reminderSentAt` from checkpoint (set after the first reminder fires) and skips the duplicate — but only if the first reminder write succeeded. If the write failed, a second reminder is sent.

**Actions:**
- Before sending a recovery card, check `approvalState.reminderSentAt` — if set, skip the reminder-equivalent portion of the recovery card content (the "N minutes remaining" text is already stale).
- Add an idempotency key: before sending any card in `rehydrateOnStartup`, write a `rehydrationCardSentAt` field to the checkpoint first (optimistic lock). Read it back on startup and skip if already set within the last 60 seconds.

---

## 5. Monitoring Approval Latency

**Finding:** The spec logs card delivery failures and writes `resolvedAt` to checkpoints, but does not define any metrics or alerts for approval flow health.

**Risk:** No visibility into: average time-to-approve, timeout rate, reminder frequency, or gate delivery failure rate. Degraded approval flow could go undetected.

**Actions:** Add the following Application Insights custom metrics/events:

| Event | Dimensions | Use |
|-------|-----------|-----|
| `approval_gate_sent` | `storyId`, `gatePhase` | Gate delivery rate |
| `approval_gate_resolved` | `storyId`, `outcome`, `durationMs` (sentAt to resolvedAt) | Latency and outcome distribution |
| `approval_gate_timed_out` | `storyId`, `gatePhase` | Timeout rate alert trigger |
| `approval_delivery_failed` | `storyId`, error message | Delivery health alert |
| `approval_gate_rehydrated` | `storyId`, `timedOutOnStartup` | Restart impact visibility |

- Set an alert if `approval_gate_timed_out` rate exceeds 20% of sent gates in a rolling 24-hour window.
- Set an alert if `approval_delivery_failed` fires at all (should be zero under normal conditions).

---

## Summary Table

| Finding | Severity | Effort |
|---------|----------|--------|
| Restart during short timeout window silently expires gate | Medium | Low (log + test) |
| Checkpoint on ephemeral disk loses state on container replacement | High | Medium (infra config) |
| Heartbeat interval handle may leak on resolution | Medium | Low |
| Concurrent heartbeat writes may corrupt checkpoint | High | Medium (depends on STORY-006) |
| Multiple restarts in reminder window may double-send | Low | Low |
| No metrics or alerts for approval latency / timeout rate | High | Medium |
