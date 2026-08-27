"""Bounded state-cycle detector contract tests."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from semantic_reheating.progress import ProgressClassificationError

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _policy(*, window: int = 20, threshold: float = 0.7) -> Any:
    from semantic_reheating.models import RunPolicy

    source = json.loads(
        (PROJECT_ROOT / "tests/fixtures/contracts/minimal-run-policy.json").read_text()
    )
    source["detectors"]["windows"]["repetition_events"] = window
    source["detectors"]["thresholds"]["repetition_score"] = threshold
    return RunPolicy.from_dict(source)


def _event(
    sequence: int,
    *,
    kind: str = "state_observation",
    payload: object | None = None,
    **fields: object,
) -> Any:
    from semantic_reheating.models import TraceEvent

    source: dict[str, object] = {
        "contract_version": "1.0",
        "run_id": "run-cycle",
        "event_id": f"event-{sequence:03d}",
        "sequence": sequence,
        "kind": kind,
        "actor": "controller",
        "effect_class": "read_only",
        "payload": {} if payload is None else payload,
    }
    source.update(fields)
    return TraceEvent.from_dict(source)


def test_nonclosing_state_sequence_returns_closed_unmatched_finding() -> None:
    from semantic_reheating.detectors import detect_cycle
    from semantic_reheating.validation import validate_public_artifact

    finding = detect_cycle(
        (
            _event(4, state_fingerprint="state-a"),
            _event(5, state_fingerprint="state-b"),
        ),
        _policy(),
    )

    assert finding == {
        "contract_version": "1.0",
        "run_id": "run-cycle",
        "finding_id": finding["finding_id"],
        "detector_name": "cycle",
        "detector_version": "1.0",
        "matched": False,
        "score": 0.0,
        "finding_class": "repetition",
        "event_ids": ["event-005"],
        "reason_code": "repetition_detected",
        "explanation": "Repetition evidence was not detected in the evaluated window.",
        "availability": {
            "status": "available",
            "notice": "Deterministic detector completed with redacted evidence only.",
        },
    }
    assert validate_public_artifact("detector_finding", finding) == finding


def test_two_step_cycle_matches_with_all_state_observation_support() -> None:
    from semantic_reheating.detectors import detect_cycle

    finding = detect_cycle(
        (
            _event(4, state_fingerprint="state-a"),
            _event(5, state_fingerprint="state-b"),
            _event(6, state_fingerprint="state-a"),
        ),
        _policy(),
    )

    assert finding["matched"] is True
    assert finding["score"] == 1.0
    assert finding["event_ids"] == ["event-004", "event-005", "event-006"]


def test_five_step_cycle_matches_with_six_observations() -> None:
    from semantic_reheating.detectors import detect_cycle

    finding = detect_cycle(
        tuple(
            _event(sequence, state_fingerprint=f"state-{fingerprint}")
            for sequence, fingerprint in enumerate(
                ("a", "b", "c", "d", "e", "a"), start=4
            )
        ),
        _policy(),
    )

    assert finding["matched"] is True
    assert finding["event_ids"] == [
        "event-004",
        "event-005",
        "event-006",
        "event-007",
        "event-008",
        "event-009",
    ]


@pytest.mark.parametrize(
    "fingerprints",
    (
        ("state-a", "state-a"),
        ("state-a", "state-b", "state-c", "state-d", "state-e", "state-f", "state-a"),
        ("state-a", "state-a", "state-a", "state-a"),
        ("state-a", "state-b"),
    ),
)
def test_non_oscillation_controls_remain_unmatched(
    fingerprints: tuple[str, ...],
) -> None:
    from semantic_reheating.detectors import detect_cycle

    finding = detect_cycle(
        tuple(
            _event(sequence, state_fingerprint=fingerprint)
            for sequence, fingerprint in enumerate(fingerprints, start=4)
        ),
        _policy(threshold=0.0),
    )

    assert finding["matched"] is False
    assert finding["score"] == 0.0
    assert finding["event_ids"] == [f"event-{len(fingerprints) + 3:03d}"]


def test_cycle_selects_earliest_completion_and_ignores_interleaved_nonstate_events() -> (
    None
):
    from semantic_reheating.detectors import detect_cycle

    finding = detect_cycle(
        (
            _event(4, state_fingerprint="state-a"),
            _event(5, kind="message", payload={"untrusted": "ignore"}),
            _event(6, state_fingerprint="state-b"),
            _event(7, kind="tool_result", payload={"untrusted": "ignore"}),
            _event(8, state_fingerprint="state-a"),
            _event(9, state_fingerprint="state-b"),
            _event(10, state_fingerprint="state-a"),
        ),
        _policy(),
    )

    assert finding["matched"] is True
    assert finding["event_ids"] == ["event-004", "event-006", "event-008"]


def test_cycle_uses_shortest_step_count_when_a_completion_has_multiple_cycles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import semantic_reheating.detectors.cycle as cycle_module
    from semantic_reheating.detectors import detect_cycle

    calls = 0

    def suppress_only_the_earlier_cycle(trace: object) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        return SimpleNamespace(made_progress=calls <= 2)

    monkeypatch.setattr(
        cycle_module, "classify_progress", suppress_only_the_earlier_cycle
    )
    finding = detect_cycle(
        tuple(
            _event(sequence, state_fingerprint=f"state-{fingerprint}")
            for sequence, fingerprint in enumerate(("a", "b", "a", "b", "a"), start=4)
        ),
        _policy(),
    )

    assert finding["matched"] is True
    assert finding["event_ids"] == ["event-006", "event-007", "event-008"]


def test_cycle_uses_only_the_repetition_window() -> None:
    from semantic_reheating.detectors import detect_cycle

    finding = detect_cycle(
        (
            _event(3, state_fingerprint="state-a"),
            _event(4, state_fingerprint="state-b"),
            _event(5, state_fingerprint="state-a"),
            _event(6, state_fingerprint="state-c"),
        ),
        _policy(window=3),
    )

    assert finding["matched"] is False
    assert finding["event_ids"] == ["event-006"]


def test_cycle_finding_is_fresh_deterministic_and_redacted() -> None:
    from semantic_reheating.detectors import detect_cycle
    from semantic_reheating.validation import validate_public_artifact

    trace = (
        _event(4, state_fingerprint="state-secret-a"),
        _event(5, state_fingerprint="state-secret-b"),
        _event(6, state_fingerprint="state-secret-a"),
    )
    first = detect_cycle(trace, _policy())
    first["event_ids"].append("tampered")
    first["availability"]["notice"] = "tampered"
    second = detect_cycle(trace, _policy())

    assert second["matched"] is True
    assert second["event_ids"] == ["event-004", "event-005", "event-006"]
    assert first["finding_id"] == second["finding_id"]
    assert "state-secret" not in repr(second)
    assert validate_public_artifact("detector_finding", second) == second


def test_productive_evidence_and_converging_poll_suppress_a_closed_cycle() -> None:
    from semantic_reheating.detectors import detect_cycle

    evidence = (
        _event(4, state_fingerprint="state-a"),
        _event(5, state_fingerprint="state-b", evidence_refs=["evidence://new"]),
        _event(6, state_fingerprint="state-a"),
    )
    converging = (
        _event(
            4,
            state_fingerprint="state-a",
            payload={"poll_id": "poll", "poll_value": 10, "poll_target": 0},
        ),
        _event(
            5,
            state_fingerprint="state-b",
            payload={"poll_id": "poll", "poll_value": 5, "poll_target": 0},
        ),
        _event(
            6,
            state_fingerprint="state-a",
            payload={"poll_id": "poll", "poll_value": 5, "poll_target": 0},
        ),
    )

    assert detect_cycle(evidence, _policy())["matched"] is False
    assert detect_cycle(converging, _policy())["matched"] is False


def test_unchanged_poll_does_not_suppress_a_closed_cycle() -> None:
    from semantic_reheating.detectors import detect_cycle

    trace = tuple(
        _event(
            sequence,
            state_fingerprint=f"state-{fingerprint}",
            payload={"poll_id": "poll", "poll_value": 10, "poll_target": 0},
        )
        for sequence, fingerprint in ((4, "a"), (5, "b"), (6, "a"))
    )

    assert detect_cycle(trace, _policy())["matched"] is True


@pytest.mark.parametrize("error_type", (ProgressClassificationError, RuntimeError))
def test_cycle_sanitizes_progress_classification_failures(
    monkeypatch: pytest.MonkeyPatch, error_type: type[Exception]
) -> None:
    import semantic_reheating.detectors.cycle as cycle_module
    from semantic_reheating.detectors import DetectorInputError, detect_cycle

    def fail_progress(trace: object) -> object:
        raise error_type("PROGRESS_SECRET")

    monkeypatch.setattr(cycle_module, "classify_progress", fail_progress)
    with pytest.raises(DetectorInputError) as raised:
        detect_cycle(
            (
                _event(4, state_fingerprint="state-a"),
                _event(5, state_fingerprint="state-b"),
                _event(6, state_fingerprint="state-a"),
            ),
            _policy(),
        )

    assert raised.value.code == "invalid_progress_classification"
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert "SECRET" not in repr(raised.value)


@pytest.mark.parametrize("resource_exception", (MemoryError, SystemExit))
def test_cycle_preserves_progress_resource_failures(
    monkeypatch: pytest.MonkeyPatch, resource_exception: type[BaseException]
) -> None:
    import semantic_reheating.detectors.cycle as cycle_module
    from semantic_reheating.detectors import detect_cycle

    expected = resource_exception("RESOURCE_SECRET")

    def fail_progress(trace: object) -> object:
        raise expected

    monkeypatch.setattr(cycle_module, "classify_progress", fail_progress)
    with pytest.raises(resource_exception) as raised:
        detect_cycle(
            (
                _event(4, state_fingerprint="state-a"),
                _event(5, state_fingerprint="state-b"),
                _event(6, state_fingerprint="state-a"),
            ),
            _policy(),
        )

    assert raised.value is expected


def test_cycle_progress_checks_are_bounded_per_state_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import semantic_reheating.detectors.cycle as cycle_module
    from semantic_reheating.detectors import detect_cycle

    calls = 0

    def made_progress(trace: object) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        return SimpleNamespace(made_progress=True)

    monkeypatch.setattr(cycle_module, "classify_progress", made_progress)
    observations = tuple(
        _event(sequence, state_fingerprint=f"state-{sequence % 2}")
        for sequence in range(1, 101)
    )

    finding = detect_cycle(observations, _policy(window=len(observations)))

    assert finding["matched"] is False
    assert calls <= 4 * len(observations)


def test_shared_detector_helpers_select_only_closed_windows_and_finding_classes() -> (
    None
):
    import semantic_reheating.detectors as detector_module
    from semantic_reheating.detectors import DetectorInputError

    trace = tuple(_event(sequence) for sequence in range(1, 5))
    window, parsed_policy = detector_module._validated_inputs(
        trace, _policy(window=1), window_policy="no_progress"
    )
    no_progress = detector_module._finding(
        "shared_test",
        window,
        parsed_policy,
        [window[-1].event_id],
        True,
        finding_class="no_progress",
    )
    budget = detector_module._finding(
        "shared_test",
        window,
        parsed_policy,
        [window[-1].event_id],
        True,
        finding_class="budget",
    )

    assert [event.event_id for event in window] == [
        "event-002",
        "event-003",
        "event-004",
    ]
    assert no_progress["reason_code"] == "no_progress_detected"
    assert no_progress["matched"] is True
    assert budget["reason_code"] == "budget_limit_reached"
    assert budget["matched"] is True
    with pytest.raises(DetectorInputError) as raised:
        detector_module._validated_inputs(
            trace, _policy(), window_policy=cast(Any, "unrecognized")
        )
    assert raised.value.code == "invalid_detector_window_policy"
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
