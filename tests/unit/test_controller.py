"""Public controller aggregation contract tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _fixture(name: str) -> dict[str, Any]:
    return json.loads(
        (PROJECT_ROOT / "tests" / "fixtures" / "contracts" / name).read_text()
    )


def _policy(**changes: Any) -> Any:
    from semantic_reheating.models import RunPolicy

    source = _fixture("minimal-run-policy.json")
    source["detectors"]["windows"] = {"repetition_events": 10, "no_progress_events": 10}
    source["recovery_ladder"]["nudge"]["permitted"] = False
    source["recovery_ladder"]["diagnose"]["permitted"] = False
    for path, value in changes.items():
        target: Any = source
        components = path.split("__")
        for component in components[:-1]:
            target = target[component]
        target[components[-1]] = value
    return RunPolicy.from_dict(source)


def _event(
    sequence: int,
    kind: str,
    *,
    event_id: str | None = None,
    effect_class: str = "read_only",
    payload: Any = None,
    **fields: Any,
) -> Any:
    from semantic_reheating.models import TraceEvent

    source: dict[str, Any] = {
        "contract_version": "1.0",
        "run_id": "run-controller",
        "event_id": event_id or f"event-{sequence}",
        "sequence": sequence,
        "kind": kind,
        "actor": "agent",
        "effect_class": effect_class,
        "payload": {"index": sequence} if payload is None else payload,
        **fields,
    }
    return TraceEvent.from_dict(source)


def _repetition_and_stall_trace() -> list[Any]:
    return [
        _event(1, "tool_call", payload={"action": "read"}),
        _event(2, "tool_result", payload={"result": "same"}, parent_event_id="event-1"),
        _event(
            3, "acceptance_check", payload={"check": "done"}, acceptance_delta="none"
        ),
        _event(4, "tool_call", payload={"action": "read"}),
        _event(5, "tool_result", payload={"result": "same"}, parent_event_id="event-4"),
        _event(
            6, "acceptance_check", payload={"check": "done"}, acceptance_delta="none"
        ),
    ]


def test_decision_envelope_preserves_raw_weight_and_weighted_scores() -> None:
    from semantic_reheating.models import DecisionEnvelope

    source = _fixture("minimal-decision-envelope.json")
    contributing = source["confidence"]["contributing_findings"][0]
    contributing["weight"] = 0.5
    contributing["weighted_score"] = 0.45

    model = DecisionEnvelope.from_dict(source)

    finding = model.confidence.contributing_findings[0]
    assert finding.score == 0.9
    assert finding.weight == 0.5
    assert finding.weighted_score == 0.45
    assert model.to_dict() == source


@pytest.mark.parametrize(
    "mutation",
    (
        lambda source: source["confidence"]["contributing_findings"][0].pop("weight"),
        lambda source: source["confidence"]["contributing_findings"][0].pop(
            "weighted_score"
        ),
        lambda source: source["confidence"]["contributing_findings"][0].__setitem__(
            "weight", 1.1
        ),
        lambda source: source["confidence"]["contributing_findings"][0].__setitem__(
            "weighted_score", -0.1
        ),
        lambda source: source["confidence"]["contributing_findings"][0].__setitem__(
            "private", True
        ),
    ),
)
def test_contributing_finding_weight_contract_fails_closed(mutation: Any) -> None:
    from semantic_reheating.models import DecisionEnvelope, ModelValidationError

    source = _fixture("minimal-decision-envelope.json")
    contributing = source["confidence"]["contributing_findings"][0]
    contributing["weight"] = 0.5
    contributing["weighted_score"] = 0.45
    mutation(source)

    with pytest.raises(ModelValidationError) as caught:
        DecisionEnvelope.from_dict(source)
    assert caught.value.code == "schema_validation_error"


def test_analyze_aggregates_repetition_and_no_progress_with_weighted_confidence() -> (
    None
):
    from semantic_reheating.controller import analyze
    from semantic_reheating.models import Decision

    envelope = analyze(_repetition_and_stall_trace(), _policy())

    assert envelope.decision is Decision.REHEAT
    assert envelope.reason_codes == (
        "signals_agree",
        "repetition_detected",
        "no_progress_detected",
    )
    assert envelope.evidence_event_ids == (
        "event-1",
        "event-2",
        "event-4",
        "event-5",
        "event-3",
        "event-6",
    )
    assert envelope.confidence.score == 0.4
    assert [
        (item.finding_class.value, item.score, item.weight, item.weighted_score)
        for item in envelope.confidence.contributing_findings
    ] == [
        ("repetition", 1.0, 0.4, 0.4),
        ("no_progress", 1.0, 0.4, 0.4),
    ]
    assert (
        envelope.recovery_budget.to_dict()
        == _policy().budgets.per_intervention.to_dict()
    )
    assert envelope.constraints.must_preserve_evidence is True
    assert envelope.constraints.no_non_idempotent_repeat is True
    assert [item.value for item in envelope.constraints.allowed_effect_classes] == [
        "read_only",
        "idempotent_write",
    ]


def test_analyze_repetition_without_no_progress_is_continue_with_zero_confidence() -> (
    None
):
    from semantic_reheating.controller import analyze
    from semantic_reheating.models import Decision

    trace = [
        _event(1, "tool_call", payload={"action": "read"}),
        _event(2, "tool_result", payload={"result": "same"}, parent_event_id="event-1"),
        _event(3, "tool_call", payload={"action": "read"}),
        _event(4, "tool_result", payload={"result": "same"}, parent_event_id="event-3"),
    ]
    envelope = analyze(trace, _policy())

    assert envelope.decision is Decision.CONTINUE
    assert envelope.reason_codes == ()
    assert envelope.confidence.score == 0.0
    assert [
        (item.finding_class.value, item.weighted_score)
        for item in envelope.confidence.contributing_findings
    ] == [("repetition", 0.4)]


def test_analyze_hard_budget_stop_dominates_recovery_and_records_exact_event() -> None:
    from semantic_reheating.controller import analyze
    from semantic_reheating.models import Decision

    trace = _repetition_and_stall_trace() + [
        _event(
            7,
            "budget",
            budget_counters={
                "turns": 5,
                "tool_calls": 0,
                "tokens": 0,
                "elapsed_seconds": 0,
                "cost": 0,
            },
        )
    ]
    envelope = analyze(trace, _policy())

    assert envelope.decision is Decision.STOP
    assert envelope.reason_codes[-1] == "budget_limit_reached"
    assert envelope.confidence.score == 1.0
    budget = envelope.confidence.contributing_findings[-1]
    assert (
        budget.finding_class.value,
        budget.score,
        budget.weight,
        budget.weighted_score,
    ) == (
        "budget",
        1.0,
        0.1,
        0.1,
    )
    assert envelope.evidence_event_ids[0] == "event-7"


@pytest.mark.parametrize("effect_class", ("non_idempotent_write", "unknown"))
def test_analyze_repeated_risky_call_stops_without_result(effect_class: str) -> None:
    from semantic_reheating.controller import analyze
    from semantic_reheating.models import Decision

    trace = [
        _event(1, "tool_call", effect_class=effect_class, payload={"action": "write"}),
        _event(2, "tool_call", effect_class=effect_class, payload={"action": "write"}),
    ]
    envelope = analyze(trace, _policy())

    assert envelope.decision is Decision.STOP
    assert envelope.reason_codes == ("risk_detected",)
    assert envelope.evidence_event_ids == ("event-1", "event-2")
    assert envelope.confidence.score == 1.0
    risk = envelope.confidence.contributing_findings[0]
    assert (risk.finding_class.value, risk.score, risk.weight, risk.weighted_score) == (
        "risk",
        1.0,
        0.1,
        0.1,
    )


def test_analyze_ignores_distinct_or_safe_tool_calls() -> None:
    from semantic_reheating.controller import analyze
    from semantic_reheating.models import Decision

    distinct = analyze(
        [
            _event(1, "tool_call", effect_class="unknown", payload={"action": "one"}),
            _event(2, "tool_call", effect_class="unknown", payload={"action": "two"}),
        ],
        _policy(),
    )
    safe = analyze(
        [
            _event(
                1,
                "tool_call",
                effect_class="idempotent_write",
                payload={"action": "write"},
            ),
            _event(
                2,
                "tool_call",
                effect_class="idempotent_write",
                payload={"action": "write"},
            ),
        ],
        _policy(),
    )

    assert distinct.decision is Decision.CONTINUE
    assert safe.decision is Decision.CONTINUE


def test_analyze_is_canonical_deterministic_and_semantic_seam_is_fail_closed() -> None:
    from semantic_reheating.controller import ControllerError, analyze

    trace = _repetition_and_stall_trace()
    policy = _policy()
    first = analyze(trace, policy)
    second = analyze(trace, policy)
    assert (
        json.dumps(first.to_dict(), separators=(",", ":"), sort_keys=True).encode()
        == json.dumps(second.to_dict(), separators=(",", ":"), sort_keys=True).encode()
    )
    assert first.decision_id.startswith("decision-")
    assert len(first.decision_id) == len("decision-") + 24
    from hashlib import sha256

    from semantic_reheating.canonical import canonicalize_json

    basis = first.to_dict()
    del basis["decision_id"]
    assert (
        first.decision_id
        == f"decision-{sha256(canonicalize_json(basis)).hexdigest()[:24]}"
    )

    optional = _policy(detectors__semantic_detector__enabled=True)
    degraded = analyze(trace, optional)
    assert degraded.detector_notices[0].status == "unavailable"
    assert "Semantic detector" not in degraded.human_summary
    required = _policy(
        detectors__semantic_detector__enabled=True,
        detectors__semantic_detector__required=True,
    )
    with pytest.raises(ControllerError) as caught:
        analyze(trace, required)
    assert caught.value.code == "required_detector_unavailable"

    class DisabledDetector:
        calls = 0

        def detect(self, *_: Any) -> dict[str, Any]:
            self.calls += 1
            raise AssertionError("disabled detector must not run")

    disabled_detector = DisabledDetector()
    assert (
        analyze(trace, policy, semantic_detector=disabled_detector).to_dict()
        == first.to_dict()
    )
    assert disabled_detector.calls == 0


def test_envelope_carries_portable_redacted_recovery_context() -> None:
    """A serialized envelope retains the only public recovery provenance."""
    from semantic_reheating.controller import analyze
    from semantic_reheating.models import DecisionEnvelope

    trace = _repetition_and_stall_trace()
    trace[2] = _event(
        3,
        "plan",
        payload={
            "diagnostic_cause": "missing_knowledge",
            "eliminated_hypotheses": ["password=not-public"],
        },
    )
    envelope = analyze(trace, _policy(detectors__semantic_detector__enabled=False))
    restored = DecisionEnvelope.from_dict(json.loads(json.dumps(envelope.to_dict())))

    assert [(gap.kind, gap.description) for gap in restored.diagnosed_gaps] == [
        ("missing_evidence", "Required evidence is unavailable."),
    ]
    assert len(restored.rejected_hypothesis_refs) == 1
    assert "password=not-public" not in repr(restored)
    assert "password=not-public" not in json.dumps(restored.to_dict())
    assert restored.detector_notices == ()


def test_semantic_detector_is_injected_once_and_separately_metered() -> None:
    from semantic_reheating.controller import analyze

    class Detector:
        calls = 0

        def detect(self, trace: tuple[Any, ...], policy: Any) -> dict[str, Any]:
            self.calls += 1
            return {
                "contract_version": "1.0",
                "run_id": trace[0].run_id,
                "finding_id": "semantic-finding",
                "detector_name": "semantic",
                "detector_version": "1.0",
                "matched": True,
                "score": 0.5,
                "finding_class": "repetition",
                "event_ids": [trace[0].event_id],
                "reason_code": "repetition_detected",
                "explanation": "Redacted.",
                "availability": {"status": "available", "notice": "Available."},
            }

    detector = Detector()
    envelope = analyze(
        _repetition_and_stall_trace(),
        _policy(
            detectors__semantic_detector__enabled=True,
            detectors__semantic_detector__weight=0.2,
        ),
        semantic_detector=detector,
    )

    assert detector.calls == 1
    assert envelope.confidence.contributing_findings[-1].weight == 0.2
    assert envelope.detector_notices == ()


def test_optional_semantic_degradation_is_fixed_and_structured() -> None:
    from semantic_reheating.controller import analyze

    class DegradedDetector:
        def detect(self, trace: tuple[Any, ...], _: Any) -> dict[str, Any]:
            return {
                "contract_version": "1.0",
                "run_id": trace[0].run_id,
                "finding_id": "semantic-degraded",
                "detector_name": "semantic",
                "detector_version": "1.0",
                "matched": False,
                "score": 0,
                "finding_class": "repetition",
                "event_ids": [trace[0].event_id],
                "reason_code": "detector_degraded",
                "explanation": "SECRET-MUST-NOT-LEAK",
                "availability": {
                    "status": "degraded",
                    "notice": "SECRET-MUST-NOT-LEAK",
                },
            }

    envelope = analyze(
        _repetition_and_stall_trace(),
        _policy(detectors__semantic_detector__enabled=True),
        semantic_detector=DegradedDetector(),
    )

    assert envelope.detector_notices[0].status == "degraded"
    assert envelope.detector_notices[0].notice == (
        "Semantic detector degraded; deterministic analysis continued."
    )
    assert "SECRET-MUST-NOT-LEAK" not in repr(envelope)


def test_build_recovery_instruction_is_portable_and_advisory() -> None:
    from semantic_reheating.controller import (
        ControllerError,
        analyze,
        build_recovery_instruction,
    )

    decision = analyze(
        _repetition_and_stall_trace(),
        _policy(
            recovery_ladder__nudge__permitted=False,
            recovery_ladder__diagnose__permitted=False,
        ),
    )
    instruction = build_recovery_instruction(
        type(decision).from_dict(json.loads(json.dumps(decision.to_dict())))
    )

    assert instruction is not None
    assert instruction.to_dict()["selected_prompt_asset_id"] == "prompt-reheat-v1"
    assert instruction.to_dict()["advisory_only"] is True
    assert instruction.to_dict()["grants_authority"] is False

    object.__setattr__(decision, "run_id", "tampered-decision-run")
    with pytest.raises(ControllerError) as caught:
        build_recovery_instruction(decision)
    assert caught.value.code == "invalid_recovery_decision"


def test_shared_gap_basis_preserves_task10_cause_provenance() -> None:
    """Task11's shared basis must not collapse distinct diagnosed causes."""
    from semantic_reheating.diagnosis import CauseClass
    from semantic_reheating.policies import recovery_gaps_for_causes

    assert recovery_gaps_for_causes(
        (CauseClass.UNSUITABLE_TOOL, CauseClass.RUNTIME_DEFECT)
    ) == [
        {
            "kind": "stalled_progress",
            "description": "Progress is stalled pending a safe reassessment.",
        },
        {
            "kind": "stalled_progress",
            "description": "Progress is stalled pending a safe reassessment.",
        },
    ]


def test_portable_instruction_matches_task10_for_equivalent_context() -> None:
    from semantic_reheating.controller import analyze, build_recovery_instruction
    from semantic_reheating.detectors.acceptance_stall import detect_acceptance_stall
    from semantic_reheating.detectors.budget_burn import detect_budget_burn
    from semantic_reheating.detectors.cycle import detect_cycle
    from semantic_reheating.detectors.exact_repetition import detect_exact_repetition
    from semantic_reheating.detectors.repeated_error import detect_repeated_error
    from semantic_reheating.detectors.unchanged_state import detect_unchanged_state
    from semantic_reheating.diagnosis import diagnose
    from semantic_reheating.policies import (
        construct_recovery_instruction,
        select_recovery_policy,
    )

    policy = _policy(
        recovery_ladder__nudge__permitted=False,
        recovery_ladder__diagnose__permitted=False,
    )
    trace = _repetition_and_stall_trace()
    findings = [
        detector(tuple(trace), policy)
        for detector in (
            detect_exact_repetition,
            detect_cycle,
            detect_repeated_error,
            detect_unchanged_state,
            detect_acceptance_stall,
            detect_budget_burn,
        )
    ]
    diagnosis = diagnose(tuple(trace), findings)
    task10 = construct_recovery_instruction(
        select_recovery_policy(diagnosis, findings, policy), diagnosis, findings, policy
    )
    task11 = build_recovery_instruction(analyze(trace, policy))

    assert task10 is not None and task11 is not None
    assert task11.to_dict() == task10


def test_cooling_conditions_and_independent_episode_supports_are_portable() -> None:
    from semantic_reheating.controller import analyze, build_recovery_instruction
    from semantic_reheating.models import EffectClass
    from semantic_reheating.policies import BranchCandidate, select_cooling_branch

    policy = _policy(
        recovery_ladder__nudge__permitted=False,
        recovery_ladder__diagnose__permitted=False,
    )
    base_trace = _repetition_and_stall_trace()
    base = analyze(base_trace, policy)
    replay = analyze(base_trace, policy)
    instruction = build_recovery_instruction(base)
    extended = analyze(
        base_trace
        + [
            _event(7, "tool_call", payload={"action": "new-read"}),
            _event(
                8,
                "tool_result",
                payload={"result": "new-same"},
                parent_event_id="event-7",
            ),
            _event(
                9, "acceptance_check", payload={"check": "new"}, acceptance_delta="none"
            ),
            _event(10, "tool_call", payload={"action": "new-read"}),
            _event(
                11,
                "tool_result",
                payload={"result": "new-same"},
                parent_event_id="event-10",
            ),
            _event(
                12,
                "acceptance_check",
                payload={"check": "new"},
                acceptance_delta="none",
            ),
        ],
        policy,
    )

    assert instruction is not None
    assert (
        instruction.to_dict()["cooling_conditions"]
        == base.to_dict()["cooling_conditions"]
    )
    assert (
        select_cooling_branch(
            [
                BranchCandidate(
                    "sole", ("evidence",), EffectClass.READ_ONLY, True, True, False
                )
            ],
            policy,
        )
        == "sole"
    )
    assert replay.decision_id == base.decision_id
    assert extended.decision_id != base.decision_id
    assert extended.evidence_event_ids != base.evidence_event_ids


@pytest.mark.parametrize("trace", ([], (), [object()]))
def test_analyze_rejects_invalid_trace_inputs_with_sanitized_codes(trace: Any) -> None:
    from semantic_reheating.controller import ControllerError, analyze

    with pytest.raises(ControllerError) as caught:
        analyze(trace, _policy())
    assert caught.value.code in {"empty_trace", "invalid_trace_event"}
    assert "object" not in str(caught.value)


def test_analyze_rejects_over_limit_trace_and_invalid_policy() -> None:
    from semantic_reheating.controller import ControllerError, analyze

    event = _event(1, "message")
    with pytest.raises(ControllerError) as caught:
        analyze([event] * 10_001, _policy())
    assert caught.value.code == "trace_item_limit"
    with pytest.raises(ControllerError) as caught:
        analyze([event], object())
    assert caught.value.code == "invalid_run_policy"
