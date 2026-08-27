"""Typed model boundary behavior."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from copy import deepcopy
from pathlib import Path
from typing import Any, Never

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _fixture(name: str) -> dict[str, object]:
    return json.loads((PROJECT_ROOT / "tests" / "fixtures" / "contracts" / name).read_text())


def _parse_trace_event(data: Any) -> object:
    from semantic_reheating.models import TraceEvent

    return TraceEvent.from_dict(data)


def _parse_run_policy(data: Any) -> object:
    from semantic_reheating.models import RunPolicy

    return RunPolicy.from_dict(data)


def _parse_decision_envelope(data: Any) -> object:
    from semantic_reheating.models import DecisionEnvelope

    return DecisionEnvelope.from_dict(data)


_MODEL_PARITY_REGISTRY: tuple[tuple[str, str, str, Callable[[Any], object]], ...] = (
    ("TraceEvent", "trace-event.schema.json", "minimal-trace-event.json", _parse_trace_event),
    ("RunPolicy", "run-policy.schema.json", "minimal-run-policy.json", _parse_run_policy),
    (
        "DecisionEnvelope",
        "decision-envelope.schema.json",
        "minimal-decision-envelope.json",
        _parse_decision_envelope,
    ),
)

_JSON_PATH = tuple[str | int, ...]
_PARITY_CATEGORIES = ("required", "unknown", "enum_const", "wrong_type")
_PARITY_NAMED_PATH_GUARDS = {
    "required": {
        "TraceEvent": "$.run_id",
        "RunPolicy": "$.detectors.windows.repetition_events",
        "DecisionEnvelope": "$.confidence.contributing_findings[0].finding_id",
    },
    "unknown": {
        "TraceEvent": "$",
        "RunPolicy": "$.recovery_ladder.escalate",
        "DecisionEnvelope": "$.confidence.contributing_findings[0]",
    },
    "enum_const": {
        "TraceEvent": "$.contract_version",
        "RunPolicy": "$.agreeing_signals.required_classes[0]",
        "DecisionEnvelope": "$.decision",
    },
    "wrong_type": {
        "TraceEvent": "$",
        "RunPolicy": "$.detectors.windows.repetition_events",
        "DecisionEnvelope": "$.confidence.contributing_findings",
    },
}


def _schema(name: str) -> dict[str, Any]:
    return json.loads((PROJECT_ROOT / "contracts" / "v1" / name).read_text())


def _json_path(path: _JSON_PATH) -> str:
    result = "$"
    for segment in path:
        result += f"[{segment}]" if isinstance(segment, int) else f".{segment}"
    return result


def _json_pointer(root: dict[str, Any], reference: str) -> dict[str, Any]:
    assert reference.startswith("#/")
    target: Any = root
    for segment in reference[2:].split("/"):
        target = target[segment.replace("~1", "/").replace("~0", "~")]
    assert type(target) is dict
    return target


def _merge_schema(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in overlay.items():
        if key == "required":
            result[key] = list(dict.fromkeys([*result.get(key, []), *value]))
        elif key == "properties" and type(result.get(key)) is dict and type(value) is dict:
            result[key] = {
                name: _merge_schema(result[key].get(name, {}), child)
                for name, child in (result[key] | value).items()
            }
        else:
            result[key] = value
    return result


def _effective_schema(
    schema: dict[str, Any], root: dict[str, Any], value: Any
) -> dict[str, Any]:
    """Resolve local refs, allOf, and fixture-applicable conditionals only."""
    if "$ref" in schema:
        schema = _merge_schema(_json_pointer(root, schema["$ref"]), schema)
    result = {
        key: item
        for key, item in schema.items()
        if key not in {"$ref", "allOf", "if", "then", "else"}
    }
    for component in schema.get("allOf", []):
        result = _merge_schema(result, _effective_schema(component, root, value))
    if "if" in schema:
        from jsonschema import Draft202012Validator

        branch = "then" if Draft202012Validator(schema["if"]).is_valid(value) else "else"
        if branch in schema:
            result = _merge_schema(result, _effective_schema(schema[branch], root, value))
    return result


def _populated_nodes(
    value: Any, schema: dict[str, Any], root: dict[str, Any], path: _JSON_PATH = ()
) -> Iterator[tuple[_JSON_PATH, Any, dict[str, Any]]]:
    effective = _effective_schema(schema, root, value)
    yield path, value, effective
    if type(value) is dict:
        properties = effective.get("properties", {})
        for name, child in value.items():
            yield from _populated_nodes(child, properties.get(name, {}), root, (*path, name))
    elif type(value) is list:
        item_schema = effective.get("items", {})
        for index, child in enumerate(value):
            yield from _populated_nodes(child, item_schema, root, (*path, index))


def _at_path(value: Any, path: _JSON_PATH) -> Any:
    for segment in path:
        value = value[segment]
    return value


def _replace_at_path(source: Any, path: _JSON_PATH, replacement: Any) -> Any:
    result = deepcopy(source)
    if not path:
        return replacement
    _at_path(result, path[:-1])[path[-1]] = replacement
    return result


def _schema_json_type(value: Any) -> str:
    if value is None:
        return "null"
    if type(value) is bool:
        return "boolean"
    if type(value) is int:
        return "integer"
    if type(value) is float:
        return "number"
    if type(value) is str:
        return "string"
    if type(value) is list:
        return "array"
    assert type(value) is dict
    return "object"


def _wrong_json_type(schema: dict[str, Any], value: Any) -> tuple[bool, Any]:
    declared = schema.get("type")
    if type(declared) is str:
        allowed = {declared}
    elif type(declared) is list:
        allowed = set(declared)
    elif "enum" in schema:
        allowed = {_schema_json_type(item) for item in schema["enum"]}
    elif "const" in schema:
        allowed = {_schema_json_type(schema["const"])}
    else:
        return False, None
    if "number" in allowed:
        allowed.add("integer")
    for candidate in (None, False, 0, 0.5, "__wrong_json_type__", [], {}):
        if _schema_json_type(candidate) not in allowed:
            return True, candidate
    return False, None


def _invalid_enum_or_const(value: Any, path: _JSON_PATH) -> Any:
    if path == ("contract_version",):
        return "2.0"
    if type(value) is str:
        return "__invalid_enum_or_const__"
    if type(value) is bool:
        return not value
    if type(value) is int:
        return value + 1_000_000
    if type(value) is float:
        return value + 1_000_000.0
    return "__invalid_enum_or_const__"


def _parity_cases(
    model_name: str, schema_name: str, fixture_name: str, category: str
) -> list[tuple[str, Any, str]]:
    source = _fixture(fixture_name)
    root = _schema(schema_name)
    nodes = list(_populated_nodes(source, root, root))
    cases: list[tuple[str, Any, str]] = []
    if category == "required":
        for path, value, schema in nodes:
            if type(value) is dict:
                for name in schema.get("required", []):
                    if name in value:
                        cases.append(
                            (
                                f"{model_name}|required|{_json_path((*path, name))}",
                                _replace_at_path(source, path, {**value, name: None}),
                                "schema_validation_error",
                            )
                        )
                        del _at_path(cases[-1][1], path)[name]
    elif category == "unknown":
        for path, value, schema in nodes:
            if type(value) is dict and schema.get("additionalProperties") is False:
                unknown = "__unknown_field__"
                assert unknown not in value
                cases.append(
                    (
                        f"{model_name}|unknown|{_json_path(path)}",
                        _replace_at_path(source, path, {**value, unknown: True}),
                        "schema_validation_error",
                    )
                )
    elif category == "enum_const":
        for path, value, schema in nodes:
            if "enum" in schema or "const" in schema:
                expected = "unknown_contract_major" if path == ("contract_version",) else "schema_validation_error"
                cases.append(
                    (
                        f"{model_name}|enum_const|{_json_path(path)}",
                        _replace_at_path(source, path, _invalid_enum_or_const(value, path)),
                        expected,
                    )
                )
    elif category == "wrong_type":
        for path, value, schema in nodes:
            available, replacement = _wrong_json_type(schema, value)
            if available:
                cases.append(
                    (
                        f"{model_name}|wrong_type|{_json_path(path)}",
                        _replace_at_path(source, path, replacement),
                        "schema_validation_error",
                    )
                )
    else:
        raise AssertionError(f"Unknown parity category: {category}")
    identifiers = [identifier for identifier, _, _ in cases]
    assert identifiers and len(identifiers) == len(set(identifiers))
    assert f"{model_name}|{category}|{_PARITY_NAMED_PATH_GUARDS[category][model_name]}" in identifiers
    return cases


def test_public_enums_are_closed_string_enums() -> None:
    from semantic_reheating.models import Decision, EffectClass, FindingClass, TraceKind

    assert tuple(member.value for member in TraceKind) == (
        "message",
        "plan",
        "tool_call",
        "tool_result",
        "state_observation",
        "acceptance_check",
        "handoff",
        "error",
        "budget",
    )
    assert tuple(member.value for member in EffectClass) == (
        "read_only",
        "idempotent_write",
        "non_idempotent_write",
        "unknown",
    )
    assert tuple(member.value for member in FindingClass) == (
        "repetition",
        "no_progress",
        "risk",
        "budget",
    )
    assert tuple(member.value for member in Decision) == (
        "continue",
        "nudge",
        "diagnose",
        "reheat",
        "restart",
        "escalate",
        "stop",
    )


def test_trace_event_minimal_fixture_has_typed_fields_and_exact_roundtrip() -> None:
    from semantic_reheating.models import EffectClass, TraceEvent, TraceKind

    source = _fixture("minimal-trace-event.json")
    model = TraceEvent.from_dict(source)
    assert model.kind is TraceKind.MESSAGE
    assert model.effect_class is EffectClass.READ_ONLY
    assert model.sequence == 1
    assert model.to_dict() == source


def test_budget_counters_have_only_the_five_public_dimensions() -> None:
    from dataclasses import fields

    from semantic_reheating.models import BudgetCounters

    assert tuple(item.name for item in fields(BudgetCounters)) == (
        "turns",
        "tool_calls",
        "tokens",
        "elapsed_seconds",
        "cost",
    )


def test_trace_event_is_deeply_immutable_and_to_dict_is_fresh() -> None:
    import pytest

    from semantic_reheating.models import TraceEvent

    source = _fixture("minimal-trace-event.json")
    model = TraceEvent.from_dict(source)
    source["payload"]["message"] = "mutated"  # type: ignore[index]
    with pytest.raises(TypeError):
        model.payload["message"] = "nope"  # type: ignore[index]
    first = model.to_dict()
    first["payload"]["message"] = "changed"  # type: ignore[index]
    assert model.to_dict()["payload"]["message"] == "public example"  # type: ignore[index]


def test_trace_event_rejects_direct_unvalidated_construction() -> None:
    import pytest

    from semantic_reheating.models import (
        EffectClass,
        ModelValidationError,
        TraceEvent,
        TraceKind,
    )

    with pytest.raises(ModelValidationError) as caught:
        TraceEvent(
            contract_version="1.0",
            run_id="run-001",
            event_id="event-001",
            sequence=1,
            kind=TraceKind.MESSAGE,
            actor="controller",
            effect_class=EffectClass.READ_ONLY,
            payload={"mutable": True},
        )

    assert caught.value.code == "validated_construction_required"


def test_core_models_reject_replace_alterations_without_validated_construction() -> None:
    from dataclasses import replace

    import pytest

    from semantic_reheating.models import (
        DecisionEnvelope,
        ModelValidationError,
        RunPolicy,
        TraceEvent,
    )

    for model, changes in (
        (TraceEvent.from_dict(_fixture("minimal-trace-event.json")), {"actor": "altered"}),
        (RunPolicy.from_dict(_fixture("minimal-run-policy.json")), {"policy_id": "altered"}),
        (
            DecisionEnvelope.from_dict(_fixture("minimal-decision-envelope.json")),
            {"human_summary": "altered"},
        ),
    ):
        with pytest.raises(ModelValidationError) as caught:
            replace(model, **changes)  # type: ignore[arg-type]
        assert caught.value.code == "validated_construction_required"


def test_parse_trace_does_not_return_a_forged_exact_model() -> None:
    from dataclasses import fields
    from types import MappingProxyType

    from semantic_reheating import models
    from semantic_reheating.models import TraceEvent, parse_trace

    assert not hasattr(models, "_VALIDATED_MODEL_CONSTRUCTION")
    assert "_validation_token" not in {item.name for item in fields(TraceEvent)}

    source = _fixture("minimal-trace-event.json")
    forged = object.__new__(TraceEvent)
    object.__setattr__(forged, "actor", "forged-actor")
    object.__setattr__(forged, "_source", MappingProxyType(source))

    parsed = parse_trace([forged])

    assert parsed[0] is not forged
    assert parsed[0].actor == source["actor"]


def test_trace_event_repr_excludes_raw_payload_but_keeps_public_identifiers() -> None:
    from semantic_reheating.models import TraceEvent

    sentinel = "__trace-payload-secret__"
    source = _fixture("minimal-trace-event.json")
    source["payload"] = {"secret": sentinel}

    model = TraceEvent.from_dict(source)

    assert sentinel not in repr(model)
    assert model.event_id in repr(model)


def test_trace_event_schema_errors_are_typed_and_sanitized() -> None:
    import pytest

    from semantic_reheating.models import ModelValidationError, TraceEvent

    for mutate, code in (
        (lambda value: value.update({"private": "__secret__"}), "schema_validation_error"),
        (lambda value: value.update({"kind": "__secret__"}), "schema_validation_error"),
        (lambda value: value.update({"contract_version": "2.0"}), "unknown_contract_major"),
    ):
        source = _fixture("minimal-trace-event.json")
        mutate(source)
        with pytest.raises(ModelValidationError) as caught:
            TraceEvent.from_dict(source)
        assert caught.value.code == code
        assert "__secret__" not in str(caught.value)


def test_trace_event_preserves_optional_field_absence() -> None:
    from semantic_reheating.models import TraceEvent

    source = _fixture("minimal-trace-event.json")
    source.pop("payload")
    source["payload_ref"] = "payload://public"
    model = TraceEvent.from_dict(source)
    assert model.payload is None
    assert model.payload_ref == "payload://public"
    assert model.to_dict() == source


def test_parse_trace_returns_tuple_and_rejects_sequence_gaps_and_run_mismatch() -> None:
    import pytest

    from semantic_reheating.models import ModelValidationError, parse_trace

    first = _fixture("minimal-trace-event.json")
    second = _fixture("minimal-trace-event.json")
    second.update({"event_id": "event-002", "sequence": 2})
    assert isinstance(parse_trace([first, second]), tuple)
    duplicate = {**second, "sequence": 1}
    for invalid in (
        [second],
        [first, {**second, "sequence": 3}],
        [second, first],
        [first, duplicate],
    ):
        with pytest.raises(ModelValidationError) as caught:
            parse_trace(invalid)
        assert caught.value.code == "sequence_gap"
    with pytest.raises(ModelValidationError) as caught:
        parse_trace([first, {**second, "run_id": "other-run"}])
    assert caught.value.code == "run_id_mismatch"


def test_parse_trace_rejects_hostile_class_attribute_without_leaking_it() -> None:
    import pytest

    from semantic_reheating.models import ModelValidationError, parse_trace

    sentinel = "__hostile-class-secret__"

    class HostileEvent:
        @property
        def __class__(self) -> type[object]:
            raise RuntimeError(sentinel)

    with pytest.raises(ModelValidationError) as caught:
        parse_trace([HostileEvent()])

    assert caught.value.code == "non_json_data"
    assert sentinel not in str(caught.value)


def test_core_model_constructors_reject_every_public_argument_shape() -> None:
    import pytest

    from semantic_reheating.models import (
        DecisionEnvelope,
        ModelValidationError,
        RunPolicy,
        TraceEvent,
    )

    for model_class in (TraceEvent, RunPolicy, DecisionEnvelope):
        for arguments, keywords in (((), {}), ((object(),), {}), ((), {"_validation_token": object()})):
            with pytest.raises(ModelValidationError) as caught:
                model_class(*arguments, **keywords)
            assert caught.value.code == "validated_construction_required"


def test_core_model_to_dict_rejects_non_internal_source_state() -> None:
    import pytest

    from semantic_reheating.models import (
        DecisionEnvelope,
        ModelValidationError,
        RunPolicy,
        TraceEvent,
    )

    for model_class in (TraceEvent, RunPolicy, DecisionEnvelope):
        forged: Any = object.__new__(model_class)
        sources: tuple[Any, ...] = (None, {})
        for source in sources:
            object.__setattr__(forged, "_source", source)
            with pytest.raises(ModelValidationError) as caught:
                forged.to_dict()
            assert caught.value.code == "invalid_model_state"


def test_parse_trace_sanitizes_forged_missing_wrong_and_hostile_sources() -> None:
    from types import MappingProxyType

    import pytest

    from semantic_reheating.models import ModelValidationError, TraceEvent, parse_trace

    sentinel = "__hostile-model-source__"

    class HostileDict(dict[str, object]):
        def items(self) -> Never:
            raise RuntimeError(sentinel)

    forged_missing = object.__new__(TraceEvent)
    forged_wrong = object.__new__(TraceEvent)
    object.__setattr__(forged_wrong, "_source", {})
    forged_hostile = object.__new__(TraceEvent)
    object.__setattr__(forged_hostile, "_source", MappingProxyType(HostileDict()))

    for forged in (forged_missing, forged_wrong, forged_hostile):
        with pytest.raises(ModelValidationError) as caught:
            parse_trace([forged])
        assert caught.value.code == "invalid_model_state"
        assert sentinel not in str(caught.value)


def test_parse_trace_revalidates_source_instead_of_tampered_typed_fields() -> None:
    from types import MappingProxyType

    import pytest

    from semantic_reheating.models import ModelValidationError, TraceEvent, parse_trace

    source = _fixture("minimal-trace-event.json")
    model = TraceEvent.from_dict(source)
    object.__setattr__(model, "actor", "tampered-actor")

    parsed = parse_trace([model])

    assert parsed[0] is not model
    assert parsed[0].actor == source["actor"]

    object.__setattr__(model, "_source", MappingProxyType({"actor": "invalid"}))
    with pytest.raises(ModelValidationError) as caught:
        parse_trace([model])
    assert caught.value.code == "schema_validation_error"


def test_run_policy_minimal_fixture_is_frozen_typed_and_exact() -> None:
    import pytest

    from semantic_reheating.models import EffectClass, RunPolicy

    source = _fixture("minimal-run-policy.json")
    model = RunPolicy.from_dict(source)
    assert model.detectors.windows.repetition_events == 3
    assert model.detectors.semantic_detector is not None
    assert model.budgets.whole_run.tokens == 500
    assert model.side_effect_rules.automatic_repeat_allowed_effect_classes == (
        EffectClass.READ_ONLY,
        EffectClass.IDEMPOTENT_WRITE,
    )
    with pytest.raises(AttributeError):
        model.budgets.whole_run.tokens = 0
    source["detectors"]["windows"]["repetition_events"] = 99  # type: ignore[index]
    assert model.to_dict()["detectors"]["windows"]["repetition_events"] == 3  # type: ignore[index]
    assert model.to_dict() == _fixture("minimal-run-policy.json")


def test_run_policy_schema_rejections_remain_typed() -> None:
    import pytest

    from semantic_reheating.models import ModelValidationError, RunPolicy

    for path, value, code in (
        (("unexpected",), True, "schema_validation_error"),
        (("detectors", "windows", "repetition_events"), "wrong", "schema_validation_error"),
        (("contract_version",), "2.0", "unknown_contract_major"),
    ):
        source = _fixture("minimal-run-policy.json")
        target: dict[str, object] = source
        for segment in path[:-1]:
            target = target[segment]  # type: ignore[assignment,index]
        target[path[-1]] = value
        with pytest.raises(ModelValidationError) as caught:
            RunPolicy.from_dict(source)
        assert caught.value.code == code


def test_decision_envelope_escalation_fixture_is_frozen_typed_and_exact() -> None:
    import pytest

    from semantic_reheating.models import Decision, DecisionEnvelope, FindingClass

    source = _fixture("minimal-decision-envelope.json")
    model = DecisionEnvelope.from_dict(source)
    assert model.decision is Decision.ESCALATE
    assert model.confidence.contributing_findings[0].finding_class is FindingClass.REPETITION
    assert model.constraints.allowed_effect_classes[0].value == "read_only"
    with pytest.raises(AttributeError):
        model.confidence.score = 0
    source["confidence"]["contributing_findings"][0]["score"] = 0  # type: ignore[index]
    assert model.to_dict()["confidence"]["contributing_findings"][0]["score"] == 0.9  # type: ignore[index]
    assert model.to_dict() == _fixture("minimal-decision-envelope.json")


def test_decision_envelope_schema_rejections_remain_typed() -> None:
    import pytest

    from semantic_reheating.models import DecisionEnvelope, ModelValidationError

    for path, value, code in (
        (("private",), "__secret__", "schema_validation_error"),
        (("constraints", "allowed_effect_classes"), ["unknown"], "schema_validation_error"),
        (("contract_version",), "2.0", "unknown_contract_major"),
    ):
        source = _fixture("minimal-decision-envelope.json")
        target: dict[str, object] = source
        for segment in path[:-1]:
            target = target[segment]  # type: ignore[assignment,index]
        target[path[-1]] = value
        with pytest.raises(ModelValidationError) as caught:
            DecisionEnvelope.from_dict(source)
        assert caught.value.code == code
        assert "__secret__" not in str(caught.value)


@pytest.mark.parametrize("category", _PARITY_CATEGORIES, ids=_PARITY_CATEGORIES)
def test_schema_runtime_parity_matrix(category: str) -> None:
    """Schema-first model parsers reject every deterministic invalid mutation."""
    from semantic_reheating.models import ModelValidationError

    for model_name, schema_name, fixture_name, parser in _MODEL_PARITY_REGISTRY:
        cases = _parity_cases(model_name, schema_name, fixture_name, category)
        assert len(cases) >= 1
        for identifier, mutation, expected_code in cases:
            with pytest.raises(ModelValidationError) as caught:
                parser(mutation)
            assert caught.value.code == expected_code, identifier


def test_model_parsers_reject_hostile_raw_inputs_without_native_leaks() -> None:
    from semantic_reheating.models import ModelValidationError

    sentinel = "__model-hostile-input__"

    class HostileClass:
        @property
        def __class__(self) -> type[object]:
            raise RuntimeError(sentinel)

    class RawDict(dict[str, object]):
        def items(self) -> Never:
            raise RuntimeError(sentinel)

    inputs = (object(), HostileClass(), RawDict())
    for model_name, _, _, parser in _MODEL_PARITY_REGISTRY:
        for hostile in inputs:
            with pytest.raises(ModelValidationError) as caught:
                parser(hostile)
            assert type(caught.value) is ModelValidationError, model_name
            assert caught.value.code == "non_json_data", model_name
            assert sentinel not in str(caught.value), model_name
