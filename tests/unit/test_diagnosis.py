"""Deterministic diagnosis and closed uncertainty-map behavior."""

import pickle
from copy import deepcopy
from typing import Any

import pytest


def _event(
    sequence: int,
    *,
    kind: str = "message",
    payload: object | None = None,
    run_id: str = "run-diagnosis",
    event_id: str | None = None,
) -> Any:
    from semantic_reheating.models import TraceEvent

    return TraceEvent.from_dict(
        {
            "contract_version": "1.0",
            "run_id": run_id,
            "event_id": event_id or f"event-{sequence:03d}",
            "sequence": sequence,
            "kind": kind,
            "actor": "controller",
            "effect_class": "read_only",
            "payload": {} if payload is None else payload,
        }
    )


def _finding(
    finding_id: str,
    *,
    finding_class: str,
    reason_code: str,
    event_ids: list[str],
    matched: bool = True,
    run_id: str = "run-diagnosis",
) -> dict[str, object]:
    return {
        "contract_version": "1.0",
        "run_id": run_id,
        "finding_id": finding_id,
        "detector_name": "detector",
        "detector_version": "1.0",
        "matched": matched,
        "score": 1.0 if matched else 0.0,
        "finding_class": finding_class,
        "event_ids": event_ids,
        "reason_code": reason_code,
        "explanation": "Closed deterministic detector result.",
        "availability": {"status": "available", "notice": "Available."},
    }


def test_unknown_or_prose_event_produces_empty_diagnosis() -> None:
    from semantic_reheating.diagnosis import diagnose

    diagnosis = diagnose(
        [
            _event(
                7, payload={"diagnostic_cause": "not-a-cause", "text": "missing plan"}
            )
        ],
        [],
    )

    assert diagnosis.to_dict() == {
        "run_id": "run-diagnosis",
        "cause_classes": [],
        "uncertainty_map": [],
        "evidence_event_ids": [],
    }


def test_plan_and_error_markers_map_all_causes_in_design_order() -> None:
    from semantic_reheating.diagnosis import (
        CauseClass,
        UncertaintyDisposition,
        diagnose,
    )

    declared = tuple(member.value for member in CauseClass)
    events = [
        _event(
            index + 1,
            kind="plan" if index % 2 == 0 else "error",
            payload={"diagnostic_cause": cause},
        )
        for index, cause in enumerate(reversed(declared))
    ]

    diagnosis = diagnose(events, [])

    assert diagnosis.cause_classes == tuple(CauseClass)
    assert diagnosis.evidence_event_ids == tuple(event.event_id for event in events)
    assert [item.uncertainty_id for item in diagnosis.uncertainty_map] == [
        f"uncertainty-{cause}" for cause in declared
    ]
    assert [item.disposition for item in diagnosis.uncertainty_map] == [
        UncertaintyDisposition.VERIFY,
        UncertaintyDisposition.VERIFY,
        UncertaintyDisposition.VERIFY,
        UncertaintyDisposition.VERIFY,
        UncertaintyDisposition.ESCALATE,
        UncertaintyDisposition.BLOCK,
        UncertaintyDisposition.VERIFY,
        UncertaintyDisposition.BLOCK,
    ]
    assert [item.high_risk for item in diagnosis.uncertainty_map] == [
        False,
        False,
        False,
        False,
        True,
        True,
        False,
        True,
    ]


def test_only_exact_top_level_plan_or_error_marker_is_causal() -> None:
    from semantic_reheating.diagnosis import diagnose

    diagnosis = diagnose(
        [
            _event(1, kind="message", payload={"diagnostic_cause": "runtime_defect"}),
            _event(
                2,
                kind="plan",
                payload={"nested": {"diagnostic_cause": "runtime_defect"}},
            ),
            _event(3, kind="error", payload={"diagnostic_cause": "unknown"}),
        ],
        [],
    )

    assert diagnosis.to_dict()["cause_classes"] == []
    assert diagnosis.to_dict()["evidence_event_ids"] == []


def test_matched_risk_and_budget_findings_map_with_deduplicated_support() -> None:
    from semantic_reheating.diagnosis import CauseClass, diagnose

    diagnosis = diagnose(
        [_event(1, kind="plan", payload={"diagnostic_cause": "runtime_defect"})],
        [
            _finding(
                "finding-risk",
                finding_class="risk",
                reason_code="risk_detected",
                event_ids=["event-001", "risk-002"],
            ),
            _finding(
                "finding-budget",
                finding_class="budget",
                reason_code="budget_limit_reached",
                event_ids=["risk-002", "budget-003"],
            ),
        ],
    )

    assert diagnosis.cause_classes == (
        CauseClass.RUNTIME_DEFECT,
        CauseClass.UNSAFE_SIDE_EFFECT,
        CauseClass.EXHAUSTED_BUDGET,
    )
    assert diagnosis.evidence_event_ids == ("event-001", "risk-002", "budget-003")


def test_unmatched_or_mismatched_findings_do_not_infer_a_cause() -> None:
    from semantic_reheating.diagnosis import diagnose

    diagnosis = diagnose(
        [_event(1)],
        [
            _finding(
                "finding-unmatched-risk",
                finding_class="risk",
                reason_code="risk_detected",
                event_ids=["risk-001"],
                matched=False,
            ),
            _finding(
                "finding-other-risk",
                finding_class="risk",
                reason_code="detector_degraded",
                event_ids=["risk-002"],
            ),
            _finding(
                "finding-other-budget",
                finding_class="budget",
                reason_code="detector_unavailable",
                event_ids=["budget-003"],
            ),
        ],
    )

    assert diagnosis.to_dict()["cause_classes"] == []
    assert diagnosis.to_dict()["evidence_event_ids"] == []


@pytest.mark.parametrize(
    ("cause", "disposition", "high_risk"),
    (
        ("missing_authority", "escalate", True),
        ("unsafe_side_effect", "block", True),
        ("exhausted_budget", "block", True),
    ),
)
def test_high_risk_marker_dispositions_are_closed(
    cause: str, disposition: str, high_risk: bool
) -> None:
    from semantic_reheating.diagnosis import diagnose

    diagnosis = diagnose(
        [_event(1, kind="error", payload={"diagnostic_cause": cause})], []
    )

    item = diagnosis.uncertainty_map[0]
    assert item.disposition.value == disposition
    assert item.high_risk is high_risk


def test_high_risk_or_protected_cause_cannot_assume() -> None:
    from semantic_reheating.diagnosis import (
        CauseClass,
        DiagnosisError,
        UncertaintyDisposition,
        UncertaintyItem,
    )

    with pytest.raises(DiagnosisError) as raised:
        UncertaintyItem(
            "uncertainty-missing_authority",
            CauseClass.MISSING_AUTHORITY,
            UncertaintyDisposition.ASSUME,
            False,
        )

    assert raised.value.code == "invalid_uncertainty_item"
    assert raised.value.args == ("Invalid diagnosis input",)
    assert raised.value.__cause__ is None


def test_diagnosis_objects_are_immutable_fresh_and_roundtrip_without_source_secrets() -> (
    None
):
    from semantic_reheating.diagnosis import CauseClass, diagnose

    diagnosis = diagnose(
        [
            _event(
                1,
                kind="plan",
                payload={"diagnostic_cause": "runtime_defect", "secret": "NO_LEAK"},
            )
        ],
        [],
    )
    first = diagnosis.to_dict()
    first["cause_classes"].append("unsafe_side_effect")
    first["uncertainty_map"][0]["high_risk"] = True
    first["evidence_event_ids"].append("other")

    assert diagnosis.cause_classes == (CauseClass.RUNTIME_DEFECT,)
    assert diagnosis.to_dict() == {
        "run_id": "run-diagnosis",
        "cause_classes": ["runtime_defect"],
        "uncertainty_map": [
            {
                "uncertainty_id": "uncertainty-runtime_defect",
                "cause_class": "runtime_defect",
                "disposition": "verify",
                "high_risk": False,
            }
        ],
        "evidence_event_ids": ["event-001"],
    }
    with pytest.raises(AttributeError):
        diagnosis.run_id = "other"  # type: ignore[misc]
    assert deepcopy(diagnosis).to_dict() == diagnosis.to_dict()
    assert pickle.loads(pickle.dumps(diagnosis)).to_dict() == diagnosis.to_dict()
    assert "NO_LEAK" not in repr(diagnosis)


def test_diagnosis_cause_order_is_fixed_while_evidence_keeps_input_order() -> None:
    from semantic_reheating.diagnosis import diagnose

    forward = diagnose(
        [
            _event(1, kind="error", payload={"diagnostic_cause": "unsafe_side_effect"}),
            _event(2, kind="plan", payload={"diagnostic_cause": "missing_knowledge"}),
        ],
        [],
    )
    reverse = diagnose(
        [
            _event(1, kind="error", payload={"diagnostic_cause": "missing_knowledge"}),
            _event(2, kind="plan", payload={"diagnostic_cause": "unsafe_side_effect"}),
        ],
        [],
    )

    assert (
        forward.to_dict()["cause_classes"]
        == reverse.to_dict()["cause_classes"]
        == [
            "missing_knowledge",
            "unsafe_side_effect",
        ]
    )
    assert forward.evidence_event_ids == ("event-001", "event-002")
    assert reverse.evidence_event_ids == ("event-001", "event-002")


@pytest.mark.parametrize(
    ("trace", "findings", "code"),
    (
        ([], [], "empty_diagnosis_input"),
        ([{}], [], "invalid_trace_event"),
        ([_event(1), _event(3)], [], "sequence_gap"),
        ([_event(1), _event(2, event_id="event-001")], [], "duplicate_event_id"),
        (
            [_event(1)],
            [
                _finding(
                    "one",
                    finding_class="risk",
                    reason_code="risk_detected",
                    event_ids=["r"],
                ),
                _finding(
                    "one",
                    finding_class="budget",
                    reason_code="budget_limit_reached",
                    event_ids=["b"],
                ),
            ],
            "duplicate_finding_id",
        ),
        (
            [_event(1)],
            [
                _finding(
                    "one",
                    finding_class="risk",
                    reason_code="risk_detected",
                    event_ids=["r"],
                    run_id="other",
                )
            ],
            "run_id_mismatch",
        ),
    ),
)
def test_input_failures_are_sanitized_with_stable_codes(
    trace: object, findings: object, code: str
) -> None:
    from semantic_reheating.diagnosis import DiagnosisError, diagnose

    with pytest.raises(DiagnosisError) as raised:
        diagnose(trace, findings)

    assert raised.value.code == code
    assert raised.value.args == ("Invalid diagnosis input",)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_exact_plain_boundary_rejects_raw_trace_dict_finding_subclass_and_forged_model() -> (
    None
):
    from semantic_reheating.diagnosis import DiagnosisError, diagnose
    from semantic_reheating.models import TraceEvent

    class FindingSubclass(dict[str, object]):
        pass

    forged = object.__new__(TraceEvent)
    cases = (
        ([{}], []),
        (
            [_event(1)],
            [
                FindingSubclass(
                    _finding(
                        "subclass",
                        finding_class="risk",
                        reason_code="risk_detected",
                        event_ids=["r"],
                    )
                )
            ],
        ),
        ([forged], []),
    )
    for trace, findings in cases:
        with pytest.raises(DiagnosisError) as raised:
            diagnose(trace, findings)
        assert raised.value.code in {"invalid_trace_event", "invalid_detector_finding"}
        assert raised.value.__cause__ is None
        assert raised.value.__context__ is None


def test_input_and_public_evidence_limits_fail_closed_before_loss() -> None:
    from semantic_reheating.diagnosis import DiagnosisError, diagnose

    with pytest.raises(DiagnosisError, match="Invalid diagnosis input") as item_limit:
        diagnose([_event(1)] * 10_001, [])
    assert item_limit.value.code == "diagnosis_item_limit"

    first_ids = [f"risk-{index}" for index in range(1_000)]
    second_ids = [f"budget-{index}" for index in range(1_000)]
    with pytest.raises(
        DiagnosisError, match="Invalid diagnosis input"
    ) as evidence_limit:
        diagnose(
            [],
            [
                _finding(
                    "risk",
                    finding_class="risk",
                    reason_code="risk_detected",
                    event_ids=first_ids,
                ),
                _finding(
                    "budget",
                    finding_class="budget",
                    reason_code="budget_limit_reached",
                    event_ids=second_ids,
                ),
            ],
        )
    assert evidence_limit.value.code == "diagnosis_evidence_limit"


@pytest.mark.parametrize("resource_exception", (MemoryError, SystemExit))
def test_resource_exceptions_from_finding_validation_propagate(
    monkeypatch: pytest.MonkeyPatch, resource_exception: type[BaseException]
) -> None:
    import semantic_reheating.diagnosis as diagnosis_module

    def raise_resource(*args: object, **kwargs: object) -> object:
        raise resource_exception()

    monkeypatch.setattr(diagnosis_module, "validate_public_artifact", raise_resource)
    with pytest.raises(resource_exception):
        diagnosis_module.diagnose(
            [],
            [
                _finding(
                    "risk",
                    finding_class="risk",
                    reason_code="risk_detected",
                    event_ids=["r"],
                )
            ],
        )


def test_trace_validation_work_is_linear_in_input_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import semantic_reheating.diagnosis as diagnosis_module
    from semantic_reheating.models import TraceEvent

    events = [_event(index + 1) for index in range(128)]
    original = TraceEvent.to_dict
    calls = 0

    def counted(self: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return original(self)

    monkeypatch.setattr(TraceEvent, "to_dict", counted)
    diagnosis_module.diagnose(events, [])

    assert calls <= 2 * len(events)
