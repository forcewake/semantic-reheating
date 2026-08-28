"""Immutable, schema-first typed views of public controller artifacts."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields
from enum import Enum
from importlib import resources
from pathlib import Path
from types import MappingProxyType
from typing import Any

from jsonschema import Draft202012Validator

from .validation import (
    ContractValidationError,
    _check_contract_major,
    _ensure_json_value,
    load_public_json,
    validate_public_artifact,
    validate_run_policy,
)


class TraceKind(str, Enum):
    MESSAGE = "message"
    PLAN = "plan"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    STATE_OBSERVATION = "state_observation"
    ACCEPTANCE_CHECK = "acceptance_check"
    HANDOFF = "handoff"
    ERROR = "error"
    BUDGET = "budget"


class EffectClass(str, Enum):
    READ_ONLY = "read_only"
    IDEMPOTENT_WRITE = "idempotent_write"
    NON_IDEMPOTENT_WRITE = "non_idempotent_write"
    UNKNOWN = "unknown"


class FindingClass(str, Enum):
    REPETITION = "repetition"
    NO_PROGRESS = "no_progress"
    RISK = "risk"
    BUDGET = "budget"


class Decision(str, Enum):
    CONTINUE = "continue"
    NUDGE = "nudge"
    DIAGNOSE = "diagnose"
    REHEAT = "reheat"
    RESTART = "restart"
    ESCALATE = "escalate"
    STOP = "stop"


class ModelValidationError(ValueError):
    """Sanitized typed failure from a model input boundary."""

    def __init__(self, code: str, message: str = "Invalid model input") -> None:
        self.code = code
        super().__init__(message)


_TRACE_SCHEMA_PATH = "contracts/v1/trace-event.schema.json"
_TRACE_VALIDATOR: Draft202012Validator | None = None


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


def _allocate_model(cls: type[Any], /, **field_values: Any) -> Any:
    """Allocate an exact core model only after validating its public source."""
    if cls not in (TraceEvent, RunPolicy, DecisionEnvelope):
        raise ModelValidationError("invalid_model_state")
    try:
        model = object.__new__(cls)
        for descriptor in fields(cls):
            if descriptor.name not in field_values:
                raise ModelValidationError("invalid_model_state")
            object.__setattr__(model, descriptor.name, field_values[descriptor.name])
        return model
    except ModelValidationError:
        raise
    except Exception as error:
        raise ModelValidationError("invalid_model_state") from error


def _thaw_model_source(source: Any) -> dict[str, Any]:
    try:
        value = _thaw(source)
    except Exception as error:
        raise ModelValidationError("invalid_model_state") from error
    if type(value) is not dict:
        raise ModelValidationError("invalid_model_state")
    return value


def _model_input(data: Any) -> Any:
    try:
        if type(data) in (str, bytes, bytearray):
            return load_public_json(data)
        _ensure_json_value(data)
        return data
    except ContractValidationError as error:
        raise ModelValidationError(error.code) from error


def _trace_validator() -> Draft202012Validator:
    global _TRACE_VALIDATOR
    if _TRACE_VALIDATOR is None:
        try:
            schema_resource = resources.files("semantic_reheating").joinpath(
                _TRACE_SCHEMA_PATH
            )
            schema_bytes = (
                schema_resource.read_bytes()
                if schema_resource.is_file()
                else (
                    Path(__file__).resolve().parents[2]
                    / "contracts"
                    / "v1"
                    / "trace-event.schema.json"
                ).read_bytes()
            )
            schema = load_public_json(schema_bytes)
            Draft202012Validator.check_schema(schema)
            _TRACE_VALIDATOR = Draft202012Validator(schema)
        except ContractValidationError as error:
            raise ModelValidationError(error.code) from error
        except (OSError, ValueError) as error:
            raise ModelValidationError("invalid_contract_schema") from error
    return _TRACE_VALIDATOR


def _validate_trace(data: Any) -> dict[str, Any]:
    value = _model_input(data)
    try:
        _check_contract_major(value)
    except ContractValidationError as error:
        raise ModelValidationError(error.code) from error
    errors = sorted(
        _trace_validator().iter_errors(value),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        raise ModelValidationError("schema_validation_error")
    if type(value) is not dict:
        raise ModelValidationError("schema_validation_error")
    return value


@dataclass(frozen=True, slots=True)
class BudgetCounters:
    """The five public counters, inclusive of all execution modalities."""

    turns: int
    tool_calls: int
    tokens: int
    elapsed_seconds: int | float
    cost: int | float

    def __post_init__(self) -> None:
        self._validate_values(
            self.turns,
            self.tool_calls,
            self.tokens,
            self.elapsed_seconds,
            self.cost,
        )

    @staticmethod
    def _validate_values(
        turns: Any,
        tool_calls: Any,
        tokens: Any,
        elapsed_seconds: Any,
        cost: Any,
    ) -> None:
        if any(
            type(value) is not int or value < 0 for value in (turns, tool_calls, tokens)
        ):
            raise ModelValidationError("invalid_budget_counters")
        if any(
            not (
                (type(value) is int and value >= 0)
                or (type(value) is float and math.isfinite(value) and value >= 0)
            )
            for value in (elapsed_seconds, cost)
        ):
            raise ModelValidationError("invalid_budget_counters")

    @staticmethod
    def from_dict(data: Any) -> BudgetCounters:
        try:
            _ensure_json_value(data)
        except ContractValidationError as error:
            raise ModelValidationError(error.code) from error
        except Exception as error:
            raise ModelValidationError("non_json_data") from error
        if type(data) is not dict:
            raise ModelValidationError("non_json_data")
        if set(data) != {"turns", "tool_calls", "tokens", "elapsed_seconds", "cost"}:
            raise ModelValidationError("invalid_budget_counters")
        try:
            return BudgetCounters(
                turns=data["turns"],
                tool_calls=data["tool_calls"],
                tokens=data["tokens"],
                elapsed_seconds=data["elapsed_seconds"],
                cost=data["cost"],
            )
        except ModelValidationError:
            raise
        except Exception as error:
            raise ModelValidationError("invalid_budget_counters") from error

    def to_dict(self) -> dict[str, int | float]:
        BudgetCounters(
            turns=self.turns,
            tool_calls=self.tool_calls,
            tokens=self.tokens,
            elapsed_seconds=self.elapsed_seconds,
            cost=self.cost,
        )
        return {
            "turns": self.turns,
            "tool_calls": self.tool_calls,
            "tokens": self.tokens,
            "elapsed_seconds": self.elapsed_seconds,
            "cost": self.cost,
        }


@dataclass(frozen=True, slots=True, init=False)
class TraceEvent:
    contract_version: str
    run_id: str
    event_id: str
    sequence: int
    kind: TraceKind
    actor: str
    effect_class: EffectClass
    payload: Any | None = field(default=None, repr=False, hash=False)
    payload_ref: str | None = None
    payload_digest: str | None = None
    parent_event_id: str | None = None
    state_fingerprint: str | None = None
    error_fingerprint: str | None = None
    acceptance_delta: str | None = None
    evidence_refs: tuple[str, ...] | None = None
    budget_counters: BudgetCounters | None = None
    expected_state_change: bool | None = None
    _source: Any = field(repr=False, compare=False, hash=False, default=None)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise ModelValidationError("validated_construction_required")

    @staticmethod
    def from_dict(data: Any) -> TraceEvent:
        value = _validate_trace(data)
        counters = value.get("budget_counters")
        return _allocate_model(
            TraceEvent,
            contract_version=value["contract_version"],
            run_id=value["run_id"],
            event_id=value["event_id"],
            sequence=value["sequence"],
            kind=TraceKind(value["kind"]),
            actor=value["actor"],
            effect_class=EffectClass(value["effect_class"]),
            payload=_freeze(value["payload"]) if "payload" in value else None,
            payload_ref=value.get("payload_ref"),
            payload_digest=value.get("payload_digest"),
            parent_event_id=value.get("parent_event_id"),
            state_fingerprint=value.get("state_fingerprint"),
            error_fingerprint=value.get("error_fingerprint"),
            acceptance_delta=value.get("acceptance_delta"),
            evidence_refs=tuple(value["evidence_refs"])
            if "evidence_refs" in value
            else None,
            budget_counters=BudgetCounters.from_dict(counters)
            if counters is not None
            else None,
            expected_state_change=value.get("expected_state_change"),
            _source=_freeze(value),
        )

    def to_dict(self) -> dict[str, Any]:
        try:
            source = self._source
        except Exception as error:
            raise ModelValidationError("invalid_model_state") from error
        if type(source) is not MappingProxyType:
            raise ModelValidationError("invalid_model_state")
        return _validate_trace(_thaw_model_source(source))

    def __reduce__(self) -> tuple[Any, tuple[dict[str, Any]]]:
        return (TraceEvent.from_dict, (self.to_dict(),))


def parse_trace(events: Any) -> tuple[TraceEvent, ...]:
    """Parse one contiguous public trace without changing event order."""
    if type(events) not in (list, tuple):
        raise ModelValidationError("non_json_data")
    parsed_items: list[TraceEvent] = []
    for event in events:
        if type(event) is TraceEvent:
            try:
                parsed_items.append(TraceEvent.from_dict(event.to_dict()))
            except ModelValidationError:
                raise
            except Exception as error:
                raise ModelValidationError("invalid_model_state") from error
        else:
            parsed_items.append(TraceEvent.from_dict(event))
    parsed = tuple(parsed_items)
    for expected, event in enumerate(parsed, start=1):
        if event.sequence != expected:
            raise ModelValidationError("sequence_gap")
    if parsed and any(event.run_id != parsed[0].run_id for event in parsed[1:]):
        raise ModelValidationError("run_id_mismatch")
    return parsed


def _validated_public(kind: str, data: Any) -> dict[str, Any]:
    try:
        value = validate_public_artifact(kind, data)
    except ContractValidationError as error:
        raise ModelValidationError(error.code) from error
    if type(value) is not dict:
        raise ModelValidationError("schema_validation_error")
    return value


def _validated_run_policy(data: Any) -> dict[str, Any]:
    try:
        value = validate_run_policy(data)
    except ContractValidationError as error:
        failure_code = error.code
    except Exception:  # noqa: BLE001 - public/model boundary must not leak native errors
        failure_code = "unsafe_policy"
    else:
        if type(value) is dict:
            return value
        failure_code = "unsafe_policy"
    raise ModelValidationError(failure_code)


@dataclass(frozen=True, slots=True)
class DetectorWindows:
    repetition_events: int
    no_progress_events: int


@dataclass(frozen=True, slots=True)
class DetectorThresholds:
    repetition_score: int | float
    no_progress_score: int | float
    risk_score: int | float
    budget_score: int | float


@dataclass(frozen=True, slots=True)
class DetectorWeights:
    repetition: int | float
    no_progress: int | float
    risk: int | float
    budget: int | float


@dataclass(frozen=True, slots=True)
class SemanticDetector:
    enabled: bool
    metered: bool
    weight: int | float
    required: bool
    can_relax_hard_stops: bool


@dataclass(frozen=True, slots=True)
class Detectors:
    windows: DetectorWindows
    thresholds: DetectorThresholds
    weights: DetectorWeights
    semantic_detector: SemanticDetector | None


@dataclass(frozen=True, slots=True)
class AgreeingSignals:
    required_classes: tuple[FindingClass, ...]
    minimum_count: int
    budget_can_substitute: bool


@dataclass(frozen=True, slots=True)
class RecoveryStagePermission:
    permitted: bool
    requires_host_action: bool


@dataclass(frozen=True, slots=True)
class RecoveryLadder:
    nudge: RecoveryStagePermission
    diagnose: RecoveryStagePermission
    reheat: RecoveryStagePermission
    restart: RecoveryStagePermission
    escalate: RecoveryStagePermission
    stop: RecoveryStagePermission


@dataclass(frozen=True, slots=True)
class PolicyBudgets:
    per_intervention: BudgetCounters
    whole_run: BudgetCounters


@dataclass(frozen=True, slots=True)
class SideEffectRules:
    automatic_repeat_allowed_effect_classes: tuple[EffectClass, ...]
    automatic_unconfirmed_non_idempotent_repeat: bool
    unknown_treated_as_repeatable: bool


@dataclass(frozen=True, slots=True)
class CoolingConditions:
    minimum_elapsed_seconds: int | float
    require_new_evidence: bool
    minimum_acceptance_gain: int | float


def _budget(data: Mapping[str, Any]) -> BudgetCounters:
    return BudgetCounters.from_dict(data)


def _stage(data: Mapping[str, Any]) -> RecoveryStagePermission:
    return RecoveryStagePermission(data["permitted"], data["requires_host_action"])


@dataclass(frozen=True, slots=True, init=False)
class RunPolicy:
    contract_version: str
    policy_id: str
    detectors: Detectors
    agreeing_signals: AgreeingSignals
    recovery_ladder: RecoveryLadder
    budgets: PolicyBudgets
    max_recovery_episodes: int
    max_reentry_depth: int
    side_effect_rules: SideEffectRules
    cooling_conditions: CoolingConditions
    _source: Any = field(repr=False, compare=False, hash=False, default=None)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise ModelValidationError("validated_construction_required")

    @staticmethod
    def from_dict(data: Any) -> RunPolicy:
        value = _validated_run_policy(data)
        detector_data = value["detectors"]
        windows = detector_data["windows"]
        thresholds = detector_data["thresholds"]
        weights = detector_data["weights"]
        semantic = detector_data.get("semantic_detector")
        detectors = Detectors(
            DetectorWindows(**windows),
            DetectorThresholds(**thresholds),
            DetectorWeights(**weights),
            SemanticDetector(**semantic) if semantic is not None else None,
        )
        signals = value["agreeing_signals"]
        ladder = value["recovery_ladder"]
        budgets = value["budgets"]
        rules = value["side_effect_rules"]
        cooling = value["cooling_conditions"]
        return _allocate_model(
            RunPolicy,
            contract_version=value["contract_version"],
            policy_id=value["policy_id"],
            detectors=detectors,
            agreeing_signals=AgreeingSignals(
                tuple(FindingClass(item) for item in signals["required_classes"]),
                signals["minimum_count"],
                signals["budget_can_substitute"],
            ),
            recovery_ladder=RecoveryLadder(
                **{
                    name: _stage(ladder[name])
                    for name in (
                        "nudge",
                        "diagnose",
                        "reheat",
                        "restart",
                        "escalate",
                        "stop",
                    )
                }
            ),
            budgets=PolicyBudgets(
                _budget(budgets["per_intervention"]), _budget(budgets["whole_run"])
            ),
            max_recovery_episodes=value["max_recovery_episodes"],
            max_reentry_depth=value["max_reentry_depth"],
            side_effect_rules=SideEffectRules(
                tuple(
                    EffectClass(item)
                    for item in rules["automatic_repeat_allowed_effect_classes"]
                ),
                rules["automatic_unconfirmed_non_idempotent_repeat"],
                rules["unknown_treated_as_repeatable"],
            ),
            cooling_conditions=CoolingConditions(**cooling),
            _source=_freeze(value),
        )

    def to_dict(self) -> dict[str, Any]:
        try:
            source = self._source
        except AttributeError:
            source = None
        if type(source) is not MappingProxyType:
            raise ModelValidationError("invalid_model_state")
        try:
            value = _thaw_model_source(source)
        except ModelValidationError:
            value = None
        if type(value) is not dict:
            raise ModelValidationError("unsafe_policy")
        return _validated_run_policy(value)

    def allows_reheat(self, finding_classes: Sequence[FindingClass]) -> bool:
        """Allow only the fixed independent repetition/no-progress gate."""
        policy = self.to_dict()
        if type(finding_classes) not in (list, tuple):
            raise ModelValidationError("invalid_finding_classes") from None
        if any(type(finding) is not FindingClass for finding in finding_classes):
            raise ModelValidationError("invalid_finding_classes") from None
        observed = set(finding_classes)
        return (
            policy["recovery_ladder"]["reheat"]["permitted"] is True
            and policy["max_recovery_episodes"] > 0
            and FindingClass.REPETITION in observed
            and FindingClass.NO_PROGRESS in observed
        )

    def __reduce__(self) -> tuple[Any, tuple[dict[str, Any]]]:
        return (RunPolicy.from_dict, (self.to_dict(),))


@dataclass(frozen=True, slots=True)
class DecisionConstraints:
    must_preserve_evidence: bool
    no_non_idempotent_repeat: bool
    require_host_confirmation: bool
    allowed_effect_classes: tuple[EffectClass, ...]


@dataclass(frozen=True, slots=True)
class ContributingFinding:
    finding_id: str
    finding_class: FindingClass
    matched: bool
    score: int | float
    weight: int | float
    weighted_score: int | float


@dataclass(frozen=True, slots=True)
class DecisionConfidence:
    score: int | float
    contributing_findings: tuple[ContributingFinding, ...]


@dataclass(frozen=True, slots=True, init=False)
class DecisionEnvelope:
    contract_version: str
    run_id: str
    decision_id: str
    decision: Decision
    reason_codes: tuple[str, ...]
    evidence_event_ids: tuple[str, ...]
    recovery_policy: str | None
    recovery_budget: BudgetCounters
    constraints: DecisionConstraints
    cooling_conditions: CoolingConditions
    confidence: DecisionConfidence
    requires_host_action: bool
    human_summary: str
    _source: Any = field(repr=False, compare=False, hash=False, default=None)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise ModelValidationError("validated_construction_required")

    @staticmethod
    def from_dict(data: Any) -> DecisionEnvelope:
        value = _validated_public("decision_envelope", data)
        constraints = value["constraints"]
        confidence = value["confidence"]
        return _allocate_model(
            DecisionEnvelope,
            contract_version=value["contract_version"],
            run_id=value["run_id"],
            decision_id=value["decision_id"],
            decision=Decision(value["decision"]),
            reason_codes=tuple(value["reason_codes"]),
            evidence_event_ids=tuple(value["evidence_event_ids"]),
            recovery_policy=value["recovery_policy"],
            recovery_budget=_budget(value["recovery_budget"]),
            constraints=DecisionConstraints(
                must_preserve_evidence=constraints["must_preserve_evidence"],
                no_non_idempotent_repeat=constraints["no_non_idempotent_repeat"],
                require_host_confirmation=constraints["require_host_confirmation"],
                allowed_effect_classes=tuple(
                    EffectClass(item) for item in constraints["allowed_effect_classes"]
                ),
            ),
            cooling_conditions=CoolingConditions(**value["cooling_conditions"]),
            confidence=DecisionConfidence(
                score=confidence["score"],
                contributing_findings=tuple(
                    ContributingFinding(
                        finding_id=item["finding_id"],
                        finding_class=FindingClass(item["finding_class"]),
                        matched=item["matched"],
                        score=item["score"],
                        weight=item["weight"],
                        weighted_score=item["weighted_score"],
                    )
                    for item in confidence["contributing_findings"]
                ),
            ),
            requires_host_action=value["requires_host_action"],
            human_summary=value["human_summary"],
            _source=_freeze(value),
        )

    def to_dict(self) -> dict[str, Any]:
        try:
            source = self._source
        except Exception as error:
            raise ModelValidationError("invalid_model_state") from error
        if type(source) is not MappingProxyType:
            raise ModelValidationError("invalid_model_state")
        return _validated_public("decision_envelope", _thaw_model_source(source))

    def __reduce__(self) -> tuple[Any, tuple[dict[str, Any]]]:
        return (DecisionEnvelope.from_dict, (self.to_dict(),))
