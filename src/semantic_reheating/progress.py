"""Deterministic progress classification from validated trace facts.

Payloads are opaque except for the exact documented shapes interpreted by this
module.  Classification never treats message or plan prose as evidence.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from itertools import pairwise
from typing import Any

from .canonical import action_fingerprint
from .models import ModelValidationError, TraceEvent, TraceKind


class ProgressClassificationError(ValueError):
    """Sanitized failure from the progress-classification boundary."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__("Invalid progress classification input")


class ProgressReason(str, Enum):
    """Stable trace-supported reasons that can establish progress."""

    PAGINATION_ADVANCED = "pagination_advanced"
    BATCH_ITEM_CHANGED = "batch_item_changed"
    HYPOTHESIS_INPUT_CHANGED = "hypothesis_input_changed"
    ERROR_CHANGED = "error_changed"
    STACK_FRAME_ADDED = "stack_frame_added"
    EVIDENCE_ADDED = "evidence_added"
    HYPOTHESIS_ELIMINATED = "hypothesis_eliminated"
    REQUIRED_ACCEPTANCE_VERIFIED = "required_acceptance_verified"
    PRODUCTIVE_HANDOFF = "productive_handoff"
    EXPECTED_STATE_CHANGE_OBSERVED = "expected_state_change_observed"
    POLL_CONVERGING = "poll_converging"


@dataclass(frozen=True, slots=True)
class ProgressAssessment:
    """Immutable, debug-safe outcome with trace event identifiers only."""

    made_progress: bool
    reason_codes: tuple[ProgressReason, ...]
    supporting_event_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        ProgressAssessment._validate_state(self)

    def _validate_state(self) -> None:
        if (
            type(self) is not ProgressAssessment
            or type(self.made_progress) is not bool
            or type(self.reason_codes) is not tuple
            or type(self.supporting_event_ids) is not tuple
            or any(type(reason) is not ProgressReason for reason in self.reason_codes)
            or any(type(event_id) is not str for event_id in self.supporting_event_ids)
            or len(self.reason_codes) != len(set(self.reason_codes))
            or len(self.supporting_event_ids) != len(set(self.supporting_event_ids))
            or self.made_progress is not bool(self.reason_codes)
            or (self.made_progress and not self.supporting_event_ids)
            or (not self.made_progress and (self.reason_codes or self.supporting_event_ids))
        ):
            raise ProgressClassificationError("invalid_assessment_state")

    def to_dict(self) -> dict[str, bool | list[str]]:
        """Return a fresh JSON-safe public representation after revalidation."""
        ProgressAssessment._validate_state(self)
        return {
            "made_progress": self.made_progress,
            "reason_codes": [reason.value for reason in self.reason_codes],
            "supporting_event_ids": list(self.supporting_event_ids),
        }


def _validated_window(trace: Any) -> tuple[TraceEvent, ...]:
    """Return an exact-model contiguous window, rebuilt at the public boundary."""
    if type(trace) not in (list, tuple):
        raise ProgressClassificationError("invalid_trace_window")
    parsed: list[TraceEvent] = []
    code: str | None = None
    try:
        for event in trace:
            if type(event) is not TraceEvent:
                code = "invalid_trace_event"
                break
            parsed.append(TraceEvent.from_dict(event.to_dict()))
    except ModelValidationError as error:
        code = error.code
    except Exception:  # noqa: BLE001 - public boundary must sanitize hostile models.
        code = "invalid_trace_event"
    if code is not None:
        raise ProgressClassificationError(code)
    for previous, current in pairwise(parsed):
        if current.sequence != previous.sequence + 1:
            raise ProgressClassificationError("sequence_gap")
        if current.run_id != previous.run_id:
            raise ProgressClassificationError("run_id_mismatch")
    return tuple(parsed)


def _payload_object(event: TraceEvent) -> dict[str, Any] | None:
    """Return a payload only when its raw public representation is an object."""
    code: str | None = None
    payload: Any = None
    try:
        payload = event.to_dict().get("payload")
    except ModelValidationError as error:
        code = error.code
    except Exception:  # noqa: BLE001 - public boundary must sanitize hostile models.
        code = "invalid_trace_event"
    if code is not None:
        raise ProgressClassificationError(code)
    return payload if type(payload) is dict else None


def _scalar_key(value: Any) -> tuple[type[Any], Any] | None:
    """Give an exact JSON scalar a type-sensitive comparable key."""
    if type(value) in (type(None), bool, int, float, str):
        return (type(value), value)
    return None


def _hypothesis_fingerprint(value: Any) -> str | None:
    """Return a redacted canonical input fingerprint, or ignore a bad boundary."""
    try:
        return action_fingerprint(value).digest
    except Exception:  # noqa: BLE001 - malformed opaque payloads are no-progress.
        return None


def _poll_number(value: Any) -> Decimal | None:
    """Return an exact Decimal for an exact finite documented poll number."""
    if type(value) is int:
        return Decimal(value)
    if type(value) is float and math.isfinite(value):
        return Decimal.from_float(value)
    return None


def _poll_distance(value: Any, target: Any) -> Decimal | None:
    """Return an exact finite numeric distance for documented poll values."""
    numeric_value = _poll_number(value)
    numeric_target = _poll_number(target)
    if numeric_value is None or numeric_target is None:
        return None
    return abs(numeric_value - numeric_target)


def _assessment(reasons: list[ProgressReason], event_ids: list[str]) -> ProgressAssessment:
    return ProgressAssessment(bool(reasons), tuple(reasons), tuple(dict.fromkeys(event_ids)))


def classify_progress(trace: Any) -> ProgressAssessment:
    """Classify progress from documented factual fields in one contiguous window.

    Pagination and batches inspect only ``tool_result`` or ``state_observation``
    object payloads. ``pagination_cursor`` must be an exact JSON scalar;
    ``batch_item_id`` must be an exact string. The initial observation is only a
    baseline, while a later value not observed earlier establishes progress.
    """
    events = _validated_window(trace)
    seen_cursors: set[tuple[type[Any], Any]] = set()
    seen_batch_items: set[str] = set()
    seen_hypothesis_inputs: dict[str, set[str]] = {}
    seen_error_fingerprints: set[str] = set()
    seen_stack_frames: set[str] = set()
    has_error_baseline = False
    seen_evidence_refs: set[str] = set()
    seen_eliminated_hypotheses: set[str] = set()
    has_prior_event = False
    last_state_fingerprint: str | None = None
    pending_state_expectations: list[tuple[str, int]] = []
    poll_baselines: dict[str, tuple[Decimal, Decimal]] = {}
    reasons: list[ProgressReason] = []
    event_ids: list[str] = []
    for event in events:
        payload = _payload_object(event)
        fingerprint = event.state_fingerprint
        baseline = fingerprint if type(fingerprint) is str and fingerprint else last_state_fingerprint
        if event.expected_state_change is True and baseline is not None:
            pending_state_expectations.append((baseline, event.sequence))
        if event.kind is TraceKind.STATE_OBSERVATION and type(fingerprint) is str and fingerprint:
            if (
                any(sequence < event.sequence and expected != fingerprint for expected, sequence in pending_state_expectations)
                and ProgressReason.EXPECTED_STATE_CHANGE_OBSERVED not in reasons
            ):
                reasons.append(ProgressReason.EXPECTED_STATE_CHANGE_OBSERVED)
                event_ids.append(event.event_id)
            last_state_fingerprint = fingerprint
        elif type(fingerprint) is str and fingerprint:
            last_state_fingerprint = fingerprint
        if (
            event.kind is TraceKind.ACCEPTANCE_CHECK
            and type(payload) is dict
            and payload.get("required_verification") is True
            and type(event.acceptance_delta) is str
            and event.acceptance_delta
            and ProgressReason.REQUIRED_ACCEPTANCE_VERIFIED not in reasons
        ):
            reasons.append(ProgressReason.REQUIRED_ACCEPTANCE_VERIFIED)
            event_ids.append(event.event_id)
        if event.kind is TraceKind.HANDOFF and type(payload) is dict:
            plan_id = payload.get("new_plan_id")
            capabilities = payload.get("new_capabilities")
            is_productive = (type(plan_id) is str and bool(plan_id)) or (
                type(capabilities) is list
                and bool(capabilities)
                and all(type(capability) is str and capability for capability in capabilities)
            )
            if is_productive and ProgressReason.PRODUCTIVE_HANDOFF not in reasons:
                reasons.append(ProgressReason.PRODUCTIVE_HANDOFF)
                event_ids.append(event.event_id)
        evidence_refs = event.evidence_refs or ()
        unseen_evidence = [reference for reference in evidence_refs if reference not in seen_evidence_refs]
        if has_prior_event and unseen_evidence and ProgressReason.EVIDENCE_ADDED not in reasons:
            reasons.append(ProgressReason.EVIDENCE_ADDED)
            event_ids.append(event.event_id)
        seen_evidence_refs.update(evidence_refs)
        eliminated = payload.get("eliminated_hypotheses") if payload is not None else None
        if type(eliminated) is list and eliminated and all(type(item) is str and item for item in eliminated):
            unseen_hypotheses = [item for item in eliminated if item not in seen_eliminated_hypotheses]
            if has_prior_event and unseen_hypotheses and ProgressReason.HYPOTHESIS_ELIMINATED not in reasons:
                reasons.append(ProgressReason.HYPOTHESIS_ELIMINATED)
                event_ids.append(event.event_id)
            seen_eliminated_hypotheses.update(eliminated)
        if event.kind is TraceKind.ERROR:
            fingerprint = event.error_fingerprint
            if type(fingerprint) is str and fingerprint:
                if (
                    seen_error_fingerprints
                    and fingerprint not in seen_error_fingerprints
                    and ProgressReason.ERROR_CHANGED not in reasons
                ):
                    reasons.append(ProgressReason.ERROR_CHANGED)
                    event_ids.append(event.event_id)
                seen_error_fingerprints.add(fingerprint)
            frames = payload.get("stack_frames") if payload is not None else None
            if type(frames) is list and all(type(frame) is str for frame in frames):
                unseen_frames = [frame for frame in frames if frame not in seen_stack_frames]
                if has_error_baseline and unseen_frames and ProgressReason.STACK_FRAME_ADDED not in reasons:
                    reasons.append(ProgressReason.STACK_FRAME_ADDED)
                    event_ids.append(event.event_id)
                seen_stack_frames.update(frames)
            has_error_baseline = True
        if payload is None:
            has_prior_event = True
            continue
        if event.kind in (TraceKind.TOOL_RESULT, TraceKind.STATE_OBSERVATION):
            cursor = _scalar_key(payload.get("pagination_cursor")) if "pagination_cursor" in payload else None
            if cursor is not None:
                if seen_cursors and cursor not in seen_cursors and ProgressReason.PAGINATION_ADVANCED not in reasons:
                    reasons.append(ProgressReason.PAGINATION_ADVANCED)
                    event_ids.append(event.event_id)
                seen_cursors.add(cursor)
            batch_item = payload.get("batch_item_id")
            if type(batch_item) is str:
                if seen_batch_items and batch_item not in seen_batch_items and ProgressReason.BATCH_ITEM_CHANGED not in reasons:
                    reasons.append(ProgressReason.BATCH_ITEM_CHANGED)
                    event_ids.append(event.event_id)
                seen_batch_items.add(batch_item)
        if event.kind is TraceKind.STATE_OBSERVATION:
            poll_id = payload.get("poll_id")
            target = payload.get("poll_target")
            distance = _poll_distance(payload.get("poll_value"), target)
            target_number = _poll_number(target)
            if type(poll_id) is str and poll_id and distance is not None and target_number is not None:
                poll_baseline = poll_baselines.get(poll_id)
                if (
                    poll_baseline is not None
                    and poll_baseline[0] == target_number
                    and distance < poll_baseline[1]
                    and ProgressReason.POLL_CONVERGING not in reasons
                ):
                    reasons.append(ProgressReason.POLL_CONVERGING)
                    event_ids.append(event.event_id)
                poll_baselines[poll_id] = (target_number, distance)
        if event.kind is TraceKind.TOOL_CALL:
            hypothesis_id = payload.get("hypothesis_id")
            if type(hypothesis_id) is str and hypothesis_id and "hypothesis_test_input" in payload:
                fingerprint = _hypothesis_fingerprint(payload["hypothesis_test_input"])
                if fingerprint is not None:
                    prior_inputs = seen_hypothesis_inputs.setdefault(hypothesis_id, set())
                    if (
                        prior_inputs
                        and fingerprint not in prior_inputs
                        and ProgressReason.HYPOTHESIS_INPUT_CHANGED not in reasons
                    ):
                        reasons.append(ProgressReason.HYPOTHESIS_INPUT_CHANGED)
                        event_ids.append(event.event_id)
                    prior_inputs.add(fingerprint)
        has_prior_event = True
    return _assessment(reasons, event_ids)
