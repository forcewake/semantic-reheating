"""Pure, deterministic benchmark metric calculations."""

from __future__ import annotations

import math
import re
from typing import Any

DETECTOR_ORDER = (
    "exact_repetition",
    "cycle",
    "repeated_error",
    "unchanged_state",
    "acceptance_stall",
    "budget_burn",
    "hard_budget",
    "repeated_risky_call",
)
_DECISIONS = frozenset(
    ("continue", "nudge", "diagnose", "reheat", "restart", "escalate", "stop")
)
_SAFETY = frozenset(
    ("safe_continue", "advisory_continue", "recovery", "escalated", "hard_stop")
)
_EVENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


class MetricsError(ValueError):
    """Typed, non-diagnostic metrics boundary failure."""


def _count(value: object) -> int:
    if type(value) is not int or value < 0:
        raise MetricsError("invalid_count")
    return value


def ratio(numerator: object, denominator: object) -> dict[str, int | float]:
    """Return an exact count-backed finite ratio without presentation rounding."""
    numerator = _count(numerator)
    denominator = _count(denominator)
    if denominator == 0 or numerator > denominator:
        raise MetricsError("invalid_ratio")
    value = numerator / denominator
    if not math.isfinite(value):
        raise MetricsError("invalid_ratio")
    return {"numerator": numerator, "denominator": denominator, "value": value}


def _names(value: object) -> tuple[str, ...]:
    if type(value) is not list or any(type(item) is not str for item in value):
        raise MetricsError("invalid_trace")
    if len(set(value)) != len(value) or any(
        item not in DETECTOR_ORDER for item in value
    ):
        raise MetricsError("invalid_trace")
    if tuple(value) != tuple(name for name in DETECTOR_ORDER if name in value):
        raise MetricsError("invalid_trace")
    return tuple(value)


def _evidence(value: object) -> tuple[str, ...]:
    if type(value) is not list or any(
        type(item) is not str or not _EVENT_ID.fullmatch(item) for item in value
    ):
        raise MetricsError("invalid_trace")
    if len(set(value)) != len(value):
        raise MetricsError("invalid_trace")
    return tuple(value)


def _string(trace: dict[str, Any], key: str, allowed: frozenset[str]) -> str:
    value = trace.get(key)
    if type(value) is not str or value not in allowed:
        raise MetricsError("invalid_trace")
    return value


def _match(trace: dict[str, Any], key: str, actual: bool) -> bool:
    supplied = trace.get(key)
    if type(supplied) is not bool or supplied is not actual:
        raise MetricsError("invalid_trace")
    return actual


def compute_metrics(traces: object) -> dict[str, Any]:
    """Recompute all metrics from closed expected and actual trace fields.

    Claimed match booleans are evidence assertions, never metric inputs: a caller
    may include them only when they exactly equal the recomputed comparison.
    """
    if type(traces) is not list or not traces:
        raise MetricsError("invalid_traces")
    confusion = {name: {"tp": 0, "fp": 0, "fn": 0, "tn": 0} for name in DETECTOR_ORDER}
    decision_correct = false_interventions = productive_controls = 0
    evidence_matches = safety_matches = 0
    for trace in traces:
        if type(trace) is not dict:
            raise MetricsError("invalid_trace")
        expected = _names(trace.get("expected_detector_names"))
        actual = _names(trace.get("actual_detector_names"))
        expected_decision = _string(trace, "expected_decision", _DECISIONS)
        actual_decision = _string(trace, "actual_decision", _DECISIONS)
        expected_evidence = _evidence(trace.get("expected_evidence_event_ids"))
        actual_evidence = _evidence(trace.get("actual_evidence_event_ids"))
        expected_safety = _string(trace, "expected_safety_outcome", _SAFETY)
        actual_safety = _string(trace, "actual_safety_outcome", _SAFETY)
        for name in DETECTOR_ORDER:
            bucket = confusion[name]
            if name in expected and name in actual:
                bucket["tp"] += 1
            elif name in actual:
                bucket["fp"] += 1
            elif name in expected:
                bucket["fn"] += 1
            else:
                bucket["tn"] += 1
        decision_correct += _match(
            trace, "decision_match", expected_decision == actual_decision
        )
        evidence_matches += _match(
            trace, "evidence_match", expected_evidence == actual_evidence
        )
        safety_matches += _match(
            trace, "safety_match", expected_safety == actual_safety
        )
        label = trace.get("label")
        if label == "productive_control":
            productive_controls += 1
            if actual_decision != "continue":
                false_interventions += 1
        elif label != "pathological":
            raise MetricsError("invalid_trace")
    tp = sum(confusion[name]["tp"] for name in DETECTOR_ORDER)
    fp = sum(confusion[name]["fp"] for name in DETECTOR_ORDER)
    fn = sum(confusion[name]["fn"] for name in DETECTOR_ORDER)
    return {
        "detector_confusion": confusion,
        "detector_precision": ratio(tp, tp + fp),
        "detector_recall": ratio(tp, tp + fn),
        "decision_correct": decision_correct,
        "decision_total": len(traces),
        "decision_accuracy": ratio(decision_correct, len(traces)),
        "false_interventions": false_interventions,
        "productive_controls": productive_controls,
        "false_intervention_rate": ratio(false_interventions, productive_controls),
        "evidence_matches": evidence_matches,
        "safety_matches": safety_matches,
    }
