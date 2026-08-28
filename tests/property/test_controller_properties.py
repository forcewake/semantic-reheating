"""Generated controller-boundary proofs over valid traces and policies."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

ROOT = Path(__file__).resolve().parents[2]
POLICY_FIXTURE = ROOT / "tests" / "fixtures" / "contracts" / "minimal-run-policy.json"


def _policy(
    *,
    repetition_weight: float = 0.4,
    no_progress_weight: float = 0.4,
    permits_reheat: bool = True,
    semantic_enabled: bool = False,
) -> Any:
    from semantic_reheating.models import RunPolicy

    source = json.loads(POLICY_FIXTURE.read_text(encoding="utf-8"))
    source["detectors"]["windows"] = {"repetition_events": 10, "no_progress_events": 10}
    source["detectors"]["weights"]["repetition"] = repetition_weight
    source["detectors"]["weights"]["no_progress"] = no_progress_weight
    source["recovery_ladder"]["nudge"]["permitted"] = False
    source["recovery_ladder"]["diagnose"]["permitted"] = False
    source["recovery_ladder"]["reheat"]["permitted"] = permits_reheat
    source["detectors"]["semantic_detector"]["enabled"] = semantic_enabled
    source["detectors"]["semantic_detector"]["weight"] = 0.2 if semantic_enabled else 0
    return RunPolicy.from_dict(source)


def _event(sequence: int, kind: str, *, payload: dict[str, Any], **fields: Any) -> Any:
    from semantic_reheating.models import TraceEvent

    return TraceEvent.from_dict(
        {
            "contract_version": "1.0",
            "run_id": "run-property",
            "event_id": f"event-{sequence:03d}",
            "sequence": sequence,
            "kind": kind,
            "actor": "agent",
            "effect_class": "read_only",
            "payload": payload,
            **fields,
        }
    )


def _repetition_and_no_progress_trace() -> list[Any]:
    return [
        _event(1, "tool_call", payload={"action": "read"}),
        _event(
            2, "tool_result", payload={"result": "same"}, parent_event_id="event-001"
        ),
        _event(
            3, "acceptance_check", payload={"check": "done"}, acceptance_delta="none"
        ),
        _event(4, "tool_call", payload={"action": "read"}),
        _event(
            5, "tool_result", payload={"result": "same"}, parent_event_id="event-004"
        ),
        _event(
            6, "acceptance_check", payload={"check": "done"}, acceptance_delta="none"
        ),
    ]


@settings(derandomize=True, database=None, deadline=500, max_examples=20)
@given(
    repetition_weight=st.floats(
        min_value=0.01, max_value=1.0, allow_nan=False, allow_infinity=False
    ),
    no_progress_weight=st.floats(
        min_value=0.01, max_value=1.0, allow_nan=False, allow_infinity=False
    ),
)
def test_generated_valid_controller_confidence_is_bounded_and_arithmetically_ordered(
    repetition_weight: float, no_progress_weight: float
) -> None:
    from semantic_reheating.controller import analyze
    from semantic_reheating.models import Decision

    decision = analyze(
        _repetition_and_no_progress_trace(),
        _policy(
            repetition_weight=repetition_weight,
            no_progress_weight=no_progress_weight,
        ),
    )

    assert decision.decision is Decision.REHEAT
    contributions = decision.confidence.contributing_findings
    assert [item.finding_class.value for item in contributions] == [
        "repetition",
        "no_progress",
    ]
    assert 0.0 <= float(decision.confidence.score) <= 1.0
    for item in contributions:
        assert 0.0 <= float(item.score) <= 1.0
        assert 0.0 <= float(item.weight) <= 1.0
        assert item.weighted_score == min(1.0, max(0.0, item.weight * item.score))
    assert decision.confidence.score == min(
        contributions[0].weighted_score, contributions[1].weighted_score
    )


class _SemanticRepetitionSupport:
    """A valid injected semantic seam that competes with deterministic findings."""

    def detect(self, events: tuple[Any, ...], _: Any) -> dict[str, Any]:
        return {
            "contract_version": "1.0",
            "run_id": events[0].run_id,
            "finding_id": "semantic-repetition-support",
            "detector_name": "semantic",
            "detector_version": "1.0",
            "matched": True,
            "score": 1.0,
            "finding_class": "repetition",
            "event_ids": [events[0].event_id],
            "reason_code": "repetition_detected",
            "explanation": "Redacted semantic repetition support.",
            "availability": {"status": "available", "notice": "Available."},
        }


@settings(derandomize=True, database=None, deadline=500, max_examples=5)
@given(
    dimension=st.sampled_from(
        ("turns", "tool_calls", "tokens", "elapsed_seconds", "cost")
    )
)
def test_hard_budget_stop_dominates_recovery_and_semantic_signals(
    dimension: str,
) -> None:
    from semantic_reheating.controller import analyze
    from semantic_reheating.models import Decision

    policy = _policy(semantic_enabled=True)
    counters = policy.budgets.whole_run.to_dict()
    trace = _repetition_and_no_progress_trace() + [
        _event(
            7,
            "budget",
            payload={"budget_dimension": dimension},
            budget_counters=counters,
        )
    ]

    decision = analyze(trace, policy, semantic_detector=_SemanticRepetitionSupport())

    assert decision.decision is Decision.STOP
    assert decision.reason_codes == (
        "signals_agree",
        "repetition_detected",
        "no_progress_detected",
        "budget_limit_reached",
    )
    assert decision.confidence.score == 1.0
    assert any(
        item.finding_class.value == "budget"
        for item in decision.confidence.contributing_findings
    )
    assert "event-007" in decision.evidence_event_ids


def _repetition_only_trace() -> list[Any]:
    return [
        _event(1, "tool_call", payload={"action": "read"}),
        _event(
            2, "tool_result", payload={"result": "same"}, parent_event_id="event-001"
        ),
        _event(3, "tool_call", payload={"action": "read"}),
        _event(
            4, "tool_result", payload={"result": "same"}, parent_event_id="event-003"
        ),
    ]


def _no_progress_only_trace() -> list[Any]:
    return [
        _event(
            1, "acceptance_check", payload={"check": "done"}, acceptance_delta="none"
        ),
        _event(
            2, "acceptance_check", payload={"check": "done"}, acceptance_delta="none"
        ),
        _event(
            3, "acceptance_check", payload={"check": "done"}, acceptance_delta="none"
        ),
    ]


@settings(derandomize=True, database=None, deadline=500, max_examples=3)
@given(missing_class=st.sampled_from(("repetition", "no_progress")))
def test_reheat_never_occurs_when_either_required_signal_class_is_missing(
    missing_class: str,
) -> None:
    from semantic_reheating.controller import analyze
    from semantic_reheating.models import Decision

    trace = (
        _no_progress_only_trace()
        if missing_class == "repetition"
        else _repetition_only_trace()
    )
    decision = analyze(trace, _policy())

    assert decision.decision is Decision.CONTINUE
    assert decision.confidence.score == 0.0


def test_budget_only_is_a_hard_stop_not_substitute_reheat_evidence() -> None:
    from semantic_reheating.controller import analyze
    from semantic_reheating.models import Decision

    policy = _policy()
    decision = analyze(
        [
            _event(
                1,
                "budget",
                payload={"budget_dimension": "turns"},
                budget_counters=policy.budgets.whole_run.to_dict(),
            )
        ],
        policy,
    )

    assert decision.decision is Decision.STOP
    assert "signals_agree" not in decision.reason_codes
    assert decision.evidence_event_ids == ("event-001",)


@settings(derandomize=True, database=None, deadline=500, max_examples=2)
@given(permits_reheat=st.booleans())
def test_exact_required_signal_gate_reheats_only_when_policy_permits(
    permits_reheat: bool,
) -> None:
    from semantic_reheating.controller import analyze
    from semantic_reheating.models import Decision

    decision = analyze(
        _repetition_and_no_progress_trace(), _policy(permits_reheat=permits_reheat)
    )

    assert decision.decision is (
        Decision.REHEAT if permits_reheat else Decision.RESTART
    )
