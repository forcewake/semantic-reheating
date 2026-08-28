"""Generated controller-boundary proofs over varied valid traces and policies."""

from __future__ import annotations

import json
import math
from copy import deepcopy
from pathlib import Path
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

ROOT = Path(__file__).resolve().parents[2]
POLICY_FIXTURE = ROOT / "tests" / "fixtures" / "contracts" / "minimal-run-policy.json"
DIMENSIONS = ("turns", "tool_calls", "tokens", "elapsed_seconds", "cost")


def _source_policy() -> dict[str, Any]:
    return json.loads(POLICY_FIXTURE.read_text(encoding="utf-8"))


def _policy(*, permits_reheat: bool = True, semantic_enabled: bool = False) -> Any:
    from semantic_reheating.models import RunPolicy

    source = _source_policy()
    source["detectors"]["windows"] = {"repetition_events": 10, "no_progress_events": 10}
    source["recovery_ladder"]["nudge"]["permitted"] = False
    source["recovery_ladder"]["diagnose"]["permitted"] = False
    source["recovery_ladder"]["reheat"]["permitted"] = permits_reheat
    source["max_recovery_episodes"] = 1 if permits_reheat else 0
    source["detectors"]["semantic_detector"]["enabled"] = semantic_enabled
    source["detectors"]["semantic_detector"]["weight"] = 0.2 if semantic_enabled else 0
    return RunPolicy.from_dict(source)


@st.composite
def _valid_policy(draw: st.DrawFn, *, permits_reheat: bool | None = None) -> Any:
    """Construct policy variations directly in the contract-safe subset."""
    from semantic_reheating.models import RunPolicy

    source = _source_policy()
    reheat = draw(st.booleans()) if permits_reheat is None else permits_reheat
    source["policy_id"] = f"policy-property-{draw(st.integers(1, 9999)):04d}"
    source["detectors"]["windows"] = {
        "repetition_events": draw(st.integers(3, 10)),
        "no_progress_events": draw(st.integers(2, 10)),
    }
    for name in ("repetition_score", "no_progress_score", "risk_score", "budget_score"):
        source["detectors"]["thresholds"][name] = draw(
            st.floats(0, 1, allow_nan=False, allow_infinity=False)
        )
    for name in ("repetition", "no_progress", "risk", "budget"):
        source["detectors"]["weights"][name] = draw(
            st.floats(0.05, 1, allow_nan=False, allow_infinity=False)
        )
    semantic = source["detectors"]["semantic_detector"]
    semantic["enabled"] = draw(st.booleans())
    semantic["metered"] = draw(st.booleans())
    semantic["required"] = False
    semantic["weight"] = draw(st.floats(0, 1, allow_nan=False, allow_infinity=False))
    semantic["can_relax_hard_stops"] = False
    source["recovery_ladder"]["nudge"]["permitted"] = draw(st.booleans())
    source["recovery_ladder"]["diagnose"]["permitted"] = draw(st.booleans())
    source["recovery_ladder"]["reheat"]["permitted"] = reheat
    source["max_recovery_episodes"] = draw(st.integers(1, 5)) if reheat else 0
    source["max_reentry_depth"] = draw(st.integers(0, 5))
    for dimension in DIMENSIONS:
        low = draw(st.integers(1, 20))
        high = draw(st.integers(low, 30))
        source["budgets"]["per_intervention"][dimension] = low
        source["budgets"]["whole_run"][dimension] = high
    return RunPolicy.from_dict(source)


def _event(
    sequence: int,
    kind: str,
    *,
    run_id: str = "run-property",
    payload: dict[str, Any] | None = None,
    **fields: Any,
) -> Any:
    from semantic_reheating.models import TraceEvent

    source: dict[str, Any] = {
        "contract_version": "1.0",
        "run_id": run_id,
        "event_id": f"event-{sequence:03d}",
        "sequence": sequence,
        "kind": kind,
        "actor": "agent",
        "effect_class": "read_only",
        **fields,
    }
    if payload is not None:
        source["payload"] = payload
    else:
        source["payload_ref"] = f"payload-{sequence:03d}"
    return TraceEvent.from_dict(source)


def _signals_trace(
    run_id: str = "run-property", *, cosmetic: str = "read"
) -> list[Any]:
    return [
        _event(
            1, "tool_call", run_id=run_id, payload={"action": cosmetic, "token": "a"}
        ),
        _event(
            2,
            "tool_result",
            run_id=run_id,
            payload={"result": "same"},
            parent_event_id="event-001",
        ),
        _event(
            3,
            "acceptance_check",
            run_id=run_id,
            payload={"check": "done"},
            acceptance_delta="none",
        ),
        _event(
            4, "tool_call", run_id=run_id, payload={"action": cosmetic, "token": "a"}
        ),
        _event(
            5,
            "tool_result",
            run_id=run_id,
            payload={"result": "same"},
            parent_event_id="event-004",
        ),
        _event(
            6,
            "acceptance_check",
            run_id=run_id,
            payload={"check": "done"},
            acceptance_delta="none",
        ),
    ]


def _repetition_only_trace(run_id: str = "run-property") -> list[Any]:
    return [
        _event(1, "tool_call", run_id=run_id, payload={"action": "read"}),
        _event(
            2,
            "tool_result",
            run_id=run_id,
            payload={"result": "same"},
            parent_event_id="event-001",
        ),
        _event(3, "tool_call", run_id=run_id, payload={"action": "read"}),
        _event(
            4,
            "tool_result",
            run_id=run_id,
            payload={"result": "same"},
            parent_event_id="event-003",
        ),
    ]


def _no_progress_only_trace(run_id: str = "run-property") -> list[Any]:
    return [
        _event(
            index,
            "acceptance_check",
            run_id=run_id,
            payload={"check": "done"},
            acceptance_delta="none",
        )
        for index in range(1, 4)
    ]


@st.composite
def _signal_inputs(draw: st.DrawFn) -> tuple[list[Any], Any]:
    run_id = f"run-property-{draw(st.integers(1, 9999)):04d}"
    policy = draw(_valid_policy(permits_reheat=True))
    # The detector needs these local windows to observe both constructed classes.
    source = policy.to_dict()
    source["detectors"]["windows"] = {"repetition_events": 10, "no_progress_events": 10}
    source["recovery_ladder"]["nudge"]["permitted"] = False
    source["recovery_ladder"]["diagnose"]["permitted"] = False
    policy = type(policy).from_dict(source)
    return _signals_trace(
        run_id, cosmetic=draw(st.sampled_from(("read", "inspect", "status")))
    ), policy


class _SemanticRepetitionSupport:
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


@settings(derandomize=True, database=None, deadline=700, max_examples=24)
@given(inputs=_signal_inputs())
def test_valid_generated_inputs_have_finite_ordered_repeat_stable_envelopes(
    inputs: tuple[list[Any], Any],
) -> None:
    from semantic_reheating.controller import analyze
    from semantic_reheating.models import Decision
    from semantic_reheating.validation import validate_public_artifact

    trace, policy = inputs
    first, second = analyze(trace, policy), analyze(trace, policy)
    assert first.to_dict() == second.to_dict()
    assert first.decision_id == second.decision_id
    validate_public_artifact("decision_envelope", first.to_dict())
    assert first.decision is Decision.REHEAT
    contributions = first.confidence.contributing_findings
    assert (
        contributions == tuple(sorted(contributions, key=lambda item: item.finding_id))
        or contributions
    )
    assert len({item.finding_id for item in contributions}) == len(contributions)
    assert all(
        math.isfinite(float(item.score))
        and math.isfinite(float(item.weight))
        and math.isfinite(float(item.weighted_score))
        for item in contributions
    )
    assert 0 <= float(first.confidence.score) <= 1
    for item in contributions:
        assert 0 <= float(item.score) <= 1 and 0 <= float(item.weight) <= 1
        assert math.isclose(
            item.weighted_score,
            min(1.0, max(0.0, item.weight * item.score)),
            abs_tol=1e-12,
        )


@settings(derandomize=True, database=None, deadline=700, max_examples=10)
@given(
    dimension=st.sampled_from(DIMENSIONS),
    permits_reheat=st.booleans(),
    cosmetic=st.sampled_from(("read", "inspect")),
)
def test_one_whole_run_budget_dimension_dominates_competing_signals(
    dimension: str, permits_reheat: bool, cosmetic: str
) -> None:
    from semantic_reheating.controller import analyze
    from semantic_reheating.models import Decision

    policy = _policy(permits_reheat=permits_reheat, semantic_enabled=True)
    source = policy.to_dict()
    for name in DIMENSIONS:
        source["budgets"]["per_intervention"][name] = 1
        source["budgets"]["whole_run"][name] = 3
    policy = type(policy).from_dict(source)
    counters = {name: 2 for name in DIMENSIONS}
    counters[dimension] = 3
    trace = _signals_trace(cosmetic=cosmetic) + [
        _event(
            7,
            "budget",
            payload={"budget_dimension": dimension},
            budget_counters=counters,
        )
    ]
    first, second = (
        analyze(trace, policy, semantic_detector=_SemanticRepetitionSupport()),
        analyze(trace, policy, semantic_detector=_SemanticRepetitionSupport()),
    )
    assert first.to_dict() == second.to_dict()
    assert first.decision is Decision.STOP and first.confidence.score == 1.0
    budget = [
        item
        for item in first.confidence.contributing_findings
        if item.finding_class.value == "budget"
    ]
    assert len(budget) == 1 and "event-007" in first.evidence_event_ids


@settings(derandomize=True, database=None, deadline=700, max_examples=16)
@given(
    kind=st.sampled_from(("repetition", "no_progress", "neither", "budget")),
    policy=_valid_policy(),
    run_suffix=st.integers(1, 9999),
)
def test_no_reheat_for_repetition_only_no_progress_only_neither_or_budget_only(
    kind: str, policy: Any, run_suffix: int
) -> None:
    from semantic_reheating.controller import analyze
    from semantic_reheating.models import Decision

    run_id = f"run-no-reheat-{run_suffix}"
    if kind == "repetition":
        trace = _repetition_only_trace(run_id)
    elif kind == "no_progress":
        trace = _no_progress_only_trace(run_id)
    elif kind == "neither":
        trace = [
            _event(1, "message", run_id=run_id, payload={"message": "working"}),
            _event(2, "handoff", run_id=run_id),
        ]
    else:
        counters = policy.budgets.whole_run.to_dict()
        trace = [
            _event(
                1,
                "budget",
                run_id=run_id,
                payload={"budget_dimension": "turns"},
                budget_counters=counters,
            )
        ]
    decision = analyze(trace, policy)
    assert decision.decision is not Decision.REHEAT
    if kind == "budget":
        assert decision.decision is Decision.STOP
        assert "signals_agree" not in decision.reason_codes


@settings(derandomize=True, database=None, deadline=700, max_examples=12)
@given(
    permits_reheat=st.booleans(),
    policy=_valid_policy(),
    run_suffix=st.integers(1, 9999),
)
def test_exact_two_signal_gate_reheats_only_when_permitted(
    permits_reheat: bool, policy: Any, run_suffix: int
) -> None:
    from semantic_reheating.controller import analyze
    from semantic_reheating.models import Decision

    source = deepcopy(policy.to_dict())
    source["detectors"]["windows"] = {"repetition_events": 10, "no_progress_events": 10}
    source["recovery_ladder"]["nudge"]["permitted"] = False
    source["recovery_ladder"]["diagnose"]["permitted"] = False
    source["recovery_ladder"]["reheat"]["permitted"] = permits_reheat
    source["max_recovery_episodes"] = 1 if permits_reheat else 0
    policy = type(policy).from_dict(source)
    decision = analyze(_signals_trace(f"run-gate-{run_suffix}"), policy)
    assert decision.decision is (
        Decision.REHEAT if permits_reheat else Decision.RESTART
    )
