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


@pytest.mark.parametrize("recovery_policy", ("research", "branch", "model_switch"))
def test_recovery_policy_values_roundtrip_through_public_decision_envelope(
    recovery_policy: str,
) -> None:
    from semantic_reheating.models import DecisionEnvelope
    from semantic_reheating.validation import validate_public_artifact

    source = json.loads(
        (
            Path(__file__).parents[1]
            / "fixtures"
            / "contracts"
            / "minimal-decision-envelope.json"
        ).read_text(encoding="utf-8")
    )
    source.update(
        {
            "decision": "reheat",
            "reason_codes": ["signals_agree"],
            "recovery_policy": recovery_policy,
            "requires_host_action": False,
        }
    )
    source["constraints"]["require_host_confirmation"] = False

    envelope = DecisionEnvelope.from_dict(source)

    assert envelope.to_dict() == source
    assert validate_public_artifact("decision_envelope", envelope.to_dict()) == source


@pytest.mark.parametrize("value", ("research", "branch", "model_switch"))
def test_recovery_policy_values_remain_invalid_controller_decisions(value: str) -> None:
    from semantic_reheating.models import Decision

    with pytest.raises(ValueError):
        Decision(value)


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
    "candidates",
    (
        [_candidate("duplicate", ("e1",)), _candidate("duplicate", ("e1", "e2"))],
        [_candidate("duplicate", ("e1",)), _candidate("duplicate", ("e1",))],
    ),
)
def test_cooling_rejects_duplicate_branch_ids(
    candidates: list[Any],
) -> None:
    from semantic_reheating.policies import PolicySelectionError, select_cooling_branch

    with pytest.raises(PolicySelectionError) as raised:
        select_cooling_branch(candidates, _policy())

    assert raised.value.code == "duplicate_branch_id"
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


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


def test_construct_recovery_instruction_rejects_forged_reheat_without_findings() -> (
    None
):
    from semantic_reheating.models import Decision
    from semantic_reheating.policies import (
        PolicySelection,
        PolicySelectionError,
        RecoveryPolicy,
        construct_recovery_instruction,
        select_recovery_policy,
    )

    diagnosis = _diagnosis()
    policy = _policy(nudge=True, diagnose=True)
    forged = PolicySelection(
        Decision.REHEAT,
        RecoveryPolicy.BRANCH,
        ("signals_agree", "repetition_detected", "no_progress_detected"),
        ("fabricated-evidence",),
        False,
    )

    assert select_recovery_policy(diagnosis, [], policy).decision is Decision.CONTINUE
    with pytest.raises(PolicySelectionError) as raised:
        construct_recovery_instruction(forged, diagnosis, [], policy)

    assert raised.value.code == "policy_selection_mismatch"
    assert raised.value.args == ("Invalid policy selection input",)
    assert raised.value.__dict__ == {"code": "policy_selection_mismatch"}
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_construct_recovery_instruction_returns_none_for_genuine_non_reheat_selections() -> (
    None
):
    from semantic_reheating.models import Decision
    from semantic_reheating.policies import (
        construct_recovery_instruction,
        select_recovery_policy,
    )

    gate = [
        _finding("rep", finding_class="repetition"),
        _finding("progress", finding_class="no_progress"),
    ]
    cases = (
        (Decision.CONTINUE, _diagnosis(), [], _policy()),
        (Decision.NUDGE, _diagnosis("runtime_defect"), gate, _policy()),
        (Decision.DIAGNOSE, _diagnosis("runtime_defect"), gate, _policy(nudge=True)),
        (
            Decision.RESTART,
            _diagnosis("runtime_defect"),
            gate,
            _policy(nudge=True, diagnose=True, reheat=True),
        ),
        (Decision.ESCALATE, _diagnosis("missing_authority"), [], _policy()),
        (Decision.STOP, _diagnosis("unsafe_side_effect"), [], _policy()),
    )
    for decision, diagnosis, findings, policy in cases:
        selection = select_recovery_policy(diagnosis, findings, policy)

        assert selection.decision is decision
        assert (
            construct_recovery_instruction(selection, diagnosis, findings, policy)
            is None
        )


def test_construct_recovery_instruction_rejects_forged_non_reheat_selection() -> None:
    from semantic_reheating.models import Decision
    from semantic_reheating.policies import (
        PolicySelection,
        PolicySelectionError,
        construct_recovery_instruction,
    )

    diagnosis = _diagnosis()
    policy = _policy()
    forged = PolicySelection(Decision.NUDGE, None, (), (), False)

    with pytest.raises(PolicySelectionError) as raised:
        construct_recovery_instruction(forged, diagnosis, [], policy)

    assert raised.value.code == "policy_selection_mismatch"
    assert raised.value.args == ("Invalid policy selection input",)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


@pytest.mark.parametrize(
    "mutation",
    (
        "recovery_policy",
        "reason_codes",
        "evidence_event_ids",
        "requires_host_action",
        "decision",
    ),
)
def test_construct_recovery_instruction_rejects_each_selection_field_mutation(
    mutation: str,
) -> None:
    from semantic_reheating.models import Decision
    from semantic_reheating.policies import (
        PolicySelection,
        PolicySelectionError,
        RecoveryPolicy,
        construct_recovery_instruction,
        select_recovery_policy,
    )

    diagnosis = _diagnosis("runtime_defect")
    findings = [
        _finding("rep", finding_class="repetition"),
        _finding("progress", finding_class="no_progress"),
    ]
    policy = _policy(nudge=True, diagnose=True)
    selection = select_recovery_policy(diagnosis, findings, policy)
    replacement = {
        "recovery_policy": RecoveryPolicy.RESEARCH,
        "reason_codes": ("signals_agree", "repetition_detected"),
        "evidence_event_ids": (*selection.evidence_event_ids, "fabricated-evidence"),
        "requires_host_action": not selection.requires_host_action,
        "decision": Decision.RESTART,
    }[mutation]
    mutated = PolicySelection(
        replacement if mutation == "decision" else selection.decision,
        replacement if mutation == "recovery_policy" else selection.recovery_policy,
        replacement if mutation == "reason_codes" else selection.reason_codes,
        replacement
        if mutation == "evidence_event_ids"
        else selection.evidence_event_ids,
        replacement
        if mutation == "requires_host_action"
        else selection.requires_host_action,
    )

    assert selection.decision is Decision.REHEAT
    with pytest.raises(PolicySelectionError) as raised:
        construct_recovery_instruction(mutated, diagnosis, findings, policy)

    assert raised.value.code == "policy_selection_mismatch"
    assert raised.value.args == ("Invalid policy selection input",)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_construct_recovery_instruction_preserves_prefix_and_later_evidence() -> None:
    from semantic_reheating.policies import (
        construct_recovery_instruction,
        select_recovery_policy,
    )

    diagnosis = _diagnosis("missing_knowledge")
    findings = [
        _finding(
            "rep", finding_class="repetition", event_ids=["later-matched-finding"]
        ),
        _finding("progress", finding_class="no_progress"),
    ]
    policy = _policy(nudge=True, diagnose=True)
    selection = select_recovery_policy(diagnosis, findings, policy)

    instruction = construct_recovery_instruction(selection, diagnosis, findings, policy)

    assert instruction is not None
    assert instruction["evidence_refs"] == list(selection.evidence_event_ids)
    assert instruction["evidence_refs"] == [
        *diagnosis.evidence_event_ids,
        "later-matched-finding",
        "event-progress",
    ]


def test_construct_recovery_instruction_builds_exact_valid_reheat_contract() -> None:
    from hashlib import sha256

    from semantic_reheating.canonical import canonicalize_json
    from semantic_reheating.policies import (
        construct_recovery_instruction,
        select_recovery_policy,
    )
    from semantic_reheating.validation import validate_public_artifact

    diagnosis = _diagnosis("missing_knowledge", "incorrect_plan", "runtime_defect")
    findings = [
        _finding("rep", finding_class="repetition"),
        _finding("progress", finding_class="no_progress"),
    ]
    policy = _policy(nudge=True, diagnose=True)
    selection = select_recovery_policy(diagnosis, findings, policy)

    instruction = construct_recovery_instruction(selection, diagnosis, findings, policy)

    assert instruction is not None
    assert validate_public_artifact("recovery_instruction", instruction) == instruction
    basis = {
        key: value for key, value in instruction.items() if key != "instruction_id"
    }
    assert (
        instruction["instruction_id"]
        == "instruction-" + sha256(canonicalize_json(basis)).hexdigest()[:24]
    )
    assert instruction == {
        **basis,
        "instruction_id": instruction["instruction_id"],
    }
    assert instruction["run_id"] == diagnosis.run_id
    assert instruction["selected_prompt_asset_id"] == "prompt-reheat-v1"
    assert instruction["variables"] == [
        {
            "name": "constraint",
            "value": (
                "Produce exactly three mutually exclusive, falsifiable hypotheses "
                "and one discriminating read-only test per hypothesis."
            ),
        },
        {
            "name": "evidence_summary",
            "value": "Preserve the referenced public evidence identifiers.",
        },
        {
            "name": "next_step",
            "value": (
                "Return advisory structured output to the host runtime without "
                "executing tools."
            ),
        },
    ]
    assert instruction["allowed_tools"] == ["read_only", "analysis", "validation"]
    assert instruction["forbidden_actions"] == [
        "credential_access",
        "authority_grant",
        "network_publish",
        "non_idempotent_repeat",
    ]
    assert instruction["evidence_refs"] == list(selection.evidence_event_ids)
    assert instruction["rejected_hypothesis_refs"] == list(
        diagnosis.rejected_hypothesis_refs
    )
    assert instruction["expected_output"] == {
        "kind": "plan",
        "required_sections": [
            "summary",
            "evidence",
            "constraints",
            "next_steps",
            "stop_conditions",
        ],
        "max_characters": 6000,
        "hypothesis_contract": {
            "exact_hypotheses": 3,
            "mutually_exclusive": True,
            "falsifiable": True,
            "discriminating_tests_per_hypothesis": 1,
            "allowed_test_effect_classes": ["read_only"],
        },
    }
    assert instruction["advisory_only"] is True
    assert instruction["grants_authority"] is False


@pytest.mark.parametrize(
    "mutation",
    (
        {"exact_hypotheses": 2},
        {"exact_hypotheses": 4},
        {"discriminating_tests_per_hypothesis": 0},
        {"discriminating_tests_per_hypothesis": 2},
        {"allowed_test_effect_classes": ["non_idempotent_write"]},
        {"unexpected": True},
    ),
)
def test_reheat_hypothesis_contract_is_closed_and_fail_closed(
    mutation: dict[str, object],
) -> None:
    from semantic_reheating.validation import (
        ContractValidationError,
        validate_public_artifact,
    )

    source = json.loads(
        (
            Path(__file__).parents[1]
            / "fixtures"
            / "contracts"
            / "minimal-recovery-instruction.json"
        ).read_text(encoding="utf-8")
    )
    source["expected_output"]["hypothesis_contract"] = {
        "exact_hypotheses": 3,
        "mutually_exclusive": True,
        "falsifiable": True,
        "discriminating_tests_per_hypothesis": 1,
        "allowed_test_effect_classes": ["read_only"],
    }
    assert validate_public_artifact("recovery_instruction", source) == source
    rejected = deepcopy(source)
    rejected["expected_output"]["hypothesis_contract"].update(mutation)
    with pytest.raises(ContractValidationError) as caught:
        validate_public_artifact("recovery_instruction", rejected)
    assert caught.value.code == "schema_validation_error"


def test_reheat_prompt_requires_hypothesis_contract() -> None:
    from semantic_reheating.validation import (
        ContractValidationError,
        validate_public_artifact,
    )

    source = json.loads(
        (
            Path(__file__).parents[1]
            / "fixtures"
            / "contracts"
            / "minimal-recovery-instruction.json"
        ).read_text(encoding="utf-8")
    )
    source["expected_output"].pop("hypothesis_contract")
    with pytest.raises(ContractValidationError) as caught:
        validate_public_artifact("recovery_instruction", source)
    assert caught.value.code == "schema_validation_error"


def test_recovery_instruction_requires_closed_redacted_rejected_hypothesis_refs() -> (
    None
):
    from semantic_reheating.validation import (
        ContractValidationError,
        validate_public_artifact,
    )

    source = json.loads(
        (
            Path(__file__).parents[1]
            / "fixtures"
            / "contracts"
            / "minimal-recovery-instruction.json"
        ).read_text(encoding="utf-8")
    )
    assert validate_public_artifact("recovery_instruction", source) == source
    rejected = deepcopy(source)
    rejected.pop("rejected_hypothesis_refs", None)
    with pytest.raises(ContractValidationError) as caught:
        validate_public_artifact("recovery_instruction", rejected)
    assert caught.value.code == "schema_validation_error"


@pytest.mark.parametrize(
    ("refs", "valid"),
    (
        ([], True),
        (["rejected-hypothesis-ref"], True),
        (["unsafe ref"], False),
        (["rejected-hypothesis-ref", "rejected-hypothesis-ref"], False),
        ([f"rejected-hypothesis-{index}" for index in range(101)], False),
    ),
)
def test_recovery_instruction_rejected_hypothesis_refs_are_safe_unique_and_bounded(
    refs: list[str], valid: bool
) -> None:
    from semantic_reheating.validation import (
        ContractValidationError,
        validate_public_artifact,
    )

    source = json.loads(
        (
            Path(__file__).parents[1]
            / "fixtures"
            / "contracts"
            / "minimal-recovery-instruction.json"
        ).read_text(encoding="utf-8")
    )
    source["rejected_hypothesis_refs"] = refs

    if valid:
        assert validate_public_artifact("recovery_instruction", source) == source
    else:
        with pytest.raises(ContractValidationError) as caught:
            validate_public_artifact("recovery_instruction", source)
        assert caught.value.code == "schema_validation_error"


@pytest.mark.parametrize(
    ("causes", "expected_gaps"),
    (
        (
            ("missing_knowledge",),
            [("missing_evidence", "Required evidence is unavailable.")],
        ),
        (
            ("ambiguous_completion",),
            [("ambiguous_goal", "Completion criteria require clarification.")],
        ),
        (
            ("incorrect_plan",),
            [("failed_acceptance", "Acceptance criteria did not validate the plan.")],
        ),
        (
            ("unsuitable_tool", "runtime_defect"),
            [
                (
                    "stalled_progress",
                    "Progress is stalled pending a safe reassessment.",
                ),
                (
                    "stalled_progress",
                    "Progress is stalled pending a safe reassessment.",
                ),
            ],
        ),
    ),
)
def test_construct_recovery_instruction_maps_causes_in_design_order_without_prose(
    causes: tuple[str, ...], expected_gaps: list[tuple[str, str]]
) -> None:
    from semantic_reheating.policies import (
        construct_recovery_instruction,
        select_recovery_policy,
    )

    diagnosis = _diagnosis(*causes)
    findings = [
        _finding("rep", finding_class="repetition"),
        _finding("progress", finding_class="no_progress"),
    ]
    policy = _policy(nudge=True, diagnose=True)
    selection = select_recovery_policy(diagnosis, findings, policy)
    instruction = construct_recovery_instruction(selection, diagnosis, findings, policy)

    assert instruction is not None
    assert instruction["diagnosed_gaps"] == [
        {"kind": kind, "description": description}
        for kind, description in expected_gaps
    ]
    serialized = repr(instruction)
    assert "diagnostic_cause" not in serialized
    assert "hypotheses" not in instruction
    assert "tool_calls" not in instruction


def test_construct_recovery_instruction_is_deterministic_fresh_and_redacted() -> None:
    from semantic_reheating.diagnosis import diagnose
    from semantic_reheating.models import TraceEvent
    from semantic_reheating.policies import (
        construct_recovery_instruction,
        select_recovery_policy,
    )

    secret = "SECRET-MARKER-DO-NOT-COPY"
    diagnosis = diagnose(
        [
            TraceEvent.from_dict(
                {
                    "contract_version": "1.0",
                    "run_id": "run-policy",
                    "event_id": "secret-evidence",
                    "sequence": 1,
                    "kind": "plan",
                    "actor": "controller",
                    "effect_class": "read_only",
                    "payload": {
                        "diagnostic_cause": "missing_knowledge",
                        "eliminated_hypotheses": [secret],
                        "secret": secret,
                    },
                }
            )
        ],
        [],
    )
    findings = [
        _finding("rep", finding_class="repetition"),
        _finding("progress", finding_class="no_progress"),
    ]
    policy = _policy(nudge=True, diagnose=True)
    selection = select_recovery_policy(diagnosis, findings, policy)
    first = construct_recovery_instruction(selection, diagnosis, findings, policy)
    second = construct_recovery_instruction(selection, diagnosis, findings, policy)

    assert first == second
    assert first is not second
    assert first is not None and second is not None
    first["expected_output"]["hypothesis_contract"]["exact_hypotheses"] = 99
    assert second["expected_output"]["hypothesis_contract"]["exact_hypotheses"] == 3
    assert secret not in repr(second)
    assert second["rejected_hypothesis_refs"] == list(
        diagnosis.rejected_hypothesis_refs
    )
    assert secret not in repr(selection)
    assert secret not in repr(diagnosis)


def test_construct_recovery_instruction_fails_closed_and_preserves_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from semantic_reheating import policies
    from semantic_reheating.models import Decision
    from semantic_reheating.policies import select_recovery_policy
    from semantic_reheating.validation import validate_public_artifact

    diagnosis = _diagnosis("missing_knowledge")
    findings = [
        _finding("rep", finding_class="repetition"),
        _finding("progress", finding_class="no_progress"),
    ]
    policy = _policy(nudge=True, diagnose=True)
    selection = select_recovery_policy(diagnosis, findings, policy)
    forged = object.__new__(policies.PolicySelection)
    invalid_reheat = policies.PolicySelection(
        Decision.REHEAT,
        None,
        ("signals_agree",),
        (),
        False,
    )
    for bad_selection, bad_diagnosis, bad_policy, code in (
        (forged, diagnosis, policy, "invalid_policy_selection"),
        (invalid_reheat, diagnosis, policy, "policy_selection_mismatch"),
        (selection, object(), policy, "invalid_diagnosis"),
        (selection, diagnosis, object(), "invalid_run_policy"),
    ):
        with pytest.raises(policies.PolicySelectionError) as caught:
            policies.construct_recovery_instruction(
                bad_selection, bad_diagnosis, findings, bad_policy
            )
        assert caught.value.code == code
        assert caught.value.args == ("Invalid policy selection input",)
        assert caught.value.__cause__ is None
        assert caught.value.__context__ is None

    def invalid_canonical(*args: object, **kwargs: object) -> bytes:
        raise ValueError("SECRET-MARKER-DO-NOT-LEAK")

    monkeypatch.setattr(policies, "canonicalize_json", invalid_canonical)
    with pytest.raises(policies.PolicySelectionError) as canonical_error:
        policies.construct_recovery_instruction(selection, diagnosis, findings, policy)
    assert canonical_error.value.code == "invalid_recovery_instruction"
    assert "SECRET-MARKER-DO-NOT-LEAK" not in repr(canonical_error.value)
    assert canonical_error.value.__cause__ is None
    assert canonical_error.value.__context__ is None

    from semantic_reheating.canonical import canonicalize_json

    monkeypatch.setattr(policies, "canonicalize_json", canonicalize_json)

    def invalid_validator(kind: str, *args: object, **kwargs: object) -> object:
        if kind == "recovery_instruction":
            raise ValueError("SECRET-MARKER-DO-NOT-LEAK")
        return validate_public_artifact(kind, *args, **kwargs)

    monkeypatch.setattr(policies, "validate_public_artifact", invalid_validator)
    with pytest.raises(policies.PolicySelectionError) as validator_error:
        policies.construct_recovery_instruction(selection, diagnosis, findings, policy)
    assert validator_error.value.code == "invalid_recovery_instruction"
    assert "SECRET-MARKER-DO-NOT-LEAK" not in repr(validator_error.value)
    assert validator_error.value.__cause__ is None
    assert validator_error.value.__context__ is None

    monkeypatch.setattr(policies, "validate_public_artifact", validate_public_artifact)

    from semantic_reheating.diagnosis import Diagnosis
    from semantic_reheating.policies import RecoveryPolicy

    unsafe_run = Diagnosis("run-policy", (), (), ())
    object.__setattr__(unsafe_run, "run_id", "unsafe run id")
    unsafe_selection = policies.PolicySelection(
        Decision.REHEAT, RecoveryPolicy.BRANCH, ("signals_agree",), (), False
    )
    with pytest.raises(policies.PolicySelectionError) as run_error:
        policies.construct_recovery_instruction(
            unsafe_selection, unsafe_run, findings, policy
        )
    assert run_error.value.code == "invalid_diagnosis"
    assert run_error.value.__cause__ is None
    assert run_error.value.__context__ is None

    def resource(*args: object, **kwargs: object) -> bytes:
        raise MemoryError()

    monkeypatch.setattr(policies, "canonicalize_json", resource)
    with pytest.raises(MemoryError):
        policies.construct_recovery_instruction(selection, diagnosis, findings, policy)


def test_construct_recovery_instruction_validates_authoritative_findings_and_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from semantic_reheating import policies
    from semantic_reheating.policies import PolicySelectionError, select_recovery_policy

    class FindingSubclass(dict[str, object]):
        pass

    diagnosis = _diagnosis()
    policy = _policy()
    selection = select_recovery_policy(diagnosis, [], policy)
    cases = (
        ([{}], "invalid_detector_finding"),
        (
            [FindingSubclass(_finding("subclass", finding_class="repetition"))],
            "invalid_detector_finding",
        ),
        (
            [_finding("wrong-run", finding_class="repetition", run_id="other-run")],
            "run_id_mismatch",
        ),
        (
            [_finding("duplicate", finding_class="repetition")] * 2,
            "duplicate_finding_id",
        ),
    )
    for findings, code in cases:
        with pytest.raises(PolicySelectionError) as raised:
            policies.construct_recovery_instruction(
                selection, diagnosis, findings, policy
            )

        assert raised.value.code == code
        assert raised.value.args == ("Invalid policy selection input",)
        assert raised.value.__cause__ is None
        assert raised.value.__context__ is None

    memory_error = MemoryError("finding-validation-resource")

    def raise_memory_error(*args: object, **kwargs: object) -> object:
        raise memory_error

    monkeypatch.setattr(policies, "validate_public_artifact", raise_memory_error)
    with pytest.raises(MemoryError) as raised_memory:
        policies.construct_recovery_instruction(
            selection,
            diagnosis,
            [_finding("resource-memory", finding_class="repetition")],
            policy,
        )
    assert raised_memory.value is memory_error

    system_exit = SystemExit("finding-validation-resource")

    def raise_system_exit(*args: object, **kwargs: object) -> object:
        raise system_exit

    monkeypatch.setattr(policies, "validate_public_artifact", raise_system_exit)
    with pytest.raises(SystemExit) as raised_system_exit:
        policies.construct_recovery_instruction(
            selection,
            diagnosis,
            [_finding("resource-system-exit", finding_class="repetition")],
            policy,
        )
    assert raised_system_exit.value is system_exit


@pytest.mark.parametrize(
    "mutation",
    (
        "unsafe_side_effect",
        "exhausted_budget",
        "lower_stage",
        "recovery_policy",
        "host_action",
        "incomplete_reasons",
        "extra_reasons",
    ),
)
def test_construct_recovery_instruction_rejects_inconsistent_direct_reheat(
    mutation: str,
) -> None:
    from semantic_reheating.models import Decision, RunPolicy
    from semantic_reheating.policies import (
        PolicySelection,
        PolicySelectionError,
        RecoveryPolicy,
        construct_recovery_instruction,
        select_recovery_policy,
    )

    diagnosis = _diagnosis(
        mutation
        if mutation in {"unsafe_side_effect", "exhausted_budget"}
        else "runtime_defect"
    )
    reheat_policy = _policy(nudge=True, diagnose=True)
    findings = [
        _finding("rep", finding_class="repetition"),
        _finding("progress", finding_class="no_progress"),
    ]
    selection = select_recovery_policy(diagnosis, findings, reheat_policy)
    policy = reheat_policy
    if mutation in {"unsafe_side_effect", "exhausted_budget"}:
        selection = PolicySelection(
            Decision.REHEAT,
            RecoveryPolicy.BRANCH,
            ("signals_agree", "repetition_detected", "no_progress_detected"),
            selection.evidence_event_ids,
            False,
        )
    if mutation == "lower_stage":
        policy = _policy()
    elif mutation == "recovery_policy":
        selection = PolicySelection(
            Decision.REHEAT,
            RecoveryPolicy.RESEARCH,
            selection.reason_codes,
            selection.evidence_event_ids,
            selection.requires_host_action,
        )
    elif mutation == "host_action":
        source = _policy_source()
        source["recovery_ladder"]["nudge"]["permitted"] = False
        source["recovery_ladder"]["diagnose"]["permitted"] = False
        source["recovery_ladder"]["reheat"]["requires_host_action"] = True
        policy = RunPolicy.from_dict(source)
    elif mutation == "incomplete_reasons":
        selection = PolicySelection(
            Decision.REHEAT,
            selection.recovery_policy,
            ("signals_agree", "repetition_detected"),
            selection.evidence_event_ids,
            selection.requires_host_action,
        )
    elif mutation == "extra_reasons":
        selection = PolicySelection(
            Decision.REHEAT,
            selection.recovery_policy,
            (*selection.reason_codes, "risk_detected"),
            selection.evidence_event_ids,
            selection.requires_host_action,
        )

    with pytest.raises(PolicySelectionError) as raised:
        construct_recovery_instruction(selection, diagnosis, findings, policy)

    assert raised.value.code == "policy_selection_mismatch"
