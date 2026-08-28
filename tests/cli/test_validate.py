from __future__ import annotations

import contextlib
import io
import json
import multiprocessing
import os
from pathlib import Path

import pytest

from semantic_reheating import cli

ROOT = Path(__file__).resolve().parents[2]


def _main_in_child(argv: list[str], results: object) -> None:
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        result = cli.main(argv)
    results.put((result, stdout.getvalue(), stderr.getvalue()))  # type: ignore[union-attr]


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


def test_benchmark_is_parsed_but_unavailable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["benchmark", "corpus", "--manifest", "manifest.json"]) == 8
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error: benchmark_unavailable\n"


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
