"""Deterministic, trace-supported progress classification."""

from __future__ import annotations

import pytest


def _event(
    sequence: int,
    *,
    kind: str = "message",
    payload: object = None,
    **fields: object,
):
    from semantic_reheating.models import TraceEvent

    source: dict[str, object] = {
        "contract_version": "1.0",
        "run_id": "run-progress",
        "event_id": f"event-{sequence:03d}",
        "sequence": sequence,
        "kind": kind,
        "actor": "controller",
        "effect_class": "read_only",
        "payload": {} if payload is None else payload,
    }
    source.update(fields)
    return TraceEvent.from_dict(source)


def test_empty_window_returns_an_immutable_no_progress_assessment() -> None:
    from dataclasses import FrozenInstanceError

    import pytest

    from semantic_reheating.progress import ProgressAssessment, classify_progress

    assessment = classify_progress(())

    assert assessment == ProgressAssessment(False, (), ())
    assert assessment.made_progress is False
    assert assessment.reason_codes == ()
    assert assessment.supporting_event_ids == ()
    with pytest.raises(FrozenInstanceError):
        assessment.made_progress = True  # type: ignore[misc]


def test_pagination_and_batch_progress_require_a_changed_prior_observation() -> None:
    from semantic_reheating.progress import ProgressReason, classify_progress

    pagination = classify_progress(
        (
            _event(4, kind="tool_result", payload={"pagination_cursor": "cursor-a"}),
            _event(5, kind="tool_result", payload={"pagination_cursor": "cursor-b"}),
        )
    )
    batch = classify_progress(
        (
            _event(4, kind="state_observation", payload={"batch_item_id": "item-a"}),
            _event(5, kind="state_observation", payload={"batch_item_id": "item-b"}),
        )
    )

    assert pagination.reason_codes == (ProgressReason.PAGINATION_ADVANCED,)
    assert pagination.supporting_event_ids == ("event-005",)
    assert batch.reason_codes == (ProgressReason.BATCH_ITEM_CHANGED,)
    assert batch.supporting_event_ids == ("event-005",)
    for trace in (
        (_event(4, kind="tool_result", payload={"pagination_cursor": "cursor-a"}),),
        (
            _event(4, kind="tool_result", payload={"pagination_cursor": "cursor-a"}),
            _event(5, kind="tool_result", payload={"pagination_cursor": "cursor-a"}),
        ),
    ):
        assert classify_progress(trace).made_progress is False


def test_hypothesis_input_progress_requires_an_explicit_marker_and_new_fingerprint() -> None:
    from semantic_reheating.progress import ProgressReason, classify_progress

    assessment = classify_progress(
        (
            _event(
                4,
                kind="tool_call",
                payload={"hypothesis_id": "h-1", "hypothesis_test_input": {"query": "one"}},
            ),
            _event(
                5,
                kind="tool_call",
                payload={"hypothesis_id": "h-1", "hypothesis_test_input": {"query": "two"}},
            ),
        )
    )

    assert assessment.reason_codes == (ProgressReason.HYPOTHESIS_INPUT_CHANGED,)
    assert assessment.supporting_event_ids == ("event-005",)
    prose_only = (
        _event(4, payload={"message": "try one"}),
        _event(5, payload={"message": "try two"}),
    )
    unmarked_arguments = (
        _event(4, kind="tool_call", payload={"query": "one"}),
        _event(5, kind="tool_call", payload={"query": "two"}),
    )
    assert classify_progress(prose_only).made_progress is False
    assert classify_progress(unmarked_arguments).made_progress is False


@pytest.mark.parametrize(
    ("first", "second"),
    (
        ("alpha", "beta"),
        (None, True),
        (True, False),
        (1, 2),
        (1.25, 2.5),
        (["one"], ["two"]),
        ({"query": "one"}, {"query": "two"}),
    ),
)
def test_hypothesis_input_progress_distinguishes_json_value_shapes(
    first: object, second: object
) -> None:
    from semantic_reheating.progress import ProgressReason, classify_progress

    assessment = classify_progress(
        (
            _event(4, kind="tool_call", payload={"hypothesis_id": "h", "hypothesis_test_input": first}),
            _event(5, kind="tool_call", payload={"hypothesis_id": "h", "hypothesis_test_input": second}),
        )
    )

    assert assessment.reason_codes == (ProgressReason.HYPOTHESIS_INPUT_CHANGED,)
    assert assessment.supporting_event_ids == ("event-005",)


def test_hypothesis_input_fingerprinting_is_canonical_and_excludes_nested_volatile_fields() -> None:
    from semantic_reheating.progress import classify_progress

    same_semantic_object = classify_progress(
        (
            _event(
                4,
                kind="tool_call",
                payload={"hypothesis_id": "h", "hypothesis_test_input": {"a": 1, "b": 2}},
            ),
            _event(
                5,
                kind="tool_call",
                payload={"hypothesis_id": "h", "hypothesis_test_input": {"b": 2, "a": 1}},
            ),
        )
    )
    volatile_only = classify_progress(
        (
            _event(
                4,
                kind="tool_call",
                payload={
                    "hypothesis_id": "h",
                    "hypothesis_test_input": {"query": "same", "event_id": "one", "timestamp": "old", "request_id": "a"},
                },
            ),
            _event(
                5,
                kind="tool_call",
                payload={
                    "hypothesis_id": "h",
                    "hypothesis_test_input": {"query": "same", "event_id": "two", "timestamp": "new", "request_id": "b"},
                },
            ),
        )
    )

    assert same_semantic_object.made_progress is False
    assert volatile_only.made_progress is False


def test_error_fingerprint_changes_and_new_stack_frames_are_progress() -> None:
    from semantic_reheating.progress import ProgressReason, classify_progress

    errors = classify_progress(
        (
            _event(4, kind="error", error_fingerprint="error-a"),
            _event(5, kind="error", error_fingerprint="error-b"),
        )
    )
    frames = classify_progress(
        (
            _event(4, kind="error", payload={"stack_frames": ["frame-a"]}),
            _event(5, kind="error", payload={"stack_frames": ["frame-a", "frame-b"]}),
        )
    )

    assert errors.reason_codes == (ProgressReason.ERROR_CHANGED,)
    assert errors.supporting_event_ids == ("event-005",)
    assert frames.reason_codes == (ProgressReason.STACK_FRAME_ADDED,)
    assert frames.supporting_event_ids == ("event-005",)
    assert classify_progress((_event(4, kind="error", error_fingerprint="error-a"),)).made_progress is False
    assert classify_progress(
        (
            _event(4, kind="error", error_fingerprint="error-a"),
            _event(5, kind="error", error_fingerprint="error-a"),
        )
    ).made_progress is False


def test_evidence_and_eliminated_hypotheses_require_a_prior_window_baseline() -> None:
    from semantic_reheating.progress import ProgressReason, classify_progress

    evidence = classify_progress(
        (
            _event(4),
            _event(5, evidence_refs=["evidence://one"]),
        )
    )
    elimination = classify_progress(
        (
            _event(4, kind="plan", payload={"eliminated_hypotheses": ["h-1"]}),
            _event(5, kind="plan", payload={"eliminated_hypotheses": ["h-1", "h-2"]}),
        )
    )

    assert evidence.reason_codes == (ProgressReason.EVIDENCE_ADDED,)
    assert evidence.supporting_event_ids == ("event-005",)
    assert elimination.reason_codes == (ProgressReason.HYPOTHESIS_ELIMINATED,)
    assert elimination.supporting_event_ids == ("event-005",)
    assert classify_progress((_event(4, evidence_refs=["evidence://one"]),)).made_progress is False
    assert classify_progress(
        (
            _event(4, kind="plan", payload={"eliminated_hypotheses": ["h-1"]}),
            _event(5, kind="plan", payload={"eliminated_hypotheses": ["h-1"]}),
        )
    ).made_progress is False


@pytest.mark.parametrize("kind", ("message", "tool_call", "state_observation"))
def test_eliminated_hypotheses_marker_is_ignored_outside_plan_events(kind: str) -> None:
    from semantic_reheating.progress import classify_progress

    assessment = classify_progress(
        (
            _event(4, kind=kind, payload={"eliminated_hypotheses": ["h-1"]}),
            _event(5, kind=kind, payload={"eliminated_hypotheses": ["h-1", "h-2"]}),
        )
    )

    assert assessment.made_progress is False


def test_acceptance_progress_requires_a_required_acceptance_check_rerun() -> None:
    from semantic_reheating.progress import ProgressReason, classify_progress

    verified = classify_progress(
        (
            _event(
                4,
                kind="acceptance_check",
                payload={"required_verification": True},
                acceptance_delta="criterion satisfied",
            ),
            _event(
                5,
                kind="acceptance_check",
                payload={"required_verification": True},
                acceptance_delta="criterion satisfied again",
            ),
        )
    )
    singleton_required = classify_progress(
        (
            _event(
                4,
                kind="acceptance_check",
                payload={"required_verification": True},
                acceptance_delta="criterion satisfied",
            ),
        )
    )
    unrelated_then_required = classify_progress(
        (
            _event(4),
            _event(
                5,
                kind="acceptance_check",
                payload={"required_verification": True},
                acceptance_delta="criterion satisfied",
            ),
        )
    )
    unrequired_then_required = classify_progress(
        (
            _event(
                4,
                kind="acceptance_check",
                payload={"required_verification": False},
                acceptance_delta="criterion satisfied",
            ),
            _event(
                5,
                kind="acceptance_check",
                payload={"required_verification": True},
                acceptance_delta="criterion satisfied",
            ),
        )
    )
    empty_rerun = classify_progress(
        (
            _event(
                4,
                kind="acceptance_check",
                payload={"required_verification": True},
                acceptance_delta="criterion satisfied",
            ),
            _event(
                5,
                kind="acceptance_check",
                payload={"required_verification": True},
                acceptance_delta="",
            ),
        )
    )

    assert verified.reason_codes == (ProgressReason.REQUIRED_ACCEPTANCE_VERIFIED,)
    assert verified.supporting_event_ids == ("event-005",)
    assert singleton_required.made_progress is False
    assert unrelated_then_required.made_progress is False
    assert unrequired_then_required.made_progress is False
    assert empty_rerun.made_progress is False


def test_productive_handoff_requires_a_new_plan_or_capability() -> None:
    from semantic_reheating.progress import ProgressReason, classify_progress

    plan = classify_progress((_event(4), _event(5, kind="handoff", payload={"new_plan_id": "plan-2"})))
    capability = classify_progress(
        (_event(4), _event(5, kind="handoff", payload={"new_capabilities": ["capability-2"]}))
    )
    plain = classify_progress((_event(4), _event(5, kind="handoff", payload={"message": "over to you"})))

    assert plan.reason_codes == (ProgressReason.PRODUCTIVE_HANDOFF,)
    assert plan.supporting_event_ids == ("event-005",)
    assert capability.reason_codes == (ProgressReason.PRODUCTIVE_HANDOFF,)
    assert plain.made_progress is False


def test_expected_state_change_requires_a_later_observed_fingerprint_change() -> None:
    from semantic_reheating.progress import ProgressReason, classify_progress

    observed = classify_progress(
        (
            _event(4, kind="tool_call", state_fingerprint="state-a", expected_state_change=True),
            _event(5, kind="state_observation", state_fingerprint="state-b"),
        )
    )
    never_appears = classify_progress(
        (
            _event(4, kind="tool_call", state_fingerprint="state-a", expected_state_change=True),
            _event(5, kind="state_observation", state_fingerprint="state-a"),
        )
    )

    assert observed.reason_codes == (ProgressReason.EXPECTED_STATE_CHANGE_OBSERVED,)
    assert observed.supporting_event_ids == ("event-005",)
    assert never_appears.made_progress is False


def test_expected_state_change_without_a_baseline_or_expectation_is_not_progress() -> None:
    from semantic_reheating.progress import classify_progress

    no_baseline = classify_progress(
        (
            _event(4, kind="tool_call", expected_state_change=True),
            _event(5, kind="state_observation", state_fingerprint="state-a"),
        )
    )
    no_expectation = classify_progress(
        (
            _event(4, kind="state_observation", state_fingerprint="state-a"),
            _event(5, kind="state_observation", state_fingerprint="state-b"),
        )
    )

    assert no_baseline.made_progress is False
    assert no_expectation.made_progress is False


def test_poll_convergence_requires_a_same_target_distance_decrease_per_poll() -> None:
    from semantic_reheating.progress import ProgressReason, classify_progress

    assessment = classify_progress(
        (
            _event(
                4,
                kind="state_observation",
                payload={"poll_id": "alpha", "poll_value": 100, "poll_target": 0},
            ),
            _event(
                5,
                kind="state_observation",
                payload={"poll_id": "bravo", "poll_value": 50.0, "poll_target": 0},
            ),
            _event(
                6,
                kind="state_observation",
                payload={"poll_id": "alpha", "poll_value": 4.0, "poll_target": 0},
            ),
        )
    )

    assert assessment.reason_codes == (ProgressReason.POLL_CONVERGING,)
    assert assessment.supporting_event_ids == ("event-006",)


def test_poll_baselines_are_isolated_by_exact_poll_id_and_target() -> None:
    from semantic_reheating.progress import ProgressReason, classify_progress

    interleaved = classify_progress(
        (
            _event(1, kind="state_observation", payload={"poll_id": "p", "poll_value": 10, "poll_target": 0}),
            _event(2, kind="state_observation", payload={"poll_id": "p", "poll_value": 100, "poll_target": 100}),
            _event(3, kind="state_observation", payload={"poll_id": "p", "poll_value": 9, "poll_target": 0}),
        )
    )
    independent_baselines = classify_progress(
        (
            _event(1, kind="state_observation", payload={"poll_id": "p", "poll_value": 10, "poll_target": 0}),
            _event(2, kind="state_observation", payload={"poll_id": "p", "poll_value": 100, "poll_target": 100}),
        )
    )
    original_target_uses_its_own_last_distance = classify_progress(
        (
            _event(1, kind="state_observation", payload={"poll_id": "p", "poll_value": 10, "poll_target": 0}),
            _event(2, kind="state_observation", payload={"poll_id": "p", "poll_value": 100, "poll_target": 100}),
            _event(3, kind="state_observation", payload={"poll_id": "p", "poll_value": 11, "poll_target": 0}),
        )
    )
    second_target_uses_its_own_baseline = classify_progress(
        (
            _event(1, kind="state_observation", payload={"poll_id": "p", "poll_value": 10, "poll_target": 0}),
            _event(2, kind="state_observation", payload={"poll_id": "p", "poll_value": 90, "poll_target": 100}),
            _event(3, kind="state_observation", payload={"poll_id": "p", "poll_value": 95, "poll_target": 100}),
        )
    )
    cross_target_only = classify_progress(
        (
            _event(1, kind="state_observation", payload={"poll_id": "p", "poll_value": 10, "poll_target": 0}),
            _event(2, kind="state_observation", payload={"poll_id": "p", "poll_value": 99, "poll_target": 100}),
        )
    )

    assert interleaved.reason_codes == (ProgressReason.POLL_CONVERGING,)
    assert interleaved.supporting_event_ids == ("event-003",)
    assert independent_baselines.made_progress is False
    assert original_target_uses_its_own_last_distance.made_progress is False
    assert second_target_uses_its_own_baseline.reason_codes == (ProgressReason.POLL_CONVERGING,)
    assert second_target_uses_its_own_baseline.supporting_event_ids == ("event-003",)
    assert cross_target_only.made_progress is False


def test_poll_convergence_compares_unbounded_integer_distances_exactly() -> None:
    from semantic_reheating.progress import ProgressReason, classify_progress

    converging = classify_progress(
        (
            _event(
                4,
                kind="state_observation",
                payload={"poll_id": "p", "poll_value": 10**1000 + 1, "poll_target": 0},
            ),
            _event(
                5,
                kind="state_observation",
                payload={"poll_id": "p", "poll_value": 10**1000, "poll_target": 0},
            ),
        )
    )
    diverging = classify_progress(
        (
            _event(
                4,
                kind="state_observation",
                payload={"poll_id": "p", "poll_value": 10**1000, "poll_target": 0},
            ),
            _event(
                5,
                kind="state_observation",
                payload={"poll_id": "p", "poll_value": 10**1000 + 1, "poll_target": 0},
            ),
        )
    )

    assert converging.reason_codes == (ProgressReason.POLL_CONVERGING,)
    assert converging.supporting_event_ids == ("event-005",)
    assert diverging.made_progress is False


def test_poll_controls_and_non_poll_payload_kinds_are_not_progress() -> None:
    from semantic_reheating.progress import classify_progress

    for trace in (
        (
            _event(4, kind="state_observation", payload={"poll_id": "p", "poll_value": 8, "poll_target": 0}),
        ),
        (
            _event(4, kind="state_observation", payload={"poll_id": "p", "poll_value": 8, "poll_target": 0}),
            _event(5, kind="state_observation", payload={"poll_id": "p", "poll_value": 8, "poll_target": 0}),
        ),
        (
            _event(4, kind="state_observation", payload={"poll_id": "p", "poll_value": 4, "poll_target": 0}),
            _event(5, kind="state_observation", payload={"poll_id": "p", "poll_value": 8, "poll_target": 0}),
        ),
        (
            _event(4, kind="state_observation", payload={"poll_id": "p", "poll_value": 4, "poll_target": 0}),
            _event(5, kind="state_observation", payload={"poll_id": "p", "poll_value": 3, "poll_target": 2}),
        ),
        (
            _event(4, kind="state_observation", payload={"poll_id": "p", "poll_value": True, "poll_target": 0}),
            _event(5, kind="state_observation", payload={"poll_id": "p", "poll_value": 0, "poll_target": 0}),
        ),
        (
            _event(4, kind="message", payload={"pagination_cursor": "a", "batch_item_id": "a"}),
            _event(5, kind="plan", payload={"pagination_cursor": "b", "batch_item_id": "b"}),
        ),
    ):
        assert classify_progress(trace).made_progress is False


def test_progress_boundary_accepts_only_exact_contiguous_trace_events() -> None:
    import pytest

    from semantic_reheating.models import TraceEvent
    from semantic_reheating.progress import (
        ProgressClassificationError,
        classify_progress,
    )

    class TraceList(list[TraceEvent]):
        pass

    class TraceEventChild(TraceEvent):
        pass

    class PretendsToBeAList:
        @property
        def __class__(self) -> type[list[object]]:
            return list

        def __repr__(self) -> str:
            raise AssertionError("repr must not run")

    traces: tuple[object, ...] = (
        TraceList([_event(4)]),
        [{"not": "an event"}],
        (object.__new__(TraceEventChild),),
        PretendsToBeAList(),
        (_event(4), _event(6)),
        (_event(4), _event(5, run_id="other-run")),
    )
    for trace in traces:
        with pytest.raises(ProgressClassificationError) as raised:
            classify_progress(trace)
        assert raised.value.code in {"invalid_trace_window", "invalid_trace_event", "sequence_gap", "run_id_mismatch"}
        assert raised.value.__cause__ is None
        assert raised.value.__context__ is None


def test_forged_trace_source_is_revalidated_without_exception_graph_leakage() -> None:
    from collections.abc import Mapping
    from types import MappingProxyType

    import pytest

    from semantic_reheating.progress import (
        ProgressClassificationError,
        classify_progress,
    )

    class Sentinel(Exception):
        pass

    class HostileSource(Mapping[str, object]):
        def __iter__(self):
            raise Sentinel("SOURCE_SENTINEL")

        def __len__(self) -> int:
            return 1

        def __getitem__(self, key: str) -> object:
            raise Sentinel("SOURCE_SENTINEL")

    event = _event(4)
    object.__setattr__(event, "_source", MappingProxyType(HostileSource()))
    with pytest.raises(ProgressClassificationError) as raised:
        classify_progress((event,))

    error = raised.value
    assert error.code == "invalid_model_state"
    assert error.__cause__ is None
    assert error.__context__ is None
    assert "SOURCE_SENTINEL" not in str(error)
    assert "SOURCE_SENTINEL" not in repr(error)
    assert all("SOURCE_SENTINEL" not in repr(argument) for argument in error.args)
    assert all("SOURCE_SENTINEL" not in repr(value) for value in vars(error).values())


def test_forged_poll_or_utf8_source_is_rejected_as_a_typed_error() -> None:
    from types import MappingProxyType

    import pytest

    from semantic_reheating.progress import (
        ProgressClassificationError,
        classify_progress,
    )

    for payload in (
        {"poll_id": "p", "poll_value": float("inf"), "poll_target": 0},
        b"\xff",
    ):
        event = _event(4)
        source = event.to_dict()
        source["payload"] = payload
        object.__setattr__(event, "_source", MappingProxyType(source))
        with pytest.raises(ProgressClassificationError) as raised:
            classify_progress((event,))
        assert raised.value.__cause__ is None
        assert raised.value.__context__ is None


def test_assessment_contract_revalidates_and_serializes_fresh_plain_data() -> None:
    import pytest

    from semantic_reheating.progress import (
        ProgressAssessment,
        ProgressClassificationError,
        ProgressReason,
    )

    invalid_states = (
        (True, (), ()),
        (False, (ProgressReason.EVIDENCE_ADDED,), ("event-005",)),
        (True, (ProgressReason.EVIDENCE_ADDED,), ()),
        (True, (ProgressReason.EVIDENCE_ADDED, ProgressReason.EVIDENCE_ADDED), ("event-005",)),
        (True, (ProgressReason.EVIDENCE_ADDED,), ("event-005", "event-005")),
        (1, (), ()),
        (False, [ProgressReason.EVIDENCE_ADDED], ()),
        (False, (), ["event-005"]),
        (True, ("evidence_added",), ("event-005",)),
    )
    for state in invalid_states:
        with pytest.raises(ProgressClassificationError, match="Invalid progress classification input") as raised:
            ProgressAssessment(*state)  # type: ignore[arg-type]
        assert raised.value.code == "invalid_assessment_state"

    assessment = ProgressAssessment(True, (ProgressReason.EVIDENCE_ADDED,), ("event-005",))
    exported = assessment.to_dict()
    assert exported == {
        "made_progress": True,
        "reason_codes": ["evidence_added"],
        "supporting_event_ids": ["event-005"],
    }
    exported["reason_codes"].append("tampered")
    assert assessment.to_dict()["reason_codes"] == ["evidence_added"]
    assert "evidence_added" in repr(assessment)
    assert "event-005" in repr(assessment)
    object.__setattr__(assessment, "supporting_event_ids", [])
    with pytest.raises(ProgressClassificationError) as raised:
        assessment.to_dict()
    assert raised.value.code == "invalid_assessment_state"

    class ForgedAssessment(ProgressAssessment):
        def _validate_state(self) -> None:
            pass

    forged = object.__new__(ForgedAssessment)
    object.__setattr__(forged, "made_progress", True)
    object.__setattr__(forged, "reason_codes", (ProgressReason.EVIDENCE_ADDED,))
    object.__setattr__(forged, "supporting_event_ids", ("event-005",))
    with pytest.raises(ProgressClassificationError) as raised:
        forged.to_dict()
    assert raised.value.code == "invalid_assessment_state"


def test_one_event_can_support_multiple_reasons_with_one_supporting_id() -> None:
    from semantic_reheating.progress import ProgressReason, classify_progress

    assessment = classify_progress(
        (
            _event(4),
            _event(
                5,
                kind="acceptance_check",
                payload={"required_verification": True},
                acceptance_delta="criterion satisfied",
                evidence_refs=["evidence://one"],
            ),
        )
    )

    assert assessment.reason_codes == (ProgressReason.EVIDENCE_ADDED,)
    assert assessment.supporting_event_ids == ("event-005",)


def test_hypothesis_canonicalization_failure_is_not_progress() -> None:
    from semantic_reheating.progress import classify_progress

    assessment = classify_progress(
        (
            _event(
                4,
                kind="tool_call",
                payload={"hypothesis_id": "h", "hypothesis_test_input": {"value": 1}},
            ),
            _event(
                5,
                kind="tool_call",
                payload={"hypothesis_id": "h", "hypothesis_test_input": {"value": 2**80}},
            ),
        )
    )

    assert assessment.made_progress is False


def test_progress_reason_inventory_is_the_closed_eleven_value_contract() -> None:
    from semantic_reheating.progress import ProgressReason

    assert {reason.value for reason in ProgressReason} == {
        "pagination_advanced",
        "batch_item_changed",
        "hypothesis_input_changed",
        "error_changed",
        "stack_frame_added",
        "evidence_added",
        "hypothesis_eliminated",
        "required_acceptance_verified",
        "productive_handoff",
        "expected_state_change_observed",
        "poll_converging",
    }


def test_mixed_huge_int_and_float_poll_values_converge_without_overflow() -> None:
    from semantic_reheating.progress import ProgressReason, classify_progress

    assessment = classify_progress(
        (
            _event(
                4,
                kind="state_observation",
                payload={"poll_id": "p", "poll_value": 10**1000, "poll_target": 0},
            ),
            _event(
                5,
                kind="state_observation",
                payload={"poll_id": "p", "poll_value": 1.0e300, "poll_target": 0.0},
            ),
        )
    )

    assert assessment.reason_codes == (ProgressReason.POLL_CONVERGING,)
