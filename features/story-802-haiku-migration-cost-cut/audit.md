# STORY-802 — VM Hermes Config Audit (AC-1, AC-2, AC-4, AC-5)

## Per-VM Audit Table

| VM | `model.default` | `auxiliary.compression.model` | `compression.threshold` | Per-Job Overrides | Status |
|----|-----------------|------------------------------|------------------------|-------------------|--------|
| derrick | _pending_ | _pending_ | _pending_ | _pending_ | Day 1 pilot |
| dan | _pending_ | _pending_ | _pending_ | _pending_ | Day 2 batch |
| devon | _pending_ | _pending_ | _pending_ | _pending_ | Day 2 batch |
| daisy | _pending_ | _pending_ | _pending_ | _pending_ | Day 2 batch |
| morris | _pending_ | _pending_ | _pending_ | _pending_ | Day 3 (after auth rotation) |

### Required Values

| Setting | Required | Current (assumed) |
|---------|----------|------------------|
| `model.default` | `sonnet` | `opus` (config drift) |
| `auxiliary.compression.model` | `claude-haiku-4-5` | likely `opus` |
| `compression.threshold` | `>= 0.85` | likely `0.5` |

## Verification Commands

```bash
# Check config
grep -A1 "^model:" /home/hermes/.hermes/config.yaml
grep "compression" /home/hermes/.hermes/config.yaml

# Check running model after a cron job completes
sqlite3 /home/hermes/.hermes/state.db "SELECT model, created_at FROM sessions ORDER BY created_at DESC LIMIT 3;"

# Restart persistence test (CRITICAL — run on derrick first)
sudo systemctl restart hermes-gateway
sleep 5
grep "model:" /home/hermes/.hermes/config.yaml | head -3
```

## Per-Job Audit (AC-5)

Walk `/home/hermes/.hermes/cron/jobs.json` on each VM. Any job with `model: null` whose actual runs are on Opus needs explicit `model: sonnet` override (or `model: haiku` for low-stakes summaries).

| VM | Jobs Audited | Overrides Applied | Notes |
|----|-------------|-------------------|-------|
| derrick | _pending_ | _pending_ | |
| dan | _pending_ | _pending_ | |
| devon | _pending_ | _pending_ | |
| daisy | _pending_ | _pending_ | |
| morris | _pending_ | _pending_ | |
