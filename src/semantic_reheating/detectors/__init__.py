"""Shared safe boundaries and public finding construction for detectors."""

from __future__ import annotations

from hashlib import sha256
from itertools import pairwise
from typing import Any, Literal, NoReturn, cast

from semantic_reheating.canonical import action_fingerprint
from semantic_reheating.models import RunPolicy, TraceEvent
from semantic_reheating.validation import validate_public_artifact


class DetectorInputError(ValueError):
    """Sanitized failure from a detector input boundary."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__("Invalid detector input")


def _fail(code: str) -> NoReturn:
    raise DetectorInputError(code) from None


def _validated_inputs(
    trace: Any,
    policy: Any,
    *,
    window_policy: Literal["repetition", "no_progress"] = "repetition",
) -> tuple[tuple[TraceEvent, ...], RunPolicy]:
    if type(trace) not in (list, tuple):
        _fail("invalid_trace_window")
    if not trace:
        _fail("empty_trace_window")
    parsed: list[TraceEvent] = []
    trace_failure: str | None = None
    try:
        for event in trace:
            if type(event) is not TraceEvent:
                trace_failure = "invalid_trace_event"
                break
            parsed.append(TraceEvent.from_dict(event.to_dict()))
    except MemoryError:
        raise
    except Exception:  # noqa: BLE001
        trace_failure = "invalid_trace_event"
    if trace_failure is not None:
        _fail(trace_failure)
    event_ids: set[str] = set()
    for event in parsed:
        if event.event_id in event_ids:
            _fail("duplicate_event_id")
        event_ids.add(event.event_id)
    if any(current.run_id != parsed[0].run_id for current in parsed[1:]):
        _fail("run_id_mismatch")
    if any(
        current.sequence != previous.sequence + 1
        for previous, current in pairwise(parsed)
    ):
        _fail("sequence_gap")
    if type(policy) is not RunPolicy:
        _fail("invalid_run_policy")
    policy_failure = False
    try:
        parsed_policy = RunPolicy.from_dict(policy.to_dict())
    except MemoryError:
        raise
    except Exception:  # noqa: BLE001
        policy_failure = True
        parsed_policy = None
    if policy_failure or type(parsed_policy) is not RunPolicy:
        _fail("invalid_run_policy")
    parsed_policy = cast(RunPolicy, parsed_policy)
    window_sizes = {
        "repetition": parsed_policy.detectors.windows.repetition_events,
        "no_progress": parsed_policy.detectors.windows.no_progress_events,
    }
    if window_policy not in window_sizes:
        _fail("invalid_detector_window_policy")
    return (
        tuple(parsed[-window_sizes[window_policy] :]),
        parsed_policy,
    )


def _identity(event: TraceEvent) -> tuple[str, str] | None:
    """Return a declared stable digest or a canonical payload identity, never a ref."""
    identity: tuple[str, str] | None = None
    failed = False
    try:
        source = event.to_dict()
        digest = source.get("payload_digest")
        if type(digest) is str and digest:
            identity = ("declared_digest", digest)
        elif "payload" in source:
            identity = ("payload", action_fingerprint(source["payload"]).digest)
    except MemoryError:
        raise
    except Exception:  # noqa: BLE001 - sanitize all hostile sources.
        failed = True
    if failed:
        _fail("invalid_payload_identity")
    return identity


_FINDING_CLASSES = {
    "repetition": (
        "repetition_score",
        "repetition_detected",
        "Equivalent repetition evidence was detected in the evaluated window.",
        "Repetition evidence was not detected in the evaluated window.",
    ),
    "no_progress": (
        "no_progress_score",
        "no_progress_detected",
        "No-progress evidence was detected in the evaluated window.",
        "No-progress evidence was not detected in the evaluated window.",
    ),
    "budget": (
        "budget_score",
        "budget_limit_reached",
        "Budget-limit evidence was detected in the evaluated window.",
        "Budget-limit evidence was not detected in the evaluated window.",
    ),
}


def _finding(
    detector_name: str,
    trace: tuple[TraceEvent, ...],
    policy: RunPolicy,
    event_ids: list[str],
    candidate: bool,
    *,
    finding_class: Literal["repetition", "no_progress", "budget"] = "repetition",
) -> dict[str, Any]:
    event_ids = list(dict.fromkeys(event_ids))[:1000]
    if not event_ids:
        _fail("invalid_detector_support")
    finding_details = _FINDING_CLASSES.get(finding_class)
    if finding_details is None:
        _fail("invalid_detector_finding_class")
    threshold_name, reason_code, matched_explanation, unmatched_explanation = (
        finding_details
    )
    score = 1.0 if candidate else 0.0
    threshold = getattr(policy.detectors.thresholds, threshold_name)
    matched = candidate and score >= threshold
    digest_input = "\x1f".join((detector_name, "1.0", trace[0].run_id, *event_ids))
    availability = {
        "status": "available",
        "notice": "Deterministic detector completed with redacted evidence only.",
    }
    finding = {
        "contract_version": "1.0",
        "run_id": trace[0].run_id,
        "finding_id": f"{detector_name.replace('_', '-')}-{sha256(digest_input.encode()).hexdigest()}",
        "detector_name": detector_name,
        "detector_version": "1.0",
        "matched": matched,
        "score": score,
        "finding_class": finding_class,
        "event_ids": event_ids,
        "reason_code": reason_code,
        "explanation": matched_explanation if candidate else unmatched_explanation,
        "availability": availability,
    }
    finding_invalid = False
    try:
        validate_public_artifact("detector_finding", finding)
    except MemoryError:
        raise
    except Exception:  # noqa: BLE001
        finding_invalid = True
    if finding_invalid:
        _fail("invalid_detector_finding")
    return {**finding, "event_ids": list(event_ids), "availability": dict(availability)}


from .cycle import (
    detect_cycle,  # noqa: E402, RUF100 - shared helpers must exist before public submodule imports.
)
from .exact_repetition import (
    detect_exact_repetition,  # noqa: E402, RUF100 - shared helpers must exist before public submodule imports.
)
from .repeated_error import (
    detect_repeated_error,  # noqa: E402, RUF100 - shared helpers must exist before public submodule imports.
)
from .unchanged_state import (
    detect_unchanged_state,  # noqa: E402, RUF100 - shared helpers must exist before public submodule imports.
)

__all__ = [
    "DetectorInputError",
    "detect_cycle",
    "detect_exact_repetition",
    "detect_repeated_error",
    "detect_unchanged_state",
]
