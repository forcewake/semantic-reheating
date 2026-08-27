"""Bounded advisory budget-burn detector contract tests."""

from __future__ import annotations

import json
import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _policy(*, window: int = 20, threshold: float = 0.7) -> Any:
    from semantic_reheating.models import RunPolicy

    source = json.loads(
        (PROJECT_ROOT / "tests/fixtures/contracts/minimal-run-policy.json").read_text()
    )
    source["detectors"]["windows"]["no_progress_events"] = window
    source["detectors"]["thresholds"]["budget_score"] = threshold
    return RunPolicy.from_dict(source)


def _event(
    sequence: int,
    *,
    kind: str = "message",
    payload: object | None = None,
    **fields: object,
) -> Any:
    from semantic_reheating.models import TraceEvent

    source: dict[str, object] = {
        "contract_version": "1.0",
        "run_id": "run-budget",
        "event_id": f"event-{sequence:03d}",
        "sequence": sequence,
        "kind": kind,
        "actor": "controller",
        "effect_class": "read_only",
        "payload": {} if payload is None else payload,
    }
    source.update(fields)
    return TraceEvent.from_dict(source)


def _budget(
    sequence: int, *, counters: dict[str, int | float], **fields: object
) -> Any:
    return _event(sequence, kind="budget", budget_counters=counters, **fields)


def test_singleton_window_returns_closed_unmatched_advisory_budget_finding() -> None:
    from semantic_reheating.detectors import detect_budget_burn
    from semantic_reheating.validation import validate_public_artifact

    finding = detect_budget_burn(
        (
            _budget(
                4,
                counters={
                    "turns": 1,
                    "tool_calls": 2,
                    "tokens": 3,
                    "elapsed_seconds": 4.5,
                    "cost": 6.0,
                },
            ),
        ),
        _policy(),
    )

    assert finding == {
        "contract_version": "1.0",
        "run_id": "run-budget",
        "finding_id": finding["finding_id"],
        "detector_name": "budget_burn",
        "detector_version": "1.0",
        "matched": False,
        "score": 0.0,
        "finding_class": "budget",
        "event_ids": ["event-004"],
        "reason_code": "budget_limit_reached",
        "explanation": "Budget-limit evidence was not detected in the evaluated window.",
        "availability": {
            "status": "available",
            "notice": "Deterministic detector completed with redacted evidence only.",
        },
    }
    assert validate_public_artifact("detector_finding", finding) == finding


@pytest.mark.parametrize(
    ("dimension", "value"),
    (
        ("turns", 2),
        ("tool_calls", 3),
        ("tokens", 4),
        ("elapsed_seconds", 5.5),
        ("cost", 7.0),
    ),
)
def test_one_monotonic_budget_dimension_rising_without_progress_matches(
    dimension: str, value: float
) -> None:
    from semantic_reheating.detectors import detect_budget_burn

    baseline = {
        "turns": 1,
        "tool_calls": 2,
        "tokens": 3,
        "elapsed_seconds": 4.5,
        "cost": 6.0,
    }
    current = {**baseline, dimension: value}

    finding = detect_budget_burn(
        (_budget(4, counters=baseline), _budget(5, counters=current)), _policy()
    )

    assert finding["matched"] is True
    assert finding["score"] == 1.0
    assert finding["event_ids"] == ["event-004", "event-005"]


@pytest.mark.parametrize(
    "trace",
    (
        (
            _budget(
                4,
                counters={
                    "turns": 1,
                    "tool_calls": 2,
                    "tokens": 3,
                    "elapsed_seconds": 4.5,
                    "cost": 6.0,
                },
            ),
            _budget(
                5,
                counters={
                    "turns": 1,
                    "tool_calls": 2,
                    "tokens": 3,
                    "elapsed_seconds": 4.5,
                    "cost": 6.0,
                },
            ),
        ),
        (
            _budget(
                4,
                counters={
                    "turns": 2,
                    "tool_calls": 2,
                    "tokens": 3,
                    "elapsed_seconds": 4.5,
                    "cost": 6.0,
                },
            ),
            _budget(
                5,
                counters={
                    "turns": 3,
                    "tool_calls": 1,
                    "tokens": 3,
                    "elapsed_seconds": 4.5,
                    "cost": 6.0,
                },
            ),
        ),
        (
            _event(
                4,
                kind="message",
                budget_counters={
                    "turns": 1,
                    "tool_calls": 2,
                    "tokens": 3,
                    "elapsed_seconds": 4.5,
                    "cost": 6.0,
                },
            ),
            _event(
                5,
                kind="message",
                budget_counters={
                    "turns": 2,
                    "tool_calls": 2,
                    "tokens": 3,
                    "elapsed_seconds": 4.5,
                    "cost": 6.0,
                },
            ),
        ),
        (
            _budget(
                4,
                counters={
                    "turns": 1,
                    "tool_calls": 2,
                    "tokens": 3,
                    "elapsed_seconds": 4.5,
                    "cost": 6.0,
                },
            ),
            _event(5, kind="budget"),
        ),
    ),
)
def test_equal_decreased_unrelated_or_missing_budget_counters_do_not_match(
    trace: tuple[Any, ...],
) -> None:
    from semantic_reheating.detectors import detect_budget_burn

    finding = detect_budget_burn(trace, _policy(threshold=0.0))

    assert finding["matched"] is False
    assert finding["score"] == 0.0
    assert finding["event_ids"] == [trace[-1].event_id]


def test_decrease_resets_the_baseline_and_later_monotonic_rise_matches() -> None:
    from semantic_reheating.detectors import detect_budget_burn

    finding = detect_budget_burn(
        (
            _budget(
                4,
                counters={
                    "turns": 10,
                    "tool_calls": 2,
                    "tokens": 3,
                    "elapsed_seconds": 4.5,
                    "cost": 6.0,
                },
            ),
            _budget(
                5,
                counters={
                    "turns": 1,
                    "tool_calls": 2,
                    "tokens": 3,
                    "elapsed_seconds": 4.5,
                    "cost": 6.0,
                },
            ),
            _budget(
                6,
                counters={
                    "turns": 2,
                    "tool_calls": 2,
                    "tokens": 3,
                    "elapsed_seconds": 4.5,
                    "cost": 6.0,
                },
            ),
        ),
        _policy(),
    )

    assert finding["matched"] is True
    assert finding["event_ids"] == ["event-005", "event-006"]


@pytest.mark.parametrize(
    "progress_events",
    (
        (_event(5, kind="tool_result", evidence_refs=["evidence://new"]),),
        (
            _event(
                5,
                kind="tool_call",
                payload={"hypothesis_id": "h", "hypothesis_test_input": "one"},
            ),
            _event(
                6,
                kind="tool_call",
                payload={"hypothesis_id": "h", "hypothesis_test_input": "two"},
            ),
        ),
        (
            _event(
                5,
                kind="state_observation",
                payload={"poll_id": "poll", "poll_value": 10, "poll_target": 0},
            ),
            _event(
                6,
                kind="state_observation",
                payload={"poll_id": "poll", "poll_value": 5, "poll_target": 0},
            ),
        ),
    ),
)
def test_documented_progress_inside_budget_span_suppresses_and_rebases(
    progress_events: tuple[Any, ...],
) -> None:
    from semantic_reheating.detectors import detect_budget_burn

    baseline = {
        "turns": 1,
        "tool_calls": 2,
        "tokens": 3,
        "elapsed_seconds": 4.5,
        "cost": 6.0,
    }
    current = {**baseline, "tokens": 4}
    finding = detect_budget_burn(
        (
            _budget(4, counters=baseline),
            *progress_events,
            _budget(len(progress_events) + 5, counters=current),
        ),
        _policy(),
    )

    assert finding["matched"] is False


def test_unchanged_or_nonconverging_poll_does_not_suppress_budget_burn() -> None:
    from semantic_reheating.detectors import detect_budget_burn

    finding = detect_budget_burn(
        (
            _budget(
                4,
                counters={
                    "turns": 1,
                    "tool_calls": 2,
                    "tokens": 3,
                    "elapsed_seconds": 4.5,
                    "cost": 6.0,
                },
            ),
            _event(
                5,
                kind="state_observation",
                payload={"poll_id": "poll", "poll_value": 5, "poll_target": 0},
            ),
            _event(
                6,
                kind="state_observation",
                payload={"poll_id": "poll", "poll_value": 5, "poll_target": 0},
            ),
            _budget(
                7,
                counters={
                    "turns": 2,
                    "tool_calls": 2,
                    "tokens": 3,
                    "elapsed_seconds": 4.5,
                    "cost": 6.0,
                },
            ),
        ),
        _policy(),
    )

    assert finding["matched"] is True
    assert finding["event_ids"] == ["event-004", "event-007"]


def test_window_cut_threshold_zero_huge_integer_and_adjacent_float_are_exact() -> None:
    from semantic_reheating.detectors import detect_budget_burn

    huge = 10**1000
    cut = detect_budget_burn(
        (
            _budget(
                3,
                counters={
                    "turns": 0,
                    "tool_calls": 0,
                    "tokens": 0,
                    "elapsed_seconds": 0,
                    "cost": 0,
                },
            ),
            _event(4),
            _budget(
                5,
                counters={
                    "turns": 1,
                    "tool_calls": 0,
                    "tokens": 0,
                    "elapsed_seconds": 0,
                    "cost": 0,
                },
            ),
        ),
        _policy(window=2, threshold=0.0),
    )
    huge_finding = detect_budget_burn(
        (
            _budget(
                4,
                counters={
                    "turns": huge,
                    "tool_calls": 0,
                    "tokens": 0,
                    "elapsed_seconds": huge,
                    "cost": huge,
                },
            ),
            _budget(
                5,
                counters={
                    "turns": huge + 1,
                    "tool_calls": 0,
                    "tokens": 0,
                    "elapsed_seconds": huge,
                    "cost": huge,
                },
            ),
        ),
        _policy(),
    )
    adjacent = math.nextafter(4.5, math.inf)
    float_finding = detect_budget_burn(
        (
            _budget(
                4,
                counters={
                    "turns": 0,
                    "tool_calls": 0,
                    "tokens": 0,
                    "elapsed_seconds": 4.5,
                    "cost": 0,
                },
            ),
            _budget(
                5,
                counters={
                    "turns": 0,
                    "tool_calls": 0,
                    "tokens": 0,
                    "elapsed_seconds": adjacent,
                    "cost": 0,
                },
            ),
        ),
        _policy(),
    )

    assert cut["matched"] is False
    assert cut["score"] == 0.0
    assert cut["event_ids"] == ["event-005"]
    assert huge_finding["event_ids"] == ["event-004", "event-005"]
    assert float_finding["matched"] is True


def test_earliest_qualifying_completion_is_deterministic() -> None:
    from semantic_reheating.detectors import detect_budget_burn

    trace = tuple(
        _budget(
            sequence,
            counters={
                "turns": sequence,
                "tool_calls": 0,
                "tokens": 0,
                "elapsed_seconds": 0,
                "cost": 0,
            },
        )
        for sequence in range(4, 8)
    )

    first = detect_budget_burn(trace, _policy())
    second = detect_budget_burn(trace, _policy())

    assert first == second
    assert first["event_ids"] == ["event-004", "event-005"]


def test_finding_is_fresh_schema_valid_redacted_and_does_not_mutate_inputs() -> None:
    from semantic_reheating.detectors import detect_budget_burn
    from semantic_reheating.validation import validate_public_artifact

    trace = (
        _budget(
            4,
            counters={
                "turns": 1,
                "tool_calls": 2,
                "tokens": 3,
                "elapsed_seconds": 4.5,
                "cost": 6.0,
            },
            payload={"secret": "counter-secret"},
            evidence_refs=["evidence://secret"],
        ),
        _budget(
            5,
            counters={
                "turns": 2,
                "tool_calls": 2,
                "tokens": 3,
                "elapsed_seconds": 4.5,
                "cost": 6.0,
            },
            payload={"secret": "counter-secret"},
            evidence_refs=["evidence://secret"],
        ),
    )
    source = tuple(event.to_dict() for event in trace)
    first = detect_budget_burn(trace, _policy())
    first["event_ids"].append("tampered")
    first["availability"]["notice"] = "tampered"
    second = detect_budget_burn(trace, _policy())

    assert second["event_ids"] == ["event-004", "event-005"]
    assert "secret" not in repr(second)
    assert tuple(event.to_dict() for event in trace) == source
    assert validate_public_artifact("detector_finding", second) == second


@pytest.mark.parametrize("resource_exception", (MemoryError, SystemExit))
def test_progress_resource_failures_propagate(
    monkeypatch: pytest.MonkeyPatch, resource_exception: type[BaseException]
) -> None:
    import semantic_reheating.detectors.budget_burn as budget_module
    from semantic_reheating.detectors import detect_budget_burn

    expected = resource_exception("RESOURCE_SECRET")

    def fail_progress(trace: object) -> object:
        raise expected

    monkeypatch.setattr(budget_module, "classify_progress", fail_progress)
    with pytest.raises(resource_exception) as raised:
        detect_budget_burn(
            (
                _budget(
                    4,
                    counters={
                        "turns": 1,
                        "tool_calls": 2,
                        "tokens": 3,
                        "elapsed_seconds": 4.5,
                        "cost": 6.0,
                    },
                ),
                _budget(
                    5,
                    counters={
                        "turns": 2,
                        "tool_calls": 2,
                        "tokens": 3,
                        "elapsed_seconds": 4.5,
                        "cost": 6.0,
                    },
                ),
            ),
            _policy(),
        )

    assert raised.value is expected


def test_ordinary_progress_failure_is_sanitized_without_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import semantic_reheating.detectors.budget_burn as budget_module
    from semantic_reheating.detectors import DetectorInputError, detect_budget_burn

    def fail_progress(trace: object) -> object:
        raise RuntimeError("PROGRESS_SECRET")

    monkeypatch.setattr(budget_module, "classify_progress", fail_progress)
    with pytest.raises(DetectorInputError) as raised:
        detect_budget_burn(
            (
                _budget(
                    4,
                    counters={
                        "turns": 1,
                        "tool_calls": 2,
                        "tokens": 3,
                        "elapsed_seconds": 4.5,
                        "cost": 6.0,
                    },
                ),
                _budget(
                    5,
                    counters={
                        "turns": 2,
                        "tool_calls": 2,
                        "tokens": 3,
                        "elapsed_seconds": 4.5,
                        "cost": 6.0,
                    },
                ),
            ),
            _policy(),
        )

    assert raised.value.code == "invalid_progress_classification"
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert "SECRET" not in repr(raised.value)


def test_productive_candidate_classification_is_linear_after_rebasing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import semantic_reheating.detectors.budget_burn as budget_module
    from semantic_reheating.detectors import detect_budget_burn

    classified_lengths: list[int] = []

    def made_progress(trace: tuple[Any, ...]) -> SimpleNamespace:
        classified_lengths.append(len(trace))
        return SimpleNamespace(made_progress=True)

    monkeypatch.setattr(budget_module, "classify_progress", made_progress)
    trace = tuple(
        _budget(
            sequence,
            counters={
                "turns": sequence,
                "tool_calls": 0,
                "tokens": 0,
                "elapsed_seconds": 0,
                "cost": 0,
            },
        )
        for sequence in range(1, 101)
    )

    finding = detect_budget_burn(trace, _policy(window=len(trace)))

    assert finding["matched"] is False
    assert len(classified_lengths) == len(trace) - 1
    assert sum(classified_lengths) <= 2 * len(trace)
