"""Deterministic mapping from validated recovery evidence to uncertainty."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
from itertools import pairwise
from json import dumps
from re import compile as re_compile
from typing import Any, NoReturn

from .canonical import canonicalize_json
from .models import TraceEvent, TraceKind
from .validation import validate_public_artifact


class CauseClass(str, Enum):
    """Closed recovery causes.

    ``MISSING_KNOWLEDGE`` means required knowledge is missing;
    ``INCORRECT_PLAN`` means the declared plan is incorrect;
    ``UNSUITABLE_TOOL`` means the selected tool is unsuitable;
    ``RUNTIME_DEFECT`` means a runtime defect occurred;
    ``MISSING_AUTHORITY`` means required authority or credentials are missing;
    ``UNSAFE_SIDE_EFFECT`` means a side effect is unsafe to perform;
    ``AMBIGUOUS_COMPLETION`` means completion status is ambiguous; and
    ``EXHAUSTED_BUDGET`` means the permitted recovery budget is exhausted.
    """

    MISSING_KNOWLEDGE = "missing_knowledge"
    INCORRECT_PLAN = "incorrect_plan"
    UNSUITABLE_TOOL = "unsuitable_tool"
    RUNTIME_DEFECT = "runtime_defect"
    MISSING_AUTHORITY = "missing_authority"
    UNSAFE_SIDE_EFFECT = "unsafe_side_effect"
    AMBIGUOUS_COMPLETION = "ambiguous_completion"
    EXHAUSTED_BUDGET = "exhausted_budget"


class UncertaintyDisposition(str, Enum):
    """The closed actions available for a diagnosed uncertainty."""

    VERIFY = "verify"
    ASSUME = "assume"
    ESCALATE = "escalate"
    BLOCK = "block"


class DiagnosisError(ValueError):
    """Sanitized failure from the diagnosis input boundary."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__("Invalid diagnosis input")


_CAUSE_ORDER = tuple(CauseClass)
_HIGH_RISK_CAUSES = frozenset(
    {
        CauseClass.MISSING_AUTHORITY,
        CauseClass.UNSAFE_SIDE_EFFECT,
        CauseClass.EXHAUSTED_BUDGET,
    }
)
_DISPOSITIONS = {
    CauseClass.MISSING_AUTHORITY: UncertaintyDisposition.ESCALATE,
    CauseClass.UNSAFE_SIDE_EFFECT: UncertaintyDisposition.BLOCK,
    CauseClass.EXHAUSTED_BUDGET: UncertaintyDisposition.BLOCK,
}
_SAFE_ID = re_compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_REJECTED_HYPOTHESIS_REF = re_compile(r"^rejected-hypothesis-[0-9a-f]{24}$")
_SHA256_HEXDIGEST = re_compile(r"^[0-9a-f]{64}$")
_MAX_ITEMS = 10_000
_MAX_EVIDENCE = 1_000
_MAX_REJECTED_HYPOTHESES = 100


def _fail(code: str) -> NoReturn:
    raise DiagnosisError(code) from None


def _safe_identifier(value: Any) -> bool:
    return (
        type(value) is str
        and 0 < len(value) <= 128
        and _SAFE_ID.fullmatch(value) is not None
    )


@dataclass(frozen=True, slots=True)
class UncertaintyItem:
    """One closed disposition for a single diagnosed cause."""

    uncertainty_id: str
    cause_class: CauseClass
    disposition: UncertaintyDisposition
    high_risk: bool

    def __post_init__(self) -> None:
        if (
            type(self.cause_class) is not CauseClass
            or type(self.disposition) is not UncertaintyDisposition
            or type(self.high_risk) is not bool
            or self.uncertainty_id != f"uncertainty-{self.cause_class.value}"
            or not _safe_identifier(self.uncertainty_id)
            or self.high_risk is not (self.cause_class in _HIGH_RISK_CAUSES)
            or (self.disposition is UncertaintyDisposition.ASSUME and self.high_risk)
        ):
            _fail("invalid_uncertainty_item")

    def to_dict(self) -> dict[str, str | bool]:
        self._validate_state()
        return {
            "uncertainty_id": self.uncertainty_id,
            "cause_class": self.cause_class.value,
            "disposition": self.disposition.value,
            "high_risk": self.high_risk,
        }

    def _validate_state(self) -> None:
        invalid_state = False
        try:
            self.__post_init__()
        except (MemoryError, SystemExit):
            raise
        except DiagnosisError:
            raise
        except Exception:  # noqa: BLE001 - typed object integrity boundary.
            invalid_state = True
        if invalid_state:
            _fail("invalid_uncertainty_item")

    def __reduce__(
        self,
    ) -> tuple[Any, tuple[str, CauseClass, UncertaintyDisposition, bool]]:
        self._validate_state()
        return (
            UncertaintyItem,
            (self.uncertainty_id, self.cause_class, self.disposition, self.high_risk),
        )


@dataclass(frozen=True, slots=True)
class Diagnosis:
    """Immutable deterministic diagnosis with a one-to-one uncertainty map."""

    run_id: str
    cause_classes: tuple[CauseClass, ...]
    uncertainty_map: tuple[UncertaintyItem, ...]
    evidence_event_ids: tuple[str, ...]
    rejected_hypothesis_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            type(self.run_id) is not str
            or not _safe_identifier(self.run_id)
            or type(self.cause_classes) is not tuple
            or type(self.uncertainty_map) is not tuple
            or type(self.evidence_event_ids) is not tuple
            or type(self.rejected_hypothesis_refs) is not tuple
            or any(type(cause) is not CauseClass for cause in self.cause_classes)
            or tuple(sorted(self.cause_classes, key=_CAUSE_ORDER.index))
            != self.cause_classes
            or len(set(self.cause_classes)) != len(self.cause_classes)
            or len(self.uncertainty_map) != len(self.cause_classes)
            or any(type(item) is not UncertaintyItem for item in self.uncertainty_map)
            or any(
                item.cause_class is not cause
                for cause, item in zip(
                    self.cause_classes, self.uncertainty_map, strict=True
                )
            )
            or any(
                type(event_id) is not str or not _safe_identifier(event_id)
                for event_id in self.evidence_event_ids
            )
            or len(set(self.evidence_event_ids)) != len(self.evidence_event_ids)
            or len(self.evidence_event_ids) > _MAX_EVIDENCE
            or any(
                type(ref) is not str or _REJECTED_HYPOTHESIS_REF.fullmatch(ref) is None
                for ref in self.rejected_hypothesis_refs
            )
            or len(set(self.rejected_hypothesis_refs))
            != len(self.rejected_hypothesis_refs)
            or len(self.rejected_hypothesis_refs) > _MAX_REJECTED_HYPOTHESES
        ):
            _fail("invalid_diagnosis")
        for item in self.uncertainty_map:
            item._validate_state()

    def to_dict(self) -> dict[str, Any]:
        self._validate_state()
        return {
            "run_id": self.run_id,
            "cause_classes": [cause.value for cause in self.cause_classes],
            "uncertainty_map": [item.to_dict() for item in self.uncertainty_map],
            "evidence_event_ids": list(self.evidence_event_ids),
            "rejected_hypothesis_refs": list(self.rejected_hypothesis_refs),
        }

    def _validate_state(self) -> None:
        invalid_state = False
        try:
            self.__post_init__()
        except (MemoryError, SystemExit):
            raise
        except DiagnosisError:
            raise
        except Exception:  # noqa: BLE001 - typed object integrity boundary.
            invalid_state = True
        if invalid_state:
            _fail("invalid_diagnosis")

    def __reduce__(
        self,
    ) -> tuple[
        Any,
        tuple[
            str,
            tuple[CauseClass, ...],
            tuple[UncertaintyItem, ...],
            tuple[str, ...],
            tuple[str, ...],
        ],
    ]:
        self._validate_state()
        return (
            Diagnosis,
            (
                self.run_id,
                self.cause_classes,
                self.uncertainty_map,
                self.evidence_event_ids,
                self.rejected_hypothesis_refs,
            ),
        )


def _validated_trace(trace: Any) -> tuple[TraceEvent, ...]:
    if type(trace) not in (list, tuple):
        _fail("invalid_trace_event")
    if len(trace) > _MAX_ITEMS:
        _fail("diagnosis_item_limit")
    parsed: list[TraceEvent] = []
    trace_invalid = False
    try:
        for event in trace:
            if type(event) is not TraceEvent:
                _fail("invalid_trace_event")
            fresh_event = TraceEvent.from_dict(event.to_dict())
            if type(fresh_event) is not TraceEvent:
                _fail("invalid_trace_event")
            parsed.append(fresh_event)
    except (MemoryError, SystemExit, DiagnosisError):
        raise
    except Exception:  # noqa: BLE001 - public trace boundary is sanitized.
        trace_invalid = True
    if trace_invalid:
        _fail("invalid_trace_event")
    if len({event.event_id for event in parsed}) != len(parsed):
        _fail("duplicate_event_id")
    if any(
        current.sequence != previous.sequence + 1
        for previous, current in pairwise(parsed)
    ):
        _fail("sequence_gap")
    if parsed and any(event.run_id != parsed[0].run_id for event in parsed[1:]):
        _fail("run_id_mismatch")
    return tuple(parsed)


def _validated_findings(findings: Any) -> tuple[dict[str, Any], ...]:
    if type(findings) not in (list, tuple):
        _fail("invalid_detector_finding")
    if len(findings) > _MAX_ITEMS:
        _fail("diagnosis_item_limit")
    parsed: list[dict[str, Any]] = []
    finding_invalid = False
    try:
        for finding in findings:
            if type(finding) is not dict:
                _fail("invalid_detector_finding")
            fresh_finding = validate_public_artifact("detector_finding", finding)
            if type(fresh_finding) is not dict:
                _fail("invalid_detector_finding")
            parsed.append(fresh_finding)
    except (MemoryError, SystemExit, DiagnosisError):
        raise
    except Exception:  # noqa: BLE001 - public finding boundary is sanitized.
        finding_invalid = True
    if finding_invalid:
        _fail("invalid_detector_finding")
    if len({finding["finding_id"] for finding in parsed}) != len(parsed):
        _fail("duplicate_finding_id")
    return tuple(parsed)


def _event_marker(event: TraceEvent) -> CauseClass | None:
    if event.kind not in (TraceKind.PLAN, TraceKind.ERROR):
        return None
    source = event.to_dict()
    payload = source.get("payload")
    if type(payload) is not dict:
        return None
    marker = payload.get("diagnostic_cause")
    if type(marker) is not str:
        return None
    try:
        return CauseClass(marker)
    except ValueError:
        return None


def _finding_cause(finding: Mapping[str, Any]) -> CauseClass | None:
    if finding["matched"] is not True:
        return None
    if finding["finding_class"] == "risk" and finding["reason_code"] == "risk_detected":
        return CauseClass.UNSAFE_SIDE_EFFECT
    if (
        finding["finding_class"] == "budget"
        and finding["reason_code"] == "budget_limit_reached"
    ):
        return CauseClass.EXHAUSTED_BUDGET
    return None


def _rejected_hypothesis_sources(event: TraceEvent) -> Iterator[str]:
    if event.kind is not TraceKind.PLAN:
        return
    payload = event.to_dict().get("payload")
    if type(payload) is not dict:
        return
    eliminated = payload.get("eliminated_hypotheses")
    if type(eliminated) is not list or not eliminated:
        return
    for source in eliminated:
        if type(source) is str and source:
            yield source


def diagnose(trace: Any, findings: Any) -> Diagnosis:
    """Map exact validated marker and finding facts to closed uncertainty items."""
    parsed_trace = _validated_trace(trace)
    parsed_findings = _validated_findings(findings)
    if not parsed_trace and not parsed_findings:
        _fail("empty_diagnosis_input")

    run_id = parsed_trace[0].run_id if parsed_trace else parsed_findings[0]["run_id"]
    if any(event.run_id != run_id for event in parsed_trace) or any(
        finding["run_id"] != run_id for finding in parsed_findings
    ):
        _fail("run_id_mismatch")

    observed: set[CauseClass] = set()
    evidence: list[str] = []
    seen_evidence: set[str] = set()
    rejected_hypotheses: list[str] = []
    seen_rejected_hypotheses: set[str] = set()
    rejected_hypothesis_sources: dict[str, bytes] = {}
    seen_canonical_sources: set[bytes] = set()
    rejected_hypothesis_collision = False

    def support_evidence(event_ids: tuple[str, ...]) -> None:
        for event_id in event_ids:
            if event_id not in seen_evidence:
                seen_evidence.add(event_id)
                evidence.append(event_id)
                if len(evidence) > _MAX_EVIDENCE:
                    _fail("diagnosis_evidence_limit")

    def support(cause: CauseClass, event_ids: tuple[str, ...]) -> None:
        observed.add(cause)
        support_evidence(event_ids)

    for event in parsed_trace:
        marker = _event_marker(event)
        if marker is not None:
            support(marker, (event.event_id,))
        for source in _rejected_hypothesis_sources(event):
            canonical_source_bytes = b""
            canonical_invalid = False
            try:
                canonical_source = dumps(source, ensure_ascii=False)
                canonical_source_bytes = canonicalize_json(canonical_source)
            except (MemoryError, SystemExit):
                raise
            except Exception:  # noqa: BLE001 - source hypotheses are sanitized.
                canonical_invalid = True
            if canonical_invalid:
                _fail("invalid_trace_event")
            if canonical_source_bytes in seen_canonical_sources:
                support_evidence((event.event_id,))
                continue
            if len(seen_canonical_sources) >= _MAX_REJECTED_HYPOTHESES:
                _fail("diagnosis_rejected_hypothesis_limit")
            digest: object = ""
            digest_invalid = False
            try:
                digest = sha256(canonical_source_bytes).hexdigest()
            except (MemoryError, SystemExit):
                raise
            except Exception:  # noqa: BLE001 - digest is a sanitized boundary.
                digest_invalid = True
            if (
                digest_invalid
                or type(digest) is not str
                or _SHA256_HEXDIGEST.fullmatch(digest) is None
            ):
                _fail("invalid_rejected_hypothesis_digest")
            reference = f"rejected-hypothesis-{digest[:24]}"
            seen_canonical_sources.add(canonical_source_bytes)
            support_evidence((event.event_id,))
            if reference not in seen_rejected_hypotheses:
                seen_rejected_hypotheses.add(reference)
                rejected_hypothesis_sources[reference] = canonical_source_bytes
                rejected_hypotheses.append(reference)
            elif rejected_hypothesis_sources[reference] != canonical_source_bytes:
                rejected_hypothesis_collision = True
                break
        if rejected_hypothesis_collision:
            break
    if rejected_hypothesis_collision:
        _fail("rejected_hypothesis_collision")
    for finding in parsed_findings:
        cause = _finding_cause(finding)
        if cause is not None:
            support(cause, tuple(finding["event_ids"]))

    causes = tuple(cause for cause in _CAUSE_ORDER if cause in observed)
    uncertainty_map = tuple(
        UncertaintyItem(
            uncertainty_id=f"uncertainty-{cause.value}",
            cause_class=cause,
            disposition=_DISPOSITIONS.get(cause, UncertaintyDisposition.VERIFY),
            high_risk=cause in _HIGH_RISK_CAUSES,
        )
        for cause in causes
    )
    return Diagnosis(
        run_id,
        causes,
        uncertainty_map,
        tuple(evidence),
        tuple(rejected_hypotheses),
    )
