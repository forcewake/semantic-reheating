from __future__ import annotations

import json
from collections import UserDict
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_public_validation_api_exposes_the_six_closed_v1_artifacts() -> None:
    from semantic_reheating.validation import PUBLIC_CONTRACT_SCHEMAS

    assert set(PUBLIC_CONTRACT_SCHEMAS) == {
        "run_policy",
        "detector_finding",
        "decision_envelope",
        "recovery_instruction",
        "recovery_outcome",
        "evidence_record",
    }


def test_run_policy_schema_and_minimal_fixture_validate() -> None:
    schema = json.loads(
        (PROJECT_ROOT / "contracts" / "v1" / "run-policy.schema.json").read_text()
    )
    Draft202012Validator.check_schema(schema)
    fixture = json.loads(
        (
            PROJECT_ROOT
            / "tests"
            / "fixtures"
            / "contracts"
            / "minimal-run-policy.json"
        ).read_text()
    )
    Draft202012Validator(schema).validate(fixture)


def test_validator_accepts_every_minimal_public_fixture() -> None:
    from semantic_reheating.validation import validate_public_artifact

    kinds = {
        "run_policy": "minimal-run-policy.json",
        "detector_finding": "minimal-detector-finding.json",
        "decision_envelope": "minimal-decision-envelope.json",
        "recovery_instruction": "minimal-recovery-instruction.json",
        "recovery_outcome": "minimal-recovery-outcome.json",
        "evidence_record": "minimal-evidence-record.json",
    }
    for kind, name in kinds.items():
        data = json.loads(
            (PROJECT_ROOT / "tests" / "fixtures" / "contracts" / name).read_text()
        )
        assert validate_public_artifact(kind, data) == data


ARTIFACTS = {
    "run_policy": "minimal-run-policy.json",
    "detector_finding": "minimal-detector-finding.json",
    "decision_envelope": "minimal-decision-envelope.json",
    "recovery_instruction": "minimal-recovery-instruction.json",
    "recovery_outcome": "minimal-recovery-outcome.json",
    "evidence_record": "minimal-evidence-record.json",
}


def fixture(name: str) -> dict[str, object]:
    return json.loads(
        (PROJECT_ROOT / "tests" / "fixtures" / "contracts" / name).read_text()
    )


@pytest.mark.parametrize("kind,fixture_name", ARTIFACTS.items())
def test_each_closed_v1_schema_compiles_and_its_minimal_fixture_validates(
    kind: str, fixture_name: str
) -> None:
    from semantic_reheating.validation import (
        PUBLIC_CONTRACT_SCHEMAS,
        validate_public_artifact,
    )

    schema = json.loads((PROJECT_ROOT / PUBLIC_CONTRACT_SCHEMAS[kind]).read_text())
    Draft202012Validator.check_schema(schema)
    assert validate_public_artifact(kind, fixture(fixture_name)) is not None


@pytest.mark.parametrize("kind,fixture_name", ARTIFACTS.items())
def test_unknown_major_is_rejected_before_normal_schema_validation(
    kind: str, fixture_name: str
) -> None:
    from semantic_reheating.validation import (
        ContractValidationError,
        validate_public_artifact,
    )

    data = fixture(fixture_name)
    data["contract_version"] = "2.0"
    with pytest.raises(ContractValidationError, match="Unsupported") as caught:
        validate_public_artifact(kind, data)
    assert caught.value.code == "unknown_contract_major"


@pytest.mark.parametrize(
    ("kind", "fixture_name", "nested_field"),
    [
        ("run_policy", "minimal-run-policy.json", "detectors"),
        ("detector_finding", "minimal-detector-finding.json", "availability"),
        ("decision_envelope", "minimal-decision-envelope.json", "constraints"),
        (
            "recovery_instruction",
            "minimal-recovery-instruction.json",
            "expected_output",
        ),
        ("recovery_outcome", "minimal-recovery-outcome.json", "host_result"),
        ("evidence_record", "minimal-evidence-record.json", "trigger"),
    ],
)
def test_closed_contracts_reject_unknown_root_and_nested_fields(
    kind: str, fixture_name: str, nested_field: str
) -> None:
    from semantic_reheating.validation import (
        ContractValidationError,
        validate_public_artifact,
    )

    root_unknown = fixture(fixture_name)
    root_unknown["private_metadata"] = "not public"
    nested_unknown = fixture(fixture_name)
    nested_unknown[nested_field]["private_metadata"] = "not public"  # type: ignore[index]
    for invalid in (root_unknown, nested_unknown):
        with pytest.raises(ContractValidationError) as caught:
            validate_public_artifact(kind, invalid)
        assert caught.value.code == "schema_validation_error"


@pytest.mark.parametrize(
    ("kind", "fixture_name", "field"),
    [
        ("run_policy", "minimal-run-policy.json", "max_recovery_episodes"),
        ("detector_finding", "minimal-detector-finding.json", "event_ids"),
        ("decision_envelope", "minimal-decision-envelope.json", "requires_host_action"),
        ("recovery_instruction", "minimal-recovery-instruction.json", "allowed_tools"),
        ("recovery_outcome", "minimal-recovery-outcome.json", "human_escalation"),
        (
            "evidence_record",
            "minimal-evidence-record.json",
            "repeated_side_effects_avoided",
        ),
    ],
)
def test_closed_contracts_reject_wrong_scalar_and_collection_types(
    kind: str, fixture_name: str, field: str
) -> None:
    from semantic_reheating.validation import (
        ContractValidationError,
        validate_public_artifact,
    )

    invalid = fixture(fixture_name)
    invalid[field] = (
        {} if isinstance(invalid[field], (int, bool)) else "not-a-collection"
    )
    with pytest.raises(ContractValidationError) as caught:
        validate_public_artifact(kind, invalid)
    assert caught.value.code == "schema_validation_error"


def test_decision_enum_is_closed_and_escalation_requires_host_action() -> None:
    from semantic_reheating.validation import (
        ContractValidationError,
        validate_public_artifact,
    )

    escalation = fixture("minimal-decision-envelope.json")
    assert validate_public_artifact("decision_envelope", escalation) == escalation
    unknown = fixture("minimal-decision-envelope.json")
    unknown["decision"] = "override"
    without_host_action = fixture("minimal-decision-envelope.json")
    without_host_action["requires_host_action"] = False
    for invalid in (unknown, without_host_action):
        with pytest.raises(ContractValidationError) as caught:
            validate_public_artifact("decision_envelope", invalid)
        assert caught.value.code == "schema_validation_error"


@pytest.mark.parametrize(
    "source",
    [
        '{"contract_version":"1.0","contract_version":"2.0"}',
        '{"value":NaN}',
        '{"value":Infinity}',
        '{"value":-Infinity}',
        '{"value":1e100000}',
    ],
)
def test_strict_json_loader_rejects_ambiguous_or_nonfinite_numbers(source: str) -> None:
    from semantic_reheating.validation import ContractValidationError, load_public_json

    with pytest.raises(ContractValidationError) as caught:
        load_public_json(source)
    assert caught.value.code in {"duplicate_key", "invalid_json_number"}


def test_strict_json_loader_accepts_utf8_bytes_and_text_only() -> None:
    from semantic_reheating.validation import ContractValidationError, load_public_json

    assert load_public_json(b'{"public":true}') == {"public": True}
    with pytest.raises(ContractValidationError) as caught:
        load_public_json(42)  # type: ignore[arg-type]
    assert caught.value.code == "non_json_input"


def test_validation_rejects_non_json_host_objects_and_nonfinite_direct_data() -> None:
    from semantic_reheating.validation import (
        ContractValidationError,
        validate_public_artifact,
    )

    nonfinite = fixture("minimal-detector-finding.json")
    nonfinite["score"] = float("inf")
    with pytest.raises(ContractValidationError) as caught:
        validate_public_artifact("detector_finding", nonfinite)
    assert caught.value.code == "nonfinite_json_number"
    with pytest.raises(ContractValidationError) as caught:
        validate_public_artifact(
            "detector_finding", UserDict(fixture("minimal-detector-finding.json"))
        )
    assert caught.value.code == "non_json_data"


def test_unknown_kind_is_typed_and_registry_is_closed() -> None:
    from semantic_reheating.validation import (
        ContractValidationError,
        validate_public_artifact,
    )

    with pytest.raises(ContractValidationError) as caught:
        validate_public_artifact(
            "trace_event", fixture("minimal-detector-finding.json")
        )
    assert caught.value.code == "unknown_artifact_kind"
