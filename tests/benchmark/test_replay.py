"""Offline deterministic replay contract and mismatch reporting tests."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from benchmark.metrics import compute_metrics
from benchmark.replay import (
    _CORPUS_MANIFEST_SCHEMA_SHA256,
    _REPLAY_RESULT_SCHEMA_SHA256,
    BenchmarkError,
    _CapturedTrace,
    _detector_names,
    _parse_events,
    _policy,
    _revision,
    _trace_record,
    replay_bytes,
    replay_result,
    validate_result,
)

CORPUS = ROOT / "benchmark/corpus"
MANIFEST = ROOT / "benchmark/scenarios/manifest.json"
RESULT = ROOT / "benchmark/results/deterministic-results.json"
SCHEMA = ROOT / "benchmark/schemas/v1/replay-result.schema.json"


def _context() -> tuple[dict[str, object], tuple[_CapturedTrace, ...], str]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    traces = tuple(
        _CapturedTrace(
            dict(entry),
            (ROOT / entry["trace_path"]).read_bytes(),
            _parse_events(
                (ROOT / entry["trace_path"]).read_bytes(), entry["scenario_id"]
            ),
        )
        for entry in manifest["entries"]
    )
    return manifest, traces, manifest["evaluation_policy"]["source_sha256"]


def _copied_root(tmp_path: Path) -> Path:
    root = tmp_path / "copy"
    shutil.copytree(ROOT / "benchmark", root / "benchmark")
    (root / "tests/fixtures").mkdir(parents=True)
    shutil.copytree(
        ROOT / "tests/fixtures/contracts", root / "tests/fixtures/contracts"
    )
    return root


def test_replay_is_byte_deterministic_matches_committed_artifact_and_schema() -> None:
    first = replay_bytes(CORPUS, MANIFEST)
    second = replay_bytes(CORPUS, MANIFEST)
    assert first == second == RESULT.read_bytes()
    assert first.endswith(b"\n") and first.count(b"\n") == 1
    result = json.loads(first)
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    assert not list(Draft202012Validator(schema).iter_errors(result))
    assert result["deterministic_replay"] is True
    assert result["metrics"]["decision_accuracy"]["value"] == 1.0
    assert result["metrics"]["false_intervention_rate"]["value"] == 0.0
    assert len(result["traces"]) == 29


def test_schema_contract_digests_pin_the_raw_committed_bytes() -> None:
    assert (
        _CORPUS_MANIFEST_SCHEMA_SHA256
        == hashlib.sha256(
            (ROOT / "benchmark/schemas/v1/corpus-manifest.schema.json").read_bytes()
        ).hexdigest()
    )
    assert (
        _REPLAY_RESULT_SCHEMA_SHA256 == hashlib.sha256(SCHEMA.read_bytes()).hexdigest()
    )


@pytest.mark.parametrize(
    "schema_name", ["corpus-manifest.schema.json", "replay-result.schema.json"]
)
def test_replay_rejects_even_valid_but_unpinned_schema_bytes(
    tmp_path: Path, schema_name: str
) -> None:
    root = _copied_root(tmp_path)
    schema = root / "benchmark/schemas/v1" / schema_name
    schema.write_text(
        '{"$schema":"https://json-schema.org/draft/2020-12/schema","anyOf":[{}]}'
    )

    with pytest.raises(BenchmarkError) as caught:
        replay_result(
            root / "benchmark/corpus", root / "benchmark/scenarios/manifest.json"
        )

    assert caught.value.code == "invalid_schema"


@pytest.mark.parametrize("extra_name", ["unlisted.jsonl", "unexpected.txt"])
def test_replay_rejects_a_physically_open_corpus_directory(
    tmp_path: Path, extra_name: str
) -> None:
    root = _copied_root(tmp_path)
    (root / "benchmark/corpus" / extra_name).write_text("x", encoding="utf-8")

    with pytest.raises(BenchmarkError) as caught:
        replay_result(
            root / "benchmark/corpus", root / "benchmark/scenarios/manifest.json"
        )

    assert caught.value.code == "io"


@pytest.mark.parametrize(
    "relative",
    [
        "benchmark/scenarios/manifest.json",
        "tests/fixtures/contracts/minimal-run-policy.json",
        "benchmark/schemas/v1/corpus-manifest.schema.json",
        "benchmark/corpus/exact-repetition-stall.jsonl",
    ],
)
def test_replay_rejects_hardlinked_fixed_and_trace_leaves(
    tmp_path: Path, relative: str
) -> None:
    root = _copied_root(tmp_path)
    target = root / relative
    external = tmp_path / "external"
    external.write_bytes(target.read_bytes())
    target.unlink()
    os.link(external, target)

    with pytest.raises(BenchmarkError) as caught:
        replay_result(
            root / "benchmark/corpus", root / "benchmark/scenarios/manifest.json"
        )

    assert caught.value.code in {"io", "invalid_schema"}


@pytest.mark.parametrize(
    "finding_id",
    [
        "unknown-detector-" + "0" * 64,
        "exact-repetition-" + "g" * 64,
        "exact-repetition-" + "0" * 63,
        "exact-repetition-" + "0" * 65,
        "exact-repetition-" + "0" * 64 + "-spoof",
    ],
)
def test_detector_names_rejects_malformed_or_unknown_closed_ids(
    finding_id: str,
) -> None:
    record = replay_result(CORPUS, MANIFEST)["traces"][0]["decision_record"]
    record = copy.deepcopy(record)
    record["confidence"]["contributing_findings"][0]["finding_id"] = finding_id

    with pytest.raises(BenchmarkError) as caught:
        _detector_names(record)

    assert caught.value.code == "internal"


def test_detector_names_rejects_a_duplicate_detector_contribution() -> None:
    record = replay_result(CORPUS, MANIFEST)["traces"][0]["decision_record"]
    record = copy.deepcopy(record)
    duplicate = copy.deepcopy(record["confidence"]["contributing_findings"][0])
    duplicate["finding_id"] = duplicate["finding_id"][:-1] + (
        "0" if duplicate["finding_id"][-1] != "0" else "1"
    )
    record["confidence"]["contributing_findings"].append(duplicate)

    with pytest.raises(BenchmarkError) as caught:
        _detector_names(record)

    assert caught.value.code == "internal"


def test_replay_reports_valid_expected_evidence_mismatch_without_hiding_it() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    entry = dict(manifest["entries"][0])
    entry["expected_evidence_event_ids"] = entry["expected_evidence_event_ids"][:-1]
    raw = (CORPUS / "exact-repetition-stall.jsonl").read_bytes()
    captured = _CapturedTrace(entry, raw, _parse_events(raw, entry["scenario_id"]))

    trace, _ = _trace_record(
        captured,
        _policy(
            (ROOT / "tests/fixtures/contracts/minimal-run-policy.json").read_bytes(),
            manifest,
        )[0],
    )

    assert trace["evidence_match"] is False
    assert trace["evidence_unexpected_event_ids"] == ["event-006"]
    assert trace["decision_match"] is True


def test_validate_result_rejects_forged_relational_fields() -> None:
    result = replay_result(CORPUS, MANIFEST)
    result["traces"][0]["decision_sha256"] = "0" * 64

    with pytest.raises(BenchmarkError):
        validate_result(result)


def test_contextual_validation_rejects_a_forged_trace_hash() -> None:
    result = replay_result(CORPUS, MANIFEST)
    manifest, traces, policy_sha256 = _context()
    result["traces"][0]["trace_sha256"] = "0" * 64

    with pytest.raises(BenchmarkError) as caught:
        validate_result(
            result,
            manifest=manifest,
            traces=traces,
            policy_sha256=policy_sha256,
        )

    assert caught.value.code == "invalid_schema"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("label", "productive_control"),
        ("expected_detector_names", []),
        ("expected_decision", "continue"),
        ("expected_evidence_event_ids", []),
        ("expected_safety_outcome", "safe_continue"),
    ],
)
def test_contextual_validation_rejects_forged_manifest_expected_fields(
    field: str, value: object
) -> None:
    result = replay_result(CORPUS, MANIFEST)
    manifest, traces, policy_sha256 = _context()
    entry = copy.deepcopy(traces[0].entry)
    entry[field] = value
    forged, _ = _trace_record(
        _CapturedTrace(entry, traces[0].raw, traces[0].events),
        _policy(
            (ROOT / "tests/fixtures/contracts/minimal-run-policy.json").read_bytes(),
            manifest,
        )[0],
    )
    result["traces"][0] = forged
    result["metrics"] = compute_metrics(result["traces"])

    with pytest.raises(BenchmarkError) as caught:
        validate_result(
            result,
            manifest=manifest,
            traces=traces,
            policy_sha256=policy_sha256,
        )

    assert caught.value.code == "invalid_schema"


def test_contextual_validation_accepts_equal_but_distinct_captured_entries() -> None:
    result = replay_result(CORPUS, MANIFEST)
    manifest, traces, policy_sha256 = _context()

    validate_result(
        result, manifest=manifest, traces=traces, policy_sha256=policy_sha256
    )


def test_contextual_validation_rejects_swapped_result_trace_records() -> None:
    result = replay_result(CORPUS, MANIFEST)
    manifest, traces, policy_sha256 = _context()
    result["traces"][0], result["traces"][1] = (
        result["traces"][1],
        result["traces"][0],
    )

    with pytest.raises(BenchmarkError, match="Invalid benchmark input"):
        validate_result(
            result,
            manifest=manifest,
            traces=traces,
            policy_sha256=policy_sha256,
        )


def test_contextual_validation_rejects_swapped_captured_traces() -> None:
    result = replay_result(CORPUS, MANIFEST)
    manifest, traces, policy_sha256 = _context()
    swapped = (traces[1], traces[0], *traces[2:])
    result["traces"][0], result["traces"][1] = (
        result["traces"][1],
        result["traces"][0],
    )
    result["corpus_revision"] = _revision(manifest, swapped)

    with pytest.raises(BenchmarkError, match="Invalid benchmark input"):
        validate_result(
            result,
            manifest=manifest,
            traces=swapped,
            policy_sha256=policy_sha256,
        )


def test_contextual_validation_rejects_a_duplicate_captured_scenario() -> None:
    result = replay_result(CORPUS, MANIFEST)
    manifest, traces, policy_sha256 = _context()
    duplicated_entry = copy.deepcopy(traces[1].entry)
    duplicated_entry["scenario_id"] = traces[0].entry["scenario_id"]
    duplicated = (
        traces[0],
        _CapturedTrace(duplicated_entry, traces[1].raw, traces[1].events),
        *traces[2:],
    )

    with pytest.raises(BenchmarkError, match="Invalid benchmark input"):
        validate_result(
            result,
            manifest=manifest,
            traces=duplicated,
            policy_sha256=policy_sha256,
        )


def test_validation_without_context_cannot_prove_external_trace_hashes() -> None:
    result = replay_result(CORPUS, MANIFEST)
    result["traces"][0]["trace_sha256"] = "0" * 64

    validate_result(result)
