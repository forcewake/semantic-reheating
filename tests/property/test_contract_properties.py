"""Schema-complete public-contract parity and canonicalization properties.

The inventory is deliberately deterministic: every schema keyword is audited first,
then either given an independently valid seed and one isolated invalid mutation, or a
structural exclusion explaining why a single-keyword violation cannot exist.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator, Mapping
from copy import deepcopy
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st
from jsonschema import Draft202012Validator, ValidationError

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
_CONSTRAINT_KEYS = frozenset(
    {
        "type",
        "enum",
        "const",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minLength",
        "maxLength",
        "pattern",
        "required",
        "additionalProperties",
        "minItems",
        "maxItems",
        "uniqueItems",
    }
)


@dataclass(frozen=True)
class SeedVariant:
    artifact: str
    name: str
    value: dict[str, Any]


@dataclass(frozen=True)
class Exclusion:
    artifact: str
    schema_pointer: str
    keyword: str
    reason: str


@dataclass(frozen=True)
class Mutation:
    artifact: str
    seed: str
    schema_pointer: str
    instance_pointer: str
    keyword: str
    description: str
    changed_pointers: frozenset[str]
    apply: Callable[[Any], Any]

    @property
    def identity(self) -> tuple[str, str, str, str, str]:
        return (
            self.artifact,
            self.seed,
            self.schema_pointer,
            self.instance_pointer,
            self.keyword,
        )


@cache
def _fixture(kind: str) -> dict[str, Any]:
    return json.loads((FIXTURES / _ARTIFACTS[kind][0]).read_text(encoding="utf-8"))


@cache
def _schema_data(kind: str) -> dict[str, Any]:
    value = json.loads((SCHEMAS / _ARTIFACTS[kind][1]).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(value)
    return value


@cache
def _schema(kind: str) -> Draft202012Validator:
    return Draft202012Validator(_schema_data(kind))


def _runtime_parse(kind: str, value: Any) -> Any:
    from semantic_reheating.controller import RecoveryInstruction
    from semantic_reheating.evidence import EvidenceRecord, RecoveryOutcome
    from semantic_reheating.models import DecisionEnvelope, RunPolicy, TraceEvent
    from semantic_reheating.validation import validate_public_artifact

    parsers: Mapping[str, Callable[[Any], Any]] = {
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


def _runtime_accepts(kind: str, value: Any) -> bool:
    try:
        _runtime_parse(kind, value)
    except ValueError:
        return False
    return True


def _json_pointer(parts: tuple[str | int, ...]) -> str:
    if not parts:
        return ""
    return "/" + "/".join(
        str(part).replace("~", "~0").replace("/", "~1") for part in parts
    )


def _schema_pointer(parts: tuple[str | int, ...]) -> str:
    return "#" + _json_pointer(parts)


def _at(value: Any, parts: tuple[str | int, ...]) -> Any:
    for part in parts:
        value = value[part]
    return value


def _replace(parts: tuple[str | int, ...], replacement: Any) -> Callable[[Any], Any]:
    def apply(value: Any) -> Any:
        copied = deepcopy(value)
        if not parts:
            return deepcopy(replacement)
        _at(copied, parts[:-1])[parts[-1]] = deepcopy(replacement)
        return copied

    return apply


def _delete(parts: tuple[str | int, ...]) -> Callable[[Any], Any]:
    def apply(value: Any) -> Any:
        copied = deepcopy(value)
        del _at(copied, parts[:-1])[parts[-1]]
        return copied

    return apply


def _add_unknown(parts: tuple[str | int, ...]) -> Callable[[Any], Any]:
    def apply(value: Any) -> Any:
        copied = deepcopy(value)
        _at(copied, parts)["task15_unknown"] = None
        return copied

    return apply


def _resolve(
    root: dict[str, Any], node: dict[str, Any], pointer: tuple[str | int, ...]
) -> tuple[dict[str, Any], tuple[str | int, ...]]:
    ref = node.get("$ref")
    if ref is None:
        return node, pointer
    assert type(ref) is str and ref.startswith("#/") and not ref.startswith("#//"), ref
    target: Any = root
    ref_parts: list[str] = []
    for part in ref[2:].split("/"):
        unescaped = part.replace("~1", "/").replace("~0", "~")
        ref_parts.append(unescaped)
        target = target[unescaped]
    assert type(target) is dict
    overlays = {key: item for key, item in node.items() if key != "$ref"}
    return {**target, **overlays}, tuple(ref_parts)


def _wrong_type(types: object) -> object:
    allowed = {types} if type(types) is str else set(types)
    candidates: tuple[object, ...] = (False, 0, "task15-wrong-type", [], {}, None)
    for candidate in candidates:
        actual = (
            "boolean"
            if type(candidate) is bool
            else "integer"
            if type(candidate) is int
            else "string"
            if type(candidate) is str
            else "array"
            if type(candidate) is list
            else "object"
            if type(candidate) is dict
            else "null"
        )
        if actual not in allowed:
            return candidate
    raise AssertionError(f"cannot construct wrong JSON type for {types!r}")


def _different(value: Any) -> Any:
    if value is True:
        return False
    if value is False:
        return True
    if value is None:
        return "task15-invalid"
    if type(value) is int:
        return value + 1
    if type(value) is float:
        return value + 1.0
    if type(value) is str:
        return "task15-invalid" if value != "task15-invalid" else "task15-other"
    if type(value) is list:
        return ["task15-invalid"]
    return "task15-invalid"


def _valid_long_string(node: dict[str, Any]) -> str | None:
    maximum = node["maxLength"]
    pattern = node.get("pattern")
    candidates = (
        "x" * (maximum + 1),
        "a" + "0" * maximum,
        "rejected-hypothesis-" + "a" * 24,
    )
    for candidate in candidates:
        if len(candidate) <= maximum:
            continue
        if pattern is None or re.search(pattern, candidate) is not None:
            return candidate
    return None


def _invalid_pattern(node: dict[str, Any]) -> str | None:
    pattern = re.compile(node["pattern"])
    lower = node.get("minLength", 0)
    upper = node.get("maxLength", 10_000)
    for candidate in ("!", " ", "?", "task15 invalid", "Z" * max(1, lower)):
        if lower <= len(candidate) <= upper and pattern.search(candidate) is None:
            return candidate
    return None


def _leaf_errors(errors: Iterator[ValidationError]) -> tuple[ValidationError, ...]:
    leaves: list[ValidationError] = []

    def visit(error: ValidationError) -> None:
        if error.context:
            for child in error.context:
                visit(child)
        elif error.validator != "oneOf":
            leaves.append(error)

    for error in errors:
        visit(error)
    return tuple(leaves)


def _error_location(error: ValidationError) -> str:
    return _schema_pointer(tuple(error.absolute_schema_path))


def _recursive_diff(
    before: Any, after: Any, path: tuple[str | int, ...] = ()
) -> set[str]:
    """Return recursive JSON-pointer changes, collapsing list edits to their list root."""
    if type(before) is not type(after):
        return {_json_pointer(path)}
    if type(before) is dict:
        changed: set[str] = set()
        for key in set(before) | set(after):
            if key not in before or key not in after:
                changed.add(_json_pointer(path + (key,)))
            else:
                changed |= _recursive_diff(before[key], after[key], path + (key,))
        return changed
    if type(before) is list:
        return set() if before == after else {_json_pointer(path)}
    return set() if before == after else {_json_pointer(path)}


def _all_seed_variants() -> tuple[SeedVariant, ...]:
    trace_maximal = deepcopy(_fixture("trace_event"))
    trace_maximal["payload"] = {
        "outer": {"right": 1, "left": ["payload", {"z": 1, "a": 2}]}
    }
    trace_maximal.update(
        parent_event_id="event-parent",
        state_fingerprint="state-001",
        error_fingerprint="error-001",
        acceptance_delta="unchanged",
        evidence_refs=["evidence-001"],
        budget_counters={
            "turns": 1,
            "tool_calls": 2,
            "tokens": 3,
            "elapsed_seconds": 4.0,
            "cost": 5.0,
        },
        expected_state_change=True,
    )
    trace_ref = deepcopy(trace_maximal)
    trace_ref.pop("payload")
    trace_ref["payload_ref"] = "payload-ref-001"
    trace_digest = deepcopy(trace_maximal)
    trace_digest.pop("payload")
    trace_digest["payload_digest"] = "digest-001"

    decision_maximal = deepcopy(_fixture("decision_envelope"))
    decision_maximal["diagnosed_gaps"] = [
        {"kind": "missing_evidence", "description": "Gap."}
    ]
    decision_maximal["rejected_hypothesis_refs"] = [
        "rejected-hypothesis-0123456789abcdef01234567"
    ]
    decision_maximal["detector_notices"] = [
        {"detector_name": "semantic", "status": "degraded", "notice": "Degraded."}
    ]
    decision_branches: list[SeedVariant] = []
    for decision in (
        "continue",
        "nudge",
        "diagnose",
        "reheat",
        "restart",
        "escalate",
        "stop",
    ):
        branch = deepcopy(decision_maximal)
        branch["decision"] = decision
        branch["requires_host_action"] = decision == "escalate"
        branch["constraints"]["require_host_confirmation"] = decision == "escalate"
        decision_branches.append(
            SeedVariant("decision_envelope", f"decision-{decision}", branch)
        )

    instruction_maximal = deepcopy(_fixture("recovery_instruction"))
    instruction_maximal["variables"] = [{"name": "current_goal", "value": "Goal."}]
    instruction_maximal["rejected_hypothesis_refs"] = ["rejected-001"]
    instruction_other = deepcopy(instruction_maximal)
    instruction_other["selected_prompt_asset_id"] = "prompt-diagnose-v1"
    instruction_other["expected_output"].pop("hypothesis_contract")

    variants = [
        SeedVariant("trace_event", "payload-maximal", trace_maximal),
        SeedVariant("trace_event", "payload-ref", trace_ref),
        SeedVariant("trace_event", "payload-digest", trace_digest),
        SeedVariant("run_policy", "maximal", deepcopy(_fixture("run_policy"))),
        SeedVariant(
            "detector_finding", "maximal", deepcopy(_fixture("detector_finding"))
        ),
        *decision_branches,
        SeedVariant("recovery_instruction", "reheat-maximal", instruction_maximal),
        SeedVariant("recovery_instruction", "non-reheat-branch", instruction_other),
        SeedVariant(
            "recovery_outcome", "maximal", deepcopy(_fixture("recovery_outcome"))
        ),
        SeedVariant(
            "evidence_record", "maximal", deepcopy(_fixture("evidence_record"))
        ),
    ]
    return tuple(variants)


SEED_VARIANTS = _all_seed_variants()


def _constraints(
    root: dict[str, Any], node: dict[str, Any], pointer: tuple[str | int, ...] = ()
) -> set[tuple[str, str]]:
    node, pointer = _resolve(root, node, pointer)
    found = {
        (_schema_pointer(pointer + (key,)), key)
        for key in node
        if key in _CONSTRAINT_KEYS
    }
    for index, member in enumerate(node.get("allOf", [])):
        if "if" in member and "then" in member:
            found |= _constraints(
                root, member["then"], pointer + ("allOf", index, "then")
            )
        else:
            found |= _constraints(root, member, pointer + ("allOf", index))
    for index, member in enumerate(node.get("oneOf", [])):
        found |= _constraints(root, member, pointer + ("oneOf", index))
    for field, child in node.get("properties", {}).items():
        found |= _constraints(root, child, pointer + ("properties", field))
    items = node.get("items")
    if type(items) is dict:
        found |= _constraints(root, items, pointer + ("items",))
    return found


def _has_enum_or_const(node: dict[str, Any]) -> bool:
    return "enum" in node or "const" in node


def _novel_item(root: dict[str, Any], node: dict[str, Any], value: Any) -> Any | None:
    """Construct an item valid by itself and unequal to every current item."""
    node, _ = _resolve(root, node, ())
    validator = Draft202012Validator(node)
    existing = list(value)
    candidates: list[Any] = []
    if "enum" in node:
        candidates.extend(item for item in node["enum"] if item not in existing)
    elif "const" not in node:
        types = node.get("type")
        types = (types,) if type(types) is str else tuple(types or ())
        if "string" in types:
            candidates.extend(
                (
                    f"task15-novel-{len(existing)}",
                    "rejected-hypothesis-" + f"{len(existing):024x}",
                )
            )
        if "object" in types and existing and type(existing[0]) is dict:
            for field, item in existing[0].items():
                if type(item) is str:
                    changed = deepcopy(existing[0])
                    changed[field] = f"task15-novel-{len(existing)}"
                    candidates.append(changed)
        if not types and existing:
            candidates.append("task15-novel")
    for candidate in candidates:
        if candidate not in existing and validator.is_valid(candidate):
            return candidate
    return None


def _walk_seed(
    artifact: str,
    seed: SeedVariant,
    root: dict[str, Any],
    node: dict[str, Any],
    value: Any,
    schema_path: tuple[str | int, ...] = (),
    instance_path: tuple[str | int, ...] = (),
) -> tuple[list[Mutation], list[Exclusion]]:
    node, schema_path = _resolve(root, node, schema_path)
    mutations: list[Mutation] = []
    exclusions: list[Exclusion] = []

    def add(
        keyword: str,
        description: str,
        changed: tuple[str | int, ...],
        apply: Callable[[Any], Any],
    ) -> None:
        declared = changed
        for index in range(len(changed)):
            if type(_at(seed.value, changed[:index])) is list:
                declared = changed[:index]
                break
        mutations.append(
            Mutation(
                artifact,
                seed.name,
                _schema_pointer(schema_path + (keyword,)),
                _json_pointer(instance_path),
                keyword,
                description,
                frozenset((_json_pointer(declared),)),
                apply,
            )
        )

    def exclude(keyword: str, reason: str) -> None:
        exclusions.append(
            Exclusion(
                artifact, _schema_pointer(schema_path + (keyword,)), keyword, reason
            )
        )

    # A type counterexample cannot avoid a closed enum/const failure at this node.
    if "type" in node:
        if _has_enum_or_const(node):
            exclude(
                "type",
                "Closed enum/const permits no wrong JSON type without also violating enum/const.",
            )
        else:
            add(
                "type",
                "exact JSON type violation",
                instance_path,
                _replace(instance_path, _wrong_type(node["type"])),
            )
    if "enum" in node:
        candidate = _different(node["enum"][0])
        while candidate in node["enum"]:
            candidate = _different(candidate)
        add(
            "enum",
            "outside closed enumeration",
            instance_path,
            _replace(instance_path, candidate),
        )
    if "const" in node:
        add(
            "const",
            "outside fixed constant",
            instance_path,
            _replace(instance_path, _different(node["const"])),
        )
    if isinstance(value, (int, float)) and type(value) is not bool:
        for keyword, delta in (
            ("minimum", -1),
            ("maximum", 1),
            ("exclusiveMinimum", -1),
            ("exclusiveMaximum", 1),
        ):
            if keyword in node:
                add(
                    keyword,
                    f"violates {keyword}",
                    instance_path,
                    _replace(instance_path, node[keyword] + delta),
                )
        if "multipleOf" in node:
            add(
                "multipleOf",
                "not a multiple",
                instance_path,
                _replace(instance_path, node["multipleOf"] / 2),
            )
    if type(value) is str:
        if "minLength" in node:
            if (
                "pattern" in node
                and _invalid_pattern(
                    {**node, "minLength": 0, "maxLength": node["minLength"] - 1}
                )
                is None
            ):
                exclude(
                    "minLength",
                    "Every shorter candidate also violates the required pattern.",
                )
            else:
                add(
                    "minLength",
                    "below string lower bound",
                    instance_path,
                    _replace(instance_path, ""),
                )
        if "maxLength" in node:
            candidate = _valid_long_string(node)
            if candidate is None:
                exclude(
                    "maxLength",
                    "No string above maxLength can satisfy the fixed pattern.",
                )
            else:
                add(
                    "maxLength",
                    "above string upper bound",
                    instance_path,
                    _replace(instance_path, candidate),
                )
        if "pattern" in node:
            candidate = _invalid_pattern(node)
            if candidate is None:
                exclude(
                    "pattern",
                    "No length-valid counterexample could be constructed for this pattern.",
                )
            else:
                add(
                    "pattern",
                    "outside regular language",
                    instance_path,
                    _replace(instance_path, candidate),
                )
    if type(value) is dict:
        if node.get("additionalProperties") is False:
            unknown_path = instance_path + ("task15_unknown",)
            add(
                "additionalProperties",
                "unknown key at closed object",
                unknown_path,
                _add_unknown(instance_path),
            )
        for field in node.get("required", []):
            if field in value:
                field_path = instance_path + (field,)
                add(
                    "required", "remove required field", field_path, _delete(field_path)
                )
        for field, child in node.get("properties", {}).items():
            if field in value:
                more, omitted = _walk_seed(
                    artifact,
                    seed,
                    root,
                    child,
                    value[field],
                    schema_path + ("properties", field),
                    instance_path + (field,),
                )
                mutations.extend(more)
                exclusions.extend(omitted)
    if type(value) is list:
        if "minItems" in node:
            if node["minItems"] == 0:
                exclude("minItems", "JSON arrays cannot have fewer than zero items.")
            else:
                add(
                    "minItems",
                    "below array lower bound",
                    instance_path,
                    _replace(instance_path, []),
                )
        if node.get("maxItems") is not None:
            overflow = deepcopy(value)
            if node.get("uniqueItems") is True:
                while len(overflow) <= node["maxItems"]:
                    novel = _novel_item(root, node.get("items", {}), overflow)
                    if novel is None:
                        break
                    overflow.append(novel)
                if len(overflow) <= node["maxItems"]:
                    exclude(
                        "maxItems",
                        "uniqueItems with a closed item universe has no novel valid overflow item.",
                    )
                else:
                    add(
                        "maxItems",
                        "above array upper bound only",
                        instance_path,
                        _replace(instance_path, overflow),
                    )
            else:
                while len(overflow) <= node["maxItems"]:
                    overflow.append(deepcopy(value[0]))
                add(
                    "maxItems",
                    "above array upper bound only",
                    instance_path,
                    _replace(instance_path, overflow),
                )
        if node.get("uniqueItems") is True:
            if len(value) < 1 or node.get("maxItems", float("inf")) < 2:
                exclude(
                    "uniqueItems",
                    "The maximum cardinality cannot contain a duplicate pair.",
                )
            else:
                duplicate = [value[0], value[0], *value[2:]]
                add(
                    "uniqueItems",
                    "duplicate array member",
                    instance_path,
                    _replace(instance_path, duplicate),
                )
        items = node.get("items")
        if type(items) is dict:
            for index, item in enumerate(value):
                more, omitted = _walk_seed(
                    artifact,
                    seed,
                    root,
                    items,
                    item,
                    schema_path + ("items",),
                    instance_path + (index,),
                )
                mutations.extend(more)
                exclusions.extend(omitted)

    for index, member in enumerate(node.get("allOf", [])):
        if "if" in member and "then" in member:
            if not Draft202012Validator(member["if"]).is_valid(value):
                continue
            more, omitted = _walk_seed(
                artifact,
                seed,
                root,
                member["then"],
                value,
                schema_path + ("allOf", index, "then"),
                instance_path,
            )
        else:
            more, omitted = _walk_seed(
                artifact,
                seed,
                root,
                member,
                value,
                schema_path + ("allOf", index),
                instance_path,
            )
        mutations.extend(more)
        exclusions.extend(omitted)
    if "oneOf" in node:
        matching = [
            member
            for member in node["oneOf"]
            if Draft202012Validator(member).is_valid(value)
        ]
        assert len(matching) == 1, (artifact, seed.name, schema_path, matching)
        branch_index = node["oneOf"].index(matching[0])
        more, omitted = _walk_seed(
            artifact,
            seed,
            root,
            matching[0],
            value,
            schema_path + ("oneOf", branch_index),
            instance_path,
        )
        mutations.extend(more)
        exclusions.extend(omitted)
    return mutations, exclusions


def _candidate_isolated(case: Mutation, seed: SeedVariant) -> bool:
    errors = _leaf_errors(
        _schema(case.artifact).iter_errors(case.apply(deepcopy(seed.value)))
    )
    validators = {error.validator for error in errors}
    return bool(errors) and validators == {case.keyword}


def _inventory() -> tuple[tuple[Mutation, ...], tuple[Exclusion, ...]]:
    mutations: list[Mutation] = []
    exclusions: list[Exclusion] = []
    for seed in SEED_VARIANTS:
        cases, skipped = _walk_seed(
            seed.artifact,
            seed,
            _schema_data(seed.artifact),
            _schema_data(seed.artifact),
            seed.value,
        )
        mutations.extend(case for case in cases if _candidate_isolated(case, seed))
        exclusions.extend(skipped)
    identities = {case.identity: case for case in mutations}
    exclusion_keys: dict[tuple[str, str, str], Exclusion] = {}
    for exclusion in exclusions:
        exclusion_keys.setdefault(
            (exclusion.artifact, exclusion.schema_pointer, exclusion.keyword), exclusion
        )
    return tuple(identities.values()), tuple(exclusion_keys.values())


MUTATIONS, EXCLUSIONS = _inventory()
SCHEMA_CONSTRAINTS = {
    (artifact, pointer, keyword)
    for artifact in _ARTIFACTS
    for pointer, keyword in _constraints(_schema_data(artifact), _schema_data(artifact))
}


def _coverage_keys() -> set[tuple[str, str, str]]:
    return {
        (item.artifact, item.schema_pointer, item.keyword) for item in MUTATIONS
    } | {(item.artifact, item.schema_pointer, item.keyword) for item in EXCLUSIONS}


def _assert_isolated_rejection(mutation: Mutation, before: Any, invalid: Any) -> None:
    errors = _leaf_errors(_schema(mutation.artifact).iter_errors(invalid))
    found = {(_error_location(error), error.validator) for error in errors}
    validators = {validator for _, validator in found}
    # jsonschema reports a local $ref constraint at its use site; the inventory
    # retains the referenced schema location so repeated refs stay distinct.
    assert mutation.keyword in validators, (mutation, found)
    # A failed selected oneOf branch necessarily reports the alternative branch
    # requirements too; those are branch-discrimination contexts, not extra
    # violations credited to this mutation.
    if "/oneOf/" in mutation.schema_pointer:
        oneof_root = mutation.schema_pointer.split("/oneOf/", 1)[0] + "/oneOf/"
        assert validators == {mutation.keyword} and all(
            location.startswith(oneof_root) for location, _ in found
        ), (mutation, found)
    else:
        assert validators == {mutation.keyword}, (mutation, found)
    assert not _runtime_accepts(mutation.artifact, invalid), mutation


def test_schema_complete_seed_inventory_and_runtime_parity() -> None:
    from semantic_reheating.validation import PUBLIC_CONTRACT_SCHEMAS

    actual_schemas = {
        path.name.removesuffix(".schema.json").replace("-", "_")
        for path in SCHEMAS.glob("*.schema.json")
    }
    assert (
        actual_schemas == set(_ARTIFACTS) == {"trace_event", *PUBLIC_CONTRACT_SCHEMAS}
    )
    assert {seed.name for seed in SEED_VARIANTS if seed.artifact == "trace_event"} >= {
        "payload-maximal",
        "payload-ref",
        "payload-digest",
    }
    assert {
        seed.name for seed in SEED_VARIANTS if seed.artifact == "decision_envelope"
    } == {
        "decision-continue",
        "decision-nudge",
        "decision-diagnose",
        "decision-reheat",
        "decision-restart",
        "decision-escalate",
        "decision-stop",
    }
    for seed in SEED_VARIANTS:
        _schema(seed.artifact).validate(deepcopy(seed.value))
        _runtime_parse(seed.artifact, deepcopy(seed.value))
    assert _coverage_keys() == SCHEMA_CONSTRAINTS
    assert all(item.reason for item in EXCLUSIONS)


def test_every_schema_constraint_has_one_isolated_runtime_rejected_mutation() -> None:
    for mutation in MUTATIONS:
        seed = next(
            item
            for item in SEED_VARIANTS
            if item.artifact == mutation.artifact and item.name == mutation.seed
        )
        before = deepcopy(seed.value)
        # Each mutation iteration independently proves its named seed at both boundaries.
        _schema(mutation.artifact).validate(deepcopy(before))
        _runtime_parse(mutation.artifact, deepcopy(before))
        invalid = mutation.apply(before)
        assert _recursive_diff(before, invalid) == set(mutation.changed_pointers), (
            mutation
        )
        _assert_isolated_rejection(mutation, before, invalid)


@settings(derandomize=True, database=None, deadline=900, max_examples=64)
@given(
    index=st.integers(min_value=0, max_value=max(0, len(MUTATIONS) - 1)),
    reverse=st.booleans(),
)
def test_hypothesis_orders_named_variants_and_isolated_mutations(
    index: int, reverse: bool
) -> None:
    mutation = MUTATIONS[index]
    seed = next(
        item
        for item in SEED_VARIANTS
        if item.artifact == mutation.artifact and item.name == mutation.seed
    )
    before = deepcopy(seed.value)
    _schema(mutation.artifact).validate(deepcopy(before))
    _runtime_parse(mutation.artifact, deepcopy(before))
    invalid = mutation.apply(before)
    _assert_isolated_rejection(mutation, before, invalid)
    if reverse:
        reordered = _reverse_every_mapping(before)
        _schema(mutation.artifact).validate(reordered)
        _runtime_parse(mutation.artifact, reordered)


def _reverse_every_mapping(value: Any) -> Any:
    if type(value) is dict:
        return {
            key: _reverse_every_mapping(value[key]) for key in reversed(tuple(value))
        }
    if type(value) is list:
        return [_reverse_every_mapping(item) for item in value]
    return value


def _nested_key_order_changed(before: Any, after: Any, *, depth: int = 0) -> bool:
    if type(before) is dict and type(after) is dict:
        if (
            depth > 0
            and len(before) > 1
            and list(before) == list(reversed(list(after)))
        ):
            return True
        return all(
            _nested_key_order_changed(before[key], after[key], depth=depth + 1)
            for key in before
        )
    if type(before) is list and type(after) is list:
        return all(
            _nested_key_order_changed(left, right, depth=depth + 1)
            for left, right in zip(before, after, strict=True)
        )
    return True


@settings(derandomize=True, database=None, deadline=500, max_examples=24)
@given(
    scalar=st.one_of(
        st.none(),
        st.booleans(),
        st.integers(min_value=-(2**53 - 1), max_value=2**53 - 1),
        st.text(max_size=24),
    )
)
def test_canonical_bytes_and_fingerprint_ignore_every_nested_map_key_order(
    scalar: object,
) -> None:
    from semantic_reheating.canonical import action_fingerprint, canonicalize_json

    source = {
        "zeta": {"third": scalar, "first": [scalar, {"z": 1, "a": scalar}]},
        "alpha": [{"omega": scalar, "beta": {"right": scalar, "left": 0}}],
    }
    reordered = _reverse_every_mapping(source)
    assert list(source) == list(reversed(list(reordered)))
    assert _nested_key_order_changed(source, reordered)
    assert canonicalize_json(source) == canonicalize_json(reordered)
    assert action_fingerprint(source).digest == action_fingerprint(reordered).digest
