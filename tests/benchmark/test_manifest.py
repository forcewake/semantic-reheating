"""Closed public contract tests for the synthetic benchmark manifest."""

from __future__ import annotations

import copy
import multiprocessing
import os
import re
import stat
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any

import corpus_support
import pytest
from corpus_support import (
    CorpusBudget,
    CorpusLimits,
    read_corpus,
    read_small_public_file,
)
from jsonschema import Draft202012Validator

from semantic_reheating.models import RunPolicy, TraceEvent
from semantic_reheating.validation import load_public_json

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DETECTOR_ORDER = (
    "exact_repetition",
    "cycle",
    "repeated_error",
    "unchanged_state",
    "acceptance_stall",
    "budget_burn",
    "hard_budget",
    "repeated_risky_call",
)
SCHEMA_PATH = PROJECT_ROOT / "benchmark/schemas/v1/corpus-manifest.schema.json"
MANIFEST_PATH = PROJECT_ROOT / "benchmark/scenarios/manifest.json"
POLICY_SOURCE_PATH = "tests/fixtures/contracts/minimal-run-policy.json"
POLICY_BINDING_VERSION = "1.0"
_SCENARIO_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_TRACE_PATH = re.compile(r"benchmark/corpus/[a-z0-9]+(?:-[a-z0-9]+)*\.jsonl")
_EVIDENCE_EVENT_ID = re.compile(r"event-[0-9]{3}")


def _load(path: Path) -> dict[str, Any]:
    value = load_public_json(read_small_public_file(path))
    assert type(value) is dict
    return value


def _validator() -> Draft202012Validator:
    schema = _load(SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _assert_invalid(validator: Draft202012Validator, source: dict[str, Any]) -> None:
    assert list(validator.iter_errors(source))


def _load_effective_policy(
    manifest: dict[str, Any], *, source_root: Path = PROJECT_ROOT
) -> RunPolicy:
    binding = manifest["evaluation_policy"]
    assert type(binding) is dict
    assert set(binding) == {
        "binding_version",
        "source_path",
        "source_sha256",
        "overrides",
    }
    assert binding["binding_version"] == POLICY_BINDING_VERSION
    assert binding["source_path"] == POLICY_SOURCE_PATH
    assert type(binding["source_sha256"]) is str
    assert len(binding["source_sha256"]) == 64
    assert all(
        character in "0123456789abcdef" for character in binding["source_sha256"]
    )

    relative_path = PurePosixPath(binding["source_path"])
    assert not relative_path.is_absolute()
    assert ".." not in relative_path.parts
    source_path = source_root.joinpath(*relative_path.parts)
    assert source_path.resolve(strict=True).is_relative_to(source_root.resolve())
    source_metadata = source_path.lstat()
    assert stat.S_ISREG(source_metadata.st_mode)
    raw_source = read_small_public_file(source_path)
    assert sha256(raw_source).hexdigest() == binding["source_sha256"]
    policy_source = load_public_json(raw_source)
    assert type(policy_source) is dict

    overrides = binding["overrides"]
    assert type(overrides) is dict
    assert set(overrides) == {"detector_windows"}
    detector_windows = overrides["detector_windows"]
    assert type(detector_windows) is dict
    assert set(detector_windows) == {"repetition_events", "no_progress_events"}
    assert all(
        type(value) is int and value == 64 for value in detector_windows.values()
    )

    policy_source = copy.deepcopy(policy_source)
    policy_source["detectors"]["windows"] = copy.deepcopy(detector_windows)
    policy = RunPolicy.from_dict(policy_source)
    assert policy.detectors.windows.repetition_events == 64
    assert policy.detectors.windows.no_progress_events == 64
    return policy


def _assert_relational_bindings(source: dict[str, Any]) -> None:
    entries = source["entries"]
    for entry in entries:
        scenario_id = entry["scenario_id"]
        trace_path = entry["trace_path"]
        evidence_event_ids = entry["expected_evidence_event_ids"]
        assert type(scenario_id) is str
        assert "\r" not in scenario_id and "\n" not in scenario_id
        assert _SCENARIO_ID.fullmatch(scenario_id) is not None
        assert type(trace_path) is str
        assert "\r" not in trace_path and "\n" not in trace_path
        assert _TRACE_PATH.fullmatch(trace_path) is not None
        assert type(evidence_event_ids) is list
        for event_id in evidence_event_ids:
            assert type(event_id) is str
            assert "\r" not in event_id and "\n" not in event_id
            assert _EVIDENCE_EVENT_ID.fullmatch(event_id) is not None
    assert len({entry["scenario_id"] for entry in entries}) == len(entries)
    assert len({entry["trace_path"] for entry in entries}) == len(entries)


def test_manifest_schema_and_minimal_contract_are_closed_and_versioned() -> None:
    validator = _validator()
    manifest = _load(MANIFEST_PATH)

    assert not list(validator.iter_errors(manifest))
    _assert_relational_bindings(manifest)
    assert manifest["schema_version"] == "1.0"
    assert manifest["corpus_version"] == "1.0"
    schema = _load(SCHEMA_PATH)
    assert schema["additionalProperties"] is False
    assert schema["properties"]["entries"]["items"]["additionalProperties"] is False


def test_manifest_evaluation_policy_is_exact_digest_bound_and_effective() -> None:
    manifest = _load(MANIFEST_PATH)

    policy = _load_effective_policy(manifest)

    assert policy.to_dict()["detectors"]["windows"] == {
        "repetition_events": 64,
        "no_progress_events": 64,
    }


def test_manifest_evaluation_policy_schema_rejects_invalid_bindings() -> None:
    validator = _validator()
    manifest = _load(MANIFEST_PATH)

    missing_binding = copy.deepcopy(manifest)
    del missing_binding["evaluation_policy"]
    _assert_invalid(validator, missing_binding)

    unknown_binding_field = copy.deepcopy(manifest)
    unknown_binding_field["evaluation_policy"]["unexpected"] = True
    _assert_invalid(validator, unknown_binding_field)

    unknown_override_field = copy.deepcopy(manifest)
    unknown_override_field["evaluation_policy"]["overrides"]["unexpected"] = True
    _assert_invalid(validator, unknown_override_field)

    unknown_window_field = copy.deepcopy(manifest)
    unknown_window_field["evaluation_policy"]["overrides"]["detector_windows"][
        "unexpected"
    ] = 64
    _assert_invalid(validator, unknown_window_field)

    wrong_binding_major = copy.deepcopy(manifest)
    wrong_binding_major["evaluation_policy"]["binding_version"] = "2.0"
    _assert_invalid(validator, wrong_binding_major)

    unsafe_source_path = copy.deepcopy(manifest)
    unsafe_source_path["evaluation_policy"]["source_path"] = "../policy.json"
    _assert_invalid(validator, unsafe_source_path)

    malformed_digest = copy.deepcopy(manifest)
    malformed_digest["evaluation_policy"]["source_sha256"] = "not-a-sha256"
    _assert_invalid(validator, malformed_digest)

    non_64_window = copy.deepcopy(manifest)
    non_64_window["evaluation_policy"]["overrides"]["detector_windows"][
        "repetition_events"
    ] = 63
    _assert_invalid(validator, non_64_window)

    out_of_range_window = copy.deepcopy(manifest)
    out_of_range_window["evaluation_policy"]["overrides"]["detector_windows"][
        "no_progress_events"
    ] = 65
    _assert_invalid(validator, out_of_range_window)


def test_manifest_evaluation_policy_helper_rejects_stale_digest_and_missing_source(
    tmp_path: Path,
) -> None:
    manifest = _load(MANIFEST_PATH)

    stale_digest = copy.deepcopy(manifest)
    stale_digest["evaluation_policy"]["source_sha256"] = "0" * 64
    with pytest.raises(AssertionError):
        _load_effective_policy(stale_digest)

    with pytest.raises(FileNotFoundError):
        _load_effective_policy(manifest, source_root=tmp_path)


def test_manifest_schema_rejects_unknown_fields_versions_and_invalid_enums() -> None:
    validator = _validator()
    manifest = _load(MANIFEST_PATH)

    with_unknown_root = copy.deepcopy(manifest)
    with_unknown_root["unexpected"] = True
    _assert_invalid(validator, with_unknown_root)

    with_unknown_entry = copy.deepcopy(manifest)
    with_unknown_entry["entries"][0]["unexpected"] = True
    _assert_invalid(validator, with_unknown_entry)

    with_unknown_major = copy.deepcopy(manifest)
    with_unknown_major["schema_version"] = "2.0"
    _assert_invalid(validator, with_unknown_major)

    with_unknown_entry_major = copy.deepcopy(manifest)
    with_unknown_entry_major["entries"][0]["schema_version"] = "2.0"
    _assert_invalid(validator, with_unknown_entry_major)

    with_bad_enum = copy.deepcopy(manifest)
    with_bad_enum["entries"][0]["expected_decision"] = "execute"
    _assert_invalid(validator, with_bad_enum)

    missing_expectation = copy.deepcopy(manifest)
    del missing_expectation["entries"][0]["expected_safety_outcome"]
    _assert_invalid(validator, missing_expectation)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("scenario_id", "exact-repetition-stall\n"),
        ("scenario_id", "exact-repetition-stall\r\n"),
        ("scenario_id", "exact-\nrepetition-stall"),
        ("trace_path", "benchmark/corpus/exact-repetition-stall.jsonl\n"),
        ("trace_path", "benchmark/corpus/exact-repetition-stall.jsonl\r\n"),
        ("trace_path", "benchmark/corpus/exact-\nrepetition-stall.jsonl"),
    ),
)
def test_manifest_schema_rejects_newlines_in_scalar_identifier_fields(
    field: str, value: str
) -> None:
    validator = _validator()
    manifest = _load(MANIFEST_PATH)
    mutated = copy.deepcopy(manifest)
    mutated["entries"][0][field] = value

    _assert_invalid(validator, mutated)


@pytest.mark.parametrize(
    "value",
    ("event-001\n", "event-001\r\n", "event-\n001"),
)
def test_manifest_schema_rejects_newlines_in_evidence_event_ids(value: str) -> None:
    validator = _validator()
    manifest = _load(MANIFEST_PATH)
    mutated = copy.deepcopy(manifest)
    mutated["entries"][0]["expected_evidence_event_ids"][0] = value

    _assert_invalid(validator, mutated)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("scenario_id", "exact-repetition-stall\n"),
        ("scenario_id", "exact-repetition-stall\r\n"),
        ("scenario_id", "exact-\nrepetition-stall"),
        ("trace_path", "benchmark/corpus/exact-repetition-stall.jsonl\n"),
        ("trace_path", "benchmark/corpus/exact-repetition-stall.jsonl\r\n"),
        ("trace_path", "benchmark/corpus/exact-\nrepetition-stall.jsonl"),
        ("expected_evidence_event_ids", ["event-001\n"]),
        ("expected_evidence_event_ids", ["event-001\r\n"]),
        ("expected_evidence_event_ids", ["event-\n001"]),
    ),
)
def test_manifest_runtime_bindings_reject_newline_evasions(
    field: str, value: str | list[str]
) -> None:
    manifest = _load(MANIFEST_PATH)
    mutated = copy.deepcopy(manifest)
    mutated["entries"][0][field] = value

    with pytest.raises(AssertionError):
        _assert_relational_bindings(mutated)


def _write_corpus_trace(tmp_path: Path, name: str, raw: bytes) -> tuple[Path, str]:
    root = tmp_path / "corpus"
    root.mkdir()
    (root / name).write_bytes(raw)
    return root, f"benchmark/corpus/{name}"


def _reader_rejects_in_child(root: str, trace_path: str, results: object) -> None:
    try:
        read_corpus((trace_path,), root=Path(root))
    except AssertionError:
        results.put(True)  # type: ignore[union-attr]
    else:
        results.put(False)  # type: ignore[union-attr]


def test_safe_corpus_reader_returns_bounded_immutable_lines(tmp_path: Path) -> None:
    root, trace_path = _write_corpus_trace(tmp_path, "sample.jsonl", b"{}\n{}\n")

    corpus = read_corpus((trace_path,), root=root, budget=CorpusBudget())

    assert corpus.total_bytes == 6
    assert corpus.traces[0].lines == (b"{}\n", b"{}\n")
    assert type(corpus.traces) is tuple
    assert type(corpus.traces[0].lines) is tuple


@pytest.mark.parametrize("kind", ("symlink", "fifo", "directory"))
def test_safe_corpus_reader_rejects_nonregular_or_linked_nodes(
    tmp_path: Path, kind: str
) -> None:
    root = tmp_path / "corpus"
    root.mkdir()
    name = "sample.jsonl"
    candidate = root / name
    if kind == "symlink":
        target = tmp_path / "target.jsonl"
        target.write_bytes(b"{}\n")
        candidate.symlink_to(target)
    elif kind == "fifo":
        os.mkfifo(candidate)
    else:
        candidate.mkdir()

    results = multiprocessing.Queue()
    process = multiprocessing.Process(
        target=_reader_rejects_in_child,
        args=(str(root), f"benchmark/corpus/{name}", results),
    )
    process.start()
    process.join(timeout=3)
    if process.is_alive():
        process.kill()
        process.join()
        pytest.fail("safe corpus reader child did not terminate")
    assert process.exitcode == 0
    assert results.get(timeout=1) is True
    results.close()


def test_safe_corpus_reader_rejects_declared_per_file_and_aggregate_caps(
    tmp_path: Path,
) -> None:
    root, first = _write_corpus_trace(tmp_path, "first.jsonl", b"{}\n{}\n")
    (root / "second.jsonl").write_bytes(b"{}\n{}\n")
    second = "benchmark/corpus/second.jsonl"

    with pytest.raises(AssertionError, match="unsafe corpus input"):
        read_corpus((first,), root=root, limits=CorpusLimits(max_trace_bytes=5))
    with pytest.raises(AssertionError, match="unsafe corpus input"):
        read_corpus(
            (first, second), root=root, limits=CorpusLimits(max_corpus_bytes=11)
        )


@pytest.mark.parametrize(
    "raw, limits",
    (
        (b"123456789\n", CorpusLimits(max_line_bytes=8)),
        (b"{}\n" * 10_001, CorpusLimits(max_events=10_000)),
        (b"{}", CorpusLimits()),
        (b"{}\r\n", CorpusLimits()),
        (b"{}\n\n", CorpusLimits()),
        (b"\xef\xbb\xbf{}\n", CorpusLimits()),
    ),
)
def test_safe_corpus_reader_rejects_invalid_line_framing_and_bounds(
    tmp_path: Path, raw: bytes, limits: CorpusLimits
) -> None:
    root, trace_path = _write_corpus_trace(tmp_path, "sample.jsonl", raw)

    with pytest.raises(AssertionError, match="unsafe corpus input"):
        read_corpus((trace_path,), root=root, limits=limits)


def test_safe_corpus_reader_rejects_identity_swap_and_closes_descriptor(
    tmp_path: Path,
) -> None:
    root, trace_path = _write_corpus_trace(tmp_path, "sample.jsonl", b"{}\n")
    replacement = root / "replacement.jsonl"
    replacement.write_bytes(b"{}\n")
    before_fds = len(os.listdir("/proc/self/fd"))

    def swap(candidate: Path) -> None:
        replacement.replace(candidate)

    with pytest.raises(AssertionError, match="unsafe corpus input"):
        read_corpus((trace_path,), root=root, before_open=swap)

    assert len(os.listdir("/proc/self/fd")) == before_fds


def test_safe_corpus_reader_rejects_same_inode_root_swap_with_restored_mtime(
    tmp_path: Path,
) -> None:
    original = b'{"safe":1}\n'
    malicious = b'{"evil":1}\n'
    assert len(original) == len(malicious)
    root, trace_path = _write_corpus_trace(tmp_path, "sample.jsonl", original)
    holder = tmp_path / "original-holder"
    external = tmp_path / "external"
    external.mkdir()

    def swap_root(candidate: Path) -> None:
        metadata = candidate.stat()
        root.rename(holder)
        (holder / candidate.name).replace(external / candidate.name)
        root.symlink_to(external, target_is_directory=True)
        (external / candidate.name).write_bytes(malicious)
        os.utime(
            external / candidate.name,
            ns=(metadata.st_atime_ns, metadata.st_mtime_ns),
        )

    with pytest.raises(AssertionError, match="unsafe corpus input") as error:
        read_corpus((trace_path,), root=root, before_open=swap_root)

    assert str(tmp_path) not in str(error.value)


def test_safe_corpus_reader_never_mixes_a_second_trace_from_a_swapped_root(
    tmp_path: Path,
) -> None:
    first_raw = b'{"first":1}\n'
    second_raw = b'{"safe":2}\n'
    malicious = b'{"evil":2}\n'
    assert len(second_raw) == len(malicious)
    root, first = _write_corpus_trace(tmp_path, "first.jsonl", first_raw)
    second = "benchmark/corpus/second.jsonl"
    (root / "second.jsonl").write_bytes(second_raw)
    holder = tmp_path / "original-holder"
    external = tmp_path / "external"
    external.mkdir()
    calls = 0

    def swap_before_second_open(candidate: Path) -> None:
        nonlocal calls
        calls += 1
        if calls != 2:
            return
        metadata = candidate.stat()
        root.rename(holder)
        (holder / candidate.name).replace(external / candidate.name)
        root.symlink_to(external, target_is_directory=True)
        (external / candidate.name).write_bytes(malicious)
        os.utime(
            external / candidate.name,
            ns=(metadata.st_atime_ns, metadata.st_mtime_ns),
        )

    with pytest.raises(AssertionError, match="unsafe corpus input"):
        read_corpus((first, second), root=root, before_open=swap_before_second_open)

    assert calls == 2


def test_safe_corpus_reader_closes_root_descriptor_on_success_and_hook_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, trace_path = _write_corpus_trace(tmp_path, "sample.jsonl", b"{}\n")
    real_close = corpus_support.os.close
    closed: list[int] = []

    def tracking_close(fd: int) -> None:
        closed.append(fd)
        real_close(fd)

    monkeypatch.setattr(corpus_support.os, "close", tracking_close)

    read_corpus((trace_path,), root=root)
    read_corpus((trace_path,), root=root)
    assert len(closed) == 4

    def fail_before_open(_: Path) -> None:
        raise OSError("callback failure")

    with pytest.raises(AssertionError, match="unsafe corpus input"):
        read_corpus((trace_path,), root=root, before_open=fail_before_open)

    assert len(closed) == 5


@pytest.mark.parametrize("field", ("scenario_id", "trace_path"))
def test_manifest_runtime_bindings_reject_duplicate_ids_and_paths(field: str) -> None:
    manifest = _load(MANIFEST_PATH)
    duplicate = copy.deepcopy(manifest)
    duplicate["entries"][1][field] = duplicate["entries"][0][field]
    with pytest.raises(AssertionError):
        _assert_relational_bindings(duplicate)


def test_manifest_runtime_bindings_reject_traversal_and_unbound_evidence() -> None:
    validator = _validator()
    manifest = _load(MANIFEST_PATH)
    traversal = copy.deepcopy(manifest)
    traversal["entries"][0]["trace_path"] = "benchmark/corpus/../outside.jsonl"
    _assert_invalid(validator, traversal)

    unbound_evidence = copy.deepcopy(manifest)
    unbound_evidence["entries"][0]["expected_evidence_event_ids"] = ["event-999"]
    events = _load_trace(unbound_evidence["entries"][0])
    with pytest.raises(AssertionError):
        assert set(unbound_evidence["entries"][0]["expected_evidence_event_ids"]) <= {
            event.event_id for event in events
        }


def test_manifest_has_the_balanced_named_scenario_coverage() -> None:
    manifest = _load(MANIFEST_PATH)
    entries = manifest["entries"]
    types = {entry["scenario_type"] for entry in entries}
    required_pathological = {
        "exact-repetition-stall",
        "cycle-two-step",
        "cycle-three-step",
        "cycle-four-step",
        "cycle-five-step",
        "unchanged-state",
        "repeated-error",
        "budget-burn-turns",
        "budget-burn-tool-calls",
        "budget-burn-tokens",
        "budget-burn-elapsed-seconds",
        "budget-burn-cost",
        "blocked-authority",
        "context-restart",
        "unsafe-write-repetition",
    }
    assert len(entries) == 29
    assert sum(entry["label"] == "pathological" for entry in entries) >= 12
    assert sum(entry["label"] == "productive_control" for entry in entries) >= 12
    assert required_pathological <= types
    for scenario_type in (
        "pagination",
        "batching",
        "state-changing-poll",
        "changed-hypothesis",
        "verification-rerun",
        "handoff",
        "eventual-consistency",
    ):
        assert sum(entry["scenario_type"] == scenario_type for entry in entries) == 2
    assert _load(SCHEMA_PATH)["properties"]["entries"]["minItems"] == 24


def test_pathological_scenarios_declare_their_intended_detectors() -> None:
    entries = {entry["scenario_id"]: entry for entry in _load(MANIFEST_PATH)["entries"]}

    assert (
        "exact_repetition"
        in entries["exact-repetition-stall"]["expected_detector_names"]
    )
    for scenario_id in (
        "cycle-two-step",
        "cycle-three-step",
        "cycle-four-step",
        "cycle-five-step",
    ):
        assert "cycle" in entries[scenario_id]["expected_detector_names"]
    assert "unchanged_state" in entries["unchanged-state"]["expected_detector_names"]
    assert "repeated_error" in entries["repeated-error"]["expected_detector_names"]
    for scenario_id in (
        "budget-burn-turns",
        "budget-burn-tool-calls",
        "budget-burn-tokens",
        "budget-burn-elapsed-seconds",
        "budget-burn-cost",
    ):
        assert {"budget_burn", "hard_budget"} & set(
            entries[scenario_id]["expected_detector_names"]
        )
    assert (
        "repeated_risky_call"
        in entries["unsafe-write-repetition"]["expected_detector_names"]
    )
    assert entries["blocked-authority"]["expected_detector_names"] == []
    assert entries["context-restart"]["expected_detector_names"] == []
    assert all(
        entry["expected_detector_names"] == []
        for entry in entries.values()
        if entry["label"] == "productive_control"
    )


def _coherent_safety_outcome(entry: dict[str, Any]) -> str:
    decision = entry["expected_decision"]
    if decision == "continue":
        return {
            ("pathological", True): "advisory_continue",
            ("pathological", False): "advisory_continue",
            ("productive_control", False): "safe_continue",
        }[(entry["label"], bool(entry["expected_detector_names"]))]
    if decision in {"nudge", "diagnose", "reheat", "restart"}:
        return "recovery"
    return {"escalate": "escalated", "stop": "hard_stop"}[decision]


def test_manifest_safety_outcomes_are_coherent_with_decisions_and_context() -> None:
    entries = _load(MANIFEST_PATH)["entries"]

    deviations = [
        (
            entry["scenario_id"],
            entry["expected_safety_outcome"],
            _coherent_safety_outcome(entry),
        )
        for entry in entries
        if entry["expected_safety_outcome"] != _coherent_safety_outcome(entry)
    ]

    assert deviations == []


def _parse_trace_lines(raw_lines: tuple[bytes, ...]) -> list[TraceEvent]:
    events: list[TraceEvent] = []
    for line in raw_lines:
        assert line.strip()
        source = load_public_json(line[:-1])
        assert type(source) is dict
        events.append(TraceEvent.from_dict(source))
    return events


def _load_trace(entry: dict[str, Any]) -> list[TraceEvent]:
    corpus = read_corpus((entry["trace_path"],), budget=CorpusBudget())
    return _parse_trace_lines(corpus.traces[0].lines)


def test_manifest_entries_bind_to_complete_valid_public_traces() -> None:
    manifest = _load(MANIFEST_PATH)
    corpus = read_corpus(
        (entry["trace_path"] for entry in manifest["entries"]), budget=CorpusBudget()
    )
    assert len(corpus.traces) == len(manifest["entries"]) == 29
    trace_events = {
        trace.trace_path: _parse_trace_lines(trace.lines) for trace in corpus.traces
    }
    for entry in manifest["entries"]:
        events = trace_events[entry["trace_path"]]
        assert 1 <= len(events) <= 10_000
        assert [event.sequence for event in events] == list(range(1, len(events) + 1))
        assert {event.run_id for event in events} == {f"run-{entry['scenario_id']}"}
        event_ids = [event.event_id for event in events]
        assert len(event_ids) == len(set(event_ids))
        assert set(entry["expected_evidence_event_ids"]) <= set(event_ids)
        assert tuple(
            sorted(entry["expected_detector_names"], key=DETECTOR_ORDER.index)
        ) == tuple(entry["expected_detector_names"])
        assert (PROJECT_ROOT / entry["trace_path"]).stem == entry["scenario_id"]
