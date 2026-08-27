"""Strict validation seam for closed public controller artifacts."""

from __future__ import annotations

import json
import math
from importlib import resources
from pathlib import Path
from types import MappingProxyType
from typing import Any, Never

from jsonschema import Draft202012Validator

PUBLIC_CONTRACT_SCHEMAS = MappingProxyType(
    {
        "run_policy": "contracts/v1/run-policy.schema.json",
        "detector_finding": "contracts/v1/detector-finding.schema.json",
        "decision_envelope": "contracts/v1/decision-envelope.schema.json",
        "recovery_instruction": "contracts/v1/recovery-instruction.schema.json",
        "recovery_outcome": "contracts/v1/recovery-outcome.schema.json",
        "evidence_record": "contracts/v1/evidence-record.schema.json",
    }
)

_VALIDATOR_CACHE: dict[str, Draft202012Validator] = {}


class ContractValidationError(ValueError):
    """Typed public-artifact validation failure."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _reject_constant(value: str) -> None:
    raise ContractValidationError(
        "invalid_json_number", "JSON constant is not allowed"
    )


def _parse_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ContractValidationError(
            "invalid_json_number", "JSON number must be finite and not overflow"
        )
    return number


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractValidationError(
                "duplicate_key", "Duplicate JSON object key"
            )
        result[key] = value
    return result


# These limits bound validation work on untrusted public artifacts while allowing
# substantially larger documents than the shipped v1 fixtures. Raw input limits
# protect decoding/parsing; traversal limits protect direct Python JSON values.
MAX_JSON_INPUT_BYTES = 1_048_576
MAX_JSON_INPUT_CHARS = 1_048_576
MAX_JSON_STRING_CHARS = 262_144
MAX_JSON_KEY_CHARS = 4_096
MAX_JSON_AGGREGATE_CHARS = 1_048_576
MAX_JSON_DEPTH = 64
MAX_JSON_NODES = 10_000
# Direct Python integers bypass JSON text parsing. Bound their magnitude before
# schema validation so a validator diagnostic can never format an unbounded int.
MAX_JSON_INTEGER_BITS = 4_096


def _ensure_json_value(value: Any) -> None:
    """Accept only finite plain JSON values within explicit resource bounds."""
    active_containers: set[int] = set()
    stack: list[tuple[Any, int, bool]] = [(value, 0, False)]
    node_count = 0
    aggregate_chars = 0
    while stack:
        current, depth, leaving = stack.pop()
        if leaving:
            active_containers.remove(id(current))
            continue

        node_count += 1
        if node_count > MAX_JSON_NODES:
            raise ContractValidationError(
                "json_node_limit_exceeded", "JSON value exceeds the node limit"
            )
        if depth > MAX_JSON_DEPTH:
            raise ContractValidationError(
                "json_depth_exceeded", "JSON value exceeds the nesting-depth limit"
            )
        if type(current) is str:
            string_chars = len(current)
            if string_chars > MAX_JSON_STRING_CHARS:
                raise ContractValidationError(
                    "json_string_too_large", "JSON string exceeds the character limit"
                )
            aggregate_chars += string_chars
            if aggregate_chars > MAX_JSON_AGGREGATE_CHARS:
                raise ContractValidationError(
                    "json_character_limit_exceeded",
                    "JSON value exceeds the aggregate character limit",
                )
            continue
        if current is None or type(current) is bool:
            continue
        if type(current) is int:
            if abs(current).bit_length() > MAX_JSON_INTEGER_BITS:
                raise ContractValidationError(
                    "json_integer_too_large", "JSON integer exceeds the bit limit"
                )
            continue
        if type(current) is float:
            if math.isfinite(current):
                continue
            raise ContractValidationError(
                "nonfinite_json_number", "JSON number must be finite"
            )
        if type(current) is dict:
            container_id = id(current)
            if container_id in active_containers:
                raise ContractValidationError("json_cycle", "JSON value contains a cycle")
            active_containers.add(container_id)
            stack.append((current, depth, True))
            for key, nested in reversed(current.items()):
                if type(key) is not str:
                    raise ContractValidationError(
                        "non_json_data", "JSON object keys must be strings"
                    )
                key_chars = len(key)
                if key_chars > MAX_JSON_KEY_CHARS:
                    raise ContractValidationError(
                        "json_key_too_large", "JSON object key exceeds the character limit"
                    )
                aggregate_chars += key_chars
                if aggregate_chars > MAX_JSON_AGGREGATE_CHARS:
                    raise ContractValidationError(
                        "json_character_limit_exceeded",
                        "JSON value exceeds the aggregate character limit",
                    )
                stack.append((nested, depth + 1, False))
            continue
        if type(current) is list:
            container_id = id(current)
            if container_id in active_containers:
                raise ContractValidationError("json_cycle", "JSON value contains a cycle")
            active_containers.add(container_id)
            stack.append((current, depth, True))
            for nested in reversed(current):
                stack.append((nested, depth + 1, False))
            continue
        raise ContractValidationError(
            "non_json_data", "Only JSON-compatible values are accepted"
        )


def load_public_json(source: str | bytes | bytearray) -> Any:
    """Load one public JSON text/byte payload without permissive JSON extensions."""
    if type(source) not in (str, bytes, bytearray):
        raise ContractValidationError(
            "non_json_input", "JSON loader accepts text or bytes only"
        )
    if isinstance(source, (bytes, bytearray)):
        if len(source) > MAX_JSON_INPUT_BYTES:
            raise ContractValidationError(
                "json_input_too_large", "JSON input exceeds the byte limit"
            )
        if isinstance(source, bytearray):
            source = bytes(source)
    if isinstance(source, bytes):
        try:
            source = source.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise ContractValidationError(
                "invalid_json_encoding", "JSON bytes must be UTF-8"
            ) from error
    if not isinstance(source, str):
        raise ContractValidationError(
            "non_json_input", "JSON loader accepts text or bytes only"
        )
    if len(source) > MAX_JSON_INPUT_CHARS:
        raise ContractValidationError(
            "json_input_too_large", "JSON input exceeds the character limit"
        )
    try:
        data = json.loads(
            source,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
            parse_float=_parse_float,
        )
    except ContractValidationError:
        raise
    except (json.JSONDecodeError, OverflowError, RecursionError, ValueError) as error:
        raise ContractValidationError("invalid_json", "Malformed JSON input") from error
    _ensure_json_value(data)
    return data


def _ensure_public_artifact_kind(kind: object) -> str:
    """Accept an exact built-in registered kind before mapping operations."""
    if type(kind) is not str or kind not in PUBLIC_CONTRACT_SCHEMAS:
        raise ContractValidationError(
            "unknown_artifact_kind", "Unknown public artifact kind"
        )
    return kind


def _validator_for(kind: str) -> Draft202012Validator:
    kind = _ensure_public_artifact_kind(kind)
    if kind not in _VALIDATOR_CACHE:
        try:
            schema_resource = resources.files("semantic_reheating").joinpath(
                PUBLIC_CONTRACT_SCHEMAS[kind]
            )
            if schema_resource.is_file():
                schema_bytes = schema_resource.read_bytes()
            else:
                source_schema = (
                    Path(__file__).resolve().parents[2]
                    / "contracts"
                    / "v1"
                    / schema_resource.name
                )
                schema_bytes = source_schema.read_bytes()
            schema = load_public_json(schema_bytes)
            Draft202012Validator.check_schema(schema)
        except ContractValidationError:
            raise
        except (OSError, ValueError) as error:
            raise ContractValidationError(
                "invalid_contract_schema", "Public contract schema is unavailable"
            ) from error
        _VALIDATOR_CACHE[kind] = Draft202012Validator(schema)
    return _VALIDATOR_CACHE[kind]


def _check_contract_major(data: Any) -> None:
    if type(data) is not dict:
        return
    version = data.get("contract_version")
    if isinstance(version, str):
        major = version.split(".", 1)[0]
        if major != "1":
            raise ContractValidationError(
                "unknown_contract_major", "Unsupported public contract major"
            )


def validate_public_artifact(kind: str, data: Any) -> Any:
    """Validate real public artifact data against its closed v1 contract."""
    kind = _ensure_public_artifact_kind(kind)
    if type(data) in (str, bytes, bytearray):
        data = load_public_json(data)
    else:
        _ensure_json_value(data)
    _check_contract_major(data)
    validator = _validator_for(kind)
    unexpected_validator_failure = False
    try:
        errors = sorted(
            validator.iter_errors(data),
            key=lambda error: tuple(str(part) for part in error.absolute_path),
        )
        if errors:
            error = errors[0]
            path = "$"
            for segment in error.absolute_path:
                path += f"[{segment}]" if isinstance(segment, int) else f".{segment}"
            raise ContractValidationError(
                "schema_validation_error",
                f"Invalid {kind} at {path}: validator {error.validator}",
            )
    except ContractValidationError:
        raise
    except Exception:  # noqa: BLE001 - jsonschema diagnostics must be sanitized.
        unexpected_validator_failure = True
    if unexpected_validator_failure:
        raise ContractValidationError(
            "schema_validation_error", "Invalid public artifact"
        )
    return data


def _unsafe_policy() -> Never:
    raise ContractValidationError("unsafe_policy", "Unsafe run policy") from None


_BUDGET_DIMENSIONS = ("turns", "tool_calls", "tokens", "elapsed_seconds", "cost")


def _has_named_structural_safety_failure(data: Any) -> bool:
    """Recognize only the closed raw shapes that weaken policy safety limits."""
    if type(data) is not dict:
        return False
    budgets = data.get("budgets")
    if type(budgets) is dict:
        if "whole_run" not in budgets:
            return True
        for scope_name in ("per_intervention", "whole_run"):
            scope = budgets.get(scope_name)
            if type(scope) is dict and any(
                dimension not in scope for dimension in _BUDGET_DIMENSIONS
            ):
                return True
    side_effect_rules = data.get("side_effect_rules")
    if type(side_effect_rules) is dict and (
        side_effect_rules.get("automatic_unconfirmed_non_idempotent_repeat") is True
        or side_effect_rules.get("unknown_treated_as_repeatable") is True
    ):
        return True
    ladder = data.get("recovery_ladder")
    if type(ladder) is dict:
        restart = ladder.get("restart")
        stop = ladder.get("stop")
        escalate = ladder.get("escalate")
        if (
            type(restart) is dict
            and restart.get("requires_host_action") is False
        ) or (type(stop) is dict and stop.get("permitted") is False) or (
            type(escalate) is dict
            and (
                escalate.get("permitted") is False
                or escalate.get("requires_host_action") is False
            )
        ):
            return True
    detectors = data.get("detectors")
    semantic = detectors.get("semantic_detector") if type(detectors) is dict else None
    return type(semantic) is dict and semantic.get("can_relax_hard_stops") is True


def _run_policy_is_safe(policy: dict[str, Any]) -> bool:
    """Recheck policy invariants that must survive typed-state tampering."""
    budgets = policy["budgets"]
    per_intervention = budgets["per_intervention"]
    whole_run = budgets["whole_run"]
    dimensions = ("turns", "tool_calls", "tokens", "elapsed_seconds", "cost")
    if any(whole_run[dimension] < per_intervention[dimension] for dimension in dimensions):
        return False

    signals = policy["agreeing_signals"]
    if (
        set(signals["required_classes"]) != {"repetition", "no_progress"}
        or signals["minimum_count"] != 2
        or signals["budget_can_substitute"] is not False
    ):
        return False

    ladder = policy["recovery_ladder"]
    if (
        ladder["restart"]["requires_host_action"] is not True
        or ladder["escalate"]["requires_host_action"] is not True
        or ladder["escalate"]["permitted"] is not True
        or ladder["stop"]["permitted"] is not True
        or (ladder["reheat"]["permitted"] is True and policy["max_recovery_episodes"] == 0)
    ):
        return False

    rules = policy["side_effect_rules"]
    if (
        rules["automatic_unconfirmed_non_idempotent_repeat"] is not False
        or rules["unknown_treated_as_repeatable"] is not False
        or not set(rules["automatic_repeat_allowed_effect_classes"]).issubset(
            {"read_only", "idempotent_write"}
        )
    ):
        return False

    semantic = policy["detectors"].get("semantic_detector")
    return semantic is None or semantic["can_relax_hard_stops"] is False


def validate_run_policy(data: Any) -> dict[str, Any]:
    """Fail closed for a complete run policy and its semantic safety rules."""
    normalized = data
    normalization_failure_code: str | None = None
    if type(data) in (str, bytes, bytearray):
        try:
            normalized = load_public_json(data)
        except ContractValidationError as error:
            normalization_failure_code = error.code
    if normalization_failure_code is not None:
        raise ContractValidationError(
            normalization_failure_code, "Invalid run policy"
        ) from None

    named_safety_failure = _has_named_structural_safety_failure(normalized)
    failure_code: str | None = None
    try:
        value = validate_public_artifact("run_policy", normalized)
    except ContractValidationError as error:
        failure_code = error.code
        value = None
    if failure_code is not None:
        if named_safety_failure:
            _unsafe_policy()
        raise ContractValidationError(failure_code, "Invalid run policy") from None
    if type(value) is not dict:
        _unsafe_policy()
    try:
        safe = _run_policy_is_safe(value)
    except Exception:  # noqa: BLE001 - schema-valid semantic checks fail closed
        _unsafe_policy()
    if not safe:
        _unsafe_policy()
    return value
