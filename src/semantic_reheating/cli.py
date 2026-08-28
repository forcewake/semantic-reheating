"""Safe, typed command-line interface for semantic reheating."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from itertools import pairwise
from pathlib import Path
from typing import Any

from . import ControllerError, DecisionEnvelope, RunPolicy, TraceEvent, analyze
from .canonical import canonicalize_json
from .models import ModelValidationError

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_INVALID_SCHEMA = 3
EXIT_SEQUENCE_GAP = 4
EXIT_INCOMPATIBLE_VERSION = 5
EXIT_UNSAFE_POLICY = 6
EXIT_REQUIRED_DETECTOR_UNAVAILABLE = 7
EXIT_BENCHMARK_UNAVAILABLE = 8
EXIT_IO = 9
EXIT_INTERNAL = 10

MAX_EVENTS = 10_000
MAX_JSON_FILE_BYTES = 1_048_576
MAX_JSONL_LINE_BYTES = 262_144
MAX_JSONL_TOTAL_BYTES = 4_194_304

_ERROR_NAMES = {
    EXIT_INVALID_SCHEMA: "invalid_schema",
    EXIT_SEQUENCE_GAP: "sequence_gap",
    EXIT_INCOMPATIBLE_VERSION: "incompatible_version",
    EXIT_UNSAFE_POLICY: "unsafe_policy",
    EXIT_REQUIRED_DETECTOR_UNAVAILABLE: "required_detector_unavailable",
    EXIT_IO: "io_error",
    EXIT_INTERNAL: "internal_error",
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reheat", description="Safe semantic reheating analysis."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="validate a trace and policy")
    validate.add_argument("trace")
    validate.add_argument("--policy", required=True)
    analyze_command = commands.add_parser(
        "analyze", help="analyze a trace with a policy"
    )
    analyze_command.add_argument("trace")
    analyze_command.add_argument("--policy", required=True)
    analyze_command.add_argument("--format", choices=("json", "text"), default="json")
    explain = commands.add_parser("explain", help="render a validated decision")
    explain.add_argument("decision")
    benchmark = commands.add_parser(
        "benchmark", help="benchmark support is unavailable"
    )
    benchmark.add_argument("corpus")
    benchmark.add_argument("--manifest", required=True)
    return parser


def _error(exit_code: int) -> int:
    print(f"error: {_ERROR_NAMES[exit_code]}", file=sys.stderr)
    return exit_code


def _read_bytes(path: str, limit: int) -> bytes:
    try:
        with Path(path).open("rb") as source:
            data = source.read(limit + 1)
    except (FileNotFoundError, PermissionError, IsADirectoryError, OSError) as error:
        raise _CliFailure(EXIT_IO) from error
    if len(data) > limit:
        raise _CliFailure(EXIT_INVALID_SCHEMA)
    return data


def _json_object_bytes(data: bytes) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _CliFailure(EXIT_INVALID_SCHEMA) from error
    if type(value) is not dict:
        raise _CliFailure(EXIT_INVALID_SCHEMA)
    return value


def _load_json(path: str) -> dict[str, Any]:
    return _json_object_bytes(_read_bytes(path, MAX_JSON_FILE_BYTES))


def _load_trace(path: str) -> list[dict[str, Any]]:
    raw = _read_bytes(path, MAX_JSONL_TOTAL_BYTES)
    events: list[dict[str, Any]] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        if len(line) > MAX_JSONL_LINE_BYTES:
            raise _CliFailure(EXIT_INVALID_SCHEMA)
        events.append(_json_object_bytes(line))
        if len(events) > MAX_EVENTS:
            raise _CliFailure(EXIT_INVALID_SCHEMA)
    if not events:
        raise _CliFailure(EXIT_INVALID_SCHEMA)
    return events


def _preflight_version(value: dict[str, Any]) -> None:
    if "contract_version" not in value:
        raise _CliFailure(EXIT_INVALID_SCHEMA)
    if value["contract_version"] != "1.0":
        raise _CliFailure(EXIT_INCOMPATIBLE_VERSION)


def _model_code(error: Exception) -> int:
    if isinstance(error, ModelValidationError):
        if error.code == "unsafe_policy":
            return EXIT_UNSAFE_POLICY
        if error.code == "sequence_gap":
            return EXIT_SEQUENCE_GAP
    return EXIT_INVALID_SCHEMA


def _parse_trace(raw_trace: list[dict[str, Any]]) -> tuple[TraceEvent, ...]:
    parsed: list[TraceEvent] = []
    for value in raw_trace:
        _preflight_version(value)
        try:
            parsed.append(TraceEvent.from_dict(value))
        except ModelValidationError as error:
            raise _CliFailure(_model_code(error)) from error
        except Exception as error:
            raise _CliFailure(EXIT_INVALID_SCHEMA) from error
    if len({event.event_id for event in parsed}) != len(parsed):
        raise _CliFailure(EXIT_INVALID_SCHEMA)
    if any(event.run_id != parsed[0].run_id for event in parsed[1:]):
        raise _CliFailure(EXIT_INVALID_SCHEMA)
    if any(
        current.sequence != previous.sequence + 1
        for previous, current in pairwise(parsed)
    ):
        raise _CliFailure(EXIT_SEQUENCE_GAP)
    return tuple(parsed)


def _parse_policy(raw_policy: dict[str, Any]) -> RunPolicy:
    _preflight_version(raw_policy)
    try:
        return RunPolicy.from_dict(raw_policy)
    except ModelValidationError as error:
        raise _CliFailure(_model_code(error)) from error
    except Exception as error:
        raise _CliFailure(EXIT_INVALID_SCHEMA) from error


def _parse_decision(raw_decision: dict[str, Any]) -> DecisionEnvelope:
    _preflight_version(raw_decision)
    try:
        return DecisionEnvelope.from_dict(raw_decision)
    except ModelValidationError as error:
        raise _CliFailure(_model_code(error)) from error
    except Exception as error:
        raise _CliFailure(EXIT_INVALID_SCHEMA) from error


def _inputs(
    trace_path: str, policy_path: str
) -> tuple[tuple[TraceEvent, ...], RunPolicy]:
    return _parse_trace(_load_trace(trace_path)), _parse_policy(_load_json(policy_path))


def _canonical_line(value: dict[str, Any]) -> str:
    return canonicalize_json(value).decode("utf-8") + "\n"


def _text(envelope: DecisionEnvelope) -> str:
    return (
        "\n".join(
            (
                f"decision: {envelope.decision.value}",
                f"confidence: {envelope.confidence.score}",
                f"requires_host_action: {str(envelope.requires_host_action).lower()}",
                "reason_codes: " + (",".join(envelope.reason_codes) or "none"),
                "evidence_event_ids: "
                + (",".join(envelope.evidence_event_ids) or "none"),
                "summary: " + json.dumps(envelope.human_summary, ensure_ascii=False),
            )
        )
        + "\n"
    )


def _validate(trace_path: str, policy_path: str) -> int:
    trace, _ = _inputs(trace_path, policy_path)
    print(
        _canonical_line(
            {
                "contract_version": "1.0",
                "event_count": len(trace),
                "run_id": trace[0].run_id,
                "status": "valid",
            }
        ),
        end="",
    )
    return EXIT_OK


def _analyze(trace_path: str, policy_path: str, output_format: str) -> int:
    trace, policy = _inputs(trace_path, policy_path)
    try:
        envelope = analyze(trace, policy)
    except ControllerError as error:
        if error.code == "required_detector_unavailable":
            raise _CliFailure(EXIT_REQUIRED_DETECTOR_UNAVAILABLE) from error
        if error.code == "sequence_gap":
            raise _CliFailure(EXIT_SEQUENCE_GAP) from error
        raise _CliFailure(EXIT_INVALID_SCHEMA) from error
    except ModelValidationError as error:
        raise _CliFailure(_model_code(error)) from error
    except Exception as error:
        raise _CliFailure(EXIT_INTERNAL) from error
    if output_format == "json":
        print(_canonical_line(envelope.to_dict()), end="")
    else:
        print(_text(envelope), end="")
    return EXIT_OK


def _explain(decision_path: str) -> int:
    print(_text(_parse_decision(_load_json(decision_path))), end="")
    return EXIT_OK


class _CliFailure(Exception):
    def __init__(self, exit_code: int) -> None:
        self.exit_code = exit_code


def main(argv: Sequence[str] | None = None) -> int:
    """Run a bounded, side-effect-free CLI and return its process status."""
    try:
        arguments = _parser().parse_args(argv)
    except SystemExit as error:
        return error.code if type(error.code) is int else EXIT_USAGE
    try:
        if arguments.command == "benchmark":
            print("error: benchmark_unavailable", file=sys.stderr)
            return EXIT_BENCHMARK_UNAVAILABLE
        if arguments.command == "validate":
            return _validate(arguments.trace, arguments.policy)
        if arguments.command == "analyze":
            return _analyze(arguments.trace, arguments.policy, arguments.format)
        return _explain(arguments.decision)
    except _CliFailure as error:
        return _error(error.exit_code)
    except (MemoryError, KeyboardInterrupt):
        raise
    except Exception:  # noqa: BLE001 - final CLI boundary must never expose internals.
        return _error(EXIT_INTERNAL)
