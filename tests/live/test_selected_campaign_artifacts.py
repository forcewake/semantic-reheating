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


def _result_record(cell: dict[str, object]) -> dict[str, object]:
    return {
        **cell,
        "status": "accepted",
        "failure_kind": "none",
        "recovery": "not_attempted",
        "intervention": "none",
        "detector_contribution": "not_applicable",
        "degraded_mode": False,
        "evidence_gain": 0,
        "repeated_side_effects_prevented": 0,
        "usage": {
            "tokens": 0,
            "tool_calls": 0,
            "elapsed_seconds": 0.0,
            "cost_usd": 0.0,
        },
    }


def manifest_document() -> dict[str, Any]:
    return {
        "contract_version": "1.0",
        "manifest_id": "campaign-2026-08-29-manifest",
        "campaign_id": "synthetic-bounded-campaign",
        "result_source_kind": "blocked_campaign",
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


@pytest.mark.parametrize(
    ("source_kind", "status", "recorded_count", "recorded_cells"),
    [
        ("blocked_campaign", "partial", 0, []),
        ("blocked_campaign", "blocked", 1, [_cell()]),
        ("partial_campaign", "completed", 1, [_cell()]),
        ("partial_campaign", "partial", 0, []),
        ("executed_campaign", "completed", 0, []),
    ],
)
def test_manifest_schema_rejects_source_status_and_count_mismatches(
    source_kind: str,
    status: str,
    recorded_count: int,
    recorded_cells: list[dict[str, object]],
) -> None:
    document = manifest_document()
    document.update(
        result_source_kind=source_kind,
        status=status,
        recorded_run_count=recorded_count,
        recorded_cells=recorded_cells,
    )
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(document)


@pytest.mark.parametrize("recorded_count", [0, 1])
def test_selected_artifact_rejects_fabricated_completed_execution_evidence(
    tmp_path: Path, recorded_count: int
) -> None:
    from benchmark.live.executor import validate_selected_artifacts

    selected = json.loads(SELECTED_PATH.read_text(encoding="utf-8"))
    result_path = PROJECT_ROOT / "benchmark/live/results/campaign-2026-08-29.json"
    manifest_path = (
        PROJECT_ROOT / "benchmark/live/results/campaign-2026-08-29-manifest.json"
    )
    result = json.loads(result_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    result["source_kind"] = "executed_campaign"
    if recorded_count:
        result["results"] = [_result_record(result["planned_cells"][0])]
    manifest["result_source_kind"] = "executed_campaign"
    manifest["status"] = "completed"
    manifest["recorded_run_count"] = recorded_count
    manifest["recorded_cells"] = [
        {key: record[key] for key in ("stack_id", "task_id", "arm", "replicate")}
        for record in result["results"]
    ]
    result_bytes = json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
    manifest["result_sha256"] = hashlib.sha256(result_bytes).hexdigest()
    manifest["result_size"] = len(result_bytes)
    forged_path = tmp_path / result_path.name
    forged_path.write_bytes(result_bytes)

    with pytest.raises(ValueError):
        validate_selected_artifacts(selected, result, manifest, result_path=forged_path)


def test_selected_artifact_requires_the_exact_executed_planned_cell_set(
    tmp_path: Path,
) -> None:
    from benchmark.live.executor import validate_selected_artifacts

    selected = json.loads(SELECTED_PATH.read_text(encoding="utf-8"))
    result_path = PROJECT_ROOT / "benchmark/live/results/campaign-2026-08-29.json"
    manifest_path = (
        PROJECT_ROOT / "benchmark/live/results/campaign-2026-08-29-manifest.json"
    )
    result = json.loads(result_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    result["source_kind"] = "executed_campaign"
    result["results"] = [_result_record(cell) for cell in result["planned_cells"]]
    result["results"][-1]["task_id"] = "unplanned-task"
    manifest.update(
        result_source_kind="executed_campaign",
        status="completed",
        recorded_run_count=len(result["results"]),
        recorded_cells=[
            {key: record[key] for key in ("stack_id", "task_id", "arm", "replicate")}
            for record in result["results"]
        ],
    )
    result_bytes = json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
    manifest["result_sha256"] = hashlib.sha256(result_bytes).hexdigest()
    manifest["result_size"] = len(result_bytes)
    forged_path = tmp_path / result_path.name
    forged_path.write_bytes(result_bytes)

    with pytest.raises(ValueError, match="result cell is not planned"):
        validate_selected_artifacts(selected, result, manifest, result_path=forged_path)


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
