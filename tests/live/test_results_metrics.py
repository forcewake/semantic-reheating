from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESULTS_PATH = (
    PROJECT_ROOT / "benchmark" / "live" / "results" / "example-redacted-results.json"
)
SCHEMA_PATH = PROJECT_ROOT / "benchmark" / "live" / "results.schema.json"


def results_document() -> dict[str, Any]:
    return json.loads(RESULTS_PATH.read_text(encoding="utf-8"))


def test_results_schema_is_a_closed_draft_2020_12_contract() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["properties"]["contract_version"]["const"] == "1.0"
    Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update(contract_version="2.0"),
        lambda value: value.update(unexpected=True),
        lambda value: value["execution_scope"].update(unexpected=True),
        lambda value: value["stacks"][0]["provider"].update(unexpected=True),
        lambda value: value.pop("caps_consumed"),
        lambda value: value.update(matched_run_manifest_ids="manifest-id"),
        lambda value: value["results"][0].update(status="unknown"),
        lambda value: value["results"][0].update(failure_kind="unknown_failure"),
        lambda value: value["results"][0]["usage"].update(tokens="many"),
    ],
)
def test_results_schema_rejects_unknowns_missing_wrong_types_and_unknown_vocabularies(
    mutate: object,
) -> None:
    document = deepcopy(results_document())
    assert callable(mutate)
    mutate(document)
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(document)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update(source_kind="blocked_campaign"),
        lambda value: value.update(source_kind="partial_campaign", results=[]),
        lambda value: value.update(source_kind="executed_campaign"),
    ],
)
def test_results_schema_rejects_source_kind_count_mismatches(mutate: object) -> None:
    document = deepcopy(results_document())
    assert callable(mutate)
    mutate(document)
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(document)


def test_compute_metrics_preserves_partial_matrix_and_recomputes_all_public_measures() -> (
    None
):
    from benchmark.live.metrics import compute_metrics

    metrics = compute_metrics(results_document())

    assert metrics == {
        "sample_size": 5,
        "planned_sample_size": 8,
        "missing_cells": [
            {
                "stack_id": "local-synthetic-a",
                "task_id": "cycle-two-step",
                "arm": "hard_stop_only",
                "replicate": 1,
            },
            {
                "stack_id": "remote-synthetic-b",
                "task_id": "cycle-two-step",
                "arm": "semantic_reheating",
                "replicate": 1,
            },
            {
                "stack_id": "remote-synthetic-b",
                "task_id": "exact-repetition-stall",
                "arm": "generic_rethink",
                "replicate": 1,
            },
        ],
        "accepted_outcomes": 2,
        "recovery_success_rate": 0.5,
        "false_interventions": 1,
        "false_intervention_rate": 0.5,
        "tokens_per_accepted_outcome": 725.0,
        "tool_calls_per_accepted_outcome": 4.5,
        "elapsed_seconds_per_accepted_outcome": 23.0,
        "cost_usd_per_accepted_outcome": 0.12,
        "evidence_gain_total": 7,
        "evidence_gain_per_recorded_cell": 1.4,
        "repeated_side_effects_prevented": 2,
        "restart_rate": 0.2,
        "stop_rate": 0.2,
        "detector_contribution_rate": 0.5,
        "degraded_mode_frequency": 0.2,
        "failure_counts": {
            "controller_failure": 1,
            "infrastructure_failure": 1,
            "provider_error": 1,
            "safety_refusal": 0,
        },
        "caps_consumed": {
            "tokens": 1450,
            "tool_calls": 9,
            "elapsed_seconds": 46.0,
            "cost_usd": 0.24,
        },
    }


def test_compute_metrics_keeps_provider_safety_infrastructure_and_controller_failures_distinct() -> (
    None
):
    from benchmark.live.metrics import compute_metrics

    document = results_document()
    document["results"][1]["failure_kind"] = "safety_refusal"
    document["results"][2]["failure_kind"] = "provider_error"
    document["results"][3]["failure_kind"] = "infrastructure_failure"
    document["results"][4]["failure_kind"] = "controller_failure"

    metrics = compute_metrics(document)

    assert metrics["failure_counts"] == {
        "controller_failure": 1,
        "infrastructure_failure": 1,
        "provider_error": 1,
        "safety_refusal": 1,
    }
