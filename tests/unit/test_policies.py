"""Constrained deterministic recovery-policy selection and cooling."""

from __future__ import annotations

import json
import pickle
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest


def _policy_source() -> dict[str, Any]:
    return json.loads(
        (
            Path(__file__).parents[1]
            / "fixtures"
            / "contracts"
            / "minimal-run-policy.json"
        ).read_text(encoding="utf-8")
    )


def _policy(**disabled: bool) -> Any:
    from semantic_reheating.models import RunPolicy

    source = _policy_source()
    for stage, disabled_value in disabled.items():
        source["recovery_ladder"][stage]["permitted"] = not disabled_value
    if source["recovery_ladder"]["reheat"]["permitted"] is False:
        source["max_recovery_episodes"] = 0
    return RunPolicy.from_dict(source)


def _event(sequence: int, cause: str, *, run_id: str = "run-policy") -> Any:
    from semantic_reheating.models import TraceEvent

    return TraceEvent.from_dict(
        {
            "contract_version": "1.0",
            "run_id": run_id,
            "event_id": f"diagnosis-{sequence}",
            "sequence": sequence,
            "kind": "error",
            "actor": "controller",
            "effect_class": "read_only",
            "payload": {"diagnostic_cause": cause},
        }
    )


def _diagnosis(*causes: str) -> Any:
    from semantic_reheating.diagnosis import diagnose

    if not causes:
        return diagnose(
            [], [_finding("neutral", finding_class="repetition", matched=False)]
        )
    return diagnose([_event(index, cause) for index, cause in enumerate(causes, 1)], [])


def _finding(
    finding_id: str,
    *,
    finding_class: str,
    matched: bool = True,
    reason_code: str | None = None,
    run_id: str = "run-policy",
    event_ids: list[str] | None = None,
) -> dict[str, object]:
    return {
        "contract_version": "1.0",
        "run_id": run_id,
        "finding_id": finding_id,
        "detector_name": "detector",
        "detector_version": "1.0",
        "matched": matched,
        "score": 1.0 if matched else 0.0,
        "finding_class": finding_class,
        "event_ids": event_ids or [f"event-{finding_id}"],
        "reason_code": reason_code or f"{finding_class}_detected",
        "explanation": "Closed deterministic detector result.",
        "availability": {"status": "available", "notice": "Available."},
    }


def _candidate(
    branch_id: str,
    refs: tuple[str, ...] = ("evidence-1",),
    *,
    effect: str = "read_only",
    within_budget: bool = True,
    host_authorized: bool = True,
    rejected: bool = False,
) -> Any:
    from semantic_reheating.models import EffectClass
    from semantic_reheating.policies import BranchCandidate

    return BranchCandidate(
        branch_id,
        refs,
        EffectClass(effect),
        within_budget,
        host_authorized,
        rejected,
    )


def _selected(findings: list[dict[str, object]], policy: Any | None = None) -> Any:
    from semantic_reheating.policies import select_recovery_policy

    return select_recovery_policy(_diagnosis(), findings, policy or _policy())


def test_no_gate_continues_without_recovery() -> None:
    from semantic_reheating.models import Decision

    selection = _selected([])

    assert selection.decision is Decision.CONTINUE
    assert selection.recovery_policy is None
    assert selection.reason_codes == ()
    assert selection.evidence_event_ids == ()
    assert selection.requires_host_action is False


@pytest.mark.parametrize(
    "findings",
    (
        [_finding("rep", finding_class="repetition")],
        [_finding("progress", finding_class="no_progress")],
    ),
)
def test_single_signal_cannot_substitute_for_exact_gate(
    findings: list[dict[str, object]],
) -> None:
    from semantic_reheating.models import Decision

    selection = _selected(findings)

    assert selection.decision is Decision.CONTINUE
    assert selection.recovery_policy is None


def test_budget_signal_stops_under_hard_precedence() -> None:
    from semantic_reheating.models import Decision

    selection = _selected(
        [_finding("budget", finding_class="budget", reason_code="budget_limit_reached")]
    )

    assert selection.decision is Decision.STOP


def test_exact_gate_selects_least_expensive_nudge_with_ordered_evidence() -> None:
    from semantic_reheating.models import Decision
    from semantic_reheating.policies import select_recovery_policy

    selection = select_recovery_policy(
        _diagnosis("runtime_defect"),
        [
            _finding(
                "progress", finding_class="no_progress", event_ids=["e2", "shared"]
            ),
            _finding("rep", finding_class="repetition", event_ids=["shared", "e1"]),
        ],
        _policy(),
    )

    assert selection.decision is Decision.NUDGE
    assert selection.recovery_policy is None
    assert selection.reason_codes == (
        "signals_agree",
        "repetition_detected",
        "no_progress_detected",
    )
    assert selection.evidence_event_ids == ("diagnosis-1", "e2", "shared", "e1")
    assert selection.requires_host_action is False


@pytest.mark.parametrize(
    ("disabled", "expected"),
    (
        ({"nudge": True}, "diagnose"),
        ({"nudge": True, "diagnose": True}, "reheat"),
        ({"nudge": True, "diagnose": True, "reheat": True}, "restart"),
        (
            {"nudge": True, "diagnose": True, "reheat": True, "restart": True},
            "escalate",
        ),
    ),
)
def test_ladder_cost_order_and_fallback_are_constrained(
    disabled: dict[str, bool], expected: str
) -> None:
    selection = _selected(
        [
            _finding("rep", finding_class="repetition"),
            _finding("progress", finding_class="no_progress"),
        ],
        _policy(**disabled),
    )

    assert selection.decision.value == expected
    assert selection.requires_host_action is (expected in {"restart", "escalate"})


def test_missing_authority_escalates_and_hard_blockers_stop_before_gate() -> None:
    from semantic_reheating.models import Decision
    from semantic_reheating.policies import select_recovery_policy

    gate = [
        _finding("rep", finding_class="repetition"),
        _finding("progress", finding_class="no_progress"),
    ]
    authority = select_recovery_policy(_diagnosis("missing_authority"), gate, _policy())
    risk = select_recovery_policy(_diagnosis("unsafe_side_effect"), gate, _policy())
    budget = select_recovery_policy(_diagnosis("exhausted_budget"), gate, _policy())

    assert authority.decision is Decision.ESCALATE
    assert authority.requires_host_action is True
    assert authority.reason_codes[-1] == "host_action_required"
    assert risk.decision is budget.decision is Decision.STOP
    assert risk.recovery_policy is budget.recovery_policy is None


@pytest.mark.parametrize(
    ("cause", "expected"),
    (
        ("missing_knowledge", "research"),
        ("incorrect_plan", "branch"),
        ("ambiguous_completion", "branch"),
        ("unsuitable_tool", "model_switch"),
        ("runtime_defect", "model_switch"),
    ),
)
def test_reheat_cause_mapping_is_closed_and_never_a_decision(
    cause: str, expected: str
) -> None:
    selection = _selected(
        [
            _finding("rep", finding_class="repetition"),
            _finding("progress", finding_class="no_progress"),
        ],
        _policy(nudge=True, diagnose=True),
    )
    from semantic_reheating.policies import select_recovery_policy

    selection = select_recovery_policy(
        _diagnosis(cause),
        [
            _finding("rep", finding_class="repetition"),
            _finding("progress", finding_class="no_progress"),
        ],
        _policy(nudge=True, diagnose=True),
    )

    assert selection.recovery_policy is not None
    assert selection.recovery_policy.value == expected
    assert selection.recovery_policy.value not in {
        "nudge",
        "diagnose",
        "reheat",
        "restart",
    }


def test_fixed_cause_precedence_and_generic_reheat_restart_branch() -> None:
    from semantic_reheating.policies import select_recovery_policy

    gate = [
        _finding("rep", finding_class="repetition"),
        _finding("p", finding_class="no_progress"),
    ]
    multi = select_recovery_policy(
        _diagnosis("runtime_defect", "incorrect_plan"),
        gate,
        _policy(nudge=True, diagnose=True),
    )
    generic = _selected(gate, _policy(nudge=True, diagnose=True))
    restart = _selected(gate, _policy(nudge=True, diagnose=True, reheat=True))

    assert multi.recovery_policy is not None and multi.recovery_policy.value == "branch"
    assert (
        generic.recovery_policy is not None
        and generic.recovery_policy.value == "branch"
    )
    assert (
        restart.recovery_policy is not None
        and restart.recovery_policy.value == "branch"
    )


def test_cooling_selects_only_a_unique_best_safe_evidence_branch() -> None:
    from semantic_reheating.policies import select_cooling_branch

    policy = _policy()
    sole = _candidate("sole")
    best = _candidate("best", ("e1", "e2"))
    lesser = _candidate("lesser", ("e1",))

    assert select_cooling_branch([sole], policy) == "sole"
    assert select_cooling_branch([lesser, best], policy) == "best"
    assert select_cooling_branch([best, lesser], policy) == "best"
    assert select_cooling_branch([best, _candidate("tied", ("x", "y"))], policy) is None


@pytest.mark.parametrize(
    "candidate",
    (
        _candidate("unknown", effect="unknown"),
        _candidate("write", effect="non_idempotent_write"),
        _candidate("empty", ()),
        _candidate("budget", within_budget=False),
        _candidate("authority", host_authorized=False),
        _candidate("rejected", rejected=True),
    ),
)
def test_cooling_rejects_unsafe_or_unverified_candidates(candidate: Any) -> None:
    from semantic_reheating.policies import select_cooling_branch

    assert select_cooling_branch([candidate], _policy()) is None


def test_public_records_are_strict_immutable_fresh_and_pickle_safe() -> None:
    from semantic_reheating.models import Decision, EffectClass
    from semantic_reheating.policies import (
        BranchCandidate,
        PolicySelection,
        PolicySelectionError,
    )

    selection = PolicySelection(
        Decision.REHEAT,
        None,
        ("signals_agree",),
        ("secret-free-evidence",),
        False,
    )
    serialized = selection.to_dict()
    serialized["reason_codes"].append("other")
    assert selection.to_dict()["reason_codes"] == ["signals_agree"]
    assert deepcopy(selection).to_dict() == selection.to_dict()
    assert pickle.loads(pickle.dumps(selection)).to_dict() == selection.to_dict()
    with pytest.raises(PolicySelectionError) as selection_error:
        PolicySelection(Decision.CONTINUE, None, ("bad space",), (), False)
    assert selection_error.value.code == "invalid_policy_selection"
    assert selection_error.value.args == ("Invalid policy selection input",)
    assert selection_error.value.__cause__ is None
    candidate = BranchCandidate(
        "branch", ("evidence",), EffectClass.READ_ONLY, True, True, False
    )
    assert pickle.loads(pickle.dumps(candidate)) == candidate


def test_boundaries_are_sanitized_and_exact_with_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from semantic_reheating import policies
    from semantic_reheating.models import RunPolicy

    forged = object.__new__(RunPolicy)
    cases = (
        (_diagnosis(), [{}], _policy()),
        (_diagnosis(), [_finding("same", finding_class="repetition")] * 2, _policy()),
        (
            _diagnosis(),
            [_finding("wrong", finding_class="repetition", run_id="other")],
            _policy(),
        ),
        (_diagnosis(), [], forged),
    )
    for diagnosis, findings, policy in cases:
        with pytest.raises(policies.PolicySelectionError) as raised:
            policies.select_recovery_policy(diagnosis, findings, policy)
        assert raised.value.args == ("Invalid policy selection input",)
        assert raised.value.__cause__ is None
        assert raised.value.__context__ is None
    with pytest.raises(policies.PolicySelectionError) as item_limit:
        policies.select_recovery_policy(
            _diagnosis(),
            [_finding(str(i), finding_class="repetition") for i in range(10_001)],
            _policy(),
        )
    assert item_limit.value.code == "policy_item_limit"
    with pytest.raises(policies.PolicySelectionError) as evidence_limit:
        policies.select_recovery_policy(
            _diagnosis(),
            [
                _finding(
                    f"many-{batch}",
                    finding_class="repetition",
                    event_ids=[f"e{batch}-{index}" for index in range(100)],
                )
                for batch in range(11)
            ],
            _policy(),
        )
    assert evidence_limit.value.code == "policy_evidence_limit"
    with pytest.raises(policies.PolicySelectionError) as cooling_limit:
        policies.select_cooling_branch(
            [_candidate(str(i)) for i in range(101)], _policy()
        )
    assert cooling_limit.value.code == "cooling_item_limit"

    def resource(*args: object, **kwargs: object) -> object:
        raise MemoryError()

    monkeypatch.setattr(policies, "validate_public_artifact", resource)
    with pytest.raises(MemoryError):
        policies.select_recovery_policy(
            _diagnosis(), [_finding("r", finding_class="repetition")], _policy()
        )


def test_selection_validation_work_is_linear(monkeypatch: pytest.MonkeyPatch) -> None:
    from semantic_reheating import policies

    findings = [
        _finding(str(index), finding_class="repetition", matched=False)
        for index in range(128)
    ]
    original = policies.validate_public_artifact
    calls = 0

    def counted(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(policies, "validate_public_artifact", counted)
    _selected(findings)
    assert calls <= len(findings) + 1
