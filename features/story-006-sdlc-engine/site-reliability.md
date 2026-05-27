# Site Reliability Report — STORY-006 SDLC Execution Engine

Date: 2026-03-31
Story: STORY-006
Scope: Large
Phase: 10

## Component Overview

| Attribute | Value |
|-----------|-------|
| Module | `tech_dev_agents/sdlc_engine.py` |
| Type | Library (no daemon, no server) |
| Runtime | Called by orchestrator; no standalone process |
| State persistence | JSON checkpoint files on local filesystem |
| External dependencies | None (stdlib only) |
| Network calls | None |
| Database | None |

## Operational Characteristics

### Resource Usage

| Resource | Usage | Notes |
|----------|-------|-------|
| CPU | Negligible | All operations are in-memory dict lookups, regex, and JSON serialization |
| Memory | <10 MB | Checkpoint records are small (<10 KB each); no large data structures retained |
| Disk I/O | Low | Single JSON file per story (~2-5 KB); atomic write uses temp file + rename |
| Network | None | Engine is offline-capable; network calls are downstream runner/tracker responsibility |

### Failure Modes

| Failure | Impact | Recovery | Detection |
|---------|--------|----------|-----------|
| Checkpoint file corruption | Engine starts fresh for the story | Automatic — `load()` returns `None` on corrupt JSON | Warning log on corrupt checkpoint |
| Disk full during checkpoint write | Temp file write fails; original checkpoint preserved | Atomic protocol ensures no partial writes | `OSError` raised to caller |
| Orphaned temp files from crash | Disk clutter (trivial) | Manual cleanup or startup sweep of `.tmp` files in story folder | `ls features/*/checkpoint.json.tmp` |
| Invalid scope string | `ValueError` raised | Caller-level error handling | Exception propagation |
| Unknown phase ID | `KeyError` from advance category lookup | Caller-level error handling | Exception propagation |

### Checkpoint Durability

The atomic write protocol ensures:
1. **Crash during write**: Temp file may be partial, but `.checkpoint.json` is untouched
2. **Crash after write, before rename**: Temp file is complete but not renamed; checkpoint is untouched
3. **Power loss after `os.replace()`**: POSIX guarantees atomic rename on same filesystem; checkpoint is consistent
4. **`fsync` before rename**: Ensures temp file contents are flushed to disk before rename

### Scaling Characteristics

| Dimension | Current | Limit | Notes |
|-----------|---------|-------|-------|
| Stories per engine instance | 1 | 1 | By design — engine manages one story at a time |
| Phases per story | 1-12 | 12 | Determined by scope; large = 12 entries |
| Checkpoint file size | ~2-5 KB | ~50 KB | Grows with deliverables map and error log |
| Concurrent engines | N/A | Unlimited | Each engine operates on its own story folder; no shared state |

## Monitoring

### Metrics to Track (when integrated)

| Metric | Type | Source |
|--------|------|--------|
| `sdlc.phase.duration_seconds` | Histogram | Phase execution timing (downstream runner) |
| `sdlc.phase.retry_count` | Counter | Retry attempts per phase |
| `sdlc.story.scope` | Label | Classified scope for each story |
| `sdlc.checkpoint.write_count` | Counter | Checkpoint writes per story |
| `sdlc.classification.method` | Label | `override` vs `rule_based` vs `llm_hybrid` |

### Log Events

| Event | Level | Fields |
|-------|-------|--------|
| Checkpoint written | DEBUG | `story_id`, `phase`, `status` |
| Checkpoint loaded | DEBUG | `story_id`, `phase`, `status` |
| Checkpoint corrupt | WARNING | `story_id`, `path`, `error` |
| Scope classified | INFO | `story_id`, `scope`, `method`, `score` |
| Phase advanced | INFO | `story_id`, `phase`, `category`, `next_phase` |

## Runbook

### Checkpoint Recovery

If a story is stuck:
```bash
# Inspect checkpoint
cat features/<story-slug>/.checkpoint.json | python3 -m json.tool

# Reset checkpoint (restart from scratch)
rm features/<story-slug>/.checkpoint.json

# Clean orphaned temp files
rm -f features/*/.checkpoint.json.tmp
```

### Scope Override

If automatic classification is wrong:
```python
engine.classify_scope("task description", scope_override="medium")
```

### Test Verification

```bash
python3 -m pytest tests/test_sdlc_engine.py -v
# Expected: 26 passed
```

## SLA / Reliability Targets

| Metric | Target | Rationale |
|--------|--------|-----------|
| Checkpoint durability | 99.99% | Atomic writes prevent corruption; only catastrophic disk failure causes data loss |
| Classification correctness | 80%+ | Heuristic-based V1; override available as escape hatch; LLM-hybrid in Milestone 2 |
| Engine availability | N/A | Library, not a service; availability depends on calling process |

## Recommendations

1. **Add startup sweep** for orphaned `.checkpoint.json.tmp` files when the orchestrator initializes
2. **Add structured logging** to checkpoint write/load/delete operations for Grafana/Loki observability
3. **Consider checkpoint versioning migration** when schema changes are needed (version field already present)
4. **No alerting needed** — this is a library component; alerts belong at the orchestrator level
