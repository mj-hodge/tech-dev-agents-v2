#!/usr/bin/env python3
"""Validate ops/bot-registry consistency.

Checks:
- Required top-level keys exist
- Required bot fields exist
- All bot secret refs are present in secret-catalog.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "ops" / "bot-registry" / "hermes-registry.json"
SECRETS_PATH = ROOT / "ops" / "bot-registry" / "secret-catalog.json"


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"failed to parse {path}: {exc}") from exc


def main() -> int:
    errors: list[str] = []

    registry = load_json(REGISTRY_PATH)
    secrets = load_json(SECRETS_PATH)

    for key in ("version", "updated_at_utc", "bots"):
        if key not in registry:
            errors.append(f"registry missing key: {key}")

    for key in ("version", "updated_at_utc", "secrets"):
        if key not in secrets:
            errors.append(f"secret-catalog missing key: {key}")

    bot_secret_refs: set[str] = set()

    for index, bot in enumerate(registry.get("bots", []), start=1):
        prefix = f"bot[{index}]"
        for key in ("bot_id", "display_name", "runtime", "secrets", "logs"):
            if key not in bot:
                errors.append(f"{prefix} missing key: {key}")

        runtime = bot.get("runtime", {})
        for key in ("platform", "resource_group"):
            if key not in runtime:
                errors.append(f"{prefix}.runtime missing key: {key}")

        for ref in bot.get("secrets", []):
            if not isinstance(ref, str) or not ref.startswith("kv://"):
                errors.append(f"{prefix} has invalid secret ref: {ref!r}")
            else:
                bot_secret_refs.add(ref)

    catalog_refs = {
        item.get("secret_ref")
        for item in secrets.get("secrets", [])
        if isinstance(item, dict) and "secret_ref" in item
    }

    for ref in sorted(bot_secret_refs):
        if ref not in catalog_refs:
            errors.append(f"secret ref used by bot but missing in catalog: {ref}")

    if errors:
        print("BOT REGISTRY VALIDATION: FAIL")
        for err in errors:
            print(f"- {err}")
        return 1

    print("BOT REGISTRY VALIDATION: PASS")
    print(f"bots={len(registry.get('bots', []))}")
    print(f"secrets={len(secrets.get('secrets', []))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
