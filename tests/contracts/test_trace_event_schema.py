from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = PROJECT_ROOT / "contracts" / "v1" / "trace-event.schema.json"


def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def validator() -> Draft202012Validator:
    schema = load_schema()
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def load_fixture(name: str) -> dict[str, Any]:
    fixture_path = PROJECT_ROOT / "tests" / "fixtures" / "contracts" / name
    return json.loads(fixture_path.read_text(encoding="utf-8"))


def test_trace_event_schema_loads_and_is_a_valid_draft_2020_12_schema() -> None:
    validator()


def test_minimal_trace_event_fixture_validates() -> None:
    validator().validate(load_fixture("minimal-trace-event.json"))


def test_contract_version_is_pinned_to_1_0() -> None:
    event = load_fixture("minimal-trace-event.json")
    event["contract_version"] = "2.0"

    assert not validator().is_valid(event)


def test_unknown_top_level_fields_fail_closed() -> None:
    event = load_fixture("unknown-trace-field.json")

    assert not validator().is_valid(event)


def test_kind_is_a_closed_enum() -> None:
    event = load_fixture("minimal-trace-event.json")
    event["kind"] = "private_reasoning"

    assert not validator().is_valid(event)


def test_effect_class_is_a_closed_enum() -> None:
    event = load_fixture("minimal-trace-event.json")
    event["effect_class"] = "potentially_repeatable"

    assert not validator().is_valid(event)


@pytest.mark.parametrize(
    ("representation", "value"),
    [
        ("payload", None),
        ("payload_ref", "trace://public/example"),
        ("payload_digest", "sha256:public-example"),
    ],
)
def test_each_single_payload_representation_validates(
    representation: str, value: Any
) -> None:
    event = load_fixture("minimal-trace-event.json")
    event.pop("payload")
    event[representation] = value

    assert validator().is_valid(event)


def test_payload_representation_requires_exactly_one_form() -> None:
    no_representation = load_fixture("minimal-trace-event.json")
    no_representation.pop("payload")
    two_representations = load_fixture("minimal-trace-event.json")
    two_representations["payload_ref"] = "trace://public/example"
    three_representations = load_fixture("minimal-trace-event.json")
    three_representations["payload_ref"] = "trace://public/example"
    three_representations["payload_digest"] = "sha256:public-example"

    assert not validator().is_valid(no_representation)
    assert not validator().is_valid(two_representations)
    assert not validator().is_valid(three_representations)


@pytest.mark.parametrize(
    "required_field",
    [
        "contract_version",
        "run_id",
        "event_id",
        "sequence",
        "kind",
        "actor",
        "effect_class",
    ],
)
def test_required_trace_event_fields_are_not_optional(required_field: str) -> None:
    event = load_fixture("minimal-trace-event.json")
    event.pop(required_field)

    assert not validator().is_valid(event)


def test_sequence_must_be_at_least_one() -> None:
    event = load_fixture("minimal-trace-event.json")
    event["sequence"] = 0

    assert not validator().is_valid(event)


def budget_counters() -> dict[str, int | float]:
    return {
        "turns": 0,
        "tool_calls": 0,
        "tokens": 0,
        "elapsed_seconds": 0.0,
        "cost": 0.0,
    }


def test_optional_public_trace_metadata_validates() -> None:
    event = load_fixture("minimal-trace-event.json")
    event.update(
        {
            "parent_event_id": "event-000",
            "state_fingerprint": "sha256:public-state",
            "error_fingerprint": "sha256:public-error",
            "acceptance_delta": "public acceptance summary",
            "evidence_refs": ["evidence://public/example"],
            "expected_state_change": True,
        }
    )

    assert validator().is_valid(event)


def test_budget_counters_are_closed_and_nonnegative() -> None:
    valid_event = load_fixture("minimal-trace-event.json")
    valid_event["budget_counters"] = budget_counters()
    unexpected_counter = load_fixture("minimal-trace-event.json")
    unexpected_counter["budget_counters"] = budget_counters() | {"private_limit": 1}
    negative_counter = load_fixture("minimal-trace-event.json")
    negative_counter["budget_counters"] = budget_counters() | {"cost": -0.01}
    incomplete_counter = load_fixture("minimal-trace-event.json")
    incomplete_counter["budget_counters"] = {
        key: value for key, value in budget_counters().items() if key != "tokens"
    }

    assert validator().is_valid(valid_event)
    assert not validator().is_valid(unexpected_counter)
    assert not validator().is_valid(negative_counter)
    assert not validator().is_valid(incomplete_counter)


def test_payload_remains_an_intentionally_opaque_json_value() -> None:
    event = load_fixture("minimal-trace-event.json")
    event["payload"] = ["public", {"nested": True}, 7]

    assert validator().is_valid(event)
