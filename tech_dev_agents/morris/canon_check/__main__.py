"""CLI entrypoint: `python -m tech_dev_agents.morris.canon_check owner/repo PR`.

Prints a JSON report of the canon-check result to stdout. Exits 0 on
SUCCESS / NA, 1 on FAILURE, 2 on usage error.

The CLI does NOT post comments or set status checks by itself — the
skill doc (`deployment/vm/skills/morris/canon-check/SKILL.md`) drives
those side-effects. This keeps the CLI safe for ad-hoc inspection.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys

from tech_dev_agents.morris.canon_check.checker import (
    check_pr_for_drift,
    load_pinned_ref,
)


def _result_to_dict(result) -> dict:
    out = dataclasses.asdict(result)
    # Replace tuple of DriftedFile dataclasses with plain dicts
    out["drifted"] = [dataclasses.asdict(d) for d in result.drifted]
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="canon-check")
    parser.add_argument("repo", help="owner/repo, e.g. hpi-gorillacommerce/walmart-supplier-v2")
    parser.add_argument("pr_number", type=int)
    parser.add_argument("--head-sha", default=None)
    parser.add_argument("--scaffold-ref", default=None,
                        help="Override scaffold ref. Default: pin file / env / 'main'.")
    args = parser.parse_args(argv)

    ref = args.scaffold_ref or load_pinned_ref()
    result = check_pr_for_drift(
        args.repo,
        args.pr_number,
        head_sha=args.head_sha,
        scaffold_ref=ref,
    )
    print(json.dumps(_result_to_dict(result), indent=2, default=str))
    return 1 if result.status == "FAILURE" else 0


if __name__ == "__main__":
    sys.exit(main())
