"""Generated contract and canonicalization boundary proofs."""

from __future__ import annotations

import json
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "contracts"
SCHEMAS = ROOT / "contracts" / "v1"

_ARTIFACTS = {
    "trace_event": ("minimal-trace-event.json", "trace-event.schema.json"),
    "run_policy": ("minimal-run-policy.json", "run-policy.schema.json"),
    "detector_finding": (
        "minimal-detector-finding.json",
        "detector-finding.schema.json",
    ),
    "decision_envelope": (
        "minimal-decision-envelope.json",
        "decision-envelope.schema.json",
    ),
    "recovery_instruction": (
        "minimal-recovery-instruction.json",
        "recovery-instruction.schema.json",
    ),
    "recovery_outcome": (
        "minimal-recovery-outcome.json",
        "recovery-outcome.schema.json",
    ),
    "evidence_record": ("minimal-evidence-record.json", "evidence-record.schema.json"),
}


def _fixture(kind: str) -> dict[str, Any]:
    fixture_name, _ = _ARTIFACTS[kind]
    return json.loads((FIXTURES / fixture_name).read_text(encoding="utf-8"))


def _schema(kind: str) -> Draft202012Validator:
    _, schema_name = _ARTIFACTS[kind]
    schema = json.loads((SCHEMAS / schema_name).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _runtime_accepts(kind: str, value: dict[str, Any]) -> bool:
    """Exercise the public runtime parser rather than a test-local mirror."""
    from semantic_reheating.controller import RecoveryInstruction
    from semantic_reheating.evidence import EvidenceRecord, RecoveryOutcome
    from semantic_reheating.models import DecisionEnvelope, RunPolicy, TraceEvent
    from semantic_reheating.validation import validate_public_artifact

    parsers: dict[str, Callable[[Any], Any]] = {
        "trace_event": TraceEvent.from_dict,
        "run_policy": RunPolicy.from_dict,
        "detector_finding": lambda data: validate_public_artifact(
            "detector_finding", data
        ),
        "decision_envelope": DecisionEnvelope.from_dict,
        "recovery_instruction": RecoveryInstruction.from_dict,
        "recovery_outcome": RecoveryOutcome.from_dict,
        "evidence_record": EvidenceRecord.from_dict,
    }
    try:
        parsers[kind](value)
    except (
        ValueError
    ):  # Public parsers expose rejected artifacts as ValueError subclasses.
        return False
    return True


def _assert_schema_runtime_reject_together(kind: str, invalid: dict[str, Any]) -> None:
    """Both layers must reject every locally structural one-invariant mutation."""
    schema_accepts = _schema(kind).is_valid(invalid)
    runtime_accepts = _runtime_accepts(kind, invalid)
    assert schema_accepts == runtime_accepts, kind
    assert not schema_accepts, kind


@settings(derandomize=True, database=None, deadline=500, max_examples=24)
@given(
    scalar=st.one_of(
        st.none(),
        st.booleans(),
        st.integers(min_value=-(2**32), max_value=2**32),
        st.text(max_size=24),
    )
)
def test_canonical_bytes_and_fingerprint_ignore_nested_map_key_order(
    scalar: object,
) -> None:
    """RFC 8785 bytes and action digests bind values, not insertion order."""
    from semantic_reheating.canonical import action_fingerprint, canonicalize_json

    source = {
        "zeta": {"third": scalar, "first": [scalar, {"z": 1, "a": scalar}]},
        "alpha": [{"omega": scalar, "beta": {"right": scalar, "left": 0}}],
        "middle": {"two": scalar, "one": {"b": scalar, "a": True}},
    }

    def reverse_maps(value: object) -> object:
        if type(value) is dict:
            return {
                key: reverse_maps(nested)
                for key, nested in reversed(tuple(value.items()))
            }
        if type(value) is list:
            return [reverse_maps(nested) for nested in value]
        return value

    reordered = reverse_maps(source)
    assert type(reordered) is dict
    assert tuple(source) != tuple(reordered)
    assert canonicalize_json(source) == canonicalize_json(reordered)
    assert action_fingerprint(source).digest == action_fingerprint(reordered).digest


@settings(derandomize=True, database=None, deadline=500, max_examples=8)
@pytest.mark.parametrize("kind", tuple(_ARTIFACTS))
@given(suffix=st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=12))
def test_closed_artifacts_reject_generated_unknown_fields_in_schema_and_runtime(
    kind: str, suffix: str
) -> None:
    """Every public artifact is closed at both validation boundaries."""
    invalid = _fixture(kind)
    invalid[f"task15_unknown_{suffix}"] = None
    _assert_schema_runtime_reject_together(kind, invalid)


@pytest.mark.parametrize("kind", tuple(_ARTIFACTS))
def test_closed_contract_major_is_rejected_by_schema_and_runtime(kind: str) -> None:
    invalid = _fixture(kind)
    invalid["contract_version"] = "2.0"
    _assert_schema_runtime_reject_together(kind, invalid)


@pytest.mark.parametrize("kind", tuple(_ARTIFACTS))
@pytest.mark.parametrize("violation", ("missing_required", "wrong_type"))
def test_common_generated_structural_violations_have_no_schema_runtime_disagreement(
    kind: str, violation: str
) -> None:
    invalid = _fixture(kind)
    if violation == "missing_required":
        del invalid["contract_version"]
    else:
        invalid["contract_version"] = 7
    _assert_schema_runtime_reject_together(kind, invalid)


@pytest.mark.parametrize(
    "kind", tuple(kind for kind in _ARTIFACTS if kind != "trace_event")
)
def test_identifier_pattern_violations_are_rejected_by_schema_and_runtime(
    kind: str,
) -> None:
    """TraceEvent has no identifier pattern; all other public schemas do."""
    invalid = _fixture(kind)
    invalid["run_id"] = "invalid identifier"
    _assert_schema_runtime_reject_together(kind, invalid)


# These cases cover local enum, numeric-bound, and conditional constraints.  The
# property intentionally excludes cross-record/controller semantics (sequence
# contiguity, same-run traces, detector agreement, and hard-stop precedence):
# those are not expressible by one standalone Draft 2020-12 artifact schema.
_LOCAL_INVARIANT_MUTATIONS: tuple[
    tuple[str, str, Callable[[dict[str, Any]], None]], ...
] = (
    ("trace_event", "enum", lambda data: data.__setitem__("kind", "not-a-trace-kind")),
    ("trace_event", "minimum", lambda data: data.__setitem__("sequence", 0)),
    (
        "run_policy",
        "maximum",
        lambda data: data.__setitem__("max_recovery_episodes", 101),
    ),
    (
        "detector_finding",
        "enum",
        lambda data: data.__setitem__("finding_class", "unknown"),
    ),
    ("detector_finding", "maximum", lambda data: data.__setitem__("score", 1.01)),
    ("decision_envelope", "enum", lambda data: data.__setitem__("decision", "unknown")),
    (
        "decision_envelope",
        "maximum",
        lambda data: data["confidence"].__setitem__("score", 1.01),
    ),
    (
        "decision_envelope",
        "conditional",
        lambda data: data.__setitem__("requires_host_action", False),
    ),
    (
        "recovery_instruction",
        "const",
        lambda data: data.__setitem__("advisory_only", False),
    ),
    (
        "recovery_instruction",
        "conditional",
        lambda data: data["expected_output"].pop("hypothesis_contract"),
    ),
    (
        "recovery_outcome",
        "enum",
        lambda data: data.__setitem__("error_class", "unknown"),
    ),
    (
        "recovery_outcome",
        "minimum",
        lambda data: data["consumed_counters"].__setitem__("turns", -1),
    ),
    (
        "evidence_record",
        "enum",
        lambda data: data.__setitem__("final_status", "unknown"),
    ),
    (
        "evidence_record",
        "minimum",
        lambda data: data["actual_counters"].__setitem__("turns", -1),
    ),
)


@pytest.mark.parametrize(("kind", "invariant", "mutate"), _LOCAL_INVARIANT_MUTATIONS)
def test_local_schema_invariant_violations_are_rejected_by_both_layers(
    kind: str, invariant: str, mutate: Callable[[dict[str, Any]], None]
) -> None:
    invalid = deepcopy(_fixture(kind))
    mutate(invalid)
    _assert_schema_runtime_reject_together(kind, invalid)
