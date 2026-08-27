"""Pure, fail-closed constrained recovery-policy selection."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
from re import compile as re_compile
from typing import Any, NoReturn

from .canonical import canonicalize_json
from .diagnosis import CauseClass, Diagnosis, UncertaintyDisposition, UncertaintyItem
from .models import Decision, EffectClass, FindingClass, RunPolicy
from .validation import validate_public_artifact

_MAX_FINDINGS = 10_000
_MAX_EVIDENCE = 1_000
_MAX_CANDIDATES = 100
_SAFE_ID = re_compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_REASON_BY_CLASS = {
    "repetition": "repetition_detected",
    "no_progress": "no_progress_detected",
    "risk": "risk_detected",
    "budget": "budget_limit_reached",
}
_RECOVERY_GAPS = {
    CauseClass.MISSING_KNOWLEDGE: (
        "missing_evidence",
        "Required evidence is unavailable.",
    ),
    CauseClass.AMBIGUOUS_COMPLETION: (
        "ambiguous_goal",
        "Completion criteria require clarification.",
    ),
    CauseClass.INCORRECT_PLAN: (
        "failed_acceptance",
        "Acceptance criteria did not validate the plan.",
    ),
    CauseClass.UNSUITABLE_TOOL: (
        "stalled_progress",
        "Progress is stalled pending a safe reassessment.",
    ),
    CauseClass.RUNTIME_DEFECT: (
        "stalled_progress",
        "Progress is stalled pending a safe reassessment.",
    ),
    CauseClass.MISSING_AUTHORITY: (
        "risk_blocker",
        "Recovery is blocked by a safety or authority constraint.",
    ),
    CauseClass.UNSAFE_SIDE_EFFECT: (
        "risk_blocker",
        "Recovery is blocked by a safety or authority constraint.",
    ),
    CauseClass.EXHAUSTED_BUDGET: (
        "risk_blocker",
        "Recovery is blocked by a safety or authority constraint.",
    ),
}
_HYPOTHESIS_CONTRACT = {
    "exact_hypotheses": 3,
    "mutually_exclusive": True,
    "falsifiable": True,
    "discriminating_tests_per_hypothesis": 1,
    "allowed_test_effect_classes": ["read_only"],
}


class RecoveryPolicy(str, Enum):
    """Closed advisory policy labels; these are not controller decisions."""

    RESEARCH = "research"
    BRANCH = "branch"
    MODEL_SWITCH = "model_switch"


class PolicySelectionError(ValueError):
    """Sanitized failure from the policy-selection boundary."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__("Invalid policy selection input")


def _fail(code: str) -> NoReturn:
    raise PolicySelectionError(code) from None


def _safe_identifier(value: Any) -> bool:
    return (
        type(value) is str
        and 0 < len(value) <= 128
        and _SAFE_ID.fullmatch(value) is not None
    )


@dataclass(frozen=True, slots=True)
class PolicySelection:
    """One validated internal decision record, deliberately not an envelope."""

    decision: Decision
    recovery_policy: RecoveryPolicy | None
    reason_codes: tuple[str, ...]
    evidence_event_ids: tuple[str, ...]
    requires_host_action: bool

    def __post_init__(self) -> None:
        if (
            type(self.decision) is not Decision
            or (
                self.recovery_policy is not None
                and type(self.recovery_policy) is not RecoveryPolicy
            )
            or type(self.reason_codes) is not tuple
            or type(self.evidence_event_ids) is not tuple
            or type(self.requires_host_action) is not bool
            or any(not _safe_identifier(value) for value in self.reason_codes)
            or any(not _safe_identifier(value) for value in self.evidence_event_ids)
            or len(set(self.reason_codes)) != len(self.reason_codes)
            or len(set(self.evidence_event_ids)) != len(self.evidence_event_ids)
            or len(self.evidence_event_ids) > _MAX_EVIDENCE
            or (
                self.recovery_policy is not None
                and self.decision not in (Decision.REHEAT, Decision.RESTART)
            )
            or (self.decision is Decision.CONTINUE and self.requires_host_action)
        ):
            _fail("invalid_policy_selection")

    def _validate_state(self) -> None:
        invalid = False
        try:
            self.__post_init__()
        except (MemoryError, SystemExit, PolicySelectionError):
            raise
        except Exception:  # noqa: BLE001 - typed object integrity boundary.
            invalid = True
        if invalid:
            _fail("invalid_policy_selection")

    def to_dict(self) -> dict[str, Any]:
        self._validate_state()
        return {
            "decision": self.decision.value,
            "recovery_policy": (
                self.recovery_policy.value if self.recovery_policy is not None else None
            ),
            "reason_codes": list(self.reason_codes),
            "evidence_event_ids": list(self.evidence_event_ids),
            "requires_host_action": self.requires_host_action,
        }

    def __reduce__(self) -> tuple[Any, tuple[Any, ...]]:
        self._validate_state()
        return (
            PolicySelection,
            (
                self.decision,
                self.recovery_policy,
                self.reason_codes,
                self.evidence_event_ids,
                self.requires_host_action,
            ),
        )


@dataclass(frozen=True, slots=True)
class BranchCandidate:
    """An evidence-backed advisory cooling branch candidate."""

    branch_id: str
    verified_evidence_refs: tuple[str, ...]
    next_action_effect: EffectClass
    within_budget: bool
    host_authorized: bool
    rejected: bool

    def __post_init__(self) -> None:
        if (
            not _safe_identifier(self.branch_id)
            or type(self.verified_evidence_refs) is not tuple
            or any(not _safe_identifier(ref) for ref in self.verified_evidence_refs)
            or len(set(self.verified_evidence_refs)) != len(self.verified_evidence_refs)
            or len(self.verified_evidence_refs) > _MAX_EVIDENCE
            or type(self.next_action_effect) is not EffectClass
            or type(self.within_budget) is not bool
            or type(self.host_authorized) is not bool
            or type(self.rejected) is not bool
        ):
            _fail("invalid_branch_candidate")

    def _validate_state(self) -> None:
        invalid = False
        try:
            self.__post_init__()
        except (MemoryError, SystemExit, PolicySelectionError):
            raise
        except Exception:  # noqa: BLE001 - typed object integrity boundary.
            invalid = True
        if invalid:
            _fail("invalid_branch_candidate")

    def to_dict(self) -> dict[str, Any]:
        self._validate_state()
        return {
            "branch_id": self.branch_id,
            "verified_evidence_refs": list(self.verified_evidence_refs),
            "next_action_effect": self.next_action_effect.value,
            "within_budget": self.within_budget,
            "host_authorized": self.host_authorized,
            "rejected": self.rejected,
        }

    def __reduce__(self) -> tuple[Any, tuple[Any, ...]]:
        self._validate_state()
        return (
            BranchCandidate,
            (
                self.branch_id,
                self.verified_evidence_refs,
                self.next_action_effect,
                self.within_budget,
                self.host_authorized,
                self.rejected,
            ),
        )


def _validated_diagnosis(diagnosis: Any) -> dict[str, Any]:
    if type(diagnosis) is not Diagnosis:
        _fail("invalid_diagnosis")
    value: Any = None
    invalid = False
    try:
        value = diagnosis.to_dict()
    except (MemoryError, SystemExit):
        raise
    except Exception:  # noqa: BLE001 - public diagnosis boundary is sanitized.
        invalid = True
    if invalid or type(value) is not dict:
        _fail("invalid_diagnosis")
    return value


def _validated_policy(policy: Any) -> RunPolicy:
    if type(policy) is not RunPolicy:
        _fail("invalid_run_policy")
    fresh: Any = None
    invalid = False
    try:
        fresh = RunPolicy.from_dict(policy.to_dict())
    except (MemoryError, SystemExit):
        raise
    except Exception:  # noqa: BLE001 - public policy boundary is sanitized.
        invalid = True
    if invalid or type(fresh) is not RunPolicy:
        _fail("invalid_run_policy")
    return fresh


def _validated_recovery_selection(selection: Any) -> PolicySelection:
    if type(selection) is not PolicySelection:
        _fail("invalid_policy_selection")
    fresh: Any = None
    invalid = False
    try:
        data = selection.to_dict()
        if type(data) is not dict:
            invalid = True
        else:
            recovery_policy = data["recovery_policy"]
            fresh = PolicySelection(
                Decision(data["decision"]),
                RecoveryPolicy(recovery_policy)
                if recovery_policy is not None
                else None,
                tuple(data["reason_codes"]),
                tuple(data["evidence_event_ids"]),
                data["requires_host_action"],
            )
    except (MemoryError, SystemExit):
        raise
    except Exception:  # noqa: BLE001 - typed selection integrity boundary.
        invalid = True
    if invalid or type(fresh) is not PolicySelection:
        _fail("invalid_policy_selection")
    return fresh


def _validated_recovery_diagnosis(diagnosis: Any) -> Diagnosis:
    if type(diagnosis) is not Diagnosis:
        _fail("invalid_diagnosis")
    fresh: Any = None
    invalid = False
    try:
        data = diagnosis.to_dict()
        if type(data) is not dict:
            invalid = True
        else:
            causes = tuple(CauseClass(value) for value in data["cause_classes"])
            uncertainties = tuple(
                UncertaintyItem(
                    item["uncertainty_id"],
                    CauseClass(item["cause_class"]),
                    UncertaintyDisposition(item["disposition"]),
                    item["high_risk"],
                )
                for item in data["uncertainty_map"]
            )
            fresh = Diagnosis(
                data["run_id"],
                causes,
                uncertainties,
                tuple(data["evidence_event_ids"]),
                tuple(data["rejected_hypothesis_refs"]),
            )
    except (MemoryError, SystemExit):
        raise
    except Exception:  # noqa: BLE001 - typed diagnosis integrity boundary.
        invalid = True
    if invalid or type(fresh) is not Diagnosis:
        _fail("invalid_diagnosis")
    return fresh


def _copy_json(value: Any) -> Any:
    if type(value) is dict:
        return {key: _copy_json(item) for key, item in value.items()}
    if type(value) is list:
        return [_copy_json(item) for item in value]
    return value


def _validate_reheat_selection(
    selection: PolicySelection, diagnosis: Diagnosis, policy: RunPolicy
) -> None:
    """Require a direct REHEAT record to match the selector's clean gate."""
    invalid = False
    try:
        ladder = policy.recovery_ladder
        expected_reasons = (
            "signals_agree",
            "repetition_detected",
            "no_progress_detected",
        ) + (("host_action_required",) if ladder.reheat.requires_host_action else ())
        invalid = (
            any(
                cause
                in {
                    CauseClass.MISSING_AUTHORITY,
                    CauseClass.UNSAFE_SIDE_EFFECT,
                    CauseClass.EXHAUSTED_BUDGET,
                }
                for cause in diagnosis.cause_classes
            )
            or ladder.nudge.permitted
            or ladder.diagnose.permitted
            or not policy.allows_reheat(
                (FindingClass.REPETITION, FindingClass.NO_PROGRESS)
            )
            or selection.recovery_policy
            is not _recovery_policy(Decision.REHEAT, diagnosis.cause_classes)
            or selection.requires_host_action is not ladder.reheat.requires_host_action
            or selection.reason_codes != expected_reasons
            or selection.evidence_event_ids[: len(diagnosis.evidence_event_ids)]
            != diagnosis.evidence_event_ids
        )
    except (MemoryError, SystemExit):
        raise
    except Exception:  # noqa: BLE001 - direct records are an integrity boundary.
        invalid = True
    if invalid:
        _fail("invalid_reheat_selection")


def construct_recovery_instruction(
    selection: Any, diagnosis: Any, findings: Any, policy: Any
) -> dict[str, Any] | None:
    """Construct a deterministic, bounded advisory instruction for exact REHEAT."""
    fresh_selection = _validated_recovery_selection(selection)
    fresh_diagnosis = _validated_recovery_diagnosis(diagnosis)
    fresh_policy = _validated_policy(policy)
    authoritative_selection = select_recovery_policy(
        fresh_diagnosis, findings, fresh_policy
    )
    if fresh_selection != authoritative_selection:
        _fail("policy_selection_mismatch")
    if fresh_selection.decision is not Decision.REHEAT:
        return None
    _validate_reheat_selection(fresh_selection, fresh_diagnosis, fresh_policy)

    failure = False
    try:
        policy_data = fresh_policy.to_dict()
        basis: dict[str, Any] = {
            "contract_version": "1.0",
            "run_id": fresh_diagnosis.run_id,
            "selected_prompt_asset_id": "prompt-reheat-v1",
            "variables": [
                {
                    "name": "constraint",
                    "value": (
                        "Produce exactly three mutually exclusive, falsifiable "
                        "hypotheses and one discriminating read-only test per hypothesis."
                    ),
                },
                {
                    "name": "evidence_summary",
                    "value": "Preserve the referenced public evidence identifiers.",
                },
                {
                    "name": "next_step",
                    "value": (
                        "Return advisory structured output to the host runtime "
                        "without executing tools."
                    ),
                },
            ],
            "diagnosed_gaps": [
                {
                    "kind": _RECOVERY_GAPS[cause][0],
                    "description": _RECOVERY_GAPS[cause][1],
                }
                for cause in fresh_diagnosis.cause_classes
            ],
            "recovery_budget": _copy_json(policy_data["budgets"]["per_intervention"]),
            "allowed_tools": ["read_only", "analysis", "validation"],
            "forbidden_actions": [
                "credential_access",
                "authority_grant",
                "network_publish",
                "non_idempotent_repeat",
            ],
            "evidence_refs": list(fresh_selection.evidence_event_ids),
            "rejected_hypothesis_refs": list(fresh_diagnosis.rejected_hypothesis_refs),
            "expected_output": {
                "kind": "plan",
                "required_sections": [
                    "summary",
                    "evidence",
                    "constraints",
                    "next_steps",
                    "stop_conditions",
                ],
                "max_characters": 6000,
                "hypothesis_contract": _copy_json(_HYPOTHESIS_CONTRACT),
            },
            "cooling_conditions": _copy_json(policy_data["cooling_conditions"]),
            "stop_conditions": [
                "budget_exhausted",
                "host_denial",
                "risk_detected",
                "acceptance_met",
                "cooling_required",
            ],
            "advisory_only": True,
            "grants_authority": False,
        }
        source = {
            "instruction_id": "instruction-"
            + sha256(canonicalize_json(basis)).hexdigest()[:24],
            **basis,
        }
        validated = validate_public_artifact("recovery_instruction", source)
        if type(validated) is dict:
            return _copy_json(validated)
        failure = True
    except (MemoryError, SystemExit):
        raise
    except Exception:  # noqa: BLE001 - instruction assembly must not leak inputs.
        failure = True
    if failure:
        _fail("invalid_recovery_instruction")
    _fail("invalid_recovery_instruction")


def _validated_findings(findings: Any, run_id: str) -> tuple[dict[str, Any], ...]:
    if type(findings) not in (list, tuple):
        _fail("invalid_detector_finding")
    if len(findings) > _MAX_FINDINGS:
        _fail("policy_item_limit")
    parsed: list[dict[str, Any]] = []
    invalid = False
    try:
        for finding in findings:
            if type(finding) is not dict:
                invalid = True
                break
            fresh = validate_public_artifact("detector_finding", finding)
            if type(fresh) is not dict:
                invalid = True
                break
            parsed.append(fresh)
    except (MemoryError, SystemExit):
        raise
    except Exception:  # noqa: BLE001 - public finding boundary is sanitized.
        invalid = True
    if invalid:
        _fail("invalid_detector_finding")
    if len({finding["finding_id"] for finding in parsed}) != len(parsed):
        _fail("duplicate_finding_id")
    if any(finding["run_id"] != run_id for finding in parsed):
        _fail("run_id_mismatch")
    return tuple(parsed)


def _collect_evidence(
    diagnosis: dict[str, Any], findings: tuple[dict[str, Any], ...]
) -> tuple[str, ...]:
    evidence: list[str] = []
    seen: set[str] = set()
    for event_id in diagnosis["evidence_event_ids"]:
        if event_id not in seen:
            seen.add(event_id)
            evidence.append(event_id)
    for finding in findings:
        if finding["matched"] is not True:
            continue
        for event_id in finding["event_ids"]:
            if event_id not in seen:
                seen.add(event_id)
                evidence.append(event_id)
                if len(evidence) > _MAX_EVIDENCE:
                    _fail("policy_evidence_limit")
    if len(evidence) > _MAX_EVIDENCE:
        _fail("policy_evidence_limit")
    return tuple(evidence)


def _reason_codes(
    findings: tuple[dict[str, Any], ...], host_action: bool
) -> tuple[str, ...]:
    observed = {
        finding["finding_class"] for finding in findings if finding["matched"] is True
    }
    risk = any(
        finding["matched"] is True
        and (
            finding["finding_class"] == "risk"
            or finding["reason_code"] == "risk_detected"
        )
        for finding in findings
    )
    budget = any(
        finding["matched"] is True
        and (
            finding["finding_class"] == "budget"
            or finding["reason_code"] == "budget_limit_reached"
        )
        for finding in findings
    )
    reasons: list[str] = []
    if {"repetition", "no_progress"}.issubset(observed):
        reasons.append("signals_agree")
    if "repetition" in observed:
        reasons.append(_REASON_BY_CLASS["repetition"])
    if "no_progress" in observed:
        reasons.append(_REASON_BY_CLASS["no_progress"])
    if risk:
        reasons.append(_REASON_BY_CLASS["risk"])
    if budget:
        reasons.append(_REASON_BY_CLASS["budget"])
    if host_action:
        reasons.append("host_action_required")
    return tuple(reasons)


def _recovery_policy(
    decision: Decision, cause_classes: tuple[CauseClass, ...]
) -> RecoveryPolicy | None:
    if decision not in (Decision.REHEAT, Decision.RESTART):
        return None
    for cause in CauseClass:
        if cause not in cause_classes:
            continue
        if cause is CauseClass.MISSING_KNOWLEDGE:
            return RecoveryPolicy.RESEARCH
        if cause in (CauseClass.INCORRECT_PLAN, CauseClass.AMBIGUOUS_COMPLETION):
            return RecoveryPolicy.BRANCH
        if cause in (CauseClass.UNSUITABLE_TOOL, CauseClass.RUNTIME_DEFECT):
            return RecoveryPolicy.MODEL_SWITCH
    return RecoveryPolicy.BRANCH


def _selection(
    decision: Decision,
    policy: RunPolicy,
    cause_classes: tuple[CauseClass, ...],
    findings: tuple[dict[str, Any], ...],
    evidence: tuple[str, ...],
) -> PolicySelection:
    stage = policy.recovery_ladder
    requires_host_action = {
        Decision.NUDGE: stage.nudge.requires_host_action,
        Decision.DIAGNOSE: stage.diagnose.requires_host_action,
        Decision.REHEAT: stage.reheat.requires_host_action,
        Decision.RESTART: stage.restart.requires_host_action,
        Decision.ESCALATE: True,
        Decision.STOP: stage.stop.requires_host_action,
        Decision.CONTINUE: False,
    }[decision]
    return PolicySelection(
        decision,
        _recovery_policy(decision, cause_classes),
        _reason_codes(findings, requires_host_action),
        evidence,
        requires_host_action,
    )


def select_recovery_policy(
    diagnosis: Any, findings: Any, policy: Any
) -> PolicySelection:
    """Choose the least-cost permitted response under closed safety gates."""
    diagnosis_data = _validated_diagnosis(diagnosis)
    fresh_policy = _validated_policy(policy)
    parsed_findings = _validated_findings(findings, diagnosis_data["run_id"])
    evidence = _collect_evidence(diagnosis_data, parsed_findings)
    cause_classes = tuple(
        CauseClass(value) for value in diagnosis_data["cause_classes"]
    )
    observed = {
        finding["finding_class"]
        for finding in parsed_findings
        if finding["matched"] is True
    }
    risk = "risk" in observed or any(
        finding["matched"] is True and finding["reason_code"] == "risk_detected"
        for finding in parsed_findings
    )
    budget = "budget" in observed or any(
        finding["matched"] is True and finding["reason_code"] == "budget_limit_reached"
        for finding in parsed_findings
    )
    if risk or CauseClass.UNSAFE_SIDE_EFFECT in cause_classes:
        return _selection(
            Decision.STOP, fresh_policy, cause_classes, parsed_findings, evidence
        )
    if budget or CauseClass.EXHAUSTED_BUDGET in cause_classes:
        return _selection(
            Decision.STOP, fresh_policy, cause_classes, parsed_findings, evidence
        )
    if CauseClass.MISSING_AUTHORITY in cause_classes:
        decision = (
            Decision.ESCALATE
            if fresh_policy.recovery_ladder.escalate.permitted
            else Decision.STOP
        )
        return _selection(
            decision, fresh_policy, cause_classes, parsed_findings, evidence
        )
    gate = {"repetition", "no_progress"}.issubset(observed)
    if not gate:
        return PolicySelection(Decision.CONTINUE, None, (), evidence, False)
    ladder = fresh_policy.recovery_ladder
    if ladder.nudge.permitted:
        return _selection(
            Decision.NUDGE, fresh_policy, cause_classes, parsed_findings, evidence
        )
    if ladder.diagnose.permitted:
        return _selection(
            Decision.DIAGNOSE, fresh_policy, cause_classes, parsed_findings, evidence
        )
    allows_reheat = False
    invalid_policy = False
    try:
        allows_reheat = fresh_policy.allows_reheat(
            (FindingClass.REPETITION, FindingClass.NO_PROGRESS)
        )
    except (MemoryError, SystemExit):
        raise
    except Exception:  # noqa: BLE001 - reheat permission boundary is sanitized.
        invalid_policy = True
    if invalid_policy:
        _fail("invalid_run_policy")
    if allows_reheat:
        return _selection(
            Decision.REHEAT, fresh_policy, cause_classes, parsed_findings, evidence
        )
    if ladder.restart.permitted:
        return _selection(
            Decision.RESTART, fresh_policy, cause_classes, parsed_findings, evidence
        )
    decision = Decision.ESCALATE if ladder.escalate.permitted else Decision.STOP
    return _selection(decision, fresh_policy, cause_classes, parsed_findings, evidence)


def _validated_candidates(candidates: Any) -> tuple[BranchCandidate, ...]:
    if type(candidates) not in (list, tuple):
        _fail("invalid_branch_candidate")
    if len(candidates) > _MAX_CANDIDATES:
        _fail("cooling_item_limit")
    validated: list[BranchCandidate] = []
    seen_branch_ids: set[str] = set()
    invalid = False
    duplicate = False
    try:
        for candidate in candidates:
            if type(candidate) is not BranchCandidate:
                invalid = True
                break
            candidate.to_dict()
            if candidate.branch_id in seen_branch_ids:
                duplicate = True
                break
            seen_branch_ids.add(candidate.branch_id)
            validated.append(candidate)
    except (MemoryError, SystemExit):
        raise
    except Exception:  # noqa: BLE001 - candidate integrity boundary is sanitized.
        invalid = True
    if invalid:
        _fail("invalid_branch_candidate")
    if duplicate:
        _fail("duplicate_branch_id")
    return tuple(validated)


def select_cooling_branch(candidates: Any, policy: Any) -> str | None:
    """Return the sole strictly best safe evidence-backed branch, if any."""
    parsed_candidates = _validated_candidates(candidates)
    fresh_policy = _validated_policy(policy)
    allowed = set(
        fresh_policy.side_effect_rules.automatic_repeat_allowed_effect_classes
    )
    best_id: str | None = None
    best_evidence = -1
    tied = False
    for candidate in parsed_candidates:
        viable = (
            not candidate.rejected
            and bool(candidate.verified_evidence_refs)
            and candidate.within_budget
            and candidate.host_authorized
            and candidate.next_action_effect
            in (EffectClass.READ_ONLY, EffectClass.IDEMPOTENT_WRITE)
            and candidate.next_action_effect in allowed
        )
        if not viable:
            continue
        count = len(candidate.verified_evidence_refs)
        if count > best_evidence:
            best_id = candidate.branch_id
            best_evidence = count
            tied = False
        elif count == best_evidence:
            tied = True
    return None if best_id is None or tied else best_id
