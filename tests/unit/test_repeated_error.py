"""Repeated normalized error detector contract tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _policy(*, window: int = 20) -> Any:
    from semantic_reheating.models import RunPolicy

    source = json.loads(
        (PROJECT_ROOT / "tests/fixtures/contracts/minimal-run-policy.json").read_text()
    )
    source["detectors"]["windows"]["repetition_events"] = window
    return RunPolicy.from_dict(source)


def _event(
    sequence: int, *, kind: str = "message", payload: object | None = None, **fields: object
) -> Any:
    from semantic_reheating.models import TraceEvent

    source: dict[str, object] = {
        "contract_version": "1.0",
        "run_id": "run-errors",
        "event_id": f"event-{sequence:03d}",
        "sequence": sequence,
        "kind": kind,
        "actor": "controller",
        "effect_class": "read_only",
        "payload": {} if payload is None else payload,
    }
    source.update(fields)
    return TraceEvent.from_dict(source)


def test_same_declared_error_identity_under_same_tool_input_matches() -> None:
    from semantic_reheating.detectors import detect_repeated_error

    trace = (
        _event(4, kind="tool_call", payload={"tool": "lookup", "q": {"a": 1, "b": 2}}),
        _event(5, kind="error", error_fingerprint="error-timeout"),
        _event(
            6,
            kind="tool_call",
            payload={"request_id": "volatile", "q": {"b": 2, "a": 1}, "tool": "lookup"},
        ),
        _event(7, kind="error", error_fingerprint="error-timeout"),
    )

    finding = detect_repeated_error(trace, _policy())

    assert finding["matched"] is True
    assert finding["score"] == 1.0
    assert finding["event_ids"] == ["event-005", "event-007"]
    assert finding["detector_name"] == "repeated_error"


def test_changed_input_or_error_does_not_repeat_and_prose_does_not_reset() -> None:
    from semantic_reheating.detectors import detect_repeated_error

    changed_input = (
        _event(4, kind="tool_call", payload={"tool": "lookup", "hypothesis_test_input": {"q": "one"}}),
        _event(5, kind="error", error_fingerprint="error-timeout"),
        _event(6, kind="tool_call", payload={"tool": "lookup", "hypothesis_test_input": {"q": "two"}}),
        _event(7, kind="error", error_fingerprint="error-timeout"),
    )
    changed_error = (
        _event(4, kind="tool_call", payload={"tool": "lookup", "q": "one"}),
        _event(5, kind="error", error_fingerprint="error-a"),
        _event(6, kind="error", error_fingerprint="error-b"),
    )
    prose_only = (
        _event(4, kind="tool_call", payload={"tool": "lookup", "q": "one"}),
        _event(5, kind="error", error_fingerprint="error-a"),
        _event(6, kind="plan", payload={"untrusted": "new prose"}),
        _event(7, kind="message", payload={"untrusted": "more prose"}),
        _event(8, kind="error", error_fingerprint="error-a"),
    )

    assert detect_repeated_error(changed_input, _policy())["matched"] is False
    assert detect_repeated_error(changed_error, _policy())["matched"] is False
    assert detect_repeated_error(prose_only, _policy())["event_ids"] == ["event-005", "event-008"]


def test_no_call_context_and_window_cut_are_explicit() -> None:
    from semantic_reheating.detectors import detect_repeated_error

    no_call = (
        _event(4, kind="error", error_fingerprint="error-a"),
        _event(5, kind="error", error_fingerprint="error-a"),
    )
    cut = (
        _event(3, kind="tool_call", payload={"tool": "lookup", "q": "one"}),
        _event(4, kind="error", error_fingerprint="error-a"),
        _event(5, kind="tool_call", payload={"tool": "other", "q": "two"}),
        _event(6, kind="error", error_fingerprint="error-a"),
    )

    assert detect_repeated_error(no_call, _policy())["event_ids"] == ["event-004", "event-005"]
    finding = detect_repeated_error(cut, _policy(window=3))
    assert finding["matched"] is False
    assert finding["event_ids"] == ["event-006"]
