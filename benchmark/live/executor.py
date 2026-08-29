"""Capped, injectable executor for a preflight-approved synthetic matrix.

The command-line path deliberately writes only a typed blocked artifact.  It has no
provider integration and therefore cannot spend money or invoke a model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .metrics import validate_results
from .runner import CampaignConfigurationError, build_run_matrix, configuration_blockers

_USAGE_FIELDS = ("tokens", "tool_calls", "elapsed_seconds", "cost_usd")
_CAP_FIELDS = ("turns", *_USAGE_FIELDS)
_ENV_KEYS = (
    "TASK_SANDBOX",
    "FIXTURE_PATH",
    "CAMPAIGN_ARM",
    "REPLICATE",
    "SYNTHETIC_TOOL_ALLOWLIST",
)
_OUTCOMES = frozenset(
    {
        "accepted",
        "not_accepted",
        "provider_error",
        "safety_refusal",
        "infrastructure_failure",
    }
)


class ManifestArtifactError(ValueError):
    """Raised when a redacted campaign manifest is invalid or detached."""


def _load_schema(name: str) -> Draft202012Validator:
    path = Path(__file__).with_name(name)
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _cell_key(cell: Mapping[str, Any]) -> tuple[str, str, str, int]:
    return cell["stack_id"], cell["task_id"], cell["arm"], cell["replicate"]


def _public_cell(cell: Mapping[str, Any]) -> dict[str, object]:
    return {
        "stack_id": cell["stack_id"],
        "task_id": cell["task_id"],
        "arm": cell["arm"],
        "replicate": cell["replicate"],
    }


def _metadata(stacks: Mapping[str, Any]) -> list[dict[str, object]]:
    return [
        {
            "stack_id": stack["stack_id"],
            "kind": stack["kind"],
            "provider": stack["provider"],
            "model": stack["model"],
            "cli": stack["cli"],
            "framework": stack["framework"],
        }
        for stack in stacks["stacks"]
    ]


def _scope(campaign: Mapping[str, Any]) -> dict[str, object]:
    return {
        "task_ids": [task["task_id"] for task in campaign["tasks"]],
        "arms": campaign["arms"],
        "replicates": list(range(1, campaign["replicates"] + 1)),
        "network": "disabled",
        "external_side_effect_capability": False,
    }


def _empty_usage() -> dict[str, int | float]:
    return {"tokens": 0, "tool_calls": 0, "elapsed_seconds": 0.0, "cost_usd": 0.0}


def _canonical_bytes(document: Mapping[str, Any]) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _result_path_id(path: Path | None) -> tuple[str, str]:
    name = path.name if path else "in-memory.json"
    if not name.endswith(".json"):
        raise ManifestArtifactError("result path must end in .json")
    result_id = name.removesuffix(".json")
    return f"benchmark/live/results/{name}", result_id


def _manifest_id(result_id: str) -> str:
    return f"{result_id}-manifest"


def _validate_closed_schema(document: object, schema_name: str) -> None:
    instance: Any = document
    errors = sorted(
        _load_schema(schema_name).iter_errors(instance),
        key=lambda error: list(error.path),
    )
    if errors:
        raise ManifestArtifactError(errors[0].message)


def validate_manifest(document: object) -> None:
    """Validate schema and semantic cell/count bindings without reading a provider."""
    _validate_closed_schema(document, "campaign-run-manifest.schema.json")
    assert isinstance(document, Mapping)
    planned = document["planned_cells"]
    recorded = document["recorded_cells"]
    assert isinstance(planned, Sequence)
    assert isinstance(recorded, Sequence)
    planned_keys: set[tuple[str, str, str, int]] = set()
    for cell in planned:
        assert isinstance(cell, Mapping)
        key = _cell_key(cell)
        if key in planned_keys:
            raise ManifestArtifactError("duplicate planned cell")
        planned_keys.add(key)
    recorded_keys: set[tuple[str, str, str, int]] = set()
    for cell in recorded:
        assert isinstance(cell, Mapping)
        key = _cell_key(cell)
        if key in recorded_keys:
            raise ManifestArtifactError("duplicate recorded cell")
        if key not in planned_keys:
            raise ManifestArtifactError("recorded cell is not planned")
        recorded_keys.add(key)
    if document["planned_run_count"] != len(planned_keys):
        raise ManifestArtifactError("planned run count does not bind planned cells")
    if document["recorded_run_count"] != len(recorded_keys):
        raise ManifestArtifactError("recorded run count does not bind recorded cells")


def _result_document(
    campaign: Mapping[str, Any],
    stacks: Mapping[str, Any],
    matrix: Sequence[Mapping[str, Any]],
    results: list[dict[str, object]],
    *,
    source_kind: str,
    result_id: str,
) -> dict[str, object]:
    usage = _empty_usage()
    for record in results:
        record_usage = record["usage"]
        assert isinstance(record_usage, Mapping)
        for field in _USAGE_FIELDS:
            usage[field] += record_usage[field]
    return {
        "contract_version": "1.0",
        "result_set_id": result_id,
        "source_kind": source_kind,
        "campaign_id": campaign["campaign_id"],
        "matched_run_manifest_ids": [_manifest_id(result_id)],
        "execution_scope": _scope(campaign),
        "stacks": _metadata(stacks),
        "caps_consumed": usage,
        "planned_cells": [_public_cell(cell) for cell in matrix],
        "results": results,
    }


def _manifest_document(
    campaign: Mapping[str, Any],
    matrix: Sequence[Mapping[str, Any]],
    results: Sequence[Mapping[str, Any]],
    result: Mapping[str, Any],
    *,
    result_path: str,
    result_id: str,
    status: str,
    blockers: Sequence[str],
) -> dict[str, object]:
    result_bytes = _canonical_bytes(result)
    return {
        "contract_version": "1.0",
        "manifest_id": _manifest_id(result_id),
        "campaign_id": campaign["campaign_id"],
        "result_source_kind": result["source_kind"],
        "status": status,
        "blockers": sorted(set(blockers)),
        "result_path": result_path,
        "result_sha256": hashlib.sha256(result_bytes).hexdigest(),
        "result_size": len(result_bytes),
        "planned_run_count": len(matrix),
        "recorded_run_count": len(results),
        "planned_cells": [_public_cell(cell) for cell in matrix],
        "recorded_cells": [_public_cell(record) for record in results],
    }


def _cap_reached(
    usage: Mapping[str, int | float], caps: Mapping[str, Any]
) -> str | None:
    for field in _CAP_FIELDS:
        if field in caps and usage[field] >= caps[field]:
            return field
    return None


def _event_usage(
    event: Mapping[str, Any], clock: Callable[[], float]
) -> dict[str, int | float]:
    raw_usage = event.get("usage")
    if not isinstance(raw_usage, Mapping):
        raise CampaignConfigurationError("runner_event_usage_invalid")
    elapsed = raw_usage.get("elapsed_seconds", clock())
    usage = {
        "turns": raw_usage.get("turns", 0),
        "tokens": raw_usage.get("tokens", 0),
        "tool_calls": raw_usage.get("tool_calls", 0),
        "elapsed_seconds": elapsed,
        "cost_usd": raw_usage.get("cost_usd", 0.0),
    }
    if not all(
        isinstance(value, (int, float)) and value >= 0 for value in usage.values()
    ):
        raise CampaignConfigurationError("runner_event_usage_invalid")
    return usage


def _record_for_run(
    cell: Mapping[str, Any],
    response: object,
    per_run_caps: Mapping[str, Any],
    clock: Callable[[], float],
) -> dict[str, object]:
    if not isinstance(response, Mapping) or not isinstance(
        response.get("events"), Sequence
    ):
        raise CampaignConfigurationError("runner_response_invalid")
    usage: dict[str, int | float] = {"turns": 0, **_empty_usage()}
    outcome = "not_accepted"
    first_cap: str | None = None
    for event in response["events"]:
        if not isinstance(event, Mapping):
            raise CampaignConfigurationError("runner_event_invalid")
        event_outcome = event.get("outcome", "not_accepted")
        if event_outcome not in _OUTCOMES:
            raise CampaignConfigurationError("runner_outcome_invalid")
        event_usage = _event_usage(event, clock)
        for field in _CAP_FIELDS:
            usage[field] += event_usage[field]
        outcome = event_outcome
        first_cap = _cap_reached(usage, per_run_caps)
        if first_cap:
            break
    if first_cap:
        status, failure_kind, intervention = "interrupted", "controller_failure", "stop"
    elif outcome == "accepted":
        status, failure_kind, intervention = "accepted", "none", "none"
    elif outcome == "not_accepted":
        status, failure_kind, intervention = "not_accepted", "none", "none"
    else:
        status, failure_kind, intervention = "failed", outcome, "none"
    return {
        **_public_cell(cell),
        "status": status,
        "failure_kind": failure_kind,
        "recovery": "not_attempted",
        "intervention": intervention,
        "detector_contribution": "not_applicable",
        "degraded_mode": False,
        "evidence_gain": 0,
        "repeated_side_effects_prevented": 0,
        "usage": {field: usage[field] for field in _USAGE_FIELDS},
    }


def _run_env(
    cell: Mapping[str, Any], campaign: Mapping[str, Any], sandbox_root: Path
) -> dict[str, str]:
    task = next(
        task for task in campaign["tasks"] if task["task_id"] == cell["task_id"]
    )
    sandbox = (
        sandbox_root
        / cell["stack_id"]
        / cell["task_id"]
        / cell["arm"]
        / str(cell["replicate"])
    )
    sandbox.mkdir(parents=True, exist_ok=False)
    return {
        "TASK_SANDBOX": str(sandbox),
        "FIXTURE_PATH": str(task["fixture_path"]),
        "CAMPAIGN_ARM": str(cell["arm"]),
        "REPLICATE": str(cell["replicate"]),
        "SYNTHETIC_TOOL_ALLOWLIST": ",".join(task["tools"]["allowlist"]),
    }


def execute_campaign(
    campaign: Mapping[str, Any],
    stacks: Mapping[str, Any],
    *,
    command_runner: Callable[[tuple[str, ...], Mapping[str, str]], object],
    clock: Callable[[], float],
    result_sink: Callable[[dict[str, object]], None],
    sandbox_root: Path,
    limit_matrix: int | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run only a configuration-approved finite matrix through injected commands."""
    full_matrix = build_run_matrix(campaign, stacks)
    matrix = full_matrix
    if limit_matrix is not None:
        matrix = matrix[:limit_matrix]
    if not matrix or len(full_matrix) > 108:
        raise CampaignConfigurationError("matrix_not_bounded")
    results: list[dict[str, object]] = []
    campaign_usage = _empty_usage()
    blockers: list[str] = []
    by_id = {stack["stack_id"]: stack for stack in stacks["stacks"]}
    for cell in matrix:
        stack = by_id[cell["stack_id"]]
        env = _run_env(cell, campaign, sandbox_root)
        record = _record_for_run(
            cell,
            command_runner(tuple(stack["command"]), env),
            campaign["per_run_caps"],
            clock,
        )
        results.append(record)
        record_usage = record["usage"]
        assert isinstance(record_usage, Mapping)
        for field in _USAGE_FIELDS:
            campaign_usage[field] += record_usage[field]
        cap = _cap_reached(campaign_usage, campaign["campaign_caps"])
        if cap:
            blockers.append(f"campaign_{cap}_cap_reached")
            break
    result_path, result_id = _result_path_id(None)
    source_kind = (
        "partial_campaign"
        if blockers or len(results) < len(full_matrix)
        else "executed_campaign"
    )
    result = _result_document(
        campaign,
        stacks,
        full_matrix,
        results,
        source_kind=source_kind,
        result_id=result_id,
    )
    validate_results(result)
    manifest = _manifest_document(
        campaign,
        full_matrix,
        results,
        result,
        result_path=result_path,
        result_id=result_id,
        status="partial" if source_kind == "partial_campaign" else "completed",
        blockers=blockers,
    )
    validate_manifest(manifest)
    result_sink(result)
    return result, manifest


def _write_json(path: Path, document: Mapping[str, Any]) -> None:
    """Persist the exact canonical bytes bound by the manifest hash and size."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical_bytes(document))


def create_blocked_artifacts(
    campaign: Mapping[str, Any],
    stacks: Mapping[str, Any],
    *,
    blockers: Sequence[str],
    command_runner: Callable[[tuple[str, ...], Mapping[str, str]], object],
    output_path: Path,
    manifest_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Write typed blocked evidence without dereferencing the command runner."""
    del command_runner
    _validate_closed_schema(campaign, "campaign.schema.json")
    _validate_closed_schema(stacks, "stacks.schema.json")
    matrix = tuple(
        {
            "stack_id": stack["stack_id"],
            "task_id": task["task_id"],
            "arm": arm,
            "replicate": replicate,
        }
        for stack in stacks["stacks"]
        for task in campaign["tasks"]
        for arm in campaign["arms"]
        for replicate in range(1, campaign["replicates"] + 1)
    )
    if len(matrix) > 108:
        raise CampaignConfigurationError("matrix_not_bounded")
    result_path, result_id = _result_path_id(output_path)
    result = _result_document(
        campaign,
        stacks,
        matrix,
        [],
        source_kind="blocked_campaign",
        result_id=result_id,
    )
    validate_results(result)
    manifest = _manifest_document(
        campaign,
        matrix,
        [],
        result,
        result_path=result_path,
        result_id=result_id,
        status="blocked",
        blockers=blockers,
    )
    validate_manifest(manifest)
    _write_json(output_path, result)
    _write_json(manifest_path, manifest)
    return result, manifest


def validate_selected_artifacts(
    selected: object,
    result: object,
    manifest: object,
    *,
    result_path: Path,
) -> None:
    """Cross-validate selected stacks, result bytes, and manifest bindings."""
    _validate_closed_schema(selected, "stacks.schema.json")
    validate_results(result)
    validate_manifest(manifest)
    assert isinstance(selected, Mapping)
    assert isinstance(result, Mapping)
    assert isinstance(manifest, Mapping)
    if [stack["stack_id"] for stack in selected["stacks"]] != [
        stack["stack_id"] for stack in result["stacks"]
    ]:
        raise ManifestArtifactError(
            "result stack metadata does not bind selected stacks"
        )
    if result["source_kind"] == "synthetic_example":
        raise ManifestArtifactError(
            "synthetic example cannot bind selected campaign artifacts"
        )
    if result["campaign_id"] != manifest["campaign_id"]:
        raise ManifestArtifactError("result campaign does not bind manifest campaign")
    if result["matched_run_manifest_ids"] != [manifest["manifest_id"]]:
        raise ManifestArtifactError("result manifest ids do not bind manifest")
    if manifest["result_source_kind"] != result["source_kind"]:
        raise ManifestArtifactError(
            "manifest source kind does not bind result source kind"
        )
    if manifest["planned_run_count"] != len(result["planned_cells"]):
        raise ManifestArtifactError("manifest planned count does not bind result cells")
    if manifest["recorded_run_count"] != len(result["results"]):
        raise ManifestArtifactError(
            "manifest recorded count does not bind result records"
        )
    if manifest["planned_cells"] != result["planned_cells"]:
        raise ManifestArtifactError("manifest planned cells do not bind result cells")
    result_bytes = result_path.read_bytes()
    if manifest["result_sha256"] != hashlib.sha256(result_bytes).hexdigest():
        raise ManifestArtifactError("result hash does not bind result bytes")
    if manifest["result_size"] != len(result_bytes):
        raise ManifestArtifactError("result size does not bind result bytes")
    expected_path = f"benchmark/live/results/{result_path.name}"
    if manifest["result_path"] != expected_path:
        raise ManifestArtifactError("result path does not bind artifact path")
    result_cells = [_public_cell(record) for record in result["results"]]
    if manifest["recorded_cells"] != result_cells:
        raise ManifestArtifactError(
            "manifest recorded cells do not bind result records"
        )


def _no_call_runner(command: tuple[str, ...], env: Mapping[str, str]) -> object:
    raise AssertionError("blocked no-call path must not invoke a stack command")


def main(argv: Sequence[str] | None = None) -> int:
    """Emit only no-call blocked evidence until an operator authorizes a campaign."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--stacks", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest-output", type=Path, required=True)
    args = parser.parse_args(argv)
    campaign = json.loads(args.campaign.read_text(encoding="utf-8"))
    stacks = json.loads(args.stacks.read_text(encoding="utf-8"))
    if not isinstance(campaign, Mapping) or not isinstance(stacks, Mapping):
        raise CampaignConfigurationError("campaign_or_stacks_not_object")
    blockers = [
        "second_selected_executable_stack_absent",
        "paid_execution_not_authorized",
    ]
    if not configuration_blockers(campaign, stacks):
        blockers = ["paid_execution_not_authorized"]
    _, manifest = create_blocked_artifacts(
        campaign,
        stacks,
        blockers=blockers,
        command_runner=_no_call_runner,
        output_path=args.output,
        manifest_path=args.manifest_output,
    )
    print(
        json.dumps(
            {"status": manifest["status"], "blockers": manifest["blockers"]},
            sort_keys=True,
        )
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
