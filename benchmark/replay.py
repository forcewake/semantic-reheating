"""Offline, descriptor-bound deterministic replay of the synthetic corpus."""

from __future__ import annotations

import copy
import os
import re
import stat
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn

from jsonschema import Draft202012Validator

from semantic_reheating import (
    ControllerError,
    DecisionEnvelope,
    RunPolicy,
    TraceEvent,
    analyze,
)
from semantic_reheating.canonical import canonicalize_json
from semantic_reheating.models import ModelValidationError
from semantic_reheating.validation import ContractValidationError, load_public_json

from .metrics import DETECTOR_ORDER, MetricsError, compute_metrics

MAX_MANIFEST_ENTRIES = 100
MAX_TRACE_BYTES = 1_048_576
MAX_CORPUS_BYTES = 33_554_432
MAX_LINE_BYTES = 262_144
MAX_EVENTS = 10_000
MAX_SMALL_BYTES = 1_048_576
_READ_CHUNK = 65_536
_TRACE_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\.jsonl$")
_CORPUS_MANIFEST_SCHEMA_SHA256 = (
    "6e4c9626bf7b6d9adabdf095473c1df3f0930b1416ffdd2706b9d84b3d3e9182"
)
_REPLAY_RESULT_SCHEMA_SHA256 = (
    "792054afc0fbd8b729fb12fe61b502f13f58f4246daf1edd6cd3596e75d1bf30"
)
_DETECTOR_FINDING_IDS = (
    ("exact-repetition", "exact_repetition"),
    ("cycle", "cycle"),
    ("repeated-error", "repeated_error"),
    ("unchanged-state", "unchanged_state"),
    ("acceptance-stall", "acceptance_stall"),
    ("budget-burn", "budget_burn"),
    ("hard-budget", "hard_budget"),
    ("repeated-risky-call", "repeated_risky_call"),
)
_DETECTOR_FINDING_PATTERNS = tuple(
    (re.compile(re.escape(prefix) + r"-[0-9a-f]{64}\Z"), name)
    for prefix, name in _DETECTOR_FINDING_IDS
)


class BenchmarkError(ValueError):
    """Sanitized benchmark failure with an exit-code-facing class."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__("Invalid benchmark input")


@dataclass(frozen=True, slots=True)
class _Capabilities:
    nonblock: int
    nofollow: int
    directory: int
    listdir: bool


@dataclass(frozen=True, slots=True)
class _CapturedTrace:
    entry: dict[str, Any]
    raw: bytes
    events: tuple[TraceEvent, ...]


def _fail(code: str) -> NoReturn:
    raise BenchmarkError(code) from None


def _capabilities() -> _Capabilities:
    try:
        supported = (
            os.open in os.supports_dir_fd
            and os.stat in os.supports_dir_fd
            and os.listdir in os.supports_fd
        )
        caps = _Capabilities(os.O_NONBLOCK, os.O_NOFOLLOW, os.O_DIRECTORY, supported)
    except (AttributeError, TypeError, ValueError):
        _fail("io")
    if not supported or any(
        type(flag) is not int or flag <= 0
        for flag in (caps.nonblock, caps.nofollow, caps.directory)
    ):
        _fail("io")
    return caps


def _same_node(left: os.stat_result, right: os.stat_result) -> bool:
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def _path_stat(path: Path) -> os.stat_result:
    try:
        return path.lstat()
    except OSError:
        _fail("io")


def _open_root(root: Path, caps: _Capabilities) -> int:
    """Accept one lexical root leaf and hold its FD for the complete replay."""
    fd = -1
    accepted = False
    try:
        before = _path_stat(root)
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
            _fail("io")
        fd = os.open(root, os.O_RDONLY | caps.nonblock | caps.nofollow | caps.directory)
        opened = os.fstat(fd)
        after = _path_stat(root)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or not _same_node(before, opened)
            or not stat.S_ISDIR(after.st_mode)
            or not _same_node(before, after)
        ):
            _fail("io")
        accepted = True
        return fd
    except BenchmarkError:
        raise
    except (OSError, ValueError):
        _fail("io")
    finally:
        if fd >= 0 and not accepted:
            os.close(fd)


def _open_dir(parent_fd: int, name: str, caps: _Capabilities) -> int:
    """Open a fixed direct component with pre/open/post identity checks."""
    fd = -1
    accepted = False
    try:
        before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
            _fail("io")
        fd = os.open(
            name,
            os.O_RDONLY | caps.nonblock | caps.nofollow | caps.directory,
            dir_fd=parent_fd,
        )
        opened = os.fstat(fd)
        after = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or not _same_node(before, opened)
            or not stat.S_ISDIR(after.st_mode)
            or not _same_node(before, after)
        ):
            _fail("io")
        accepted = True
        return fd
    except BenchmarkError:
        raise
    except (OSError, ValueError):
        _fail("io")
    finally:
        if fd >= 0 and not accepted:
            os.close(fd)


def _read_regular(parent_fd: int, name: str, caps: _Capabilities, limit: int) -> bytes:
    """Read a regular direct child once through a no-follow descriptor to EOF."""
    fd = -1
    try:
        before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            stat.S_ISLNK(before.st_mode)
            or not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
        ):
            _fail("io")
        if before.st_size < 0 or before.st_size > limit:
            _fail("invalid_schema")
        fd = os.open(
            name, os.O_RDONLY | caps.nonblock | caps.nofollow, dir_fd=parent_fd
        )
        opened = os.fstat(fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or not _same_node(before, opened)
            or opened.st_nlink != 1
        ):
            _fail("io")
        data = bytearray()
        while True:
            chunk = os.read(fd, min(_READ_CHUNK, limit + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > limit:
                _fail("invalid_schema")
        after = os.fstat(fd)
        named_after = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            not stat.S_ISREG(after.st_mode)
            or not _same_node(before, after)
            or after.st_size != before.st_size
            or after.st_mtime_ns != before.st_mtime_ns
            or after.st_nlink != 1
            or not stat.S_ISREG(named_after.st_mode)
            or not _same_node(before, named_after)
            or named_after.st_size != before.st_size
            or named_after.st_mtime_ns != before.st_mtime_ns
            or named_after.st_nlink != 1
            or len(data) != before.st_size
        ):
            _fail("io")
        return bytes(data)
    except BenchmarkError:
        raise
    except (OSError, ValueError):
        _fail("io")
    finally:
        if fd >= 0:
            os.close(fd)


def _bound_root(corpus: Path, manifest: Path) -> Path:
    """Lexically bind CLI paths to the one supported repo layout before opening."""
    if any(part == ".." for part in corpus.parts) or any(
        part == ".." for part in manifest.parts
    ):
        _fail("io")
    corpus = corpus.absolute()
    manifest = manifest.absolute()
    if corpus.name != "corpus" or corpus.parent.name != "benchmark":
        _fail("io")
    root = corpus.parent.parent
    if manifest != root / "benchmark" / "scenarios" / "manifest.json":
        _fail("io")
    return root


def _json_object(raw: bytes) -> dict[str, Any]:
    try:
        value = load_public_json(raw)
    except ContractValidationError:
        _fail("invalid_schema")
    if type(value) is not dict:
        _fail("invalid_schema")
    return value


def _trace_leaf(trace_path: object) -> str:
    if type(trace_path) is not str:
        _fail("invalid_schema")
    relative = PurePosixPath(trace_path)
    name = relative.name
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or relative.parts != ("benchmark", "corpus", name)
        or trace_path != f"benchmark/corpus/{name}"
        or not name.isascii()
        or _TRACE_NAME.fullmatch(name) is None
    ):
        _fail("invalid_schema")
    return name


def _corpus_names(corpus_fd: int) -> frozenset[str]:
    """Enumerate an already-opened corpus directory without pathname traversal."""
    try:
        names = os.listdir(corpus_fd)
    except (OSError, TypeError, ValueError):
        _fail("io")
    if type(names) is not list or any(
        type(name) is not str
        or not name.isascii()
        or _TRACE_NAME.fullmatch(name) is None
        for name in names
    ):
        _fail("io")
    if len(names) != len(set(names)):
        _fail("io")
    return frozenset(names)


def _validator(schema_raw: bytes) -> Draft202012Validator:
    try:
        schema = _json_object(schema_raw)
        Draft202012Validator.check_schema(schema)
        return Draft202012Validator(schema)
    except BenchmarkError:
        raise
    except (ValueError, TypeError):
        _fail("invalid_schema")


def _validate(validator: Draft202012Validator, value: Any) -> None:
    try:
        if next(validator.iter_errors(value), None) is not None:
            _fail("invalid_schema")
    except BenchmarkError:
        raise
    except Exception:  # noqa: BLE001 - jsonschema implementation failures are sanitized.
        _fail("invalid_schema")


def _parse_event_line(line: bytes) -> TraceEvent:
    if not line or len(line) > MAX_LINE_BYTES:
        _fail("invalid_schema")
    try:
        return TraceEvent.from_dict(_json_object(line))
    except (BenchmarkError, ModelValidationError):
        _fail("invalid_schema")


def _validate_events(
    events: list[TraceEvent], scenario_id: str
) -> tuple[TraceEvent, ...]:
    if (
        not events
        or len(events) > MAX_EVENTS
        or any(event.sequence != index for index, event in enumerate(events, 1))
        or len({event.event_id for event in events}) != len(events)
        or {event.run_id for event in events} != {f"run-{scenario_id}"}
    ):
        _fail("invalid_schema")
    return tuple(events)


def _parse_events(raw: bytes, scenario_id: str) -> tuple[TraceEvent, ...]:
    """Test-facing parsing of already captured bounded bytes."""
    if (
        not raw
        or raw.startswith(b"\xef\xbb\xbf")
        or b"\r" in raw
        or not raw.endswith(b"\n")
    ):
        _fail("invalid_schema")
    return _validate_events(
        [_parse_event_line(line) for line in raw[:-1].split(b"\n")], scenario_id
    )


def _read_trace(
    parent_fd: int,
    trace_path: str,
    caps: _Capabilities,
    remaining: int,
    scenario_id: str,
) -> tuple[bytes, tuple[TraceEvent, ...]]:
    name = _trace_leaf(trace_path)
    fd = -1
    try:
        before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            stat.S_ISLNK(before.st_mode)
            or not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
        ):
            _fail("io")
        if before.st_size > MAX_TRACE_BYTES or before.st_size > remaining:
            _fail("invalid_schema")
        fd = os.open(
            name, os.O_RDONLY | caps.nonblock | caps.nofollow, dir_fd=parent_fd
        )
        opened = os.fstat(fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or not _same_node(before, opened)
            or opened.st_nlink != 1
        ):
            _fail("io")
        raw, line, events = bytearray(), bytearray(), []
        while True:
            chunk = os.read(fd, min(_READ_CHUNK, MAX_TRACE_BYTES + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
            if len(raw) > MAX_TRACE_BYTES or len(raw) > remaining:
                _fail("invalid_schema")
            for byte in chunk:
                if byte == 10:
                    events.append(_parse_event_line(bytes(line)))
                    if len(events) > MAX_EVENTS:
                        _fail("invalid_schema")
                    line.clear()
                else:
                    if byte == 13 or len(line) >= MAX_LINE_BYTES:
                        _fail("invalid_schema")
                    line.append(byte)
        after = os.fstat(fd)
        named_after = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            line
            or not raw
            or raw.startswith(b"\xef\xbb\xbf")
            or not stat.S_ISREG(after.st_mode)
            or not _same_node(before, after)
            or after.st_size != before.st_size
            or after.st_mtime_ns != before.st_mtime_ns
            or after.st_nlink != 1
            or not stat.S_ISREG(named_after.st_mode)
            or not _same_node(before, named_after)
            or named_after.st_size != before.st_size
            or named_after.st_mtime_ns != before.st_mtime_ns
            or named_after.st_nlink != 1
            or len(raw) != before.st_size
        ):
            _fail("io")
        return bytes(raw), _validate_events(events, scenario_id)
    except BenchmarkError:
        raise
    except (OSError, ValueError):
        _fail("io")
    finally:
        if fd >= 0:
            os.close(fd)


def _policy(raw: bytes, manifest: dict[str, Any]) -> tuple[RunPolicy, str]:
    try:
        binding = manifest["evaluation_policy"]
        if (
            type(binding) is not dict
            or set(binding)
            != {"binding_version", "source_path", "source_sha256", "overrides"}
            or binding["binding_version"] != "1.0"
            or binding["source_path"]
            != "tests/fixtures/contracts/minimal-run-policy.json"
        ):
            _fail("invalid_schema")
        digest = binding["source_sha256"]
        if (
            type(digest) is not str
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            or sha256(raw).hexdigest() != digest
        ):
            _fail("invalid_schema")
        policy_data = _json_object(raw)
        overrides = binding["overrides"]
        if type(overrides) is not dict or overrides != {
            "detector_windows": {"repetition_events": 64, "no_progress_events": 64}
        }:
            _fail("invalid_schema")
        policy_data = copy.deepcopy(policy_data)
        policy_data["detectors"]["windows"] = copy.deepcopy(
            overrides["detector_windows"]
        )
        return RunPolicy.from_dict(policy_data), digest
    except (BenchmarkError, ModelValidationError):
        raise
    except (KeyError, TypeError, ValueError):
        _fail("invalid_schema")


def _detector_names(record: dict[str, Any]) -> list[str]:
    """Return a closed detector set only from exact, unambiguous finding IDs."""
    if type(record) is not dict:
        _fail("internal")
    confidence = record.get("confidence")
    if type(confidence) is not dict:
        _fail("internal")
    contributions = confidence.get("contributing_findings")
    if type(contributions) is not list:
        _fail("internal")
    found: set[str] = set()
    for contribution in contributions:
        if type(contribution) is not dict:
            _fail("internal")
        finding_id = contribution.get("finding_id")
        if type(finding_id) is not str:
            _fail("internal")
        matches = [
            name
            for pattern, name in _DETECTOR_FINDING_PATTERNS
            if pattern.fullmatch(finding_id) is not None
        ]
        if len(matches) != 1 or matches[0] in found:
            _fail("internal")
        found.add(matches[0])
    return [name for name in DETECTOR_ORDER if name in found]


def _safety(decision: str, label: str) -> str:
    if decision == "continue":
        return "advisory_continue" if label == "pathological" else "safe_continue"
    if decision in {"nudge", "diagnose", "reheat", "restart"}:
        return "recovery"
    return {"escalate": "escalated", "stop": "hard_stop"}[decision]


def _trace_record(
    captured: _CapturedTrace, policy: RunPolicy
) -> tuple[dict[str, Any], bytes]:
    entry = captured.entry
    try:
        record = analyze(captured.events, policy).to_dict()
        DecisionEnvelope.from_dict(record)
        actual_detectors, expected_detectors = (
            _detector_names(record),
            entry["expected_detector_names"],
        )
        actual_evidence, expected_evidence = (
            record["evidence_event_ids"],
            entry["expected_evidence_event_ids"],
        )
        actual_safety = _safety(record["decision"], entry["label"])
        item = {
            "scenario_id": entry["scenario_id"],
            "label": entry["label"],
            "trace_sha256": sha256(captured.raw).hexdigest(),
            "expected_detector_names": expected_detectors,
            "actual_detector_names": actual_detectors,
            "detector_missing_names": [
                name for name in expected_detectors if name not in actual_detectors
            ],
            "detector_unexpected_names": [
                name for name in actual_detectors if name not in expected_detectors
            ],
            "detectors_match": expected_detectors == actual_detectors,
            "expected_decision": entry["expected_decision"],
            "actual_decision": record["decision"],
            "decision_match": entry["expected_decision"] == record["decision"],
            "expected_evidence_event_ids": expected_evidence,
            "actual_evidence_event_ids": actual_evidence,
            "evidence_missing_event_ids": [
                name for name in expected_evidence if name not in actual_evidence
            ],
            "evidence_unexpected_event_ids": [
                name for name in actual_evidence if name not in expected_evidence
            ],
            "evidence_match": expected_evidence == actual_evidence,
            "expected_safety_outcome": entry["expected_safety_outcome"],
            "actual_safety_outcome": actual_safety,
            "safety_match": entry["expected_safety_outcome"] == actual_safety,
            "decision_record": record,
            "decision_sha256": sha256(canonicalize_json(record)).hexdigest(),
        }
        return item, canonicalize_json(record)
    except (ControllerError, ModelValidationError, ContractValidationError):
        _fail("invalid_schema")
    except BenchmarkError:
        raise
    except Exception:  # noqa: BLE001 - controller implementation failures are sanitized.
        _fail("internal")


def _revision(manifest: dict[str, Any], traces: tuple[_CapturedTrace, ...]) -> str:
    payload = bytearray(b"semantic-reheating-corpus-revision-v1\x00")
    value = canonicalize_json(manifest)
    payload.extend(len(value).to_bytes(8, "big"))
    payload.extend(value)
    for captured in traces:
        name = captured.entry["trace_path"].encode("ascii")
        payload.extend(len(name).to_bytes(8, "big"))
        payload.extend(name)
        payload.extend(len(captured.raw).to_bytes(8, "big"))
        payload.extend(captured.raw)
    return sha256(payload).hexdigest()


_MANIFEST_ENTRY_KEYS = frozenset(
    {
        "schema_version",
        "scenario_id",
        "trace_path",
        "label",
        "scenario_type",
        "expected_detector_names",
        "expected_decision",
        "expected_evidence_event_ids",
        "expected_safety_outcome",
    }
)
_ENTRY_STRING_KEYS = frozenset(
    {
        "schema_version",
        "scenario_id",
        "trace_path",
        "label",
        "scenario_type",
        "expected_decision",
        "expected_safety_outcome",
    }
)
_ENTRY_LIST_KEYS = frozenset({"expected_detector_names", "expected_evidence_event_ids"})
_RESULT_BINDING_STRING_KEYS = frozenset(
    {
        "scenario_id",
        "label",
        "trace_sha256",
        "expected_decision",
        "expected_safety_outcome",
    }
)
_RESULT_BINDING_LIST_KEYS = frozenset(
    {"expected_detector_names", "expected_evidence_event_ids"}
)


def _closed_manifest_entry(value: object) -> dict[str, Any]:
    if type(value) is not dict or set(value) != _MANIFEST_ENTRY_KEYS:
        _fail("invalid_schema")
    if any(type(value[key]) is not str for key in _ENTRY_STRING_KEYS) or any(
        type(value[key]) is not list
        or any(type(item) is not str for item in value[key])
        for key in _ENTRY_LIST_KEYS
    ):
        _fail("invalid_schema")
    return value


def _validate_contextual_binding(
    result: dict[str, Any],
    records: list[dict[str, Any]],
    manifest: object,
    traces: object,
    policy_sha256: object,
) -> None:
    if (
        type(manifest) is not dict
        or type(manifest.get("entries")) is not list
        or type(traces) is not tuple
        or type(policy_sha256) is not str
    ):
        _fail("invalid_schema")
    entries = manifest["entries"]
    if (
        not (len(records) == len(traces) == len(entries))
        or len(records) > MAX_MANIFEST_ENTRIES
    ):
        _fail("invalid_schema")
    for item, captured, entry in zip(records, traces, entries, strict=True):
        if type(captured) is not _CapturedTrace or type(captured.raw) is not bytes:
            _fail("invalid_schema")
        captured_entry = _closed_manifest_entry(captured.entry)
        manifest_entry = _closed_manifest_entry(entry)
        if captured_entry != manifest_entry:
            _fail("invalid_schema")
        if any(
            type(item.get(key)) is not str for key in _RESULT_BINDING_STRING_KEYS
        ) or any(
            type(item.get(key)) is not list
            or any(type(value) is not str for value in item[key])
            for key in _RESULT_BINDING_LIST_KEYS
        ):
            _fail("invalid_schema")
        if (
            any(
                item[key] != manifest_entry[key]
                for key in (
                    "scenario_id",
                    "label",
                    "expected_detector_names",
                    "expected_decision",
                    "expected_evidence_event_ids",
                    "expected_safety_outcome",
                )
            )
            or item["trace_sha256"] != sha256(captured.raw).hexdigest()
        ):
            _fail("invalid_schema")
    if result.get("corpus_revision") != _revision(manifest, traces):
        _fail("invalid_schema")
    if result.get("policy_sha256") != policy_sha256:
        _fail("invalid_schema")


def _validate_result(
    result: object,
    *,
    manifest: dict[str, Any] | None = None,
    traces: tuple[_CapturedTrace, ...] | None = None,
    policy_sha256: str | None = None,
) -> None:
    """Reject schema-shaped result forgeries by recomputing every relation."""
    if (
        type(result) is not dict
        or type(result.get("traces")) is not list
        or type(result.get("metrics")) is not dict
    ):
        _fail("invalid_schema")
    records = result["traces"]
    for item in records:
        if type(item) is not dict or type(item.get("decision_record")) is not dict:
            _fail("invalid_schema")
        record = item["decision_record"]
        try:
            DecisionEnvelope.from_dict(record)
        except ModelValidationError:
            _fail("invalid_schema")
        if (
            item.get("decision_sha256") != sha256(canonicalize_json(record)).hexdigest()
            or item.get("actual_decision") != record["decision"]
            or item.get("actual_detector_names") != _detector_names(record)
        ):
            _fail("invalid_schema")
        label = item.get("label")
        actual_evidence = record["evidence_event_ids"]
        checks = {
            "detector_missing_names": [
                name
                for name in item.get("expected_detector_names", [])
                if name not in item.get("actual_detector_names", [])
            ],
            "detector_unexpected_names": [
                name
                for name in item.get("actual_detector_names", [])
                if name not in item.get("expected_detector_names", [])
            ],
            "detectors_match": item.get("expected_detector_names")
            == item.get("actual_detector_names"),
            "decision_match": item.get("expected_decision")
            == item.get("actual_decision"),
            "evidence_missing_event_ids": [
                name
                for name in item.get("expected_evidence_event_ids", [])
                if name not in actual_evidence
            ],
            "evidence_unexpected_event_ids": [
                name
                for name in actual_evidence
                if name not in item.get("expected_evidence_event_ids", [])
            ],
            "evidence_match": item.get("expected_evidence_event_ids")
            == actual_evidence,
            "actual_safety_outcome": _safety(record["decision"], label)
            if type(label) is str
            else None,
            "safety_match": item.get("expected_safety_outcome")
            == (_safety(record["decision"], label) if type(label) is str else None),
        }
        if item.get("actual_evidence_event_ids") != actual_evidence or any(
            item.get(key) != value for key, value in checks.items()
        ):
            _fail("invalid_schema")
    try:
        if result["metrics"] != compute_metrics(records):
            _fail("invalid_schema")
    except MetricsError:
        _fail("invalid_schema")
    contextual = manifest is not None or traces is not None or policy_sha256 is not None
    if contextual:
        if manifest is None or traces is None or policy_sha256 is None:
            _fail("invalid_schema")
        _validate_contextual_binding(result, records, manifest, traces, policy_sha256)


def validate_result(
    result: object,
    *,
    manifest: dict[str, Any] | None = None,
    traces: tuple[_CapturedTrace, ...] | None = None,
    policy_sha256: str | None = None,
) -> None:
    """Validate result relations; contextual inputs are required for provenance."""
    try:
        _validate_result(
            result,
            manifest=manifest,
            traces=traces,
            policy_sha256=policy_sha256,
        )
    except BenchmarkError:
        raise
    except Exception:  # noqa: BLE001 - untrusted standalone inputs fail closed.
        _fail("invalid_schema")


def replay_result(corpus: Path, manifest_path: Path) -> dict[str, Any]:
    """Capture once from root-anchored descriptors, analyze twice, then verify."""
    caps = _capabilities()
    root_fd = _open_root(_bound_root(corpus, manifest_path), caps)
    fds = [root_fd]
    try:
        benchmark_fd = _open_dir(root_fd, "benchmark", caps)
        fds.append(benchmark_fd)
        scenarios_fd = _open_dir(benchmark_fd, "scenarios", caps)
        fds.append(scenarios_fd)
        schemas_fd = _open_dir(benchmark_fd, "schemas", caps)
        fds.append(schemas_fd)
        v1_fd = _open_dir(schemas_fd, "v1", caps)
        fds.append(v1_fd)
        tests_fd = _open_dir(root_fd, "tests", caps)
        fds.append(tests_fd)
        fixtures_fd = _open_dir(tests_fd, "fixtures", caps)
        fds.append(fixtures_fd)
        contracts_fd = _open_dir(fixtures_fd, "contracts", caps)
        fds.append(contracts_fd)
        corpus_fd = _open_dir(benchmark_fd, "corpus", caps)
        fds.append(corpus_fd)
        manifest_raw = _read_regular(
            scenarios_fd, "manifest.json", caps, MAX_SMALL_BYTES
        )
        manifest_schema_raw = _read_regular(
            v1_fd, "corpus-manifest.schema.json", caps, MAX_SMALL_BYTES
        )
        result_schema_raw = _read_regular(
            v1_fd, "replay-result.schema.json", caps, MAX_SMALL_BYTES
        )
        if (
            sha256(manifest_schema_raw).hexdigest() != _CORPUS_MANIFEST_SCHEMA_SHA256
            or sha256(result_schema_raw).hexdigest() != _REPLAY_RESULT_SCHEMA_SHA256
        ):
            _fail("invalid_schema")
        manifest = _json_object(manifest_raw)
        _validate(
            _validator(manifest_schema_raw),
            manifest,
        )
        entries = manifest.get("entries")
        if (
            type(entries) is not list
            or not 24 <= len(entries) <= MAX_MANIFEST_ENTRIES
            or len(
                {entry.get("scenario_id") for entry in entries if type(entry) is dict}
            )
            != len(entries)
            or len(
                {entry.get("trace_path") for entry in entries if type(entry) is dict}
            )
            != len(entries)
        ):
            _fail("invalid_schema")
        expected_names = frozenset(
            _trace_leaf(entry["trace_path"]) for entry in entries
        )
        if (
            len(expected_names) != len(entries)
            or _corpus_names(corpus_fd) != expected_names
        ):
            _fail("io")
        policy, policy_digest = _policy(
            _read_regular(
                contracts_fd, "minimal-run-policy.json", caps, MAX_SMALL_BYTES
            ),
            manifest,
        )
        captured: list[_CapturedTrace] = []
        total = 0
        for entry in entries:
            if (
                type(entry) is not dict
                or _trace_leaf(entry["trace_path"]).removesuffix(".jsonl")
                != entry["scenario_id"]
            ):
                _fail("invalid_schema")
            raw, events = _read_trace(
                corpus_fd,
                entry["trace_path"],
                caps,
                MAX_CORPUS_BYTES - total,
                entry["scenario_id"],
            )
            total += len(raw)
            captured.append(_CapturedTrace(entry, raw, events))
        if _corpus_names(corpus_fd) != expected_names:
            _fail("io")
        frozen = tuple(captured)
        records: list[dict[str, Any]] = []
        deterministic = True
        for trace in frozen:
            first, first_bytes = _trace_record(trace, policy)
            _, second_bytes = _trace_record(trace, policy)
            deterministic = deterministic and first_bytes == second_bytes
            records.append(first)
        result = {
            "schema_version": "1.0",
            "corpus_version": "1.0",
            "corpus_revision": _revision(manifest, frozen),
            "policy_sha256": policy_digest,
            "tool": {
                "name": "semantic-reheating",
                "version": "0.1.0",
                "command": [
                    "benchmark",
                    "benchmark/corpus",
                    "--manifest",
                    "benchmark/scenarios/manifest.json",
                    "--format",
                    "json",
                ],
            },
            "deterministic_replay": deterministic,
            "metrics": compute_metrics(records),
            "traces": records,
        }
        _validate(
            _validator(result_schema_raw),
            result,
        )
        validate_result(
            result, manifest=manifest, traces=frozen, policy_sha256=policy_digest
        )
        return result
    except MetricsError:
        _fail("internal")
    finally:
        for fd in reversed(fds):
            try:
                os.close(fd)
            except OSError:
                pass


def replay_bytes(corpus: Path, manifest_path: Path) -> bytes:
    return canonicalize_json(replay_result(corpus, manifest_path)) + b"\n"


__all__ = ("BenchmarkError", "replay_bytes", "replay_result", "validate_result")
