"""Deterministic bounded acceptance-stall detection."""

from __future__ import annotations

from typing import Any

from semantic_reheating.models import TraceEvent, TraceKind
from semantic_reheating.progress import classify_progress

from . import _fail, _finding, _identity, _validated_inputs


def _acceptance_key(event: TraceEvent) -> tuple[tuple[str, str], str] | None:
    """Return a comparable check identity and explicit exact delta, if present."""
    if event.kind is not TraceKind.ACCEPTANCE_CHECK:
        return None
    failed = False
    try:
        identity = _identity(event)
        source = event.to_dict()
        delta = source.get("acceptance_delta")
        explicit_delta = "acceptance_delta" in source
    except MemoryError:
        raise
    except Exception:  # noqa: BLE001 - public detector boundary is sanitized.
        failed = True
        identity = None
        delta = None
        explicit_delta = False
    if failed:
        _fail("invalid_payload_identity")
    if identity is None or not explicit_delta or type(delta) is not str:
        return None
    return (identity, delta)


def detect_acceptance_stall(trace: Any, policy: Any) -> dict[str, Any]:
    """Detect the earliest equivalent acceptance check without documented progress."""
    window, parsed_policy = _validated_inputs(
        trace, policy, window_policy="no_progress"
    )
    baselines: dict[tuple[tuple[str, str], str], tuple[int, str]] = {}
    for position, event in enumerate(window):
        key = _acceptance_key(event)
        if key is None:
            continue
        baseline = baselines.get(key)
        if baseline is None:
            baselines[key] = (position, event.event_id)
            continue
        baseline_position, baseline_id = baseline
        classification_failed = False
        try:
            made_progress = classify_progress(
                window[baseline_position : position + 1]
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
                "acceptance_stall",
                window,
                parsed_policy,
                [baseline_id, event.event_id],
                True,
                finding_class="no_progress",
            )
        baselines[key] = (position, event.event_id)
    return _finding(
        "acceptance_stall",
        window,
        parsed_policy,
        [window[-1].event_id],
        False,
        finding_class="no_progress",
    )
