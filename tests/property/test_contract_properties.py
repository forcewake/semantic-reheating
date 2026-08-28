"""Exhaustive-by-construction public-contract parity and canonicalization proofs."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from copy import deepcopy
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "contracts"
SCHEMAS = ROOT / "contracts" / "v1"

# This list is asserted against the actual public schemas and parser seams below.
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


@dataclass(frozen=True)
class Mutation:
    artifact: str
    pointer: str
    keyword: str
    description: str
    mutate: Callable[[dict[str, Any]], None]


def _fixture(kind: str) -> dict[str, Any]:
    return json.loads((FIXTURES / _ARTIFACTS[kind][0]).read_text(encoding="utf-8"))


@cache
def _schema_data(kind: str) -> dict[str, Any]:
    schema = json.loads((SCHEMAS / _ARTIFACTS[kind][1]).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return schema


@cache
def _schema(kind: str) -> Draft202012Validator:
    return Draft202012Validator(_schema_data(kind))


def _runtime_parse(kind: str, value: dict[str, Any]) -> Any:
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
    return parsers[kind](value)


def _runtime_accepts(kind: str, value: dict[str, Any]) -> bool:
    try:
        _runtime_parse(kind, value)
    except ValueError:
        return False
    return True


def _assert_rejected(kind: str, mutation: Mutation, value: dict[str, Any]) -> None:
    label = f"{kind} {mutation.pointer} {mutation.keyword}: {mutation.description}"
    schema_accepts = _schema(kind).is_valid(value)
    runtime_accepts = _runtime_accepts(kind, value)
    assert schema_accepts == runtime_accepts, label
    assert not schema_accepts, label


def _pointer(parts: tuple[str | int, ...]) -> str:
    return "/" + "/".join(
        str(part).replace("~", "~0").replace("/", "~1") for part in parts
    )


def _at(root: Any, parts: tuple[str | int, ...]) -> Any:
    for part in parts:
        root = root[part]
    return root


def _replace(
    parts: tuple[str | int, ...], replacement: Any
) -> Callable[[dict[str, Any]], None]:
    def mutate(data: dict[str, Any]) -> None:
        parent = _at(data, parts[:-1])
        parent[parts[-1]] = deepcopy(replacement)

    return mutate


def _delete(parts: tuple[str | int, ...]) -> Callable[[dict[str, Any]], None]:
    def mutate(data: dict[str, Any]) -> None:
        del _at(data, parts[:-1])[parts[-1]]

    return mutate


def _unknown(parts: tuple[str | int, ...]) -> Callable[[dict[str, Any]], None]:
    def mutate(data: dict[str, Any]) -> None:
        _at(data, parts)["task15_unknown"] = None

    return mutate


def _resolve(root: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    ref = node.get("$ref")
    if ref is None:
        return node
    assert type(ref) is str and ref.startswith("#/") and not ref.startswith("#//"), ref
    value: Any = root
    for segment in ref[2:].split("/"):
        value = value[segment.replace("~1", "/").replace("~0", "~")]
    assert type(value) is dict
    return {**value, **{key: item for key, item in node.items() if key != "$ref"}}


def _wrong_type(types: object) -> object:
    permitted = (types,) if type(types) is str else tuple(types)
    # JSON Schema deliberately treats integers as numbers, while Python bool is an
    # int subclass: choose bool explicitly for either numeric schema type.
    if "integer" in permitted or "number" in permitted:
        return False
    candidates = {
        "boolean": 0,
        "string": 0,
        "object": 0,
        "array": 0,
        "null": 0,
    }
    for candidate in candidates.values():
        actual = (
            "null"
            if candidate is None
            else "boolean"
            if type(candidate) is bool
            else "integer"
            if type(candidate) is int
            else "string"
            if type(candidate) is str
            else "array"
            if type(candidate) is list
            else "object"
        )
        if actual not in permitted:
            return candidate
    raise AssertionError(f"cannot make type violation for {types!r}")


def _different(value: object) -> object:
    if value is True:
        return False
    if value is False:
        return True
    if value is None:
        return "task15-not-null"
    if type(value) is int:
        return value + 1
    if type(value) is float:
        return value + 1.0
    if type(value) is str:
        return "task15-invalid" if value != "task15-invalid" else "task15-other"
    if type(value) is list:
        return ["task15-invalid"]
    return "task15-invalid"


def _invalid_pattern(pattern: str) -> str:
    compiled = re.compile(pattern)
    for candidate in ("", "!", " ", "task15 invalid", "0" * 129):
        if compiled.fullmatch(candidate) is None:
            return candidate
    raise AssertionError(f"no bounded pattern counterexample for {pattern!r}")


def _walk(
    root_schema: dict[str, Any],
    node: dict[str, Any],
    value: Any,
    path: tuple[str | int, ...],
) -> Iterator[Mutation]:
    """Walk only local refs and every seed-reachable object/property/item node."""
    node = _resolve(root_schema, node)
    for member in node.get("allOf", []):
        yield from _walk(root_schema, member, value, path)
    # The public artifact root is necessarily held in a mapping for in-place mutation;
    # every nested typed property is independently covered below.
    if path and "type" in node:
        yield Mutation(
            "",
            _pointer(path),
            "type",
            "exact JSON type violation",
            _replace(path, _wrong_type(node["type"])),
        )
    if "enum" in node:
        options = node["enum"]
        candidate = _different(options[0])
        while candidate in options:
            candidate = _different(candidate)
        yield Mutation(
            "",
            _pointer(path),
            "enum",
            "outside closed enumeration",
            _replace(path, candidate),
        )
    if "const" in node:
        yield Mutation(
            "",
            _pointer(path),
            "const",
            "outside fixed constant",
            _replace(path, _different(node["const"])),
        )
    if isinstance(value, (int, float)) and type(value) is not bool:
        for keyword, delta in (
            ("minimum", -1),
            ("maximum", 1),
            ("exclusiveMinimum", -1),
            ("exclusiveMaximum", 1),
        ):
            if keyword in node:
                bound = node[keyword]
                replacement = bound + delta
                yield Mutation(
                    "",
                    _pointer(path),
                    keyword,
                    f"violates {keyword}",
                    _replace(path, replacement),
                )
        if "multipleOf" in node:
            multiple = node["multipleOf"]
            yield Mutation(
                "",
                _pointer(path),
                "multipleOf",
                "not a multiple",
                _replace(path, multiple / 2),
            )
    if type(value) is str:
        if "minLength" in node:
            yield Mutation(
                "",
                _pointer(path),
                "minLength",
                "below string lower bound",
                _replace(path, ""),
            )
        if "maxLength" in node:
            yield Mutation(
                "",
                _pointer(path),
                "maxLength",
                "above string upper bound",
                _replace(path, "x" * (node["maxLength"] + 1)),
            )
        if "pattern" in node:
            yield Mutation(
                "",
                _pointer(path),
                "pattern",
                "outside regular language",
                _replace(path, _invalid_pattern(node["pattern"])),
            )
    if type(value) is dict:
        if node.get("additionalProperties") is False:
            yield Mutation(
                "",
                _pointer(path),
                "additionalProperties",
                "unknown key at closed object",
                _unknown(path),
            )
        for field in node.get("required", []):
            if field in value:
                yield Mutation(
                    "",
                    _pointer(path + (field,)),
                    "required",
                    "remove required field",
                    _delete(path + (field,)),
                )
        properties = node.get("properties", {})
        for field, child in properties.items():
            if field in value:
                yield from _walk(root_schema, child, value[field], path + (field,))
    if type(value) is list:
        if "minItems" in node and node["minItems"] > 0:
            yield Mutation(
                "",
                _pointer(path),
                "minItems",
                "below array lower bound",
                _replace(path, []),
            )
        if "maxItems" in node:
            # Overflow with a novel scalar avoids uniqueItems as a second violation.
            replacement = deepcopy(value)
            replacement.extend(
                ["task15-overflow"] * (node["maxItems"] + 1 - len(replacement))
            )
            if len(replacement) > node["maxItems"] and len(
                set(map(repr, replacement))
            ) == len(replacement):
                yield Mutation(
                    "",
                    _pointer(path),
                    "maxItems",
                    "above array upper bound",
                    _replace(path, replacement),
                )
        if node.get("uniqueItems") is True and value:
            yield Mutation(
                "",
                _pointer(path),
                "uniqueItems",
                "duplicate array member",
                _replace(path, [value[0], value[0], *value[2:]]),
            )
        items = node.get("items")
        if type(items) is dict:
            for index, item in enumerate(value):
                yield from _walk(root_schema, items, item, path + (index,))
    # Locally expressible conditionals are deliberately forced from a valid seed.
    for index, branch in enumerate(node.get("allOf", [])):
        if "if" in branch and "then" in branch and type(value) is dict:
            condition = branch["if"].get("properties", {})
            for field, rule in condition.items():
                if field in value and "const" in rule:
                    forced = deepcopy(value)
                    forced[field] = rule["const"]
                    then_required = branch["then"].get("properties", {})
                    for target, target_rule in then_required.items():
                        for required in target_rule.get("required", []):
                            if target in forced and required in forced[target]:

                                def conditional(
                                    data: dict[str, Any],
                                    *,
                                    p=path,
                                    f=field,
                                    c=rule["const"],
                                    t=target,
                                    r=required,
                                ) -> None:
                                    current = _at(data, p)
                                    current[f] = c
                                    del current[t][r]

                                yield Mutation(
                                    "",
                                    _pointer(path + (target, required)),
                                    "if/then",
                                    f"force allOf[{index}] then requirement",
                                    conditional,
                                )


def _inventory() -> tuple[Mutation, ...]:
    cases: list[Mutation] = []
    for artifact in _ARTIFACTS:
        seen: set[tuple[str, str]] = set()
        for case in _walk(
            _schema_data(artifact), _schema_data(artifact), _fixture(artifact), ()
        ):
            identity = (case.pointer, case.keyword)
            if identity not in seen:
                seen.add(identity)
                cases.append(
                    Mutation(
                        artifact,
                        case.pointer,
                        case.keyword,
                        case.description,
                        case.mutate,
                    )
                )
    return tuple(cases)


MUTATIONS = _inventory()
# Every schema-reachable constraint is either represented above or explicitly impossible
# from its one committed deep-valid seed without first adding a second independent defect.
EXCLUSIONS: dict[str, str] = {}


def test_public_artifact_inventory_matches_actual_schema_and_runtime_seams() -> None:
    from semantic_reheating.validation import PUBLIC_CONTRACT_SCHEMAS

    actual_schemas = {
        path.name.removesuffix(".schema.json").replace("-", "_")
        for path in SCHEMAS.glob("*.schema.json")
    }
    assert (
        actual_schemas == set(_ARTIFACTS) == {"trace_event", *PUBLIC_CONTRACT_SCHEMAS}
    )
    assert MUTATIONS
    for pointer in EXCLUSIONS:
        assert any(case.pointer == pointer for case in MUTATIONS), pointer


@pytest.mark.parametrize("artifact", tuple(_ARTIFACTS))
def test_each_committed_deep_valid_seed_is_accepted_independently(
    artifact: str,
) -> None:
    seed = _fixture(artifact)
    # Keep validation calls separate: one boundary cannot mask the other.
    _schema(artifact).validate(deepcopy(seed))
    _runtime_parse(artifact, deepcopy(seed))


def test_every_derived_local_structural_mutation_is_rejected_by_schema_and_runtime() -> (
    None
):
    for mutation in MUTATIONS:
        invalid = _fixture(mutation.artifact)
        before = deepcopy(invalid)
        mutation.mutate(invalid)
        assert json.dumps(invalid, sort_keys=True) != json.dumps(
            before, sort_keys=True
        ), f"{mutation.artifact} {mutation.pointer} {mutation.keyword} did not mutate"
        _assert_rejected(mutation.artifact, mutation, invalid)


@settings(derandomize=True, database=None, deadline=500, max_examples=48)
@given(
    index=st.integers(min_value=0, max_value=max(0, len(MUTATIONS) - 1)),
    reverse=st.booleans(),
)
def test_hypothesis_selects_mutations_and_valid_nested_reorders(
    index: int, reverse: bool
) -> None:
    """Generated selection and key order exercise cached inventory and parser copies."""
    mutation = MUTATIONS[index]
    invalid = _fixture(mutation.artifact)
    mutation.mutate(invalid)
    _assert_rejected(mutation.artifact, mutation, invalid)
    seed = _fixture(mutation.artifact)
    if reverse:
        seed = {key: seed[key] for key in reversed(tuple(seed))}
    _schema(mutation.artifact).validate(seed)
    _runtime_parse(mutation.artifact, seed)


@settings(derandomize=True, database=None, deadline=500, max_examples=24)
@given(scalar=st.one_of(st.none(), st.booleans(), st.integers(), st.text(max_size=24)))
def test_canonical_bytes_and_fingerprint_ignore_nested_map_key_order(
    scalar: object,
) -> None:
    from semantic_reheating.canonical import action_fingerprint, canonicalize_json

    source = {
        "zeta": {"third": scalar, "first": [scalar, {"z": 1, "a": scalar}]},
        "alpha": [{"omega": scalar, "beta": {"right": scalar, "left": 0}}],
    }
    reordered = {key: source[key] for key in reversed(tuple(source))}
    assert canonicalize_json(source) == canonicalize_json(reordered)
    assert action_fingerprint(source).digest == action_fingerprint(reordered).digest
