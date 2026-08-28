"""Closed public contract tests for the synthetic benchmark manifest."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from semantic_reheating.models import TraceEvent
from semantic_reheating.validation import load_public_json

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DETECTOR_ORDER = (
    "exact_repetition",
    "cycle",
    "repeated_error",
    "unchanged_state",
    "acceptance_stall",
    "budget_burn",
    "hard_budget",
    "repeated_risky_call",
)
SCHEMA_PATH = PROJECT_ROOT / "benchmark/schemas/v1/corpus-manifest.schema.json"
MANIFEST_PATH = PROJECT_ROOT / "benchmark/scenarios/manifest.json"


def _load(path: Path) -> dict[str, Any]:
    value = load_public_json(path.read_bytes())
    assert type(value) is dict
    return value


def _validator() -> Draft202012Validator:
    schema = _load(SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _assert_invalid(validator: Draft202012Validator, source: dict[str, Any]) -> None:
    assert list(validator.iter_errors(source))


def _assert_relational_bindings(source: dict[str, Any]) -> None:
    entries = source["entries"]
    assert len({entry["scenario_id"] for entry in entries}) == len(entries)
    assert len({entry["trace_path"] for entry in entries}) == len(entries)


def test_manifest_schema_and_minimal_contract_are_closed_and_versioned() -> None:
    validator = _validator()
    manifest = _load(MANIFEST_PATH)

    assert not list(validator.iter_errors(manifest))
    _assert_relational_bindings(manifest)
    assert manifest["schema_version"] == "1.0"
    assert manifest["corpus_version"] == "1.0"
    schema = _load(SCHEMA_PATH)
    assert schema["additionalProperties"] is False
    assert schema["properties"]["entries"]["items"]["additionalProperties"] is False


def test_manifest_schema_rejects_unknown_fields_versions_and_invalid_enums() -> None:
    validator = _validator()
    manifest = _load(MANIFEST_PATH)

    with_unknown_root = copy.deepcopy(manifest)
    with_unknown_root["unexpected"] = True
    _assert_invalid(validator, with_unknown_root)

    with_unknown_entry = copy.deepcopy(manifest)
    with_unknown_entry["entries"][0]["unexpected"] = True
    _assert_invalid(validator, with_unknown_entry)

    with_unknown_major = copy.deepcopy(manifest)
    with_unknown_major["schema_version"] = "2.0"
    _assert_invalid(validator, with_unknown_major)

    with_unknown_entry_major = copy.deepcopy(manifest)
    with_unknown_entry_major["entries"][0]["schema_version"] = "2.0"
    _assert_invalid(validator, with_unknown_entry_major)

    with_bad_enum = copy.deepcopy(manifest)
    with_bad_enum["entries"][0]["expected_decision"] = "execute"
    _assert_invalid(validator, with_bad_enum)

    missing_expectation = copy.deepcopy(manifest)
    del missing_expectation["entries"][0]["expected_safety_outcome"]
    _assert_invalid(validator, missing_expectation)


@pytest.mark.parametrize("field", ("scenario_id", "trace_path"))
def test_manifest_runtime_bindings_reject_duplicate_ids_and_paths(field: str) -> None:
    manifest = _load(MANIFEST_PATH)
    duplicate = copy.deepcopy(manifest)
    duplicate["entries"][1][field] = duplicate["entries"][0][field]
    with pytest.raises(AssertionError):
        _assert_relational_bindings(duplicate)


def test_manifest_runtime_bindings_reject_traversal_and_unbound_evidence() -> None:
    validator = _validator()
    manifest = _load(MANIFEST_PATH)
    traversal = copy.deepcopy(manifest)
    traversal["entries"][0]["trace_path"] = "benchmark/corpus/../outside.jsonl"
    _assert_invalid(validator, traversal)

    unbound_evidence = copy.deepcopy(manifest)
    unbound_evidence["entries"][0]["expected_evidence_event_ids"] = ["event-999"]
    events = _load_trace(unbound_evidence["entries"][0])
    with pytest.raises(AssertionError):
        assert set(unbound_evidence["entries"][0]["expected_evidence_event_ids"]) <= {
            event.event_id for event in events
        }


def test_manifest_has_the_balanced_named_scenario_coverage() -> None:
    manifest = _load(MANIFEST_PATH)
    entries = manifest["entries"]
    types = {entry["scenario_type"] for entry in entries}
    required_pathological = {
        "exact-repetition-stall",
        "cycle-two-step",
        "cycle-three-step",
        "cycle-four-step",
        "cycle-five-step",
        "unchanged-state",
        "repeated-error",
        "budget-burn-turns",
        "budget-burn-tool-calls",
        "budget-burn-tokens",
        "budget-burn-elapsed-seconds",
        "budget-burn-cost",
        "blocked-authority",
        "context-restart",
        "unsafe-write-repetition",
    }
    assert len(entries) == 29
    assert sum(entry["label"] == "pathological" for entry in entries) >= 12
    assert sum(entry["label"] == "productive_control" for entry in entries) >= 12
    assert required_pathological <= types
    for scenario_type in (
        "pagination",
        "batching",
        "state-changing-poll",
        "changed-hypothesis",
        "verification-rerun",
        "handoff",
        "eventual-consistency",
    ):
        assert sum(entry["scenario_type"] == scenario_type for entry in entries) == 2
    assert _load(SCHEMA_PATH)["properties"]["entries"]["minItems"] == 24


def test_pathological_scenarios_declare_their_intended_detectors() -> None:
    entries = {entry["scenario_id"]: entry for entry in _load(MANIFEST_PATH)["entries"]}

    assert (
        "exact_repetition"
        in entries["exact-repetition-stall"]["expected_detector_names"]
    )
    for scenario_id in (
        "cycle-two-step",
        "cycle-three-step",
        "cycle-four-step",
        "cycle-five-step",
    ):
        assert "cycle" in entries[scenario_id]["expected_detector_names"]
    assert "unchanged_state" in entries["unchanged-state"]["expected_detector_names"]
    assert "repeated_error" in entries["repeated-error"]["expected_detector_names"]
    for scenario_id in (
        "budget-burn-turns",
        "budget-burn-tool-calls",
        "budget-burn-tokens",
        "budget-burn-elapsed-seconds",
        "budget-burn-cost",
    ):
        assert {"budget_burn", "hard_budget"} & set(
            entries[scenario_id]["expected_detector_names"]
        )
    assert (
        "repeated_risky_call"
        in entries["unsafe-write-repetition"]["expected_detector_names"]
    )
    assert entries["blocked-authority"]["expected_detector_names"] == []
    assert entries["context-restart"]["expected_detector_names"] == []
    assert all(
        entry["expected_detector_names"] == []
        for entry in entries.values()
        if entry["label"] == "productive_control"
    )


def _load_trace(entry: dict[str, Any]) -> list[TraceEvent]:
    path = PROJECT_ROOT / entry["trace_path"]
    raw_lines = path.read_bytes().splitlines()
    assert raw_lines
    events: list[TraceEvent] = []
    for line in raw_lines:
        assert line.strip()
        source = load_public_json(line)
        assert type(source) is dict
        events.append(TraceEvent.from_dict(source))
    return events


def test_manifest_entries_bind_to_complete_valid_public_traces() -> None:
    manifest = _load(MANIFEST_PATH)
    for entry in manifest["entries"]:
        events = _load_trace(entry)
        assert 1 <= len(events) <= 10_000
        assert [event.sequence for event in events] == list(range(1, len(events) + 1))
        assert {event.run_id for event in events} == {f"run-{entry['scenario_id']}"}
        event_ids = [event.event_id for event in events]
        assert len(event_ids) == len(set(event_ids))
        assert set(entry["expected_evidence_event_ids"]) <= set(event_ids)
        assert tuple(
            sorted(entry["expected_detector_names"], key=DETECTOR_ORDER.index)
        ) == tuple(entry["expected_detector_names"])
        assert (PROJECT_ROOT / entry["trace_path"]).stem == entry["scenario_id"]
