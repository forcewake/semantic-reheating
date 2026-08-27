"""RunPolicy semantic safety seam contract tests."""

from __future__ import annotations

import json
import pickle
from copy import deepcopy
from itertools import product
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _policy() -> dict[str, Any]:
    return json.loads(
        (PROJECT_ROOT / "tests" / "fixtures" / "contracts" / "minimal-run-policy.json").read_text()
    )


def _freeze_json(value: Any) -> Any:
    """Recursively freeze an otherwise JSON-shaped policy source for tamper tests."""
    if type(value) is dict:
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if type(value) is list:
        return tuple(_freeze_json(item) for item in value)
    return value


def test_complete_default_policy_validates_through_safety_seam() -> None:
    from semantic_reheating.validation import validate_run_policy

    source = _policy()

    assert validate_run_policy(source) == source


@pytest.mark.parametrize(
    ("value", "expected_code"),
    [
        pytest.param(
            lambda: {**_policy(), "unexpected": "field"},
            "schema_validation_error",
            id="unknown_field",
        ),
        pytest.param(
            lambda: {**_policy(), "max_recovery_episodes": "zero"},
            "schema_validation_error",
            id="wrong_type",
        ),
        pytest.param(
            lambda: {**_policy(), "contract_version": "2.0"},
            "unknown_contract_major",
            id="unknown_contract_major",
        ),
        pytest.param(object, "non_json_data", id="hostile_object"),
        pytest.param(
            lambda: type("HostileList", (list,), {})(),
            "non_json_data",
            id="hostile_list_subclass",
        ),
    ],
)
def test_run_policy_model_preserves_ordinary_validation_codes_without_exception_graphs(
    value: Any, expected_code: str
) -> None:
    from semantic_reheating.models import ModelValidationError, RunPolicy

    source = value()

    with pytest.raises(ModelValidationError) as caught:
        RunPolicy.from_dict(source)
    assert caught.value.code == expected_code
    assert caught.value.args == ("Invalid model input",)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


_NAMED_STRUCTURAL_SAFETY_MUTATIONS = (
    (
        "missing_whole_run",
        lambda source: source["budgets"].pop("whole_run"),
    ),
    *(
        (
            f"missing_{scope}_{dimension}",
            lambda source, scope=scope, dimension=dimension: source["budgets"][scope].pop(dimension),
        )
        for scope in ("per_intervention", "whole_run")
        for dimension in ("turns", "tool_calls", "tokens", "elapsed_seconds", "cost")
    ),
    (
        "automatic_non_idempotent_repeat",
        lambda source: source["side_effect_rules"].__setitem__(
            "automatic_unconfirmed_non_idempotent_repeat", True
        ),
    ),
    (
        "unknown_treated_as_repeatable",
        lambda source: source["side_effect_rules"].__setitem__(
            "unknown_treated_as_repeatable", True
        ),
    ),
    (
        "restart_without_host_action",
        lambda source: source["recovery_ladder"]["restart"].__setitem__(
            "requires_host_action", False
        ),
    ),
    (
        "stop_not_permitted",
        lambda source: source["recovery_ladder"]["stop"].__setitem__("permitted", False),
    ),
    (
        "escalate_not_permitted",
        lambda source: source["recovery_ladder"]["escalate"].__setitem__("permitted", False),
    ),
    (
        "escalate_without_host_action",
        lambda source: source["recovery_ladder"]["escalate"].__setitem__(
            "requires_host_action", False
        ),
    ),
    (
        "semantic_detector_relaxes_hard_stops",
        lambda source: source["detectors"]["semantic_detector"].__setitem__(
            "can_relax_hard_stops", True
        ),
    ),
)


def _encoded_policy(source: dict[str, Any], representation: str) -> Any:
    if representation == "dict":
        return deepcopy(source)
    encoded = json.dumps(source, separators=(",", ":"))
    if representation == "str":
        return encoded
    if representation == "bytes":
        return encoded.encode("utf-8")
    if representation == "bytearray":
        return bytearray(encoded, "utf-8")
    raise AssertionError("unknown test representation")


@pytest.mark.parametrize(
    ("mutation_id", "mutation"), _NAMED_STRUCTURAL_SAFETY_MUTATIONS
)
@pytest.mark.parametrize("representation", ("dict", "str", "bytes", "bytearray"))
def test_named_structurally_unsafe_policies_are_schema_rejected_and_sanitized_by_safety_seam(
    mutation_id: str, mutation: Any, representation: str
) -> None:
    from semantic_reheating.models import ModelValidationError, RunPolicy
    from semantic_reheating.validation import (
        ContractValidationError,
        validate_public_artifact,
        validate_run_policy,
    )

    source = _policy()
    mutation(source)

    encoded = _encoded_policy(source, representation)

    with pytest.raises(ContractValidationError) as schema_error:
        validate_public_artifact("run_policy", encoded)
    assert schema_error.value.code == "schema_validation_error"  # immediate-pass schema proof
    with pytest.raises(ContractValidationError) as safety_error:
        validate_run_policy(encoded)
    assert safety_error.value.code == "unsafe_policy"
    assert safety_error.value.args == ("Unsafe run policy",)
    assert safety_error.value.__cause__ is None
    assert safety_error.value.__context__ is None
    with pytest.raises(ModelValidationError) as model_error:
        RunPolicy.from_dict(encoded)
    assert model_error.value.code == "unsafe_policy"
    assert model_error.value.args == ("Invalid model input",)
    assert model_error.value.__cause__ is None
    assert model_error.value.__context__ is None

    if representation == "dict":
        forged = object.__new__(RunPolicy)
        object.__setattr__(forged, "_source", _freeze_json(source))
        with pytest.raises(ModelValidationError) as forged_error:
            forged.to_dict()
        assert forged_error.value.code == "unsafe_policy"
        assert forged_error.value.__cause__ is None
        assert forged_error.value.__context__ is None


@pytest.mark.parametrize("dimension", ("turns", "tool_calls", "tokens", "elapsed_seconds", "cost"))
def test_whole_run_budget_must_cover_each_per_intervention_dimension(dimension: str) -> None:
    from semantic_reheating.validation import (
        ContractValidationError,
        validate_run_policy,
    )

    source = _policy()
    source["budgets"]["whole_run"][dimension] = source["budgets"]["per_intervention"][dimension] - 1

    with pytest.raises(ContractValidationError) as caught:
        validate_run_policy(source)
    assert caught.value.code == "unsafe_policy"


def test_equal_mixed_numeric_budget_caps_are_safe() -> None:
    from semantic_reheating.validation import validate_run_policy

    source = _policy()
    source["budgets"]["per_intervention"]["elapsed_seconds"] = 2
    source["budgets"]["whole_run"]["elapsed_seconds"] = 2.0
    source["budgets"]["per_intervention"]["cost"] = 0.5
    source["budgets"]["whole_run"]["cost"] = 0.5

    assert validate_run_policy(source) == source


@pytest.mark.parametrize(
    ("source", "expected_code", "secret"),
    [
        ('{"__malformed-policy-secret__":', "invalid_json", "__malformed-policy-secret__"),
        (
            '{"__duplicate-policy-secret__":1,"__duplicate-policy-secret__":2}',
            "duplicate_key",
            "__duplicate-policy-secret__",
        ),
        (b'{"__invalid-utf8-policy-secret__":"\xff"}', "invalid_json_encoding", "__invalid-utf8-policy-secret__"),
        (bytearray(b'{"__invalid-utf8-bytearray-secret__":"\xff"}'), "invalid_json_encoding", "__invalid-utf8-bytearray-secret__"),
        ('{"__nonfinite-policy-secret__":NaN}', "invalid_json_number", "__nonfinite-policy-secret__"),
        (
            json.dumps({**_policy(), "contract_version": "2.0-__unknown-major-policy-secret__"}),
            "unknown_contract_major",
            "__unknown-major-policy-secret__",
        ),
    ],
)
def test_encoded_run_policy_parse_failures_preserve_codes_and_sanitize_exception_graphs(
    source: str | bytes | bytearray, expected_code: str, secret: str
) -> None:
    from semantic_reheating.models import ModelValidationError, RunPolicy
    from semantic_reheating.validation import (
        ContractValidationError,
        validate_run_policy,
    )

    with pytest.raises(ContractValidationError) as safety_error:
        validate_run_policy(source)
    assert safety_error.value.code == expected_code
    assert safety_error.value.args == ("Invalid run policy",)
    assert safety_error.value.__cause__ is None
    assert safety_error.value.__context__ is None
    assert secret not in str(safety_error.value)

    with pytest.raises(ModelValidationError) as model_error:
        RunPolicy.from_dict(source)
    assert model_error.value.code == expected_code
    assert model_error.value.args == ("Invalid model input",)
    assert model_error.value.__cause__ is None
    assert model_error.value.__context__ is None
    assert secret not in str(model_error.value)


@pytest.mark.parametrize(
    ("reheat_permitted", "episodes", "safe"),
    [(True, 0, False), (False, 0, True), (False, 2, True)],
)
def test_recovery_episode_bounds_are_explicit_and_reheat_aware(
    reheat_permitted: bool, episodes: int, safe: bool
) -> None:
    from semantic_reheating.validation import (
        ContractValidationError,
        validate_run_policy,
    )

    source = _policy()
    source["recovery_ladder"]["reheat"]["permitted"] = reheat_permitted
    source["max_recovery_episodes"] = episodes
    source["max_reentry_depth"] = 0

    if safe:
        assert validate_run_policy(source) == source
    else:
        with pytest.raises(ContractValidationError) as caught:
            validate_run_policy(source)
        assert caught.value.code == "unsafe_policy"


@pytest.mark.parametrize(
    "required_classes",
    [("repetition", "risk"), ("no_progress", "budget"), ("repetition", "repetition")],
)
def test_default_reheat_gate_is_exact_and_schema_protected(required_classes: tuple[str, str]) -> None:
    from semantic_reheating.validation import (
        ContractValidationError,
        validate_public_artifact,
        validate_run_policy,
    )

    source = _policy()
    source["agreeing_signals"]["required_classes"] = list(required_classes)

    with pytest.raises(ContractValidationError) as schema_error:
        validate_public_artifact("run_policy", deepcopy(source))
    assert schema_error.value.code == "schema_validation_error"  # immediate-pass schema proof
    with pytest.raises(ContractValidationError) as safety_error:
        validate_run_policy(source)
    assert safety_error.value.code == "schema_validation_error"


@pytest.mark.parametrize(
    "findings",
    [
        sequence
        for length in range(5)
        for sequence in product(("repetition", "no_progress", "risk", "budget"), repeat=length)
    ],
)
def test_reheat_gate_requires_both_independent_default_finding_classes(
    findings: tuple[str, ...]
) -> None:
    from semantic_reheating.models import FindingClass, RunPolicy

    policy = RunPolicy.from_dict(_policy())
    observed = tuple(FindingClass(item) for item in findings)

    assert policy.allows_reheat(observed) is (
        FindingClass.REPETITION in observed and FindingClass.NO_PROGRESS in observed
    )


def test_reheat_gate_is_false_when_reheat_is_disabled() -> None:
    from semantic_reheating.models import FindingClass, RunPolicy

    source = _policy()
    source["recovery_ladder"]["reheat"]["permitted"] = False
    policy = RunPolicy.from_dict(source)

    assert policy.allows_reheat((FindingClass.REPETITION, FindingClass.NO_PROGRESS)) is False


def test_run_policy_model_and_gate_fail_closed_without_exception_graph_leakage() -> None:
    from types import MappingProxyType

    from semantic_reheating.models import FindingClass, ModelValidationError, RunPolicy

    secret = "__hostile-policy-secret__"

    class HostileDict(dict[str, Any]):
        def items(self) -> Any:
            raise RuntimeError(secret)

    unsafe_source = _policy()
    unsafe_source["max_recovery_episodes"] = 0
    with pytest.raises(ModelValidationError) as unsafe_error:
        RunPolicy.from_dict(unsafe_source)
    assert unsafe_error.value.code == "unsafe_policy"
    assert unsafe_error.value.args == ("Invalid model input",)
    assert unsafe_error.value.__cause__ is None
    assert unsafe_error.value.__context__ is None

    forged = object.__new__(RunPolicy)
    object.__setattr__(forged, "_source", MappingProxyType(HostileDict()))
    with pytest.raises(ModelValidationError) as forged_error:
        forged.to_dict()
    assert forged_error.value.code == "unsafe_policy"
    assert secret not in repr(forged_error.value)
    assert forged_error.value.__cause__ is None
    assert forged_error.value.__context__ is None

    policy = RunPolicy.from_dict(_policy())
    object.__setattr__(policy, "max_recovery_episodes", 0)
    assert policy.allows_reheat((FindingClass.REPETITION, FindingClass.NO_PROGRESS)) is True
    for hostile_input in (
        "repetition",
        b"repetition",
        [FindingClass.REPETITION, "no_progress"],
        (FindingClass.REPETITION, True),
    ):
        with pytest.raises(ModelValidationError) as input_error:
            policy.allows_reheat(hostile_input)  # type: ignore[arg-type]
        assert input_error.value.code == "invalid_finding_classes"
        assert input_error.value.__cause__ is None
        assert input_error.value.__context__ is None


def test_valid_run_policy_round_trips_through_to_dict_pickle_and_deepcopy() -> None:
    from semantic_reheating.models import RunPolicy

    policy = RunPolicy.from_dict(_policy())

    assert policy.to_dict() == _policy()
    assert pickle.loads(pickle.dumps(policy)).to_dict() == _policy()
    assert deepcopy(policy).to_dict() == _policy()
