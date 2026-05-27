# Q6: Single Deploy Artifact + Protocol Manifest

**Story:** Epic-Queue-v2 / Q6  
**Phase:** 7 (Test Design)  
**Scope:** Small (1 day)

## Goal

Make partial deploys of the v2 dispatch route impossible.  
A `protocol_manifest.json` lists all required v2 migrations and the minimum worker
version. The FastAPI startup hook reads the manifest and refuses to boot if any
migration is missing from the database. The deploy pipeline rejects artifact
tampering via a SHA256 checksum. A CI grep gate prevents `except TypeError`
shims from creeping back into v2 files.

## Files Added (Q6 owns)

- `tech_dev_agents/ops_console/protocol_manifest.json`
- `tech_dev_agents/ops_console/startup_check.py`
- `deployment/ops-console/build-artifact.sh`
- `.github/workflows/` — CI grep gate step

## Acceptance Criteria

- **AC1** `build-artifact.sh` produces a deterministic tarball (hash identical across two runs with identical inputs).
- **AC2** `startup_check.py` fails with `[STARTUP-FATAL]` when a manifest migration is missing from the DB.
- **AC3** Modifying `routes/dispatch_v2.py` without rebuilding fails `deploy.sh` checksum.
- **AC4** CI grep gate: `except TypeError` in `dispatch_v2.py` or `dispatch_poller_v2.py` fails the build.

## Key Decisions

- Migration check: query `pg_tables` / `information_schema.tables` for a known sentinel table from each migration — no need for a separate migration-tracking table.
- `startup_check.py` is imported and called from the existing `lifespan` context manager in `main.py`; it does NOT replace the lifespan.
- The grep gate is additive (new step in `test.yml`), targeting only v2 files.
