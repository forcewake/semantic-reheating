"""Safe, typed command-line interface for semantic reheating."""

from __future__ import annotations

import argparse
import io
import json
import os
import stat
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from itertools import pairwise
from pathlib import Path
from types import ModuleType
from typing import Any, Never

from . import ControllerError, DecisionEnvelope, RunPolicy, TraceEvent, analyze
from .canonical import canonicalize_json
from .models import ModelValidationError
from .validation import ContractValidationError, load_public_json

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
_MAX_TRUSTED_SOURCE_BYTES = 1_048_576

# These are reviewed source contracts, not discovery metadata.  Update them only
# with the corresponding trusted source change.
_TRUSTED_BENCHMARK_SOURCE_SHA256 = {
    "__init__.py": "19d094b3835620f7899a84a0ceedae7fb64ed3b44f47a5d3cb8c0976a5136ca3",
    "metrics.py": f"{'6d68f4a79e84cbddd323272'}{'965397a529fe27b5a60bd38c24d796f6c71ee2945'}",
    "replay.py": "eab8682b86c70f1fb713a8b3ecdd24c61947d3e1050a9910b133335b6c46562b",
}
_TRUSTED_BENCHMARK_PACKAGE = "_semantic_reheating_benchmark"

_ERROR_NAMES = {
    EXIT_USAGE: "usage",
    EXIT_INVALID_SCHEMA: "invalid_schema",
    EXIT_SEQUENCE_GAP: "sequence_gap",
    EXIT_INCOMPATIBLE_VERSION: "incompatible_version",
    EXIT_UNSAFE_POLICY: "unsafe_policy",
    EXIT_REQUIRED_DETECTOR_UNAVAILABLE: "required_detector_unavailable",
    EXIT_BENCHMARK_UNAVAILABLE: "benchmark_unavailable",
    EXIT_IO: "io_error",
    EXIT_INTERNAL: "internal_error",
}


class _UsageFailure(Exception):
    """A parse failure whose diagnostic must not reflect untrusted argv."""


class _HelpRequested(Exception):
    """Normal argparse help completion without letting SystemExit escape main."""


@dataclass(frozen=True, slots=True)
class _TrustedSourceCapabilities:
    """Descriptor primitives captured before trusted source discovery."""

    nonblock: int
    nofollow: int
    directory: int


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        del message
        raise _UsageFailure

    def exit(self, status: int = 0, message: str | None = None) -> Never:
        del message
        if status == EXIT_OK:
            raise _HelpRequested
        raise _UsageFailure


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(
        prog="reheat", description="Safe semantic reheating analysis."
    )
    commands = parser.add_subparsers(
        dest="command", required=True, parser_class=_SafeArgumentParser
    )
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
        "benchmark", help="replay the committed synthetic benchmark corpus"
    )
    benchmark.add_argument("corpus")
    benchmark.add_argument("--manifest", required=True)
    benchmark.add_argument("--format", choices=("json",), default="json")
    return parser


def _error(exit_code: int) -> int:
    print(f"error: {_ERROR_NAMES[exit_code]}", file=sys.stderr)
    return exit_code


def _read_bytes(path: str, limit: int) -> bytes:
    """Read a bounded regular file through one verified descriptor.

    On platforms without ``O_NOFOLLOW`` the lstat/open/fstat sequence is
    best-effort: the opened descriptor is still verified, but a path can race
    between the lstat and open calls. Platforms with ``O_NOFOLLOW`` reject that
    swap at open time as well.
    """
    descriptor = -1
    try:
        preopen = os.lstat(path)
        if not stat.S_ISREG(preopen.st_mode):
            raise _CliFailure(EXIT_IO)
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise _CliFailure(EXIT_IO)
        if metadata.st_size > limit:
            raise _CliFailure(EXIT_INVALID_SCHEMA)
        data = bytearray()
        while len(data) <= limit:
            chunk = os.read(descriptor, min(65_536, limit + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > limit:
                raise _CliFailure(EXIT_INVALID_SCHEMA)
    except _CliFailure:
        raise
    except OSError as error:
        raise _CliFailure(EXIT_IO) from error
    finally:
        if descriptor != -1:
            os.close(descriptor)
    if len(data) > limit:
        raise _CliFailure(EXIT_INVALID_SCHEMA)
    return bytes(data)


def _json_object_bytes(data: bytes) -> dict[str, Any]:
    try:
        value = load_public_json(data)
    except ContractValidationError as error:
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
    if parsed[0].sequence != 1:
        raise _CliFailure(EXIT_SEQUENCE_GAP)
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


def _write_canonical_stdout(value: dict[str, Any]) -> None:
    """Write one complete RFC 8785 record without text-stream transcoding."""
    payload = canonicalize_json(value) + b"\n"
    stdout = sys.stdout
    buffer = getattr(stdout, "buffer", None)
    if buffer is None:
        if not isinstance(stdout, io.StringIO):
            raise OSError("stdout does not provide a byte buffer")
        stdout.write(payload.decode("utf-8"))
        stdout.flush()
        return
    remaining = memoryview(payload)
    while remaining:
        written = buffer.write(remaining)
        if type(written) is not int or not 0 < written <= len(remaining):
            raise OSError("stdout write failed")
        remaining = remaining[written:]
    buffer.flush()


def _write_bytes_stdout(payload: bytes) -> None:
    """Write already-canonical benchmark bytes without reparsing them."""
    stdout = sys.stdout
    buffer = getattr(stdout, "buffer", None)
    if buffer is None:
        if not isinstance(stdout, io.StringIO):
            raise OSError("stdout does not provide a byte buffer")
        stdout.write(payload.decode("utf-8"))
        stdout.flush()
        return
    remaining = memoryview(payload)
    while remaining:
        written = buffer.write(remaining)
        if type(written) is not int or not 0 < written <= len(remaining):
            raise OSError("stdout write failed")
        remaining = remaining[written:]
    buffer.flush()


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
                "summary: "
                + json.dumps(
                    envelope.human_summary, ensure_ascii=True, separators=(",", ":")
                ),
            )
        )
        + "\n"
    )


def _validate(trace_path: str, policy_path: str) -> int:
    trace, _ = _inputs(trace_path, policy_path)
    _write_canonical_stdout(
        {
            "contract_version": "1.0",
            "event_count": len(trace),
            "run_id": trace[0].run_id,
            "status": "valid",
        }
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
        _write_canonical_stdout(envelope.to_dict())
    else:
        print(_text(envelope), end="")
    return EXIT_OK


def _explain(decision_path: str) -> int:
    print(_text(_parse_decision(_load_json(decision_path))), end="")
    return EXIT_OK


def _trusted_source_capabilities() -> _TrustedSourceCapabilities:
    """Fail closed unless the descriptor operations used below are available."""
    try:
        supported = os.open in os.supports_dir_fd and os.stat in os.supports_dir_fd
        caps = _TrustedSourceCapabilities(os.O_NONBLOCK, os.O_NOFOLLOW, os.O_DIRECTORY)
    except (AttributeError, TypeError, ValueError) as error:
        raise _CliFailure(EXIT_BENCHMARK_UNAVAILABLE) from error
    if not supported or any(
        type(flag) is not int or flag <= 0
        for flag in (caps.nonblock, caps.nofollow, caps.directory)
    ):
        raise _CliFailure(EXIT_BENCHMARK_UNAVAILABLE)
    return caps


def _same_node(left: os.stat_result, right: os.stat_result) -> bool:
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def _open_trusted_root(root: Path, caps: _TrustedSourceCapabilities) -> int:
    descriptor = -1
    accepted = False
    try:
        before = root.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
            raise OSError
        descriptor = os.open(
            root, os.O_RDONLY | caps.nonblock | caps.nofollow | caps.directory
        )
        opened = os.fstat(descriptor)
        after = root.lstat()
        if (
            not stat.S_ISDIR(opened.st_mode)
            or not _same_node(before, opened)
            or not stat.S_ISDIR(after.st_mode)
            or not _same_node(before, after)
        ):
            raise OSError
        accepted = True
        return descriptor
    except _CliFailure:
        raise
    except (OSError, ValueError) as error:
        raise _CliFailure(EXIT_BENCHMARK_UNAVAILABLE) from error
    finally:
        if descriptor >= 0 and not accepted:
            os.close(descriptor)


def _open_trusted_dir(
    parent_fd: int, name: str, caps: _TrustedSourceCapabilities
) -> int:
    descriptor = -1
    accepted = False
    try:
        before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
            raise OSError
        descriptor = os.open(
            name,
            os.O_RDONLY | caps.nonblock | caps.nofollow | caps.directory,
            dir_fd=parent_fd,
        )
        opened = os.fstat(descriptor)
        after = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or not _same_node(before, opened)
            or not stat.S_ISDIR(after.st_mode)
            or not _same_node(before, after)
        ):
            raise OSError
        accepted = True
        return descriptor
    except (OSError, ValueError) as error:
        raise _CliFailure(EXIT_BENCHMARK_UNAVAILABLE) from error
    finally:
        if descriptor >= 0 and not accepted:
            os.close(descriptor)


def _capture_trusted_source(
    parent_fd: int, name: str, caps: _TrustedSourceCapabilities
) -> bytes:
    """Capture one audited direct child without ever reopening its pathname."""
    descriptor = -1
    try:
        before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            stat.S_ISLNK(before.st_mode)
            or not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size < 0
            or before.st_size > _MAX_TRUSTED_SOURCE_BYTES
        ):
            raise OSError
        descriptor = os.open(
            name, os.O_RDONLY | caps.nonblock | caps.nofollow, dir_fd=parent_fd
        )
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or not _same_node(before, opened)
            or opened.st_nlink != 1
            or opened.st_size != before.st_size
            or opened.st_mtime_ns != before.st_mtime_ns
        ):
            raise OSError
        data = bytearray()
        while True:
            chunk = os.read(
                descriptor,
                min(65_536, _MAX_TRUSTED_SOURCE_BYTES + 1 - len(data)),
            )
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > _MAX_TRUSTED_SOURCE_BYTES:
                raise OSError
        after = os.fstat(descriptor)
        named_after = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            not stat.S_ISREG(after.st_mode)
            or not _same_node(before, after)
            or after.st_nlink != 1
            or after.st_size != before.st_size
            or after.st_mtime_ns != before.st_mtime_ns
            or not stat.S_ISREG(named_after.st_mode)
            or not _same_node(before, named_after)
            or named_after.st_nlink != 1
            or named_after.st_size != before.st_size
            or named_after.st_mtime_ns != before.st_mtime_ns
            or len(data) != before.st_size
        ):
            raise OSError
        return bytes(data)
    except (OSError, ValueError) as error:
        raise _CliFailure(EXIT_BENCHMARK_UNAVAILABLE) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _trusted_module(name: str, package: str, filename: str) -> ModuleType:
    module = ModuleType(name)
    module.__package__ = package
    module.__file__ = filename
    return module


def _owned_benchmark_module(name: str, private_prefix: str) -> bool:
    return name in ("benchmark", private_prefix) or name.startswith(
        ("benchmark.", private_prefix + ".")
    )


def _trusted_replay() -> tuple[type[Exception], Any]:
    """Execute audited source bytes in a private package, never from a pathname."""
    caps = _trusted_source_capabilities()
    project_root = Path(__file__).absolute().parents[2]
    root_fd = _open_trusted_root(project_root, caps)
    benchmark_fd = -1
    try:
        benchmark_fd = _open_trusted_dir(root_fd, "benchmark", caps)
        sources = {
            name: _capture_trusted_source(benchmark_fd, name, caps)
            for name in _TRUSTED_BENCHMARK_SOURCE_SHA256
        }
    finally:
        if benchmark_fd >= 0:
            os.close(benchmark_fd)
        os.close(root_fd)
    if any(
        sha256(sources[name]).hexdigest() != digest
        for name, digest in _TRUSTED_BENCHMARK_SOURCE_SHA256.items()
    ):
        raise _CliFailure(EXIT_BENCHMARK_UNAVAILABLE)
    try:
        compiled = {
            name: compile(
                source,
                str(project_root / "benchmark" / name),
                "exec",
                dont_inherit=True,
            )
            for name, source in sources.items()
        }
    except (MemoryError, KeyboardInterrupt):
        raise
    except BaseException as error:
        raise _CliFailure(EXIT_BENCHMARK_UNAVAILABLE) from error

    private_prefix = _TRUSTED_BENCHMARK_PACKAGE
    private_names = (
        private_prefix,
        f"{private_prefix}.metrics",
        f"{private_prefix}.replay",
    )
    saved = {
        name: module
        for name, module in tuple(sys.modules.items())
        if _owned_benchmark_module(name, private_prefix)
    }
    for name in saved:
        del sys.modules[name]
    try:
        package = _trusted_module(
            private_prefix,
            private_prefix,
            str(project_root / "benchmark" / "__init__.py"),
        )
        package.__path__ = []  # type: ignore[attr-defined]
        metrics = _trusted_module(
            f"{private_prefix}.metrics",
            private_prefix,
            str(project_root / "benchmark" / "metrics.py"),
        )
        replay = _trusted_module(
            f"{private_prefix}.replay",
            private_prefix,
            str(project_root / "benchmark" / "replay.py"),
        )
        for name, module in zip(private_names, (package, metrics, replay), strict=True):
            sys.modules[name] = module
        exec(compiled["metrics.py"], metrics.__dict__)  # noqa: S102 - verified bytes.
        exec(compiled["replay.py"], replay.__dict__)  # noqa: S102 - verified bytes.
        exec(compiled["__init__.py"], package.__dict__)  # noqa: S102 - verified bytes.
        benchmark_error = replay.BenchmarkError
        replay_bytes = replay.replay_bytes
        if (
            not isinstance(benchmark_error, type)
            or not issubclass(benchmark_error, Exception)
            or not callable(replay_bytes)
        ):
            raise TypeError
        return benchmark_error, replay_bytes
    except (MemoryError, KeyboardInterrupt):
        raise
    except BaseException as error:
        raise _CliFailure(EXIT_BENCHMARK_UNAVAILABLE) from error
    finally:
        for name in tuple(sys.modules):
            if _owned_benchmark_module(name, private_prefix):
                del sys.modules[name]
        sys.modules.update(saved)


def _benchmark(corpus: str, manifest: str, output_format: str) -> int:
    """Run the offline repo-local benchmark only when the command is requested."""
    if output_format != "json":
        raise _CliFailure(EXIT_USAGE)
    benchmark_error, replay_bytes = _trusted_replay()
    try:
        payload = replay_bytes(Path(corpus), Path(manifest))
    except benchmark_error as error:
        code = getattr(error, "code", None)
        if code == "invalid_schema":
            raise _CliFailure(EXIT_INVALID_SCHEMA) from error
        if code == "io":
            raise _CliFailure(EXIT_IO) from error
        raise _CliFailure(EXIT_INTERNAL) from error
    except (OSError, ValueError) as error:
        raise _CliFailure(EXIT_INTERNAL) from error
    _write_bytes_stdout(payload)
    return EXIT_OK


class _CliFailure(Exception):
    def __init__(self, exit_code: int) -> None:
        self.exit_code = exit_code


def _silence_broken_pipe() -> None:
    """Prevent interpreter teardown from retrying a failed stdout flush."""
    try:
        sys.stdout.close()
    except (AttributeError, OSError):
        pass


def main(argv: Sequence[str] | None = None) -> int:
    """Run a bounded, side-effect-free CLI and return its process status."""
    try:
        arguments = _parser().parse_args(argv)
    except _HelpRequested:
        return EXIT_OK
    except _UsageFailure:
        return _error(EXIT_USAGE)
    except SystemExit as error:
        return error.code if type(error.code) is int else EXIT_USAGE
    try:
        if arguments.command == "benchmark":
            return _benchmark(arguments.corpus, arguments.manifest, arguments.format)
        if arguments.command == "validate":
            return _validate(arguments.trace, arguments.policy)
        if arguments.command == "analyze":
            return _analyze(arguments.trace, arguments.policy, arguments.format)
        return _explain(arguments.decision)
    except BrokenPipeError:
        _silence_broken_pipe()
        return EXIT_OK
    except _CliFailure as error:
        return _error(error.exit_code)
    except (MemoryError, KeyboardInterrupt):
        raise
    except Exception:  # noqa: BLE001 - final CLI boundary must never expose internals.
        return _error(EXIT_INTERNAL)
