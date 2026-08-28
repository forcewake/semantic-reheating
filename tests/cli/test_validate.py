from __future__ import annotations

import contextlib
import io
import json
import multiprocessing
import os
import subprocess
import sys
from pathlib import Path

import pytest

from semantic_reheating import cli
from semantic_reheating.canonical import canonicalize_json

ROOT = Path(__file__).resolve().parents[2]


def _main_in_child(argv: list[str], results: object) -> None:
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        result = cli.main(argv)
    results.put((result, stdout.getvalue(), stderr.getvalue()))  # type: ignore[union-attr]


def _main_without_nonblock_in_child(argv: list[str], results: object) -> None:
    delattr(cli.os, "O_NONBLOCK")
    _main_in_child(argv, results)


def _policy() -> dict[str, object]:
    return json.loads(
        (ROOT / "tests/fixtures/contracts/minimal-run-policy.json").read_text()
    )


def _event() -> dict[str, object]:
    return json.loads(
        (ROOT / "tests/fixtures/contracts/minimal-trace-event.json").read_text()
    )


def _write_json(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _write_trace(path: Path, *events: object) -> Path:
    path.write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )
    return path


def _duplicate_json_key(data: dict[str, object], key: str) -> str:
    encoded = json.dumps(data)
    key_value = f'"{key}": {json.dumps(data[key])}'
    return encoded.replace(key_value, f"{key_value}, {key_value}", 1)


def test_exit_constants_are_stable() -> None:
    assert (
        cli.EXIT_OK,
        cli.EXIT_USAGE,
        cli.EXIT_INVALID_SCHEMA,
        cli.EXIT_SEQUENCE_GAP,
        cli.EXIT_INCOMPATIBLE_VERSION,
        cli.EXIT_UNSAFE_POLICY,
        cli.EXIT_REQUIRED_DETECTOR_UNAVAILABLE,
        cli.EXIT_BENCHMARK_UNAVAILABLE,
        cli.EXIT_IO,
        cli.EXIT_INTERNAL,
    ) == (0, 2, 3, 4, 5, 6, 7, 8, 9, 10)


def test_help_lists_all_supported_subcommands(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["--help"]) == cli.EXIT_OK
    captured = capsys.readouterr()
    assert captured.err == ""
    assert all(
        command in captured.out
        for command in ("validate", "analyze", "explain", "benchmark")
    )


@pytest.mark.parametrize(
    "argv",
    [
        ["validate", "/sensitive/path/payload", "--policy", "policy", "secret"],
        ["validate", "trace", "--policy", "policy", "--token=secret"],
        ["analyze", "trace", "--policy", "policy", "--format", "attacker-value"],
        ["validate", "trace"],
    ],
)
def test_usage_errors_do_not_echo_untrusted_arguments(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(argv) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error: usage\n"


def test_benchmark_invalid_io_is_sanitized(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["benchmark", "corpus", "--manifest", "manifest.json"]) == 9
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error: io_error\n"


def test_validate_emits_only_canonical_status_record(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    trace = _write_trace(tmp_path / "trace.jsonl", _event())
    policy = _write_json(tmp_path / "policy.json", _policy())

    assert cli.main(["validate", str(trace), "--policy", str(policy)]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == (
        '{"contract_version":"1.0","event_count":1,"run_id":"run-example","status":"valid"}\n'
    )


def test_validate_emits_canonical_utf8_bytes_when_text_stdout_is_ascii(
    tmp_path: Path,
) -> None:
    event = _event()
    event["run_id"] = "run-é"
    trace = _write_trace(tmp_path / "trace.jsonl", event)
    policy = _write_json(tmp_path / "policy.json", _policy())
    result = subprocess.run(
        [
            str(Path(sys.executable).with_name("reheat")),
            "validate",
            str(trace),
            "--policy",
            str(policy),
        ],
        capture_output=True,
        check=False,
        env={**os.environ, "PYTHONIOENCODING": "ascii"},
    )

    assert result.returncode == 0
    assert (
        result.stdout
        == canonicalize_json(
            {
                "contract_version": "1.0",
                "event_count": 1,
                "run_id": "run-é",
                "status": "valid",
            }
        )
        + b"\n"
    )
    assert result.stderr == b""


def test_validate_broken_pipe_exits_successfully_without_a_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trace = _write_trace(tmp_path / "trace.jsonl", _event())
    policy = _write_json(tmp_path / "policy.json", _policy())

    class BrokenPipeBuffer:
        def write(self, data: object) -> int:
            del data
            raise BrokenPipeError

        def flush(self) -> None:
            raise BrokenPipeError

    class BrokenPipeStdout:
        buffer = BrokenPipeBuffer()

    stderr = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", BrokenPipeStdout())
    monkeypatch.setattr(cli.sys, "stderr", stderr)

    assert cli.main(["validate", str(trace), "--policy", str(policy)]) == 0
    assert stderr.getvalue() == ""


def test_validate_retries_binary_stdout_short_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trace = _write_trace(tmp_path / "trace.jsonl", _event())
    policy = _write_json(tmp_path / "policy.json", _policy())

    class ShortWriteBuffer:
        def __init__(self) -> None:
            self.output = bytearray()
            self.flushed = False

        def write(self, data: bytes | memoryview) -> int:
            chunk = bytes(data)
            written = min(len(chunk), 3)
            self.output.extend(chunk[:written])
            return written

        def flush(self) -> None:
            self.flushed = True

    class ShortWriteStdout:
        def __init__(self, buffer: ShortWriteBuffer) -> None:
            self.buffer = buffer

    buffer = ShortWriteBuffer()
    stderr = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", ShortWriteStdout(buffer))
    monkeypatch.setattr(cli.sys, "stderr", stderr)

    assert cli.main(["validate", str(trace), "--policy", str(policy)]) == 0
    assert (
        bytes(buffer.output)
        == canonicalize_json(
            {
                "contract_version": "1.0",
                "event_count": 1,
                "run_id": "run-example",
                "status": "valid",
            }
        )
        + b"\n"
    )
    assert buffer.flushed
    assert stderr.getvalue() == ""


def test_validate_rejects_indeterminate_binary_stdout_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trace = _write_trace(tmp_path / "trace.jsonl", _event())
    policy = _write_json(tmp_path / "policy.json", _policy())

    class IndeterminateBuffer:
        def write(self, data: object) -> None:
            del data

        def flush(self) -> None:
            pass

    class IndeterminateStdout:
        buffer = IndeterminateBuffer()

    stderr = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", IndeterminateStdout())
    monkeypatch.setattr(cli.sys, "stderr", stderr)

    assert cli.main(["validate", str(trace), "--policy", str(policy)]) == 10
    assert stderr.getvalue() == "error: internal_error\n"


@pytest.mark.parametrize("write_result", [True, False, type("EvilInt", (int,), {})(1)])
def test_validate_rejects_non_exact_binary_stdout_write_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, write_result: object
) -> None:
    trace = _write_trace(tmp_path / "trace.jsonl", _event())
    policy = _write_json(tmp_path / "policy.json", _policy())

    class InvalidCountBuffer:
        def __init__(self) -> None:
            self.output = bytearray()

        def write(self, data: object) -> object:
            del data
            return write_result

        def flush(self) -> None:
            pass

    class InvalidCountStdout:
        def __init__(self, buffer: InvalidCountBuffer) -> None:
            self.buffer = buffer

    buffer = InvalidCountBuffer()
    stderr = io.StringIO()
    monkeypatch.setattr(cli.sys, "stdout", InvalidCountStdout(buffer))
    monkeypatch.setattr(cli.sys, "stderr", stderr)

    assert cli.main(["validate", str(trace), "--policy", str(policy)]) == 10
    assert bytes(buffer.output) == b""
    assert stderr.getvalue() == "error: internal_error\n"


def test_validate_reads_complete_documents_when_os_reads_are_short(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    trace = _write_trace(tmp_path / "trace.jsonl", _event())
    policy = _write_json(tmp_path / "policy.json", _policy())
    original_read = cli.os.read
    read_calls = 0

    def short_read(descriptor: int, requested: int) -> bytes:
        nonlocal read_calls
        read_calls += 1
        return original_read(descriptor, min(requested, 3))

    monkeypatch.setattr(cli.os, "read", short_read)

    assert cli.main(["validate", str(trace), "--policy", str(policy)]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == (
        '{"contract_version":"1.0","event_count":1,"run_id":"run-example","status":"valid"}\n'
    )
    assert read_calls > 1


def test_validate_rejects_garbage_after_a_short_valid_trace_prefix(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    trace = tmp_path / "trace.jsonl"
    valid_prefix = (json.dumps(_event()) + "\n").encode("utf-8")
    trace.write_bytes(valid_prefix + b"garbage")
    policy = _write_json(tmp_path / "policy.json", _policy())
    trace_inode = trace.stat().st_ino
    original_read = cli.os.read
    trace_read_calls = 0

    def prefix_then_remainder(descriptor: int, requested: int) -> bytes:
        nonlocal trace_read_calls
        if os.fstat(descriptor).st_ino == trace_inode:
            trace_read_calls += 1
            if trace_read_calls == 1:
                return original_read(descriptor, min(requested, len(valid_prefix)))
        return original_read(descriptor, requested)

    monkeypatch.setattr(cli.os, "read", prefix_then_remainder)

    assert cli.main(["validate", str(trace), "--policy", str(policy)]) == 3
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error: invalid_schema\n"
    assert trace_read_calls > 1


def test_validate_works_without_os_nonblock_flag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    trace = _write_trace(tmp_path / "trace.jsonl", _event())
    policy = _write_json(tmp_path / "policy.json", _policy())
    monkeypatch.delattr(cli.os, "O_NONBLOCK", raising=False)

    assert cli.main(["validate", str(trace), "--policy", str(policy)]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == (
        '{"contract_version":"1.0","event_count":1,"run_id":"run-example","status":"valid"}\n'
    )


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda event: event.pop("contract_version"), 3),
        (lambda event: event.__setitem__("contract_version", "2.0"), 5),
        (lambda event: event.__setitem__("contract_version", 1), 5),
    ],
)
def test_validate_preflights_trace_versions(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    mutate: object,
    expected: int,
) -> None:
    event = _event()
    mutate(event)  # type: ignore[operator]
    trace = _write_trace(tmp_path / "trace.jsonl", event)
    policy = _write_json(tmp_path / "policy.json", _policy())

    assert cli.main(["validate", str(trace), "--policy", str(policy)]) == expected
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("error: ")
    assert "run-example" not in captured.err


def test_validate_maps_sequence_gap_and_duplicate_to_distinct_safe_codes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first, second = _event(), _event()
    second["event_id"] = "event-002"
    second["sequence"] = 3
    trace = _write_trace(tmp_path / "trace.jsonl", first, second)
    policy = _write_json(tmp_path / "policy.json", _policy())
    assert cli.main(["validate", str(trace), "--policy", str(policy)]) == 4
    assert capsys.readouterr().out == ""

    second["sequence"] = 2
    second["event_id"] = first["event_id"]
    _write_trace(trace, first, second)
    assert cli.main(["validate", str(trace), "--policy", str(policy)]) == 3
    assert capsys.readouterr().out == ""


def test_validate_rejects_a_trace_starting_at_sequence_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    event = _event()
    event["sequence"] = 2
    trace = _write_trace(tmp_path / "trace.jsonl", event)
    policy = _write_json(tmp_path / "policy.json", _policy())

    assert cli.main(["validate", str(trace), "--policy", str(policy)]) == 4
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error: sequence_gap\n"


@pytest.mark.parametrize("duplicate_location", ["trace", "policy", "nested_policy"])
def test_validate_rejects_duplicate_json_keys_at_ingress(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    duplicate_location: str,
) -> None:
    event = _event()
    policy_data = _policy()
    trace_text = json.dumps(event)
    policy_text = json.dumps(policy_data)
    if duplicate_location == "trace":
        trace_text = _duplicate_json_key(event, "contract_version")
    elif duplicate_location == "policy":
        policy_text = _duplicate_json_key(policy_data, "contract_version")
    else:
        nested = policy_data["side_effect_rules"]  # type: ignore[index]
        nested["unknown_treated_as_repeatable"] = False  # type: ignore[index]
        policy_text = json.dumps(policy_data).replace(
            '"unknown_treated_as_repeatable": false',
            '"unknown_treated_as_repeatable": false, '
            '"unknown_treated_as_repeatable": false',
            1,
        )
    trace = tmp_path / "trace.jsonl"
    trace.write_text(trace_text + "\n", encoding="utf-8")
    policy = tmp_path / "policy.json"
    policy.write_text(policy_text, encoding="utf-8")

    assert cli.main(["validate", str(trace), "--policy", str(policy)]) == 3
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error: invalid_schema\n"


def test_validate_hides_file_and_payload_data_on_invalid_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    trace = tmp_path / "secret-trace.jsonl"
    trace.write_text('{"payload":"secret payload"\n', encoding="utf-8")
    policy = _write_json(tmp_path / "policy.json", _policy())

    assert cli.main(["validate", str(trace), "--policy", str(policy)]) == 3
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error: invalid_schema\n"
    assert "secret" not in captured.err


def test_validate_rejects_empty_and_non_object_jsonl(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    policy = _write_json(tmp_path / "policy.json", _policy())
    trace = tmp_path / "trace.jsonl"
    trace.write_text(" \n\t\n", encoding="utf-8")
    assert cli.main(["validate", str(trace), "--policy", str(policy)]) == 3
    assert capsys.readouterr().out == ""

    trace.write_text("[]\n", encoding="utf-8")
    assert cli.main(["validate", str(trace), "--policy", str(policy)]) == 3
    assert capsys.readouterr().out == ""


def test_safe_loader_enforces_exact_byte_boundaries(tmp_path: Path) -> None:
    content = b'{"x":1}'
    source = tmp_path / "small.json"
    source.write_bytes(content)
    assert cli._read_bytes(str(source), len(content)) == content
    with pytest.raises(cli._CliFailure) as caught:
        cli._read_bytes(str(source), len(content) - 1)
    assert caught.value.exit_code == cli.EXIT_INVALID_SCHEMA


def test_validate_rejects_a_fifo_without_blocking(
    tmp_path: Path,
) -> None:
    if "fork" not in multiprocessing.get_all_start_methods():
        pytest.skip("FIFO promptness probe requires fork")
    fifo = tmp_path / "trace.fifo"
    os.mkfifo(fifo)
    policy = _write_json(tmp_path / "policy.json", _policy())
    context = multiprocessing.get_context("fork")
    results = context.Queue()
    worker = context.Process(
        target=_main_in_child,
        args=(["validate", str(fifo), "--policy", str(policy)], results),
    )
    worker.start()
    try:
        worker.join(1)
        assert not worker.is_alive(), "CLI blocked while opening FIFO"
        assert worker.exitcode == 0
        assert results.get(timeout=1) == (9, "", "error: io_error\n")
    finally:
        if worker.is_alive():
            worker.terminate()
        worker.join()
        results.close()


def test_validate_rejects_a_fifo_without_nonblock_flag_without_blocking(
    tmp_path: Path,
) -> None:
    if "fork" not in multiprocessing.get_all_start_methods():
        pytest.skip("FIFO promptness probe requires fork")
    fifo = tmp_path / "trace.fifo"
    os.mkfifo(fifo)
    policy = _write_json(tmp_path / "policy.json", _policy())
    context = multiprocessing.get_context("fork")
    results = context.Queue()
    worker = context.Process(
        target=_main_without_nonblock_in_child,
        args=(["validate", str(fifo), "--policy", str(policy)], results),
    )
    worker.start()
    try:
        worker.join(1)
        assert not worker.is_alive(), (
            "CLI blocked while opening FIFO without O_NONBLOCK"
        )
        assert worker.exitcode == 0
        assert results.get(timeout=1) == (9, "", "error: io_error\n")
    finally:
        if worker.is_alive():
            worker.terminate()
        worker.join()
        results.close()


@pytest.mark.parametrize("source_kind", ["directory", "dev_null", "symlink"])
def test_validate_rejects_non_regular_or_symlinked_trace_inputs(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    source_kind: str,
) -> None:
    policy = _write_json(tmp_path / "policy.json", _policy())
    if source_kind == "directory":
        source = tmp_path / "trace-directory"
        source.mkdir()
    elif source_kind == "dev_null":
        source = Path("/dev/null")
        if not source.exists():
            pytest.skip("/dev/null is unavailable")
    else:
        target = _write_trace(tmp_path / "valid-target.jsonl", _event())
        source = tmp_path / "trace-link.jsonl"
        source.symlink_to(target)

    assert cli.main(["validate", str(source), "--policy", str(policy)]) == 9
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error: io_error\n"
    assert str(source) not in captured.err


@pytest.mark.parametrize("source_kind", ["directory", "dev_null", "symlink"])
def test_validate_rejects_non_regular_or_symlinked_inputs_without_nonblock_flag(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    source_kind: str,
) -> None:
    policy = _write_json(tmp_path / "policy.json", _policy())
    if source_kind == "directory":
        source = tmp_path / "trace-directory"
        source.mkdir()
    elif source_kind == "dev_null":
        source = Path("/dev/null")
        if not source.exists():
            pytest.skip("/dev/null is unavailable")
    else:
        target = _write_trace(tmp_path / "valid-target.jsonl", _event())
        source = tmp_path / "trace-link.jsonl"
        source.symlink_to(target)
    monkeypatch.delattr(cli.os, "O_NONBLOCK", raising=False)

    assert cli.main(["validate", str(source), "--policy", str(policy)]) == 9
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error: io_error\n"


def test_validate_maps_unsafe_policy_and_io_without_leaking_paths(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    trace = _write_trace(tmp_path / "trace.jsonl", _event())
    policy_data = _policy()
    policy_data["side_effect_rules"]["unknown_treated_as_repeatable"] = True  # type: ignore[index]
    policy = _write_json(tmp_path / "unsafe-policy.json", policy_data)
    assert cli.main(["validate", str(trace), "--policy", str(policy)]) == 6
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error: unsafe_policy\n"

    missing = tmp_path / "secret-missing.jsonl"
    assert cli.main(["validate", str(missing), "--policy", str(policy)]) == 9
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error: io_error\n"
