"""Offline deterministic replay contract and mismatch reporting tests."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from benchmark.metrics import compute_metrics
from benchmark.replay import (
    BenchmarkError,
    _CapturedTrace,
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
