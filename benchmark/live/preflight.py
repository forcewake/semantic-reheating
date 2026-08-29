"""Command-line offline preflight for the bounded live campaign declaration."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .runner import build_run_matrix, configuration_blockers


def _load_object(path: Path) -> Mapping[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return value if isinstance(value, Mapping) else None


def preflight_documents(
    campaign: object, stacks: object, *, dry_run: bool
) -> dict[str, object]:
    """Validate and expand declarations only; this function never invokes a stack."""
    if not dry_run:
        return {
            "status": "blocked",
            "dry_run": False,
            "planned_runs": 0,
            "blockers": ["offline_preflight_requires_dry_run"],
        }
    blockers = configuration_blockers(campaign, stacks)
    if blockers:
        return {
            "status": "blocked",
            "dry_run": True,
            "planned_runs": 0,
            "blockers": blockers,
        }
    assert isinstance(campaign, Mapping)
    assert isinstance(stacks, Mapping)
    return {
        "status": "ready",
        "dry_run": True,
        "planned_runs": len(build_run_matrix(campaign, stacks)),
        "blockers": [],
    }


def preflight_files(
    campaign_path: Path, stacks_path: Path, *, dry_run: bool
) -> dict[str, object]:
    """Load bounded JSON declarations and pass them to the pure preflight gate."""
    campaign = _load_object(campaign_path)
    stacks = _load_object(stacks_path)
    if campaign is None or stacks is None:
        blockers: list[str] = []
        if campaign is None:
            blockers.append("campaign_unreadable")
        if stacks is None:
            blockers.append("stacks_unreadable")
        return {
            "status": "blocked",
            "dry_run": dry_run,
            "planned_runs": 0,
            "blockers": blockers,
        }
    return preflight_documents(campaign, stacks, dry_run=dry_run)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline bounded campaign preflight")
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--stacks", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    arguments = parser.parse_args(argv)
    result = preflight_files(
        arguments.campaign, arguments.stacks, dry_run=arguments.dry_run
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())
