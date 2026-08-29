from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESULTS_PATH = (
    PROJECT_ROOT / "benchmark" / "live" / "results" / "example-redacted-results.json"
)
SCHEMA_PATH = PROJECT_ROOT / "benchmark" / "live" / "results.schema.json"

_FORBIDDEN_KEY = re.compile(
    r"(?:api[_-]?key|authorization|credential|password|secret|token_content|"
    r"raw(?:[_-]?(?:output|transcript|response))?|prompt|private|reasoning)",
    re.IGNORECASE,
)
_FORBIDDEN_VALUE = re.compile(
    r"(?:sk-[A-Za-z0-9]|bearer\s+|/home/|\\\\Users\\\\|BEGIN (?:RSA |OPENSSH )?PRIVATE KEY)",
    re.IGNORECASE,
)


def _walk_redacted(value: object) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            assert isinstance(key, str)
            assert not _FORBIDDEN_KEY.search(key), key
            _walk_redacted(nested)
    elif isinstance(value, Sequence) and not isinstance(value, str):
        for nested in value:
            _walk_redacted(nested)
    elif isinstance(value, str):
        assert not _FORBIDDEN_VALUE.search(value), value


def test_example_results_is_closed_redacted_public_artifact() -> None:
    document: dict[str, Any] = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    Draft202012Validator(schema).validate(document)
    _walk_redacted(document)

    assert document["source_kind"] == "synthetic_example"
    assert document["matched_run_manifest_ids"] == [
        "synthetic-bounded-campaign-manifest-v1"
    ]
    assert document["execution_scope"] == {
        "task_ids": ["exact-repetition-stall", "cycle-two-step"],
        "arms": ["hard_stop_only", "generic_rethink", "semantic_reheating"],
        "replicates": [1],
        "network": "disabled",
        "external_side_effect_capability": False,
    }
    assert {stack["stack_id"] for stack in document["stacks"]} == {
        "local-synthetic-a",
        "remote-synthetic-b",
    }
    assert all(
        set(stack) == {"stack_id", "kind", "provider", "model", "cli", "framework"}
        for stack in document["stacks"]
    )
    assert set(document["caps_consumed"]) == {
        "tokens",
        "tool_calls",
        "elapsed_seconds",
        "cost_usd",
    }


def test_result_records_are_aggregate_metrics_not_raw_provider_outputs() -> None:
    document: dict[str, Any] = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))

    for result in document["results"]:
        assert set(result) == {
            "stack_id",
            "task_id",
            "arm",
            "replicate",
            "status",
            "failure_kind",
            "recovery",
            "intervention",
            "detector_contribution",
            "degraded_mode",
            "evidence_gain",
            "repeated_side_effects_prevented",
            "usage",
        }
        assert set(result["usage"]) == {
            "tokens",
            "tool_calls",
            "elapsed_seconds",
            "cost_usd",
        }
