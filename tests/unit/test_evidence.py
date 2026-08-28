"""Deterministic public recovery evidence records."""

from __future__ import annotations

import json
import pickle
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from typing import Never

import pytest

from semantic_reheating.canonical import canonicalize_json

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "contracts"


def _fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text())


def test_public_outcome_and_evidence_models_roundtrip_minimal_fixtures() -> None:
    from semantic_reheating import EvidenceRecord, RecoveryOutcome

    outcome_source = _fixture("minimal-recovery-outcome.json")
    evidence_source = _fixture("minimal-evidence-record.json")

    assert RecoveryOutcome.from_dict(outcome_source).to_dict() == outcome_source
    assert EvidenceRecord.from_dict(evidence_source).to_dict() == evidence_source


def test_evidence_record_chosen_policy_exactly_matches_decision_policy_vocabulary() -> (
    None
):
    from semantic_reheating.evidence import EvidenceRecord

    source = _fixture("minimal-evidence-record.json")
    for chosen_policy in (
        "policy-standard",
        "policy-conservative",
        "research",
        "branch",
        "model_switch",
        None,
    ):
        source["chosen_policy"] = chosen_policy
        assert EvidenceRecord.from_dict(source).chosen_policy == chosen_policy


def test_record_outcome_marks_host_denial_as_avoiding_repetition_when_constrained() -> (
    None
):
    from semantic_reheating import RecoveryOutcome, record_outcome
    from semantic_reheating.models import DecisionEnvelope

    decision_source = _fixture("minimal-decision-envelope.json")
    decision_source["decision"] = "continue"
    decision_source["requires_host_action"] = False
    outcome_source = _fixture("minimal-recovery-outcome.json")
    outcome_source["host_result"]["status"] = "denied"  # type: ignore[index]
    outcome_source["host_denial"] = {"denied": True, "reason_code": "not_confirmed"}

    record = record_outcome(
        DecisionEnvelope.from_dict(decision_source),
        RecoveryOutcome.from_dict(outcome_source),
    )

    assert record.repeated_side_effects_avoided is True
    assert record.final_status == "blocked"


def test_record_outcome_treats_host_denied_status_as_blocked_without_overclaiming() -> (
    None
):
    from semantic_reheating.evidence import RecoveryOutcome, record_outcome
    from semantic_reheating.models import DecisionEnvelope

    decision_source = _fixture("minimal-decision-envelope.json")
    decision_source["decision"] = "continue"
    decision_source["requires_host_action"] = False
    outcome_source = _fixture("minimal-recovery-outcome.json")
    outcome_source["host_result"]["status"] = "denied"  # type: ignore[index]

    record = record_outcome(
        DecisionEnvelope.from_dict(decision_source),
        RecoveryOutcome.from_dict(outcome_source),
    )

    assert record.final_status == "blocked"
    assert record.repeated_side_effects_avoided is False


def test_record_outcome_rejects_tampered_typed_state_without_leaking_values() -> None:
    import pytest

    from semantic_reheating.evidence import (
        EvidenceError,
        RecoveryOutcome,
        record_outcome,
    )
    from semantic_reheating.models import DecisionEnvelope

    decision = DecisionEnvelope.from_dict(_fixture("minimal-decision-envelope.json"))
    object.__setattr__(decision, "run_id", "__tampered-decision-run-id__")

    with pytest.raises(EvidenceError) as caught:
        record_outcome(
            decision,
            RecoveryOutcome.from_dict(_fixture("minimal-recovery-outcome.json")),
        )

    assert caught.value.code == "invalid_decision_envelope"
    assert "__tampered-decision-run-id__" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_evidence_models_reject_forged_missing_source_with_sanitized_error() -> None:
    import pytest

    from semantic_reheating.evidence import (
        EvidenceError,
        EvidenceRecord,
        RecoveryOutcome,
    )

    for model_class in (RecoveryOutcome, EvidenceRecord):
        forged = object.__new__(model_class)
        with pytest.raises(EvidenceError) as caught:
            forged.to_dict()
        assert caught.value.code == "invalid_evidence_state"
        assert caught.value.__cause__ is None
        assert caught.value.__context__ is None


def test_evidence_model_schema_and_hostile_input_failures_are_sanitized() -> None:
    import pytest

    from semantic_reheating.evidence import (
        EvidenceError,
        EvidenceRecord,
        RecoveryOutcome,
    )

    sentinel = "__hostile-evidence-input__"

    class HostileDict(dict[str, object]):
        def items(self) -> object:
            raise RuntimeError(sentinel)

    invalid_outcome = _fixture("minimal-recovery-outcome.json")
    invalid_outcome["private"] = sentinel
    invalid_evidence = _fixture("minimal-evidence-record.json")
    invalid_evidence["private"] = sentinel
    for parser, invalid in (
        (RecoveryOutcome.from_dict, invalid_outcome),
        (EvidenceRecord.from_dict, invalid_evidence),
        (RecoveryOutcome.from_dict, HostileDict()),
        (EvidenceRecord.from_dict, HostileDict()),
    ):
        with pytest.raises(EvidenceError) as caught:
            parser(invalid)
        assert caught.value.code in {
            "invalid_recovery_outcome",
            "invalid_evidence_record",
        }
        assert sentinel not in str(caught.value)
        assert caught.value.__cause__ is None
        assert caught.value.__context__ is None


@pytest.mark.parametrize(
    "model_class,fixture_name",
    [
        ("RecoveryOutcome", "minimal-recovery-outcome.json"),
        ("EvidenceRecord", "minimal-evidence-record.json"),
    ],
)
def test_evidence_models_are_immutable_and_copy_via_validated_reconstruction(
    model_class: str, fixture_name: str
) -> None:
    from semantic_reheating import evidence

    source = _fixture(fixture_name)
    model_type = getattr(evidence, model_class)
    model = model_type.from_dict(source)
    source["run_id"] = "mutated-source"
    first = model.to_dict()
    first["run_id"] = "mutated-copy"

    assert not hasattr(model, "__dict__")
    assert model.to_dict()["run_id"] == "run-example"
    with pytest.raises(AttributeError):
        model.run_id = "mutated-model"
    for copied in (deepcopy(model), pickle.loads(pickle.dumps(model))):
        assert type(copied) is model_type
        assert copied is not model
        assert copied.to_dict() == model.to_dict()


def test_record_outcome_uses_only_public_decision_and_outcome_evidence() -> None:
    from semantic_reheating.evidence import RecoveryOutcome, record_outcome
    from semantic_reheating.models import DecisionEnvelope

    decision_source = _fixture("minimal-decision-envelope.json")
    decision_source.update(
        {
            "decision": "reheat",
            "recovery_policy": "research",
            "requires_host_action": False,
        }
    )
    decision_source["confidence"]["contributing_findings"] = [  # type: ignore[index]
        {
            "finding_id": "rep-1",
            "finding_class": "repetition",
            "matched": True,
            "score": 1,
            "weight": 0.4,
            "weighted_score": 0.4,
        },
        {
            "finding_id": "prog-1",
            "finding_class": "no_progress",
            "matched": True,
            "score": 1,
            "weight": 0.4,
            "weighted_score": 0.4,
        },
        {
            "finding_id": "risk-1",
            "finding_class": "risk",
            "matched": True,
            "score": 1,
            "weight": 0.1,
            "weighted_score": 0.1,
        },
        {
            "finding_id": "budget-1",
            "finding_class": "budget",
            "matched": True,
            "score": 1,
            "weight": 0.1,
            "weighted_score": 0.1,
        },
        {
            "finding_id": "prog-1",
            "finding_class": "no_progress",
            "matched": True,
            "score": 0.5,
            "weight": 0.4,
            "weighted_score": 0.2,
        },
        {
            "finding_id": "ignored",
            "finding_class": "risk",
            "matched": False,
            "score": 1,
            "weight": 0.1,
            "weighted_score": 0.1,
        },
    ]
    outcome_source = _fixture("minimal-recovery-outcome.json")
    outcome_source["consumed_counters"] = {
        "turns": 2,
        "tool_calls": 3,
        "tokens": 400,
        "elapsed_seconds": 2.5,
        "cost": 7,
    }
    outcome_source["evidence_gained"] = ["evidence-new-1", "evidence-new-2"]
    outcome_source["host_result"]["summary"] = "must not appear"  # type: ignore[index]
    outcome_source["state_delta"]["summary"] = "must not appear"  # type: ignore[index]

    record = record_outcome(
        DecisionEnvelope.from_dict(decision_source),
        RecoveryOutcome.from_dict(outcome_source),
    )
    serialized = record.to_dict()
    basis = {key: value for key, value in serialized.items() if key != "evidence_id"}

    assert (
        serialized
        == record_outcome(
            DecisionEnvelope.from_dict(decision_source),
            RecoveryOutcome.from_dict(outcome_source),
        ).to_dict()
    )
    assert (
        serialized["evidence_id"]
        == "evidence-" + sha256(canonicalize_json(basis)).hexdigest()[:24]
    )
    assert serialized["trigger"] == {
        "finding_ids": ["rep-1", "prog-1", "risk-1", "budget-1"],
        "reason_code": "budget_limit_reached",
    }
    assert serialized["chosen_policy"] == "research"
    assert serialized["actual_counters"] == outcome_source["consumed_counters"]
    assert serialized["new_evidence_refs"] == outcome_source["evidence_gained"]
    assert serialized["acceptance_delta"] == outcome_source["acceptance_delta"]
    assert serialized["repeated_side_effects_avoided"] is False
    assert serialized["final_status"] == "recovered"
    assert set(serialized) == {
        "contract_version",
        "run_id",
        "evidence_id",
        "trigger",
        "chosen_policy",
        "actual_counters",
        "new_evidence_refs",
        "acceptance_delta",
        "repeated_side_effects_avoided",
        "final_status",
    }
    assert "must not appear" not in canonicalize_json(serialized).decode()


@pytest.mark.parametrize(
    (
        "decision_name",
        "host_status",
        "host_denied",
        "human_escalation",
        "acceptance",
        "expected",
    ),
    [
        ("continue", "completed", False, True, "improved", "escalated"),
        ("continue", "escalated", False, False, "improved", "escalated"),
        ("stop", "failed", True, False, "improved", "blocked"),
        ("stop", "partial", False, False, "improved", "stopped"),
        ("continue", "failed", False, False, "improved", "stopped"),
        ("continue", "completed", False, False, "improved", "recovered"),
        ("continue", "partial", False, False, "improved", "continued"),
    ],
)
def test_record_outcome_final_status_precedence(
    decision_name: str,
    host_status: str,
    host_denied: bool,
    human_escalation: bool,
    acceptance: str,
    expected: str,
) -> None:
    from semantic_reheating.evidence import RecoveryOutcome, record_outcome
    from semantic_reheating.models import DecisionEnvelope

    decision_source = _fixture("minimal-decision-envelope.json")
    decision_source["decision"] = decision_name
    decision_source["requires_host_action"] = False
    outcome_source = _fixture("minimal-recovery-outcome.json")
    outcome_source["host_result"]["status"] = host_status  # type: ignore[index]
    outcome_source["host_denial"] = {
        "denied": host_denied,
        "reason_code": "not_confirmed" if host_denied else None,
    }
    outcome_source["human_escalation"] = human_escalation
    outcome_source["acceptance_delta"]["status"] = acceptance  # type: ignore[index]

    assert (
        record_outcome(
            DecisionEnvelope.from_dict(decision_source),
            RecoveryOutcome.from_dict(outcome_source),
        ).final_status
        == expected
    )


def test_record_outcome_rejects_empty_trigger_and_run_mismatch() -> None:
    from semantic_reheating.evidence import (
        EvidenceError,
        RecoveryOutcome,
        record_outcome,
    )
    from semantic_reheating.models import DecisionEnvelope

    empty = _fixture("minimal-decision-envelope.json")
    empty["confidence"]["contributing_findings"] = []  # type: ignore[index]
    mismatched = _fixture("minimal-recovery-outcome.json")
    mismatched["run_id"] = "other-run"
    for decision, outcome, code in (
        (
            DecisionEnvelope.from_dict(empty),
            RecoveryOutcome.from_dict(_fixture("minimal-recovery-outcome.json")),
            "missing_trigger_findings",
        ),
        (
            DecisionEnvelope.from_dict(_fixture("minimal-decision-envelope.json")),
            RecoveryOutcome.from_dict(mismatched),
            "run_id_mismatch",
        ),
    ):
        with pytest.raises(EvidenceError) as caught:
            record_outcome(decision, outcome)
        assert caught.value.code == code
        assert caught.value.__cause__ is None
        assert caught.value.__context__ is None


def test_evidence_resource_exceptions_propagate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from semantic_reheating import evidence
    from semantic_reheating.evidence import RecoveryOutcome, record_outcome
    from semantic_reheating.models import DecisionEnvelope

    def memory_error(*args: object, **kwargs: object) -> Never:
        raise MemoryError

    monkeypatch.setattr(evidence, "canonicalize_json", memory_error)
    with pytest.raises(MemoryError):
        record_outcome(
            DecisionEnvelope.from_dict(_fixture("minimal-decision-envelope.json")),
            RecoveryOutcome.from_dict(_fixture("minimal-recovery-outcome.json")),
        )
