"""Deterministic bounded state-cycle detection."""

from __future__ import annotations

from typing import Any

from semantic_reheating.models import TraceEvent, TraceKind
from semantic_reheating.progress import classify_progress

from . import _fail, _finding, _validated_inputs


def _has_cycle(observations: list[tuple[int, TraceEvent]], steps: int) -> bool:
    """Return whether the latest observations close a non-stable k-step cycle."""
    candidate = observations[-(steps + 1) :]
    first_fingerprint = candidate[0][1].state_fingerprint
    return first_fingerprint == candidate[-1][1].state_fingerprint and any(
        event.state_fingerprint != first_fingerprint for _, event in candidate[1:-1]
    )


def detect_cycle(trace: Any, policy: Any) -> dict[str, Any]:
    """Detect the earliest non-progressing state cycle of two through five steps."""
    window, parsed_policy = _validated_inputs(trace, policy, window_policy="repetition")
    observations: list[tuple[int, TraceEvent]] = []
    for position, event in enumerate(window):
        if (
            event.kind is not TraceKind.STATE_OBSERVATION
            or type(event.state_fingerprint) is not str
            or not event.state_fingerprint
        ):
            continue
        observations.append((position, event))
        for steps in range(2, min(5, len(observations) - 1) + 1):
            if not _has_cycle(observations, steps):
                continue
            start = observations[-(steps + 1)][0]
            candidate_window = window[start : position + 1]
            classification_failed = False
            try:
                made_progress = classify_progress(candidate_window).made_progress
            except (MemoryError, SystemExit):
                raise
            except Exception:  # noqa: BLE001 - public detector boundary is sanitized.
                classification_failed = True
                made_progress = False
            if classification_failed:
                _fail("invalid_progress_classification")
            if not made_progress:
                return _finding(
                    "cycle",
                    window,
                    parsed_policy,
                    [
                        candidate.event_id
                        for _, candidate in observations[-(steps + 1) :]
                    ],
                    True,
                )
    return _finding("cycle", window, parsed_policy, [window[-1].event_id], False)
