"""Bounded unchanged-state detector contract tests."""

from __future__ import annotations

import json
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
    source["detectors"]["thresholds"]["no_progress_score"] = threshold
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
        "run_id": "run-unchanged",
        "event_id": f"event-{sequence:03d}",
        "sequence": sequence,
        "kind": kind,
        "actor": "controller",
        "effect_class": "read_only",
        "payload": {} if payload is None else payload,
    }
    source.update(fields)
    return TraceEvent.from_dict(source)


def test_nonempty_window_returns_closed_unmatched_no_progress_finding() -> None:
    from semantic_reheating.detectors import detect_unchanged_state
    from semantic_reheating.validation import validate_public_artifact

    finding = detect_unchanged_state(
        (_event(4, kind="state_observation", state_fingerprint="state-a"),), _policy()
    )

    assert finding == {
        "contract_version": "1.0",
        "run_id": "run-unchanged",
        "finding_id": finding["finding_id"],
        "detector_name": "unchanged_state",
        "detector_version": "1.0",
        "matched": False,
        "score": 0.0,
        "finding_class": "no_progress",
        "event_ids": ["event-004"],
        "reason_code": "no_progress_detected",
        "explanation": "No-progress evidence was not detected in the evaluated window.",
        "availability": {
            "status": "available",
            "notice": "Deterministic detector completed with redacted evidence only.",
        },
    }
    assert validate_public_artifact("detector_finding", finding) == finding


def test_prior_baseline_expected_action_and_same_observation_match() -> None:
    from semantic_reheating.detectors import detect_unchanged_state

    finding = detect_unchanged_state(
        (
            _event(4, kind="state_observation", state_fingerprint="state-a"),
            _event(5, kind="tool_call", expected_state_change=True),
            _event(6, kind="state_observation", state_fingerprint="state-a"),
        ),
        _policy(),
    )

    assert finding["matched"] is True
    assert finding["score"] == 1.0
    assert finding["event_ids"] == ["event-004", "event-005", "event-006"]


def test_expectation_carrying_baseline_matches_later_same_observation() -> None:
    from semantic_reheating.detectors import detect_unchanged_state

    finding = detect_unchanged_state(
        (
            _event(
                4,
                kind="tool_call",
                state_fingerprint="state-a",
                expected_state_change=True,
            ),
            _event(5, kind="state_observation", state_fingerprint="state-a"),
        ),
        _policy(),
    )

    assert finding["matched"] is True
    assert finding["event_ids"] == ["event-004", "event-005"]


@pytest.mark.parametrize(
    "trace",
    (
        (
            _event(4, kind="state_observation", state_fingerprint="state-a"),
            _event(5, kind="tool_call", expected_state_change=True),
            _event(6, kind="state_observation", state_fingerprint="state-b"),
        ),
        (
            _event(4, kind="tool_call", expected_state_change=True),
            _event(5, kind="state_observation", state_fingerprint="state-a"),
        ),
        (
            _event(4, kind="state_observation", state_fingerprint="state-a"),
            _event(
                5,
                kind="tool_call",
                state_fingerprint="state-a",
                expected_state_change=True,
            ),
        ),
        (
            _event(4, kind="state_observation", state_fingerprint="state-a"),
            _event(5, kind="tool_call", expected_state_change=False),
            _event(6, kind="state_observation", state_fingerprint="state-a"),
        ),
        (
            _event(4, kind="state_observation", state_fingerprint="state-a"),
            _event(5, kind="tool_call"),
            _event(6, kind="state_observation", state_fingerprint="state-a"),
        ),
        (
            _event(4, kind="state_observation", state_fingerprint="state-a"),
            _event(
                5,
                kind="tool_call",
                state_fingerprint="state-a",
                expected_state_change=True,
            ),
            _event(6, kind="tool_result", state_fingerprint="state-a"),
        ),
        (
            _event(4, kind="state_observation", state_fingerprint="state-a"),
            _event(5, kind="tool_call", expected_state_change=True),
            _event(
                6, kind="state_observation", payload={"state_fingerprint": "state-a"}
            ),
        ),
        (
            _event(
                4,
                kind="state_observation",
                state_fingerprint="state-a",
                expected_state_change=True,
            ),
        ),
    ),
)
def test_controls_do_not_match_without_a_later_unchanged_state_observation(
    trace: tuple[Any, ...],
) -> None:
    from semantic_reheating.detectors import detect_unchanged_state

    finding = detect_unchanged_state(trace, _policy(threshold=0.0))

    assert finding["matched"] is False
    assert finding["score"] == 0.0
    assert finding["event_ids"] == [trace[-1].event_id]


def test_window_never_uses_a_baseline_or_expectation_before_its_cut() -> None:
    from semantic_reheating.detectors import detect_unchanged_state

    baseline_cut = (
        _event(3, kind="state_observation", state_fingerprint="state-a"),
        _event(4, kind="tool_call", expected_state_change=True),
        _event(5, kind="state_observation", state_fingerprint="state-a"),
    )
    expectation_cut = (
        _event(
            3, kind="tool_call", state_fingerprint="state-a", expected_state_change=True
        ),
        _event(4, kind="state_observation", state_fingerprint="state-a"),
        _event(5),
    )

    assert detect_unchanged_state(baseline_cut, _policy(window=2))["matched"] is False
    assert (
        detect_unchanged_state(expectation_cut, _policy(window=2))["matched"] is False
    )


def test_duplicate_expectations_use_the_earliest_support_once() -> None:
    from semantic_reheating.detectors import detect_unchanged_state

    finding = detect_unchanged_state(
        (
            _event(4, kind="state_observation", state_fingerprint="state-a"),
            _event(5, kind="tool_call", expected_state_change=True),
            _event(6, kind="tool_call", expected_state_change=True),
            _event(7, kind="state_observation", state_fingerprint="state-a"),
        ),
        _policy(),
    )

    assert finding["matched"] is True
    assert finding["event_ids"] == ["event-004", "event-005", "event-007"]


def test_same_state_converging_poll_and_new_evidence_suppress_the_finding() -> None:
    from semantic_reheating.detectors import detect_unchanged_state

    converging = (
        _event(
            4,
            kind="state_observation",
            state_fingerprint="state-a",
            payload={"poll_id": "poll", "poll_value": 10, "poll_target": 0},
        ),
        _event(5, kind="tool_call", expected_state_change=True),
        _event(
            6,
            kind="state_observation",
            state_fingerprint="state-a",
            payload={"poll_id": "poll", "poll_value": 5, "poll_target": 0},
        ),
    )
    evidence = (
        _event(4, kind="state_observation", state_fingerprint="state-a"),
        _event(5, kind="tool_call", expected_state_change=True),
        _event(
            6,
            kind="state_observation",
            state_fingerprint="state-a",
            evidence_refs=["evidence://new"],
        ),
    )

    assert detect_unchanged_state(converging, _policy())["matched"] is False
    assert detect_unchanged_state(evidence, _policy())["matched"] is False


@pytest.mark.parametrize(
    "marker_events",
    (
        (
            _event(
                6,
                kind="tool_call",
                state_fingerprint="state-b",
                expected_state_change=True,
            ),
            _event(7, kind="state_observation", state_fingerprint="state-a"),
        ),
        (
            _event(
                6,
                kind="acceptance_check",
                payload={"required_verification": True},
                acceptance_delta="first check",
            ),
            _event(
                7,
                kind="acceptance_check",
                payload={"required_verification": True},
                acceptance_delta="rerun check",
            ),
        ),
        (
            _event(
                6,
                kind="tool_call",
                payload={"hypothesis_id": "h", "hypothesis_test_input": "one"},
            ),
            _event(
                7,
                kind="tool_call",
                payload={"hypothesis_id": "h", "hypothesis_test_input": "two"},
            ),
        ),
        (
            _event(6, kind="handoff", payload={"new_plan_id": "plan-b"}),
            _event(7),
        ),
    ),
)
def test_documented_productive_markers_suppress_an_unchanged_candidate(
    marker_events: tuple[Any, ...],
) -> None:
    from semantic_reheating.detectors import detect_unchanged_state

    trace = (
        _event(4, kind="state_observation", state_fingerprint="state-a"),
        _event(5, kind="tool_call", expected_state_change=True),
        *marker_events,
        _event(8, kind="state_observation", state_fingerprint="state-a"),
    )

    assert detect_unchanged_state(trace, _policy())["matched"] is False


def test_changed_state_polling_is_not_an_unchanged_candidate() -> None:
    from semantic_reheating.detectors import detect_unchanged_state

    trace = (
        _event(
            4,
            kind="state_observation",
            state_fingerprint="state-a",
            payload={"poll_id": "poll", "poll_value": 5, "poll_target": 0},
        ),
        _event(5, kind="tool_call", expected_state_change=True),
        _event(
            6,
            kind="state_observation",
            state_fingerprint="state-b",
            payload={"poll_id": "poll", "poll_value": 5, "poll_target": 0},
        ),
    )

    assert detect_unchanged_state(trace, _policy())["matched"] is False


def test_unchanged_or_nonconverging_poll_remains_an_unchanged_candidate() -> None:
    from semantic_reheating.detectors import detect_unchanged_state

    trace = (
        _event(
            4,
            kind="state_observation",
            state_fingerprint="state-a",
            payload={"poll_id": "poll", "poll_value": 5, "poll_target": 0},
        ),
        _event(5, kind="tool_call", expected_state_change=True),
        _event(
            6,
            kind="state_observation",
            state_fingerprint="state-a",
            payload={"poll_id": "poll", "poll_value": 5, "poll_target": 0},
        ),
    )

    assert detect_unchanged_state(trace, _policy())["matched"] is True


def test_finding_is_fresh_deterministic_redacted_and_does_not_mutate_inputs() -> None:
    from semantic_reheating.detectors import detect_unchanged_state
    from semantic_reheating.validation import validate_public_artifact

    trace = (
        _event(4, kind="state_observation", state_fingerprint="state-secret"),
        _event(5, kind="tool_call", expected_state_change=True),
        _event(6, kind="state_observation", state_fingerprint="state-secret"),
    )
    source = tuple(event.to_dict() for event in trace)
    first = detect_unchanged_state(trace, _policy())
    first["event_ids"].append("tampered")
    first["availability"]["notice"] = "tampered"
    second = detect_unchanged_state(trace, _policy())

    assert first["finding_id"] == second["finding_id"]
    assert second["event_ids"] == ["event-004", "event-005", "event-006"]
    assert "state-secret" not in repr(second)
    assert tuple(event.to_dict() for event in trace) == source
    assert validate_public_artifact("detector_finding", second) == second


@pytest.mark.parametrize(
    ("trace", "code"),
    (
        ((), "empty_trace_window"),
        (
            (
                _event(4, kind="state_observation", state_fingerprint="state-a"),
                _event(6, kind="state_observation", state_fingerprint="state-a"),
            ),
            "sequence_gap",
        ),
        (
            (
                _event(4, kind="state_observation", state_fingerprint="state-a"),
                _event(
                    5,
                    kind="state_observation",
                    run_id="other",
                    state_fingerprint="state-a",
                ),
            ),
            "run_id_mismatch",
        ),
    ),
)
def test_detector_inherits_sanitized_window_boundary_errors(
    trace: tuple[Any, ...], code: str
) -> None:
    from semantic_reheating.detectors import DetectorInputError, detect_unchanged_state

    with pytest.raises(DetectorInputError) as raised:
        detect_unchanged_state(trace, _policy())

    assert raised.value.code == code
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_detector_rejects_duplicate_event_ids_at_the_shared_boundary() -> None:
    from semantic_reheating.detectors import DetectorInputError, detect_unchanged_state
    from semantic_reheating.models import TraceEvent

    duplicate = _event(
        5, kind="state_observation", state_fingerprint="state-a"
    ).to_dict()
    duplicate["event_id"] = "event-004"
    with pytest.raises(DetectorInputError) as raised:
        detect_unchanged_state(
            (
                _event(4, kind="state_observation", state_fingerprint="state-a"),
                TraceEvent.from_dict(duplicate),
            ),
            _policy(),
        )

    assert raised.value.code == "duplicate_event_id"


@pytest.mark.parametrize("error_type", (RuntimeError, ValueError))
def test_progress_classification_failure_is_sanitized(
    monkeypatch: pytest.MonkeyPatch, error_type: type[Exception]
) -> None:
    import semantic_reheating.detectors.unchanged_state as unchanged_module
    from semantic_reheating.detectors import DetectorInputError, detect_unchanged_state

    def fail_progress(trace: object) -> object:
        raise error_type("PROGRESS_SECRET")

    monkeypatch.setattr(unchanged_module, "classify_progress", fail_progress)
    with pytest.raises(DetectorInputError) as raised:
        detect_unchanged_state(
            (
                _event(4, kind="state_observation", state_fingerprint="state-a"),
                _event(5, kind="tool_call", expected_state_change=True),
                _event(6, kind="state_observation", state_fingerprint="state-a"),
            ),
            _policy(),
        )

    assert raised.value.code == "invalid_progress_classification"
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


@pytest.mark.parametrize("resource_exception", (MemoryError, SystemExit))
def test_progress_resource_failure_propagates(
    monkeypatch: pytest.MonkeyPatch, resource_exception: type[BaseException]
) -> None:
    import semantic_reheating.detectors.unchanged_state as unchanged_module
    from semantic_reheating.detectors import detect_unchanged_state

    expected = resource_exception("RESOURCE_SECRET")

    def fail_progress(trace: object) -> object:
        raise expected

    monkeypatch.setattr(unchanged_module, "classify_progress", fail_progress)
    with pytest.raises(resource_exception) as raised:
        detect_unchanged_state(
            (
                _event(4, kind="state_observation", state_fingerprint="state-a"),
                _event(5, kind="tool_call", expected_state_change=True),
                _event(6, kind="state_observation", state_fingerprint="state-a"),
            ),
            _policy(),
        )

    assert raised.value is expected


def test_progress_classification_is_called_only_for_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import semantic_reheating.detectors.unchanged_state as unchanged_module
    from semantic_reheating.detectors import detect_unchanged_state

    calls = 0

    def made_progress(trace: object) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        return SimpleNamespace(made_progress=True)

    monkeypatch.setattr(unchanged_module, "classify_progress", made_progress)
    trace = tuple(_event(index, kind="message") for index in range(1, 501))
    finding = detect_unchanged_state(trace, _policy(window=len(trace)))

    assert finding["matched"] is False
    assert calls == 0


def test_progress_classification_calls_do_not_exceed_unchanged_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import semantic_reheating.detectors.unchanged_state as unchanged_module
    from semantic_reheating.detectors import detect_unchanged_state

    calls = 0

    def made_progress(trace: object) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        return SimpleNamespace(made_progress=True)

    monkeypatch.setattr(unchanged_module, "classify_progress", made_progress)
    candidate_count = 100
    trace = (
        _event(1, kind="state_observation", state_fingerprint="state-a"),
        _event(2, kind="tool_call", expected_state_change=True),
        *(
            _event(index, kind="state_observation", state_fingerprint="state-a")
            for index in range(3, candidate_count + 3)
        ),
    )
    finding = detect_unchanged_state(trace, _policy(window=len(trace)))

    assert finding["matched"] is False
    assert calls <= candidate_count
