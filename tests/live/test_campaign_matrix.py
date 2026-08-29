from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def campaign() -> dict[str, object]:
    task_ids = (
        "exact-repetition-stall",
        "cycle-two-step",
        "unchanged-state",
        "repeated-error",
        "budget-burn-turns",
        "blocked-authority",
    )
    return {
        "contract_version": "1.0",
        "campaign_id": "synthetic-bounded-campaign",
        "tasks": [
            {
                "task_id": task_id,
                "fixture_path": f"benchmark/corpus/{task_id}.jsonl",
                "sandbox": {
                    "kind": "fixture_owned_isolated",
                    "root": f"$TASK_SANDBOX/{task_id}",
                    "external_side_effect_capability": False,
                },
                "tools": {
                    "allowlist": ["synthetic_trace_read", "fixture_read"],
                    "external_side_effect_capability": False,
                },
            }
            for task_id in task_ids
        ],
        "arms": ["hard_stop_only", "generic_rethink", "semantic_reheating"],
        "replicates": 3,
        "per_run_caps": {
            "turns": 30,
            "tool_calls": 40,
            "tokens": 50000,
            "elapsed_seconds": 1200,
            "cost_usd": 1.0,
        },
        "campaign_caps": {
            "tokens": 2000000,
            "tool_calls": 4320,
            "elapsed_seconds": 86400,
            "cost_usd": 40.0,
        },
    }


def stacks() -> dict[str, object]:
    common = {
        "status": "selected",
        "provider": {"name": "synthetic-provider", "version": "1.0.0"},
        "model": {"name": "synthetic-model", "version": "2026-08"},
        "cli": {"name": "synthetic-agent", "version": "1.0.0"},
        "framework": {"name": "synthetic-framework", "version": "1.0.0"},
        "command": [
            "synthetic-agent",
            "--offline",
            "--read-only",
            "--sandbox=$TASK_SANDBOX",
        ],
        "sandbox": {
            "mode": "task_local_isolated",
            "writable_scope": "task_sandbox_only",
            "network": "disabled",
            "external_side_effect_capability": False,
        },
        "tools": {
            "allowlist": ["synthetic_trace_read", "fixture_read"],
            "external_side_effect_capability": False,
        },
        "controls": {
            "decoding": {"status": "fixed", "temperature": 0.0, "top_p": 1.0},
            "seed": {"status": "fixed", "value": 7},
        },
        "telemetry": {
            "token_reporting": "available",
            "time_reporting": "available",
            "compute_metadata": {"runtime": "synthetic", "hardware": "none"},
        },
    }
    return {
        "contract_version": "1.0",
        "stacks": [
            {
                **deepcopy(common),
                "stack_id": "local-synthetic-a",
                "kind": "local",
                "pricing": {"cost_reporting": "local_zero", "direct_api_cost_usd": 0.0},
            },
            {
                **deepcopy(common),
                "stack_id": "remote-synthetic-b",
                "kind": "paid_remote",
                "pricing": {
                    "cost_reporting": "not_available",
                    "static_schedule": {
                        "status": "reviewed",
                        "input_usd_per_million": 1.0,
                        "output_usd_per_million": 2.0,
                        "conservative_token_upper_bound": 50000,
                    },
                },
            },
        ],
    }


@pytest.mark.parametrize("schema_name", ("campaign.schema.json", "stacks.schema.json"))
def test_live_schemas_are_closed_draft_2020_12_contracts(schema_name: str) -> None:
    import json

    schema = json.loads((PROJECT_ROOT / "benchmark" / "live" / schema_name).read_text())
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize(
    ("source", "mutate"),
    [
        ("campaign", lambda value: value.update(contract_version="2.0")),
        ("campaign", lambda value: value.update(unexpected=True)),
        (
            "campaign",
            lambda value: value["tasks"][0]["sandbox"].update(unexpected=True),
        ),
        ("campaign", lambda value: value.pop("per_run_caps")),
        ("campaign", lambda value: value.update(replicates="three")),
        ("campaign", lambda value: value.update(arms=["unknown_arm"])),
        (
            "campaign",
            lambda value: value["tasks"][0]["tools"].update(allowlist=["shell"]),
        ),
        ("stacks", lambda value: value.update(contract_version="2.0")),
        ("stacks", lambda value: value.update(unexpected=True)),
        ("stacks", lambda value: value["stacks"][0]["sandbox"].update(unexpected=True)),
        ("stacks", lambda value: value["stacks"][0].pop("provider")),
        ("stacks", lambda value: value["stacks"][0].update(command="synthetic-agent")),
        (
            "stacks",
            lambda value: value["stacks"][0]["controls"]["seed"].update(
                status="random"
            ),
        ),
        (
            "stacks",
            lambda value: value["stacks"][0]["tools"].update(allowlist=["shell"]),
        ),
    ],
)
def test_live_schemas_reject_unknowns_missing_wrong_types_and_closed_vocabularies(
    source: str, mutate: object
) -> None:
    import json

    instance = campaign() if source == "campaign" else stacks()
    assert callable(mutate)
    mutate(instance)
    schema = json.loads(
        (PROJECT_ROOT / "benchmark" / "live" / f"{source}.schema.json").read_text()
    )
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(instance)


def test_matrix_is_the_complete_two_stack_three_arm_three_replicate_campaign() -> None:
    from benchmark.live.runner import build_run_matrix

    planned = build_run_matrix(campaign(), stacks())
    assert len(planned) == 108
    assert {
        (run["stack_id"], run["task_id"], run["arm"], run["replicate"])
        for run in planned
    } == {
        (stack["stack_id"], task["task_id"], arm, replicate)
        for stack in stacks()["stacks"]
        for task in campaign()["tasks"]
        for arm in campaign()["arms"]
        for replicate in range(1, 4)
    }
