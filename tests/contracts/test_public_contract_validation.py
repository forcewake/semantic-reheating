from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from collections import UserDict
from collections.abc import Iterator, Mapping, Sequence
from copy import deepcopy
from importlib import resources
from pathlib import Path
from typing import Any

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


def test_source_checkout_falls_back_to_authoritative_contracts() -> None:
    """Source mode reads top-level contracts when no packaged resource exists."""
    from semantic_reheating import validation

    validation._VALIDATOR_CACHE.clear()
    schema_path = resources.files("semantic_reheating").joinpath(
        validation.PUBLIC_CONTRACT_SCHEMAS["run_policy"]
    )
    assert not schema_path.is_file()
    fixture_data = json.loads(
        (
            PROJECT_ROOT
            / "tests"
            / "fixtures"
            / "contracts"
            / "minimal-run-policy.json"
        ).read_text()
    )
    assert (
        validation.validate_public_artifact("run_policy", fixture_data) == fixture_data
    )


def test_wheel_packages_authoritative_schemas_and_validates_outside_checkout(
    tmp_path: Path,
) -> None:
    """The installed wheel is self-contained and its schemas cannot drift."""
    wheel_dir = tmp_path / "wheels"
    wheel_dir.mkdir()
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(wheel_dir)],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    wheel = next(wheel_dir.glob("semantic_reheating-*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        from semantic_reheating.validation import PUBLIC_CONTRACT_SCHEMAS

        schema_entries = [
            path
            for path in archive.namelist()
            if path.startswith("semantic_reheating/contracts/")
        ]
        packaged_schema_paths = set(schema_entries)
        expected_schema_paths = {
            f"semantic_reheating/contracts/v1/{Path(schema_path).name}"
            for schema_path in PUBLIC_CONTRACT_SCHEMAS.values()
        } | {"semantic_reheating/contracts/v1/trace-event.schema.json"}
        assert packaged_schema_paths == expected_schema_paths
        assert len(schema_entries) == 7
        assert len(packaged_schema_paths) == 7
        for packaged_path in packaged_schema_paths:
            info = archive.getinfo(packaged_path)
            assert (info.external_attr >> 16) & 0o170000 != 0o120000
            authoritative_path = (
                PROJECT_ROOT / "contracts" / "v1" / Path(packaged_path).name
            )
            assert archive.read(packaged_path) == authoritative_path.read_bytes()

    target = tmp_path / "isolated-wheel"
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--no-deps",
            "--target",
            str(target),
            str(wheel),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    fixtures = {
        kind: json.loads(
            (PROJECT_ROOT / "tests" / "fixtures" / "contracts" / name).read_text()
        )
        for kind, name in ARTIFACTS.items()
    }
    model_fixtures = {
        "trace_event": json.loads(
            (
                PROJECT_ROOT
                / "tests"
                / "fixtures"
                / "contracts"
                / "minimal-trace-event.json"
            ).read_text()
        ),
        "run_policy": json.loads(
            (
                PROJECT_ROOT
                / "tests"
                / "fixtures"
                / "contracts"
                / "minimal-run-policy.json"
            ).read_text()
        ),
        "decision_envelope": json.loads(
            (
                PROJECT_ROOT
                / "tests"
                / "fixtures"
                / "contracts"
                / "minimal-decision-envelope.json"
            ).read_text()
        ),
    }
    script = """
import json
import os
import semantic_reheating.validation as validation
import semantic_reheating.models as models_module
from semantic_reheating.models import DecisionEnvelope, RunPolicy, TraceEvent
assert str(validation.__file__).startswith(os.environ["TARGET"]), validation.__file__
assert str(models_module.__file__).startswith(os.environ["TARGET"]), models_module.__file__
for kind, value in json.loads(os.environ["FIXTURES"]).items():
    validation.validate_public_artifact(kind, value)
models = json.loads(os.environ["MODELS"])
assert TraceEvent.from_dict(models["trace_event"]).to_dict() == models["trace_event"]
assert RunPolicy.from_dict(models["run_policy"]).to_dict() == models["run_policy"]
assert DecisionEnvelope.from_dict(models["decision_envelope"]).to_dict() == models["decision_envelope"]
"""
    environment = {
        **os.environ,
        "PYTHONPATH": str(target),
        "TARGET": str(target),
        "FIXTURES": json.dumps(fixtures),
        "MODELS": json.dumps(model_fixtures),
    }
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


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


def _unique_128_char_event_ids(count: int) -> list[str]:
    """Return deterministic, schema-valid unique public event identifiers."""
    return [f"event-{index:0122d}" for index in range(count)]


def _json_string_and_key_chars(value: Any) -> int:
    """Measure the direct-value character budget enforced by the public seam."""
    if type(value) is str:
        return len(value)
    if type(value) is list:
        return sum(_json_string_and_key_chars(item) for item in value)
    if type(value) is dict:
        return sum(
            len(key) + _json_string_and_key_chars(item) for key, item in value.items()
        )
    return 0


@pytest.mark.parametrize(
    ("kind", "fixture_name", "field"),
    [
        ("detector_finding", "minimal-detector-finding.json", "event_ids"),
        (
            "decision_envelope",
            "minimal-decision-envelope.json",
            "evidence_event_ids",
        ),
    ],
)
def test_event_id_schema_boundary_validates_via_direct_and_raw_public_apis(
    kind: str, fixture_name: str, field: str
) -> None:
    from semantic_reheating.validation import (
        MAX_JSON_AGGREGATE_CHARS,
        MAX_JSON_INPUT_BYTES,
        MAX_JSON_INPUT_CHARS,
        validate_public_artifact,
    )

    data = fixture(fixture_name)
    event_ids = _unique_128_char_event_ids(1_000)
    assert len(event_ids) == 1_000
    assert len(set(event_ids)) == 1_000
    assert all(
        len(event_id) == 128
        and event_id[0].isalnum()
        and all(character.isalnum() or character in "._:-" for character in event_id)
        for event_id in event_ids
    )
    data[field] = event_ids
    raw = json.dumps(data, separators=(",", ":"))
    assert len(raw) < MAX_JSON_INPUT_CHARS
    assert len(raw.encode("utf-8")) < MAX_JSON_INPUT_BYTES
    assert _json_string_and_key_chars(data) < MAX_JSON_AGGREGATE_CHARS
    assert validate_public_artifact(kind, data) == data
    assert validate_public_artifact(kind, raw) == data


@pytest.mark.parametrize(
    ("kind", "fixture_name", "field"),
    [
        ("detector_finding", "minimal-detector-finding.json", "event_ids"),
        (
            "decision_envelope",
            "minimal-decision-envelope.json",
            "evidence_event_ids",
        ),
    ],
)
def test_event_id_schema_limit_one_over_is_rejected_by_direct_and_raw_public_apis(
    kind: str, fixture_name: str, field: str
) -> None:
    from semantic_reheating.validation import (
        ContractValidationError,
        validate_public_artifact,
    )

    data = fixture(fixture_name)
    data[field] = _unique_128_char_event_ids(1_001)
    raw = json.dumps(data, separators=(",", ":"))
    for public_input in (data, raw):
        with pytest.raises(ContractValidationError) as caught:
            validate_public_artifact(kind, public_input)
        assert caught.value.code == "schema_validation_error"


# Keep the schema/fixture registry explicit: the public validator deliberately
# excludes trace_event, and these tests must not infer coverage from filenames.
ADVERSARIAL_ARTIFACTS = (
    ("run_policy", "contracts/v1/run-policy.schema.json", "minimal-run-policy.json"),
    (
        "detector_finding",
        "contracts/v1/detector-finding.schema.json",
        "minimal-detector-finding.json",
    ),
    (
        "decision_envelope",
        "contracts/v1/decision-envelope.schema.json",
        "minimal-decision-envelope.json",
    ),
    (
        "recovery_instruction",
        "contracts/v1/recovery-instruction.schema.json",
        "minimal-recovery-instruction.json",
    ),
    (
        "recovery_outcome",
        "contracts/v1/recovery-outcome.schema.json",
        "minimal-recovery-outcome.json",
    ),
    (
        "evidence_record",
        "contracts/v1/evidence-record.schema.json",
        "minimal-evidence-record.json",
    ),
)

PathSegment = str | int
RequiredCase = tuple[str, tuple[PathSegment, ...], str]
ValueCase = tuple[str, tuple[PathSegment, ...]]
ClosedObjectCase = tuple[str, tuple[PathSegment, ...]]


def _resolve_local_ref(
    schema: dict[str, Any], root_schema: dict[str, Any]
) -> dict[str, Any]:
    """Resolve a local JSON Pointer reference without silently ignoring it."""
    resolved = schema
    seen: set[str] = set()
    while "$ref" in resolved:
        reference = resolved["$ref"]
        if not isinstance(reference, str) or not reference.startswith("#/"):
            raise AssertionError(
                f"Unsupported non-local schema reference: {reference!r}"
            )
        if reference in seen:
            raise AssertionError(f"Cyclic local schema reference: {reference}")
        seen.add(reference)
        target: Any = root_schema
        for token in reference[2:].split("/"):
            token = token.replace("~1", "/").replace("~0", "~")
            if not isinstance(target, dict) or token not in target:
                raise AssertionError(
                    f"Unresolvable local schema reference: {reference}"
                )
            target = target[token]
        if not isinstance(target, dict):
            raise TypeError(f"Local schema reference is not an object: {reference}")
        siblings = {key: value for key, value in resolved.items() if key != "$ref"}
        resolved = target if not siblings else {"allOf": [target, siblings]}
    return resolved


def _matches_condition(
    condition: dict[str, Any], instance: Any, root_schema: dict[str, Any]
) -> bool:
    condition_with_defs = {"$defs": root_schema.get("$defs", {}), **condition}
    return Draft202012Validator(condition_with_defs).is_valid(instance)


def _effective_parts(
    schema: dict[str, Any], instance: Any, root_schema: dict[str, Any]
) -> list[dict[str, Any]]:
    """Return every conjunction branch applicable to this fixture instance."""
    schema = _resolve_local_ref(schema, root_schema)
    parts = [schema]
    all_of = schema.get("allOf", [])
    if not isinstance(all_of, list):
        raise TypeError("Schema allOf must be an array")
    for branch in all_of:
        if not isinstance(branch, dict):
            raise TypeError("Schema allOf branch must be an object")
        branch = _resolve_local_ref(branch, root_schema)
        if "if" in branch:
            condition = branch["if"]
            if not isinstance(condition, dict):
                raise AssertionError("Schema conditional must be an object")
            selected = (
                "then"
                if _matches_condition(condition, instance, root_schema)
                else "else"
            )
            selected_schema = branch.get(selected)
            if selected_schema is None:
                continue
            if not isinstance(selected_schema, dict):
                raise AssertionError("Schema conditional branch must be an object")
            parts.extend(_effective_parts(selected_schema, instance, root_schema))
        else:
            parts.extend(_effective_parts(branch, instance, root_schema))
    return parts


def _property_parts(
    parent_parts: list[dict[str, Any]],
    property_name: str,
    instance: Any,
    root_schema: dict[str, Any],
) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    for parent in parent_parts:
        properties = parent.get("properties", {})
        if not isinstance(properties, dict):
            raise TypeError("Schema properties must be an object")
        property_schema = properties.get(property_name)
        if property_schema is None:
            continue
        if not isinstance(property_schema, dict):
            raise TypeError("Property schema must be an object")
        parts.extend(_effective_parts(property_schema, instance, root_schema))
    return parts


def _item_parts(
    parent_parts: list[dict[str, Any]], instance: Any, root_schema: dict[str, Any]
) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    for parent in parent_parts:
        item_schema = parent.get("items")
        if item_schema is None:
            continue
        if not isinstance(item_schema, dict):
            raise TypeError("Array item schema must be an object")
        parts.extend(_effective_parts(item_schema, instance, root_schema))
    return parts


def _is_closed_object_part(part: dict[str, Any]) -> bool:
    """Return whether one effective schema part closes an object boundary."""
    schema_type = part.get("type")
    is_object = schema_type == "object" or (
        isinstance(schema_type, list) and "object" in schema_type
    )
    return (
        is_object
        and isinstance(part.get("properties"), dict)
        and part.get("additionalProperties") is False
    )


def _walk_populated_closed_objects(
    instance: Any,
    parts: list[dict[str, Any]],
    root_schema: dict[str, Any],
    path: tuple[PathSegment, ...] = (),
) -> Iterator[tuple[PathSegment, ...]]:
    """Yield every populated object boundary closed by its effective schema."""
    if isinstance(instance, dict):
        if any(_is_closed_object_part(part) for part in parts):
            yield path
        for field, value in instance.items():
            child_parts = _property_parts(parts, field, value, root_schema)
            if child_parts:
                yield from _walk_populated_closed_objects(
                    value, child_parts, root_schema, (*path, field)
                )
    elif isinstance(instance, list):
        for index, value in enumerate(instance):
            child_parts = _item_parts(parts, value, root_schema)
            if child_parts:
                yield from _walk_populated_closed_objects(
                    value, child_parts, root_schema, (*path, index)
                )


def _walk_populated_invariants(
    instance: Any,
    parts: list[dict[str, Any]],
    root_schema: dict[str, Any],
    path: tuple[PathSegment, ...] = (),
) -> Iterator[tuple[str, tuple[PathSegment, ...], str | None]]:
    if isinstance(instance, dict):
        required_fields = {
            field
            for part in parts
            for field in part.get("required", [])
            if isinstance(field, str) and field in instance
        }
        for field in sorted(required_fields):
            yield "required", path, field
        for field, value in instance.items():
            child_parts = _property_parts(parts, field, value, root_schema)
            if not child_parts:
                continue
            child_path = (*path, field)
            if any("enum" in part for part in child_parts):
                yield "enum", child_path, None
            if any("const" in part for part in child_parts):
                yield "const", child_path, None
            yield from _walk_populated_invariants(
                value, child_parts, root_schema, child_path
            )
    elif isinstance(instance, list):
        for index, value in enumerate(instance):
            child_parts = _item_parts(parts, value, root_schema)
            if not child_parts:
                continue
            child_path = (*path, index)
            if any("enum" in part for part in child_parts):
                yield "enum", child_path, None
            if any("const" in part for part in child_parts):
                yield "const", child_path, None
            yield from _walk_populated_invariants(
                value, child_parts, root_schema, child_path
            )


def _matrix_cases() -> tuple[list[RequiredCase], list[ValueCase], list[ValueCase]]:
    required: list[RequiredCase] = []
    enums: list[ValueCase] = []
    consts: list[ValueCase] = []
    for kind, schema_name, fixture_name in ADVERSARIAL_ARTIFACTS:
        schema = json.loads((PROJECT_ROOT / schema_name).read_text())
        data = fixture(fixture_name)
        for invariant, path, field in _walk_populated_invariants(
            data, _effective_parts(schema, data, schema), schema
        ):
            if invariant == "required":
                assert field is not None
                required.append((kind, path, field))
            elif invariant == "enum":
                enums.append((kind, path))
            elif invariant == "const":
                consts.append((kind, path))
            else:  # pragma: no cover - guarded by the fixed collector vocabulary.
                raise AssertionError(f"Unknown invariant type: {invariant}")
    return required, enums, consts


def _closed_object_matrix_cases() -> list[ClosedObjectCase]:
    """Collect all populated object paths whose effective schema is closed."""
    cases: list[ClosedObjectCase] = []
    for kind, schema_name, fixture_name in ADVERSARIAL_ARTIFACTS:
        schema = json.loads((PROJECT_ROOT / schema_name).read_text())
        data = fixture(fixture_name)
        cases.extend(
            (kind, path)
            for path in _walk_populated_closed_objects(
                data, _effective_parts(schema, data, schema), schema
            )
        )
    return cases


def _require_matrix_floor(name: str, cases: list[Any], minimum: int) -> None:
    if not cases:
        raise RuntimeError(f"{name} matrix collected zero cases")
    if len(cases) < minimum:
        raise RuntimeError(
            f"{name} matrix shrank to {len(cases)} cases; expected at least {minimum}"
        )


REQUIRED_MATRIX_CASES, ENUM_MATRIX_CASES, CONST_MATRIX_CASES = _matrix_cases()
CLOSED_OBJECT_MATRIX_CASES = _closed_object_matrix_cases()
_require_matrix_floor("required", REQUIRED_MATRIX_CASES, 186)
_require_matrix_floor("enum", ENUM_MATRIX_CASES, 27)
_require_matrix_floor("const", CONST_MATRIX_CASES, 13)

# This is an independently inventoried current baseline: 19 RunPolicy, 2
# DetectorFinding, 6 DecisionEnvelope, 7 RecoveryInstruction, 6
# RecoveryOutcome, and 4 EvidenceRecord object boundaries.
CLOSED_OBJECT_MATRIX_EXPECTED_COUNT = 44
CLOSED_OBJECT_MATRIX_NAMED_PATHS: frozenset[ClosedObjectCase] = frozenset(
    {
        ("run_policy", ("detectors",)),
        ("run_policy", ("detectors", "windows")),
        ("run_policy", ("detectors", "thresholds")),
        ("run_policy", ("detectors", "weights")),
        ("run_policy", ("detectors", "semantic_detector")),
        ("run_policy", ("agreeing_signals",)),
        ("run_policy", ("recovery_ladder",)),
        *(
            ("run_policy", ("recovery_ladder", stage))
            for stage in (
                "nudge",
                "diagnose",
                "reheat",
                "restart",
                "escalate",
                "stop",
            )
        ),
        ("run_policy", ("budgets",)),
        ("run_policy", ("budgets", "per_intervention")),
        ("run_policy", ("budgets", "whole_run")),
        ("run_policy", ("side_effect_rules",)),
        ("run_policy", ("cooling_conditions",)),
        ("recovery_instruction", ("variables", 0)),
        ("recovery_instruction", ("diagnosed_gaps", 0)),
        ("recovery_instruction", ("recovery_budget",)),
        ("recovery_instruction", ("expected_output",)),
        ("recovery_instruction", ("expected_output", "hypothesis_contract")),
        ("recovery_instruction", ("cooling_conditions",)),
        ("evidence_record", ("actual_counters",)),
        ("evidence_record", ("acceptance_delta",)),
        ("evidence_record", ("trigger",)),
    }
)


def _format_path(path: tuple[PathSegment, ...]) -> str:
    rendered = "$"
    for segment in path:
        rendered += f"[{segment}]" if isinstance(segment, int) else f".{segment}"
    return rendered


def _at_path(data: dict[str, Any], path: tuple[PathSegment, ...]) -> Any:
    current: Any = data
    for segment in path:
        current = current[segment]
    return current


def _enum_sentinel(value: Any, enums: list[Any]) -> Any:
    if isinstance(value, str) or value is None:
        candidate = "__contract_matrix_invalid_enum__"
        while candidate in enums:
            candidate += "_"
        return candidate
    if type(value) is bool:
        candidate = not value
        if candidate not in enums:
            return candidate
    if type(value) is int:
        candidate = value + 1
        while candidate in enums:
            candidate += 1
        return candidate
    if type(value) is float:
        candidate = value + 1.0
        while candidate in enums:
            candidate += 1.0
        return candidate
    raise AssertionError(f"No deterministic enum sentinel for {type(value)!r}")


def _const_sentinel(value: Any) -> Any:
    if isinstance(value, str):
        return f"{value}-invalid"
    if type(value) is list:
        return [*value, "__contract_matrix_invalid_const__"]
    if type(value) is bool:
        return not value
    if type(value) is int:
        return value + 1
    if type(value) is float:
        return value + 1.0
    raise AssertionError(f"No same-type distinct const sentinel for {type(value)!r}")


def _value_constraint(case: ValueCase, keyword: str) -> list[Any]:
    kind, path = case
    schema_name = next(
        schema_name
        for artifact_kind, schema_name, _ in ADVERSARIAL_ARTIFACTS
        if artifact_kind == kind
    )
    schema = json.loads((PROJECT_ROOT / schema_name).read_text())
    data = fixture(
        next(
            fixture_name
            for artifact_kind, _, fixture_name in ADVERSARIAL_ARTIFACTS
            if artifact_kind == kind
        )
    )
    parts = _effective_parts(schema, data, schema)
    current: Any = data
    for segment in path:
        if isinstance(segment, str):
            parts = _property_parts(parts, segment, current[segment], schema)
        else:
            parts = _item_parts(parts, current[segment], schema)
        current = current[segment]
    values = [part[keyword] for part in parts if keyword in part]
    if not values:
        raise AssertionError(
            f"{keyword} case lost its effective schema: {kind} {_format_path(path)}"
        )
    return values


def _required_case_id(case: RequiredCase) -> str:
    kind, path, field = case
    return f"{kind}:{_format_path(path)}:remove-{field}"


def _value_case_id(case: ValueCase, keyword: str) -> str:
    kind, path = case
    return f"{kind}:{_format_path(path)}:{keyword}"


def _closed_object_case_id(case: ClosedObjectCase) -> str:
    kind, path = case
    return f"{kind}:{_format_path(path)}:unexpected-private-field"


def test_closed_object_unknown_field_matrix_has_exact_unique_named_coverage() -> None:
    collected = set(CLOSED_OBJECT_MATRIX_CASES)
    assert CLOSED_OBJECT_MATRIX_EXPECTED_COUNT >= 38
    assert len(CLOSED_OBJECT_MATRIX_CASES) == len(collected), (
        "closed-object matrix contains duplicate artifact/path cases"
    )
    assert len(CLOSED_OBJECT_MATRIX_CASES) == CLOSED_OBJECT_MATRIX_EXPECTED_COUNT
    assert CLOSED_OBJECT_MATRIX_NAMED_PATHS <= collected, (
        "closed-object matrix omitted named paths: "
        f"{sorted(CLOSED_OBJECT_MATRIX_NAMED_PATHS - collected)!r}"
    )


@pytest.mark.parametrize("case", CLOSED_OBJECT_MATRIX_CASES, ids=_closed_object_case_id)
def test_every_populated_closed_contract_object_rejects_unknown_field(
    case: ClosedObjectCase,
) -> None:
    from semantic_reheating.validation import (
        ContractValidationError,
        validate_public_artifact,
    )

    kind, path = case
    fixture_name = next(
        fixture_name
        for artifact_kind, _, fixture_name in ADVERSARIAL_ARTIFACTS
        if artifact_kind == kind
    )
    invalid = deepcopy(fixture(fixture_name))
    target = _at_path(invalid, path)
    assert type(target) is dict
    assert "unexpected_private_field" not in target
    target["unexpected_private_field"] = True
    with pytest.raises(ContractValidationError) as caught:
        validate_public_artifact(kind, invalid)
    assert caught.value.code == "schema_validation_error"


@pytest.mark.parametrize("case", REQUIRED_MATRIX_CASES, ids=_required_case_id)
def test_every_populated_required_contract_field_is_rejected(
    case: RequiredCase,
) -> None:
    from semantic_reheating.validation import (
        ContractValidationError,
        validate_public_artifact,
    )

    kind, path, field = case
    fixture_name = next(
        fixture_name
        for artifact_kind, _, fixture_name in ADVERSARIAL_ARTIFACTS
        if artifact_kind == kind
    )
    invalid = deepcopy(fixture(fixture_name))
    del _at_path(invalid, path)[field]
    with pytest.raises(ContractValidationError) as caught:
        validate_public_artifact(kind, invalid)
    assert caught.value.code == "schema_validation_error"


@pytest.mark.parametrize(
    "case", ENUM_MATRIX_CASES, ids=lambda case: _value_case_id(case, "enum")
)
def test_every_populated_contract_enum_is_rejected(case: ValueCase) -> None:
    from semantic_reheating.validation import (
        ContractValidationError,
        validate_public_artifact,
    )

    kind, path = case
    fixture_name = next(
        fixture_name
        for artifact_kind, _, fixture_name in ADVERSARIAL_ARTIFACTS
        if artifact_kind == kind
    )
    invalid = deepcopy(fixture(fixture_name))
    enum_values = [
        value for values in _value_constraint(case, "enum") for value in values
    ]
    target = _at_path(invalid, path)
    parent = _at_path(invalid, path[:-1])
    parent[path[-1]] = _enum_sentinel(target, enum_values)
    with pytest.raises(ContractValidationError) as caught:
        validate_public_artifact(kind, invalid)
    assert caught.value.code == "schema_validation_error"


@pytest.mark.parametrize(
    "case", CONST_MATRIX_CASES, ids=lambda case: _value_case_id(case, "const")
)
def test_every_populated_contract_const_is_rejected(case: ValueCase) -> None:
    from semantic_reheating.validation import (
        ContractValidationError,
        validate_public_artifact,
    )

    kind, path = case
    fixture_name = next(
        fixture_name
        for artifact_kind, _, fixture_name in ADVERSARIAL_ARTIFACTS
        if artifact_kind == kind
    )
    invalid = deepcopy(fixture(fixture_name))
    target = _at_path(invalid, path)
    parent = _at_path(invalid, path[:-1])
    parent[path[-1]] = (
        "2.0" if path == ("contract_version",) else _const_sentinel(target)
    )
    expected_code = (
        "unknown_contract_major"
        if path == ("contract_version",)
        else "schema_validation_error"
    )
    with pytest.raises(ContractValidationError) as caught:
        validate_public_artifact(kind, invalid)
    assert caught.value.code == expected_code


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


def test_run_policy_rejects_conflicting_duplicate_recovery_stage() -> None:
    from semantic_reheating.validation import (
        ContractValidationError,
        validate_public_artifact,
    )

    invalid = fixture("minimal-run-policy.json")
    invalid["recovery_ladder"] = [
        {
            "stage": "nudge",
            "permitted": True,
            "requires_host_action": False,
        },
        {
            "stage": "nudge",
            "permitted": False,
            "requires_host_action": True,
        },
    ]
    with pytest.raises(ContractValidationError) as caught:
        validate_public_artifact("run_policy", invalid)
    assert caught.value.code == "schema_validation_error"


def test_run_policy_rejects_missing_recovery_stage() -> None:
    from semantic_reheating.validation import (
        ContractValidationError,
        validate_public_artifact,
    )

    invalid = fixture("minimal-run-policy.json")
    invalid["recovery_ladder"].pop("stop")  # type: ignore[index]
    with pytest.raises(ContractValidationError) as caught:
        validate_public_artifact("run_policy", invalid)
    assert caught.value.code == "schema_validation_error"


def test_run_policy_rejects_unknown_recovery_stage() -> None:
    from semantic_reheating.validation import (
        ContractValidationError,
        validate_public_artifact,
    )

    invalid = fixture("minimal-run-policy.json")
    invalid["recovery_ladder"]["override"] = {  # type: ignore[index]
        "permitted": True,
        "requires_host_action": False,
    }
    with pytest.raises(ContractValidationError) as caught:
        validate_public_artifact("run_policy", invalid)
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


class _HostileRawStr(str):
    def __len__(self) -> int:
        raise RuntimeError("__hostile-raw-str-len__")

    def __str__(self) -> str:
        raise RuntimeError("__hostile-raw-str-str__")


class _HostileRawBytes(bytes):
    def __len__(self) -> int:
        raise RuntimeError("__hostile-raw-bytes-len__")

    def decode(self, *args: object, **kwargs: object) -> str:
        raise RuntimeError("__hostile-raw-bytes-decode__")


class _HostileRawBytearray(bytearray):
    def __len__(self) -> int:
        raise RuntimeError("__hostile-raw-bytearray-len__")

    def __bytes__(self) -> bytes:
        raise RuntimeError("__hostile-raw-bytearray-bytes__")


_HOSTILE_CLASS_SENTINEL = "__hostile-class__"


class _HostileClassObject:
    @property
    def __class__(self) -> type[object]:
        raise RuntimeError(_HOSTILE_CLASS_SENTINEL)


class _HostileClassMapping(Mapping[str, object]):
    @property
    def __class__(self) -> type[object]:
        raise RuntimeError(_HOSTILE_CLASS_SENTINEL)

    def __getitem__(self, key: str) -> object:
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return iter(())

    def __len__(self) -> int:
        return 0


class _HostileClassSequence(Sequence[object]):
    @property
    def __class__(self) -> type[object]:
        raise RuntimeError(_HOSTILE_CLASS_SENTINEL)

    def __getitem__(self, index: int) -> object:
        raise IndexError(index)

    def __len__(self) -> int:
        return 0


class _DangerousDisplayObject:
    def __repr__(self) -> str:
        raise RuntimeError("__dangerous-repr__")

    def __str__(self) -> str:
        raise RuntimeError("__dangerous-str__")

    def __hash__(self) -> int:
        raise RuntimeError("__dangerous-hash__")


_HOSTILE_RAW_SENTINELS = (
    "__hostile-raw-str-len__",
    "__hostile-raw-str-str__",
    "__hostile-raw-bytes-len__",
    "__hostile-raw-bytes-decode__",
    "__hostile-raw-bytearray-len__",
    "__hostile-raw-bytearray-bytes__",
)


@pytest.mark.parametrize(
    "source",
    [
        _HostileRawStr('{"public":true}'),
        _HostileRawBytes(b'{"public":true}'),
        _HostileRawBytearray(b'{"public":true}'),
    ],
    ids=["str-subclass", "bytes-subclass", "bytearray-subclass"],
)
@pytest.mark.parametrize("api", ["loader", "artifact"])
def test_hostile_raw_input_subclasses_are_rejected_before_methods_run(
    source: str | bytes | bytearray, api: str
) -> None:
    from semantic_reheating.validation import (
        ContractValidationError,
        load_public_json,
        validate_public_artifact,
    )

    with pytest.raises(ContractValidationError) as caught:
        if api == "loader":
            load_public_json(source)
        else:
            validate_public_artifact("detector_finding", source)
    assert caught.value.code == (
        "non_json_input" if api == "loader" else "non_json_data"
    )
    assert all(sentinel not in str(caught.value) for sentinel in _HOSTILE_RAW_SENTINELS)


@pytest.mark.parametrize(
    "case", ["object", "mapping", "sequence", "nested-dict", "nested-list"]
)
def test_hostile_class_introspection_is_rejected_with_sanitized_error(
    case: str,
) -> None:
    from semantic_reheating.validation import (
        ContractValidationError,
        validate_public_artifact,
    )

    hostile = _HostileClassObject()
    values: dict[str, object] = {
        "object": hostile,
        "mapping": _HostileClassMapping(),
        "sequence": _HostileClassSequence(),
        "nested-dict": {"nested": hostile},
        "nested-list": [hostile],
    }
    with pytest.raises(ContractValidationError) as caught:
        validate_public_artifact("detector_finding", values[case])
    assert caught.value.code == "non_json_data"
    assert str(caught.value) == "Only JSON-compatible values are accepted"
    assert _HOSTILE_CLASS_SENTINEL not in str(caught.value)


def test_normal_object_with_dangerous_display_methods_is_rejected_safely() -> None:
    from semantic_reheating.validation import (
        ContractValidationError,
        validate_public_artifact,
    )

    with pytest.raises(ContractValidationError) as caught:
        validate_public_artifact("detector_finding", _DangerousDisplayObject())
    assert caught.value.code == "non_json_data"
    assert str(caught.value) == "Only JSON-compatible values are accepted"


@pytest.mark.parametrize("source_kind", ["text", "bytes"])
def test_strict_json_loader_rejects_oversized_raw_text_and_bytes(
    source_kind: str,
) -> None:
    from semantic_reheating.validation import ContractValidationError, load_public_json

    limit = 1_048_576
    source = " " * limit + "0"
    if source_kind == "bytes":
        source = source.encode("utf-8")
    with pytest.raises(ContractValidationError) as caught:
        load_public_json(source)
    assert caught.value.code == "json_input_too_large"


def test_strict_json_loader_rejects_malformed_utf8_bytes() -> None:
    from semantic_reheating.validation import ContractValidationError, load_public_json

    with pytest.raises(ContractValidationError) as caught:
        load_public_json(b'{"public":"\xff"}')
    assert caught.value.code == "invalid_json_encoding"


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


def test_direct_json_string_just_over_limit_fails_closed() -> None:
    from semantic_reheating.validation import (
        ContractValidationError,
        validate_public_artifact,
    )

    with pytest.raises(ContractValidationError) as caught:
        validate_public_artifact("detector_finding", ["s" * 262_145])
    assert caught.value.code == "json_string_too_large"


def test_direct_json_key_just_over_limit_fails_closed() -> None:
    from semantic_reheating.validation import (
        ContractValidationError,
        validate_public_artifact,
    )

    key = "__oversized-json-key__" + "k" * 4_075
    with pytest.raises(ContractValidationError) as caught:
        validate_public_artifact("detector_finding", {key: None})
    assert caught.value.code == "json_key_too_large"
    assert key not in str(caught.value)


def test_direct_json_strings_over_aggregate_limit_fail_closed() -> None:
    from semantic_reheating.validation import (
        ContractValidationError,
        validate_public_artifact,
    )

    value = ["s" * 262_144] * 5
    with pytest.raises(ContractValidationError) as caught:
        validate_public_artifact("detector_finding", value)
    assert caught.value.code == "json_character_limit_exceeded"


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


@pytest.mark.parametrize(
    "hostile_kind",
    [
        type(
            "HostileDisplayKind",
            (str,),
            {"__str__": lambda self: "__hostile-kind-display__"},
        )("detector_finding"),
        type(
            "HostileHashKind",
            (str,),
            {
                "__hash__": lambda self: (_ for _ in ()).throw(
                    RuntimeError("__hostile-kind-hash__")
                )
            },
        )("detector_finding"),
    ],
)
def test_str_subclass_artifact_kinds_are_rejected_before_hostile_methods(
    hostile_kind: str,
) -> None:
    from semantic_reheating.validation import (
        ContractValidationError,
        validate_public_artifact,
    )

    with pytest.raises(ContractValidationError) as caught:
        validate_public_artifact(hostile_kind, fixture("minimal-detector-finding.json"))
    assert caught.value.code == "unknown_artifact_kind"
    assert "__hostile-kind-display__" not in str(caught.value)
    assert "__hostile-kind-hash__" not in str(caught.value)


@pytest.mark.parametrize("container", [{}, []])
def test_direct_cyclic_json_containers_fail_with_stable_typed_error(
    container: Any,
) -> None:
    from semantic_reheating.validation import (
        ContractValidationError,
        validate_public_artifact,
    )

    if type(container) is dict:
        container["cycle"] = container
    else:
        container.append(container)
    with pytest.raises(ContractValidationError) as caught:
        validate_public_artifact("detector_finding", container)
    assert caught.value.code == "json_cycle"


def test_direct_json_value_just_over_depth_limit_fails_closed() -> None:
    from semantic_reheating.validation import (
        MAX_JSON_DEPTH,
        ContractValidationError,
        validate_public_artifact,
    )

    value: object = None
    for _ in range(MAX_JSON_DEPTH + 1):
        value = [value]
    with pytest.raises(ContractValidationError) as caught:
        validate_public_artifact("detector_finding", value)
    assert caught.value.code == "json_depth_exceeded"


def test_direct_json_value_just_over_node_limit_fails_closed() -> None:
    from semantic_reheating.validation import (
        MAX_JSON_NODES,
        ContractValidationError,
        validate_public_artifact,
    )

    with pytest.raises(ContractValidationError) as caught:
        validate_public_artifact("detector_finding", [None] * MAX_JSON_NODES)
    assert caught.value.code == "json_node_limit_exceeded"


def test_schema_validation_diagnostics_do_not_echo_sensitive_instance_values() -> None:
    from semantic_reheating.validation import (
        ContractValidationError,
        validate_public_artifact,
    )

    sentinel = "__sensitive-contract-secret__"
    invalid = fixture("minimal-decision-envelope.json")
    invalid["decision"] = sentinel
    with pytest.raises(ContractValidationError) as caught:
        validate_public_artifact("decision_envelope", invalid)
    assert caught.value.code == "schema_validation_error"
    assert sentinel not in str(caught.value)


@pytest.mark.parametrize(
    ("source", "code", "secret"),
    [
        (
            '{"__sensitive-duplicate-key__": 1, "__sensitive-duplicate-key__": 2}',
            "duplicate_key",
            "__sensitive-duplicate-key__",
        ),
        ("not-used", "unknown_artifact_kind", "__sensitive-artifact-kind__"),
        ("not-used", "unknown_contract_major", "__sensitive-contract-major__"),
    ],
)
def test_public_validation_diagnostics_do_not_echo_caller_controlled_values(
    source: str, code: str, secret: str
) -> None:
    from semantic_reheating.validation import (
        ContractValidationError,
        load_public_json,
        validate_public_artifact,
    )

    with pytest.raises(ContractValidationError) as caught:
        if code == "duplicate_key":
            load_public_json(source)
        elif code == "unknown_artifact_kind":
            validate_public_artifact(secret, fixture("minimal-detector-finding.json"))
        else:
            invalid = fixture("minimal-detector-finding.json")
            invalid["contract_version"] = secret
            validate_public_artifact("detector_finding", invalid)
    assert caught.value.code == code
    assert secret not in str(caught.value)


def test_run_policy_escalation_stage_requires_host_action() -> None:
    from semantic_reheating.validation import (
        ContractValidationError,
        validate_public_artifact,
    )

    invalid = fixture("minimal-run-policy.json")
    invalid["recovery_ladder"]["escalate"]["requires_host_action"] = False  # type: ignore[index]
    with pytest.raises(ContractValidationError) as caught:
        validate_public_artifact("run_policy", invalid)
    assert caught.value.code == "schema_validation_error"


def test_recovery_capable_decision_cannot_relax_non_idempotent_repeat_safety() -> None:
    from semantic_reheating.validation import (
        ContractValidationError,
        validate_public_artifact,
    )

    invalid = fixture("minimal-decision-envelope.json")
    invalid["constraints"]["no_non_idempotent_repeat"] = False  # type: ignore[index]
    with pytest.raises(ContractValidationError) as caught:
        validate_public_artifact("decision_envelope", invalid)
    assert caught.value.code == "schema_validation_error"
