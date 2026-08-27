"""Typed model boundary behavior."""

from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _fixture(name: str) -> dict[str, object]:
    return json.loads((PROJECT_ROOT / "tests" / "fixtures" / "contracts" / name).read_text())


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
