from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = PROJECT_ROOT / "benchmark/live/campaign-run-manifest.schema.json"
SELECTED_PATH = PROJECT_ROOT / "benchmark/live/stacks.selected.json"


def _cell() -> dict[str, object]:
    return {
        "stack_id": "claude-code-zai-glm-5-3",
        "task_id": "exact-repetition-stall",
        "arm": "hard_stop_only",
        "replicate": 1,
    }


def manifest_document() -> dict[str, Any]:
    return {
        "contract_version": "1.0",
        "manifest_id": "campaign-2026-08-29-manifest",
        "campaign_id": "synthetic-bounded-campaign",
        "status": "blocked",
        "blockers": ["paid_execution_not_authorized"],
        "result_path": "benchmark/live/results/campaign-2026-08-29.json",
        "result_sha256": hashlib.sha256(b"{}").hexdigest(),
        "result_size": 2,
        "planned_run_count": 108,
        "recorded_run_count": 0,
        "planned_cells": [_cell()],
        "recorded_cells": [],
    }


def test_manifest_schema_is_closed_versioned_and_meta_valid() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["properties"]["contract_version"]["const"] == "1.0"
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(manifest_document())


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update(contract_version="2.0"),
        lambda value: value.update(unexpected=True),
        lambda value: value["planned_cells"][0].update(unexpected=True),
        lambda value: value.pop("result_path"),
        lambda value: value.pop("result_sha256"),
        lambda value: value.pop("planned_run_count"),
        lambda value: value.update(recorded_run_count="zero"),
        lambda value: value.update(status="unknown"),
    ],
)
def test_manifest_schema_rejects_unknowns_missing_bindings_wrong_types_and_statuses(
    mutate: object,
) -> None:
    document = deepcopy(manifest_document())
    assert callable(mutate)
    mutate(document)

    with pytest.raises(ValidationError):
        Draft202012Validator(json.loads(SCHEMA_PATH.read_text())).validate(document)


def test_manifest_validator_rejects_semantic_duplicate_cells() -> None:
    from benchmark.live.executor import ManifestArtifactError, validate_manifest

    document = manifest_document()
    document["planned_run_count"] = 2
    document["planned_cells"].append(deepcopy(document["planned_cells"][0]))

    with pytest.raises(ManifestArtifactError, match="duplicate planned cell"):
        validate_manifest(document)


def test_selected_stacks_and_blocked_artifacts_are_valid_and_metadata_is_explicit() -> (
    None
):
    from benchmark.live.executor import validate_selected_artifacts
    from benchmark.live.metrics import compute_metrics

    selected = json.loads(SELECTED_PATH.read_text(encoding="utf-8"))
    result_path = PROJECT_ROOT / "benchmark/live/results/campaign-2026-08-29.json"
    manifest_path = (
        PROJECT_ROOT / "benchmark/live/results/campaign-2026-08-29-manifest.json"
    )
    result = json.loads(result_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    validate_selected_artifacts(selected, result, manifest, result_path=result_path)
    assert [stack["stack_id"] for stack in selected["stacks"]] == [
        "claude-code-zai-glm-5-3",
        "second-executable-stack-unavailable",
    ]
    for stack in selected["stacks"]:
        assert set(stack).issuperset(
            {"provider", "model", "cli", "framework", "pricing", "sandbox", "tools"}
        )
    for stack in selected["stacks"]:
        if stack["kind"] == "paid_remote":
            assert stack["pricing"]["cost_reporting"] == "provider_reported"
    metrics = compute_metrics(result)
    assert metrics["sample_size"] == 0
    assert metrics["planned_sample_size"] == 108
    missing_cells = metrics["missing_cells"]
    assert isinstance(missing_cells, list)
    assert len(missing_cells) == 108
