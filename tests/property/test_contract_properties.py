"""Schema-complete public-contract parity and canonicalization properties.

The inventory is deliberately deterministic: every schema keyword is audited first,
then either given an independently valid seed and one isolated invalid mutation, or a
structural exclusion explaining why a single-keyword violation cannot exist.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Callable, Iterator, Mapping
from copy import deepcopy
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

import pytest
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
    expected_errors: tuple[tuple[str, str, str], ...]
    covered_constraints: frozenset[tuple[str, str]]
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
    root: dict[str, Any],
    node: dict[str, Any],
    pointer: tuple[str | int, ...],
    active_locations: frozenset[tuple[str | int, ...]] = frozenset(),
) -> tuple[dict[str, Any], tuple[str | int, ...]]:
    """Resolve a local reference chain without silently accepting a cycle."""
    seen = set(active_locations)
    while "$ref" in node:
        ref = node["$ref"]
        target_pointer = _ref_parts(ref)
        if target_pointer in seen:
            raise AssertionError("cyclic_local_ref")
        seen.add(target_pointer)
        target = _at(root, target_pointer)
        assert type(target) is dict
        overlays = {key: item for key, item in node.items() if key != "$ref"}
        node = {**target, **overlays}
        pointer = target_pointer
    return node, pointer


def _wrong_type(types: object) -> object:
    if isinstance(types, str):
        allowed = {types}
    else:
        assert isinstance(types, (list, tuple)) and all(
            isinstance(item, str) for item in types
        ), types
        allowed = set(types)
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


def _ref_parts(ref: str) -> tuple[str, ...]:
    assert type(ref) is str and ref.startswith("#"), ref
    if ref == "#":
        return ()
    assert ref.startswith("#/") and not ref.startswith("#//"), ref
    return tuple(
        part.replace("~1", "/").replace("~0", "~") for part in ref[2:].split("/")
    )


@cache
def _ref_aliases(kind: str) -> dict[tuple[str | int, ...], tuple[str | int, ...]]:
    """Expand each local ref use site to its canonical target path.

    jsonschema reports a Draft error below the concrete use site, even when the
    inventory deliberately names the canonical `$defs` target.  Ref expansion
    records only edges actually present in this schema graph; it never maps a
    same-keyword error merely because its instance location happens to match.
    """
    root = _schema_data(kind)
    aliases: dict[tuple[str | int, ...], tuple[str | int, ...]] = {}
    expanded: set[tuple[tuple[str | int, ...], tuple[str | int, ...]]] = set()

    def visit(node: Any, path: tuple[str | int, ...]) -> None:
        if type(node) is dict:
            ref = node.get("$ref")
            if ref is not None:
                target = _ref_parts(ref)
                aliases[path] = target
                expansion = (path, target)
                if expansion not in expanded:
                    expanded.add(expansion)
                    visit(_at(root, target), path)
            for key, child in node.items():
                if key != "$ref":
                    visit(child, path + (key,))
        elif type(node) is list:
            for index, child in enumerate(node):
                visit(child, path + (index,))

    visit(root, ())
    return aliases


def _normalized_error_location(kind: str, error: ValidationError) -> str:
    """Canonically resolve a Draft error location through real local ref edges."""
    location: tuple[str | int, ...] = tuple(error.absolute_schema_path)
    seen: set[tuple[str | int, ...]] = set()
    while location not in seen:
        seen.add(location)
        candidates = [
            source
            for source in _ref_aliases(kind)
            if len(source) <= len(location) and location[: len(source)] == source
        ]
        if not candidates:
            break
        source = max(candidates, key=len)
        normalized = _ref_aliases(kind)[source] + location[len(source) :]
        if normalized == location:
            break
        location = normalized
    return _schema_pointer(location)


def _normalized_leaf_counter(kind: str, value: Any) -> Counter[tuple[str, str, str]]:
    return Counter(
        (
            _normalized_error_location(kind, error),
            error.validator,
            _json_pointer(tuple(error.absolute_path)),
        )
        for error in _leaf_errors(_schema(kind).iter_errors(value))
    )


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


_DRAFT_METADATA_ANNOTATIONS = frozenset(
    {
        "$anchor",
        "$comment",
        "$defs",
        "$dynamicAnchor",
        "$id",
        "$schema",
        "$vocabulary",
        "contentEncoding",
        "contentMediaType",
        "contentSchema",
        "default",
        "deprecated",
        "description",
        "examples",
        "format",
        "readOnly",
        "title",
        "writeOnly",
    }
)
_DRAFT_APPLICATOR_KEYWORDS = frozenset(
    {
        "$dynamicRef",
        "$ref",
        "additionalProperties",
        "allOf",
        "anyOf",
        "contains",
        "dependentSchemas",
        "if",
        "items",
        "not",
        "oneOf",
        "patternProperties",
        "prefixItems",
        "properties",
        "propertyNames",
        "then",
        "else",
        "unevaluatedItems",
        "unevaluatedProperties",
    }
)
_DRAFT_VALIDATOR_KEYWORDS = frozenset(Draft202012Validator.VALIDATORS)
_DRAFT_ASSERTION_KEYWORDS = _DRAFT_VALIDATOR_KEYWORDS - (
    _DRAFT_METADATA_ANNOTATIONS | _DRAFT_APPLICATOR_KEYWORDS
)
# These are intentionally an explicit implementation contract, rather than a
# catch-all exclusion for Draft keywords added by a later jsonschema release.
_SUPPORTED_APPLICATOR_KEYWORDS = frozenset(
    {"$ref", "allOf", "else", "if", "items", "oneOf", "properties", "then"}
)


def _draft_child_schemas(
    node: Mapping[str, Any],
) -> Iterator[tuple[tuple[str | int, ...], Mapping[str, Any], bool]]:
    """Yield Draft child schemas using the applicator grammar only.

    The final flag says whether child assertions contribute direct validation
    errors at that location.  An `if` condition controls an application but
    does not itself reject an instance.
    """
    definitions = node.get("$defs")
    if type(definitions) is dict:
        for key, child in definitions.items():
            if type(child) is dict:
                yield ("$defs", key), child, True
    for keyword in ("properties", "patternProperties", "dependentSchemas"):
        children = node.get(keyword)
        if type(children) is dict:
            for key, child in children.items():
                if type(child) is dict:
                    yield (keyword, key), child, True
    for keyword in (
        "additionalProperties",
        "contains",
        "items",
        "not",
        "propertyNames",
        "then",
        "else",
        "unevaluatedItems",
        "unevaluatedProperties",
    ):
        child = node.get(keyword)
        if type(child) is dict:
            yield (keyword,), child, keyword != "not"
    for keyword in ("allOf", "anyOf", "oneOf", "prefixItems"):
        children = node.get(keyword)
        if type(children) is list:
            for index, child in enumerate(children):
                if type(child) is dict:
                    yield (keyword, index), child, True
    condition = node.get("if")
    if type(condition) is dict:
        yield ("if",), condition, False


def _draft_assertion_inventory(root: dict[str, Any]) -> set[tuple[str, str]]:
    """Independently inventory directly-asserting Draft 2020-12 keywords."""
    found: set[tuple[str, str]] = set()

    def visit(
        node: Mapping[str, Any],
        path: tuple[str | int, ...],
        asserting: bool,
        active_locations: frozenset[tuple[str | int, ...]],
    ) -> None:
        if "$ref" in node:
            target = _ref_parts(node["$ref"])
            if target in active_locations:
                raise AssertionError("cyclic_local_ref")
            target_node = _at(root, target)
            assert type(target_node) is dict
            visit(target_node, target, asserting, active_locations | {target})
        if asserting:
            for keyword in node:
                if keyword in _DRAFT_ASSERTION_KEYWORDS or (
                    keyword == "additionalProperties" and node[keyword] is False
                ):
                    found.add((_schema_pointer(path + (keyword,)), keyword))
        for suffix, child, child_asserting in _draft_child_schemas(node):
            visit(
                child,
                path + suffix,
                asserting and child_asserting,
                active_locations | {path},
            )

    visit(root, (), True, frozenset({()}))
    return found


def _draft_unsupported_keywords(root: dict[str, Any]) -> set[tuple[str, str]]:
    """Return registry-recognized applicators lacking a mutation traversal."""
    unsupported: set[tuple[str, str]] = set()

    def visit(
        node: Mapping[str, Any],
        path: tuple[str | int, ...],
        active_locations: frozenset[tuple[str | int, ...]],
    ) -> None:
        if "$ref" in node:
            target = _ref_parts(node["$ref"])
            if target in active_locations:
                raise AssertionError("cyclic_local_ref")
            target_node = _at(root, target)
            assert type(target_node) is dict
            visit(target_node, target, active_locations | {target})
        for keyword in node:
            if (
                keyword in _DRAFT_APPLICATOR_KEYWORDS
                and keyword not in _SUPPORTED_APPLICATOR_KEYWORDS
                and not (keyword == "additionalProperties" and node[keyword] is False)
            ):
                unsupported.add((_schema_pointer(path + (keyword,)), keyword))
        for suffix, child, _ in _draft_child_schemas(node):
            visit(child, path + suffix, active_locations | {path})

    visit(root, (), frozenset({()}))
    return unsupported


def _assert_draft_coverage(
    artifact: str,
    root: dict[str, Any],
    coverage: set[tuple[str, str, str]],
) -> None:
    missing = {
        (pointer, keyword)
        for pointer, keyword in _draft_assertion_inventory(root)
        if (artifact, pointer, keyword) not in coverage
    } | _draft_unsupported_keywords(root)
    if missing:
        pointer, keyword = min(missing)
        raise AssertionError(f"uncovered_draft_keyword:{pointer}:{keyword}")


def _constraints(
    root: dict[str, Any], node: dict[str, Any], pointer: tuple[str | int, ...] = ()
) -> set[tuple[str, str]]:
    """Compatibility shim for the independent Draft inventory."""
    assert node is root and pointer == ()
    return _draft_assertion_inventory(root)


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


def _valid_schema_value(root: dict[str, Any], node: dict[str, Any]) -> Any | None:
    """Return a small standalone value accepted by a property schema, if known."""
    node, _ = _resolve(root, node, ())
    validator = Draft202012Validator(node)
    candidates: list[Any] = []
    if "const" in node:
        candidates.append(node["const"])
    if "enum" in node:
        candidates.extend(node["enum"])
    types = node.get("type")
    type_names = (types,) if type(types) is str else tuple(types or ())
    if not type_names:
        candidates.extend(("task15-added", 0, True, [], {}, None))
    if "string" in type_names:
        candidates.extend(("", "x", "ok", "task15-added"))
    if "integer" in type_names or "number" in type_names:
        candidates.extend((-1, 0, 1, 2))
    if "boolean" in type_names:
        candidates.extend((False, True))
    if "array" in type_names:
        candidates.append([])
    if "object" in type_names:
        candidates.append({})
    if "null" in type_names:
        candidates.append(None)
    for candidate in candidates:
        if validator.is_valid(candidate):
            return deepcopy(candidate)
    return None


def _add_properties(
    parts: tuple[str | int, ...], replacements: tuple[tuple[str, Any], ...]
) -> Callable[[Any], Any]:
    def apply(value: Any) -> Any:
        copied = deepcopy(value)
        target = _at(copied, parts)
        for field, replacement in replacements:
            target[field] = deepcopy(replacement)
        return copied

    return apply


def _walk_seed(
    artifact: str,
    seed: SeedVariant,
    root: dict[str, Any],
    node: dict[str, Any],
    value: Any,
    schema_path: tuple[str | int, ...] = (),
    instance_path: tuple[str | int, ...] = (),
    active_locations: frozenset[tuple[str | int, ...]] = frozenset(),
) -> tuple[list[Mutation], list[Exclusion]]:
    node, schema_path = _resolve(root, node, schema_path, active_locations)
    active_locations = active_locations | {schema_path}
    mutations: list[Mutation] = []
    exclusions: list[Exclusion] = []

    def add(
        keyword: str,
        description: str,
        changed: tuple[str | int, ...],
        apply: Callable[[Any], Any],
        *,
        changed_paths: tuple[tuple[str | int, ...], ...] | None = None,
    ) -> None:
        def declared_path(path: tuple[str | int, ...]) -> tuple[str | int, ...]:
            for index in range(len(path)):
                if type(_at(seed.value, path[:index])) is list:
                    return path[:index]
            return path

        paths = (changed,) if changed_paths is None else changed_paths
        mutations.append(
            Mutation(
                artifact,
                seed.name,
                _schema_pointer(schema_path + (keyword,)),
                _json_pointer(instance_path),
                keyword,
                description,
                frozenset(_json_pointer(path) for path in map(declared_path, paths)),
                (
                    (
                        _schema_pointer(schema_path + (keyword,)),
                        keyword,
                        _json_pointer(instance_path),
                    ),
                ),
                frozenset(((_schema_pointer(schema_path + (keyword,)), keyword),)),
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
        if "minProperties" in node:
            target_size = node["minProperties"] - 1
            removable = [
                field for field in value if field not in node.get("required", [])
            ]
            if target_size < 0 or len(value) - len(removable) > target_size:
                exclude(
                    "minProperties",
                    "Required properties prevent reducing the object below minProperties.",
                )
            else:
                removed = removable[: len(value) - target_size]

                def remove_optional(
                    value: Any, fields: tuple[str, ...] = tuple(removed)
                ) -> Any:
                    copied = deepcopy(value)
                    target = _at(copied, instance_path)
                    for field in fields:
                        del target[field]
                    return copied

                add(
                    "minProperties",
                    "below object lower bound while retaining required properties",
                    instance_path + (removed[0],),
                    remove_optional,
                )
        if "maxProperties" in node:
            property_overflow = deepcopy(value)
            additions: list[tuple[str, Any]] = []
            for field, child in node.get("properties", {}).items():
                if field not in property_overflow:
                    candidate = _valid_schema_value(root, child)
                    if candidate is not None:
                        additions.append((field, candidate))
                        property_overflow[field] = candidate
                if len(property_overflow) > node["maxProperties"]:
                    break
            additional = node.get("additionalProperties", True)
            if (
                len(property_overflow) <= node["maxProperties"]
                and additional is not False
            ):
                candidate = (
                    _valid_schema_value(root, additional)
                    if type(additional) is dict
                    else None
                )
                if additional is True:
                    candidate = None
                if candidate is not None or additional is True:
                    field = "task15_added_property"
                    while field in property_overflow:
                        field += "_"
                    additions.append((field, candidate))
                    property_overflow[field] = candidate
            if len(property_overflow) <= node["maxProperties"]:
                closed = node.get("additionalProperties") is False
                exclude(
                    "maxProperties",
                    (
                        "Closed object has no absent named property that can overflow "
                        "maxProperties."
                        if closed
                        else "No schema-valid absent property was constructible for "
                        "maxProperties."
                    ),
                )
            else:
                addition_paths = tuple(
                    instance_path + (field,) for field, _ in additions
                )
                add(
                    "maxProperties",
                    "above object upper bound with schema-valid named properties",
                    addition_paths[0],
                    _add_properties(instance_path, tuple(additions)),
                    changed_paths=addition_paths,
                )
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
                    active_locations,
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
                    active_locations,
                )
                mutations.extend(more)
                exclusions.extend(omitted)

    if "if" in node:
        branch = "then" if Draft202012Validator(node["if"]).is_valid(value) else "else"
        if type(node.get(branch)) is dict:
            more, omitted = _walk_seed(
                artifact,
                seed,
                root,
                node[branch],
                value,
                schema_path + (branch,),
                instance_path,
                active_locations,
            )
            mutations.extend(more)
            exclusions.extend(omitted)
    for index, member in enumerate(node.get("allOf", [])):
        more, omitted = _walk_seed(
            artifact,
            seed,
            root,
            member,
            value,
            schema_path + ("allOf", index),
            instance_path,
            active_locations,
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
            active_locations,
        )
        # One `oneOf` removal is conceptual evidence, not three falsely-isolated
        # branch claims.  It must declare every terminal branch context exactly.
        branch_prefix = _schema_pointer(schema_path + ("oneOf", branch_index))
        mutations.extend(
            item
            for item in more
            if not (
                item.keyword == "required"
                and item.schema_pointer.startswith(branch_prefix)
            )
        )
        exclusions.extend(omitted)
        required = matching[0].get("required")
        assert (
            type(required) is list and len(required) == 1 and type(required[0]) is str
        )
        field = required[0]
        expected_errors = tuple(
            (
                _schema_pointer(schema_path + ("oneOf", index, "required")),
                "required",
                _json_pointer(instance_path),
            )
            for index, member in enumerate(node["oneOf"])
            if type(member.get("required")) is list and len(member["required"]) == 1
        )
        assert len(expected_errors) == len(node["oneOf"]), (schema_path, node["oneOf"])
        mutations.append(
            Mutation(
                artifact=artifact,
                seed=seed.name,
                schema_pointer=_schema_pointer(schema_path + ("oneOf",)),
                instance_pointer=_json_pointer(instance_path),
                keyword="oneOf",
                description="remove the selected oneOf discriminator",
                changed_pointers=frozenset((_json_pointer(instance_path + (field,)),)),
                expected_errors=expected_errors,
                covered_constraints=frozenset(
                    (location, keyword) for location, keyword, _ in expected_errors
                ),
                apply=_delete(instance_path + (field,)),
            )
        )
    return mutations, exclusions


def _candidate_isolated(case: Mutation, seed: SeedVariant) -> bool:
    return _normalized_leaf_counter(
        case.artifact, case.apply(deepcopy(seed.value))
    ) == Counter(case.expected_errors)


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
        (item.artifact, pointer, keyword)
        for item in MUTATIONS
        for pointer, keyword in item.covered_constraints
    } | {(item.artifact, item.schema_pointer, item.keyword) for item in EXCLUSIONS}


def _assert_isolated_rejection(mutation: Mutation, before: Any, invalid: Any) -> None:
    found = _normalized_leaf_counter(mutation.artifact, invalid)
    expected = Counter(mutation.expected_errors)
    assert found == expected, (mutation, found, expected)
    assert not _runtime_accepts(mutation.artifact, invalid), mutation


def test_reviewer_payload_oneof_probe_declares_all_branch_contexts() -> None:
    """Removing payload makes every alternative fail; this is one conceptual proof."""
    mutation = next(
        item
        for item in MUTATIONS
        if item.artifact == "trace_event"
        and item.seed == "payload-maximal"
        and item.schema_pointer == "#/oneOf"
    )
    seed = next(
        item
        for item in SEED_VARIANTS
        if item.artifact == mutation.artifact and item.name == mutation.seed
    )
    assert _normalized_leaf_counter(
        mutation.artifact, mutation.apply(seed.value)
    ) == Counter(
        {
            ("#/oneOf/0/required", "required", ""): 1,
            ("#/oneOf/1/required", "required", ""): 1,
            ("#/oneOf/2/required", "required", ""): 1,
        }
    )


def test_reviewer_run_policy_ref_aliases_normalize_every_recovery_ladder_use() -> None:
    aliases = _ref_aliases("run_policy")
    recovery = ("properties", "recovery_ladder", "properties")
    expected_targets = {
        "nudge": ("$defs", "recovery_stage_permission"),
        "diagnose": ("$defs", "recovery_stage_permission"),
        "reheat": ("$defs", "recovery_stage_permission"),
        "restart": ("$defs", "restart_stage_permission"),
        "escalate": ("$defs", "escalation_stage_permission"),
        "stop": ("$defs", "stop_stage_permission"),
    }
    assert {
        recovery + (stage,): aliases[recovery + (stage,)] for stage in expected_targets
    } == {recovery + (stage,): target for stage, target in expected_targets.items()}
    normalized = [
        _normalized_leaf_counter(item.artifact, item.apply(deepcopy(seed.value)))
        for item in MUTATIONS
        if item.artifact == "run_policy"
        and item.seed == "maximal"
        and item.instance_pointer.startswith("/recovery_ladder/")
        for seed in SEED_VARIANTS
        if seed.artifact == item.artifact and seed.name == item.seed
    ]
    assert len(normalized) == 30
    assert all(
        next(iter(errors))[0].startswith("#/$defs/") and len(errors) == 1
        for errors in normalized
    )


def test_all_declared_mutations_have_zero_normalized_expectation_mismatches() -> None:
    mismatches = []
    for mutation in MUTATIONS:
        seed = next(
            item
            for item in SEED_VARIANTS
            if item.artifact == mutation.artifact and item.name == mutation.seed
        )
        found = _normalized_leaf_counter(mutation.artifact, mutation.apply(seed.value))
        expected = Counter(mutation.expected_errors)
        if found != expected:
            mismatches.append((mutation.identity, found, expected))
    assert mismatches == []


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
    assert len(MUTATIONS) == 1420
    assert len(EXCLUSIONS) == 80
    assert len(SCHEMA_CONSTRAINTS) == 637
    coverage = _coverage_keys()
    for artifact in _ARTIFACTS:
        _assert_draft_coverage(artifact, _schema_data(artifact), coverage)
    assert coverage == SCHEMA_CONSTRAINTS
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


def test_draft_inventory_fails_closed_for_uncovered_min_properties() -> None:
    schema = {"type": "object", "minProperties": 1}
    inventory = _draft_assertion_inventory(schema)
    assert ("#/minProperties", "minProperties") in inventory
    with pytest.raises(
        AssertionError,
        match=r"uncovered_draft_keyword:#/minProperties:minProperties",
    ):
        _assert_draft_coverage("synthetic", schema, set())
    coverage_without_min = {
        ("synthetic", pointer, keyword)
        for pointer, keyword in inventory
        if keyword != "minProperties"
    }
    with pytest.raises(
        AssertionError,
        match=r"uncovered_draft_keyword:#/minProperties:minProperties",
    ):
        _assert_draft_coverage("synthetic", schema, coverage_without_min)


def test_draft_keyword_model_preserves_official_keywords_and_ignores_annotations() -> (
    None
):
    assert (
        _DRAFT_ASSERTION_KEYWORDS
        | (_DRAFT_APPLICATOR_KEYWORDS & _DRAFT_VALIDATOR_KEYWORDS)
        == _DRAFT_VALIDATOR_KEYWORDS - _DRAFT_METADATA_ANNOTATIONS
    )
    assert {
        "minProperties",
        "maxProperties",
        "dependentRequired",
    } <= _DRAFT_ASSERTION_KEYWORDS
    assert {"$ref", "contains", "oneOf"} <= _DRAFT_APPLICATOR_KEYWORDS
    schema = {
        "type": "string",
        "description": "Draft annotation",
        "format": "email",
        "x-example-extension": True,
    }
    assert _draft_assertion_inventory(schema) == {("#/type", "type")}
    _assert_draft_coverage("synthetic", schema, {("synthetic", "#/type", "type")})


def test_min_and_max_properties_build_isolated_coverage_or_exclusion() -> None:
    min_schema = {
        "type": "object",
        "required": ["required"],
        "properties": {
            "required": {"type": "string"},
            "optional": {"type": "string"},
        },
        "additionalProperties": False,
        "minProperties": 2,
    }
    third_schema: dict[str, Any] = {"enum": ["third"]}
    max_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "first": {"type": "string"},
            "second": {"type": "integer", "minimum": 1},
            "third": third_schema,
        },
        "additionalProperties": False,
        "maxProperties": 2,
    }
    cases: list[Mutation] = []
    exclusions: list[Exclusion] = []
    for name, schema, value, keyword in (
        ("min", min_schema, {"required": "ok", "optional": "ok"}, "minProperties"),
        ("max", max_schema, {"first": "ok", "second": 2}, "maxProperties"),
    ):
        seed = SeedVariant("synthetic", name, value)
        mutations, omitted = _walk_seed("synthetic", seed, schema, schema, value)
        cases.extend(item for item in mutations if item.keyword == keyword)
        exclusions.extend(item for item in omitted if item.keyword == keyword)
    assert not exclusions
    assert {item.keyword for item in cases} == {"minProperties", "maxProperties"}
    for case in cases:
        seed = next(
            item
            for item in (
                SeedVariant("synthetic", "min", {"required": "ok", "optional": "ok"}),
                SeedVariant("synthetic", "max", {"first": "ok", "second": 2}),
            )
            if item.name == case.seed
        )
        invalid = case.apply(seed.value)
        schema = min_schema if case.keyword == "minProperties" else max_schema
        found = Counter(
            (
                _schema_pointer(tuple(error.absolute_schema_path)),
                error.validator,
                _json_pointer(tuple(error.absolute_path)),
            )
            for error in Draft202012Validator(schema).iter_errors(invalid)
        )
        assert found == Counter(case.expected_errors)
        assert _recursive_diff(seed.value, invalid) == set(case.changed_pointers)
    max_case = next(item for item in cases if item.keyword == "maxProperties")
    max_invalid = max_case.apply({"first": "ok", "second": 2})
    assert max_invalid["third"] == "third"
    assert Draft202012Validator(third_schema).is_valid(max_invalid["third"])
    assert max_case.changed_pointers == frozenset({"/third"})
    assert Counter(
        error.validator
        for error in Draft202012Validator(max_schema).iter_errors(max_invalid)
    ) == Counter({"maxProperties": 1})
    closed_schema = {
        "type": "object",
        "properties": {"fixed": {"const": "fixed"}},
        "additionalProperties": False,
        "maxProperties": 1,
    }
    _, closed_exclusions = _walk_seed(
        "synthetic",
        SeedVariant("synthetic", "closed", {"fixed": "fixed"}),
        closed_schema,
        closed_schema,
        {"fixed": "fixed"},
    )
    assert [
        item.reason for item in closed_exclusions if item.keyword == "maxProperties"
    ] == ["Closed object has no absent named property that can overflow maxProperties."]
    coverage = (
        {
            ("synthetic", pointer, keyword)
            for schema in (min_schema, max_schema)
            for pointer, keyword in _draft_assertion_inventory(schema)
            if keyword not in {"minProperties", "maxProperties"}
        }
        | {
            ("synthetic", pointer, keyword)
            for case in cases
            for pointer, keyword in case.covered_constraints
        }
        | {(item.artifact, item.schema_pointer, item.keyword) for item in exclusions}
    )
    _assert_draft_coverage("synthetic", min_schema, coverage)
    _assert_draft_coverage("synthetic", max_schema, coverage)


def test_draft_inventory_rejects_recognized_unsupported_assertion() -> None:
    schema = {
        "type": "object",
        "dependentRequired": {"trigger": ["dependent"]},
    }
    with pytest.raises(
        AssertionError,
        match=r"uncovered_draft_keyword:#/dependentRequired:dependentRequired",
    ):
        _assert_draft_coverage("synthetic", schema, set())


def test_draft_inventory_walks_escaped_transitive_refs_conditionals_and_oneof() -> None:
    schema = {
        "$defs": {
            "escaped/a~b": {"$ref": "#/$defs/final"},
            "final": {
                "allOf": [
                    {
                        "if": {"properties": {"kind": {"const": "selected"}}},
                        "then": {"minProperties": 1},
                    }
                ]
            },
        },
        "properties": {
            "payload": {
                "oneOf": [
                    {"$ref": "#/$defs/escaped~1a~0b"},
                    {"dependentRequired": {"trigger": ["dependent"]}},
                ]
            }
        },
    }
    assert _draft_assertion_inventory(schema) == {
        ("#/$defs/final/allOf/0/then/minProperties", "minProperties"),
        ("#/properties/payload/oneOf/1/dependentRequired", "dependentRequired"),
    }


def test_draft_inventory_and_mutation_walk_fail_promptly_on_cyclic_local_ref() -> None:
    schema = {"$defs": {"loop": {"$ref": "#/$defs/loop"}}, "$ref": "#/$defs/loop"}
    with pytest.raises(AssertionError, match="^cyclic_local_ref$"):
        _draft_assertion_inventory(schema)
    with pytest.raises(AssertionError, match="^cyclic_local_ref$"):
        _walk_seed(
            "synthetic",
            SeedVariant("synthetic", "loop", {}),
            schema,
            schema,
            {},
        )


def test_shared_local_ref_reuse_is_not_a_cycle() -> None:
    schema = {
        "$defs": {"leaf": {"minProperties": 1}},
        "type": "object",
        "properties": {
            "left": {"$ref": "#/$defs/leaf"},
            "right": {"$ref": "#/$defs/leaf"},
        },
    }
    assert _draft_assertion_inventory(schema) == {
        ("#/type", "type"),
        ("#/$defs/leaf/minProperties", "minProperties"),
    }
    mutations, exclusions = _walk_seed(
        "synthetic",
        SeedVariant("synthetic", "shared", {"left": {"a": 1}, "right": {"b": 2}}),
        schema,
        schema,
        {"left": {"a": 1}, "right": {"b": 2}},
    )
    assert len([item for item in mutations if item.keyword == "minProperties"]) == 2
    assert not [item for item in exclusions if item.keyword == "minProperties"]
