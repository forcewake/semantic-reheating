"""Deterministic exact tool call/result repetition detection."""

from __future__ import annotations

from typing import Any

from semantic_reheating.models import TraceKind

from . import _finding, _identity, _validated_inputs


def detect_exact_repetition(trace: Any, policy: Any) -> dict[str, Any]:
    """Detect a repeated tool call/result identity pair in the bounded window."""
    window, parsed_policy = _validated_inputs(trace, policy)
    calls: dict[str, tuple[str, str]] = {}
    first_pairs: dict[tuple[tuple[str, str], tuple[str, str]], tuple[str, str]] = {}
    positions = {event.event_id: position for position, event in enumerate(window)}
    for event in window:
        if event.kind is TraceKind.TOOL_CALL:
            identity = _identity(event)
            if identity is not None:
                calls[event.event_id] = identity
            continue
        if event.kind is not TraceKind.TOOL_RESULT or event.parent_event_id not in calls:
            continue
        result_identity = _identity(event)
        if result_identity is None:
            continue
        pair = (calls[event.parent_event_id], result_identity)
        current = (event.parent_event_id, event.event_id)
        first = first_pairs.get(pair)
        if first is not None and current[0] != first[0]:
            support = sorted((*first, *current), key=positions.__getitem__)
            return _finding("exact_repetition", window, parsed_policy, support, True)
        if first is None:
            first_pairs[pair] = current
    return _finding("exact_repetition", window, parsed_policy, [window[-1].event_id], False)
