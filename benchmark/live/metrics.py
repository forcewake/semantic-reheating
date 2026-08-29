"""Pure, offline recomputation for closed redacted campaign result artifacts."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

_FAILURE_KINDS = (
    "controller_failure",
    "infrastructure_failure",
    "provider_error",
    "safety_refusal",
)
_USAGE_FIELDS = ("tokens", "tool_calls", "elapsed_seconds", "cost_usd")


class ResultArtifactError(ValueError):
    """Raised when a redacted result artifact is invalid or internally inconsistent."""


def _schema() -> Draft202012Validator:
    schema_path = Path(__file__).with_name("results.schema.json")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _cell_key(cell: Mapping[str, Any]) -> tuple[str, str, str, int]:
    return (
        cell["stack_id"],
        cell["task_id"],
        cell["arm"],
        cell["replicate"],
    )


def _sorted_cell(cell: Mapping[str, Any]) -> dict[str, object]:
    return {
        "stack_id": cell["stack_id"],
        "task_id": cell["task_id"],
        "arm": cell["arm"],
        "replicate": cell["replicate"],
    }


def _ratio(numerator: float, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def validate_results(document: object) -> None:
    """Fail closed unless an artifact is schema-valid and its cells reconcile."""
    errors = sorted(_schema().iter_errors(document), key=lambda error: list(error.path))
    if errors:
        raise ResultArtifactError(errors[0].message)
    assert isinstance(document, Mapping)

    scope = document["execution_scope"]
    stacks = document["stacks"]
    planned_cells = document["planned_cells"]
    results = document["results"]
    caps_consumed = document["caps_consumed"]
    assert isinstance(scope, Mapping)
    assert isinstance(stacks, Sequence)
    assert isinstance(planned_cells, Sequence)
    assert isinstance(results, Sequence)
    assert isinstance(caps_consumed, Mapping)

    stack_ids = {stack["stack_id"] for stack in stacks if isinstance(stack, Mapping)}
    task_ids = set(scope["task_ids"])
    arms = set(scope["arms"])
    replicates = set(scope["replicates"])

    planned_keys: set[tuple[str, str, str, int]] = set()
    for cell in planned_cells:
        assert isinstance(cell, Mapping)
        key = _cell_key(cell)
        if key in planned_keys:
            raise ResultArtifactError("duplicate planned cell")
        if (
            key[0] not in stack_ids
            or key[1] not in task_ids
            or key[2] not in arms
            or key[3] not in replicates
        ):
            raise ResultArtifactError("planned cell is outside execution scope")
        planned_keys.add(key)

    result_keys: set[tuple[str, str, str, int]] = set()
    usage_totals: dict[str, int | float] = {field: 0 for field in _USAGE_FIELDS}
    for result in results:
        assert isinstance(result, Mapping)
        key = _cell_key(result)
        if key in result_keys:
            raise ResultArtifactError("duplicate result cell")
        if key not in planned_keys:
            raise ResultArtifactError("result cell is not planned")
        result_keys.add(key)
        usage = result["usage"]
        assert isinstance(usage, Mapping)
        for field in _USAGE_FIELDS:
            usage_totals[field] += usage[field]

    if usage_totals != dict(caps_consumed):
        raise ResultArtifactError("caps consumed do not equal recorded result usage")


def compute_metrics(document: Mapping[str, Any]) -> dict[str, object]:
    """Validate and recompute public aggregate metrics without provider interaction."""
    validate_results(document)
    planned_cells = document["planned_cells"]
    results = document["results"]
    assert isinstance(planned_cells, Sequence)
    assert isinstance(results, Sequence)

    planned_by_key = {
        _cell_key(cell): cell for cell in planned_cells if isinstance(cell, Mapping)
    }
    results_by_key = {
        _cell_key(result): result for result in results if isinstance(result, Mapping)
    }
    missing_cells = [
        _sorted_cell(planned_by_key[key])
        for key in sorted(set(planned_by_key) - set(results_by_key))
    ]
    sample_size = len(results_by_key)
    accepted = [
        result for result in results_by_key.values() if result["status"] == "accepted"
    ]
    recovery_attempts = [
        result
        for result in results_by_key.values()
        if result["recovery"] != "not_attempted"
    ]
    interventions = [
        result for result in results_by_key.values() if result["intervention"] != "none"
    ]
    false_interventions = [
        result for result in interventions if result["status"] != "accepted"
    ]

    usage_totals = {
        field: sum(result["usage"][field] for result in results_by_key.values())
        for field in _USAGE_FIELDS
    }
    failure_counts = {
        kind: sum(result["failure_kind"] == kind for result in results_by_key.values())
        for kind in _FAILURE_KINDS
    }
    evidence_gain_total = sum(
        result["evidence_gain"] for result in results_by_key.values()
    )
    detector_helped = sum(
        result["detector_contribution"] == "helped"
        for result in results_by_key.values()
    )

    return {
        "sample_size": sample_size,
        "planned_sample_size": len(planned_by_key),
        "missing_cells": missing_cells,
        "accepted_outcomes": len(accepted),
        "recovery_success_rate": _ratio(
            sum(result["recovery"] == "recovered" for result in recovery_attempts),
            len(recovery_attempts),
        ),
        "false_interventions": len(false_interventions),
        "false_intervention_rate": _ratio(len(false_interventions), len(interventions)),
        "tokens_per_accepted_outcome": _ratio(usage_totals["tokens"], len(accepted)),
        "tool_calls_per_accepted_outcome": _ratio(
            usage_totals["tool_calls"], len(accepted)
        ),
        "elapsed_seconds_per_accepted_outcome": _ratio(
            usage_totals["elapsed_seconds"], len(accepted)
        ),
        "cost_usd_per_accepted_outcome": _ratio(
            usage_totals["cost_usd"], len(accepted)
        ),
        "evidence_gain_total": evidence_gain_total,
        "evidence_gain_per_recorded_cell": _ratio(evidence_gain_total, sample_size),
        "repeated_side_effects_prevented": sum(
            result["repeated_side_effects_prevented"]
            for result in results_by_key.values()
        ),
        "restart_rate": _ratio(
            sum(
                result["intervention"] == "restart"
                for result in results_by_key.values()
            ),
            sample_size,
        ),
        "stop_rate": _ratio(
            sum(result["intervention"] == "stop" for result in results_by_key.values()),
            sample_size,
        ),
        "detector_contribution_rate": _ratio(detector_helped, len(accepted)),
        "degraded_mode_frequency": _ratio(
            sum(result["degraded_mode"] for result in results_by_key.values()),
            sample_size,
        ),
        "failure_counts": failure_counts,
        "caps_consumed": usage_totals,
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Print recomputed metrics for one local redacted results artifact."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path, help="redacted results JSON path")
    args = parser.parse_args(argv)
    document = json.loads(args.results.read_text(encoding="utf-8"))
    if not isinstance(document, Mapping):
        raise ResultArtifactError("results document must be an object")
    print(json.dumps(compute_metrics(document), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
