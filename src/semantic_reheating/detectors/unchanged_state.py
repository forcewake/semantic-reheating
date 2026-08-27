"""Deterministic bounded unchanged expected-state detection."""

from __future__ import annotations

from typing import Any

from semantic_reheating.models import TraceEvent, TraceKind
from semantic_reheating.progress import classify_progress

from . import _fail, _finding, _validated_inputs


def _has_fingerprint(event: TraceEvent) -> str | None:
    """Return the sole allowed exact state identity, when present."""
    fingerprint = event.state_fingerprint
    return fingerprint if type(fingerprint) is str and fingerprint else None


def detect_unchanged_state(trace: Any, policy: Any) -> dict[str, Any]:
    """Detect an unmet explicit state-change expectation in the bounded window."""
    window, parsed_policy = _validated_inputs(
        trace, policy, window_policy="no_progress"
    )
    latest: tuple[str, int, str] | None = None
    pending: dict[str, tuple[int, str, int, str]] = {}
    for position, event in enumerate(window):
        fingerprint = _has_fingerprint(event)
        if event.kind is TraceKind.STATE_OBSERVATION and fingerprint is not None:
            expectation = pending.get(fingerprint)
            if expectation is not None:
                baseline_position, baseline_id, expectation_position, expectation_id = (
                    expectation
                )
                classification_failed = False
                try:
                    made_progress = classify_progress(
                        window[
                            min(baseline_position, expectation_position) : position + 1
                        ]
                    ).made_progress
                except (MemoryError, SystemExit):
                    raise
                except Exception:  # noqa: BLE001 - public detector boundary is sanitized.
                    classification_failed = True
                    made_progress = False
                if classification_failed:
                    _fail("invalid_progress_classification")
                if not made_progress:
                    return _finding(
                        "unchanged_state",
                        window,
                        parsed_policy,
                        [baseline_id, expectation_id, event.event_id],
                        True,
                        finding_class="no_progress",
                    )
                pending.clear()
            elif pending:
                pending.clear()
            latest = (fingerprint, position, event.event_id)
        elif fingerprint is not None:
            latest = (fingerprint, position, event.event_id)
        if event.expected_state_change is True:
            baseline = (
                (fingerprint, position, event.event_id)
                if fingerprint is not None
                else latest
            )
            if baseline is not None:
                pending.setdefault(
                    baseline[0],
                    (baseline[1], baseline[2], position, event.event_id),
                )
    return _finding(
        "unchanged_state",
        window,
        parsed_policy,
        [window[-1].event_id],
        False,
        finding_class="no_progress",
    )
