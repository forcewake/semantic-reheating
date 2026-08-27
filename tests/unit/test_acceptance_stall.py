"""Bounded acceptance-stall detector contract tests."""

from __future__ import annotations

import json
from pathlib import Path
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
        "run_id": "run-acceptance",
        "event_id": f"event-{sequence:03d}",
        "sequence": sequence,
        "kind": kind,
        "actor": "controller",
        "effect_class": "read_only",
        "payload": {} if payload is None else payload,
    }
    source.update(fields)
    return TraceEvent.from_dict(source)


def _ref_only_event(sequence: int, *, acceptance_delta: str) -> Any:
    from semantic_reheating.models import TraceEvent

    source = _event(sequence, kind="acceptance_check").to_dict()
    source.pop("payload")
    source["payload_ref"] = "check://same"
    source["acceptance_delta"] = acceptance_delta
    return TraceEvent.from_dict(source)


def _digest_event(
    sequence: int, *, acceptance_delta: str, digest: str = "check-a"
) -> Any:
    from semantic_reheating.models import TraceEvent

    source = _event(sequence, kind="acceptance_check").to_dict()
    source.pop("payload")
    source["payload_digest"] = digest
    source["acceptance_delta"] = acceptance_delta
    return TraceEvent.from_dict(source)


def test_singleton_window_returns_closed_unmatched_no_progress_finding() -> None:
    from semantic_reheating.detectors import detect_acceptance_stall
    from semantic_reheating.validation import validate_public_artifact

    finding = detect_acceptance_stall(
        (
            _event(
                4,
                kind="acceptance_check",
                payload={"check": "build"},
                acceptance_delta="",
            ),
        ),
        _policy(),
    )

    assert finding == {
        "contract_version": "1.0",
        "run_id": "run-acceptance",
        "finding_id": finding["finding_id"],
        "detector_name": "acceptance_stall",
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


def test_same_check_identity_and_explicit_delta_match_with_two_event_support() -> None:
    from semantic_reheating.detectors import detect_acceptance_stall

    trace = (
        _event(
            4,
            kind="acceptance_check",
            payload={"check": "build", "target": "controller"},
            acceptance_delta="no changes",
        ),
        _event(
            5,
            kind="acceptance_check",
            payload={"check": "build", "target": "controller"},
            acceptance_delta="no changes",
        ),
    )

    finding = detect_acceptance_stall(trace, _policy())

    assert finding["matched"] is True
    assert finding["score"] == 1.0
    assert finding["event_ids"] == ["event-004", "event-005"]


def test_reordered_payload_and_volatile_request_id_match_the_same_check() -> None:
    from semantic_reheating.detectors import detect_acceptance_stall

    finding = detect_acceptance_stall(
        (
            _event(
                4,
                kind="acceptance_check",
                payload={"check": "build", "inputs": {"a": 1, "b": 2}},
                acceptance_delta="no changes",
            ),
            _event(
                5,
                kind="acceptance_check",
                payload={
                    "inputs": {"b": 2, "a": 1},
                    "request_id": "volatile-attempt",
                    "check": "build",
                },
                acceptance_delta="no changes",
            ),
        ),
        _policy(),
    )

    assert finding["matched"] is True
    assert finding["event_ids"] == ["event-004", "event-005"]


@pytest.mark.parametrize(
    "trace",
    (
        (
            _event(
                4, kind="acceptance_check", payload={"check": "a"}, acceptance_delta=""
            ),
            _event(
                5, kind="acceptance_check", payload={"check": "b"}, acceptance_delta=""
            ),
        ),
        (
            _event(
                4,
                kind="acceptance_check",
                payload={"check": "a"},
                acceptance_delta="one",
            ),
            _event(
                5,
                kind="acceptance_check",
                payload={"check": "a"},
                acceptance_delta="two",
            ),
        ),
        (
            _ref_only_event(4, acceptance_delta=""),
            _ref_only_event(5, acceptance_delta=""),
        ),
        (
            _event(
                4, kind="acceptance_check", payload={"check": "a"}, acceptance_delta=""
            ),
            _event(5, kind="acceptance_check", payload={"check": "a"}),
        ),
        (
            _digest_event(4, acceptance_delta="", digest="check-a"),
            _digest_event(5, acceptance_delta="", digest="check-b"),
        ),
    ),
)
def test_changed_or_noncomparable_acceptance_checks_do_not_match(
    trace: tuple[Any, ...],
) -> None:
    from semantic_reheating.detectors import detect_acceptance_stall

    finding = detect_acceptance_stall(trace, _policy(threshold=0.0))

    assert finding["matched"] is False
    assert finding["score"] == 0.0
    assert finding["event_ids"] == ["event-005"]


def test_required_nonempty_acceptance_reruns_are_productive_but_empty_deltas_stall() -> (
    None
):
    from semantic_reheating.detectors import detect_acceptance_stall

    productive = tuple(
        _event(
            sequence,
            kind="acceptance_check",
            payload={"check": "release", "required_verification": True},
            acceptance_delta="verified",
        )
        for sequence in (4, 5, 6)
    )
    stalled = tuple(
        _event(
            sequence,
            kind="acceptance_check",
            payload={"check": "release", "required_verification": True},
            acceptance_delta="",
        )
        for sequence in (4, 5)
    )

    assert detect_acceptance_stall(productive, _policy())["matched"] is False
    assert detect_acceptance_stall(stalled, _policy())["event_ids"] == [
        "event-004",
        "event-005",
    ]


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
                kind="tool_call",
                state_fingerprint="state-a",
                expected_state_change=True,
            ),
            _event(6, kind="state_observation", state_fingerprint="state-b"),
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
        (_event(5, kind="handoff", payload={"new_plan_id": "next-plan"}),),
    ),
)
def test_documented_progress_between_identical_checks_suppresses_the_candidate(
    progress_events: tuple[Any, ...],
) -> None:
    from semantic_reheating.detectors import detect_acceptance_stall

    trace = (
        _event(
            4, kind="acceptance_check", payload={"check": "build"}, acceptance_delta=""
        ),
        *progress_events,
        _event(
            len(progress_events) + 5,
            kind="acceptance_check",
            payload={"check": "build"},
            acceptance_delta="",
        ),
    )

    assert detect_acceptance_stall(trace, _policy())["matched"] is False


def test_declared_payload_digest_is_comparable_but_window_cut_is_not() -> None:
    from semantic_reheating.detectors import detect_acceptance_stall

    digest_finding = detect_acceptance_stall(
        (_digest_event(4, acceptance_delta=""), _digest_event(5, acceptance_delta="")),
        _policy(),
    )
    cut_finding = detect_acceptance_stall(
        (
            _event(
                3, kind="acceptance_check", payload={"check": "a"}, acceptance_delta=""
            ),
            _event(4, kind="message", payload={"prose": "nothing happened"}),
            _event(
                5, kind="acceptance_check", payload={"check": "a"}, acceptance_delta=""
            ),
        ),
        _policy(window=2, threshold=0.0),
    )

    assert digest_finding["matched"] is True
    assert cut_finding["matched"] is False
    assert cut_finding["score"] == 0.0
    assert cut_finding["event_ids"] == ["event-005"]


def test_interleaved_prose_does_not_progress_or_reset_and_earliest_key_wins() -> None:
    from semantic_reheating.detectors import detect_acceptance_stall

    finding = detect_acceptance_stall(
        (
            _event(
                4,
                kind="acceptance_check",
                payload={"check": "first"},
                acceptance_delta="",
            ),
            _event(5, kind="message", payload={"claim": "I made progress"}),
            _event(
                6,
                kind="acceptance_check",
                payload={"check": "second"},
                acceptance_delta="",
            ),
            _event(
                7,
                kind="acceptance_check",
                payload={"check": "first"},
                acceptance_delta="",
            ),
            _event(
                8,
                kind="acceptance_check",
                payload={"check": "second"},
                acceptance_delta="",
            ),
        ),
        _policy(),
    )

    assert finding["matched"] is True
    assert finding["event_ids"] == ["event-004", "event-007"]


def test_finding_is_fresh_deterministic_redacted_and_does_not_mutate_inputs() -> None:
    from semantic_reheating.detectors import detect_acceptance_stall
    from semantic_reheating.validation import validate_public_artifact

    trace = (
        _event(
            4,
            kind="acceptance_check",
            payload={"check": "secret-check"},
            acceptance_delta="secret-delta",
        ),
        _event(
            5,
            kind="acceptance_check",
            payload={"check": "secret-check"},
            acceptance_delta="secret-delta",
        ),
    )
    source = tuple(event.to_dict() for event in trace)
    first = detect_acceptance_stall(trace, _policy())
    first["event_ids"].append("tampered")
    first["availability"]["notice"] = "tampered"
    second = detect_acceptance_stall(trace, _policy())

    assert first["finding_id"] == second["finding_id"]
    assert second["event_ids"] == ["event-004", "event-005"]
    assert "secret" not in repr(second)
    assert tuple(event.to_dict() for event in trace) == source
    assert validate_public_artifact("detector_finding", second) == second


def test_identity_failure_is_sanitized_but_resource_errors_propagate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import semantic_reheating.detectors.acceptance_stall as stall_module
    from semantic_reheating.detectors import DetectorInputError, detect_acceptance_stall

    def fail_identity(event: object) -> object:
        raise RuntimeError("IDENTITY_SECRET")

    monkeypatch.setattr(stall_module, "_identity", fail_identity)
    trace = (
        _event(
            4, kind="acceptance_check", payload={"check": "build"}, acceptance_delta=""
        ),
    )
    with pytest.raises(DetectorInputError) as raised:
        detect_acceptance_stall(trace, _policy())

    assert raised.value.code == "invalid_payload_identity"
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert "SECRET" not in repr(raised.value)


@pytest.mark.parametrize("resource_exception", (MemoryError, SystemExit))
def test_identity_and_progress_resource_failures_propagate(
    monkeypatch: pytest.MonkeyPatch, resource_exception: type[BaseException]
) -> None:
    import semantic_reheating.detectors.acceptance_stall as stall_module
    from semantic_reheating.detectors import detect_acceptance_stall

    expected = resource_exception("RESOURCE_SECRET")

    def fail_identity(event: object) -> object:
        raise expected

    monkeypatch.setattr(stall_module, "_identity", fail_identity)
    with pytest.raises(resource_exception) as raised:
        detect_acceptance_stall(
            (
                _event(
                    4,
                    kind="acceptance_check",
                    payload={"check": "build"},
                    acceptance_delta="",
                ),
            ),
            _policy(),
        )
    assert raised.value is expected

    monkeypatch.undo()
    expected = resource_exception("RESOURCE_SECRET")

    def fail_progress(trace: object) -> object:
        raise expected

    monkeypatch.setattr(stall_module, "classify_progress", fail_progress)
    with pytest.raises(resource_exception) as raised:
        detect_acceptance_stall(
            (
                _event(
                    4,
                    kind="acceptance_check",
                    payload={"check": "build"},
                    acceptance_delta="",
                ),
                _event(
                    5,
                    kind="acceptance_check",
                    payload={"check": "build"},
                    acceptance_delta="",
                ),
            ),
            _policy(),
        )
    assert raised.value is expected


def test_progress_failure_is_sanitized() -> None:
    import semantic_reheating.detectors.acceptance_stall as stall_module
    from semantic_reheating.detectors import DetectorInputError, detect_acceptance_stall

    original = stall_module.classify_progress

    def fail_progress(trace: object) -> object:
        raise RuntimeError("PROGRESS_SECRET")

    stall_module.classify_progress = fail_progress
    try:
        with pytest.raises(DetectorInputError) as raised:
            detect_acceptance_stall(
                (
                    _event(
                        4,
                        kind="acceptance_check",
                        payload={"check": "build"},
                        acceptance_delta="",
                    ),
                    _event(
                        5,
                        kind="acceptance_check",
                        payload={"check": "build"},
                        acceptance_delta="",
                    ),
                ),
                _policy(),
            )
    finally:
        stall_module.classify_progress = original

    assert raised.value.code == "invalid_progress_classification"
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_progress_checks_and_identity_work_are_linear_for_productive_reruns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    import semantic_reheating.detectors.acceptance_stall as stall_module
    from semantic_reheating.detectors import detect_acceptance_stall

    classified_lengths: list[int] = []
    identity_calls = 0
    original_identity = stall_module._identity

    def productive_progress(trace: tuple[Any, ...]) -> SimpleNamespace:
        classified_lengths.append(len(trace))
        return SimpleNamespace(made_progress=True)

    def counting_identity(event: Any) -> tuple[str, str] | None:
        nonlocal identity_calls
        identity_calls += 1
        return original_identity(event)

    monkeypatch.setattr(stall_module, "classify_progress", productive_progress)
    monkeypatch.setattr(stall_module, "_identity", counting_identity)
    trace = tuple(
        _event(
            sequence,
            kind="acceptance_check",
            payload={"check": "release", "required_verification": True},
            acceptance_delta="verified",
        )
        for sequence in range(1, 81)
    )

    finding = detect_acceptance_stall(trace, _policy(window=len(trace)))

    assert finding["matched"] is False
    assert identity_calls == len(trace)
    assert len(classified_lengths) == len(trace) - 1
    assert sum(classified_lengths) <= 2 * len(trace)
