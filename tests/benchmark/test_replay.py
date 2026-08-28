"""Offline deterministic replay contract and mismatch reporting tests."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from benchmark.replay import (
    BenchmarkError,
    _CapturedTrace,
    _parse_events,
    _policy,
    _trace_record,
    replay_bytes,
    replay_result,
    validate_result,
)

CORPUS = ROOT / "benchmark/corpus"
MANIFEST = ROOT / "benchmark/scenarios/manifest.json"
RESULT = ROOT / "benchmark/results/deterministic-results.json"
SCHEMA = ROOT / "benchmark/schemas/v1/replay-result.schema.json"


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
