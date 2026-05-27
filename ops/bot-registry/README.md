# Bot Registry Source of Truth

This directory is the persistent storage for bot operations metadata.

## Purpose

Track, in one place:

- Bot names and IDs
- Where each bot is deployed
- Which secrets are required and where they live
- Log storage targets and retention
- Alerting expectations
- Runbooks tied to each bot

## Files

- `hermes-registry.json`: bot inventory and runtime metadata
- `secret-catalog.json`: secret ownership, rotation, and expiry metadata
- `changes.md`: append-only operational changes
- `populate-from-azure.md`: commands to refresh registry fields from Azure

## Rules

- Never store secret values in git.
- Store only secret references (`kv://<vault>/<secret-name>` pattern).
- Any bot creation/rename/decommission must update `hermes-registry.json`.
- Any key rotation must update `secret-catalog.json` and `changes.md`.

## Validation

Run:

```bash
python3 scripts/validate_bot_registry.py
```

This checks required fields and that every bot secret ref appears in `secret-catalog.json`.
