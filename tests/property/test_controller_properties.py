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
_DETERMINISTIC_RANKS = (
    "exact-repetition-",
    "cycle-",
    "repeated-error-",
    "unchanged-state-",
    "acceptance-stall-",
    "budget-burn-",
    "hard-budget-",
    "repeated-risky-call-",
    "semantic-",
)
_REPETITION_PREFIXES = _DETERMINISTIC_RANKS[:3]
_NO_PROGRESS_PREFIXES = _DETERMINISTIC_RANKS[3:6]


def _contribution_rank(finding_id: str) -> int:
    matches = [
        index
        for index, prefix in enumerate(_DETERMINISTIC_RANKS)
        if finding_id.startswith(prefix)
    ]
    assert len(matches) == 1, finding_id
    return matches[0]


def _has_prefix(finding_id: str, prefixes: tuple[str, ...]) -> bool:
    return any(finding_id.startswith(prefix) for prefix in prefixes)


def _contribution_name(finding_id: str) -> str:
    if finding_id == "semantic-repetition-support":
        return "semantic"
    for name in _DETERMINISTIC_RANKS[:-1]:
        candidate = name.removesuffix("-")
        if finding_id.startswith(name):
            return candidate.replace("-", "_")
    raise AssertionError(finding_id)


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


def _policy_observing_trace(
    policy: Any, trace: list[Any], *, preserve_budget: bool = False
) -> Any:
    """Keep generated safe policy variation while making controlled evidence visible."""
    source = deepcopy(policy.to_dict())
    source["detectors"]["windows"] = {
        "repetition_events": len(trace),
        "no_progress_events": len(trace),
    }
    for threshold in source["detectors"]["thresholds"]:
        source["detectors"]["thresholds"][threshold] = 0.0
    if not preserve_budget:
        for dimension in DIMENSIONS:
            source["budgets"]["whole_run"][dimension] = 1_000_000
    return type(policy).from_dict(source)


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
    source["detectors"]["semantic_detector"]["enabled"] = True
    source["detectors"]["semantic_detector"]["weight"] = 0.2
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


def _order_policy(
    trace: list[Any], *, semantic_enabled: bool = False, hard_budget: bool = False
) -> Any:
    from semantic_reheating.models import RunPolicy

    source = _source_policy()
    source["detectors"]["windows"] = {
        "repetition_events": len(trace),
        "no_progress_events": len(trace),
    }
    source["detectors"]["semantic_detector"]["enabled"] = semantic_enabled
    source["detectors"]["semantic_detector"]["weight"] = 0.2 if semantic_enabled else 0
    for dimension in DIMENSIONS:
        source["budgets"]["per_intervention"][dimension] = 1
        source["budgets"]["whole_run"][dimension] = 3 if hard_budget else 1_000_000
    return RunPolicy.from_dict(source)


def _detector_order_cases() -> tuple[
    tuple[str, list[Any], Any, Any, tuple[str, ...]], ...
]:
    """Literal real-analyze cases for every production contribution category."""
    exact = _repetition_only_trace("run-order-exact")
    cycle = [
        _event(1, "state_observation", run_id="run-order-cycle", state_fingerprint="A"),
        _event(2, "state_observation", run_id="run-order-cycle", state_fingerprint="B"),
        _event(3, "state_observation", run_id="run-order-cycle", state_fingerprint="A"),
    ]
    repeated_error = [
        _event(1, "tool_call", run_id="run-order-error", payload={"action": "read"}),
        _event(2, "error", run_id="run-order-error", error_fingerprint="same-error"),
        _event(3, "tool_call", run_id="run-order-error", payload={"action": "read"}),
        _event(4, "error", run_id="run-order-error", error_fingerprint="same-error"),
    ]
    unchanged = [
        _event(
            1,
            "state_observation",
            run_id="run-order-unchanged",
            state_fingerprint="same-state",
        ),
        _event(
            2,
            "tool_call",
            run_id="run-order-unchanged",
            payload={"action": "read"},
            expected_state_change=True,
        ),
        _event(
            3,
            "state_observation",
            run_id="run-order-unchanged",
            state_fingerprint="same-state",
        ),
    ]
    acceptance = _no_progress_only_trace("run-order-acceptance")
    budget_burn = [
        _event(
            1,
            "budget",
            run_id="run-order-burn",
            budget_counters={dimension: 0 for dimension in DIMENSIONS},
        ),
        _event(
            2,
            "budget",
            run_id="run-order-burn",
            budget_counters={dimension: 1 for dimension in DIMENSIONS},
        ),
    ]
    hard_budget = [
        _event(
            1,
            "budget",
            run_id="run-order-hard-budget",
            budget_counters={dimension: 3 for dimension in DIMENSIONS},
        )
    ]
    repeated_risky = [
        _event(
            1,
            "tool_call",
            run_id="run-order-risk",
            payload={"action": "write"},
            effect_class="unknown",
        ),
        _event(
            2,
            "tool_call",
            run_id="run-order-risk",
            payload={"action": "write"},
            effect_class="unknown",
        ),
    ]
    semantic = [
        _event(1, "message", run_id="run-order-semantic", payload={"message": "x"})
    ]
    interaction = _signals_trace("run-order-interaction") + [
        _event(
            7,
            "budget",
            run_id="run-order-interaction",
            budget_counters={dimension: 3 for dimension in DIMENSIONS},
        ),
        _event(
            8,
            "tool_call",
            run_id="run-order-interaction",
            payload={"action": "write"},
            effect_class="unknown",
        ),
        _event(
            9,
            "tool_call",
            run_id="run-order-interaction",
            payload={"action": "write"},
            effect_class="unknown",
        ),
    ]
    full_interaction = [
        _event(1, "tool_call", run_id="run-order-full", payload={"action": "read"}),
        _event(
            2,
            "tool_result",
            run_id="run-order-full",
            payload={"result": "same"},
            parent_event_id="event-001",
        ),
        _event(3, "tool_call", run_id="run-order-full", payload={"action": "read"}),
        _event(
            4,
            "tool_result",
            run_id="run-order-full",
            payload={"result": "same"},
            parent_event_id="event-003",
        ),
        _event(5, "state_observation", run_id="run-order-full", state_fingerprint="A"),
        _event(6, "state_observation", run_id="run-order-full", state_fingerprint="B"),
        _event(7, "state_observation", run_id="run-order-full", state_fingerprint="A"),
        _event(8, "tool_call", run_id="run-order-full", payload={"action": "error"}),
        _event(9, "error", run_id="run-order-full", error_fingerprint="same-error"),
        _event(10, "tool_call", run_id="run-order-full", payload={"action": "error"}),
        _event(11, "error", run_id="run-order-full", error_fingerprint="same-error"),
        _event(
            12,
            "state_observation",
            run_id="run-order-full",
            state_fingerprint="same-state",
        ),
        _event(
            13,
            "tool_call",
            run_id="run-order-full",
            payload={"action": "observe"},
            expected_state_change=True,
        ),
        _event(
            14,
            "state_observation",
            run_id="run-order-full",
            state_fingerprint="same-state",
        ),
        _event(
            15,
            "acceptance_check",
            run_id="run-order-full",
            payload={"check": "done"},
            acceptance_delta="none",
        ),
        _event(
            16,
            "acceptance_check",
            run_id="run-order-full",
            payload={"check": "done"},
            acceptance_delta="none",
        ),
        _event(
            17,
            "budget",
            run_id="run-order-full",
            budget_counters={dimension: 0 for dimension in DIMENSIONS},
        ),
        _event(
            18,
            "budget",
            run_id="run-order-full",
            budget_counters={dimension: 3 for dimension in DIMENSIONS},
        ),
        _event(
            19,
            "tool_call",
            run_id="run-order-full",
            payload={"action": "write"},
            effect_class="unknown",
        ),
        _event(
            20,
            "tool_call",
            run_id="run-order-full",
            payload={"action": "write"},
            effect_class="unknown",
        ),
    ]
    return (
        ("exact_repetition", exact, _order_policy(exact), None, ("exact_repetition",)),
        ("cycle", cycle, _order_policy(cycle), None, ("cycle",)),
        (
            "repeated_error",
            repeated_error,
            _order_policy(repeated_error),
            None,
            ("repeated_error",),
        ),
        (
            "unchanged_state",
            unchanged,
            _order_policy(unchanged),
            None,
            ("unchanged_state",),
        ),
        (
            "acceptance_stall",
            acceptance,
            _order_policy(acceptance),
            None,
            ("acceptance_stall",),
        ),
        (
            "budget_burn",
            budget_burn,
            _order_policy(budget_burn),
            None,
            ("budget_burn",),
        ),
        (
            "hard_budget",
            hard_budget,
            _order_policy(hard_budget, hard_budget=True),
            None,
            ("hard_budget",),
        ),
        (
            "repeated_risky_call",
            repeated_risky,
            _order_policy(repeated_risky),
            None,
            ("repeated_risky_call",),
        ),
        (
            "semantic",
            semantic,
            _order_policy(semantic, semantic_enabled=True),
            _SemanticRepetitionSupport(),
            ("semantic",),
        ),
        (
            "interaction",
            interaction,
            _order_policy(interaction, semantic_enabled=True, hard_budget=True),
            _SemanticRepetitionSupport(),
            (
                "exact_repetition",
                "acceptance_stall",
                "hard_budget",
                "repeated_risky_call",
                "semantic",
            ),
        ),
        (
            "full_interaction",
            full_interaction,
            _order_policy(full_interaction, semantic_enabled=True, hard_budget=True),
            _SemanticRepetitionSupport(),
            (
                "exact_repetition",
                "cycle",
                "repeated_error",
                "unchanged_state",
                "acceptance_stall",
                "budget_burn",
                "hard_budget",
                "repeated_risky_call",
                "semantic",
            ),
        ),
    )


@settings(derandomize=True, database=None, deadline=700, max_examples=24)
@given(inputs=_signal_inputs())
def test_valid_generated_inputs_have_finite_ordered_repeat_stable_envelopes(
    inputs: tuple[list[Any], Any],
) -> None:
    from semantic_reheating.controller import analyze
    from semantic_reheating.models import Decision
    from semantic_reheating.validation import validate_public_artifact

    trace, policy = inputs
    first, second = (
        analyze(trace, policy, semantic_detector=_SemanticRepetitionSupport()),
        analyze(trace, policy, semantic_detector=_SemanticRepetitionSupport()),
    )
    first_bytes = json.dumps(first.to_dict(), separators=(",", ":")).encode("utf-8")
    second_bytes = json.dumps(second.to_dict(), separators=(",", ":")).encode("utf-8")
    assert first_bytes == second_bytes
    assert first.decision_id == second.decision_id
    validate_public_artifact("decision_envelope", first.to_dict())
    assert first.decision is Decision.REHEAT
    contributions = first.confidence.contributing_findings
    contribution_ids = tuple(item.finding_id for item in contributions)
    assert len(contribution_ids) == 3
    assert contribution_ids[0].startswith("exact-repetition-")
    assert contribution_ids[1].startswith("acceptance-stall-")
    assert contribution_ids[2] == "semantic-repetition-support"
    ranks = tuple(_contribution_rank(item.finding_id) for item in contributions)
    assert ranks == tuple(sorted(ranks))
    assert len({item.finding_id for item in contributions}) == len(contributions)
    assert contribution_ids == tuple(
        item.finding_id for item in second.confidence.contributing_findings
    )
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
    contribution_names = tuple(
        _contribution_name(item.finding_id)
        for item in first.confidence.contributing_findings
    )
    assert contribution_names == (
        "exact_repetition",
        "acceptance_stall",
        "hard_budget",
        "semantic",
    )
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
        policy = _policy_observing_trace(policy, trace)
    elif kind == "no_progress":
        trace = _no_progress_only_trace(run_id)
        policy = _policy_observing_trace(policy, trace)
    elif kind == "neither":
        trace = [
            _event(1, "message", run_id=run_id, payload={"message": "working"}),
            _event(2, "handoff", run_id=run_id),
        ]
        policy = _policy_observing_trace(policy, trace)
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
        policy = _policy_observing_trace(policy, trace, preserve_budget=True)
    decision = analyze(trace, policy)
    finding_ids = tuple(
        item.finding_id for item in decision.confidence.contributing_findings
    )
    repetition = [
        finding_id
        for finding_id in finding_ids
        if _has_prefix(finding_id, _REPETITION_PREFIXES)
    ]
    no_progress = [
        finding_id
        for finding_id in finding_ids
        if _has_prefix(finding_id, _NO_PROGRESS_PREFIXES)
    ]
    assert decision.decision is not Decision.REHEAT
    assert "signals_agree" not in decision.reason_codes
    if kind == "repetition":
        assert repetition and not no_progress
    elif kind == "no_progress":
        assert no_progress and not repetition
    elif kind == "neither":
        assert not repetition and not no_progress
        assert not any(
            finding_id.startswith("hard-budget-") for finding_id in finding_ids
        )
    else:
        assert decision.decision is Decision.STOP
        assert any(finding_id.startswith("hard-budget-") for finding_id in finding_ids)
        assert not repetition and not no_progress


def test_detector_order_table_covers_every_production_category() -> None:
    assert {case[0] for case in _detector_order_cases()} >= {
        "exact_repetition",
        "cycle",
        "repeated_error",
        "unchanged_state",
        "acceptance_stall",
        "budget_burn",
        "hard_budget",
        "repeated_risky_call",
        "semantic",
    }


def test_real_analyze_has_literal_complete_detector_contribution_order() -> None:
    from semantic_reheating.controller import analyze

    observed: set[str] = set()
    for name, trace, policy, semantic_detector, expected in _detector_order_cases():
        first = analyze(trace, policy, semantic_detector=semantic_detector)
        second = analyze(trace, policy, semantic_detector=semantic_detector)
        first_bytes = json.dumps(
            first.to_dict(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        second_bytes = json.dumps(
            second.to_dict(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        actual_names = tuple(
            _contribution_name(item.finding_id)
            for item in first.confidence.contributing_findings
        )
        assert actual_names == expected, (name, actual_names)
        assert first_bytes == second_bytes
        assert first.decision_id == second.decision_id
        assert tuple(
            item.finding_id for item in first.confidence.contributing_findings
        ) == tuple(item.finding_id for item in second.confidence.contributing_findings)
        observed.update(actual_names)
    assert observed >= {
        "exact_repetition",
        "cycle",
        "repeated_error",
        "unchanged_state",
        "acceptance_stall",
        "budget_burn",
        "hard_budget",
        "repeated_risky_call",
        "semantic",
    }


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
    finding_classes = {
        item.finding_class.value for item in decision.confidence.contributing_findings
    }
    assert {"repetition", "no_progress"} <= finding_classes
    assert "signals_agree" in decision.reason_codes
