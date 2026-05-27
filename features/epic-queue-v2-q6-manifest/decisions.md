# Q6 Decisions Log

**Story:** Epic-Queue-v2 / Q6 — Single Deploy Artifact + Protocol Manifest  
**Branch:** `epic-queue-v2/q6-manifest`  
**Date:** 2026-05-02

---

## Decision 1: Migration sentinel table mapping approach

**Context:** AC2 requires `startup_check.py` to verify that each listed migration has been applied. Two options were evaluated:
- (A) Create a dedicated `schema_migrations` tracking table and log each migration name on application.
- (B) Use `pg_tables` to check for a sentinel table that each migration creates.

**Decision:** Option B — `pg_tables` sentinel check.

**Rationale:** The spec explicitly specifies `SELECT EXISTS(SELECT FROM pg_tables WHERE tablename=$1)` — no custom tracking table needed. Option B requires zero additional schema changes (Q6 is additive-only) and maps cleanly to the immutable table names each migration introduces.

**Mapping:**
- `050_dispatch_v2_schema.sql` → sentinel table `dispatch_jobs`
- `051_dispatch_v2_dependencies.sql` → sentinel table `dispatch_dependencies`

---

## Decision 2: Deterministic tarball technique

**Context:** AC1 requires `build-artifact.sh` to produce identical SHA256 on two runs with identical inputs.

**Decision:** Use `tar --sort=name --mtime='2026-01-01 00:00:00 UTC' --owner=0 --group=0 --numeric-owner`.

**Rationale:** Filesystem mtime, owner UID/GID, and non-deterministic `find` glob ordering are the three sources of tarball non-determinism. All three are suppressed:
- `--sort=name` → alphabetical file order (independent of filesystem state)
- `--mtime` → fixed timestamp for all archive entries
- `--owner=0 --group=0 --numeric-owner` → suppress builder identity

The combined SHA256 that is embedded in the CHECKSUM file is computed over per-file hashes sorted by relative path — also order-independent.

---

## Decision 3: deploy.sh --verify-only mode

**Context:** AC3 requires `deploy.sh` to abort on checksum mismatch. The test invokes `deploy.sh --verify-only <artifact> <source_dir>` to test just the verification logic without triggering the full Docker-based deploy flow.

**Decision:** Add a `--verify-only` / `VERIFY_ONLY=1` mode at the top of the existing `deploy.sh` before all the Docker/SSH steps. When activated, it extracts the embedded CHECKSUM from the artifact, recomputes the hash from the source dir, compares, and exits 0 (match) or 1 (mismatch). The existing deploy flow is untouched.

**Rationale:** Avoids duplication — same script handles both CI artifact verification and production deployment. The verify path is exercised by tests without needing Docker infrastructure.

---

## Decision 4: CI grep gate placement

**Context:** AC4 requires a CI step that fails the build when `except TypeError` is found in `dispatch_v2.py` or `dispatch_poller_v2.py`.

**Decision:** Add the step to `.github/workflows/test.yml` in the `python-tests` job, immediately before the `Run framework-contract tests` step.

**Rationale:** The grep gate is a static code check that needs no Python runtime; placing it early in the job provides faster feedback. Scoped to exactly the two v2 files — legacy v1 files (`dispatch_poller.py`, `dispatch.py`) are explicitly excluded to avoid breaking existing shims until the Q5 v1 cutover.

**Gate logic:** Shell loop over both files; `grep -n "except TypeError"` exits 0 on match (found = bad). Uses `FAILED=1 → exit 1` pattern rather than `! grep` to produce a clear error message per file.

---

## Gate Decision: Q6 Status

**Status:** Gate C partial — Q6 ready.

Q6 is complete and all 19 tests are GREEN. Full Gate C requires:
- Q3 (failure policy — in progress on `epic-queue-v2/q3-failure-policy`)
- Q4 (lanes — in progress on `epic-queue-v2/q4-lanes`)

Once Q3 and Q4 implementation branches are merged to `feat/unified-queue-reliability`, Gate C can be formally declared complete.
