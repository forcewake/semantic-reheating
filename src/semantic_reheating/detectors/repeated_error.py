"""Deterministic repeated declared-error detection."""

from __future__ import annotations

from typing import Any

from semantic_reheating.models import TraceKind

from . import _finding, _identity, _validated_inputs


def detect_repeated_error(trace: Any, policy: Any) -> dict[str, Any]:
    """Detect a repeated normalized error under the same explicit tool input."""
    window, parsed_policy = _validated_inputs(trace, policy)
    context: tuple[str, str] | tuple[str, str, str] | None = None
    first_errors: dict[
        tuple[str, tuple[str, str] | tuple[str, str, str] | None], str
    ] = {}
    for event in window:
        if event.kind is TraceKind.TOOL_CALL:
            identity = _identity(event)
            context = (
                identity
                if identity is not None
                else ("unidentified_call", event.event_id, "")
            )
            continue
        if event.kind is not TraceKind.ERROR or not event.error_fingerprint:
            continue
        key = (event.error_fingerprint, context)
        first = first_errors.get(key)
        if first is not None:
            return _finding(
                "repeated_error", window, parsed_policy, [first, event.event_id], True
            )
        first_errors[key] = event.event_id
    return _finding(
        "repeated_error", window, parsed_policy, [window[-1].event_id], False
    )
