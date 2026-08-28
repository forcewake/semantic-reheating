"""Deterministic aggregation of detector findings into advisory decisions."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from hashlib import sha256
from itertools import pairwise
from math import isclose
from re import compile as re_compile
from types import MappingProxyType
from typing import Any, NoReturn, Protocol

from .canonical import action_fingerprint, canonicalize_json
from .detectors.acceptance_stall import detect_acceptance_stall
from .detectors.budget_burn import detect_budget_burn
from .detectors.cycle import detect_cycle
from .detectors.exact_repetition import detect_exact_repetition
from .detectors.repeated_error import detect_repeated_error
from .detectors.unchanged_state import detect_unchanged_state
from .diagnosis import diagnose
from .models import (
    Decision,
    DecisionEnvelope,
    EffectClass,
    FindingClass,
    RunPolicy,
    TraceEvent,
    TraceKind,
)
from .policies import (
    recovery_gaps_for_causes,
    recovery_instruction_source,
    select_recovery_policy,
)
from .validation import validate_public_artifact

_MAX_ITEMS = 10_000
_MAX_CONTRIBUTING_FINDINGS = 100
_SHA256_HEXDIGEST = re_compile(r"^[0-9a-f]{64}$")
_DETECTORS = (
    detect_exact_repetition,
    detect_cycle,
    detect_repeated_error,
    detect_unchanged_state,
    detect_acceptance_stall,
    detect_budget_burn,
)
_SUMMARIES = {
    Decision.CONTINUE: "Continue with deterministic monitoring.",
    Decision.NUDGE: "Issue the bounded advisory nudge.",
    Decision.DIAGNOSE: "Perform the bounded diagnostic review.",
    Decision.REHEAT: "Perform the bounded recovery analysis.",
    Decision.RESTART: "Request the bounded recovery restart.",
    Decision.ESCALATE: "Request host action for the bounded escalation.",
    Decision.STOP: "Stop automated activity and preserve evidence.",
}


class ControllerError(ValueError):
    """Sanitized failure from the public controller boundary."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__("Invalid controller input")


class SemanticDetector(Protocol):
    """Injected pure detector returning exactly one detector-finding mapping."""

    def detect(
        self, trace: tuple[TraceEvent, ...], policy: RunPolicy
    ) -> Mapping[str, Any]: ...


def _freeze_instruction(value: Any) -> Any:
    if type(value) is dict:
        return MappingProxyType(
            {key: _freeze_instruction(item) for key, item in value.items()}
        )
    if type(value) is list:
        return tuple(_freeze_instruction(item) for item in value)
    return value


def _thaw_instruction(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_instruction(item) for key, item in value.items()}
    if type(value) is tuple:
        return [_thaw_instruction(item) for item in value]
    return value


@dataclass(frozen=True, slots=True, init=False)
class RecoveryInstruction:
    """Strict portable advisory instruction; it neither executes nor grants authority."""

    _source: Any = field(repr=False, compare=False, hash=False, default=None)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise ControllerError("validated_construction_required")

    @staticmethod
    def from_dict(data: Any) -> RecoveryInstruction:
        try:
            source = validate_public_artifact("recovery_instruction", data)
            if type(source) is not dict:
                _fail("invalid_recovery_instruction")
            instance = object.__new__(RecoveryInstruction)
            object.__setattr__(instance, "_source", _freeze_instruction(source))
            return instance
        except (MemoryError, SystemExit, ControllerError):
            raise
        except Exception:  # noqa: BLE001 - public instruction boundary is sanitized.
            _fail("invalid_recovery_instruction")

    def to_dict(self) -> dict[str, Any]:
        try:
            if type(self._source) is not MappingProxyType:
                _fail("invalid_recovery_instruction")
            source = _thaw_instruction(self._source)
            fresh = validate_public_artifact("recovery_instruction", source)
            if type(fresh) is not dict:
                _fail("invalid_recovery_instruction")
            return fresh
        except (MemoryError, SystemExit, ControllerError):
            raise
        except Exception:  # noqa: BLE001 - public instruction boundary is sanitized.
            _fail("invalid_recovery_instruction")

    def __reduce__(self) -> tuple[Any, tuple[dict[str, Any]]]:
        return (RecoveryInstruction.from_dict, (self.to_dict(),))


def _fail(code: str) -> NoReturn:
    raise ControllerError(code) from None


def _validated_inputs(
    trace: Any, policy: Any
) -> tuple[tuple[TraceEvent, ...], RunPolicy]:
    if type(trace) not in (list, tuple):
        _fail("invalid_trace")
    if not trace:
        _fail("empty_trace")
    if len(trace) > _MAX_ITEMS:
        _fail("trace_item_limit")
    parsed: list[TraceEvent] = []
    try:
        for event in trace:
            if type(event) is not TraceEvent:
                _fail("invalid_trace_event")
            fresh = TraceEvent.from_dict(event.to_dict())
            if type(fresh) is not TraceEvent:
                _fail("invalid_trace_event")
            parsed.append(fresh)
    except (MemoryError, SystemExit, ControllerError):
        raise
    except Exception:  # noqa: BLE001 - public trace boundary is sanitized.
        _fail("invalid_trace_event")
    if len({event.event_id for event in parsed}) != len(parsed):
        _fail("duplicate_event_id")
    if any(event.run_id != parsed[0].run_id for event in parsed[1:]):
        _fail("run_id_mismatch")
    if any(
        current.sequence != previous.sequence + 1
        for previous, current in pairwise(parsed)
    ):
        _fail("sequence_gap")
    if type(policy) is not RunPolicy:
        _fail("invalid_run_policy")
    try:
        fresh_policy = RunPolicy.from_dict(policy.to_dict())
    except (MemoryError, SystemExit):
        raise
    except Exception:  # noqa: BLE001 - public policy boundary is sanitized.
        _fail("invalid_run_policy")
    if type(fresh_policy) is not RunPolicy:
        _fail("invalid_run_policy")
    return tuple(parsed), fresh_policy


def _validated_detector_findings(
    findings: Sequence[Any], run_id: str
) -> tuple[dict[str, Any], ...]:
    if len(findings) > _MAX_ITEMS:
        _fail("finding_item_limit")
    parsed: list[dict[str, Any]] = []
    try:
        for finding in findings:
            if type(finding) is not dict:
                _fail("invalid_detector_finding")
            fresh = validate_public_artifact("detector_finding", finding)
            if type(fresh) is not dict or fresh["run_id"] != run_id:
                _fail("invalid_detector_finding")
            parsed.append(fresh)
    except (MemoryError, SystemExit, ControllerError):
        raise
    except Exception:  # noqa: BLE001 - public finding boundary is sanitized.
        _fail("invalid_detector_finding")
    if len({finding["finding_id"] for finding in parsed}) != len(parsed):
        _fail("duplicate_finding_id")
    return tuple(parsed)


def _synthetic_finding(
    *,
    detector_name: str,
    finding_class: str,
    run_id: str,
    event_ids: tuple[str, ...],
    reason_code: str,
    explanation: str,
) -> dict[str, Any]:
    try:
        digest = sha256(
            canonicalize_json(
                {
                    "detector_name": detector_name,
                    "finding_class": finding_class,
                    "run_id": run_id,
                    "event_ids": list(event_ids),
                    "reason_code": reason_code,
                }
            )
        ).hexdigest()
        if _SHA256_HEXDIGEST.fullmatch(digest) is None:
            _fail("invalid_synthetic_finding")
        finding = {
            "contract_version": "1.0",
            "run_id": run_id,
            "finding_id": f"{detector_name.replace('_', '-')}-{digest}",
            "detector_name": detector_name,
            "detector_version": "1.0",
            "matched": True,
            "score": 1.0,
            "finding_class": finding_class,
            "event_ids": list(event_ids),
            "reason_code": reason_code,
            "explanation": explanation,
            "availability": {
                "status": "available",
                "notice": "Deterministic hard-stop detector completed with redacted evidence only.",
            },
        }
        validated = validate_public_artifact("detector_finding", finding)
        if type(validated) is not dict:
            _fail("invalid_synthetic_finding")
        return validated
    except (MemoryError, SystemExit, ControllerError):
        raise
    except Exception:  # noqa: BLE001 - synthetic finding boundary is sanitized.
        _fail("invalid_synthetic_finding")


def _hard_budget_finding(
    trace: tuple[TraceEvent, ...], policy: RunPolicy
) -> dict[str, Any] | None:
    latest = next(
        (event for event in reversed(trace) if event.budget_counters is not None), None
    )
    if latest is None:
        return None
    counters = latest.budget_counters
    limit = policy.budgets.whole_run
    if not any(
        getattr(counters, field) >= getattr(limit, field)
        for field in ("turns", "tool_calls", "tokens", "elapsed_seconds", "cost")
    ):
        return None
    return _synthetic_finding(
        detector_name="hard_budget",
        finding_class="budget",
        run_id=latest.run_id,
        event_ids=(latest.event_id,),
        reason_code="budget_limit_reached",
        explanation="A declared whole-run budget limit was reached.",
    )


def _call_identity(event: TraceEvent) -> tuple[str, str] | None:
    try:
        source = event.to_dict()
        declared = source.get("payload_digest")
        if type(declared) is str and declared:
            return "declared_digest", declared
        if "payload" not in source:
            return None
        return "payload", action_fingerprint(source["payload"]).digest
    except (MemoryError, SystemExit):
        raise
    except Exception:  # noqa: BLE001 - payload identity boundary is sanitized.
        _fail("invalid_payload_identity")


def _hard_risk_finding(
    trace: tuple[TraceEvent, ...], policy: RunPolicy
) -> dict[str, Any] | None:
    seen: dict[tuple[str, str], TraceEvent] = {}
    for event in trace:
        if event.kind is not TraceKind.TOOL_CALL:
            continue
        identity = _call_identity(event)
        if identity is None:
            continue
        previous = seen.get(identity)
        if previous is None:
            seen[identity] = event
            continue
        effects = {previous.effect_class, event.effect_class}
        risky = EffectClass.UNKNOWN in effects or (
            EffectClass.NON_IDEMPOTENT_WRITE in effects
            and not policy.side_effect_rules.automatic_unconfirmed_non_idempotent_repeat
        )
        if risky:
            return _synthetic_finding(
                detector_name="repeated_risky_call",
                finding_class="risk",
                run_id=event.run_id,
                event_ids=(previous.event_id, event.event_id),
                reason_code="risk_detected",
                explanation="Equivalent unsafe tool calls were repeated without confirmation.",
            )
    return None


def _weighted_contributions(
    findings: tuple[dict[str, Any], ...],
    policy: RunPolicy,
    semantic_finding_ids: frozenset[str] = frozenset(),
) -> tuple[list[dict[str, Any]], dict[str, float]]:
    weights = {
        "repetition": float(policy.detectors.weights.repetition),
        "no_progress": float(policy.detectors.weights.no_progress),
        "risk": float(policy.detectors.weights.risk),
        "budget": float(policy.detectors.weights.budget),
    }
    contributions: list[dict[str, Any]] = []
    class_scores = {name: 0.0 for name in weights}
    for finding in findings:
        if finding["matched"] is not True:
            continue
        finding_class = finding["finding_class"]
        weight = (
            float(policy.detectors.semantic_detector.weight)
            if finding["finding_id"] in semantic_finding_ids
            and policy.detectors.semantic_detector is not None
            else weights[finding_class]
        )
        score = float(finding["score"])
        weighted_score = min(1.0, max(0.0, weight * score))
        contributions.append(
            {
                "finding_id": finding["finding_id"],
                "finding_class": finding_class,
                "matched": True,
                "score": score,
                "weight": weight,
                "weighted_score": weighted_score,
            }
        )
        class_scores[finding_class] = max(class_scores[finding_class], weighted_score)
    if len(contributions) > _MAX_CONTRIBUTING_FINDINGS:
        _fail("contributing_finding_limit")
    return contributions, class_scores


def _envelope_source(
    trace: tuple[TraceEvent, ...],
    policy: RunPolicy,
    findings: tuple[dict[str, Any], ...],
    detector_notices: tuple[dict[str, str], ...] = (),
    semantic_finding_ids: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    try:
        diagnosis = diagnose(trace, findings)
        selection = select_recovery_policy(diagnosis, findings, policy)
        contributions, class_scores = _weighted_contributions(
            findings, policy, semantic_finding_ids
        )
        hard_stop = any(
            finding["matched"] is True
            and finding["finding_class"] in {"risk", "budget"}
            for finding in findings
        )
        confidence = (
            1.0
            if selection.decision is Decision.STOP and hard_stop
            else min(class_scores["repetition"], class_scores["no_progress"])
        )
        allowed = [
            effect.value
            for effect in policy.side_effect_rules.automatic_repeat_allowed_effect_classes
            if effect in (EffectClass.READ_ONLY, EffectClass.IDEMPOTENT_WRITE)
        ]
        summary = _SUMMARIES[selection.decision]
        basis = {
            "contract_version": "1.0",
            "run_id": trace[0].run_id,
            "decision": selection.decision.value,
            "reason_codes": list(selection.reason_codes),
            "evidence_event_ids": list(selection.evidence_event_ids),
            "diagnosed_gaps": recovery_gaps_for_causes(diagnosis.cause_classes),
            "rejected_hypothesis_refs": list(diagnosis.rejected_hypothesis_refs),
            "detector_notices": [dict(item) for item in detector_notices],
            "recovery_policy": (
                selection.recovery_policy.value
                if selection.recovery_policy is not None
                else None
            ),
            "recovery_budget": policy.budgets.per_intervention.to_dict(),
            "constraints": {
                "must_preserve_evidence": True,
                "no_non_idempotent_repeat": True,
                "require_host_confirmation": selection.requires_host_action,
                "allowed_effect_classes": allowed,
            },
            "cooling_conditions": {
                "minimum_elapsed_seconds": policy.cooling_conditions.minimum_elapsed_seconds,
                "require_new_evidence": policy.cooling_conditions.require_new_evidence,
                "minimum_acceptance_gain": policy.cooling_conditions.minimum_acceptance_gain,
            },
            "confidence": {
                "score": confidence,
                "contributing_findings": contributions,
            },
            "requires_host_action": selection.requires_host_action,
            "human_summary": summary,
        }
        digest = sha256(canonicalize_json(basis)).hexdigest()
        if _SHA256_HEXDIGEST.fullmatch(digest) is None:
            _fail("invalid_decision_digest")
        return {"decision_id": f"decision-{digest[:24]}", **basis}
    except (MemoryError, SystemExit, ControllerError):
        raise
    except Exception:  # noqa: BLE001 - envelope assembly is sanitized.
        _fail("invalid_controller_assembly")


def _semantic_finding(
    trace: tuple[TraceEvent, ...], policy: RunPolicy, detector: Any
) -> tuple[dict[str, Any] | None, tuple[dict[str, str], ...]]:
    """Call the injected detector once; any optional failure is a structured notice."""
    semantic = policy.detectors.semantic_detector
    if semantic is None or not semantic.enabled:
        return None, ()
    if detector is None:
        if semantic.required:
            _fail("required_detector_unavailable")
        return None, (
            (
                {
                    "detector_name": "semantic_detector",
                    "status": "unavailable",
                    "notice": "Semantic detector unavailable; deterministic analysis continued.",
                }
            ),
        )
    try:
        result = detector.detect(trace, policy)
        if type(result) is not dict:
            _fail("invalid_semantic_detector_finding")
        finding = _validated_detector_findings((result,), trace[0].run_id)[0]
        if finding["finding_class"] not in {"repetition", "no_progress"}:
            _fail("invalid_semantic_detector_finding")
        availability = finding["availability"]
        if availability["status"] != "available":
            if semantic.required:
                _fail("required_detector_unavailable")
            status = availability["status"]
            notice = (
                "Semantic detector degraded; deterministic analysis continued."
                if status == "degraded"
                else "Semantic detector unavailable; deterministic analysis continued."
            )
            return None, (
                (
                    {
                        "detector_name": finding["detector_name"],
                        "status": status,
                        "notice": notice,
                    }
                ),
            )
        return finding, ()
    except (MemoryError, SystemExit, ControllerError):
        raise
    except Exception:  # noqa: BLE001 - optional detector boundary is sanitized.
        if semantic.required:
            _fail("required_detector_unavailable")
        return None, (
            (
                {
                    "detector_name": "semantic_detector",
                    "status": "degraded",
                    "notice": "Semantic detector degraded; deterministic analysis continued.",
                }
            ),
        )


def analyze(
    trace: Sequence[TraceEvent], policy: RunPolicy, *, semantic_detector: Any = None
) -> DecisionEnvelope:
    """Purely aggregate deterministic findings plus one optional injected finding.

    A host reports a new trace episode; stateless analysis needs independent
    detector support IDs before it can produce a different reheat decision ID.
    """
    parsed_trace, parsed_policy = _validated_inputs(trace, policy)
    semantic_finding, detector_notices = _semantic_finding(
        parsed_trace, parsed_policy, semantic_detector
    )
    raw_findings: list[Any] = []
    try:
        for detector in _DETECTORS:
            raw_findings.append(detector(parsed_trace, parsed_policy))
    except (MemoryError, SystemExit):
        raise
    except Exception:  # noqa: BLE001 - detector boundary is sanitized.
        _fail("detector_execution_failed")
    findings = list(_validated_detector_findings(raw_findings, parsed_trace[0].run_id))
    hard_budget = _hard_budget_finding(parsed_trace, parsed_policy)
    hard_risk = _hard_risk_finding(parsed_trace, parsed_policy)
    if hard_budget is not None:
        findings.append(hard_budget)
    if hard_risk is not None:
        findings.append(hard_risk)
    semantic_finding_ids: frozenset[str] = frozenset()
    if semantic_finding is not None:
        findings.append(semantic_finding)
        semantic_finding_ids = frozenset((semantic_finding["finding_id"],))
    validated_findings = _validated_detector_findings(findings, parsed_trace[0].run_id)
    source = _envelope_source(
        parsed_trace,
        parsed_policy,
        validated_findings,
        detector_notices,
        semantic_finding_ids,
    )
    try:
        envelope = DecisionEnvelope.from_dict(source)
    except (MemoryError, SystemExit):
        raise
    except Exception:  # noqa: BLE001 - final public envelope boundary is sanitized.
        _fail("invalid_decision_envelope")
    if type(envelope) is not DecisionEnvelope:
        _fail("invalid_decision_envelope")
    return envelope


def build_recovery_instruction(
    decision: DecisionEnvelope,
) -> RecoveryInstruction | None:
    """Build a portable, advisory instruction from a serialized decision only.

    The host owns execution and reports a new independent trace episode for any
    later analysis; this function never calls tools, providers, or the host.
    """
    if type(decision) is not DecisionEnvelope:
        _fail("invalid_recovery_decision")
    try:
        fresh = DecisionEnvelope.from_dict(decision.to_dict())
        if decision != fresh:
            _fail("invalid_recovery_decision")
    except (MemoryError, SystemExit):
        raise
    except Exception:  # noqa: BLE001 - typed decision boundary is sanitized.
        _fail("invalid_recovery_decision")
    if fresh.decision is not Decision.REHEAT:
        return None
    try:
        source = fresh.to_dict()
        _validate_recovery_decision(fresh, source)
        instruction_source = recovery_instruction_source(
            run_id=fresh.run_id,
            diagnosed_gaps=[
                {"kind": gap.kind, "description": gap.description}
                for gap in fresh.diagnosed_gaps
            ],
            recovery_budget=fresh.recovery_budget.to_dict(),
            evidence_refs=list(fresh.evidence_event_ids),
            rejected_hypothesis_refs=list(fresh.rejected_hypothesis_refs),
            cooling_conditions={
                "minimum_elapsed_seconds": fresh.cooling_conditions.minimum_elapsed_seconds,
                "require_new_evidence": fresh.cooling_conditions.require_new_evidence,
                "minimum_acceptance_gain": fresh.cooling_conditions.minimum_acceptance_gain,
            },
        )
        return RecoveryInstruction.from_dict(instruction_source)
    except (MemoryError, SystemExit, ControllerError):
        raise
    except Exception:  # noqa: BLE001 - instruction assembly is sanitized.
        _fail("invalid_recovery_decision")


def _validate_recovery_decision(
    fresh: DecisionEnvelope, source: dict[str, Any]
) -> None:
    expected_id = (
        "decision-"
        + sha256(
            canonicalize_json(
                {key: value for key, value in source.items() if key != "decision_id"}
            )
        ).hexdigest()[:24]
    )
    expected_reasons = (
        "signals_agree",
        "repetition_detected",
        "no_progress_detected",
    ) + (("host_action_required",) if fresh.requires_host_action else ())
    if (
        source["decision_id"] != expected_id
        or fresh.recovery_policy not in {"research", "branch", "model_switch"}
        or fresh.reason_codes != expected_reasons
        or not fresh.constraints.must_preserve_evidence
        or not fresh.constraints.no_non_idempotent_repeat
        or EffectClass.READ_ONLY not in fresh.constraints.allowed_effect_classes
    ):
        _fail("invalid_recovery_decision")

    contributions = fresh.confidence.contributing_findings
    if len({item.finding_id for item in contributions}) != len(contributions):
        _fail("invalid_recovery_decision")
    maxima = {
        FindingClass.REPETITION: 0.0,
        FindingClass.NO_PROGRESS: 0.0,
    }
    for item in contributions:
        if item.finding_class in {FindingClass.RISK, FindingClass.BUDGET}:
            _fail("invalid_recovery_decision")
        if item.matched is not True or item.finding_class not in maxima:
            _fail("invalid_recovery_decision")
        expected_weighted_score = min(1.0, max(0.0, item.weight * item.score))
        if not isclose(
            item.weighted_score,
            expected_weighted_score,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            _fail("invalid_recovery_decision")
        maxima[item.finding_class] = max(
            maxima[item.finding_class], item.weighted_score
        )
    if (
        maxima[FindingClass.REPETITION] <= 0.0
        or maxima[FindingClass.NO_PROGRESS] <= 0.0
    ):
        _fail("invalid_recovery_decision")
    expected_confidence = min(
        maxima[FindingClass.REPETITION], maxima[FindingClass.NO_PROGRESS]
    )
    if not isclose(
        fresh.confidence.score,
        expected_confidence,
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        _fail("invalid_recovery_decision")


__all__ = [
    "ControllerError",
    "RecoveryInstruction",
    "SemanticDetector",
    "analyze",
    "build_recovery_instruction",
]
