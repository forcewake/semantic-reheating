from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.domain_json_registry import (
    RegistryError,
    discover_domain_json,
    registry_entries,
    validate_registry,
)

ROOT = Path(__file__).parents[2]


def test_registry_explicitly_covers_every_public_domain_json() -> None:
    entries = registry_entries(ROOT)
    paths = {entry.path for entry in entries}
    required = {
        "contracts/v1/trace-event.schema.json",
        "benchmark/scenarios/manifest.json",
        "benchmark/results/deterministic-results.json",
        "examples/typescript-middleware/fixtures/python-v1-artifacts.json",
        "skills/semantic-reheating/references/pressure-scenarios.json",
        "skills/semantic-reheating/references/rubric.json",
        "skills/semantic-reheating/references/stack-receipt.json",
        "skills/semantic-reheating/references/baseline-summary.json",
        "skills/semantic-reheating/references/results.json",
        "benchmark/live/campaign.example.json",
        "benchmark/live/stacks.selected.json",
        "benchmark/live/results/campaign-2026-08-29-manifest.json",
        "article/semantic-reheating/article-data-manifest.json",
        "article/semantic-reheating/sources-ledger.json",
    }
    assert required <= paths
    assert discover_domain_json(ROOT) == paths
    validate_registry(ROOT)


def test_registry_rejects_duplicate_missing_and_undeclared_records(
    tmp_path: Path,
) -> None:
    schema = tmp_path / "schema.json"
    schema.write_text(
        json.dumps(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
            }
        )
    )
    (tmp_path / "benchmark").mkdir()
    (tmp_path / "benchmark" / "one.json").write_text("{}")
    (tmp_path / "benchmark" / "other.json").write_text("{}")
    entries = [
        {"path": "benchmark/one.json", "schema": "schema.json", "mode": "json"},
        {"path": "benchmark/one.json", "schema": "schema.json", "mode": "json"},
    ]
    with pytest.raises(RegistryError, match="duplicate"):
        validate_registry(tmp_path, entries)
    with pytest.raises(RegistryError, match="undeclared"):
        validate_registry(
            tmp_path,
            [{"path": "benchmark/one.json", "schema": "schema.json", "mode": "json"}],
        )
    with pytest.raises(RegistryError, match="absent schema"):
        validate_registry(
            tmp_path,
            [{"path": "benchmark/one.json", "schema": "missing.json", "mode": "json"}],
        )


def test_registry_does_not_infer_ownership_from_extensions(tmp_path: Path) -> None:
    (tmp_path / "random.json").write_text("{}")
    assert discover_domain_json(tmp_path) == set()
    validate_registry(tmp_path, [])
