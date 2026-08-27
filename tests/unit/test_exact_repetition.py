"""Exact tool call/result repetition detector contract tests."""

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
    source["detectors"]["windows"]["repetition_events"] = window
    source["detectors"]["thresholds"]["repetition_score"] = threshold
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
        "run_id": "run-detector",
        "event_id": f"event-{sequence:03d}",
        "sequence": sequence,
        "kind": kind,
        "actor": "controller",
        "effect_class": "read_only",
        "payload": {} if payload is None else payload,
    }
    source.update(fields)
    return TraceEvent.from_dict(source)


def test_nonempty_window_returns_a_closed_unmatched_redacted_finding() -> None:
    from semantic_reheating.detectors import detect_exact_repetition
    from semantic_reheating.validation import validate_public_artifact

    trace = (_event(4, kind="tool_call", payload={"tool": "lookup", "q": "one"}),)

    finding = detect_exact_repetition(trace, _policy())

    assert finding == {
        "contract_version": "1.0",
        "run_id": "run-detector",
        "finding_id": finding["finding_id"],
        "detector_name": "exact_repetition",
        "detector_version": "1.0",
        "matched": False,
        "score": 0.0,
        "finding_class": "repetition",
        "event_ids": ["event-004"],
        "reason_code": "repetition_detected",
        "explanation": "Repetition evidence was not detected in the evaluated window.",
        "availability": {
            "status": "available",
            "notice": "Deterministic detector completed with redacted evidence only.",
        },
    }
    assert validate_public_artifact("detector_finding", finding) == finding
    assert type(finding) is dict
    assert finding["finding_id"].startswith("exact-repetition-")
    assert len(finding["finding_id"]) <= 128


def test_equivalent_tool_call_result_pairs_match_with_trace_ordered_support() -> None:
    from semantic_reheating.detectors import detect_exact_repetition

    trace = (
        _event(
            4, kind="tool_call", payload={"tool": "lookup", "query": {"a": 1, "b": 2}}
        ),
        _event(
            5,
            kind="tool_result",
            parent_event_id="event-004",
            payload={"items": ["one"]},
        ),
        _event(
            6,
            kind="tool_call",
            payload={
                "query": {"b": 2, "a": 1},
                "request_id": "volatile",
                "tool": "lookup",
            },
        ),
        _event(
            7,
            kind="tool_result",
            parent_event_id="event-006",
            payload={"request_id": "volatile", "items": ["one"]},
        ),
    )

    finding = detect_exact_repetition(trace, _policy())

    assert finding["matched"] is True
    assert finding["score"] == 1.0
    assert finding["event_ids"] == ["event-004", "event-005", "event-006", "event-007"]
    assert (
        finding["explanation"]
        == "Equivalent repetition evidence was detected in the evaluated window."
    )


@pytest.mark.parametrize(
    "trace",
    [
        (
            _event(4, kind="tool_call", payload={"tool": "lookup", "q": "one"}),
            _event(
                5, kind="tool_result", parent_event_id="event-004", payload={"value": 1}
            ),
            _event(6, kind="tool_call", payload={"tool": "lookup", "q": "two"}),
            _event(
                7, kind="tool_result", parent_event_id="event-006", payload={"value": 1}
            ),
        ),
        (
            _event(4, kind="tool_call", payload={"tool": "lookup", "q": "one"}),
            _event(
                5, kind="tool_result", parent_event_id="event-004", payload={"value": 1}
            ),
            _event(6, kind="tool_call", payload={"tool": "lookup", "q": "one"}),
            _event(
                7, kind="tool_result", parent_event_id="event-006", payload={"value": 2}
            ),
        ),
        (
            _event(4, kind="tool_call", payload={"tool": "lookup", "q": "one"}),
            _event(
                5, kind="tool_result", parent_event_id="event-004", payload={"value": 1}
            ),
            _event(6, kind="tool_call", payload={"tool": "lookup", "q": "one"}),
            _event(
                7,
                kind="tool_result",
                parent_event_id="event-missing",
                payload={"value": 1},
            ),
        ),
    ],
)
def test_changed_material_identity_or_wrong_parent_does_not_match(
    trace: tuple[Any, ...],
) -> None:
    from semantic_reheating.detectors import detect_exact_repetition

    finding = detect_exact_repetition(trace, _policy(threshold=0.0))

    assert finding["matched"] is False
    assert finding["score"] == 0.0
    assert finding["event_ids"] == ["event-007"]


def test_declared_payload_digests_are_stable_identities_but_refs_are_not() -> None:
    from semantic_reheating.detectors import detect_exact_repetition

    def digest_event(sequence: int, kind: str, **fields: object) -> Any:
        source = _event(sequence, kind=kind, **fields).to_dict()
        source.pop("payload")
        source["payload_digest"] = "declared-content-a"
        from semantic_reheating.models import TraceEvent

        return TraceEvent.from_dict(source)

    digest_trace = (
        digest_event(4, "tool_call"),
        digest_event(5, "tool_result", parent_event_id="event-004"),
        digest_event(6, "tool_call"),
        digest_event(7, "tool_result", parent_event_id="event-006"),
    )
    ref_trace = []
    for sequence, kind, parent in (
        (4, "tool_call", None),
        (5, "tool_result", "event-004"),
        (6, "tool_call", None),
        (7, "tool_result", "event-006"),
    ):
        fields = {"parent_event_id": parent} if parent is not None else {}
        source = _event(sequence, kind=kind, **fields).to_dict()
        source.pop("payload")
        source["payload_ref"] = "same-reference"
        from semantic_reheating.models import TraceEvent

        ref_trace.append(TraceEvent.from_dict(source))

    assert detect_exact_repetition(digest_trace, _policy())["matched"] is True
    assert detect_exact_repetition(tuple(ref_trace), _policy())["matched"] is False


def test_window_cut_finding_id_is_deterministic_and_sources_are_not_retained() -> None:
    from semantic_reheating.detectors import detect_exact_repetition

    mutable_payload = {"tool": "lookup", "q": "one"}
    trace = (
        _event(3, kind="tool_call", payload=mutable_payload),
        _event(
            4, kind="tool_result", parent_event_id="event-003", payload={"ok": True}
        ),
        _event(5, kind="tool_call", payload={"tool": "other"}),
        _event(
            6, kind="tool_result", parent_event_id="event-005", payload={"ok": True}
        ),
    )
    first = detect_exact_repetition(trace, _policy(window=3))
    second = detect_exact_repetition(trace, _policy(window=3))
    mutable_payload["secret"] = "do-not-retain"

    assert first == second
    assert first["matched"] is False
    assert first["event_ids"] == ["event-006"]
    assert "secret" not in repr(first)


def test_two_results_for_one_call_do_not_form_a_repeated_pair() -> None:
    from semantic_reheating.detectors import detect_exact_repetition

    trace = (
        _event(4, kind="tool_call", payload={"tool": "lookup", "q": "one"}),
        _event(
            5, kind="tool_result", parent_event_id="event-004", payload={"value": 1}
        ),
        _event(
            6, kind="tool_result", parent_event_id="event-004", payload={"value": 1}
        ),
    )

    finding = detect_exact_repetition(trace, _policy())

    assert finding["matched"] is False
    assert finding["score"] == 0.0
    assert finding["event_ids"] == ["event-006"]


def test_distinct_calls_with_shared_declared_digest_match_their_results() -> None:
    from semantic_reheating.detectors import detect_exact_repetition
    from semantic_reheating.models import TraceEvent

    def declared_call(sequence: int, attempt: str) -> Any:
        source = _event(
            sequence,
            kind="tool_call",
            payload={"tool": "lookup", "attempt": attempt},
        ).to_dict()
        source.pop("payload")
        source["payload_digest"] = "shared-declared-call"
        return TraceEvent.from_dict(source)

    trace = (
        declared_call(4, "first"),
        _event(
            5, kind="tool_result", parent_event_id="event-004", payload={"value": 1}
        ),
        declared_call(6, "second"),
        _event(
            7, kind="tool_result", parent_event_id="event-006", payload={"value": 1}
        ),
    )

    finding = detect_exact_repetition(trace, _policy())

    assert finding["matched"] is True
    assert finding["score"] == 1.0
    assert finding["event_ids"] == ["event-004", "event-005", "event-006", "event-007"]


def test_detector_boundary_rejects_hostile_or_forged_inputs_without_leaks() -> None:
    from collections.abc import Mapping

    from semantic_reheating.detectors import DetectorInputError, detect_exact_repetition
    from semantic_reheating.models import RunPolicy, TraceEvent

    class TraceList(list[Any]):
        pass

    class TraceChild(TraceEvent):
        pass

    class PolicyChild(RunPolicy):
        pass

    class HostileSource(Mapping[str, object]):
        def __iter__(self):
            raise RuntimeError("SOURCE_SECRET")

        def __len__(self) -> int:
            return 1

        def __getitem__(self, key: str) -> object:
            raise RuntimeError("SOURCE_SECRET")

    forged_trace = object.__new__(TraceEvent)
    object.__setattr__(forged_trace, "_source", HostileSource())
    forged_policy = object.__new__(RunPolicy)
    object.__setattr__(forged_policy, "_source", HostileSource())
    valid_event = _event(4)
    valid_policy = _policy()
    invalid_cases = (
        ((), valid_policy),
        (TraceList([valid_event]), valid_policy),
        ([{"raw": "dict"}], valid_policy),
        ((object.__new__(TraceChild),), valid_policy),
        ((forged_trace,), valid_policy),
        ((valid_event,), object.__new__(PolicyChild)),
        ((valid_event,), forged_policy),
    )
    for trace, policy in invalid_cases:
        with pytest.raises(DetectorInputError) as raised:
            detect_exact_repetition(trace, policy)
        assert raised.value.code in {
            "empty_trace_window",
            "invalid_trace_window",
            "invalid_trace_event",
            "invalid_run_policy",
        }
        assert raised.value.args == ("Invalid detector input",)
        assert raised.value.__cause__ is None
        assert raised.value.__context__ is None
        assert "SECRET" not in repr(raised.value)


@pytest.mark.parametrize(
    "detector_name", ["detect_exact_repetition", "detect_repeated_error"]
)
@pytest.mark.parametrize(
    ("trace", "code"),
    [
        (
            (
                _event(4, kind="tool_call", payload={"tool": "lookup"}),
                _event(
                    5,
                    kind="error",
                    run_id="other-run",
                    error_fingerprint="error-boundary",
                ),
            ),
            "run_id_mismatch",
        ),
        (
            (
                _event(4, kind="tool_call", payload={"tool": "lookup"}),
                _event(6, kind="error", error_fingerprint="error-boundary"),
            ),
            "sequence_gap",
        ),
    ],
)
def test_detectors_require_single_contiguous_run_with_stable_codes(
    detector_name: str, trace: tuple[Any, ...], code: str
) -> None:
    import semantic_reheating.detectors as detector_module
    from semantic_reheating.detectors import DetectorInputError

    with pytest.raises(DetectorInputError) as raised:
        getattr(detector_module, detector_name)(trace, _policy())

    assert raised.value.code == code
    assert raised.value.args == ("Invalid detector input",)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


@pytest.mark.parametrize(
    "detector_name", ["detect_exact_repetition", "detect_repeated_error"]
)
def test_detectors_reject_duplicate_event_ids_with_sanitized_boundary_error(
    detector_name: str,
) -> None:
    import semantic_reheating.detectors as detector_module
    from semantic_reheating.detectors import DetectorInputError
    from semantic_reheating.models import TraceEvent

    duplicate = _event(5, kind="error", error_fingerprint="error-boundary").to_dict()
    duplicate["event_id"] = "event-004"
    trace = (
        _event(4, kind="tool_call", payload={"tool": "lookup", "q": "one"}),
        TraceEvent.from_dict(duplicate),
    )

    with pytest.raises(DetectorInputError) as raised:
        getattr(detector_module, detector_name)(trace, _policy())

    assert raised.value.code == "duplicate_event_id"
    assert raised.value.args == ("Invalid detector input",)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_finding_validation_failure_does_not_retain_internal_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import semantic_reheating.detectors as detector_module
    from semantic_reheating.detectors import DetectorInputError

    def reject_finding(*args: object, **kwargs: object) -> None:
        raise RuntimeError("FINDING_SECRET")

    def exception_graph(error: BaseException) -> list[BaseException]:
        pending = [error]
        seen: set[int] = set()
        graph: list[BaseException] = []
        while pending:
            current = pending.pop()
            if id(current) in seen:
                continue
            seen.add(id(current))
            graph.append(current)
            pending.extend(
                parent
                for parent in (current.__cause__, current.__context__)
                if parent is not None
            )
        return graph

    monkeypatch.setattr(detector_module, "validate_public_artifact", reject_finding)
    trace = (_event(4, kind="tool_call", payload={"tool": "lookup", "q": "one"}),)
    for detector in (
        detector_module.detect_exact_repetition,
        detector_module.detect_repeated_error,
    ):
        with pytest.raises(DetectorInputError) as raised:
            detector(trace, _policy())
        assert raised.value.code == "invalid_detector_finding"
        assert raised.value.args == ("Invalid detector input",)
        assert raised.value.__cause__ is None
        assert raised.value.__context__ is None
        assert all(
            "FINDING_SECRET" not in repr(error)
            for error in exception_graph(raised.value)
        )


@pytest.mark.parametrize(
    "detector_name", ["detect_exact_repetition", "detect_repeated_error"]
)
@pytest.mark.parametrize("resource_exception", [MemoryError, SystemExit])
def test_event_revalidation_preserves_resource_exceptions(
    monkeypatch: pytest.MonkeyPatch,
    detector_name: str,
    resource_exception: type[BaseException],
) -> None:
    import semantic_reheating.detectors as detector_module
    from semantic_reheating.models import TraceEvent

    expected = resource_exception("RESOURCE_SECRET")

    def fail_revalidation(self: TraceEvent) -> dict[str, object]:
        raise expected

    trace = (_event(4, kind="tool_call", payload={"tool": "lookup"}),)
    monkeypatch.setattr(TraceEvent, "to_dict", fail_revalidation)

    with pytest.raises(resource_exception) as raised:
        getattr(detector_module, detector_name)(trace, _policy())

    assert raised.value is expected
    assert raised.value.args == ("RESOURCE_SECRET",)


@pytest.mark.parametrize(
    "detector_name", ["detect_exact_repetition", "detect_repeated_error"]
)
@pytest.mark.parametrize("resource_exception", [MemoryError, SystemExit])
def test_policy_revalidation_preserves_resource_exceptions(
    monkeypatch: pytest.MonkeyPatch,
    detector_name: str,
    resource_exception: type[BaseException],
) -> None:
    import semantic_reheating.detectors as detector_module
    from semantic_reheating.models import RunPolicy

    expected = resource_exception("RESOURCE_SECRET")

    def fail_revalidation(self: RunPolicy) -> dict[str, object]:
        raise expected

    trace = (_event(4, kind="tool_call", payload={"tool": "lookup"}),)
    monkeypatch.setattr(RunPolicy, "to_dict", fail_revalidation)

    with pytest.raises(resource_exception) as raised:
        getattr(detector_module, detector_name)(trace, _policy())

    assert raised.value is expected
    assert raised.value.args == ("RESOURCE_SECRET",)


@pytest.mark.parametrize(
    "detector_name", ["detect_exact_repetition", "detect_repeated_error"]
)
@pytest.mark.parametrize("resource_exception", [MemoryError, SystemExit])
def test_payload_identity_preserves_resource_exceptions(
    monkeypatch: pytest.MonkeyPatch,
    detector_name: str,
    resource_exception: type[BaseException],
) -> None:
    import semantic_reheating.detectors as detector_module

    expected = resource_exception("RESOURCE_SECRET")

    def fail_fingerprint(payload: object) -> object:
        raise expected

    trace = (_event(4, kind="tool_call", payload={"tool": "lookup"}),)
    monkeypatch.setattr(detector_module, "action_fingerprint", fail_fingerprint)

    with pytest.raises(resource_exception) as raised:
        getattr(detector_module, detector_name)(trace, _policy())

    assert raised.value is expected
    assert raised.value.args == ("RESOURCE_SECRET",)


@pytest.mark.parametrize(
    "detector_name", ["detect_exact_repetition", "detect_repeated_error"]
)
@pytest.mark.parametrize("resource_exception", [MemoryError, SystemExit])
def test_finding_validation_preserves_resource_exceptions(
    monkeypatch: pytest.MonkeyPatch,
    detector_name: str,
    resource_exception: type[BaseException],
) -> None:
    import semantic_reheating.detectors as detector_module

    expected = resource_exception("RESOURCE_SECRET")

    def fail_validation(*args: object, **kwargs: object) -> None:
        raise expected

    trace = (_event(4, kind="tool_call", payload={"tool": "lookup"}),)
    monkeypatch.setattr(detector_module, "validate_public_artifact", fail_validation)

    with pytest.raises(resource_exception) as raised:
        getattr(detector_module, detector_name)(trace, _policy())

    assert raised.value is expected
    assert raised.value.args == ("RESOURCE_SECRET",)


def test_findings_are_fresh_plain_copies() -> None:
    from semantic_reheating.detectors import detect_exact_repetition

    trace = (_event(4, kind="tool_call", payload={"tool": "lookup"}),)
    first = detect_exact_repetition(trace, _policy())
    first["event_ids"].append("event-added")
    first["availability"]["notice"] = "mutated"
    second = detect_exact_repetition(trace, _policy())

    assert second["event_ids"] == ["event-004"]
    assert (
        second["availability"]["notice"]
        == "Deterministic detector completed with redacted evidence only."
    )


def test_detector_identity_work_is_linear_and_evidence_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import semantic_reheating.detectors as detector_module
    from semantic_reheating.detectors import (
        detect_exact_repetition,
        detect_repeated_error,
    )

    calls = 0
    original = detector_module.action_fingerprint

    def counted(value: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return original(value, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(detector_module, "action_fingerprint", counted)
    pairs = tuple(
        event
        for index in range(120)
        for event in (
            _event(
                index * 2 + 1, kind="tool_call", payload={"tool": "lookup", "q": index}
            ),
            _event(
                index * 2 + 2,
                kind="tool_result",
                parent_event_id=f"event-{index * 2 + 1:03d}",
                payload={"value": index},
            ),
        )
    )
    errors = tuple(
        event
        for index in range(120)
        for event in (
            _event(
                index * 2 + 1, kind="tool_call", payload={"tool": "lookup", "q": index}
            ),
            _event(index * 2 + 2, kind="error", error_fingerprint=f"error-{index}"),
        )
    )

    exact = detect_exact_repetition(pairs, _policy(window=len(pairs)))
    repeated = detect_repeated_error(errors, _policy(window=len(errors)))

    assert exact["matched"] is False
    assert repeated["matched"] is False
    assert len(exact["event_ids"]) <= 1000
    assert len(repeated["event_ids"]) <= 1000
    assert calls <= len(pairs) + len(errors)
