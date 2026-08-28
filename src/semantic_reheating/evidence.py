"""Deterministic, schema-validated public recovery evidence records."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from hashlib import sha256
from types import MappingProxyType
from typing import Any, NoReturn

from .canonical import canonicalize_json
from .models import BudgetCounters, Decision, DecisionEnvelope, FindingClass
from .validation import validate_public_artifact


class EvidenceError(ValueError):
    """Sanitized failure from the deterministic evidence boundary."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__("Invalid evidence input")


def _fail(code: str) -> NoReturn:
    raise EvidenceError(code) from None


def _freeze(value: Any) -> Any:
    if type(value) is dict:
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if type(value) is list:
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if type(value) is tuple:
        return [_thaw(item) for item in value]
    return value


def _allocate(cls: type[Any], /, **field_values: Any) -> Any:
    if cls not in (RecoveryOutcome, EvidenceRecord):
        _fail("invalid_evidence_state")
    try:
        instance = object.__new__(cls)
        for descriptor in fields(cls):
            if descriptor.name not in field_values:
                _fail("invalid_evidence_state")
            object.__setattr__(instance, descriptor.name, field_values[descriptor.name])
        return instance
    except (MemoryError, SystemExit):
        raise
    except EvidenceError:
        raise
    except Exception:  # noqa: BLE001 - allocation state must not leak native errors.
        _fail("invalid_evidence_state")


def _source(kind: str, data: Any) -> dict[str, Any]:
    failure = False
    try:
        value = validate_public_artifact(kind, data)
    except (MemoryError, SystemExit):
        raise
    except Exception:  # noqa: BLE001 - public boundary must not leak native errors.
        failure = True
        value = None
    if failure or type(value) is not dict:
        _fail(f"invalid_{kind}")
    return value


def _public_source(kind: str, source: Any) -> dict[str, Any]:
    failure = False
    try:
        if type(source) is not MappingProxyType:
            failure = True
            value = None
        else:
            value = _thaw(source)
            if type(value) is not dict:
                failure = True
    except (MemoryError, SystemExit):
        raise
    except Exception:  # noqa: BLE001 - public boundary must not leak native errors.
        failure = True
        value = None
    if failure:
        _fail("invalid_evidence_state")
    return _source(kind, value)


@dataclass(frozen=True, slots=True)
class HostResult:
    status: str
    summary: str


@dataclass(frozen=True, slots=True)
class AcceptanceDelta:
    status: str
    summary: str


@dataclass(frozen=True, slots=True)
class StateDelta:
    observed: bool
    summary: str


@dataclass(frozen=True, slots=True)
class HostDenial:
    denied: bool
    reason_code: str | None


@dataclass(frozen=True, slots=True)
class EvidenceTrigger:
    finding_ids: tuple[str, ...]
    reason_code: str


@dataclass(frozen=True, slots=True, init=False)
class RecoveryOutcome:
    """Typed immutable view of a schema-valid, host-reported outcome."""

    contract_version: str
    run_id: str
    outcome_id: str
    instruction_id: str
    host_result: HostResult
    consumed_counters: BudgetCounters
    evidence_gained: tuple[str, ...]
    acceptance_delta: AcceptanceDelta
    state_delta: StateDelta
    error_class: str | None
    host_denial: HostDenial
    human_escalation: bool
    _source: Any = field(repr=False, compare=False, hash=False, default=None)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        _fail("validated_construction_required")

    @staticmethod
    def from_dict(data: Any) -> RecoveryOutcome:
        value = _source("recovery_outcome", data)
        host_result = value["host_result"]
        acceptance_delta = value["acceptance_delta"]
        state_delta = value["state_delta"]
        host_denial = value["host_denial"]
        return _allocate(
            RecoveryOutcome,
            contract_version=value["contract_version"],
            run_id=value["run_id"],
            outcome_id=value["outcome_id"],
            instruction_id=value["instruction_id"],
            host_result=HostResult(**host_result),
            consumed_counters=BudgetCounters.from_dict(value["consumed_counters"]),
            evidence_gained=tuple(value["evidence_gained"]),
            acceptance_delta=AcceptanceDelta(**acceptance_delta),
            state_delta=StateDelta(**state_delta),
            error_class=value["error_class"],
            host_denial=HostDenial(**host_denial),
            human_escalation=value["human_escalation"],
            _source=_freeze(value),
        )

    def to_dict(self) -> dict[str, Any]:
        invalid_state = False
        try:
            source = self._source
        except (MemoryError, SystemExit):
            raise
        except Exception:  # noqa: BLE001 - forged state must not leak native errors.
            invalid_state = True
            source = None
        if invalid_state:
            _fail("invalid_evidence_state")
        return _public_source("recovery_outcome", source)

    def __reduce__(self) -> tuple[Any, tuple[dict[str, Any]]]:
        return (RecoveryOutcome.from_dict, (self.to_dict(),))


@dataclass(frozen=True, slots=True, init=False)
class EvidenceRecord:
    """Typed immutable view of a deterministic recovery evidence record."""

    contract_version: str
    run_id: str
    evidence_id: str
    trigger: EvidenceTrigger
    chosen_policy: str | None
    actual_counters: BudgetCounters
    new_evidence_refs: tuple[str, ...]
    acceptance_delta: AcceptanceDelta
    repeated_side_effects_avoided: bool
    final_status: str
    _source: Any = field(repr=False, compare=False, hash=False, default=None)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        _fail("validated_construction_required")

    @staticmethod
    def from_dict(data: Any) -> EvidenceRecord:
        value = _source("evidence_record", data)
        trigger = value["trigger"]
        acceptance_delta = value["acceptance_delta"]
        return _allocate(
            EvidenceRecord,
            contract_version=value["contract_version"],
            run_id=value["run_id"],
            evidence_id=value["evidence_id"],
            trigger=EvidenceTrigger(
                finding_ids=tuple(trigger["finding_ids"]),
                reason_code=trigger["reason_code"],
            ),
            chosen_policy=value["chosen_policy"],
            actual_counters=BudgetCounters.from_dict(value["actual_counters"]),
            new_evidence_refs=tuple(value["new_evidence_refs"]),
            acceptance_delta=AcceptanceDelta(**acceptance_delta),
            repeated_side_effects_avoided=value["repeated_side_effects_avoided"],
            final_status=value["final_status"],
            _source=_freeze(value),
        )

    def to_dict(self) -> dict[str, Any]:
        invalid_state = False
        try:
            source = self._source
        except (MemoryError, SystemExit):
            raise
        except Exception:  # noqa: BLE001 - forged state must not leak native errors.
            invalid_state = True
            source = None
        if invalid_state:
            _fail("invalid_evidence_state")
        return _public_source("evidence_record", source)

    def __reduce__(self) -> tuple[Any, tuple[dict[str, Any]]]:
        return (EvidenceRecord.from_dict, (self.to_dict(),))


def _trigger(decision: DecisionEnvelope) -> EvidenceTrigger:
    finding_ids: list[str] = []
    seen: set[str] = set()
    matched = []
    for finding in decision.confidence.contributing_findings:
        if finding.matched is True:
            matched.append(finding)
            if finding.finding_id not in seen:
                seen.add(finding.finding_id)
                finding_ids.append(finding.finding_id)
    if not finding_ids:
        _fail("missing_trigger_findings")
    if any(finding.finding_class is FindingClass.BUDGET for finding in matched):
        reason_code = "budget_limit_reached"
    elif any(finding.finding_class is FindingClass.RISK for finding in matched):
        reason_code = "risk_detected"
    elif {finding.finding_class for finding in matched}.issuperset(
        {FindingClass.REPETITION, FindingClass.NO_PROGRESS}
    ):
        reason_code = "signals_agree"
    else:
        reason_code = "host_request"
    return EvidenceTrigger(tuple(finding_ids), reason_code)


def _final_status(decision: DecisionEnvelope, outcome: RecoveryOutcome) -> str:
    if outcome.human_escalation or outcome.host_result.status == "escalated":
        return "escalated"
    if outcome.host_denial.denied or outcome.host_result.status == "denied":
        return "blocked"
    if decision.decision is Decision.STOP or outcome.host_result.status == "failed":
        return "stopped"
    if (
        outcome.host_result.status == "completed"
        and outcome.acceptance_delta.status == "improved"
    ):
        return "recovered"
    return "continued"


def record_outcome(
    decision: DecisionEnvelope, outcome: RecoveryOutcome
) -> EvidenceRecord:
    """Build one deterministic evidence record without executing host actions."""

    if type(decision) is not DecisionEnvelope:
        _fail("invalid_decision_envelope")
    if type(outcome) is not RecoveryOutcome:
        _fail("invalid_recovery_outcome")
    try:
        decision_data = decision.to_dict()
        outcome_data = outcome.to_dict()
        fresh_decision = DecisionEnvelope.from_dict(decision_data)
        fresh_outcome = RecoveryOutcome.from_dict(outcome_data)
        if decision != fresh_decision:
            _fail("invalid_decision_envelope")
        if outcome != fresh_outcome:
            _fail("invalid_recovery_outcome")
        if fresh_decision.run_id != fresh_outcome.run_id:
            _fail("run_id_mismatch")
        trigger = _trigger(fresh_decision)
        source = {
            "contract_version": "1.0",
            "run_id": fresh_decision.run_id,
            "trigger": {
                "finding_ids": list(trigger.finding_ids),
                "reason_code": trigger.reason_code,
            },
            "chosen_policy": fresh_decision.recovery_policy,
            "actual_counters": fresh_outcome.consumed_counters.to_dict(),
            "new_evidence_refs": list(fresh_outcome.evidence_gained),
            "acceptance_delta": {
                "status": fresh_outcome.acceptance_delta.status,
                "summary": fresh_outcome.acceptance_delta.summary,
            },
            "repeated_side_effects_avoided": (
                (
                    fresh_decision.decision is Decision.STOP
                    and any(
                        finding.matched is True
                        and finding.finding_class is FindingClass.RISK
                        for finding in fresh_decision.confidence.contributing_findings
                    )
                )
                or (
                    fresh_outcome.host_denial.denied
                    and fresh_decision.constraints.no_non_idempotent_repeat
                )
            ),
            "final_status": _final_status(fresh_decision, fresh_outcome),
        }
        evidence_id = "evidence-" + sha256(canonicalize_json(source)).hexdigest()[:24]
        return EvidenceRecord.from_dict({"evidence_id": evidence_id, **source})
    except (MemoryError, SystemExit):
        raise
    except EvidenceError:
        raise
    except Exception:  # noqa: BLE001 - record assembly must not leak native errors.
        _fail("invalid_evidence_record")
