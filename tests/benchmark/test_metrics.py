"""Pure deterministic metric behavior, including imperfect replay inputs."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from benchmark.metrics import DETECTOR_ORDER, MetricsError, compute_metrics, ratio


def _trace(**overrides: object) -> dict[str, object]:
    source: dict[str, object] = {
        "expected_detector_names": [],
        "actual_detector_names": [],
        "expected_decision": "continue",
        "actual_decision": "continue",
        "expected_evidence_event_ids": [],
        "actual_evidence_event_ids": [],
        "expected_safety_outcome": "advisory_continue",
        "actual_safety_outcome": "advisory_continue",
        "decision_match": True,
        "evidence_match": True,
        "safety_match": True,
        "label": "pathological",
    }
    source.update(overrides)
    return source


def test_ratio_uses_exact_numerator_denominator_and_finite_float() -> None:
    assert ratio(2, 5) == {"numerator": 2, "denominator": 5, "value": 0.4}
    with pytest.raises(MetricsError):
        ratio(True, 1)
    with pytest.raises(MetricsError):
        ratio(1, 0)


def test_metrics_compute_micro_confusion_and_productive_false_intervention() -> None:
    metrics = compute_metrics(
        [
            _trace(expected_detector_names=["cycle"], actual_detector_names=["cycle"]),
            _trace(expected_detector_names=["exact_repetition"]),
            _trace(actual_detector_names=["repeated_error"]),
            _trace(
                label="productive_control",
                actual_decision="nudge",
                decision_match=False,
            ),
        ]
    )

    assert metrics["detector_confusion"]["cycle"] == {
        "tp": 1,
        "fp": 0,
        "fn": 0,
        "tn": 3,
    }
    assert metrics["detector_confusion"]["exact_repetition"] == {
        "tp": 0,
        "fp": 0,
        "fn": 1,
        "tn": 3,
    }
    assert metrics["detector_confusion"]["repeated_error"] == {
        "tp": 0,
        "fp": 1,
        "fn": 0,
        "tn": 3,
    }
    assert metrics["detector_precision"] == {
        "numerator": 1,
        "denominator": 2,
        "value": 0.5,
    }
    assert metrics["detector_recall"] == {
        "numerator": 1,
        "denominator": 2,
        "value": 0.5,
    }
    assert metrics["decision_accuracy"] == {
        "numerator": 3,
        "denominator": 4,
        "value": 0.75,
    }
    assert metrics["false_intervention_rate"] == {
        "numerator": 1,
        "denominator": 1,
        "value": 1.0,
    }
    assert tuple(metrics["detector_confusion"]) == DETECTOR_ORDER
    assert all(
        math.isfinite(metrics[key]["value"])
        for key in (
            "detector_precision",
            "detector_recall",
            "decision_accuracy",
            "false_intervention_rate",
        )
    )


def test_metrics_recomputes_match_flags_and_rejects_forged_boolean() -> None:
    trace = _trace(
        label="productive_control",
        expected_decision="nudge",
        actual_decision="continue",
        expected_detector_names=["cycle"],
        actual_detector_names=["cycle"],
        decision_match=True,
    )

    with pytest.raises(MetricsError):
        compute_metrics([trace])
